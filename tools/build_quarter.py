#!/usr/bin/env python3
"""Turn one quarter's staged items into its published stac-geoparquet.

    python3 tools/build_quarter.py --year 2024 --quarter Q2 \\
        --work /u/cholmes/s2-mosaics/work --out /u/cholmes/s2-mosaics/publish

Reads `{work}/quarter=YYYY.Qn/items.ndjson` (written by `make_items.py`)
and writes `{out}/mosaics/quarter=YYYY.Qn/items.parquet`.

Two steps, and the split matters. DuckDB stages the rows: it does the
casts to `schema.COLUMNS` and turns the GeoJSON text into a GEOMETRY.
Then `gpio sort column` does the ordered write. **Do not** let DuckDB
write the published file. DuckDB's Parquet writer emits GeoParquet
*1.0.0* -- a `geo` key over a plain BYTE_ARRAY -- and never the native
Parquet GEOMETRY logical type, at any row count and with any setting.
`rashid check --data` rejects that at error severity twice, PTL-DAT-012
(version must be 1.1 or 2.x) and PTL-DAT-007 (no per-row-group spatial
statistics). gpio writes geo 2.0.0 plus the native GEOMETRY type with its
statistics, and rashid raises neither. Nor is the reverse safe: sorting
in DuckDB and then `gpio convert` loses the row order, so the sort has to
be gpio's.

`geoparquet-io` is pinned to **1.5.0** in `tools/rails/environment.yml`.
Both `--compression-level` and `--row-group-size` reach the write engine
only from 1.4.0 onward; on 1.3.0 they were accepted and dropped. An
unpinned install is how a flag that "did nothing" quietly starts doing
something very expensive.

The file lands atomically. gpio writes `.items.tmp.parquet` -- a dotfile,
so every `*.parquet` glob and the uploader's dotfile rule skip it if a
killed run leaves it behind, but still `.parquet`, because `gpio check`
sniffs the extension. `gpio check all` gates it there, and only a file
that passed is `os.replace()`d onto `items.parquet`. So nothing under
`quarter=*/items.parquet` is ever half-written or unchecked, which is
what the uploader and `quarter.sbatch`'s resume rely on.

A quarter whose `items.parquet` already exists is not rebuilt; pass
`--force` to replace it.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import schema  # noqa: E402

# DuckDB's ceiling while it stages, and the budget handed to gpio. The
# two never hold memory at the same time: the limit drops for the
# duration of the gpio run and is restored afterwards.
DEFAULT_MEMORY = "8GB"
GPIO_HANDOFF = "1GB"
# The NDJSON lines carry a whole assets object and a densified footprint;
# DuckDB's default object ceiling is well under that.
MAX_OBJECT = 20_000_000


def say(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def stage(con, ndjson: Path, staged: Path) -> int:
    """NDJSON -> a staged parquet in the canonical schema. Returns rows.

    Compression here is zstd-1: this file is read once, by gpio, and
    deleted. Spending level 18 on it would double the build for nothing.
    """
    casts = ", ".join(f'CAST("{n}" AS {t}) AS "{n}"'
                      for n, t, _ in schema.DATA_COLUMNS)
    con.execute(f"""
        COPY (
          SELECT {casts},
                 ST_GeomFromGeoJSON(_geometry_json) AS geometry
          FROM read_ndjson('{ndjson}', maximum_object_size={MAX_OBJECT})
        ) TO '{staged}' (FORMAT PARQUET, COMPRESSION zstd, COMPRESSION_LEVEL 1)
    """)
    return con.execute(
        f"SELECT count(*) FROM read_parquet('{staged}')").fetchone()[0]


def sort_and_check(con, staged: Path, final: Path, memory: str) -> None:
    """The ordered GeoParquet 2.0 write, its gate, then its name."""
    tmp = final.with_name(f".{final.stem}.tmp.parquet")
    tmp.unlink(missing_ok=True)
    try:
        con.execute(f"SET memory_limit='{GPIO_HANDOFF}';")
        t0 = time.monotonic()
        cmd = ["gpio", "sort", "column", str(staged), str(tmp),
               schema.SORT_KEY,
               "--geoparquet-version", "2.0",
               "--compression", "zstd",
               "--compression-level", str(schema.ZSTD_LEVEL),
               "--row-group-size", str(schema.ROW_GROUP_SIZE),
               "--write-memory", memory]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stdout[-2000:], r.stderr[-2000:], file=sys.stderr)
            sys.exit(f"gpio sort failed for {final.parent.name}")
        say(f"{final.parent.name}: sorted ({schema.SORT_KEY}), zstd-"
            f"{schema.ZSTD_LEVEL}, {tmp.stat().st_size / 1e6:,.1f} MB, "
            f"{time.monotonic() - t0:,.1f}s")

        t0 = time.monotonic()
        chk = subprocess.run(["gpio", "check", "all", str(tmp)],
                             capture_output=True, text=True)
        if chk.returncode != 0:
            print(chk.stdout[-2000:], chk.stderr[-2000:], file=sys.stderr)
            sys.exit(f"gpio check failed for {final.parent.name}")
        say(f"{final.parent.name}: gpio check all passed, "
            f"{time.monotonic() - t0:,.1f}s")
        os.replace(tmp, final)
    finally:
        con.execute(f"SET memory_limit='{memory}';")
        tmp.unlink(missing_ok=True)


def summarise(con, final: Path) -> None:
    """What landed: rows, row groups, tiles, and the geo metadata version."""
    import json
    rows, tiles = con.execute(f"""
        SELECT count(*), count(DISTINCT _tile) FROM read_parquet('{final}')
    """).fetchone()
    groups = con.execute(
        f"SELECT num_row_groups FROM parquet_file_metadata('{final}')"
    ).fetchone()[0]
    geo = con.execute(f"""
        SELECT value FROM parquet_kv_metadata('{final}') WHERE key = 'geo'
    """).fetchall()
    version = "none"
    if geo:
        raw = geo[0][0]
        version = json.loads(raw.decode() if isinstance(raw, bytes) else raw
                             ).get("version", "?")
    say(f"{final.parent.name}: {rows:,} row(s), {tiles:,} MGRS cell(s), "
        f"{groups} row group(s), {final.stat().st_size / 1e6:,.1f} MB, "
        f"geo metadata version {version}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--quarter", required=True, choices=sorted(schema.QUARTER_MONTHS))
    ap.add_argument("--work", required=True, help="where make_items.py wrote")
    ap.add_argument("--out", required=True, help="the publish tree")
    ap.add_argument("--memory", default=DEFAULT_MEMORY,
                    help=f"DuckDB limit and gpio write budget (default "
                         f"{DEFAULT_MEMORY})")
    ap.add_argument("--force", action="store_true",
                    help="rebuild a quarter whose items.parquet exists")
    a = ap.parse_args(argv)

    import duckdb
    part = f"quarter={a.year}.{a.quarter}"
    ndjson = Path(a.work) / part / "items.ndjson"
    if not ndjson.is_file():
        sys.exit(f"{ndjson}: not there; run make_items.py first")
    final = Path(a.out) / schema.COLLECTION / part / "items.parquet"
    if final.is_file() and not a.force:
        say(f"{part}: {final} exists, nothing to do")
        return 0
    final.parent.mkdir(parents=True, exist_ok=True)

    staged = Path(a.work) / part / ".staged.parquet"
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    # Every timestamp in the staging file ends in Z, and DuckDB honours
    # that when it parses. Pinning the session zone anyway keeps what a
    # reader sees in the file identical to what the build logged, on a
    # laptop in CEST as much as on a node with TZ=UTC.
    con.execute("SET TimeZone='UTC';")
    con.execute(f"SET memory_limit='{a.memory}';")
    con.execute(f"SET temp_directory='{Path(a.work) / '.duckdb-tmp'}';")
    try:
        t0 = time.monotonic()
        rows = stage(con, ndjson, staged)
        say(f"{part}: staged {rows:,} row(s), "
            f"{staged.stat().st_size / 1e6:,.1f} MB, "
            f"{time.monotonic() - t0:,.1f}s")
        sort_and_check(con, staged, final, a.memory)
        summarise(con, final)
    finally:
        staged.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
