#!/usr/bin/env python3
"""The published schema of `mosaics/quarter=YYYY.Qn/items.parquet`.

One flat `COLUMNS` list of (name, DuckDB type, description) is the whole
contract: `build_quarter.py` casts to it, `make_items.py` fills it through
`normalize()`, and the collection's `table:columns` is generated from it.
There is nowhere for the writer and the documentation to drift apart.

Shape, and why
--------------
These items are uniform in a way scene items never are. Every tile in a
quarter shares its datetime, its gsd, its product type and its processing
level, and the only things that vary row to row are the id, the tile, the
CRS, the four hrefs, the four file sizes and the footprint. So the
properties are flattened to the top level, the constant ones included --
they cost almost nothing under zstd with run-length encoding, and a
reader that gets a row back gets a whole STAC item without having to know
what the collection said.

`assets` is the item's assets object verbatim, as a compact JSON string,
which is the only lossless way to carry a nested, per-band structure
through a flat table. The four hrefs are *also* promoted to their own
columns, because reading a window out of a band is the common case and
`B04_href` should not require a JSON parse. There are only four bands, so
promoting them all is cheap; a 13-band collection would not do this.

`links` keeps just the four fields worth carrying (href, rel, title,
type), so the struct is fixed-width and a reader can filter on `rel`
without parsing. The `derived_from` link back to the CDSE item is the one
that matters: it is how a consumer gets from this mirror to the
authoritative record.

Sort key
--------
`SORT_KEY` is `_tile, datetime, _subtile`: tile-major, so one tile's
36-quarter history is contiguous once the quarters are read together, and
so any single quarter's tile lookup is one or two row groups. `datetime`
is constant inside a quarter file and only orders a multi-quarter read;
`_subtile` is the tiebreak that makes the order total, since 71 of 2024
Q2's 28,272 tiles share an MGRS cell with another.

Helper columns are `_`-prefixed and are not STAC. `_tile` is the bare
MGRS cell (`31UFU`) and the join key against the coverage collection;
`_subtile` is the full mosaic tile id (`31UFU_0_0`); `_quarter` repeats
the Hive partition value inside the row, so a glob over every quarter
still knows which quarter each row came from.
"""
from __future__ import annotations

import json
import re

import mgrs_grid

BANDS = ("B02", "B03", "B04", "B08")

# Every id ends with the quarter and the tile it covers, which is where
# the three helper columns come from. They are derived on the way into
# the table rather than carried in the item, so the published item JSON
# holds no underscore-prefixed properties.
ID_RE = re.compile(r"^Sentinel-2_mosaic_(?P<year>\d{4})_(?P<quarter>Q[1-4])_"
                   r"(?P<tile>\d{1,2}[A-Z]{3}_\d+_\d+)$")

# The one media type for every COG in this catalog.
COG_TYPE = "image/tiff; application=geotiff; profile=cloud-optimized"

STAC_VERSION = "1.1.0"
COLLECTION = "mosaics"

# Extensions every item declares. `file` is here for `file:size`, which
# every asset carries from the transfer manifest.
STAC_EXTENSIONS = [
    "https://stac-extensions.github.io/projection/v2.0.0/schema.json",
    "https://stac-extensions.github.io/grid/v1.1.0/schema.json",
    "https://stac-extensions.github.io/product/v0.1.0/schema.json",
    "https://stac-extensions.github.io/processing/v1.2.0/schema.json",
    "https://stac-extensions.github.io/file/v2.1.0/schema.json",
]

_TS = "TIMESTAMP WITH TIME ZONE"
_LINKS = 'STRUCT(href VARCHAR, rel VARCHAR, title VARCHAR, "type" VARCHAR)[]'

SORT_KEY = "_tile,datetime,_subtile"

# Uniform row groups. A quarter is around 28,000 rows, so this is five
# or six groups: small enough that a tile lookup reads a fraction of the
# file, large enough that the footer stays short and the whole file is a
# handful of range requests. The writer fills groups in 2,048-row steps,
# so 5,000 lands as 6,144 -- measured, not a target missed.
ROW_GROUP_SIZE = 5_000
ZSTD_LEVEL = 18

# (name, DuckDB type, description).
COLUMNS = [
    ("id", "VARCHAR",
     "The upstream item id, `Sentinel-2_mosaic_{YYYY}_Q{n}_{MGRS}_{i}_{j}`. "
     "Unchanged from CDSE, so it joins straight against the source catalog."),
    ("datetime", _TS,
     "The first instant of the quarter the mosaic composites, not an "
     "acquisition time. The compositing window is start_datetime to "
     "end_datetime."),
    ("start_datetime", _TS,
     "First instant of the compositing window: the first day of the quarter."),
    ("end_datetime", _TS,
     "Last instant of the compositing window: the end of the last day of the "
     "quarter."),
    ("_tile", "VARCHAR",
     "The bare MGRS grid cell, for example `31UFU`. Join key against the "
     "coverage collection, and the first sort key. Query helper, not STAC."),
    ("_subtile", "VARCHAR",
     "The full mosaic tile id, `{MGRS}_{i}_{j}`, for example `31UFU_0_0`. "
     "Unique within a quarter. Query helper, not STAC."),
    ("_quarter", "VARCHAR",
     "The partition value, `YYYY.Qn`. Present in the row as well as the "
     "directory name, so a glob across quarters keeps it. Query helper, not "
     "STAC."),
    ("grid:code", "VARCHAR",
     "The MGRS cell in STAC grid-extension form, `MGRS-31UFU`. The bare cell "
     "is `_tile`."),
    ("proj:code", "VARCHAR",
     "The tile's own CRS as an authority code, for example `EPSG:32631`. "
     "Every tile is in its MGRS zone's UTM projection, not in EPSG:4326."),
    ("proj:shape", "INTEGER[]",
     "Raster shape in pixels, `[10008, 10008]` for every tile."),
    ("proj:bbox", "DOUBLE[]",
     "The tile's bounding box in its own projected CRS, in metres: "
     "`[minx, miny, maxx, maxy]`, a 100,080 m square."),
    ("gsd", "DOUBLE", "Ground sample distance in metres. Always 10."),
    ("constellation", "VARCHAR",
     "Always `sentinel-2`. The mosaic composites observations from both "
     "Sentinel-2 satellites, so there is no single `platform`."),
    ("instruments", "VARCHAR[]", "Always `[\"msi\"]`."),
    ("product:type", "VARCHAR", "Always `S2MSI_L3__MCQ`."),
    ("processing:level", "VARCHAR",
     "Always `L3`. The mosaic is a Level-3 composite of Level-2A inputs."),
    ("B02_href", "VARCHAR",
     "Full https URL of the blue COG. Promoted out of the assets JSON so a "
     "reader can open a band without parsing it."),
    ("B03_href", "VARCHAR", "Full https URL of the green COG."),
    ("B04_href", "VARCHAR", "Full https URL of the red COG."),
    ("B08_href", "VARCHAR", "Full https URL of the near-infrared COG."),
    ("assets", "VARCHAR",
     "The item's whole `assets` object as a JSON string, verbatim. Parse it "
     "with `json_extract` in DuckDB or `JSON.parse` in a browser. It carries "
     "`file:size`, `proj:transform` and `proj:shape` per band."),
    ("links", _LINKS,
     "The item's links, reduced to href, rel, title and type. `derived_from` "
     "points at the CDSE item this tile mirrors."),
    ("collection", "VARCHAR", "Always `mosaics`."),
    ("bbox", "DOUBLE[]",
     "The item bbox in EPSG:4326, `[west, south, east, north]`. A tile that "
     "crosses the antimeridian has `west > east`, per RFC 7946."),
    ("geometry", "GEOMETRY",
     "The tile footprint in EPSG:4326, as a Polygon, or a MultiPolygon split "
     "at 180 degrees for tiles that cross the antimeridian. Each edge is "
     "densified before reprojection, so it follows the true curve of the UTM "
     "square to under two metres."),
]

# What normalize() emits: geometry travels as GeoJSON text through the
# NDJSON staging file and becomes a GEOMETRY in the COPY.
DATA_COLUMNS = [c for c in COLUMNS if c[0] != "geometry"]
ROW_KEYS = [c[0] for c in DATA_COLUMNS] + ["_geometry_json"]

QUARTER_MONTHS = {"Q1": (1, 3), "Q2": (4, 6), "Q3": (7, 9), "Q4": (10, 12)}
_LAST_DAY = {1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30,
             7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}


def quarter_window(year: int, quarter: str) -> tuple[str, str]:
    """(start_datetime, end_datetime) of a quarter, as RFC 3339 strings.

    February is the only month whose length moves, and Q1 ends in March,
    so no quarter boundary is ever a leap day. The table above is enough.
    """
    first, last = QUARTER_MONTHS[quarter]
    return (f"{year:04d}-{first:02d}-01T00:00:00Z",
            f"{year:04d}-{last:02d}-{_LAST_DAY[last]:02d}T23:59:59Z")


def split_id(item_id: str) -> tuple[int, str, str]:
    """`Sentinel-2_mosaic_2024_Q2_31UFU_0_0` -> (2024, 'Q2', '31UFU_0_0')."""
    m = ID_RE.match(item_id)
    if not m:
        raise ValueError(f"unrecognised item id: {item_id!r}")
    return int(m["year"]), m["quarter"], m["tile"]


def normalize(item: dict) -> dict:
    """One STAC item -> one canonical row.

    The geometry leaves as `_geometry_json` rather than `geometry`: the
    NDJSON staging file carries it as text and `build_quarter.py` turns
    it into a GEOMETRY with `ST_GeomFromGeoJSON` in the COPY.
    """
    p = item["properties"]
    assets = item["assets"]
    year, quarter, subtile = split_id(item["id"])
    row = {
        "id": item["id"],
        "_tile": mgrs_grid.cell_of(subtile),
        "_subtile": subtile,
        "_quarter": f"{year}.{quarter}",
        "collection": item["collection"],
        "bbox": item["bbox"],
        "assets": json.dumps(assets, separators=(",", ":")),
        "links": [{"href": l.get("href"), "rel": l.get("rel"),
                   "title": l.get("title"), "type": l.get("type")}
                  for l in item.get("links", [])],
        "_geometry_json": json.dumps(item["geometry"], separators=(",", ":")),
    }
    for band in BANDS:
        row[f"{band}_href"] = assets[band]["href"]
    for name in ROW_KEYS:
        if name not in row:
            row[name] = p.get(name)
    missing = [n for n in ROW_KEYS if row.get(n) is None]
    if missing:
        raise ValueError(f"{item['id']}: no value for {', '.join(missing)}")
    return {k: row[k] for k in ROW_KEYS}


def table_columns() -> list[dict]:
    """`table:columns` for the collection, generated from COLUMNS.

    The types are the Arrow spellings a reader sees in the file, not the
    DuckDB ones the writer casts to.
    """
    arrow = {
        "VARCHAR": "string", "DOUBLE": "double", "GEOMETRY": "geometry",
        "VARCHAR[]": "list<string>", "DOUBLE[]": "list<double>",
        "INTEGER[]": "list<int32>", _TS: "timestamp[us, tz=UTC]",
        _LINKS: "list<struct<href: string, rel: string, title: string, "
                "type: string>>",
    }
    return [{"name": n, "type": arrow[t], "description": d}
            for n, t, d in COLUMNS]


if __name__ == "__main__":
    print(json.dumps(table_columns(), indent=2))
