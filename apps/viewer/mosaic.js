// The quarterly overview COG, read as a Web Mercator tile archive.
//
// `mosaics/quarter=YYYY.Qn/overview.tif` is a JPEG COG with an internal
// transparency mask, already in EPSG:3857, tiled in 256-pixel blocks, with an
// internal overview pyramid. That
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
//   2. Almost no pixel work. The blocks are JPEG — chosen because
//      JPEG-in-TIFF is the most widely decoded compression there is, where
//      WebP-in-TIFF needs a GDAL built with libwebp and shows an empty layer
//      without one, and lossless DEFLATE measured 13x the bytes. A block
//      plus its IFD's shared JPEGTables is a standalone JPEG, so the bytes
//      go to `createImageBitmap` and from there to MapLibre as a texture.
//      Transparency lives in the mask IFDs; a mask tile is only decoded and
//      composited where it says anything, which on this planet is the
//      coastline blocks. geotiff.js is used here only to parse the IFDs,
//      which is what it is good at. (DEFLATE and WebP overviews still
//      render, dispatched on the Compression tag, so older builds of the
//      layer keep working.)
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
      const all = [];
      for (let i = 0; i < count; i++) all.push(await tiff.getImage(i));

      // A JPEG COG stores its transparency as mask IFDs — one per level,
      // flagged by NewSubfileType bit 2 — interleaved into the same chain
      // as the imagery. Split the chain, and pair each imagery level with
      // the mask of its own dimensions.
      const isMask = [];
      for (const image of all) {
        const dir = image.fileDirectory;
        const sub = dir.hasTag("NewSubfileType")
          ? Number(await dir.loadValue("NewSubfileType")) : 0;
        isMask.push((sub & 4) !== 0);
      }
      const images = all.filter((_, i) => !isMask[i]);
      const masks = all.filter((_, i) => isMask[i]);

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
      // carry no georeferencing of their own. JPEG levels each carry their own
      // shared huffman and quantisation tables, loaded here once.
      const levels = await Promise.all(images.map(async (image) => ({
        image,
        mask: masks.find((m) => m.getWidth() === image.getWidth()
          && m.getHeight() === image.getHeight()) ?? null,
        tables: image.fileDirectory.hasTag("JPEGTables")
          ? new Uint8Array(await image.fileDirectory.loadValue("JPEGTables"))
          : null,
        scale: w / image.getWidth(),
        w: image.getWidth(),
        h: image.getHeight(),
        bw: image.getTileWidth(),
        bh: image.getTileHeight(),
      })));
      levels.sort((a, b) => a.scale - b.scale);

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

      // The compression and predictor decide how blockImage() turns a
      // block's bytes into pixels; both are constant across the file.
      const compression = Number(await base.fileDirectory.loadValue("Compression"));
      const predictor = base.fileDirectory.hasTag("Predictor")
        ? Number(await base.fileDirectory.loadValue("Predictor")) : 1;

      return { href, ox, oy, rx, ry, w, h, levels, baseZoom, bounds,
        compression, predictor, samples: base.getSamplesPerPixel() };
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

// --- Block decode ----------------------------------------------------------

const DEFLATE = new Set([8, 32946]);      // Adobe deflate, and the older code
const JPEG = new Set([6, 7]);             // old- and new-style JPEG
const WEBP = 50001;
const HORIZONTAL_PREDICTOR = 2;

async function inflate(buf) {
  const stream = new Blob([buf]).stream()
    .pipeThrough(new DecompressionStream("deflate"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

// Undo TIFF predictor 2: each stored byte is the difference from the same
// sample one pixel to the left, so a row is rebuilt by one additive pass.
function unpredict(data, width, height, samples) {
  const rowBytes = width * samples;
  for (let y = 0; y < height; y++) {
    const row = y * rowBytes;
    for (let i = samples; i < rowBytes; i++) {
      data[row + i] = (data[row + i] + data[row + i - samples]) & 255;
    }
  }
}

// A JPEG-in-TIFF tile is an abbreviated stream: its huffman and quantisation
// tables live once in the IFD's JPEGTables tag. Prepending the tables (minus
// their EOI marker) to the tile (minus its SOI marker) makes a standalone
// JPEG that the browser decodes directly, so the no-pixel-work path
// survives the compression change.
function standaloneJpeg(tables, buf) {
  const tile = new Uint8Array(buf);
  if (!tables) return tile;
  const out = new Uint8Array(tables.length - 2 + tile.length - 2);
  out.set(tables.subarray(0, tables.length - 2), 0);
  out.set(tile.subarray(2), tables.length - 2);
  return out;
}

// The alpha of one block, from the level's mask IFD: DEFLATE-compressed
// 1-bit tiles, MSB first. Returns null when the block is fully valid —
// an absent mask tile means all-valid, because the writer only omits
// all-zero tiles, and those have no imagery tile either — so the caller
// can skip the canvas round trip for most of the planet's land.
async function maskAlpha(cog, level, bx, by, signal) {
  if (!level.mask) return null;
  const m = { image: level.mask, w: level.w, bw: level.bw, href: cog.href };
  const buf = await blockBytes(m, bx, by, signal).catch(() => null);
  if (!buf || !buf.byteLength) return null;
  let packed;
  try {
    packed = await inflate(buf);
  } catch {
    return null;
  }
  let and = 0xff;
  for (let i = 0; i < packed.length; i++) and &= packed[i];
  if (and === 0xff) return null;
  const n = level.bw * level.bh;
  const alpha = new Uint8Array(n);
  for (let i = 0; i < n; i++) {
    alpha[i] = (packed[i >> 3] >> (7 - (i & 7))) & 1 ? 255 : 0;
  }
  return alpha;
}

// One block's bytes to an ImageBitmap. JPEG stitches the shared tables back
// on and lets the browser decode, with the mask applied as alpha only where
// the mask says anything. DEFLATE inflates to bytes already in ImageData's
// RGBA layout. A WebP block is a complete WebP image. TIFF blocks are always
// padded to full size, so the dimensions are the block's, never the image
// edge's.
async function blockImage(cog, level, buf, bx, by, signal) {
  if (cog.compression === WEBP) {
    return createImageBitmap(new Blob([buf], { type: "image/webp" }));
  }
  if (JPEG.has(cog.compression)) {
    const [bitmap, alpha] = await Promise.all([
      createImageBitmap(new Blob([standaloneJpeg(level.tables, buf)],
        { type: "image/jpeg" })),
      maskAlpha(cog, level, bx, by, signal),
    ]);
    if (!alpha) return bitmap;
    const canvas = new OffscreenCanvas(level.bw, level.bh);
    const ctx = canvas.getContext("2d");
    ctx.drawImage(bitmap, 0, 0);
    bitmap.close();
    const id = ctx.getImageData(0, 0, level.bw, level.bh);
    const px = id.data;
    for (let i = 0; i < alpha.length; i++) px[i * 4 + 3] = alpha[i];
    return createImageBitmap(id);
  }
  if (!DEFLATE.has(cog.compression)) {
    throw new Error(`overview compression ${cog.compression} is not handled`);
  }
  const raw = await inflate(buf);
  if (cog.predictor === HORIZONTAL_PREDICTOR) {
    unpredict(raw, level.bw, level.bh, cog.samples);
  }
  const rgba = new Uint8ClampedArray(
    raw.buffer, raw.byteOffset, level.bw * level.bh * cog.samples);
  return createImageBitmap(new ImageData(rgba, level.bw, level.bh));
}

// --- Tiles -----------------------------------------------------------------

// The 3857 extent of a slippy tile, in metres.
function tileExtent(z, x, y) {
  const size = (2 * WORLD) / 2 ** z;
  const west = -WORLD + x * size;
  const north = WORLD - y * size;
  return { west, north, east: west + size, south: north - size, size };
}

// --- The filmstrip's tile cache --------------------------------------------

// Decoded tiles, kept so the filmstrip can repaint after a camera move
// without a network read. A move wipes the frames, and MapLibre re-requests
// nothing it already holds, so without this the frames stay blank exactly
// when everything is loaded. MapLibre owns the bitmaps it is handed and may
// consume them, so the cache holds its own clones — ~60 MB at the cap, the
// oldest closed and dropped first. Purely passive: a repaint paints what is
// here and fetches nothing.
const STRIP_CACHE_MAX = 240;
const stripCache = new Map();   // "quarter/z/x/y" -> ImageBitmap (our clone)

async function rememberTile(quarter, z, x, y, bitmap) {
  if (bitmap.width > 512 || bitmap.height > 512) return;
  let clone;
  try {
    clone = await createImageBitmap(bitmap);
  } catch {
    return;
  }
  const key = `${quarter}/${z}/${x}/${y}`;
  stripCache.get(key)?.close();
  stripCache.delete(key);
  stripCache.set(key, clone);
  if (stripCache.size > STRIP_CACHE_MAX) {
    const [oldest, old] = stripCache.entries().next().value;
    stripCache.delete(oldest);
    old.close();
  }
}

// Every cached tile of `quarter` that intersects `extent`, at the finest
// zoom that has any. Synchronous and network-free; touching an entry
// marks it recently used.
export function cachedTiles(quarter, extent, maxZoom) {
  for (let z = Math.max(0, Math.min(maxZoom, 22)); z >= 0; z--) {
    const size = (2 * WORLD) / 2 ** z;
    const x0 = Math.max(0, Math.floor((extent.west + WORLD) / size));
    const x1 = Math.min(2 ** z - 1, Math.floor((extent.east + WORLD) / size));
    const y0 = Math.max(0, Math.floor((WORLD - extent.north) / size));
    const y1 = Math.min(2 ** z - 1, Math.floor((WORLD - extent.south) / size));
    if ((x1 - x0 + 1) * (y1 - y0 + 1) > 16) continue;
    const hits = [];
    for (let ty = y0; ty <= y1; ty++) {
      for (let tx = x0; tx <= x1; tx++) {
        const key = `${quarter}/${z}/${tx}/${ty}`;
        const bitmap = stripCache.get(key);
        if (bitmap) {
          stripCache.delete(key);
          stripCache.set(key, bitmap);
          hits.push({ bitmap, ext: tileExtent(z, tx, ty) });
        }
      }
    }
    if (hits.length) return hits;
  }
  return [];
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
    const bitmap = await blockImage(cog, lvl, buf, bx, by, signal).catch(() => null);
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

  await rememberTile(quarter, z, x, y, out);
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
