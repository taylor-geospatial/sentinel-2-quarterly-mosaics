#!/usr/bin/env python3
"""A static file server that answers Range requests, for developing the viewer
against local catalog artifacts.

`python3 -m http.server` ignores the Range header and answers 200 with the whole
file. Every read this app makes is a range read — parquet footers and column
chunks, COG headers and tile blocks, PMTiles directories — and they all reject a
200 on purpose, because a 200 means the whole object is coming down. So the
stdlib server cannot host the dev data at all, and this exists instead.

    python3 apps/viewer/devserver.py --data /path/to/publish --port 8787

Then open the viewer with ?base=http://127.0.0.1:8787 pointed at a tree laid out
like the published product:

    mosaics/quarter=YYYY.Qn/{items.parquet,overview.tif,thumbnail.webp}
    coverage/{tiles.parquet,footprints.pmtiles}

Serves the app directory at / and the data tree at whatever --data-prefix says
(default /data), so one origin covers both and no CORS is involved. CORS headers
are sent anyway, so the data tree can also be used from a viewer served
elsewhere.
"""

import argparse
import os
import re
import socketserver
from http.server import SimpleHTTPRequestHandler
from pathlib import Path

RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")

# The published product is served with these, and the app's behaviour depends on
# them (geotiff.js sniffs by extension-free content type in places, and a wrong
# type on the parquet is harmless but confusing in the network panel).
TYPES = {
    ".tif": "image/tiff",
    ".parquet": "application/vnd.apache.parquet",
    ".pmtiles": "application/octet-stream",
    ".webp": "image/webp",
    ".json": "application/json",
    ".js": "text/javascript",
    ".css": "text/css",
    ".html": "text/html",
}


class Handler(SimpleHTTPRequestHandler):
    # HTTP/1.1 so the browser keeps connections alive. Under the stdlib default
    # of HTTP/1.0 every one of the hundreds of range reads this app makes opens
    # and closes its own socket; the listen backlog fills, and new connections
    # simply hang while the server looks healthy to curl. Every response below
    # sends an accurate Content-Length, which is what makes keep-alive safe.
    protocol_version = "HTTP/1.1"
    app_dir = "."
    data_dir = None
    data_prefix = "/data"

    def translate_path(self, path):
        clean = path.split("?", 1)[0].split("#", 1)[0]
        if self.data_dir and (clean == self.data_prefix or clean.startswith(self.data_prefix + "/")):
            rel = clean[len(self.data_prefix):].lstrip("/")
            root = self.data_dir
        else:
            rel = clean.lstrip("/")
            root = self.app_dir
        # Resolve inside the root and refuse anything that climbs out of it.
        target = (Path(root) / rel).resolve()
        root_res = Path(root).resolve()
        if root_res != target and root_res not in target.parents:
            return str(root_res)
        return str(target)

    def guess_type(self, path):
        return TYPES.get(Path(path).suffix.lower(), "application/octet-stream")

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Expose-Headers", "Content-Range, Content-Length")
        self.send_header("Accept-Ranges", "bytes")
        # A dev server that lets the browser cache would hide every rebuild of
        # the data behind a stale entry.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Range, Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        rng = self.headers.get("Range")
        if not rng:
            return super().do_GET()
        path = self.translate_path(self.path)
        if os.path.isdir(path):
            return super().do_GET()
        try:
            size = os.path.getsize(path)
        except OSError:
            self.send_error(404, "not found")
            return
        m = RANGE_RE.match(rng.strip())
        if not m:
            self.send_error(400, "malformed Range")
            return
        first, last = m.group(1), m.group(2)
        if first == "":
            # "bytes=-N" is the last N bytes — the speculative parquet tail read.
            n = int(last or 0)
            start, end = max(0, size - n), size - 1
        else:
            start = int(first)
            end = int(last) if last else size - 1
            end = min(end, size - 1)
        if start > end or start >= size:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        length = end - start + 1
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(length))
        self.end_headers()
        with open(path, "rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                chunk = fh.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def log_message(self, fmt, *args):
        if os.environ.get("DEVSERVER_QUIET"):
            return
        super().log_message(fmt, *args)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    # The stdlib default backlog is 5, which a browser opening six connections
    # per origin exhausts on its own.
    request_queue_size = 128


def main():
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--app", default=str(here), help="directory served at /")
    ap.add_argument("--data", default=None, help="catalog tree served at --data-prefix")
    ap.add_argument("--data-prefix", default="/data")
    args = ap.parse_args()

    Handler.app_dir = args.app
    Handler.data_dir = args.data
    Handler.data_prefix = "/" + args.data_prefix.strip("/")

    with Server((args.host, args.port), Handler) as httpd:
        print(f"app   http://{args.host}:{args.port}/  ({args.app})")
        if args.data:
            print(f"data  http://{args.host}:{args.port}{Handler.data_prefix}/  ({args.data})")
            print(f"open  http://{args.host}:{args.port}/?base=http://{args.host}:{args.port}{Handler.data_prefix}")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
