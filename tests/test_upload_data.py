#!/usr/bin/env python3
"""The data upload contract: two gates, and no reimplementation.

tools/upload_data.py walks a staging directory that sits outside the published
catalog. It admits a file only when both gates pass. The path gate admits only
files under data_dir. The extension gate admits only the suffixes in
PUBLISHABLE_SUFFIXES.

This gate also checks that the script refuses an unedited config and that it
exits with a message when data_dir is absent.

No network, no AWS, no credentials.

Run: python3 tests/test_upload_data.py
"""
import io
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import upload_data  # noqa: E402
from upload_data import (  # noqa: E402
    PUBLISHABLE_SUFFIXES,
    collect_data_uploads,
    data_root,
    is_data_publishable,
    unedited_sentinels,
)

errors: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


def write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def exit_message(call) -> str:
    """Run a call that must exit, and return the message it exits with."""
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            call()
    except SystemExit as exc:
        return str(exc.code)
    return ""


# --- the extension allow-list ------------------------------------------
check(".parquet" in PUBLISHABLE_SUFFIXES, "parquet is publishable")
check(".pmtiles" in PUBLISHABLE_SUFFIXES, "pmtiles is publishable")
check(".geojson" not in PUBLISHABLE_SUFFIXES, "geojson scratch never uploads")
check(is_data_publishable(Path("a/roads.parquet")), "parquet passes")
check(is_data_publishable(Path("a/roads.PARQUET")), "suffix case is ignored")
check(is_data_publishable(Path("a/tiles.pmtiles")), "pmtiles passes")
check(not is_data_publishable(Path("a/scratch.geojson")), "geojson is barred")
check(not is_data_publishable(Path("a/notes.md")), "markdown is barred")
check(not is_data_publishable(Path("a/roads.parquet.tmp")), "tmp is barred")
check(not is_data_publishable(Path("a/.hidden/x.parquet")), "dotdir is barred")

# --- the path gate and the walk ----------------------------------------
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)

    # Staged data. Both gates pass for the first two.
    write(root / "staging/roads/part-0.parquet")
    write(root / "staging/roads/part-1.parquet")
    write(root / "staging/tiles/roads.pmtiles")
    write(root / "staging/dem/tile.tif")

    # Staged scratch. The extension gate bars it.
    write(root / "staging/scratch/roads.geojson")
    write(root / "staging/scratch/notes.md")
    write(root / "staging/.work/tmp.parquet")

    # Outside the staging tree. The path gate bars it.
    write(root / "catalog/catalog.json")
    write(root / "elsewhere/stray.parquet")
    write(root / "stray-at-root.pmtiles")

    config = {
        "write_prefix": "s3://a-bucket/a/prefix",
        "public_base": "https://data.example.org/a/prefix",
        "publish_dir": "catalog",
        "data_dir": "staging",
    }
    uploads = collect_data_uploads(config, root)
    keys = {u.key for u in uploads}

    expected = {
        "a/prefix/roads/part-0.parquet",
        "a/prefix/roads/part-1.parquet",
        "a/prefix/tiles/roads.pmtiles",
        "a/prefix/dem/tile.tif",
    }
    check(keys == expected, f"upload set wrong.\n  extra:   {keys - expected}"
                            f"\n  missing: {expected - keys}")

    # Content types come from publish.py, not from a second table.
    types = {u.key: u.content_type for u in uploads}
    check(
        types["a/prefix/roads/part-0.parquet"]
        == "application/vnd.apache.parquet",
        "parquet content type comes from publish.py",
    )
    check(
        types["a/prefix/tiles/roads.pmtiles"] == "application/vnd.pmtiles",
        "pmtiles content type comes from publish.py",
    )

    # The bare-prefix case: no prefix at all.
    flat = dict(config, write_prefix="s3://a-bucket")
    check(
        {u.key for u in collect_data_uploads(flat, root)}
        == {k.removeprefix("a/prefix/") for k in expected},
        "keys are wrong when write_prefix names no prefix",
    )

    # An absolute data_dir works too.
    absolute = dict(config, data_dir=str(root / "staging"))
    check(
        {u.key for u in collect_data_uploads(absolute, root)} == expected,
        "an absolute data_dir walks the same tree",
    )

    # --- an absent or wrong data_dir exits with a message ---------------
    message = exit_message(lambda: data_root(dict(config, data_dir=""), root))
    check("data_dir" in message, f"an empty data_dir names the key: {message}")
    check("\n" in message, "the empty data_dir message says what to do")

    no_key = {k: v for k, v in config.items() if k != "data_dir"}
    check(
        "data_dir" in exit_message(lambda: data_root(no_key, root)),
        "an absent data_dir names the key",
    )

    missing = dict(config, data_dir="no-such-directory")
    check(
        "does not exist" in exit_message(lambda: data_root(missing, root)),
        "a data_dir that does not exist says so",
    )

# --- the unedited template exits cleanly -------------------------------
# The shipped catalog.publish.yaml sets no data_dir, so main() must report
# that instead of raising a traceback.
argv = sys.argv
sys.argv = ["upload_data.py"]
try:
    message = exit_message(upload_data.main)
finally:
    sys.argv = argv
check("data_dir" in message, f"the template exits on data_dir: {message!r}")

# --- the sentinel guard ------------------------------------------------
check(
    unedited_sentinels({
        "write_prefix": "s3://EXAMPLE-BUCKET/EXAMPLE-PREFIX",
        "public_base": "https://example.invalid/EXAMPLE-PREFIX",
    }) != [],
    "an unedited config is refused",
)

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    write(root / "staging/roads/part-0.parquet")
    sentinel_config = {
        "write_prefix": "s3://EXAMPLE-BUCKET/EXAMPLE-PREFIX",
        "public_base": "https://example.invalid/EXAMPLE-PREFIX",
        "publish_dir": "catalog",
        "data_dir": str(root / "staging"),
    }
    out = io.StringIO()
    argv = sys.argv
    sys.argv = ["upload_data.py", "--confirm"]
    real_load = upload_data.load_config
    upload_data.load_config = lambda *a, **k: sentinel_config
    try:
        with redirect_stdout(out):
            code = upload_data.main()
    finally:
        upload_data.load_config = real_load
        sys.argv = argv
    check(code == 1, f"the sentinel guard refuses to upload, got {code}")
    check(
        "EXAMPLE-BUCKET" in out.getvalue(),
        "the guard names the sentinel it found",
    )

if errors:
    print("\n".join(f"error  {e}" for e in errors))
    raise SystemExit(1)
print("OK: data upload contract holds")
