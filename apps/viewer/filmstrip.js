// The filmstrip: 36 quarters as 36 frames, and each frame is the place you are
// actually looking at, in that quarter.
//
// This is the whole interface. Not a slider with availability ticks over it —
// a contact sheet of one location through nine years, which fills in as the
// time stack loads. Every tile the map decodes for any quarter is also painted
// into that quarter's frame (mosaic.js calls back with the decoded bitmap
// before handing it to MapLibre), so the strip's progress and the strip's
// content are the same thing: a frame is blank because those bytes have not
// arrived, and complete because they have. There is no separate spinner,
// because a spinner would be a worse version of the picture.
//
// Frames are narrow and tall, so all 36 fit a laptop without scrolling. Each
// shows the centre of the viewport cropped to the frame's shape, which is the
// part of the screen a person is looking at anyway.
import { QUARTERS, YEARS, yearOf, quarterNum, monthsOf } from "./catalog.js";

const WORLD = 20037508.342789244;
const DPR = Math.min(2, globalThis.devicePixelRatio || 1);

// Lon/lat to Web Mercator metres. The frames are mercator boxes because the
// tiles painted into them are, so a tile lands in a frame by arithmetic alone.
const toMercator = (lng, lat) => {
  const clamped = Math.max(-85.051129, Math.min(85.051129, lat));
  return [
    (lng / 180) * WORLD,
    (WORLD / Math.PI) * Math.log(Math.tan(Math.PI / 4 + (clamped * Math.PI) / 360)),
  ];
};

export class Filmstrip extends EventTarget {
  constructor(root, stack) {
    super();
    this.root = root;
    this.stack = stack;
    this.frames = new Map();
    this.extent = null;        // the mercator box each frame depicts
    this.playing = false;
    this._timer = null;
    this._dragging = false;
    this._build();

    stack.addEventListener("availability", () => this.refreshAvailability());
    stack.addEventListener("change", (e) => this.setCurrent(e.detail.quarter));
  }

  _build() {
    this.root.innerHTML = "";
    this.root.setAttribute("role", "slider");
    this.root.setAttribute("aria-label", "Quarter");
    this.root.tabIndex = 0;

    for (const year of YEARS) {
      const group = document.createElement("div");
      group.className = "year";

      const row = document.createElement("div");
      row.className = "year-frames";
      for (const quarter of QUARTERS.filter((q) => yearOf(q) === year)) {
        const frame = document.createElement("button");
        frame.type = "button";
        frame.className = "frame";
        frame.dataset.quarter = quarter;
        frame.tabIndex = -1;
        frame.setAttribute("aria-label", `${year} Q${quarterNum(quarter)}, ${monthsOf(quarter)}`);

        const canvas = document.createElement("canvas");
        canvas.width = 1;
        canvas.height = 1;
        frame.appendChild(canvas);

        const q = document.createElement("span");
        q.className = "frame-q";
        q.textContent = `Q${quarterNum(quarter)}`;
        frame.appendChild(q);

        row.appendChild(frame);
        this.frames.set(quarter, { el: frame, canvas, ctx: null, painted: 0 });
      }
      group.appendChild(row);

      const label = document.createElement("span");
      label.className = "year-label";
      label.textContent = year;
      group.appendChild(label);

      this.root.appendChild(group);
    }

    this._wire();
  }

  _wire() {
    const quarterAt = (clientX) => {
      let best = null, bestDist = Infinity;
      for (const [quarter, f] of this.frames) {
        const r = f.el.getBoundingClientRect();
        const d = Math.abs(clientX - (r.left + r.width / 2));
        if (d < bestDist) { bestDist = d; best = quarter; }
      }
      return best;
    };

    this.root.addEventListener("pointerdown", (e) => {
      this._dragging = true;
      this.root.setPointerCapture(e.pointerId);
      this.pause();
      this._pick(quarterAt(e.clientX), true);
    });
    this.root.addEventListener("pointermove", (e) => {
      if (!this._dragging) return;
      this._pick(quarterAt(e.clientX), true);
    });
    const end = (e) => {
      if (!this._dragging) return;
      this._dragging = false;
      try { this.root.releasePointerCapture(e.pointerId); } catch { /* already gone */ }
    };
    this.root.addEventListener("pointerup", end);
    this.root.addEventListener("pointercancel", end);

    this.root.addEventListener("keydown", (e) => {
      const jump = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: 1, ArrowDown: -1 }[e.key];
      if (jump !== undefined) {
        e.preventDefault();
        this.pause();
        this._pick(this.stack.step(e.shiftKey ? jump * 4 : jump), false);
      } else if (e.key === "Home") {
        e.preventDefault(); this.pause(); this._pick(this.stack.available()[0], false);
      } else if (e.key === "End") {
        e.preventDefault(); this.pause();
        const a = this.stack.available(); this._pick(a[a.length - 1], false);
      } else if (e.key === " ") {
        e.preventDefault(); this.toggle();
      }
    });
  }

  _pick(quarter, instant) {
    if (!quarter || quarter === this.stack.current) return;
    this.dispatchEvent(new CustomEvent("pick", { detail: { quarter, instant } }));
  }

  setCurrent(quarter) {
    for (const [q, f] of this.frames) f.el.classList.toggle("is-current", q === quarter);
    this.root.setAttribute("aria-valuenow", String(QUARTERS.indexOf(quarter) + 1));
    this.root.setAttribute("aria-valuetext", quarter ? `${yearOf(quarter)} Q${quarterNum(quarter)}` : "");
    const f = this.frames.get(quarter);
    f?.el.scrollIntoView({ block: "nearest", inline: "nearest" });
  }

  refreshAvailability() {
    for (const [q, f] of this.frames) {
      const state = this.stack.availability.get(q);
      f.el.classList.toggle("is-absent", state === "absent");
      f.el.classList.toggle("is-unknown", state === "unknown");
      f.el.disabled = state === "absent";
      if (state === "absent") {
        f.el.title = `${yearOf(q)} Q${quarterNum(q)} — not yet published`;
      } else {
        f.el.title = `${yearOf(q)} Q${quarterNum(q)} · ${monthsOf(q)}`;
      }
    }
  }

  // --- painting ------------------------------------------------------------

  // The viewport changed: every frame now depicts somewhere else, so wipe them
  // and record the new box. Called on moveend.
  setExtent(bounds) {
    const [w, s] = toMercator(bounds.getWest(), bounds.getSouth());
    const [e, n] = toMercator(bounds.getEast(), bounds.getNorth());
    // A frame is a centre crop of the viewport in the frame's own shape, so a
    // tall narrow frame shows the middle of the screen rather than a squashed
    // copy of all of it.
    const first = this.frames.values().next().value;
    const rect = first.el.getBoundingClientRect();
    const aspect = rect.width > 0 ? rect.width / rect.height : 0.6;
    const cx = (w + e) / 2, cy = (s + n) / 2;
    let halfW = Math.abs(e - w) / 2, halfH = Math.abs(n - s) / 2;
    if (halfW / halfH > aspect) halfW = halfH * aspect; else halfH = halfW / aspect;
    this.extent = { west: cx - halfW, east: cx + halfW, south: cy - halfH, north: cy + halfH };
    this.clear();
  }

  clear() {
    for (const f of this.frames.values()) {
      f.painted = 0;
      f.el.classList.remove("has-image");
      if (f.ctx) f.ctx.clearRect(0, 0, f.canvas.width, f.canvas.height);
    }
    this.stack.resetProgress();
  }

  _ctxFor(f) {
    const rect = f.el.getBoundingClientRect();
    const w = Math.max(1, Math.round(rect.width * DPR));
    const h = Math.max(1, Math.round(rect.height * DPR));
    if (f.canvas.width !== w || f.canvas.height !== h) {
      f.canvas.width = w;
      f.canvas.height = h;
      f.ctx = null;
    }
    if (!f.ctx) {
      f.ctx = f.canvas.getContext("2d");
      f.ctx.imageSmoothingEnabled = true;
      f.ctx.imageSmoothingQuality = "low";
    }
    return f.ctx;
  }

  // One decoded map tile, painted into its quarter's frame. `ext` is the
  // tile's mercator box, straight from the tile index — no reprojection, since
  // the frames are mercator too.
  paintTile(quarter, bitmap, ext) {
    const f = this.frames.get(quarter);
    if (!f || !this.extent) return;
    const { west, east, south, north } = this.extent;
    if (ext.east <= west || ext.west >= east || ext.north <= south || ext.south >= north) return;

    const ctx = this._ctxFor(f);
    const kx = f.canvas.width / (east - west);
    const ky = f.canvas.height / (north - south);
    const dx = (ext.west - west) * kx;
    const dy = (north - ext.north) * ky;
    const dw = (ext.east - ext.west) * kx;
    const dh = (ext.north - ext.south) * ky;
    // Sub-pixel tiles are common at low zoom; drawing them anyway is what
    // makes a globe-scale frame resolve into a recognisable continent.
    try {
      ctx.drawImage(bitmap, dx, dy, Math.max(dw, 0.5), Math.max(dh, 0.5));
    } catch {
      return;   // a closed bitmap, if MapLibre got there first
    }
    f.painted += 1;
    if (f.painted === 1) f.el.classList.add("has-image");
  }

  // --- transport -----------------------------------------------------------

  toggle() { this.playing ? this.pause() : this.play(); }

  play() {
    const avail = this.stack.available();
    if (avail.length < 2) return;
    this.playing = true;
    this.dispatchEvent(new CustomEvent("playing", { detail: { playing: true } }));
    clearInterval(this._timer);
    this._timer = setInterval(() => {
      const avail2 = this.stack.available();
      const here = avail2.indexOf(this.stack.current);
      const next = avail2[(here + 1) % avail2.length];
      this.dispatchEvent(new CustomEvent("pick", { detail: { quarter: next, instant: false } }));
    }, 700);
  }

  pause() {
    if (!this.playing) return;
    this.playing = false;
    clearInterval(this._timer);
    this.dispatchEvent(new CustomEvent("playing", { detail: { playing: false } }));
  }
}
