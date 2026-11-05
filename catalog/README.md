# Sentinel-2 Quarterly Cloudless Mosaics

This catalog describes cloud-free Sentinel-2 composites at 10 m for 36 quarters, from 2017 Q1 through 2025 Q4.
The mirror has 1,017,575 mosaic tiles with four bands each, for a total of 4,070,300 Cloud-Optimized GeoTIFFs and 607.5 TB.
Anyone can download the files over HTTPS without a key, account, or request quota.

The European Space Agency produces the [Copernicus Sentinel-2 Global Mosaics](https://documentation.dataspace.copernicus.eu/Data/SentinelMissions/Sentinel2.html) (product `S2MSI_L3__MCQ`), and the Copernicus Data Space Ecosystem (CDSE) publishes them.
Downloading from CDSE requires an account.
[Taylor Geospatial Engine](https://tgengine.org) hosts this anonymous copy on [Source Cooperative](https://source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics).

## Bucket layout

The imagery is stored at the top level of the bucket, one directory per year and quarter:

```
{year}/{Qn}/{MGRS}_{i}_{j}/{band}.tif     band COGs (B02, B03, B04, B08)
{year}/{Qn}/{MGRS}_{i}_{j}/{item_id}.json STAC item for the tile
mosaics/                                  STAC collection for the imagery
mosaics/quarter={year}.{Qn}/              item index, browse overview, and thumbnail for one quarter
coverage/                                 per-tile, per-quarter table and tile footprints
manifest/, _status/                       transfer records
```

The `mosaics/` and `coverage/` directories contain catalog metadata and small derived files, not imagery.
`mosaics/quarter=2024.Q2/items.parquet` lists the 2024 Q2 tiles, and its hrefs point to the band files under `2024/Q2/`.

## Composite values

For each pixel and band, the upstream algorithm gathers Sentinel-2 Level-2A surface reflectance observations from the three-month window.
It drops observations flagged as cloud in the Level-2A scene classification and outputs the first quartile of the remaining values.
Pixels with no valid observation are left empty.

Using the 25th percentile instead of the mean reduces the effect of missed clouds and haze, which are brighter than the surface.
The composites give consistent basemaps across scene boundaries and seasons, including in persistently cloudy areas.
The upstream [mosaic algorithm note](https://documentation.dataspace.copernicus.eu/Data/Others/Sentinel2_Mosaic_Algorithm.html) describes the method.

Values are `int16` reflectance multiplied by 10000, so multiply by 0.0001 to get surface reflectance.
The GeoTIFF does not record this scale factor, and an unscaled value of 1000 corresponds to a reflectance of 0.1.
Nodata is `-32768`.

| Band | Name | Center | Bandwidth | Resolution |
|---|---|---|---|---|
| `B02` | Blue | 0.493 µm | 0.065 µm | 10 m |
| `B03` | Green | 0.560 µm | 0.035 µm | 10 m |
| `B04` | Red | 0.665 µm | 0.031 µm | 10 m |
| `B08` | NIR | 0.833 µm | 0.115 µm | 10 m |

Each tile is 10008 × 10008 pixels, about 100 km square, in the UTM zone of its MGRS grid cell.
The files are COGs with DEFLATE compression, a horizontal predictor, 1024 × 1024 internal tiles, and five overview levels down to 313 × 313.

## Coverage

All 36 quarters are complete according to the transfer's `_status/{year}/{Qn}/complete.json` markers, read on 2026-09-28.

| Years | Quarters | Mosaic tiles | COG files | Size |
|---|---|---|---|---|
| 2017–2025 | 36 | 1,017,575 | 4,070,300 | 607.5 TB |

Quarterly counts range from 25,172 tiles (2017 Q3) to 30,668 (2019 Q1).
The early years have fewer tiles because the mosaic footprint grew over the record.

The bucket also has two 2026 prefixes that this catalog does not cover.
`2026/Q2/` finished transferring on 2026-09-28 at 14:44 UTC, after the collections were written.
Its completion marker and manifest record 115,572 objects across 28,893 tiles, for a total of 17.25 TB.
The 2026 Q2 files are readable and match the earlier quarters in COG layout, compression, predictor, 10008 × 10008 dimensions, `int16` type, `-32768` nodata, and five overview levels.
They use 256 × 256 internal blocks instead of 1024 × 1024, so a windowed read fetches more, smaller byte ranges for the same pixels.
The quarter will be added to the catalog once that difference is documented.
`2026/Q1/` had 799 objects (89.7 GB) and no completion marker or manifest when inspected, so do not use it as data.

Because of the 2026 Q2 manifest, `manifest/manifest_*.parquet` matches 37 files.
Filter on `year <= 2025` to select the 36 quarters described here.

## Transfer records

- `_benchmarks/` contained 3,224 fixed-size `.bin` throughput-test files totaling 481.1 GB, counted on 2026-09-28.
  They have no georeferencing but use real tile names, so exclude this prefix from recursive listings, `**/*` globs, and imagery size totals.
- `_status/` has a `complete.json` for each finished quarter, recording its object count, byte total, and manifest checksum, plus per-worker progress files.
- `manifest/` has one `manifest_{year}_{Qn}.parquet` per quarter, listing each transferred object's source key, destination key, and size.
  The [mosaics agent guide](mosaics/AGENTS.md) has tested queries.

## Access

The red band of tile `31UFU_0_0` for 2024 Q2 is at:

```
https://data.source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/2024/Q2/31UFU_0_0/B04.tif
```

For anonymous S3 access, the bucket is the account name and the key prefix is the product slug:

```
s3://tge-labs/sentinel-2-quarterly-cloudless-mosaics/
```

Set the endpoint to `https://data.source.coop` and use path-style addressing.
In DuckDB, set `s3_endpoint = 'data.source.coop'` and `s3_url_style = 'path'`.
Credentials and region can be left unset.

Before Source Cooperative 0.3, the S3 address was `s3://us-west-2.opendata.source.coop/tge-labs/...`, with the account inside a region-named bucket.
That address still resolved when this guide was written, but use the current form above.

To read a window without downloading the whole tile:

```bash
gdal_translate -srcwin 4000 4000 1024 1024 \
  /vsicurl/https://data.source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/2024/Q2/31UFU_0_0/B04.tif \
  window.tif
```

To count a quarter's files from its transfer manifest without listing the bucket:

```sql
INSTALL httpfs; LOAD httpfs;
SET http_timeout = 600000;

SELECT count(*) AS cogs, count(DISTINCT item_id) AS tiles
FROM read_parquet('https://data.source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/manifest/manifest_2024_Q2.parquet');
-- 113088 cogs, 28272 tiles
```

The [catalog agent guide](AGENTS.md) documents the query settings, joins, and interpretation limits.

## License

The imagery is covered by the [Legal Notice on the Use of Copernicus Sentinel Data and Service Information](https://sentinels.copernicus.eu/documents/247904/690755/Sentinel_Data_Legal_Notice).
The notice permits free, full, and open access, including commercial use and redistribution.
These terms have no SPDX identifier, so the collections declare `license: "other"` and link to the notice with `rel: license`.

Use this attribution:

> Copernicus Sentinel data 2017–2025

The notice requires derived data to be identified as modified Copernicus Sentinel data, so readers can tell derived products from ESA products.
Taylor Geospatial Engine's mirroring, catalog metadata, and derived coverage statistics add no restrictions.

## Provenance

The European Space Agency produces the Sentinel-2 Global Mosaics.
Sinergise Solutions runs mosaic production, and CloudFerro operates the CDSE infrastructure that this copy was made from.
Each collection has a `canonical` link to the upstream [`sentinel-2-global-mosaics`](https://stac.dataspace.copernicus.eu/v1/collections/sentinel-2-global-mosaics) collection in the CDSE STAC API.

The mirrored B02, B03, B04, and B08 files are byte-identical to the source.
The mirror omits `observations.tif`, `userdata.json`, and the zipped `Product` asset, so this catalog describes four of the seven upstream assets per tile.
Item IDs are unchanged and can be used to find the upstream records.

Upstream reprocesses old quarters, so each copied quarter is the product as it was at transfer time.
Sync checks compare manifests to detect later upstream changes.

## Collections

- [`mosaics`](mosaics/collection.json) describes the bands and tile geometry of the imagery, and has one partition per quarter with its item index.
  See its [README](mosaics/README.md) and [agent guide](mosaics/AGENTS.md).
- [`coverage`](coverage/collection.json) has tile footprints and per-tile, per-quarter statistics.
  See its [README](coverage/README.md) and [agent guide](coverage/AGENTS.md).
