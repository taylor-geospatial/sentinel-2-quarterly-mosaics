// The quarterly overview COG, read as a Web Mercator tile archive.
//
// `mosaics/quarter=YYYY.Qn/overview.tif` is an RGBA WebP COG already in
// EPSG:3857, tiled in 256-pixel blocks, with an internal overview pyramid. That
// combination is not just convenient — it means the file is *already* a tile
// pyramid, and the pipeline aligns its origin and base resolution to the Web
// Mercator tile grid, so a map tile and an internal block are usually the same
// 256x256 square of pixels. Serving a tile is then: work out which block holds
// it, range-read exactly that block's bytes, and hand them to the browser.
//
// Three consequences, and they are the reason this app can scrub:
//
//   1. No warping. The donor explorer had to inverse-map every output pixel
//      through proj4 from the scene's UTM grid; here source and destination are
//      the same projection, so there is nothing to resample and nothing to get
//      subtly wrong.
//   2. No pixel work at all. geotiff.js can decode these rasters — it does
//      register a WebP decoder, which hands the block to the browser and reads
//      the pixels back off a canvas — but there is no reason to go round that
//      loop. A COG block of WebP-compressed pixels *is* a complete WebP image,
//      so the block's bytes can go straight to `createImageBitmap`, and from
//      there straight to MapLibre, which accepts an ImageBitmap for an image
//      resource and uploads it as a texture. Nothing is decoded to an array,
//      copied, or re-encoded on the way. geotiff.js is used here only to parse
//      the IFDs, which is what it is good at.
//   3. The same byte window every quarter. Because the grid is shared across
//      all 36 files, the tile a viewport needs from 2017 Q1 sits at the same
//      place in the pyramid as the one it needs from 2025 Q4. Prefetching the
//      viewport across time is 36 reads of the same shape, which is what makes
//      a time stack load progressively and a scrub cost nothing.
import { openCogHeaders } from "./cogsource.js";
import { overviewUrl } from "./catalog.js";
import { rangeGet } from "./parquet.js";

const WORLD = 20037508.342789244;   // half the Web Mercator extent, in metres
const TILE = 256;

// One transparent pixel, returned for a tile that misses the raster entirely.
// Throwing would be more honest but MapLibre logs an error per tile, and a
// quarter whose overview covers one country (the local smoke build) would fill
// the console with them.
let blankPromise = null;
const blank = () => (blankPromise ??= createImageBitmap(new ImageData(1, 1)));

// --- Opening ---------------------------------------------------------------

// The headers of one quarter's overview, opened once per session.
//
// `blockSize: 65536` matters as much here as it did in the donor: geotiff.js 3
// reads exactly the bytes its parser asks for unless given a block size, which
// turns five IFDs sitting in the first few KB into a dozen sequential
// round-trips. Asking for 64 KiB blocks collapses them into one or two, and the
// same block cache then merges a window's contiguous reads.
const opened = new Map();

export function openOverview(quarter) {
  if (!opened.has(quarter)) {
    opened.set(quarter, (async () => {
      const href = overviewUrl(quarter);
      const tiff = await openCogHeaders(href);
      const count = await tiff.getImageCount();
      const images = [];
      for (let i = 0; i < count; i++) images.push(await tiff.getImage(i));
      const base = images[0];
      // getOrigin/getResolution read tags synchronously and throw if the tag
      // was never loaded, so make sure they are resident first.
      for (const tag of ["ModelTiepoint", "ModelPixelScale", "ModelTransformation"]) {
        if (base.fileDirectory.hasTag(tag)) await base.fileDirectory.loadValue(tag);
      }
      const [ox, oy] = base.getOrigin();
      const [rx, ry] = base.getResolution();       // ry is negative (north-up)
      const w = base.getWidth(), h = base.getHeight();

      // Each IFD is the base image scaled by its width ratio; a COG's overviews
      // carry no georeferencing of their own.
      const levels = images.map((image) => ({
        image,
        scale: w / image.getWidth(),
        w: image.getWidth(),
        h: image.getHeight(),
        bw: image.getTileWidth(),
        bh: image.getTileHeight(),
      })).sort((a, b) => a.scale - b.scale);

      // The zoom whose tiles are this raster's base pixels. The pipeline builds
      // the overview on the Web Mercator grid, so this is an integer and the
      // pyramid lines up level for level with the map's; if a future build ever
      // drifts off the grid, the window arithmetic below still works and only
      // the one-block fast path stops firing.
      const baseZoom = Math.round(Math.log2((2 * WORLD) / (TILE * Math.abs(rx))));

      // Lon/lat bounds, so the source can tell MapLibre where not to ask.
      const toLng = (x) => (x / WORLD) * 180;
      const toLat = (y) => (Math.atan(Math.exp((y / WORLD) * Math.PI)) * 360) / Math.PI - 90;
      const x1 = ox + w * rx, y1 = oy + h * ry;
      const bounds = [toLng(Math.min(ox, x1)), toLat(Math.min(oy, y1)),
        toLng(Math.max(ox, x1)), toLat(Math.max(oy, y1))];

      return { href, ox, oy, rx, ry, w, h, levels, baseZoom, bounds,
        samples: base.getSamplesPerPixel() };
    })());
    opened.get(quarter).catch(() => opened.delete(quarter));
  }
  return opened.get(quarter);
}

// --- Block reads -----------------------------------------------------------

// One block's compressed bytes. TileOffsets and TileByteCounts are ordinary
// TIFF tags; geotiff.js exposes them per index, and from there the read is our
// own — `cache: "no-store"`, for the reason parquet.js explains at length.
async function blockBytes(level, bx, by, signal) {
  const perRow = Math.ceil(level.w / level.bw);
  const index = by * perRow + bx;
  const dir = level.image.fileDirectory;
  const off = Number(await dir.loadValueIndexed("TileOffsets", index));
  const len = Number(await dir.loadValueIndexed("TileByteCounts", index));
  if (!len) return null;                 // a block the writer left empty
  return rangeGet(level.href ?? level.image.source?.url ?? "", off, len, signal);
}

// The opened COG knows its href; carrying it on the level saves
// reaching into geotiff.js internals for every block.
const withHref = (cog, level) => ({ ...level, href: cog.href });

// --- Tiles -----------------------------------------------------------------

// The 3857 extent of a slippy tile, in metres.
function tileExtent(z, x, y) {
  const size = (2 * WORLD) / 2 ** z;
  const west = -WORLD + x * size;
  const north = WORLD - y * size;
  return { west, north, east: west + size, south: north - size, size };
}

// One map tile of one quarter, as an ImageBitmap.
//
// `onTile` is handed the decoded bitmap and its geographic extent before the
// bitmap is returned, which is how the filmstrip paints itself: every tile the
// map loads for any quarter is also a brush stroke in that quarter's thumbnail,
// so the strip fills in as the time stack arrives and costs nothing extra. It
// must run before the return, because MapLibre may consume the bitmap.
export async function readTile(quarter, z, x, y, signal, onTile) {
  const cog = await openOverview(quarter);
  const ext = tileExtent(z, x, y);

  // The tile's corners in base-image pixel space. Source and destination share
  // a projection, so this is two divisions and no interpolation.
  const px0 = (ext.west - cog.ox) / cog.rx;
  const px1 = (ext.east - cog.ox) / cog.rx;
  const py0 = (ext.north - cog.oy) / cog.ry;
  const py1 = (ext.south - cog.oy) / cog.ry;
  if (px1 <= 0 || py1 <= 0 || px0 >= cog.w || py0 >= cog.h) return blank();

  // The finest level whose pixels are no smaller than the tile's. Reading a
  // coarser level and upscaling is cheaper than reading a finer one and
  // throwing three quarters of it away.
  const want = (px1 - px0) / TILE;
  let level = cog.levels[0];
  for (const l of cog.levels) if (l.scale <= want + 1e-9) level = l;
  const lvl = withHref(cog, level);
  const s = level.scale;

  // The window in this level's pixels, and the blocks that cover it.
  const lx0 = px0 / s, lx1 = px1 / s, ly0 = py0 / s, ly1 = py1 / s;
  const bx0 = Math.max(0, Math.floor(lx0 / level.bw));
  const bx1 = Math.min(Math.ceil(level.w / level.bw) - 1, Math.ceil(lx1 / level.bw) - 1);
  const by0 = Math.max(0, Math.floor(ly0 / level.bh));
  const by1 = Math.min(Math.ceil(level.h / level.bh) - 1, Math.ceil(ly1 / level.bh) - 1);
  if (bx1 < bx0 || by1 < by0) return blank();

  // The fast path, and on an aligned pyramid it is the usual one: the tile is
  // exactly one block at 1:1, so the block's bytes are the tile's image and
  // nothing is composited, scaled or copied.
  const aligned = bx0 === bx1 && by0 === by1
    && level.bw === TILE && level.bh === TILE
    && Math.abs(lx0 - bx0 * TILE) < 1e-6 && Math.abs(ly0 - by0 * TILE) < 1e-6
    && Math.abs(lx1 - lx0 - TILE) < 1e-6;

  const jobs = [];
  for (let by = by0; by <= by1; by++) {
    for (let bx = bx0; bx <= bx1; bx++) jobs.push([bx, by]);
  }
  const blocks = await Promise.all(jobs.map(async ([bx, by]) => {
    const buf = await blockBytes(lvl, bx, by, signal).catch(() => null);
    if (!buf || !buf.byteLength) return null;
    // A COG block of WebP-compressed pixels is a complete WebP image. The
    // browser decodes it; no JavaScript codec is involved.
    const bitmap = await createImageBitmap(new Blob([buf], { type: "image/webp" })).catch(() => null);
    return bitmap ? { bx, by, bitmap } : null;
  }));
  const got = blocks.filter(Boolean);
  if (!got.length) return blank();

  let out;
  if (aligned && got.length === 1) {
    out = got[0].bitmap;
  } else {
    // Composite: place each block where the window puts it. Blocks are whole
    // pixels of the same grid, so this is a translate and a uniform scale.
    const canvas = new OffscreenCanvas(TILE, TILE);
    const ctx = canvas.getContext("2d");
    const k = TILE / (lx1 - lx0);
    ctx.imageSmoothingEnabled = k < 1;    // smooth when shrinking, keep pixels crisp when growing
    ctx.imageSmoothingQuality = "high";
    for (const { bx, by, bitmap } of got) {
      ctx.drawImage(bitmap,
        (bx * level.bw - lx0) * k, (by * level.bh - ly0) * k,
        bitmap.width * k, bitmap.height * k);
      bitmap.close();
    }
    out = await createImageBitmap(canvas);
  }

  onTile?.(quarter, out, ext, z);
  return out;
}

// --- MapLibre protocol -----------------------------------------------------

// `mosaic://{quarter}/{z}/{x}/{y}`. Registered once; the callback is how the
// filmstrip subscribes to every tile the map decodes.
export function registerMosaicProtocol(addProtocol, onTile) {
  addProtocol("mosaic", async (params, abort) => {
    const m = /^mosaic:\/\/([^/]+)\/(\d+)\/(\d+)\/(\d+)/.exec(params.url);
    if (!m) throw new Error(`bad mosaic URL: ${params.url}`);
    const [, quarter, z, x, y] = m;
    const data = await readTile(quarter, +z, +x, +y, abort.signal, onTile);
    return { data };
  });
}

// The raster source definition for one quarter. `maxzoom` is the raster's own
// base zoom: there is nothing finer in the file, so letting MapLibre overzoom
// the deepest real level is both sharper-looking and strictly fewer requests
// than asking for tiles that could only be upscaled anyway.
export async function quarterSource(quarter) {
  const cog = await openOverview(quarter);
  return {
    type: "raster",
    tiles: [`mosaic://${quarter}/{z}/{x}/{y}`],
    tileSize: TILE,
    minzoom: 0,
    maxzoom: cog.baseZoom,
    bounds: cog.bounds,
    attribution: "Copernicus Sentinel data 2017–2025",
  };
}
