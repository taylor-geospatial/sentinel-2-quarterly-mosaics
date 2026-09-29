// The 36 quarters as one stack of map layers, and the prefetch that makes
// scrubbing through them feel like flipping frames rather than loading a page.
//
// The trick is entirely MapLibre's own tile machinery, pointed sideways. Each
// available quarter gets its own raster source and layer, stacked in the same
// slot; the quarter on show is at full opacity and the rest sit at zero. A
// layer at `raster-opacity: 0` still loads its tiles — only `visibility: none`
// stops that — so the quarters around the playhead quietly pull the same
// viewport, in the same byte windows, while you look at the one you picked.
// Moving the playhead is then a pair of opacity writes and no network at all.
//
// Two things keep that from being ruinous on a slow connection:
//
//   * Residency is staged and capped. Only the current quarter is loaded at
//     first; neighbours are admitted outward from the playhead once the
//     current one has settled, up to RESIDENT_MAX. Beyond the cap the farthest
//     quarter is evicted to `visibility: none`, which drops its tiles.
//   * A camera move collapses residency to the current quarter. Otherwise
//     every pan would multiply its tile requests by the number of resident
//     quarters, and a drag across the globe would queue thousands.
import { QUARTERS, overviewUrl } from "./catalog.js";
import { probe } from "./parquet.js";
import { quarterSource, openOverview } from "./mosaic.js";

// How many quarters may hold tiles at once. Each resident quarter costs its
// visible tiles in GPU memory — at 256x256 RGBA that is a quarter of a megabyte
// per tile, and a desktop viewport at mid zoom holds roughly twenty. Twelve
// quarters is a few hundred megabytes at worst and covers three years either
// side of the playhead, which is the range a scrub actually crosses.
const RESIDENT_MAX = 12;

const sourceId = (q) => `mosaic-${q}`;
const layerId = (q) => `mosaic-layer-${q}`;

export class TimeStack extends EventTarget {
  constructor(map, { beforeId } = {}) {
    super();
    this.map = map;
    this.beforeId = beforeId;
    this.current = null;
    /** quarter -> "unknown" | "absent" | "present" */
    this.availability = new Map(QUARTERS.map((q) => [q, "unknown"]));
    /** quarters whose layer is in the style */
    this.resident = new Set();
    /** quarter -> {loaded, errored} tile counts, for the filmstrip */
    this.progress = new Map(QUARTERS.map((q) => [q, { tiles: 0 }]));
    this._moving = false;
    this._expandTimer = null;

    map.on("movestart", () => { this._moving = true; this._collapse(); });
    map.on("moveend", () => { this._moving = false; this._scheduleExpand(); });
    map.on("idle", () => this._scheduleExpand());
  }

  // Ask the product which quarters exist. One one-byte range read each: the
  // 206's Content-Range names the file's size, so a probe and a size lookup are
  // the same request. Absent is expected — the catalog fills in over time and
  // the timeline has to show the gaps honestly rather than pretend.
  async discover() {
    await Promise.all(QUARTERS.map(async (q) => {
      const hit = await probe(overviewUrl(q));
      this.availability.set(q, hit ? "present" : "absent");
      this.dispatchEvent(new CustomEvent("availability", { detail: { quarter: q } }));
    }));
    this.dispatchEvent(new CustomEvent("availability", { detail: { quarter: null } }));
    return this.available();
  }

  available() {
    return QUARTERS.filter((q) => this.availability.get(q) === "present");
  }

  isAvailable(q) {
    return this.availability.get(q) === "present";
  }

  // Put a quarter on show. Returns false when nothing was published for it, so
  // the caller can leave the playhead where it is and say why.
  async show(quarter, { instant = true } = {}) {
    if (!this.isAvailable(quarter)) {
      this.dispatchEvent(new CustomEvent("unavailable", { detail: { quarter } }));
      return false;
    }
    const previous = this.current;
    this.current = quarter;
    await this._admit(quarter);
    for (const q of this.resident) {
      const on = q === quarter;
      // Always a hard cut, never a crossfade. Two quarters blended at
      // half opacity for even 140 ms read as a blur, not a transition —
      // these are film frames, and a resident quarter can cut cleanly.
      this.map.setPaintProperty(layerId(q), "raster-opacity-transition",
        { duration: 0, delay: 0 });
      this.map.setPaintProperty(layerId(q), "raster-opacity", on ? 1 : 0);
    }
    if (previous !== quarter) {
      this.dispatchEvent(new CustomEvent("change", { detail: { quarter, previous } }));
    }
    this._scheduleExpand();
    return true;
  }

  // Add a quarter's source and layer if they are not already there. Layers all
  // go in the same slot, so which one is visible is purely a paint property and
  // never a restack.
  async _admit(quarter) {
    if (this.resident.has(quarter)) {
      this.map.setLayoutProperty(layerId(quarter), "visibility", "visible");
      return;
    }
    if (!this.isAvailable(quarter)) return;
    let source;
    try {
      source = await quarterSource(quarter);
    } catch {
      // The probe said the object was there and the headers say otherwise —
      // a truncated or half-uploaded file. Treat it as absent from here on.
      this.availability.set(quarter, "absent");
      this.dispatchEvent(new CustomEvent("availability", { detail: { quarter } }));
      return;
    }
    if (this.map.getSource(sourceId(quarter))) {
      this.resident.add(quarter);
      return;
    }
    this.map.addSource(sourceId(quarter), source);
    this.map.addLayer({
      id: layerId(quarter),
      type: "raster",
      source: sourceId(quarter),
      paint: {
        "raster-opacity": quarter === this.current ? 1 : 0,
        "raster-opacity-transition": { duration: 0, delay: 0 },
        // MapLibre's default 300 ms tile fade makes a scrub look like a
        // dissolve between two half-drawn maps. These are film frames; they
        // should cut.
        "raster-fade-duration": 0,
        "raster-resampling": "linear",
      },
    }, this.beforeId);
    this.resident.add(quarter);
  }

  // While the camera moves, only the quarter on show may load tiles.
  _collapse() {
    clearTimeout(this._expandTimer);
    for (const q of this.resident) {
      if (q !== this.current) this.map.setLayoutProperty(layerId(q), "visibility", "none");
    }
  }

  _scheduleExpand() {
    if (this._moving || !this.current) return;
    clearTimeout(this._expandTimer);
    this._expandTimer = setTimeout(() => this._expand(), 250);
  }

  // Admit quarters outward from the playhead, one at a time, each only once the
  // map has gone quiet again. Staging it this way means the first view costs
  // one quarter's tiles and the stack thickens behind your back.
  async _expand() {
    if (this._moving || !this.current) return;
    if (!this.map.areTilesLoaded()) return;

    const order = this._byDistanceFromPlayhead();
    const keep = new Set(order.slice(0, RESIDENT_MAX));

    for (const q of this.resident) {
      if (!keep.has(q) && q !== this.current) {
        this.map.setLayoutProperty(layerId(q), "visibility", "none");
      }
    }
    for (const q of order.slice(0, RESIDENT_MAX)) {
      if (!this.resident.has(q)) {
        await this._admit(q);
        this._scheduleExpand();   // one per settle, so the network stays calm
        return;
      }
      if (this.map.getLayer(layerId(q))
        && this.map.getLayoutProperty(layerId(q), "visibility") === "none") {
        this.map.setLayoutProperty(layerId(q), "visibility", "visible");
        this._scheduleExpand();
        return;
      }
    }
  }

  // Available quarters ordered by how soon a scrub would reach them.
  _byDistanceFromPlayhead() {
    const avail = this.available();
    const here = avail.indexOf(this.current);
    if (here < 0) return avail;
    return [...avail].sort((a, b) =>
      Math.abs(avail.indexOf(a) - here) - Math.abs(avail.indexOf(b) - here));
  }

  // Called by the mosaic protocol for every tile it decodes: how much of a
  // quarter has actually arrived, for the filmstrip's own progress.
  noteTile(quarter) {
    const p = this.progress.get(quarter);
    if (p) p.tiles += 1;
  }

  // Everything loaded for the viewport is thrown away on a camera move, so the
  // filmstrip's frames are cleared with it.
  resetProgress() {
    for (const p of this.progress.values()) p.tiles = 0;
  }

  step(delta) {
    const avail = this.available();
    if (!avail.length) return null;
    const here = avail.indexOf(this.current);
    const next = Math.min(avail.length - 1, Math.max(0, (here < 0 ? 0 : here) + delta));
    return avail[next];
  }

  // The quarter nearest `quarter` that actually has data, for a URL that names
  // one which does not.
  nearestAvailable(quarter) {
    const avail = this.available();
    if (!avail.length) return null;
    if (avail.includes(quarter)) return quarter;
    const want = QUARTERS.indexOf(quarter);
    if (want < 0) return avail[avail.length - 1];
    return avail.reduce((best, q) =>
      Math.abs(QUARTERS.indexOf(q) - want) < Math.abs(QUARTERS.indexOf(best) - want) ? q : best);
  }
}

export { openOverview };
