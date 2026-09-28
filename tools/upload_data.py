#!/usr/bin/env python3
"""Upload staged data files to the same bucket prefix the catalog publishes to.

``tools/publish.py`` walks ``publish_dir`` and nothing else. That boundary is
the publish contract and this script does not widen it. GeoParquet partitions
and PMTiles archives are too large for git, so they live outside ``catalog/``
and never reach the bucket. This script carries them there.

Every rule the two scripts share comes from ``publish.py``. This file imports
the sentinel guard, the content types, the change detection, the AWS session,
and the upload pool. It adds one thing, a second walk root.

    python3 tools/upload_data.py            # dry run: what would change
    python3 tools/upload_data.py --confirm  # upload; needs AWS credentials
    python3 tools/upload_data.py --confirm --force   # re-upload everything

Set ``data_dir`` in ``catalog.publish.yaml`` to the staging directory that
holds the data. The template ships no staging tree, so this script exits with
a message until you set that key.

Two gates decide what uploads. The path gate admits only files under
``data_dir``. The extension gate admits only the suffixes in
PUBLISHABLE_SUFFIXES. Both apply. It never deletes, exactly as ``publish.py``
never deletes.

**Change detection is weaker here than it is for the catalog.**
``is_unchanged`` compares a compound ETag on size alone, because a multipart
ETag is not an MD5. A catalog file is small and uploads in one part, so it
compares by hash. A multi-gigabyte partition uploads in many parts, so it
compares by size. A truncated object of the correct size stays accepted on
every later run. Use ``--force`` to re-upload the data and clear that state.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from publish import (  # noqa: E402
    ROOT,
    Upload,
    aws_session,
    content_type_for,
    is_publishable,
    is_unchanged,
    load_config,
    remote_index,
    split_s3_uri,
    unedited_sentinels,
    upload_all,
)

# The suffixes that may reach the bucket. This is an allow-list, and it names
# what may pass rather than what may not. A staging tree grows new scratch
# files over time. An allow-list stays correct when it does, and a deny-list
# does not. One catalog staged 45 GB of GeoJSON that tippecanoe reads and
# nobody should download. An allow-list keeps that 45 GB out with no edit.
PUBLISHABLE_SUFFIXES = {
    ".parquet",
    ".pmtiles",
    ".tif",
    ".tiff",
    ".laz",
}


def data_root(config: dict[str, str], root: Path = ROOT) -> Path:
    """The staging directory this script walks.

    Reads the optional ``data_dir`` key. A relative value resolves against the
    repository root. Exits with a message when the key is absent or when the
    directory does not exist.
    """
    configured = config.get("data_dir", "")
    if not configured:
        sys.exit(
            "catalog.publish.yaml sets no data_dir, so there is nothing to "
            "upload.\nSet data_dir to the directory that holds your data "
            "files.\nSee the commented example in catalog.publish.yaml."
        )
    base = Path(configured)
    if not base.is_absolute():
        base = root / base
    base = base.resolve()
    if not base.is_dir():
        sys.exit(f"data_dir does not exist: {base}")
    return base


def is_data_publishable(rel: Path) -> bool:
    """True for a staged file that both gates admit.

    The path gate runs in ``collect_data_uploads``. This is the second gate.
    It applies the dotfile rule of ``publish.py`` and then the suffix
    allow-list.
    """
    return is_publishable(rel) and rel.suffix.lower() in PUBLISHABLE_SUFFIXES


def collect_data_uploads(
    config: dict[str, str], root: Path = ROOT
) -> list[Upload]:
    """Every staged data file that would be uploaded, in sorted order.

    The walk is rooted at ``data_dir`` and nothing else. Keys go under the
    same ``write_prefix`` the catalog publishes to, so the data sits beside
    the metadata that describes it.
    """
    _, prefix = split_s3_uri(config["write_prefix"])
    base = data_root(config, root)
    uploads = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(base)
        if not is_data_publishable(rel):
            continue
        key = f"{prefix}/{rel.as_posix()}" if prefix else rel.as_posix()
        uploads.append(Upload(path, key, content_type_for(path)))
    return uploads


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Upload staged data files to the catalog's bucket prefix.",
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
    base = data_root(config)

    stale = unedited_sentinels(config)
    if stale:
        print("catalog.publish.yaml still carries template values:")
        for value in stale:
            print(f"  {value}")
        print("\nEdit write_prefix and public_base, then see SETUP.md.")
        return 1

    bucket, prefix = split_s3_uri(config["write_prefix"])
    uploads = collect_data_uploads(config)
    if not uploads:
        print(f"nothing under {base}/ to upload", file=sys.stderr)
        return 1

    index = {} if args.force else remote_index(bucket, prefix, config)
    changed = [u for u in uploads if args.force or not is_unchanged(u, index)]

    print(f"data_dir:    {base}/")
    print(f"target:      s3://{bucket}/{prefix}")
    print(f"aws profile: {config.get('profile') or '(default session)'}")
    print(f"suffixes:    {', '.join(sorted(PUBLISHABLE_SUFFIXES))}")
    print(f"{len(uploads)} file(s) staged, {len(changed)} to upload")
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
