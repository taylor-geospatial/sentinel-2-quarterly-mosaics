# AGENTS.md — mosaics

These instructions apply to AI agents and automated clients querying the imagery collection.
Every claim in this guide must be quoted from a source or measured from the data.
Do not add a claim without its source or document a query without running it against the published files.

Read the [catalog agent guide](../AGENTS.md) for DuckDB settings, manifest columns, and bucket-level limitations.

## Published files

The imagery record contains 4,070,300 band COGs for 36 quarters, from 2017 Q1 through 2025 Q4.
Band files use `{year}/{Qn}/{MGRS}_{i}_{j}/{band}.tif`.
Each completed quarter has a `manifest/manifest_{year}_{Qn}.parquet` and a `_status/{year}/{Qn}/complete.json` marker.

The public collection, checked on 2026-10-20, registers 18 partitions with 521,378 item-index rows.
Its item links cover 2021 Q2 through 2025 Q4 except 2023 Q2.
Partition items use `quarter=YYYY.Qn/YYYY.Qn.json`, and their indexes use `quarter=YYYY.Qn/items.parquet`.
The manifest recipes below cover the full 2017–2025 record.

## Reading a tile

Construct the tile URL directly:

```python
def cog_url(year, quarter, subtile, band):
    """quarter is 'Q1'..'Q4'; subtile is like '31UFU_0_0'; band is 'B02'|'B03'|'B04'|'B08'."""
    return (
        "https://data.source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/"
        f"{year}/{quarter}/{subtile}/{band}.tif"
    )
```

On 2026-09-28, `cog_url(2024, "Q2", "31UFU_0_0", "B04")` returned `HTTP/2 200`, `accept-ranges: bytes`, and `access-control-allow-origin: *`.

Set a User-Agent when using `urllib.request.urlopen(...)`.
The CDN rejected Python's default User-Agent with HTTP 403 in the tests recorded in the catalog agent guide.
`requests`, GDAL, and DuckDB send their own User-Agent.

Read a window without downloading the full tile:

```bash
gdal_translate -srcwin 4000 4000 1024 1024 \
  /vsicurl/https://data.source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/2024/Q2/31UFU_0_0/B04.tif \
  window.tif
```

Set `GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR` to avoid directory-listing requests when opening files.

## Manifest queries

Each manifest is a single Parquet row group of about 2.8 MB.
The queries below were tested against the published manifests.

### Tile history

This query returned 36 rows on 2026-09-28, covering 2017 Q1 through 2025 Q4.
Reading the manifests took several minutes on a domestic connection and transferred about 100 MB.

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

### Bands for one quarter

This query reads one manifest.

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

### Quarter totals

For 2024 Q2, the result was `113088 | 28272 | 16.65`, equal to the quarter's completion marker.

```sql
SELECT count(*) AS n_cogs,
       count(DISTINCT item_id) AS n_tiles,
       round(sum(size_bytes) / 1e12, 2) AS tb
FROM read_parquet('https://data.source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/manifest/manifest_2024_Q2.parquet');
```

### Tile identifiers

The item ID follows `Sentinel-2_mosaic_{YYYY}_Q{n}_{MGRS}_{i}_{j}`.
Extract the mosaic tile and MGRS cell identifiers as follows:

```sql
SELECT DISTINCT
       regexp_extract(item_id, 'Q[1-4]_(.*)$', 1)        AS subtile,   -- 31UFU_0_0
       regexp_extract(item_id, 'Q[1-4]_([0-9]{2}[A-Z]{3})', 1) AS mgrs_tile  -- 31UFU
FROM read_parquet('https://data.source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/manifest/manifest_2024_Q2.parquet');
```

For 2024 Q2, this produced 28,272 distinct `subtile` values and 28,204 distinct `mgrs_tile` values.
Use the identifier appropriate to the join.

## Item index

Rows in each quarter's index are sorted by `_tile`, `datetime`, and `_subtile`.
The collection's `partition:glob` and `table:columns` describe the paths and columns.
Check the collection's registered partitions before selecting a quarter.
No item-index query is documented here until it has been run against the published files.

## Raster interpretation

Pixels are stored as `int16` reflectance values multiplied by 10000.
Apply the 0.0001 scale to recover reflectance.
The GeoTIFF has no scale tag.
Nodata is `-32768`.
Test against that value when masking; zero is valid, and the integer raster cannot store NaN.

The 2024 Q2 measurement found 119 distinct `EPSG:326xx`/`EPSG:327xx` codes.
Each tile uses the UTM zone of its MGRS cell.
Check the CRS before combining tiles or computing areas and distances, and reproject tiles with different coordinate systems.

Each pixel is the first quartile of three months of observations.
`datetime` labels the first instant of the quarter rather than an acquisition time.
Use `start_datetime` and `end_datetime` for the observation window.

Nodata marks pixels with no cloud-free observations during the quarter.
Persistently cloudy tropical tiles can have large nodata areas without a transfer failure.

The mirror omits `observations.tif`, which records the number of valid observations per pixel.
You cannot infer from these bands whether a pixel is based on 40 observations or 2.
Use the original CDSE product when observation counts are needed.

## Antimeridian tiles

MGRS zones 01 and 60 lie on either side of 180°.
In 2024 Q2, 125 of 28,272 tiles crossed the antimeridian.
Their geometry is a MultiPolygon split at 180°, and their `bbox` has `west > east`, as specified by RFC 7946 for antimeridian-crossing bounds.

The filter `bbox[0] < x AND x < bbox[2]` excludes those 125 tiles.
A check requiring `west <= east` rejects their bounds.
Handle longitude wrapping explicitly, or document the exclusion of zones 01 and 60.
