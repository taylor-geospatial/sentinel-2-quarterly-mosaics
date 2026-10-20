# AGENTS.md — Sentinel-2 Quarterly Cloudless Mosaics

These instructions apply to AI agents and automated clients querying the catalog.
Every claim in this guide must be quoted from a source or measured from the data.
Do not add a claim without its source, or document a query without running it against the published files.

## Catalog contents

The public root is `https://data.source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/catalog.json`.
The catalog describes quarterly cloud-free Sentinel-2 composites at 10 m for 36 quarters, from 2017 Q1 through 2025 Q4.

- `mosaics` contains 1,017,575 mosaic tiles, 4,070,300 single-band Cloud-Optimized GeoTIFFs, and 607.5 TB.
  Read the [mosaics guide](mosaics/AGENTS.md) before querying it.
- `coverage` contains per-tile, per-quarter statistics and tile footprints.
  Read the [coverage guide](coverage/AGENTS.md) before querying it.

The European Space Agency produces the Sentinel-2 Global Mosaics, and the Copernicus Data Space Ecosystem publishes them.
Taylor Geospatial Engine hosts this mirror.
The four mirrored bands are unchanged, with no filtering, reclassification, or recomputation.
Do not describe this catalog as the source.

## Published files

The imagery is complete and readable.
The public mosaics collection, checked on 2026-10-20, registers 18 partitions with 521,378 item-index rows.
Its item links cover 2021 Q2 through 2025 Q4 except 2023 Q2.
Use the manifests for quarters outside that registered index coverage.

The coverage table and footprints are published.
Measured on 2026-10-20, the table has 912,320 rows across 32 quarters, from 2018 Q1 through 2025 Q4.
Use the manifests for 2017.
The [coverage guide](coverage/AGENTS.md) includes a tested query and measured limits.

The 36 per-quarter manifests for 2017–2025 cover the full imagery record described by this catalog:

```
https://data.source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/manifest/manifest_{year}_{Qn}.parquet
```

The queries below use those manifests.
The bucket also holds a 2026 Q2 manifest, so unrestricted globs include a quarter outside the catalog's extent.

## Tile keys

A tile can be read without an index lookup using this key:

```
{year}/{Qn}/{MGRS}_{i}_{j}/{band}.tif
```

The `band` is one of `B02`, `B03`, `B04`, or `B08`.
The corresponding STAC item ID is `Sentinel-2_mosaic_{YYYY}_Q{n}_{MGRS}_{i}_{j}`.
String operations convert between the ID and key.

The following URL returned `HTTP/2 200`, `accept-ranges: bytes`, and `access-control-allow-origin: *` on 2026-09-28:

```
https://data.source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/2024/Q2/31UFU_0_0/B04.tif
```

## DuckDB settings

### S3 endpoint

Set `s3_endpoint = 'data.source.coop'` for S3 reads.
Source Cooperative uses the account name, `tge-labs`, as the bucket and the product slug as the key prefix.
Anonymous reads need no credentials.

### Path-style addressing

Set `s3_url_style = 'path'` for every S3 read.
The proxy does not serve virtual-host-style subdomains, and omitting this setting caused the following error:

```
IO Error: Could not resolve hostname error for HTTP HEAD to
'https://tge-labs.data.source.coop/sentinel-2-quarterly-cloudless-mosaics/manifest/manifest_2024_Q2.parquet'
```

No `s3_region` setting is needed against the proxy.
On 2026-09-28, the same query succeeded with the region unset and failed with the URL style unset.

### HTTP timeout

The manifest queries documented here read files containing one Parquet row group of about 2.8 MB each.
On a slow connection, the default timeout interrupted reads and produced these errors:

```
Invalid Error: Out of buffer
Invalid Error: TProtocolException: Invalid data
```

Setting `http_timeout = 600000` and `http_retries = 5` resolved those failures.
The errors in those tests did not indicate damaged files.

## Worked queries

The following queries ran against the published files on 2026-09-28.
The reported counts are their measured results.

### One quarter

DuckDB can read a named HTTPS file without credentials.

```sql
INSTALL httpfs; LOAD httpfs;
SET http_timeout = 600000;
SET http_retries = 5;

SELECT count(*) AS n_cogs,
       count(DISTINCT item_id) AS n_tiles,
       round(sum(size_bytes) / 1e12, 2) AS tb
FROM read_parquet('https://data.source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/manifest/manifest_2024_Q2.parquet');
```

The result was `113088 | 28272 | 16.65`, equal to the values in `_status/2024/Q2/complete.json`.

### Multiple quarters

DuckDB cannot expand an HTTPS glob without a listing service.
Use anonymous S3 access to expand the glob.

```sql
INSTALL httpfs; LOAD httpfs;
SET s3_endpoint = 'data.source.coop';
SET s3_url_style = 'path';   -- required, see above
SET http_timeout = 600000;
SET http_retries = 5;

SELECT year, quarter, count(DISTINCT item_id) AS tiles
FROM read_parquet('s3://tge-labs/sentinel-2-quarterly-cloudless-mosaics/manifest/manifest_2024_Q[12].parquet')
GROUP BY 1, 2 ORDER BY 1, 2;
```

The results were `2024 Q1 → 29528` and `2024 Q2 → 28272`, equal to both completion markers.

### One tile across 36 quarters

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

The query returned 36 rows, one per quarter from 2017 Q1 through 2025 Q4.
It took several minutes on a domestic connection, reading roughly 100 MB of manifests.
Cache the result when repeating the analysis.
`coverage/tiles.parquet` provides the 2018–2025 portion in one table, but use the manifest query for 2017.

## Manifest columns

The column definitions were measured with `DESCRIBE` on `manifest/manifest_2024_Q2.parquet` on 2026-09-28.

| Column | Type | Meaning |
|---|---|---|
| `year` | `BIGINT` | Calendar year of the quarter |
| `quarter` | `VARCHAR` | `Q1`–`Q4`. A string, not a number |
| `item_id` | `VARCHAR` | `Sentinel-2_mosaic_{YYYY}_Q{n}_{MGRS}_{i}_{j}` |
| `band` | `VARCHAR` | `B02`, `B03`, `B04` or `B08` |
| `source_bucket` | `VARCHAR` | Always `eodata` |
| `source_key` | `VARCHAR` | The key in the CDSE bucket |
| `size_bytes` | `BIGINT` | Object size |
| `destination_bucket` | `VARCHAR` | Always `tge-labs` |
| `destination_key` | `VARCHAR` | The key here, without a leading slash |

Construct the public URL as `'https://data.source.coop/' || destination_bucket || '/' || destination_key`.
The manifests record transfers and have no geometry column.

## Join keys

| From | Column | To | Column | Unique? |
|---|---|---|---|---|
| manifests | `item_id` | `mosaics` index | `id` | unique per quarter |
| `mosaics` index | `_subtile` | `coverage` | `subtile` | not unique: one row per quarter |
| `coverage` | `subtile` | `footprints.pmtiles` | `subtile` property | many table rows per footprint |
| `mosaics` index | `_tile` | MGRS grid | cell id | not unique |

`_tile` identifies an MGRS cell, such as `31UFU`, and `_subtile` identifies a mosaic tile, such as `31UFU_0_0`.
The 2024 Q2 measurement counted 28,272 mosaic tiles in 28,204 distinct MGRS cells and reported 68 cells with more than one tile.
Join on `_subtile` unless the analysis requires MGRS cells.

## Data interpretation

### Reflectance and nodata

Pixels are `int16` values scaled by 10000.
Multiply by 0.0001 to obtain reflectance.
GDAL reports no scale factor, so unapplied scaling yields values such as 1000 instead of 0.1 without an error.

Nodata is `-32768`, and zero is a valid reflectance value.
A `value > 0` mask discards valid dark pixels.
Test against `-32768`.

### Coordinate systems

Each tile uses the UTM zone of its MGRS cell.
The 2024 Q2 measurements found 119 distinct EPSG codes, using `EPSG:326xx` in the north and `EPSG:327xx` in the south.
Check the CRS before combining tiles or computing areas and distances, and reproject tiles with different coordinate systems.
Do not assume neighboring tiles share a projection.

### Dates

An item's `datetime` labels the first day of the quarter.
Each pixel is a percentile of three months of observations, so it has no single acquisition time.
Use `start_datetime` and `end_datetime` for the observation window.

### Benchmark files

Exclude `_benchmarks/` from imagery listings and size totals.
It held 3,224 fixed-size `.bin` throughput-test files totaling 481.1 GB, with real tile IDs in their names.
For example, `_benchmarks/20260922-copy-c128/eodata-transfer-1/Sentinel-2_mosaic_2024_Q2_16QDE_0_0/B02.bin` is a benchmark file.
Recursive listings and `**` globs include these files.

### 2026 transfers

`2026/Q2/` finished transferring on 2026-09-28 at 14:44 UTC, after the collections were written.
Its completion marker and `manifest_2026_Q2.parquet` record 115,572 objects across 28,893 tiles, totaling 17.25 TB.
The additional manifest brings the documented glob count to 37 files.
Filter on `year <= 2025` to select the 36 quarters from 2017 Q1 through 2025 Q4 described here.

The 2026 Q2 tiles use 256 × 256 internal blocks, compared with 1024 × 1024 for 2017–2025.
This difference was measured with `gdalinfo` on `2026/Q2/31UFU_0_0/B04.tif` on 2026-09-28.
The COG layout, DEFLATE compression, horizontal predictor, 10008 × 10008 dimensions, `int16` type, `-32768` nodata, and five overviews are unchanged.
Windowed reads request more, smaller byte ranges for the same pixels.
The quarter remains outside the catalog until this difference is documented in its metadata.

`2026/Q1/` held 799 objects without a completion marker or manifest when inspected.
Do not use that unfinished transfer as data or interpret its absence from manifest globs as evidence that it is absent upstream.

### Python HTTP requests

On 2026-09-28, the CDN rejected Python's default `Python-urllib/3.x` User-Agent with `HTTP Error 403: Forbidden` on public objects.
A request for `_status/2024/Q2/complete.json` returned 403 with the default User-Agent and 200 with `User-Agent: curl/8.0`.
Changing credentials was not needed for that failure.

```python
import urllib.request

req = urllib.request.Request(url, headers={"User-Agent": "my-tool/1.0"})
body = urllib.request.urlopen(req).read()  # any non-default UA works
```

`curl`, DuckDB's `httpfs`, GDAL's `/vsicurl`, and `requests` send their own User-Agent and were unaffected.
Set a User-Agent when using `urllib`.

### Upstream revisions

Upstream reprocesses old quarters, so a copied quarter can differ from the current CDSE product.
Compare manifests when checking for changes.

### Spectral bandwidths

CDSE's `eo:full_width_half_max` values are roughly 0.54 times each band's center wavelength.
The following values were read from `Sentinel-2_mosaic_2026_Q2_60XWF_1_0` on 2026-09-28:

| Band | CDSE `full_width_half_max` | ESA mission value | Used here |
|---|---|---|---|
| B02 | 0.267 µm | 0.065 µm | 0.065 µm |
| B03 | 0.291 µm | 0.035 µm | 0.035 µm |
| B04 | 0.342 µm | 0.031 µm | 0.031 µm |
| B08 | 0.454 µm | 0.115 µm | 0.115 µm |

A red band 0.342 µm wide would span green through near infrared.
The catalog uses the ESA mission bandwidths instead of those CDSE values.
Preserve this documented metadata correction rather than copying the upstream values.

## Links

Assets and structural links resolve relative to the object containing them.
The root has an absolute `self` link, as recommended by Portolan schema v0.2.0.
Collections do not have a `self` link, so clients must track their locations.
Human-facing pages use `source.coop`, and machine reads use `data.source.coop` for the same objects.
