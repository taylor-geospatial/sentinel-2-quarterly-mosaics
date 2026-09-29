#!/usr/bin/env python3
"""Upload one quarter's item JSON: tens of thousands of small objects.

    python3 tools/rails/upload_items.py \\
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
from publish import load_config, path_style, split_s3_uri, to_direct  # noqa: E402

# The shared uploader identity: long-lived keys that write the tge-labs
# prefix on the direct S3 bucket. See tools/rails/upload.py for why the
# profile and the addressing move together.
DEFAULT_PROFILE = "source-coop-uploader"
DEFAULT_WORKERS = 32
CONTENT_TYPE = "application/geo+json"
PROGRESS_EVERY = 2_000

_local = threading.local()
_new_client = threading.Lock()


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


def _client(session, endpoint: str | None, use_path_style: bool):
    """One boto3 client per worker thread, all from **one** Session.

    A client is safe to call from many threads but its connection pool is
    not worth sharing across 32 of them, so each worker gets its own. The
    Session, though, is deliberately shared: a `credential_process`
    profile would otherwise run its binary once per thread and give each
    worker its own credential cache to expire and refresh independently.
    One Session resolves the credentials once and every client reads them
    from it. Creating clients from a Session is not itself thread-safe,
    so a lock covers only that.

    `endpoint` names the data proxy in the proxy form and is empty in
    the direct form, whose dotted bucket name needs path-style
    addressing."""
    existing = getattr(_local, "client", None)
    if existing is not None:
        return existing
    with _new_client:
        from botocore.config import Config
        # Adaptive retries with client-side rate limiting. Measured
        # against the proxy from a laptop: after a burst of about forty
        # requests, every call from this process was answered
        # `AccessDenied` for roughly ten minutes while the same
        # credentials kept working from the aws CLI. Whatever that is, a
        # run of 28,272 objects will meet it, and the default of three
        # legacy attempts will not ride it out.
        cfg = {"retries": {"mode": "adaptive", "max_attempts": 10}}
        if use_path_style:
            cfg["s3"] = {"addressing_style": "path"}
        _local.client = session.client(
            "s3", endpoint_url=endpoint or None, config=Config(**cfg))
    return _local.client


def put_one(job) -> tuple[str, str, str]:
    """(outcome, key, detail). Never raises.

    One object out of a million failing must not cancel the other
    999,999: `ThreadPoolExecutor.map` propagates the first exception and
    abandons the rest, which on a run this size would throw away an hour
    of successful uploads over one bad response. Every failure is
    returned, counted, and named at the end, and the exit status is
    non-zero when there was any.
    """
    path, key, session, bucket, skip_existing, endpoint = job
    try:
        return _put_one(job)
    except Exception as exc:                          # noqa: BLE001
        return "failed", key, f"{type(exc).__name__}: {exc}"


def _put_one(job) -> tuple[str, str, str]:
    path, key, session, bucket, skip_existing, endpoint = job
    client = _client(session, endpoint, path_style(bucket))
    if skip_existing:
        try:
            if int(client.head_object(Bucket=bucket, Key=key)["ContentLength"]
                   ) == path.stat().st_size:
                return "skipped", key, ""
        except client.exceptions.ClientError as exc:
            if exc.response.get("Error", {}).get("Code", "") not in (
                    "404", "NoSuchKey", "NotFound"):
                raise
    client.upload_file(str(path), bucket, key,
                       ExtraArgs={"ContentType": CONTENT_TYPE})
    return "uploaded", key, ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--items-dir", required=True,
                    help="what make_items.py --items-dir wrote")
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--quarter", required=True)
    ap.add_argument("--profile",
                    default=os.environ.get("AWS_PROFILE") or DEFAULT_PROFILE)
    ap.add_argument("--key-prefix", default="")
    ap.add_argument("--endpoint",
                    help="S3 endpoint; defaults to endpoint_url in "
                         "catalog.publish.yaml")
    ap.add_argument("--via", choices=("direct", "proxy"), default="direct",
                    help="direct (default): the direct S3 bucket; proxy: "
                         "the data proxy, as the config writes it")
    ap.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    ap.add_argument("--skip-existing", action="store_true",
                    help="HEAD before each put; for resuming, not for a "
                         "first run")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    config = load_config()
    if a.via == "direct":
        config = to_direct(config)
    bucket, prefix = split_s3_uri(config["write_prefix"])
    jobs = collect(Path(a.items_dir), a.year, a.quarter, prefix, a.key_prefix)
    total_bytes = sum(p.stat().st_size for p, _ in jobs)
    say(f"{a.year} {a.quarter}: {len(jobs):,} item(s), "
        f"{total_bytes / 1e6:,.1f} MB -> s3://{bucket}/{prefix} "
        f"via {config.get('endpoint_url') or '(aws default)'}")
    if not jobs:
        return 0
    if a.dry_run:
        for _, key in jobs[:5]:
            print(f"  would put s3://{bucket}/{key}")
        print(f"  ... {len(jobs):,} object(s) in total")
        return 0

    import boto3
    session = boto3.Session(profile_name=a.profile,
                            region_name=config.get("region") or None)
    # Resolve the credentials once, on this thread, so the workers do not
    # race a `credential_process` profile's binary at the same moment.
    session.get_credentials().get_frozen_credentials()
    t0 = time.monotonic()
    done = {"uploaded": 0, "skipped": 0, "failed": 0}
    failures: list[tuple[str, str]] = []
    endpoint = a.endpoint or config.get("endpoint_url")
    work = [(p, k, session, bucket, a.skip_existing, endpoint)
            for p, k in jobs]
    with cf.ThreadPoolExecutor(a.workers) as pool:
        for n, (outcome, key, detail) in enumerate(pool.map(put_one, work),
                                                   start=1):
            done[outcome] += 1
            if outcome == "failed":
                failures.append((key, detail))
            if n % PROGRESS_EVERY == 0:
                rate = n / (time.monotonic() - t0)
                say(f"  {n:,}/{len(jobs):,} ({rate:,.0f}/s, "
                    f"{(len(jobs) - n) / rate / 60:,.1f} min left)")
    secs = time.monotonic() - t0
    say(f"{a.year} {a.quarter}: {done['uploaded']:,} uploaded, "
        f"{done['skipped']:,} skipped, {done['failed']:,} failed, "
        f"{secs / 60:,.1f} min ({len(jobs) / secs:,.0f} objects/s)")
    if failures:
        print(f"\n{len(failures)} object(s) failed:", file=sys.stderr)
        for key, detail in failures[:20]:
            print(f"  {key}: {detail}", file=sys.stderr)
        if len(failures) > 20:
            print(f"  ... and {len(failures) - 20} more", file=sys.stderr)
        print("Rerun with --skip-existing to retry only what is missing.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
