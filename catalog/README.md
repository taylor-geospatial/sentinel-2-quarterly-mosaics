# Sentinel-2 Quarterly Cloudless Mosaics

This catalog describes cloud-free Sentinel-2 composites at 10 m for 36 quarters, from 2017 Q1 through 2025 Q4.
The four bands comprise 1,017,575 mosaic tiles, 4,070,300 Cloud-Optimized GeoTIFFs, and 607.5 TB.
The files are available over HTTPS without a key, account, or request quota.

The European Space Agency produces the [Copernicus Sentinel-2 Global Mosaics](https://documentation.dataspace.copernicus.eu/Data/SentinelMissions/Sentinel2.html) (product `S2MSI_L3__MCQ`), and the Copernicus Data Space Ecosystem publishes them.
Upstream access requires a CDSE account.
[Taylor Geospatial Engine](https://tgengine.org) hosts this anonymous copy on [Source Cooperative](https://source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics).

## Composite values

For each pixel and band, the upstream algorithm gathers Sentinel-2 Level-2A surface reflectance observations from the three-month window.
It removes observations marked as cloud by the scene classification layer and outputs the first quartile of the remaining values.
Pixels with no valid observation remain empty.

The 25th percentile suppresses bright values from missed clouds and haze that a mean would retain.
The composites provide consistent basemaps across scene boundaries and seasons, including persistently cloudy areas.
See the upstream [mosaic algorithm note](https://documentation.dataspace.copernicus.eu/Data/Others/Sentinel2_Mosaic_Algorithm.html) for the method.

Values are `int16`, scaled by 10000.
Multiply by 0.0001 to obtain surface reflectance.
The GeoTIFF does not record this scale factor, so an unscaled value of 1000 corresponds to a reflectance of 0.1.
Nodata is `-32768`.

| Band | Name | Center | Bandwidth | Resolution |
|---|---|---|---|---|
| `B02` | Blue | 0.493 µm | 0.065 µm | 10 m |
| `B03` | Green | 0.560 µm | 0.035 µm | 10 m |
| `B04` | Red | 0.665 µm | 0.031 µm | 10 m |
| `B08` | NIR | 0.833 µm | 0.115 µm | 10 m |

Each tile is 10008 × 10008 pixels, about 100 km square, in the UTM zone of its MGRS grid cell.
The files use COG layout, DEFLATE compression with a horizontal predictor, 1024 × 1024 internal tiles, and five overview levels down to 313 × 313.

## Coverage

All 36 quarters are complete according to the transfer's `_status/{year}/{Qn}/complete.json` markers, read on 2026-09-28.

| Years | Quarters | Mosaic tiles | COG files | Size |
|---|---|---|---|---|
| 2017–2025 | 36 | 1,017,575 | 4,070,300 | 607.5 TB |

Quarterly counts range from 25,172 tiles (2017 Q3) to 30,668 (2019 Q1).
The mosaic footprint expanded over the record, accounting for the lower counts in the early years.

The bucket also contains two 2026 prefixes outside this catalog's extent.
`2026/Q2/` finished transferring on 2026-09-28 at 14:44 UTC, after the collections were written.
Its completion marker and manifest record 115,572 objects across 28,893 tiles, totaling 17.25 TB.
The files are readable, but adding the quarter to the catalog requires documenting its different internal block size.
`2026/Q1/` held 799 objects (89.7 GB) with no completion marker or manifest when inspected.
Do not use that unfinished transfer as data.

2026 Q2 tiles use 256 × 256 internal blocks, compared with 1024 × 1024 for 2017–2025.
The COG layout, DEFLATE compression, horizontal predictor, 10008 × 10008 dimensions, `int16` type, `-32768` nodata, and five overview levels are unchanged.
A windowed read fetches more, smaller byte ranges for the same pixels.

The documented imagery record remains 2017 Q1 through 2025 Q4.
With the additional 2026 Q2 manifest, `manifest/manifest_*.parquet` matches 37 files.
Filter on `year <= 2025` to select the quarters described here.

## Transfer records

Three prefixes accompany the imagery:

- `_benchmarks/` held 3,224 fixed-size `.bin` throughput-test files totaling 481.1 GB, counted on 2026-09-28.
  They have no georeferencing but use real tile names, so exclude this prefix from recursive listings, `**/*` globs, and imagery size totals.
- `_status/` holds a `complete.json` for each finished quarter and per-worker progress files.
  The completion marker records the object count, byte total, and manifest checksum.
- `manifest/` holds `manifest_{year}_{Qn}.parquet` files listing each transferred object's source key, destination key, and size.
  These records cover the full imagery history, including quarters without a published item index.
  See the [mosaics agent guide](mosaics/AGENTS.md) for tested queries.

## Access

Tile keys follow this pattern:

```
{year}/{Qn}/{MGRS}_{i}_{j}/{band}.tif
```

The red band of tile `31UFU_0_0` for 2024 Q2 is available at:

```
https://data.source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/2024/Q2/31UFU_0_0/B04.tif
```

Anonymous S3 access uses the account name as the bucket and the product slug as the key prefix:

```
s3://tge-labs/sentinel-2-quarterly-cloudless-mosaics/
```

Set the endpoint to `https://data.source.coop` and use path-style addressing.
In DuckDB, set `s3_endpoint = 'data.source.coop'` and `s3_url_style = 'path'`.
No credentials or region are needed.

Before Source Cooperative 0.3, the S3 form was `s3://us-west-2.opendata.source.coop/tge-labs/...`, with the account inside a region-named bucket.
That address still resolved when this guide was written, but use the current form above.

Read a window without downloading the whole tile:

```bash
gdal_translate -srcwin 4000 4000 1024 1024 \
  /vsicurl/https://data.source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/2024/Q2/31UFU_0_0/B04.tif \
  window.tif
```

Query a transfer manifest without listing the bucket:

```sql
INSTALL httpfs; LOAD httpfs;
SET http_timeout = 600000;

SELECT count(*) AS cogs, count(DISTINCT item_id) AS tiles
FROM read_parquet('https://data.source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/manifest/manifest_2024_Q2.parquet');
-- 113088 cogs, 28272 tiles
```

The [catalog agent guide](AGENTS.md) documents the query settings, joins, and interpretation limits.

## License

The imagery uses the [Legal Notice on the Use of Copernicus Sentinel Data and Service Information](https://sentinels.copernicus.eu/documents/247904/690755/Sentinel_Data_Legal_Notice).
It permits free, full, and open access, including commercial use and redistribution.
There is no SPDX identifier for those terms, so the collections declare `license: "other"` and link to the notice with `rel: license`.

Use this attribution:

> Copernicus Sentinel data 2017–2025

Identify modified data as modified Copernicus Sentinel data, as the notice requires, so readers can distinguish derived products from ESA products.
Taylor Geospatial Engine's mirroring, catalog metadata, and derived coverage statistics add no restrictions.

## Provenance

The European Space Agency produces the Sentinel-2 Global Mosaics.
Sinergise Solutions runs mosaic production, and CloudFerro operates the Copernicus Data Space Ecosystem infrastructure used for this copy.
Each collection has a `canonical` link to the upstream [`sentinel-2-global-mosaics`](https://stac.dataspace.copernicus.eu/v1/collections/sentinel-2-global-mosaics) collection in the CDSE STAC API.

The four mirrored bands are byte-identical to the source.
The mirror omits `observations.tif`, `userdata.json`, and the zipped `Product` asset, so this catalog describes four assets per tile rather than seven.
Item IDs are unchanged and can be used to locate the upstream records.

Upstream reprocesses old quarters, so each copied quarter reflects the product at the time of transfer.
Sync checks compare manifests to detect later changes.

## Collections

- [`mosaics`](mosaics/collection.json) contains the imagery, band descriptions, tile geometry, item index, and per-quarter partitions.
  See its [README](mosaics/README.md) and [agent guide](mosaics/AGENTS.md).
- [`coverage`](coverage/collection.json) contains tile footprints and per-tile, per-quarter statistics.
  See its [README](coverage/README.md) and [agent guide](coverage/AGENTS.md).
