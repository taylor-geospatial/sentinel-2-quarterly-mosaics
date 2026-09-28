#!/usr/bin/env python3
"""Sync the published directory to object storage, 1:1.

``catalog/`` *is* the published catalog: everything under it is published, and
nothing outside it ever is. That boundary is the whole publish contract, and it
is enforced structurally — this script walks ``publish_dir`` and has no flag,
argument, or config key that widens the walk. Config lives in
``catalog.publish.yaml``.

    python3 tools/publish.py            # dry run: what would change
    python3 tools/publish.py --confirm  # upload; needs AWS credentials
    python3 tools/publish.py --confirm --force   # re-upload everything

Change detection compares local size and MD5 against the object's size and
ETag, so a normal publish uploads only what changed. Three caveats, all
inherited from what a bucket listing can tell you:

- A listing carries no Content-Type, so a file whose bytes are unchanged but
  whose content-type mapping changed is skipped. Run ``--force`` after editing
  CONTENT_TYPES.
- Multipart-uploaded objects have a compound ETag that is not an MD5. Those are
  compared on size alone.
- ``--force`` skips the listing entirely.

**It never deletes.** Removing a file from the published directory does not
unpublish it. Delete the object yourself if that is what you meant.

If boto3 is missing or the listing fails, every file is treated as changed and
a dry run still works offline. It never silently skips.

Both AWS calls go through one session, built by ``aws_session`` from the
optional ``profile`` and ``region`` keys. Source Cooperative wants a named
profile, and the default session can select the wrong account without one.

Uploads run on a bounded thread pool of MAX_UPLOAD_WORKERS threads. A failed
object is named on stderr and the run exits non-zero. One failure does not
stop the other uploads.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "catalog.publish.yaml"

# Values the template ships with. Publishing while any of these survives would
# upload to a bucket that is not yours, so it is refused before any AWS call.
SENTINELS = ("EXAMPLE-BUCKET", "EXAMPLE-PREFIX", "example.invalid")

CONTENT_TYPES = {
    ".json": "application/json",
    ".geojson": "application/geo+json",
    ".parquet": "application/vnd.apache.parquet",
    ".pmtiles": "application/vnd.pmtiles",
    ".md": "text/markdown; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".tif": "image/tiff; application=geotiff",
    ".tiff": "image/tiff; application=geotiff",
    ".laz": "application/vnd.laszip",
    ".xml": "application/xml",
    ".yaml": "application/yaml",
}
DEFAULT_TYPE = "application/octet-stream"

# MapLibre style documents are .json but carry a more specific type, which is
# what lets a browser dispatch on them.
STYLE_TYPE = "application/vnd.mapbox.style+json"

# A catalog is thousands of small JSON objects, and one round trip to
# us-west-2 costs about 238 ms. A serial loop leaves the link idle for that
# whole time. Measured on a real catalog: 4,035 objects took 23 minutes in
# serial, and 16 workers moved the remaining 1,786 objects in 46 seconds.
MAX_UPLOAD_WORKERS = 16

# Progress is printed every this many objects, not once per object, so a
# publish of thousands of files stays readable.
PROGRESS_EVERY = 100


@dataclass(frozen=True)
class Upload:
    """One local file and the object key it publishes to."""

    local: Path
    key: str
    content_type: str


def load_config(path: Path = CONFIG) -> dict[str, str]:
    """Read the flat scalar map in catalog.publish.yaml.

    Deliberately not a YAML parser. The file is a flat map of strings by
    design, so the template publishes with no dependencies at all. Do not add
    nesting to it.

    ``write_prefix``, ``public_base`` and ``publish_dir`` are required.
    ``region`` and ``profile`` are optional scalars and may be absent or empty.
    """
    config: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        config[key.strip()] = value.strip().strip("'\"")
    missing = {"write_prefix", "public_base", "publish_dir"} - config.keys()
    if missing:
        sys.exit(f"{path.name} is missing: {', '.join(sorted(missing))}")
    return config


def split_s3_uri(uri: str) -> tuple[str, str]:
    """Split s3://bucket/some/prefix into ("bucket", "some/prefix")."""
    if not uri.startswith("s3://"):
        sys.exit(f"write_prefix must start with s3:// — got {uri!r}")
    rest = uri[len("s3://"):].strip("/")
    bucket, _, prefix = rest.partition("/")
    if not bucket:
        sys.exit(f"write_prefix names no bucket — got {uri!r}")
    return bucket, prefix


def unedited_sentinels(config: dict[str, str]) -> list[str]:
    """Sentinel values still present in the config, if any."""
    blob = f"{config['write_prefix']} {config['public_base']}"
    return [s for s in SENTINELS if s in blob]


def content_type_for(path: Path) -> str:
    """The Content-Type an object gets, by suffix and by location."""
    if path.suffix == ".json" and (
        path.name.endswith(".style.json") or "styles" in path.parts
    ):
        return STYLE_TYPE
    return CONTENT_TYPES.get(path.suffix.lower(), DEFAULT_TYPE)


def is_publishable(rel: Path) -> bool:
    """False for paths the publisher skips inside the published directory.

    Dotfiles and dot-directories are skipped, which covers .gitkeep and any
    .portolan tooling state. A template cannot know whether a downstream user's
    .portolan/metadata.yaml holds something they want public, so it publishes
    none of it.
    """
    return not any(part.startswith(".") for part in rel.parts)


def collect_uploads(config: dict[str, str], root: Path = ROOT) -> list[Upload]:
    """Every file that would be uploaded, in sorted order.

    The walk is rooted at publish_dir and nothing else. This is the function
    that makes "nothing outside the published directory is published" true.
    """
    _, prefix = split_s3_uri(config["write_prefix"])
    base = root / config["publish_dir"]
    if not base.is_dir():
        sys.exit(f"publish_dir does not exist: {base}")
    uploads = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(base)
        if not is_publishable(rel):
            continue
        key = f"{prefix}/{rel.as_posix()}" if prefix else rel.as_posix()
        uploads.append(Upload(path, key, content_type_for(path)))
    return uploads


def md5(path: Path) -> str:
    digest = hashlib.md5()  # noqa: S324 - matches S3 ETag, not a security use
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_unchanged(upload: Upload, index: dict[str, tuple[int, str]]) -> bool:
    """True when the object already matches the local bytes.

    A compound (multipart) ETag is not an MD5, so those compare on size alone.
    """
    entry = index.get(upload.key)
    if entry is None:
        return False
    size, etag = entry
    if size != upload.local.stat().st_size:
        return False
    etag = etag.strip('"')
    if "-" in etag:
        return True
    return etag == md5(upload.local)


def aws_session(config: dict[str, str]):
    """The one boto3 session every AWS call in this script is built from.

    ``profile`` and ``region`` are optional. An absent or empty value means
    "let boto3 decide", which is the behavior this script had before profiles
    existed. Raises ImportError when boto3 is missing, and ProfileNotFound
    when the named profile is not in the AWS config.
    """
    import boto3

    return boto3.Session(
        profile_name=config.get("profile") or None,
        region_name=config.get("region") or None,
    )


def remote_index(
    bucket: str, prefix: str, config: dict[str, str]
) -> dict[str, tuple[int, str]]:
    """Size and ETag for every object under the prefix, or {} when unreadable."""
    try:
        client = aws_session(config).client("s3")
        index = {}
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                index[obj["Key"]] = (obj["Size"], obj["ETag"])
        return index
    except ImportError:
        print("note: boto3 is not installed; treating every file as changed")
        return {}
    except Exception as exc:  # noqa: BLE001 - any failure means "unknown"
        print(f"note: could not list s3://{bucket}/{prefix} ({exc});")
        print("      treating every file as changed")
        return {}


def upload_all(session, bucket: str, uploads: list[Upload]) -> list[str]:
    """Upload every object on a bounded pool. Returns the keys that failed.

    Every upload is attempted. One failure does not cancel the rest, so the
    caller reports all of them at once.

    A botocore client is safe to *call* from many threads, but the Session is
    not safe to create clients from concurrently. So each worker thread builds
    its own client once, and a lock covers only that creation.
    """
    thread_state = threading.local()
    new_client = threading.Lock()

    def client() -> object:
        existing = getattr(thread_state, "client", None)
        if existing is not None:
            return existing
        with new_client:
            thread_state.client = session.client("s3")
        return thread_state.client

    def put(upload: Upload) -> None:
        client().upload_file(
            str(upload.local),
            bucket,
            upload.key,
            ExtraArgs={"ContentType": upload.content_type},
        )

    failures: list[str] = []
    done = 0
    total = len(uploads)
    with ThreadPoolExecutor(max_workers=MAX_UPLOAD_WORKERS) as pool:
        futures = {pool.submit(put, u): u for u in uploads}
        for future in as_completed(futures):
            upload = futures[future]
            done += 1
            try:
                future.result()
            except Exception as exc:  # noqa: BLE001 - report, do not abort
                failures.append(upload.key)
                print(f"  FAILED  {upload.key}: {exc}", file=sys.stderr,
                      flush=True)
            if done % PROGRESS_EVERY == 0 or done == total:
                print(f"  {done}/{total} done", flush=True)
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sync the published directory to object storage, 1:1.",
        epilog="Dry run by default. Never deletes.",
    )
    parser.add_argument(
        "--confirm", action="store_true", help="actually upload"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-upload everything; skip the remote listing",
    )
    args = parser.parse_args()

    config = load_config()

    stale = unedited_sentinels(config)
    if stale:
        print("catalog.publish.yaml still carries template values:")
        for value in stale:
            print(f"  {value}")
        print("\nEdit write_prefix and public_base, then see SETUP.md.")
        return 1

    bucket, prefix = split_s3_uri(config["write_prefix"])
    uploads = collect_uploads(config)
    if not uploads:
        print(f"nothing under {config['publish_dir']}/ to publish",
              file=sys.stderr)
        return 1

    index = {} if args.force else remote_index(bucket, prefix, config)
    changed = [u for u in uploads if args.force or not is_unchanged(u, index)]

    print(f"publish_dir: {config['publish_dir']}/")
    print(f"target:      s3://{bucket}/{prefix}")
    print(f"aws profile: {config.get('profile') or '(default session)'}")
    print(f"{len(uploads)} file(s) published, {len(changed)} to upload")
    print("this never deletes; removing a file here does not unpublish it")

    if not args.confirm:
        for upload in changed[:20]:
            print(f"  would upload  {upload.key}")
        if len(changed) > 20:
            print(f"  ... and {len(changed) - 20} more")
        print("\ndry run. re-run with --confirm to upload.")
        return 0

    if not changed:
        print("nothing to upload")
        return 0

    # A dry run tolerates a broken session, but an upload cannot. Report the
    # reason here instead of raising a traceback out of the pool.
    try:
        session = aws_session(config)
    except ImportError:
        sys.exit("boto3 is required to upload. Run: pip install boto3")
    except Exception as exc:  # noqa: BLE001 - stop before any upload
        sys.exit(f"cannot build an AWS session: {exc}")

    failed = upload_all(session, bucket, changed)
    if failed:
        print(f"\n{len(failed)} of {len(changed)} file(s) failed:",
              file=sys.stderr)
        for key in sorted(failed):
            print(f"  {key}", file=sys.stderr)
        return 1
    print(f"\nuploaded {len(changed)} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
