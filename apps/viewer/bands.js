// Turning Sentinel-2 band values into pixels.
//
// The band COGs are single-band Int16 at 10 m, holding surface reflectance
// scaled by 10000 — so 0.25 reflectance is stored as 2500 — with -32768 for
// nodata. The scale factor is *not* embedded in the TIFF; it comes from the
// collection metadata, which is why it is written down here rather than read.
//
// Only four bands exist in this product: B02 blue, B03 green, B04 red, B08
// near-infrared. That is enough for natural colour, false-colour infrared and
// NDVI, and not enough for anything else, so the presets are exactly those
// three rather than a general band mapper.

export const BAND_INFO = {
  B02: { name: "Blue", wavelength: 0.493 },
  B03: { name: "Green", wavelength: 0.560 },
  B04: { name: "Red", wavelength: 0.665 },
  B08: { name: "Near infrared", wavelength: 0.833 },
};

export const NODATA = -32768;

// The default stretch: 0 to 3000 in stored units is 0 to 0.30 reflectance,
// which covers land from dark water to bright bare soil. Gamma 1/1.8 lifts the
// midtones, because a linear ramp of reflectance looks muddy — most vegetated
// land sits under 0.15 and would otherwise occupy the bottom eighth of the
// range. What lies above 0.30 — cloud, snow, salt — is not clipped but rolled
// off; see the shoulder on `ramp` below.
export const PRESETS = {
  natural: {
    id: "natural",
    label: "Natural colour",
    bands: ["B04", "B03", "B02"],
    min: 0, max: 3000, gamma: 1.8,
  },
  infrared: {
    id: "infrared",
    label: "Colour infrared",
    bands: ["B08", "B04", "B03"],
    // Near-infrared over vegetation runs much brighter than the visible bands,
    // so the same ceiling would blow the red channel out to white everywhere
    // that is green.
    min: 0, max: 5000, gamma: 1.6,
  },
  ndvi: {
    id: "ndvi",
    label: "NDVI",
    bands: ["B08", "B04"],
    index: true,
  },
};

export const bandsOf = (spec) => [...new Set(spec.bands)];

// The gamma curve runs to white at `max` and everything brighter slams into it.
// Over the Alps in Q2 that is most of the frame: snow sits at 0.5–0.9
// reflectance against a 0.30 ceiling, so a pitched camera looks at a flat white
// sheet and the terrain it is draped on stops reading as terrain at all.
//
// So the top of the curve gets a shoulder. Below SHOULDER of full white the
// ramp is exactly the gamma curve it always was — with gamma 1.8 that is
// everything under 0.75 of the stretch, which is all vegetated land and all
// bare soil, and those pixels are byte-for-byte unchanged. Above it the curve
// approaches white asymptotically instead of reaching it, so 0.3 to 0.9
// reflectance spreads across the last two dozen levels rather than collapsing
// into one. Snow still reads as white; it just keeps its shape.
const SHOULDER = 0.85;
// How far past `max` the table runs. Fresh snow tops out near 0.9 reflectance,
// three times a 0.30 ceiling; four leaves room above that for specular ice.
const HEADROOM = 4;
// Chosen so the shoulder is within a byte of white by 2.5x the ceiling — far
// enough to hold gradation across snow, near enough that it never looks grey.
const KNEE = 0.646;

// A lookup from stored band value to byte, built once per stretch so the
// per-pixel work is an index rather than a pow(). `span` converts a stored
// value to a table index; the caller clamps.
function ramp({ min, max, gamma }) {
  const table = new Uint8ClampedArray(4096);
  const tk = SHOULDER ** gamma;     // where the gamma curve reaches SHOULDER
  for (let i = 0; i < table.length; i++) {
    const t = (HEADROOM * i) / (table.length - 1);
    const y = t <= tk
      ? t ** (1 / gamma)
      : 1 - (1 - SHOULDER) * Math.exp(-(t - tk) / KNEE);
    table[i] = Math.round(255 * y);
  }
  return { table, min, span: (table.length - 1) / ((max - min) * HEADROOM) };
}

// NDVI's colour ramp. Brown through straw to green, which is the convention
// people reading vegetation indices already have in their heads; water and
// bare rock fall below zero and go slate rather than a fourth hue, so the
// image does not invent a category the index does not have.
const NDVI_STOPS = [
  [-1.0, [38, 52, 66]],
  [0.0, [86, 76, 62]],
  [0.2, [158, 130, 80]],
  [0.4, [176, 178, 84]],
  [0.6, [92, 152, 62]],
  [0.8, [34, 104, 44]],
  [1.0, [12, 62, 30]],
];

function ndviColor(v) {
  for (let i = 1; i < NDVI_STOPS.length; i++) {
    const [x1, c1] = NDVI_STOPS[i];
    if (v <= x1 || i === NDVI_STOPS.length - 1) {
      const [x0, c0] = NDVI_STOPS[i - 1];
      const t = Math.max(0, Math.min(1, (v - x0) / (x1 - x0)));
      return [
        c0[0] + (c1[0] - c0[0]) * t,
        c0[1] + (c1[1] - c0[1]) * t,
        c0[2] + (c1[2] - c0[2]) * t,
      ];
    }
  }
  return [0, 0, 0];
}

const NDVI_LUT = (() => {
  const lut = new Uint8ClampedArray(512 * 3);
  for (let i = 0; i < 512; i++) {
    const [r, g, b] = ndviColor((i / 511) * 2 - 1);
    lut[i * 3] = r; lut[i * 3 + 1] = g; lut[i * 3 + 2] = b;
  }
  return lut;
})();

export const stretchKey = (spec) =>
  spec.index ? `ndvi` : `${spec.id}:${spec.min}:${spec.max}:${spec.gamma}`;

// Paint one tile's warped planes to RGBA. A pixel is transparent when any band
// it needs is missing there — off the image (NaN, from the warp) or nodata
// (-32768, from the file). Keeping those two cases together is deliberate:
// both mean "this product says nothing here", and the swath edge of a mosaic
// cell is exactly where they meet.
export function paintRGBA(planes, spec, W, H) {
  const out = new Uint8ClampedArray(W * H * 4);

  if (spec.index) {
    const [nir, red] = spec.bands.map((b) => planes[b]);
    if (!nir || !red) return new ImageData(out, W, H);
    for (let i = 0; i < W * H; i++) {
      const a = nir[i], b = red[i];
      if (!(a > NODATA) || !(b > NODATA) || Number.isNaN(a) || Number.isNaN(b)) continue;
      const sum = a + b;
      if (sum === 0) continue;
      const ndvi = (a - b) / sum;
      const k = Math.round(((Math.max(-1, Math.min(1, ndvi)) + 1) / 2) * 511) * 3;
      const o = i * 4;
      out[o] = NDVI_LUT[k]; out[o + 1] = NDVI_LUT[k + 1]; out[o + 2] = NDVI_LUT[k + 2];
      out[o + 3] = 255;
    }
    return new ImageData(out, W, H);
  }

  const { table, min, span } = ramp(spec);
  const chans = spec.bands.map((b) => planes[b]);
  for (let i = 0; i < W * H; i++) {
    let ok = true;
    const o = i * 4;
    for (let c = 0; c < 3; c++) {
      const plane = chans[c];
      const v = plane ? plane[i] : NaN;
      if (Number.isNaN(v) || v <= NODATA) { ok = false; break; }
      const idx = Math.max(0, Math.min(table.length - 1, Math.round((v - min) * span)));
      out[o + c] = table[idx];
    }
    out[o + 3] = ok ? 255 : 0;
  }
  return new ImageData(out, W, H);
}
