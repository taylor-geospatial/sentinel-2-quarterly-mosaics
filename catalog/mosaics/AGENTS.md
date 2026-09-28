# AGENTS.md — mosaics

Guidance for AI agents and automated clients querying the imagery collection.

**One rule survives every edit to this file.** Every claim here is either quoted
from a source or measured from the data. If you cannot point at where a fact
came from, it does not belong in this file.

Read the [catalog agent guide](../AGENTS.md) first. It carries the two DuckDB
settings without which these queries fail, the manifest schema, and the
bucket-level traps. Nothing here repeats them.

## What you can read today

| Thing | Path | Exists? |
|---|---|---|
| Band COGs | `{year}/{Qn}/{MGRS}_{i}_{j}/{band}.tif` | yes, 4,070,300 of them |
| Transfer manifests | `manifest/manifest_{year}_{Qn}.parquet` | yes, 36 |
| Completion markers | `_status/{year}/{Qn}/complete.json` | yes, 36 |
| Item index | `mosaics/quarter=YYYY.Qn/items.parquet` | **not yet** |
| Per-quarter partition items | `mosaics/quarter=YYYY.Qn/YYYY.Qn.json` | **not yet** |

Queries below are grouped by what they need. Nothing is written against
`items.parquet` while it does not exist.

## Reading a tile without any index

The key is constructible, so the cheapest lookup is no lookup:

```python
def cog_url(year, quarter, subtile, band):
    """quarter is 'Q1'..'Q4'; subtile is like '31UFU_0_0'; band is 'B02'|'B03'|'B04'|'B08'."""
    return ("https://data.source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/"
            f"{year}/{quarter}/{subtile}/{band}.tif")
```

Verified on 2026-09-28: `cog_url(2024, "Q2", "31UFU_0_0", "B04")` returns
`HTTP/2 200`, `accept-ranges: bytes`, `access-control-allow-origin: *`.

Read a window without downloading the tile:

```bash
gdal_translate -srcwin 4000 4000 1024 1024 \
  /vsicurl/https://data.source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/2024/Q2/31UFU_0_0/B04.tif \
  window.tif
```

Set `GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR`. Without it GDAL lists the
containing directory on every open, which on this bucket is a wasted round trip
against a prefix with a million siblings.

## Available now: queries against the manifests

The manifests are the authority on what exists until the item index publishes.
Each is a single 2.8 MB Parquet row group, so every query reads the whole file
and no predicate prunes anything. Budget accordingly.

**Every quarter a tile appears in, with its URL.** Run on 2026-09-28; returns
36 rows for this tile, 2017 Q1 through 2025 Q4. Expect several minutes: the
glob pulls all 36 manifests, about 100 MB, and none of them can be pruned.

```sql
INSTALL httpfs; LOAD httpfs;
SET s3_endpoint = 'data.source.coop';
SET s3_url_style = 'path';
SET http_timeout = 600000;
SET http_retries = 5;

SELECT year, quarter, item_id,
       'https://data.source.coop/' || destination_bucket || '/' || destination_key AS url,
       size_bytes
FROM read_parquet('s3://tge-labs/sentinel-2-quarterly-cloudless-mosaics/manifest/manifest_*.parquet')
WHERE item_id LIKE '%_31UFU_0_0' AND band = 'B04'
  AND year <= 2025   -- 2026 Q2 landed after this catalog was written
ORDER BY year, quarter;
```

**All four bands of one tile for one quarter.** One file, so this is fast.

```sql
INSTALL httpfs; LOAD httpfs;
SET http_timeout = 600000;

SELECT band,
       'https://data.source.coop/' || destination_bucket || '/' || destination_key AS url,
       size_bytes
FROM read_parquet('https://data.source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/manifest/manifest_2024_Q2.parquet')
WHERE item_id = 'Sentinel-2_mosaic_2024_Q2_31UFU_0_0'
ORDER BY band;
```

**What a quarter holds.** Returns `113088 | 28272 | 16.65` for 2024 Q2, which
matches that quarter's completion marker exactly.

```sql
SELECT count(*) AS n_cogs,
       count(DISTINCT item_id) AS n_tiles,
       round(sum(size_bytes) / 1e12, 2) AS tb
FROM read_parquet('https://data.source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/manifest/manifest_2024_Q2.parquet');
```

**Extracting the tile id from the item id.** The item id is
`Sentinel-2_mosaic_{YYYY}_Q{n}_{MGRS}_{i}_{j}`, so:

```sql
SELECT DISTINCT
       regexp_extract(item_id, 'Q[1-4]_(.*)$', 1)        AS subtile,   -- 31UFU_0_0
       regexp_extract(item_id, 'Q[1-4]_([0-9]{2}[A-Z]{3})', 1) AS mgrs_tile  -- 31UFU
FROM read_parquet('https://data.source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/manifest/manifest_2024_Q2.parquet');
```

Run on 2024 Q2 this gives 28,272 distinct `subtile` values and 28,204 distinct
`mgrs_tile` values. The two are not interchangeable.

## Available after the backfill

Once `quarter=YYYY.Qn/items.parquet` publishes, the same questions are answered
by a range read instead of a full download, because the file is sorted
tile-major. The collection's `partition:glob` and `table:columns` describe the
shape. **Do not write queries against it yet**, and do not assume a column
exists because `table:columns` lists it: that array is the contract the
generator writes to, and `table:row_count` being 0 is how you can tell nothing
has been written.

## Quirks specific to this collection

**Apply the 0.0001 scale yourself.** `int16` values are reflectance × 10000 and
the GeoTIFF carries no scale tag. Nothing warns you.

**NoData is `-32768`.** Not 0, and not NaN. `value > 0` is the wrong mask.

**119 EPSG codes in one quarter.** Measured across 2024 Q2: every tile is in the
UTM zone of its MGRS cell, giving 119 distinct `EPSG:326xx`/`EPSG:327xx` codes.
Two adjacent tiles frequently differ. Reproject before any cross-tile
computation, and never compute area or distance without checking the CRS of the
tile you are actually in.

**The composite has no acquisition time.** A pixel is the first quartile of
three months of observations. `datetime` is the first instant of the quarter, a
label, not a measurement. Use `start_datetime` and `end_datetime`, and do not
report the `datetime` as when the image was taken.

**Empty pixels are meaningful.** Where no cloud-free observation existed in the
quarter, the pixel is nodata. That is information about cloud persistence, not a
transfer failure. Persistently cloudy tropical tiles legitimately have large
nodata areas.

**`observations.tif` is not here.** The upstream band that says how many valid
observations backed each pixel was not mirrored. You cannot tell a pixel backed
by 40 observations from one backed by 2. If that distinction matters, go to CDSE
for the original product.

**Antimeridian tiles exist, and their bboxes look wrong.** Tiles in MGRS zones
01 and 60 sit either side of 180°. In 2024 Q2, **125 of 28,272 tiles** cross it.
Each is a MultiPolygon split at 180° rather than a wrapped polygon, and its
`bbox` has `west > east`, which is what RFC 7946 requires for a geometry that
crosses the antimeridian.

That means a filter written as `bbox[0] < x AND x < bbox[2]` silently drops
every one of those 125 tiles, and a bounds check that asserts `west <= east`
will reject them as malformed. Neither raises an error. Handle the wrap
explicitly, or exclude zones 01 and 60 knowingly rather than by accident.
