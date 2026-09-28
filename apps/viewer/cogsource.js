// Opening a COG through a fetch that does not touch Chrome's HTTP cache.
//
// This is the donor explorer's single most valuable measurement, applied to
// geotiff.js instead of to parquet. Chrome serialises concurrent requests for
// the *same URL* behind its HTTP cache lock — only one of them is allowed to
// write the cache entry, so the rest queue behind it. For a search issuing
// eight parallel column-chunk reads of one parquet file that cost 2.1 s instead
// of 0.4 s.
//
// Here it is far worse, because the same URL is read far more often. A single
// map tile at high zoom reads a window out of three band COGs; sixteen tiles in
// a viewport is 48 reads spread over three URLs, and each URL's reads are
// serialised against each other. Measured against the published imagery, a
// viewport at z12 issued its reads and never finished a tile: every one of them
// was waiting its turn on a 162 MB object.
//
// geotiff.js's built-in FetchClient calls `fetch` without cache options, so the
// fix is to hand it a client that sets `cache: "no-store"`. That takes the
// request out of the cache entirely, which is exactly what is wanted: these are
// byte ranges of a huge immutable object, the browser cache was never going to
// serve them usefully, and the CDN edge cache is untouched because no extra
// request header is sent.
import { BaseClient, BaseResponse, fromCustomClient } from "https://esm.sh/geotiff@3.0.5";

class NoStoreResponse extends BaseResponse {
  constructor(response) {
    super();
    this.response = response;
  }

  get status() { return this.response.status; }

  getHeader(name) { return this.response.headers.get(name); }

  async getData() { return this.response.arrayBuffer(); }
}

class NoStoreClient extends BaseClient {
  async request({ headers, credentials, signal } = {}) {
    const response = await fetch(this.url, {
      headers, credentials, signal, cache: "no-store",
    });
    return new NoStoreResponse(response);
  }
}

// `blockSize: 65536` is the other half of opening a COG cheaply. geotiff.js 3
// reads exactly the bytes its parser asks for unless given a block size, which
// turns the five IFDs sitting in the first few kilobytes into a dozen tiny
// sequential round-trips. Asking for 64 KiB blocks collapses those into one or
// two, and the same block cache then merges a window's contiguous reads.
export function openCogHeaders(href, options = {}) {
  return fromCustomClient(new NoStoreClient(href), {
    allowFullFile: false,
    blockSize: 65536,
    cacheSize: 64,
    ...options,
  });
}
