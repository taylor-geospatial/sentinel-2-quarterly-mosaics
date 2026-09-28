// The high-zoom tier: the band COGs themselves, read and warped in the browser.
//
// Below roughly z10 the quarterly overview carries the map (mosaic.js) — it is
// already Web Mercator, already RGB, and its blocks are already tiles. Past
// that there is nothing finer in it, and the real pixels live where they always
// did: four single-band Int16 COGs per mosaic cell, on the cell's own UTM grid
// at 10 m. Getting those onto a Mercator tile means an actual reprojection, and
// this is the donor explorer's warp, carried over nearly intact:
//
//   Every output pixel is placed by inverse-mapping its own position into the
//   COG's pixel grid through proj4, via bilinear interpolation across a control
//   grid of 16-pixel cells. UTM to lon/lat is smooth, so across 16 pixels the
//   interpolation error is far below one pixel. The visible approximation is
//   the resampling itself — nearest neighbour out of an overview between 1x and
//   2x the tile's resolution — which reads as slight aliasing on sharp edges,
//   not as misplacement.
//
// One change from the donor: the control grid is built in Web Mercator rather
// than in lon/lat. A MapLibre raster tile is a square of Mercator metres, so
// interpolating linearly in that space is exact at the tile's own corners
// instead of being a small-angle approximation of them.
//
// Each band's warped samples are kept per (cell, tile, band) as a plane of
// floats, so changing the stretch, the preset or the gamma repaints from memory
// without a single new byte, and switching between natural colour and NDVI
// fetches only the band that was not already read.
import proj4 from "https://esm.sh/proj4@2.22.0";
import { openCogHeaders } from "./cogsource.js";
import { bandUrl } from "./catalog.js";
import { paintRGBA, bandsOf } from "./bands.js";

const WORLD = 20037508.342789244;
const TILE = 256;
const CELL = 16;            // warp control-grid cell, in output pixels
const PLANE_CACHE = 240;    // warped planes kept per cell (~60 MB of Float32)

const merToLngLat = (x, y) => [
  (x / WORLD) * 180,
  (Math.atan(Math.exp((y / WORLD) * Math.PI)) * 360) / Math.PI - 90,
];

// --- Opening a band COG ----------------------------------------------------

const cogs = new Map();      // "quarter/subtile/band" -> Promise

function openBand(quarter, subtile, band) {
  const key = `${quarter}/${subtile}/${band}`;
  if (!cogs.has(key)) {
    cogs.set(key, (async () => {
      const href = bandUrl(quarter, subtile, band);
      // Through the no-store client: every window read below goes to the same
      // URL as fifteen other tiles' reads, and the browser cache lock would
      // otherwise serialise all of them. See cogsource.js.
      const tiff = await openCogHeaders(href);
      const count = await tiff.getImageCount();
      const images = [];
      for (let i = 0; i < count; i++) images.push(await tiff.getImage(i));
      const base = images[0];
      const epsg = base.getGeoKeys()?.ProjectedCSTypeGeoKey;
      const series = Math.floor((epsg ?? 0) / 100);
      if (series !== 326 && series !== 327) {
        throw new Error(`EPSG:${epsg} is not a WGS84 UTM code`);
      }
      const zone = epsg % 100;
      const def = `+proj=utm +zone=${zone}${series === 327 ? " +south" : ""} `
        + "+datum=WGS84 +units=m +no_defs";
      const proj = proj4("EPSG:4326", def);
      for (const tag of ["ModelTiepoint", "ModelPixelScale", "ModelTransformation"]) {
        if (base.fileDirectory.hasTag(tag)) await base.fileDirectory.loadValue(tag);
      }
      const [ox, oy] = base.getOrigin();
      const [rx, ry] = base.getResolution();
      const w = base.getWidth(), h = base.getHeight();
      const levels = images.map((image) => ({
        image, scale: w / image.getWidth(),
        w: image.getWidth(), h: image.getHeight(),
      })).sort((a, b) => a.scale - b.scale);
      // Reads of this COG are taken one at a time; see `queued` below.
      return { href, epsg, proj, ox, oy, rx, ry, w, h, levels, gate: Promise.resolve() };
    })());
    cogs.get(key).catch(() => cogs.delete(key));
  }
  return cogs.get(key);
}

// Serialise the reads of one COG.
//
// geotiff.js reads through a source that buffers the file in 64 KiB blocks, and
// that buffer is shared by every read of the image. A viewport at high zoom asks
// for fifteen tiles at once, each needing three bands, so forty-five reads start
// together — and at these zooms they almost all want the *same* block, because
// one 1024-pixel block of a coarse overview covers the whole screen. Forty-five
// concurrent requests for one block is what the blocked source handles badly:
// measured, all forty-five entered the read and not one ever returned.
//
// One at a time per COG is both the fix and the faster path. The first read
// fetches the block; the rest find it already buffered and return without
// touching the network, which is what would have happened anyway had they not
// all started at once.
function queued(cog, fn) {
  const run = cog.gate.then(fn, fn);
  // The gate must not reject, or one failed read would wedge the COG forever.
  cog.gate = run.then(() => {}, () => {});
  return run;
}

// --- The warp --------------------------------------------------------------

// The inverse mapping of a TILE x TILE raster covering `ext` (Mercator metres)
// into the COG's base pixel grid: a control grid about CELL output pixels
// apart, plus the pixel extent it spans.
function controlGrid(cog, ext) {
  const nx = Math.ceil(TILE / CELL), ny = nx, N = nx + 1;
  const gx = new Float64Array(N * (ny + 1)), gy = new Float64Array(N * (ny + 1));
  let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
  for (let j = 0; j <= ny; j++) {
    const my = ext.north - ((ext.north - ext.south) * j) / ny;
    for (let i = 0; i <= nx; i++) {
      const mx = ext.west + ((ext.east - ext.west) * i) / nx;
      const [lon, lat] = merToLngLat(mx, my);
      const [X, Y] = cog.proj.forward([lon, lat]);
      const px = (X - cog.ox) / cog.rx, py = (Y - cog.oy) / cog.ry;
      gx[j * N + i] = px; gy[j * N + i] = py;
      if (px < minx) minx = px; if (px > maxx) maxx = px;
      if (py < miny) miny = py; if (py > maxy) maxy = py;
    }
  }
  return { gx, gy, nx, ny, minx, miny, maxx, maxy };
}

// The overview window that best matches the tile's own resolution, as a source
// raster; null when the grid misses the image.
//
// No AbortSignal is passed to the read, deliberately. geotiff.js reads through a
// blocked source whose 64 KiB blocks are shared by every tile of this cell, and
// aborting one tile's read abandons blocks that other tiles are already waiting
// on — their promises then never settle. MapLibre cancels tile requests freely
// as the camera moves, so wiring its signal through here deadlocks the tier
// within a few frames: measured, every tile requested and not one ever resolved.
// Letting a cancelled tile's bytes land and stay cached wastes a little
// bandwidth when panning and costs nothing when the camera comes back.
//
// "Best matches" is the nearest level in log2, not the nearest level that is no
// coarser. That distinction is worth a factor of four in bytes and it is the
// difference between this tier being usable and not. These COGs are blocked at
// 1024 pixels — 10.24 km of ground per block at 10 m — so one block is already
// about one map tile at z12, and stepping one level finer than needed quadruples
// every block read for a picture that is then thrown away in the downsample.
// Measured against the published imagery, choosing level 2 where level 4 matched
// turned a viewport into tens of megabytes and minutes.
async function readWindow(cog, grid) {
  const { minx, miny, maxx, maxy } = grid;
  if (maxx <= 0 || maxy <= 0 || minx >= cog.w || miny >= cog.h) return null;
  const want = Math.max(maxx - minx, maxy - miny) / TILE;
  const target = Math.log2(Math.max(want, 1e-6));
  let lvl = cog.levels[0];
  let best = Infinity;
  for (const l of cog.levels) {
    const d = Math.abs(Math.log2(l.scale) - target);
    if (d < best) { best = d; lvl = l; }
  }
  const s = lvl.scale;
  const x0 = Math.max(0, Math.floor(minx / s)), y0 = Math.max(0, Math.floor(miny / s));
  const x1 = Math.min(lvl.w, Math.ceil(maxx / s) + 1);
  const y1 = Math.min(lvl.h, Math.ceil(maxy / s) + 1);
  if (x1 <= x0 || y1 <= y0) return null;
  sceneStats.lastWindow = { level: lvl.scale, lw: lvl.w, lh: lvl.h, win: [x0, y0, x1, y1], want };
  const raster = await queued(cog, () => lvl.image.readRasters({
    window: [x0, y0, x1, y1], interleave: true,
  }));
  return { data: raster, w: raster.width, h: raster.height, scale: s, x0, y0 };
}

// One band's samples placed into a TILE x TILE plane of floats. NaN where the
// output pixel falls outside the source; the file's own nodata (-32768) is
// carried through untouched, so whether it is keyed out stays the stretch's
// decision (bands.js) and not the warp's.
function warpPlane(grid, src) {
  const { gx, gy, nx, ny } = grid, N = nx + 1;
  const { data, w, h, scale, x0, y0 } = src;
  const plane = new Float32Array(TILE * TILE).fill(NaN);
  const cw = TILE / nx, ch = TILE / ny;
  for (let y = 0; y < TILE; y++) {
    const fy = (y + 0.5) / ch, j = Math.min(ny - 1, Math.floor(fy)), t = fy - j;
    for (let x = 0; x < TILE; x++) {
      const fx = (x + 0.5) / cw, i = Math.min(nx - 1, Math.floor(fx)), u = fx - i;
      const a = j * N + i, b = a + 1, c = a + N, d = c + 1;
      const px = (gx[a] * (1 - u) + gx[b] * u) * (1 - t) + (gx[c] * (1 - u) + gx[d] * u) * t;
      const py = (gy[a] * (1 - u) + gy[b] * u) * (1 - t) + (gy[c] * (1 - u) + gy[d] * u) * t;
      const sx = Math.floor(px / scale) - x0, sy = Math.floor(py / scale) - y0;
      if (sx < 0 || sy < 0 || sx >= w || sy >= h) continue;
      plane[y * TILE + x] = data[sy * w + sx];
    }
  }
  return plane;
}

// --- Per-cell memory -------------------------------------------------------

const planeCaches = new Map();   // "quarter/subtile" -> Map("z/x/y/band" -> plane)

function planesFor(quarter, subtile) {
  const key = `${quarter}/${subtile}`;
  if (!planeCaches.has(key)) planeCaches.set(key, new Map());
  return planeCaches.get(key);
}

export function forgetScene(quarter, subtile) {
  planeCaches.delete(`${quarter}/${subtile}`);
  for (const k of [...cogs.keys()]) {
    if (k.startsWith(`${quarter}/${subtile}/`)) cogs.delete(k);
  }
}

// --- Tiles -----------------------------------------------------------------

function tileExtent(z, x, y) {
  const size = (2 * WORLD) / 2 ** z;
  const west = -WORLD + x * size;
  const north = WORLD - y * size;
  return { west, north, east: west + size, south: north - size };
}

async function sceneTile(quarter, subtile, spec, z, x, y) {
  const ext = tileExtent(z, x, y);
  const cache = planesFor(quarter, subtile);
  const want = bandsOf(spec);
  const planes = {};

  await Promise.all(want.map(async (band) => {
    const ck = `${z}/${x}/${y}/${band}`;
    if (cache.has(ck)) { planes[band] = cache.get(ck); return; }
    let cog;
    try {
      cog = await openBand(quarter, subtile, band);
      sceneStats.opened += 1;
    } catch (err) {
      sceneStats.openFailed += 1;
      sceneStats.lastError = String(err?.message || err);
      planes[band] = null;      // a band that will not open paints transparent
      return;
    }
    const grid = controlGrid(cog, ext);
    sceneStats.gridded += 1;
    const src = await readWindow(cog, grid);
    sceneStats.windowed += 1;
    if (!src) sceneStats.noWindow += 1;
    const plane = src ? warpPlane(grid, src) : null;
    sceneStats.warped += 1;
    if (cache.size >= PLANE_CACHE) cache.delete(cache.keys().next().value);
    cache.set(ck, plane);
    planes[band] = plane;
  }));

  if (!Object.values(planes).some(Boolean)) return null;
  const image = paintRGBA(planes, spec, TILE, TILE);
  return createImageBitmap(image);
}

// Counters for the browser console: `s2.sceneStats`. A tier that reads three
// COGs and warps them per tile has more ways to come up empty than one that
// passes a block through, so it says which one happened.
export const sceneStats = {
  asked: 0, painted: 0, empty: 0, aborted: 0, failed: 0, lastError: null,
  opened: 0, openFailed: 0, gridded: 0, windowed: 0, warped: 0, noWindow: 0,
};

// `scene://{quarter}/{subtile}/{preset}/{z}/{x}/{y}`. The preset is in the URL
// so that changing it is a new tileset to MapLibre — whose tiles come straight
// back out of the plane cache above, costing a repaint and no network.
export function registerSceneProtocol(addProtocol, presetFor) {
  addProtocol("scene", async (params, abort) => {
    const m = /^scene:\/\/([^/]+)\/([^/]+)\/([^/]+)\/(\d+)\/(\d+)\/(\d+)/.exec(params.url);
    if (!m) throw new Error(`bad scene URL: ${params.url}`);
    const [, quarter, subtile, preset, z, x, y] = m;
    const spec = presetFor(preset);
    sceneStats.asked += 1;
    let bitmap = null;
    try {
      bitmap = await sceneTile(quarter, subtile, spec, +z, +x, +y);
    } catch (err) {
      if (err?.name === "AbortError") { sceneStats.aborted += 1; throw err; }
      sceneStats.failed += 1;
      sceneStats.lastError = String(err?.message || err);
      throw err;
    }
    if (bitmap) sceneStats.painted += 1; else sceneStats.empty += 1;
    // A tile the cell does not cover still has to be an image; a 1x1
    // transparent pixel is cheaper than an error MapLibre will log and retry.
    return { data: bitmap ?? await createImageBitmap(new ImageData(1, 1)) };
  });
}

export function sceneSource(quarter, subtile, presetId, bounds) {
  // `bounds: null` means the cell crosses the antimeridian and no honest
  // rectangle describes it; MapLibre rejects a null, so the key is omitted and
  // the out-of-cell tiles come back transparent instead.
  return {
    ...(bounds ? { bounds } : {}),
    type: "raster",
    tiles: [`scene://${quarter}/${subtile}/${presetId}/{z}/{x}/{y}`],
    tileSize: TILE,
    // The base grid is 10 m, which is z14 in Web Mercator at this latitude
    // band; MapLibre overzooms past that rather than asking for detail the
    // file does not hold.
    minzoom: 9,
    maxzoom: 14,
    attribution: "Copernicus Sentinel data 2017–2025",
  };
}
