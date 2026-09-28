// Switching from the quarterly overview to the band COGs, and back.
//
// The overview is the browse layer: Web Mercator, pre-composited, and about
// 150 m at its base. Past the zoom where its deepest level runs out, MapLibre
// can only stretch it, and the actual 10 m pixels are sitting in the band COGs
// beside it. This decides when that swap happens and for which cells.
//
// Which cells: the MGRS grid is already on the map as vector tiles, so the
// cells under the viewport are a `querySourceFeatures` away and no MGRS
// arithmetic is needed in the browser. Each visible cell gets its own raster
// source, bounded to its own footprint, so MapLibre never asks a cell for a
// tile outside it.
//
// How many quarters: the current one, plus its immediate neighbours either
// side. This is the one place the time stack is deliberately thinner than at
// low zoom. A band tile is three range reads and a CPU warp rather than one
// block passthrough, and a viewport at z13 can touch four cells — prefetching
// twelve quarters of that would saturate the connection for a scrub most people
// will not make from this zoom. Scrubbing still works instantly here, on the
// upscaled overview underneath, and sharpens a beat later on the quarter you
// stop at.
import { registerSceneProtocol, sceneSource, forgetScene } from "./scene.js";
import { PRESETS } from "./bands.js";

// Where the swap happens. The overview's base is z9 (305.75 m/px) for every
// quarter, deliberately, so that no quarter looks sharper than its neighbours
// while you scrub. z10 is therefore the first zoom with nothing left to show,
// and the band COGs take over there. Swapping earlier is also *cheaper* per
// tile, not dearer: a coarser map tile matches a coarser internal overview, and
// the block reads shrink by four for every zoom you hand over sooner.
const SWAP_ZOOM = 10;

// Deliberately small. A band tile is three range reads of 1024-pixel blocks and
// a CPU warp, against one block passthrough at low zoom, and the connection this
// was built on runs at tens of kilobytes a second. Two cells covers a viewport
// almost always and four covers the corner case at a cost that is not worth it.
const MAX_CELLS = 2;

// Zero: at high zoom only the quarter on show reads band COGs. This is the one
// place the time stack is thinner than at low zoom, and it is a considered
// trade. Prefetching even one neighbour doubles the bytes of the most expensive
// tier in the app. Scrubbing here still responds instantly — the overview stack
// underneath is fully resident and MapLibre stretches it — and the 10 m detail
// arrives a beat later on the quarter you actually stopped at.
const NEIGHBOUR_QUARTERS = 0;

const sourceId = (q, s) => `scene-${q}-${s}`;
const layerId = (q, s) => `scene-layer-${q}-${s}`;

export class SceneTier {
  constructor(map, stack, addProtocol, { beforeId } = {}) {
    this.map = map;
    this.stack = stack;
    this.beforeId = beforeId;
    this.preset = "natural";
    this.live = new Map();        // "quarter/subtile" -> {quarter, subtile, bounds}
    registerSceneProtocol(addProtocol, (id) => PRESETS[id] || PRESETS.natural);
  }

  get active() {
    return this.map.getZoom() >= SWAP_ZOOM;
  }

  setPreset(id) {
    if (!PRESETS[id] || id === this.preset) return;
    this.preset = id;
    // The preset is part of the tile URL, so every live cell needs a new
    // source. Its tiles come back out of scene.js's plane cache without a
    // network read.
    const was = [...this.live.values()];
    this.clear();
    for (const { quarter, subtile, bounds } of was) this._add(quarter, subtile, bounds);
  }

  // The cells under the viewport, from the grid already on the map.
  //
  // Which cells are in view comes from queryRenderedFeatures, but their extents
  // must not: that call returns geometry clipped to the viewport, so a cell
  // half off-screen reports a bbox of the visible sliver. Used as a source
  // bounds, that stops MapLibre requesting the rest of the cell — which is
  // exactly the part you are about to pan into. The extents therefore come from
  // querySourceFeatures, unioned across every loaded vector tile the cell
  // appears in, and padded, because a source bounds that is slightly too large
  // costs a few transparent tiles while one slightly too small loses imagery.
  visibleCells() {
    if (!this.map.getLayer("grid-line")) return [];
    const inView = new Set();
    for (const f of this.map.queryRenderedFeatures({ layers: ["grid-line"] })) {
      const s = f.properties?.subtile;
      if (s) inView.add(s);
    }
    if (!inView.size) return [];

    const boxes = new Map();
    for (const f of this.map.querySourceFeatures("footprints", { sourceLayer: "footprints" })) {
      const s = f.properties?.subtile;
      if (!s || !inView.has(s)) continue;
      const b = featureBounds(f);
      if (!b) { boxes.set(s, null); continue; }      // antimeridian: no bounds
      const prev = boxes.get(s);
      if (prev === null) continue;
      boxes.set(s, prev
        ? [Math.min(prev[0], b[0]), Math.min(prev[1], b[1]),
           Math.max(prev[2], b[2]), Math.max(prev[3], b[3])]
        : b);
    }

    const c = this.map.getCenter();
    return [...inView]
      .map((subtile) => {
        const b = boxes.get(subtile);
        return { subtile, bounds: straddles(b) ? null : pad(b) };
      })
      .sort((a, b) => centreDist(a.bounds, c) - centreDist(b.bounds, c))
      .slice(0, MAX_CELLS);
  }

  quartersWanted() {
    const avail = this.stack.available();
    const here = avail.indexOf(this.stack.current);
    if (here < 0) return [];
    const out = [];
    for (let d = -NEIGHBOUR_QUARTERS; d <= NEIGHBOUR_QUARTERS; d++) {
      const q = avail[here + d];
      if (q) out.push(q);
    }
    // The one on show first, so it is the one that gets the connection.
    return out.sort((a, b) =>
      Math.abs(avail.indexOf(a) - here) - Math.abs(avail.indexOf(b) - here));
  }

  refresh() {
    if (!this.active) { this.clear(); return; }
    const cells = this.visibleCells();
    if (!cells.length) { this.clear(); return; }
    const quarters = this.quartersWanted();

    const keep = new Set();
    for (const q of quarters) {
      for (const { subtile, bounds } of cells) {
        keep.add(`${q}/${subtile}`);
        this._add(q, subtile, bounds);
      }
    }
    for (const key of [...this.live.keys()]) {
      if (!keep.has(key)) this._remove(key);
    }
    this.setCurrentQuarter(this.stack.current);
  }

  // Only the quarter on show is painted; the neighbours sit at zero opacity
  // and keep their tiles, exactly as the overview stack does.
  setCurrentQuarter(quarter) {
    for (const { quarter: q, subtile } of this.live.values()) {
      const id = layerId(q, subtile);
      if (this.map.getLayer(id)) {
        this.map.setPaintProperty(id, "raster-opacity", q === quarter ? 1 : 0);
      }
    }
  }

  _add(quarter, subtile, bounds) {
    const key = `${quarter}/${subtile}`;
    if (this.live.has(key)) return;
    const sid = sourceId(quarter, subtile);
    const lid = layerId(quarter, subtile);
    if (!this.map.getSource(sid)) {
      this.map.addSource(sid, sceneSource(quarter, subtile, this.preset, bounds));
    }
    if (!this.map.getLayer(lid)) {
      this.map.addLayer({
        id: lid,
        type: "raster",
        source: sid,
        minzoom: SWAP_ZOOM - 0.5,
        paint: {
          "raster-opacity": quarter === this.stack.current ? 1 : 0,
          "raster-opacity-transition": { duration: 0, delay: 0 },
          "raster-fade-duration": 0,
        },
      }, this.beforeId);
    }
    this.live.set(key, { quarter, subtile, bounds });
  }

  _remove(key) {
    const entry = this.live.get(key);
    if (!entry) return;
    const { quarter, subtile } = entry;
    if (this.map.getLayer(layerId(quarter, subtile))) {
      this.map.removeLayer(layerId(quarter, subtile));
    }
    if (this.map.getSource(sourceId(quarter, subtile))) {
      this.map.removeSource(sourceId(quarter, subtile));
    }
    this.live.delete(key);
    forgetScene(quarter, subtile);
  }

  clear() {
    for (const key of [...this.live.keys()]) this._remove(key);
  }
}

function featureBounds(f) {
  const g = f.geometry;
  if (!g) return null;
  const rings = g.type === "Polygon" ? g.coordinates
    : g.type === "MultiPolygon" ? g.coordinates.flat() : null;
  if (!rings) return null;
  let w = Infinity, s = Infinity, e = -Infinity, n = -Infinity;
  for (const ring of rings) {
    for (const [x, y] of ring) {
      if (x < w) w = x; if (x > e) e = x;
      if (y < s) s = y; if (y > n) n = y;
    }
  }
  if (straddles([w, s, e, n])) return null;
  return [w, s, e, n];
}

// A cell that straddles the antimeridian comes back as a bbox spanning the
// whole planet, which would let MapLibre request the entire world for it. 125
// cells per quarter are like this; a bounds of the visible half is wrong, so
// the honest answer is no bounds at all and let the tile reads return
// transparent where the cell is not.
//
// This has to be asked of the union in visibleCells and not only of each
// feature, because such a cell usually arrives as two features in two
// different vector tiles — one ring at +179, one at -179 — each an honest
// narrow bbox on its own side. Only their union spans the planet, and it is
// the union that becomes the source bounds.
const straddles = (b) => !!b && b[2] - b[0] > 180;

// A cell is 100.08 km on a side; 4% of its own span is a few kilometres of
// slack, enough to cover what tile clipping shaved off an edge.
function pad(b) {
  if (!b) return null;
  const dx = (b[2] - b[0]) * 0.04, dy = (b[3] - b[1]) * 0.04;
  return [b[0] - dx, b[1] - dy, b[2] + dx, b[3] + dy];
}

// A cell with no bounds (one crossing the antimeridian) sorts last rather than
// throwing; it is still loadable, just not rankable by distance.
const centreDist = (b, c) =>
  (b ? Math.hypot((b[0] + b[2]) / 2 - c.lng, (b[1] + b[3]) / 2 - c.lat) : 1e6);

export { SWAP_ZOOM };
