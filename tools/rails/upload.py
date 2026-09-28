#!/usr/bin/env python3
"""Upload built data files from rails with a named AWS profile, skipping
what the bucket already holds.

    AWS_PROFILE=source-coop python3 tools/rails/upload.py \\
        --data-dir /u/cholmes/s2-mosaics/publish \\
        mosaics/quarter=2024.Q2/items.parquet \\
        mosaics/quarter=2024.Q2/overview.tif

Why not `tools/upload_data.py`: that one builds its session from the
`profile` key of `catalog.publish.yaml`, a laptop profile, unless
AWS_ACCESS_KEY_ID is in the environment, and rails has neither -- its
`[default]` profile is another account that gets AccessDenied on the
`tge-labs` prefix. This script takes the profile from `--profile` or
AWS_PROFILE (default `source-coop`) and reuses everything else from
`publish.py` and `upload_data.py`: the write prefix, the content types,
the suffix allow-list, and the rule that a file must live under
`--data-dir` with its path below that becoming its key under the catalog
prefix.

Skip-existing is one HEAD per file, and an object of the same size is
left alone. A multipart ETag is not an MD5, so size is the comparison
`upload_data.py` makes too. `--force` uploads regardless, which is what
a rebuilt quarter needs. After each upload a second HEAD confirms the
size, because an upload that did not land is not a file that is
published. `--key-prefix` inserts a directory between the catalog prefix
and the file's path, which is what SMOKE=1 passes to keep a rehearsal
out of the real catalog.

It never deletes.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from publish import Upload, content_type_for, load_config, split_s3_uri  # noqa: E402
from upload_data import is_data_publishable  # noqa: E402

DEFAULT_PROFILE = "source-coop"
# `upload_data.py`'s allow-list covers the bulk formats (.parquet,
# .pmtiles, .tif, .laz). The per-quarter browse images are neither those
# nor catalog metadata: they are built into $PUBLISH beside the parquet
# they belong to, so `publish.py`, which only ever walks `catalog/`, never
# sees them. Rather than widen the template's list for every catalog, this
# lane admits its own two extra suffixes.
EXTRA_SUFFIXES = {".webp", ".jpg"}
# Multipart settings for the multi-GB overview COGs over the cluster's
# uplink. A quarter's items.parquet is a few MB and never reaches the
# threshold.
PART_MB = 64
CONCURRENCY = 16


def plan_uploads(data_dir: Path, files: list[str], prefix: str,
                 key_prefix: str = "") -> list[Upload]:
    """One Upload per file. A file outside --data-dir, missing, or with a
    suffix the allow-list refuses stops the run before anything moves."""
    base = data_dir.resolve()
    uploads = []
    for name in files:
        path = Path(name)
        path = (path if path.is_absolute() else base / path).resolve()
        try:
            rel = path.relative_to(base)
        except ValueError:
            sys.exit(f"{name}: not under --data-dir {base}")
        if not path.is_file():
            sys.exit(f"{name}: no such file under {base}")
        if not (is_data_publishable(rel)
                or rel.suffix.lower() in EXTRA_SUFFIXES):
            sys.exit(f"{name}: not a publishable data file")
        parts = [p for p in (prefix, key_prefix.strip("/")) if p]
        uploads.append(Upload(path, "/".join(parts + [rel.as_posix()]),
                              content_type_for(path)))
    return uploads


def remote_size(client, bucket: str, key: str) -> int | None:
    """The object's size, or None when it is not there."""
    try:
        return int(client.head_object(Bucket=bucket, Key=key)["ContentLength"])
    except client.exceptions.ClientError as exc:
        if exc.response.get("Error", {}).get("Code", "") in (
                "404", "NoSuchKey", "NotFound"):
            return None
        raise


def upload_one(client, bucket: str, upload: Upload, force: bool,
               dry_run: bool = False) -> str:
    local = upload.local.stat().st_size
    have = remote_size(client, bucket, upload.key)
    if have == local and not force:
        print(f"  skip      s3://{bucket}/{upload.key} ({local:,} bytes, same size)")
        return "skipped"
    state = "new" if have is None else f"replaces {have:,} bytes"
    if dry_run:
        print(f"  would put s3://{bucket}/{upload.key} ({local:,} bytes, {state})")
        return "would upload"
    from boto3.s3.transfer import TransferConfig
    print(f"  put       s3://{bucket}/{upload.key} ({local:,} bytes, {state})",
          flush=True)
    client.upload_file(
        str(upload.local), bucket, upload.key,
        ExtraArgs={"ContentType": upload.content_type},
        Config=TransferConfig(multipart_chunksize=PART_MB << 20,
                              multipart_threshold=PART_MB << 20,
                              max_concurrency=CONCURRENCY))
    after = remote_size(client, bucket, upload.key)
    if after != local:
        sys.exit(f"  {upload.key}: uploaded {local:,} bytes but the bucket "
                 f"reports {after}; not counting it as published")
    print(f"  done      {upload.key}", flush=True)
    return "uploaded"


def make_client(profile: str, region: str | None, endpoint: str | None):
    """The S3 client every upload here goes through.

    Source Cooperative is a data proxy since its 0.3 CLI: the credentials
    are proxy STS tokens valid only against `endpoint_url`, the bucket is
    the account (`tge-labs`) and the product is the key prefix. botocore
    picks `endpoint_url` up from the profile by itself, but it is passed
    explicitly so that where the bytes land does not depend on a line in
    somebody's `~/.aws/config`.
    """
    import boto3
    return boto3.Session(profile_name=profile, region_name=region or None
                         ).client("s3", endpoint_url=endpoint or None)


def main(argv: list[str] | None = None, client=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data-dir", required=True,
                    help="the staging tree; each file's key is its path below it")
    ap.add_argument("--profile",
                    default=os.environ.get("AWS_PROFILE") or DEFAULT_PROFILE,
                    help=f"AWS profile (default: $AWS_PROFILE or {DEFAULT_PROFILE})")
    ap.add_argument("--key-prefix", default="",
                    help="directory between the catalog prefix and the file's path")
    ap.add_argument("--force", action="store_true",
                    help="upload even when the bucket holds an object of the same size")
    ap.add_argument("--endpoint",
                    help="S3 endpoint; defaults to endpoint_url in "
                         "catalog.publish.yaml")
    ap.add_argument("--dry-run", action="store_true",
                    help="HEAD only; print what would upload")
    ap.add_argument("files", nargs="+", help="files under --data-dir")
    a = ap.parse_args(argv)

    config = load_config()
    bucket, prefix = split_s3_uri(config["write_prefix"])
    uploads = plan_uploads(Path(a.data_dir), a.files, prefix, a.key_prefix)
    suffix = a.key_prefix.strip("/")
    endpoint = a.endpoint or config.get("endpoint_url")
    print(f"profile: {a.profile}; endpoint: {endpoint or '(aws default)'}; "
          f"target: s3://{bucket}/{prefix}{'/' + suffix if suffix else ''}")
    if client is None:
        client = make_client(a.profile, config.get("region"),
                             a.endpoint or config.get("endpoint_url"))
    outcomes = [upload_one(client, bucket, u, a.force, a.dry_run) for u in uploads]
    print(f"{len(outcomes)} file(s): {outcomes.count('uploaded')} uploaded, "
          f"{outcomes.count('skipped')} skipped"
          + (f", {outcomes.count('would upload')} would upload"
             if a.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
