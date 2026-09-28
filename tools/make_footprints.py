#!/usr/bin/env python3
"""Build the tile-footprint tileset for the coverage collection.

    python3 tools/make_footprints.py --work w --out p
    python3 tools/make_footprints.py --work w --out p --max-zoom 8

Rolls `coverage/tiles.parquet` up to one polygon per mosaic tile --
`subtile`, `mgrs_tile`, how many quarters carry it, and the first and
last quarter that do -- and turns that into
`{out}/coverage/footprints.pmtiles`.

The geometry is the true mosaic cell, not a bounding envelope: the same
`mgrs_grid` footprint the items and the coverage rows carry, so a click
on the map lands on exactly the polygon the item index describes,
antimeridian split included. A client joins `tiles.parquet` onto the
tileset by `subtile`, which is why the tileset itself carries only the
fields a style needs -- recomputing the statistics does not mean
rebuilding the tiles.

`gpio pmtiles create` streams the GeoParquet through tippecanoe, so
tippecanoe has to be on PATH. Without it this writes
`footprints.parquet` and stops with a message rather than failing
silently: the parquet is the input the tileset is made from, and a
machine that cannot make tiles can still hand that file to one that can.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import schema  # noqa: E402

LAYER = "footprints"
SORT_KEY = "subtile"
ROW_GROUP_SIZE = 20_000


def say(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def roll_up(con, tiles: Path, staged: Path) -> int:
    """One row per mosaic tile, with its quarter counts."""
    con.execute(f"""
        COPY (
          SELECT subtile,
                 any_value(mgrs_tile)      AS mgrs_tile,
                 count(*)::INTEGER         AS quarters_present,
                 min(quarter)              AS first_quarter,
                 max(quarter)              AS last_quarter,
                 any_value(geometry)       AS geometry
          FROM read_parquet('{tiles}')
          GROUP BY subtile
          ORDER BY subtile
        ) TO '{staged}' (FORMAT PARQUET, COMPRESSION zstd, COMPRESSION_LEVEL 1)
    """)
    return con.execute(
        f"SELECT count(*) FROM read_parquet('{staged}')").fetchone()[0]


def write_parquet(staged: Path, final: Path) -> None:
    """The gpio sort/check/rename the rest of the catalog uses."""
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp = final.with_name(f".{final.stem}.tmp.parquet")
    tmp.unlink(missing_ok=True)
    try:
        r = subprocess.run(
            ["gpio", "sort", "column", str(staged), str(tmp), SORT_KEY,
             "--geoparquet-version", "2.0", "--compression", "zstd",
             "--compression-level", str(schema.ZSTD_LEVEL),
             "--row-group-size", str(ROW_GROUP_SIZE)],
            capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stdout[-2000:], r.stderr[-2000:], file=sys.stderr)
            sys.exit("gpio sort failed for the footprints")
        chk = subprocess.run(["gpio", "check", "all", str(tmp)],
                             capture_output=True, text=True)
        if chk.returncode != 0:
            print(chk.stdout[-2000:], chk.stderr[-2000:], file=sys.stderr)
            sys.exit("gpio check failed for the footprints")
        os.replace(tmp, final)
    finally:
        tmp.unlink(missing_ok=True)


def make_pmtiles(source: Path, dest: Path, max_zoom: int) -> bool:
    if shutil.which("tippecanoe") is None:
        say("tippecanoe is not on PATH, so no tileset was built. "
            f"{source} is the input; build the tileset where tippecanoe "
            "is available (tools/rails/environment.yml pins it).")
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    # The temporary name keeps the .pmtiles extension. tippecanoe picks
    # its output format by sniffing the extension, so writing to
    # `.footprints.pmtiles.tmp` silently produces an MBTiles SQLite file
    # under a .pmtiles name -- measured, not hypothetical. A leading dot
    # is enough to keep a killed run's leftovers out of every glob.
    tmp = dest.with_name(f".{dest.stem}.tmp.pmtiles")
    tmp.unlink(missing_ok=True)
    t0 = time.monotonic()
    r = subprocess.run(
        ["gpio", "pmtiles", "create", str(source), str(tmp),
         "-l", LAYER, "--min-zoom", "0", "--max-zoom", str(max_zoom),
         # A mosaic cell is 100 km across, so four decimals (about 11 m)
         # is already far finer than the geometry. Six would spend a
         # third of the tileset on digits no client can use.
         "--precision", "4", "--force"],
        capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-3000:], r.stderr[-3000:], file=sys.stderr)
        sys.exit("gpio pmtiles create failed")
    with tmp.open("rb") as fh:
        magic = fh.read(7)
    if magic != b"PMTiles":
        sys.exit(f"{tmp}: tippecanoe did not write PMTiles (check the "
                 "output extension); refusing to publish it")
    os.replace(tmp, dest)
    say(f"{dest}: {dest.stat().st_size / 1e6:,.1f} MB, "
        f"{time.monotonic() - t0:,.1f}s")
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--work", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-zoom", type=int, default=7,
                    help="tippecanoe simplifies below the max zoom and not "
                         "at it, so the cells keep full detail here and "
                         "clients overzoom past it. 7 is 10 MB against 19 "
                         "at 8, for the same geometry (default 7)")
    a = ap.parse_args(argv)

    import duckdb
    tiles = Path(a.out) / "coverage" / "tiles.parquet"
    if not tiles.is_file():
        sys.exit(f"{tiles}: not there; run make_coverage.py first")
    work = Path(a.work) / "coverage"
    work.mkdir(parents=True, exist_ok=True)
    staged = work / ".footprints-staged.parquet"
    final = work / "footprints.parquet"

    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    t0 = time.monotonic()
    rows = roll_up(con, tiles, staged)
    say(f"footprints: {rows:,} mosaic tile(s), {time.monotonic() - t0:,.1f}s")
    write_parquet(staged, final)
    staged.unlink(missing_ok=True)
    say(f"footprints: {final}, {final.stat().st_size / 1e6:,.1f} MB, "
        "gpio check all passed")
    make_pmtiles(final, Path(a.out) / "coverage" / "footprints.pmtiles",
                 a.max_zoom)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
