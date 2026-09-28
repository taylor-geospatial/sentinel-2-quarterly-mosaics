# Sentinel-2 Quarterly Cloudless Mosaics

Cloud-free composites of the whole Sentinel-2 archive, one per quarter, at 10 m,
for 36 quarters from 2017 Q1 through 2025 Q4. Four bands, 1,017,575 mosaic tiles,
4,070,300 Cloud-Optimized GeoTIFFs, 607.5 TB. Open over plain HTTPS, with no key,
no account and no request quota.

This is a mirror of the
[Copernicus Sentinel-2 Global Mosaics](https://documentation.dataspace.copernicus.eu/Data/SentinelMissions/Sentinel2.html)
(product `S2MSI_L3__MCQ`), which the European Space Agency produces and the
Copernicus Data Space Ecosystem publishes. Upstream the product sits behind a
CDSE account. Here it is anonymous.
[Taylor Geospatial Engine](https://tgengine.org) hosts this copy on
[Source Cooperative](https://source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics).

## What the data is

A quarterly mosaic is not a scene and not an average. For each pixel and each
band, the upstream algorithm takes every Sentinel-2 Level-2A surface reflectance
observation in the three-month window, drops the ones the scene classification
layer marks as cloud, and outputs the **first quartile** of the values that
remain. Where no valid observation exists for the quarter, the pixel is left
empty.

The low percentile is the point. Cloud-classification errors are one-sided: a
missed cloud or a haze edge makes a pixel brighter, never darker. A mean would
carry that error into the output; the 25th percentile mostly discards it. The
result is a basemap that stays consistent across a scene boundary and across a
season, which is what makes it usable over large, persistently cloudy areas.
The upstream
[mosaic algorithm note](https://documentation.dataspace.copernicus.eu/Data/Others/Sentinel2_Mosaic_Algorithm.html)
is the authority on this.

Values are `int16`, scaled by 10000. **Multiply by 0.0001 to get surface
reflectance.** The scale factor is not written into the GeoTIFF, so a reader
that does not apply it gets numbers around 1000 where it expected 0.1. Nodata is
`-32768`.

| Band | Name | Centre | Bandwidth | Resolution |
|---|---|---|---|---|
| `B02` | Blue | 0.493 µm | 0.065 µm | 10 m |
| `B03` | Green | 0.560 µm | 0.035 µm | 10 m |
| `B04` | Red | 0.665 µm | 0.031 µm | 10 m |
| `B08` | NIR | 0.833 µm | 0.115 µm | 10 m |

Each tile is 10008 × 10008 pixels, about 100 km square, projected in the UTM
zone of its MGRS grid cell. Every file is a valid COG: DEFLATE with a horizontal
predictor, 1024 × 1024 internal tiles, and five levels of overview down to
313 × 313.

## Coverage

All 36 quarters are complete. The figures below come from the transfer's own
`_status/{year}/{Qn}/complete.json` markers, read on 2026-09-28.

| Years | Quarters | Mosaic tiles | COG files | Size |
|---|---|---|---|---|
| 2017–2025 | 36 | 1,017,575 | 4,070,300 | 607.5 TB |

A quarter holds between 25,172 tiles (2017 Q3) and 30,668 (2019 Q1). The count
grows over the record because the mosaic footprint expanded, not because
anything is missing from the early years.

**2026 is in the bucket but not yet in this catalog.** Two prefixes sit outside
the 36 quarters described here, and they are in different states:

- **`2026/Q2/` finished transferring on 2026-09-28 at 14:44 UTC**, after this
  catalog's collections were written. It has a completion marker and a
  manifest: 115,572 objects across 28,893 tiles, 17.25 TB. The bytes are
  readable. What is missing is the catalog work, because its tiles are built
  differently from every other quarter (see below) and that difference has to
  be described before the quarter is folded in.
- **`2026/Q1/`** holds 799 objects (89.7 GB) with no completion marker and no
  manifest. It is an unfinished transfer. Do not read it as data.

2026 Q2 tiles use **256 × 256 internal blocks** where every quarter from 2017
to 2025 uses 1024 × 1024. Everything else matches: same COG layout, same
DEFLATE with a horizontal predictor, same 10008 × 10008 size, same `int16` and
`-32768` nodata, same five overview levels. Smaller blocks mean a windowed read
fetches more and smaller byte ranges for the same pixels, so code tuned against
the 2017–2025 tiles will behave differently here without failing.

Until 2026 Q2 is folded in, treat the record as 2017 Q1 through 2025 Q4. A glob
over `manifest/manifest_*.parquet` now matches 37 files, not 36, so filter on
`year <= 2025` when you mean the quarters this catalog describes.

## What else is in the bucket

Three prefixes beside the imagery are transfer bookkeeping, not data:

- **`_benchmarks/`** holds 3,224 fixed-size `.bin` files from throughput tests,
  481.1 GB in all, counted on 2026-09-28. They are not imagery, they carry no
  georeferencing, and they are named after real tiles, so a recursive listing or
  a `**/*` glob will pull them in and a naive size total will count them as
  data. Exclude the prefix.
- **`_status/`** holds one `complete.json` per finished quarter plus per-worker
  progress files. `complete.json` is the authority on whether a quarter is done,
  and it records the object count, the byte total and the manifest checksum.
- **`manifest/`** holds one Parquet per quarter, `manifest_{year}_{Qn}.parquet`,
  listing every object transferred with its source key, destination key and
  size. Until the item index publishes, this is the fastest way to ask what
  exists. See [`mosaics/AGENTS.md`](mosaics/AGENTS.md) for worked queries.

## Access

Keys are fully constructible. Nothing has to be looked up to read a tile:

```
{year}/{Qn}/{MGRS}_{i}_{j}/{band}.tif
```

So the red band of tile `31UFU_0_0` for 2024 Q2 is:

```
https://data.source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/2024/Q2/31UFU_0_0/B04.tif
```

The same objects are readable over S3 without credentials. Source Cooperative is
a data proxy, so the bucket is the account name and the product slug is the key
prefix:

```
s3://tge-labs/sentinel-2-quarterly-cloudless-mosaics/
```

Point your client at the proxy endpoint `https://data.source.coop` and use
path-style addressing. In DuckDB that is `SET s3_endpoint = 'data.source.coop';`
and `SET s3_url_style = 'path';`, with no credentials and no region.

> Source Cooperative used a different S3 form before its 0.3 release:
> `s3://us-west-2.opendata.source.coop/tge-labs/...`, with the account as a
> prefix inside a region-named bucket. That form still resolves today, but it is
> the old addressing. Prefer the one above.

Read a window out of one tile without downloading it:

```bash
gdal_translate -srcwin 4000 4000 1024 1024 \
  /vsicurl/https://data.source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/2024/Q2/31UFU_0_0/B04.tif \
  window.tif
```

Ask what exists, from the transfer manifests, without listing the bucket:

```sql
INSTALL httpfs; LOAD httpfs;
SET http_timeout = 600000;

SELECT count(*) AS cogs, count(DISTINCT item_id) AS tiles
FROM read_parquet('https://data.source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/manifest/manifest_2024_Q2.parquet');
-- 113088 cogs, 28272 tiles
```

[`AGENTS.md`](AGENTS.md) carries the rest, including the gotchas that make a
query silently fail or silently lie. Read it before writing anything larger than
the above.

## License

The imagery carries the
[Legal Notice on the Use of Copernicus Sentinel Data and Service Information](https://sentinels.copernicus.eu/documents/247904/690755/Sentinel_Data_Legal_Notice):
free, full and open access for any use, including commercial use and
redistribution. There is no SPDX identifier for those terms, so the collections
declare `license: "other"` with a `rel: license` link to the notice itself.

Attribute it as:

> Copernicus Sentinel data 2017–2025

Where you have modified the data, say so. The notice asks that derived products
make clear they contain modified Copernicus Sentinel data, so that a user does
not read your output as an ESA product.

The mirroring, the catalog metadata and the derived coverage statistics are the
work of Taylor Geospatial Engine and add no restriction of their own.

## Provenance

The European Space Agency produces the Sentinel-2 Global Mosaics. Sinergise
Solutions runs the mosaic production, and CloudFerro operates the Copernicus
Data Space Ecosystem infrastructure this mirror copied from. The upstream
collection is
[`sentinel-2-global-mosaics`](https://stac.dataspace.copernicus.eu/v1/collections/sentinel-2-global-mosaics)
in the CDSE STAC API, and each collection here carries a `canonical` link to it.

The copy is byte-identical for the four bands it carries. It does **not** carry
the upstream `observations.tif` band, the `userdata.json` metadata, or the zipped
`Product` asset, so this catalog describes four assets per tile where the
upstream collection describes seven. Item ids are preserved unchanged, so a tile
here can be matched to its upstream record by id.

Upstream reprocesses old quarters. A quarter that completed here is a snapshot
of the upstream product at the time it was copied, not a permanent record. Sync
checks compare manifests rather than assuming a finished quarter stays fixed.

## Collections

- [`mosaics`](mosaics/collection.json) — the imagery: bands, tile geometry, the
  item index, and the per-quarter partitions.
  ([README](mosaics/README.md), [agent guide](mosaics/AGENTS.md))
- [`coverage`](coverage/collection.json) — tile footprints and per-tile,
  per-quarter statistics: what exists where, and how full it is.
  ([README](coverage/README.md), [agent guide](coverage/AGENTS.md))
