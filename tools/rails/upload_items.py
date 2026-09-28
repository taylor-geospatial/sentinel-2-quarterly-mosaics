#!/usr/bin/env python3
"""Upload one quarter's item JSON: tens of thousands of small objects.

    AWS_PROFILE=source-coop python3 tools/rails/upload_items.py \\
        --items-dir /u/cholmes/s2-mosaics/work/items --year 2024 --quarter Q2

Each item lands beside the four COGs it describes, at
`{year}/{Qn}/{tile}/{item_id}.json`, so a client that found a tile in the
bucket finds its metadata in the same place.

Why this is not `upload.py`. That script is built for a handful of large
files: it HEADs each one, uses multipart, and verifies the size
afterwards. A quarter is about 28,000 objects of 6 kB, and 36 quarters is
a million. At one round trip of roughly 240 ms, a serial loop would spend
two hours per quarter waiting rather than sending. This one runs a pool
of workers, and the pool size is the only thing that matters.

Idempotent in the way that counts at this scale: `--skip-existing` HEADs
before putting, which makes a resumed job cheap but doubles the request
count, so it is off by default. A plain rerun simply overwrites, which
costs the same as the first run and is correct, because the item JSON is
a pure function of the manifest -- the same input gives the same bytes.
Use `--skip-existing` when resuming a job that died most of the way
through, and not otherwise.

`--dry-run` counts the objects and prints the first few keys without
opening a session, which is how to check the key layout before sending a
million things.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import os
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from publish import load_config, split_s3_uri  # noqa: E402

DEFAULT_PROFILE = "source-coop"
DEFAULT_WORKERS = 32
CONTENT_TYPE = "application/geo+json"
PROGRESS_EVERY = 2_000

_local = threading.local()


def say(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def collect(items_dir: Path, year: int, quarter: str, prefix: str,
            key_prefix: str) -> list[tuple[Path, str]]:
    """(local path, object key) for every item JSON of one quarter."""
    root = items_dir.resolve() / str(year) / quarter
    if not root.is_dir():
        sys.exit(f"{root}: no item JSON there; run make_items.py --items-dir")
    parts = [p for p in (prefix, key_prefix.strip("/")) if p]
    out = []
    for path in sorted(root.rglob("*.json")):
        if path.name.startswith("."):
            continue
        rel = path.relative_to(items_dir.resolve()).as_posix()
        out.append((path, "/".join(parts + [rel])))
    return out


def _client(profile: str, region: str | None):
    """One boto3 client per worker thread. A client is thread-safe for
    calls but the connection pool is not worth sharing across 32 workers,
    and a per-thread client keeps each one's pool to itself."""
    if not hasattr(_local, "client"):
        import boto3
        _local.client = boto3.Session(
            profile_name=profile, region_name=region or None).client("s3")
    return _local.client


def put_one(job: tuple[Path, str, str, str | None, str, bool]) -> str:
    path, key, profile, region, bucket, skip_existing = job
    client = _client(profile, region)
    if skip_existing:
        try:
            if int(client.head_object(Bucket=bucket, Key=key)["ContentLength"]
                   ) == path.stat().st_size:
                return "skipped"
        except client.exceptions.ClientError as exc:
            if exc.response.get("Error", {}).get("Code", "") not in (
                    "404", "NoSuchKey", "NotFound"):
                raise
    client.upload_file(str(path), bucket, key,
                       ExtraArgs={"ContentType": CONTENT_TYPE})
    return "uploaded"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--items-dir", required=True,
                    help="what make_items.py --items-dir wrote")
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--quarter", required=True)
    ap.add_argument("--profile",
                    default=os.environ.get("AWS_PROFILE") or DEFAULT_PROFILE)
    ap.add_argument("--key-prefix", default="")
    ap.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    ap.add_argument("--skip-existing", action="store_true",
                    help="HEAD before each put; for resuming, not for a "
                         "first run")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    config = load_config()
    bucket, prefix = split_s3_uri(config["write_prefix"])
    jobs = collect(Path(a.items_dir), a.year, a.quarter, prefix, a.key_prefix)
    total_bytes = sum(p.stat().st_size for p, _ in jobs)
    say(f"{a.year} {a.quarter}: {len(jobs):,} item(s), "
        f"{total_bytes / 1e6:,.1f} MB -> s3://{bucket}/{prefix}")
    if not jobs:
        return 0
    if a.dry_run:
        for _, key in jobs[:5]:
            print(f"  would put s3://{bucket}/{key}")
        print(f"  ... {len(jobs):,} object(s) in total")
        return 0

    region = config.get("region")
    t0 = time.monotonic()
    done = {"uploaded": 0, "skipped": 0}
    work = [(p, k, a.profile, region, bucket, a.skip_existing) for p, k in jobs]
    with cf.ThreadPoolExecutor(a.workers) as pool:
        for n, outcome in enumerate(pool.map(put_one, work), start=1):
            done[outcome] += 1
            if n % PROGRESS_EVERY == 0:
                rate = n / (time.monotonic() - t0)
                say(f"  {n:,}/{len(jobs):,} ({rate:,.0f}/s, "
                    f"{(len(jobs) - n) / rate / 60:,.1f} min left)")
    secs = time.monotonic() - t0
    say(f"{a.year} {a.quarter}: {done['uploaded']:,} uploaded, "
        f"{done['skipped']:,} skipped, {secs / 60:,.1f} min "
        f"({len(jobs) / secs:,.0f} objects/s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
