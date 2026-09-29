#!/usr/bin/env python3
"""Build the coverage table: what exists where, per tile and per quarter.

    python3 tools/make_coverage.py --work w --out p --quarters 2024.Q2
    python3 tools/make_coverage.py --work w --out p --quarters 2024.Q2 \\
        --valid-fraction 500 --jobs 16
    python3 tools/make_coverage.py --work w --out p --rebuild

Writes `{out}/coverage/tiles.parquet`: one row per (mosaic tile,
quarter), sorted by tile and then quarter, so one tile's whole
36-quarter history is a contiguous run and the viewer's "what do I have
here, over time" question is a single range read.

Incremental by construction. A quarter is staged once into
`{work}/coverage/YYYY.Qn.parquet` from its transfer manifest, and
`tiles.parquet` is then rebuilt from every staged quarter present. So
`quarter.sbatch` stages its own quarter and refreshes the rollup, a
rerun stages nothing new, and `--rebuild` redoes the rollup without
touching the manifests.

Sizes come from the manifest, which records the byte size of every
object that landed, so the size columns cost one 2.8 MB download per
quarter. `valid_fraction` is the one column that needs the rasters, and
`--valid-fraction N` measures it for N sampled tiles (`all` for every
tile). It reads the *smallest* internal overview of the red band, 313 x
313 pixels, and counts the pixels that are not the -32768 nodata value:
a 0.1 megapixel read per tile instead of a 100 megapixel one, which is
what makes the measure affordable across a million tiles. Tiles that
were not sampled keep NULL, which the collection documents as "not read
yet" rather than "no data".
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mgrs_grid  # noqa: E402
import schema  # noqa: E402
from publish import load_config  # noqa: E402

MANIFEST_URL = ("https://data.source.coop/tge-labs/"
                "sentinel-2-quarterly-cloudless-mosaics/manifest/"
                "manifest_{year}_{quarter}.parquet")
QUARTER_RE = re.compile(r"^(?P<year>\d{4})\.(?P<quarter>Q[1-4])$")
NODATA = -32768
SORT_KEY = "subtile,quarter"
ROW_GROUP_SIZE = 20_000

COLUMNS = [
    ("subtile", "VARCHAR"), ("mgrs_tile", "VARCHAR"), ("year", "SMALLINT"),
    ("quarter_num", "TINYINT"), ("quarter", "VARCHAR"),
    ("bytes_total", "BIGINT"), ("bytes_b02", "BIGINT"), ("bytes_b03", "BIGINT"),
    ("bytes_b04", "BIGINT"), ("bytes_b08", "BIGINT"),
    ("valid_fraction", "FLOAT"), ("geometry", "GEOMETRY"),
]


def say(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def parse_quarter(value: str) -> tuple[int, str]:
    m = QUARTER_RE.match(value)
    if not m:
        sys.exit(f"{value!r}: expected YYYY.Qn, for example 2024.Q2")
    return int(m["year"]), m["quarter"]


def measure_valid_fraction(tiles: list[str], public_base: str, year: int,
                           quarter: str, jobs: int) -> dict[str, float]:
    """Fraction of real pixels per tile, from the smallest red overview."""
    os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
    try:
        import numpy as np
        import rasterio
    except ImportError:
        sys.exit("--valid-fraction needs rasterio and numpy")

    def one(tile: str) -> tuple[str, float | None]:
        url = (f"/vsicurl/{public_base}/{year}/{quarter}/{tile}/B04.tif")
        try:
            # OVERVIEW_LEVEL is 0-based over [2x, 4x, 8x, 16x, 32x], so 4
            # is the 32x overview: 313 x 313 for a 10,008 px tile.
            with rasterio.open(url, OVERVIEW_LEVEL=4) as ds:
                data = ds.read(1)
            return tile, float(np.count_nonzero(data != NODATA) / data.size)
        except Exception as exc:                      # noqa: BLE001
            print(f"  {tile}: {exc}", file=sys.stderr)
            return tile, None

    t0 = time.monotonic()
    out = {}
    with cf.ThreadPoolExecutor(jobs) as pool:
        for tile, value in pool.map(one, tiles):
            if value is not None:
                out[tile] = value
    say(f"valid_fraction: {len(out):,} of {len(tiles):,} tile(s) measured, "
        f"{time.monotonic() - t0:,.1f}s")
    return out


def stage_quarter(con, year: int, quarter: str, dest: Path, public_base: str,
                  manifest: str | None, sample: str | None, jobs: int) -> int:
    """One quarter's rows, from its manifest plus derived geometry."""
    source = manifest or MANIFEST_URL.format(year=year, quarter=quarter)
    rows = con.execute(f"""
        SELECT regexp_extract(item_id, '_(\\d+[A-Z]{{3}}_\\d+_\\d+)$', 1) AS subtile,
               band, size_bytes
        FROM read_parquet('{source}')
    """).fetchall()
    sizes: dict[str, dict[str, int]] = {}
    for subtile, band, size in rows:
        sizes.setdefault(subtile, {})[band] = size
    tiles = sorted(sizes)
    say(f"{year}.{quarter}: {len(tiles):,} tile(s) in the manifest")

    fractions: dict[str, float] = {}
    if sample:
        import random
        picked = tiles if sample == "all" else random.Random(0).sample(
            tiles, min(int(sample), len(tiles)))
        fractions = measure_valid_fraction(picked, public_base, year, quarter,
                                           jobs)

    quarter_num = int(quarter[1])
    values = []
    for tile in tiles:
        band = sizes[tile]
        geom, _ = mgrs_grid.geometry_of(tile)
        values.append((
            tile, mgrs_grid.cell_of(tile), year, quarter_num,
            f"{year}.{quarter}", sum(band.values()),
            band.get("B02"), band.get("B03"), band.get("B04"), band.get("B08"),
            fractions.get(tile), __import__("json").dumps(geom)))
    con.execute("DROP TABLE IF EXISTS staged")
    con.execute("""CREATE TABLE staged (
        subtile VARCHAR, mgrs_tile VARCHAR, year SMALLINT, quarter_num TINYINT,
        quarter VARCHAR, bytes_total BIGINT, bytes_b02 BIGINT, bytes_b03 BIGINT,
        bytes_b04 BIGINT, bytes_b08 BIGINT, valid_fraction FLOAT,
        geometry_json VARCHAR)""")
    con.executemany(
        "INSERT INTO staged VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", values)
    tmp = dest.with_name(f".{dest.name}.tmp")
    con.execute(f"""
        COPY (SELECT * EXCLUDE (geometry_json),
                     ST_GeomFromGeoJSON(geometry_json) AS geometry
              FROM staged)
        TO '{tmp}' (FORMAT PARQUET, COMPRESSION zstd, COMPRESSION_LEVEL 1)
    """)
    os.replace(tmp, dest)
    return len(values)


# A staged quarter is exactly `YYYY.Qn.parquet`. The staging directory
# also holds files with other schemas: make_footprints.py stages its
# `footprints.parquet` there. A bare glob reads those too and fails on
# the schema mismatch, so the roll-up names the shape it wants.
QUARTER_FILE = re.compile(r"^\d{4}\.Q[1-4]\.parquet$")


def roll_up(con, staged_dir: Path, final: Path) -> None:
    """Every staged quarter into one sorted, checked tiles.parquet."""
    parts = sorted(p for p in staged_dir.glob("*.parquet")
                   if QUARTER_FILE.match(p.name))
    if not parts:
        sys.exit(f"{staged_dir}: no staged quarter to roll up")
    union = ", ".join(f"'{p}'" for p in parts)
    merged = staged_dir / ".merged.parquet"
    con.execute(f"""
        COPY (SELECT * FROM read_parquet([{union}]))
        TO '{merged}' (FORMAT PARQUET, COMPRESSION zstd, COMPRESSION_LEVEL 1)
    """)
    rows = con.execute(
        f"SELECT count(*) FROM read_parquet('{merged}')").fetchone()[0]
    say(f"coverage: {len(parts)} quarter(s), {rows:,} row(s) staged")

    final.parent.mkdir(parents=True, exist_ok=True)
    tmp = final.with_name(f".{final.stem}.tmp.parquet")
    tmp.unlink(missing_ok=True)
    try:
        t0 = time.monotonic()
        r = subprocess.run(
            ["gpio", "sort", "column", str(merged), str(tmp), SORT_KEY,
             "--geoparquet-version", "2.0", "--compression", "zstd",
             "--compression-level", str(schema.ZSTD_LEVEL),
             "--row-group-size", str(ROW_GROUP_SIZE)],
            capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stdout[-2000:], r.stderr[-2000:], file=sys.stderr)
            sys.exit("gpio sort failed for the coverage table")
        chk = subprocess.run(["gpio", "check", "all", str(tmp)],
                             capture_output=True, text=True)
        if chk.returncode != 0:
            print(chk.stdout[-2000:], chk.stderr[-2000:], file=sys.stderr)
            sys.exit("gpio check failed for the coverage table")
        os.replace(tmp, final)
        say(f"coverage: {final} written, {final.stat().st_size / 1e6:,.1f} MB, "
            f"gpio check all passed, {time.monotonic() - t0:,.1f}s")
    finally:
        tmp.unlink(missing_ok=True)
        merged.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--work", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--quarters", help="comma-separated YYYY.Qn to stage")
    ap.add_argument("--manifest", help="manifest path or URL; one quarter only")
    ap.add_argument("--valid-fraction", metavar="N|all",
                    help="measure valid_fraction for N sampled tiles")
    ap.add_argument("--jobs", type=int, default=16)
    ap.add_argument("--rebuild", action="store_true",
                    help="roll up the staged quarters without staging any")
    ap.add_argument("--force", action="store_true",
                    help="restage a quarter that is already staged")
    a = ap.parse_args(argv)
    if not a.quarters and not a.rebuild:
        ap.error("pass --quarters or --rebuild")

    import duckdb
    config = load_config()
    public_base = config["public_base"].rstrip("/")
    staged_dir = Path(a.work) / "coverage"
    staged_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute("SET TimeZone='UTC';")

    for value in (a.quarters.split(",") if a.quarters else []):
        year, quarter = parse_quarter(value.strip())
        dest = staged_dir / f"{year}.{quarter}.parquet"
        if dest.is_file() and not a.force and not a.valid_fraction:
            say(f"{year}.{quarter}: already staged, skipping")
            continue
        t0 = time.monotonic()
        n = stage_quarter(con, year, quarter, dest, public_base, a.manifest,
                          a.valid_fraction, a.jobs)
        say(f"{year}.{quarter}: staged {n:,} row(s), "
            f"{time.monotonic() - t0:,.1f}s")

    roll_up(con, staged_dir, Path(a.out) / "coverage" / "tiles.parquet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
