#!/usr/bin/env python3
"""Build one quarter's STAC items from its transfer manifest.

    python3 tools/make_items.py --year 2024 --quarter Q2 --out /u/cholmes/s2-mosaics/work
    python3 tools/make_items.py --year 2024 --quarter Q2 --out work \\
        --items-dir work/items --limit 300 --verify 20

Input is `manifest/manifest_{year}_{quarter}.parquet` in the published
product -- one row per object transferred, with `item_id`, `band` and
`size_bytes`. Four rows make one item. Nothing else is read: the
geometry comes from `mgrs_grid`, the band metadata lives on the
collection, and the hrefs are constructed, so a quarter's 28,272 items
are built from a 2.8 MB download rather than 28,272 HTTP requests.

Two outputs, and the first is not optional:

* `{out}/quarter={year}.{quarter}/items.ndjson` -- one `schema.normalize()`
  row per line, the staging table `build_quarter.py` turns into
  `items.parquet`. Written through a temporary name and `os.replace()`,
  so a killed run never leaves a half-written file that the next step
  would read as complete.
* `--items-dir DIR` -- the item JSON itself, at
  `DIR/{year}/{Qn}/{tile}/{item_id}.json`, which is the layout it
  publishes in: each item sits beside the four COGs it describes.

Both are idempotent: the same manifest gives the same bytes, so a rerun
after a failure is safe and a resumed backfill does not need to know how
far it got.

`--limit N` and `--tiles A,B,C` cut the work down for a smoke test.
`--verify N` is the correctness gate: it opens the red band of N sampled
tiles over HTTP and asserts that the `proj:code`, `proj:shape` and
`proj:transform` this tool computed match what the file actually says.
Only the header is read (about 16 KB with `GDAL_DISABLE_READDIR_ON_OPEN`),
so verifying a hundred tiles costs a couple of megabytes. It needs
rasterio; without it the flag fails loudly rather than passing quietly.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
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
CDSE_ITEM = ("https://stac.dataspace.copernicus.eu/v1/collections/"
             "sentinel-2-global-mosaics/items/{id}")
BAND_TITLES = {"B02": "Blue (band 2), 10 m", "B03": "Green (band 3), 10 m",
               "B04": "Red (band 4), 10 m",
               "B08": "Near-infrared (band 8), 10 m"}


def say(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def read_manifest(source: str, limit: int | None,
                  tiles: list[str] | None) -> list[tuple[str, dict[str, int]]]:
    """(item_id, {band: size_bytes}) for each item, ordered by id.

    One DuckDB query does the grouping. An item that does not carry all
    four bands stops the run: a three-band item would publish an asset
    list that does not match the collection's `item_assets`, and a
    missing object is a transfer to repair, not a row to write.
    """
    import duckdb
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    where = ""
    if tiles:
        wanted = ", ".join(f"'{t}'" for t in tiles)
        where = (f"WHERE regexp_extract(item_id, "
                 f"'_(\\d+[A-Z]{{3}}_\\d+_\\d+)$', 1) IN ({wanted})")
    rows = con.execute(f"""
        SELECT item_id, band, size_bytes
        FROM read_parquet('{source}') {where}
        ORDER BY item_id, band
    """).fetchall()
    items: dict[str, dict[str, int]] = {}
    for item_id, band, size in rows:
        items.setdefault(item_id, {})[band] = size
    out = []
    for item_id, bands in items.items():
        missing = set(schema.BANDS) - set(bands)
        if missing:
            sys.exit(f"{item_id}: manifest has no {', '.join(sorted(missing))} "
                     "row; the transfer is incomplete, so no item is written")
        out.append((item_id, bands))
    out.sort()
    return out[:limit] if limit else out


def build_item(item_id: str, sizes: dict[str, int], public_base: str,
               root_href: str) -> dict:
    """One STAC 1.1.0 item. Every field is derived; nothing is fetched."""
    year, quarter, tile = schema.split_id(item_id)
    epsg, _, _ = mgrs_grid.origin(tile)
    geometry, bbox = mgrs_grid.geometry_of(tile)
    transform = mgrs_grid.transform_of(tile)
    start, end = schema.quarter_window(year, quarter)
    base = f"{public_base}/{year}/{quarter}/{tile}"
    cell = mgrs_grid.cell_of(tile)

    assets = {}
    for band in schema.BANDS:
        assets[band] = {
            "href": f"{base}/{band}.tif",
            "type": schema.COG_TYPE,
            "title": BAND_TITLES[band],
            "roles": ["data", "reflectance"],
            "file:size": sizes[band],
            "proj:shape": [mgrs_grid.TILE_PX, mgrs_grid.TILE_PX],
            "proj:transform": transform,
        }
    return {
        "type": "Feature",
        "stac_version": schema.STAC_VERSION,
        "stac_extensions": list(schema.STAC_EXTENSIONS),
        "id": item_id,
        "collection": schema.COLLECTION,
        "geometry": geometry,
        "bbox": bbox,
        "properties": {
            "datetime": start,
            "start_datetime": start,
            "end_datetime": end,
            "grid:code": f"MGRS-{cell}",
            "proj:code": f"EPSG:{epsg}",
            "proj:shape": [mgrs_grid.TILE_PX, mgrs_grid.TILE_PX],
            "proj:bbox": mgrs_grid.proj_bbox(tile),
            "gsd": mgrs_grid.GSD,
            "constellation": "sentinel-2",
            "instruments": ["msi"],
            "product:type": "S2MSI_L3__MCQ",
            "processing:level": "L3",
        },
        "assets": assets,
        "links": [
            {"rel": "root", "href": root_href, "type": "application/json",
             "title": "Sentinel-2 Quarterly Cloudless Mosaics"},
            {"rel": "collection", "href": f"{public_base}/mosaics/collection.json",
             "type": "application/json",
             "title": "Quarterly cloudless mosaic tiles"},
            {"rel": "parent", "href": f"{public_base}/mosaics/collection.json",
             "type": "application/json",
             "title": "Quarterly cloudless mosaic tiles"},
            {"rel": "self", "href": f"{base}/{item_id}.json",
             "type": "application/geo+json", "title": item_id},
            {"rel": "derived_from", "href": CDSE_ITEM.format(id=item_id),
             "type": "application/geo+json",
             "title": "The CDSE item this tile mirrors"},
        ],
    }


def write_json(path: Path, payload: dict) -> None:
    """Write one JSON file through a temporary name and os.replace()."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(payload, indent=1, sort_keys=False) + "\n")
    os.replace(tmp, path)


def verify(tiles: list[str], public_base: str, year: int, quarter: str,
           workers: int = 12) -> int:
    """Open sampled COGs and check the computed georeferencing. Returns
    the number of mismatches; the caller decides what to do about it."""
    os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
    try:
        import rasterio
    except ImportError:
        sys.exit("--verify needs rasterio; install it or drop the flag")

    def one(tile: str):
        url = f"/vsicurl/{public_base}/{year}/{quarter}/{tile}/B04.tif"
        try:
            with rasterio.open(url) as ds:
                return tile, None, (ds.crs.to_epsg(), list(ds.transform)[:6],
                                    [ds.height, ds.width])
        except Exception as exc:                      # noqa: BLE001
            return tile, str(exc), None

    bad = 0
    t0 = time.monotonic()
    with cf.ThreadPoolExecutor(workers) as pool:
        for tile, err, got in pool.map(one, tiles):
            if err:
                bad += 1
                print(f"  {tile}: could not open: {err}", file=sys.stderr)
                continue
            epsg, _, _ = mgrs_grid.origin(tile)
            want = (epsg, mgrs_grid.transform_of(tile)[:6],
                    [mgrs_grid.TILE_PX, mgrs_grid.TILE_PX])
            if got != want:
                bad += 1
                print(f"  {tile}: file says {got}, we computed {want}",
                      file=sys.stderr)
    say(f"verify: {len(tiles)} tile(s), {bad} mismatch(es), "
        f"{time.monotonic() - t0:,.1f}s")
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--quarter", required=True, choices=sorted(schema.QUARTER_MONTHS))
    ap.add_argument("--out", required=True,
                    help="work directory; the staging file lands under "
                         "quarter=YYYY.Qn/ inside it")
    ap.add_argument("--items-dir",
                    help="also write item JSON at {year}/{Qn}/{tile}/{id}.json")
    ap.add_argument("--manifest",
                    help="manifest path or URL (default: the published one)")
    ap.add_argument("--limit", type=int, help="stop after N items (smoke test)")
    ap.add_argument("--tiles", help="comma-separated tile ids to keep")
    ap.add_argument("--verify", type=int, default=0, metavar="N",
                    help="check N sampled tiles against their COG headers")
    a = ap.parse_args(argv)

    config = load_config()
    public_base = config["public_base"].rstrip("/")
    root_href = f"{public_base}/catalog.json"
    source = a.manifest or MANIFEST_URL.format(year=a.year, quarter=a.quarter)
    tiles = [t.strip() for t in a.tiles.split(",")] if a.tiles else None

    t0 = time.monotonic()
    say(f"{a.year} {a.quarter}: reading {source}")
    manifest = read_manifest(source, a.limit, tiles)
    say(f"{a.year} {a.quarter}: {len(manifest):,} item(s), "
        f"{time.monotonic() - t0:,.1f}s")
    if not manifest:
        sys.exit(f"{a.year} {a.quarter}: the manifest matched no items")

    out_dir = Path(a.out) / f"quarter={a.year}.{a.quarter}"
    out_dir.mkdir(parents=True, exist_ok=True)
    staging = out_dir / "items.ndjson"
    tmp = staging.with_name(f".{staging.name}.tmp")
    items_dir = Path(a.items_dir) if a.items_dir else None

    t0, written, size_total = time.monotonic(), 0, 0
    with tmp.open("w") as fh:
        for item_id, sizes in manifest:
            item = build_item(item_id, sizes, public_base, root_href)
            fh.write(json.dumps(schema.normalize(item), separators=(",", ":")))
            fh.write("\n")
            size_total += sum(sizes.values())
            if items_dir:
                tile = schema.split_id(item_id)[2]
                write_json(items_dir / str(a.year) / a.quarter / tile /
                           f"{item_id}.json", item)
            written += 1
    os.replace(tmp, staging)
    say(f"{a.year} {a.quarter}: wrote {written:,} row(s) to {staging} "
        f"({staging.stat().st_size / 1e6:,.1f} MB), "
        f"{time.monotonic() - t0:,.1f}s")
    if items_dir:
        say(f"{a.year} {a.quarter}: item JSON under {items_dir}")
    say(f"{a.year} {a.quarter}: {size_total / 1e12:,.3f} TB of COGs described")

    if a.verify:
        import random
        sample = [schema.split_id(i)[2] for i, _ in manifest]
        random.seed(0)
        sample = random.sample(sample, min(a.verify, len(sample)))
        if verify(sample, public_base, a.year, a.quarter):
            sys.exit("geometry verification failed; not publishing this quarter")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
