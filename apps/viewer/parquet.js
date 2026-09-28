// Range reads over remote parquet, with no query engine in the page.
//
// Adapted from the s2-stac-geoparquet explorer's search.js. Everything that
// module learned the hard way is kept, because every one of those lessons was
// measured against this same object store:
//
//   * Every range read is `cache: "no-store"`. Chrome serialises concurrent
//     requests for one URL behind its HTTP cache lock — only one of them may
//     write the entry — so the parallel column-chunk reads of a single file
//     arrive in a staircase instead of together. Taking the reads out of the
//     cache entirely was worth 2.1 s -> 0.4 s for the same eight reads of the
//     same object. It sends no extra request header, so the bytes and the edge
//     cache entry are exactly what they were.
//   * The footer is found with one speculative 64 KiB tail read. The 8-byte
//     trailer at the end of a parquet file names the footer's length, so
//     reading only those 8 bytes costs a second round trip for the footer
//     itself. 64 KiB normally holds both.
//   * Row groups are admitted by the key column's statistics before any data
//     is fetched, and a group with no statistics is admitted rather than
//     skipped — correctness over bytes.
//   * hyparquet decodes through an AsyncBuffer over the already-fetched byte
//     regions, so the decode issues no network read of its own; a miss falls
//     through to the network so a decode can never fail, and is counted.
//
// Dropped from the donor: sidecar (.idx.json) support. This catalog publishes
// none — 28k rows per partition put the whole footer inside the tail read —
// and probing for one would add a 404 to every first read.
import { parquetMetadata, parquetReadObjects } from "https://cdn.jsdelivr.net/npm/hyparquet@1.31.2/+esm";
import { compressors } from "https://cdn.jsdelivr.net/npm/hyparquet-compressors@1.1.2/+esm";

const TAIL_BYTES = 64 * 1024;
const MAX_IN_FLIGHT = 24;

export async function rangeGet(url, start, len, signal) {
  const res = await fetch(url, {
    cache: "no-store",
    signal,
    headers: { Range: `bytes=${start}-${start + len - 1}` },
  });
  // 200 means the server ignored the Range header and is sending the whole
  // object. Nothing in this app can afford that, so it is an error, not a
  // slow success. (This is what `python3 -m http.server` does; see
  // devserver.py.)
  if (res.status !== 206) throw new Error(`range read of ${url} got HTTP ${res.status}`);
  return res.arrayBuffer();
}

// Is there an object at this URL? One byte, and the Content-Range names the
// full size, so an availability probe and a size lookup are the same request.
// Absent is an answer, not a failure: most quarters are simply not published
// yet and the timeline has to say so.
export async function probe(url, signal) {
  try {
    const res = await fetch(url, {
      cache: "no-store",
      signal,
      headers: { Range: "bytes=0-0" },
    });
    if (res.status === 404 || res.status === 403) return null;
    if (res.status !== 206 && res.status !== 200) return null;
    await res.arrayBuffer();
    const total = Number(res.headers.get("content-range")?.split("/")[1]);
    return { size: Number.isFinite(total) ? total : null };
  } catch {
    return null;
  }
}

// One metadata fetch per file per session: a speculative tail read, the footer
// parsed out of it, and the per-row-group column chunk ranges laid out once.
const metaCache = new Map();

export function fileMeta(url) {
  if (!metaCache.has(url)) {
    metaCache.set(url, (async () => {
      const tail = await fetch(url, { cache: "no-store", headers: { Range: `bytes=-${TAIL_BYTES}` } });
      if (tail.status === 404 || tail.status === 403) {
        return { url, absent: true, size: 0, footerOff: 0, footer: new ArrayBuffer(0), metadata: null, groups: [] };
      }
      if (tail.status !== 206) throw new Error(`range read of ${url} got HTTP ${tail.status}`);
      const size = Number(tail.headers.get("content-range")?.split("/")[1]);
      const buf = await tail.arrayBuffer();
      if (!Number.isFinite(size) || buf.byteLength < 8) throw new Error(`no usable Content-Range from ${url}`);
      const footerLen = new DataView(buf).getUint32(buf.byteLength - 8, true) + 8;
      if (footerLen > size) throw new Error(`${url} names a ${footerLen}-byte footer in ${size} bytes`);
      const footerOff = size - footerLen;
      const footer = footerLen <= buf.byteLength
        ? buf.slice(buf.byteLength - footerLen)
        : await rangeGet(url, footerOff, footerLen);
      const metadata = parquetMetadata(footer);
      let row = 0;
      const groups = metadata.row_groups.map((g) => {
        const chunks = g.columns.map((c) => {
          const m = c.meta_data;
          return {
            column: m.path_in_schema[0],
            off: Number(m.dictionary_page_offset ?? m.data_page_offset),
            len: Number(m.total_compressed_size),
            stats: m.statistics,
          };
        });
        const out = { row0: row, row1: row + Number(g.num_rows), chunks };
        row += Number(g.num_rows);
        return out;
      });
      return { url, size, footerOff, footer, metadata, groups };
    })());
    metaCache.get(url).catch(() => metaCache.delete(url));
  }
  return metaCache.get(url);
}

const decodeStat = (v) => (typeof v === "string" ? v : v == null ? null : new TextDecoder().decode(v));

// The row groups whose key range cannot exclude `key`. A group carrying no
// statistics is admitted: a wrong answer costs more than a wasted read.
function admitted(meta, keyColumn, key) {
  return meta.groups.filter((g) => {
    const chunk = g.chunks.find((c) => c.column === keyColumn);
    if (!chunk) return true;
    const min = decodeStat(chunk.stats?.min_value);
    const max = decodeStat(chunk.stats?.max_value);
    if (min == null || max == null) return true;
    return min <= key && key <= max;
  });
}

// An AsyncBuffer over byte regions already in memory. A miss falls through to
// the network rather than failing, and is tallied so the read plan can admit
// it happened.
function regionBuffer(url, size, regions, tally) {
  return {
    byteLength: size,
    async slice(start, end) {
      for (const r of regions) {
        if (start >= r.off && end <= r.off + r.buf.byteLength) {
          return r.buf.slice(start - r.off, end - r.off);
        }
      }
      tally.misses += 1;
      const buf = await rangeGet(url, start, end - start);
      tally.gets += 1;
      tally.bytes += buf.byteLength;
      return buf;
    },
  };
}

// Every row whose `keyColumn` equals `key`, from a file sorted on that column.
//
// This is the whole viewer's read pattern, and the catalog is laid out for it:
// coverage/tiles.parquet is sorted subtile-major, so one subtile's entire
// 36-quarter history is a contiguous run inside one or two row groups, and
// asking for it costs a footer (once per session) plus a handful of parallel
// column-chunk GETs. The tile inspector's timeline is one call to this.
export async function keyedRows({ url, keyColumn, key, columns }) {
  const t0 = performance.now();
  const tally = { groups: 0, gets: 0, bytes: 0, misses: 0 };
  const meta = await fileMeta(url);
  if (meta.absent) return { rows: [], plan: `${url} is not published` };
  const groups = admitted(meta, keyColumn, key);
  tally.groups = groups.length;
  if (!groups.length) return { rows: [], plan: "no row group could hold that key" };

  const wanted = [...new Set([keyColumn, ...columns])];
  const jobs = groups.flatMap((g) => g.chunks.filter((c) => wanted.includes(c.column)));
  const regions = [{ off: meta.footerOff, buf: meta.footer }];
  let next = 0;
  await Promise.all(Array.from({ length: Math.min(MAX_IN_FLIGHT, jobs.length) }, async () => {
    while (next < jobs.length) {
      const job = jobs[next];
      next += 1;
      const buf = await rangeGet(url, job.off, job.len);
      tally.gets += 1;
      tally.bytes += buf.byteLength;
      regions.push({ off: job.off, buf });
    }
  }));

  const file = regionBuffer(url, meta.size, regions, tally);
  const parts = await Promise.all(groups.map((g) => parquetReadObjects({
    file, metadata: meta.metadata, compressors, columns: wanted,
    rowStart: g.row0, rowEnd: g.row1,
  })));
  const rows = parts.flat().filter((r) => r[keyColumn] === key);
  const secs = ((performance.now() - t0) / 1000).toFixed(1);
  const plan = `${tally.groups} row group(s) admitted by ${keyColumn} statistics, `
    + `${tally.gets} parallel range GETs, ${(tally.bytes / 1024).toFixed(0)} KiB, ${secs} s`
    + (tally.misses ? ` (${tally.misses} read fell outside the prefetch)` : "");
  return { rows, plan };
}

// Resolve a file's footer in the background so the first read that needs it
// finds it cached. Failures are swallowed; the read itself will surface them.
export function warm(url) {
  fileMeta(url).catch(() => {});
}
