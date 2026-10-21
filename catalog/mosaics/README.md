# Quarterly cloudless mosaic tiles

This collection describes the four mirrored bands of the Copernicus Sentinel-2 Global Mosaics for 36 quarters, from 2017 Q1 through 2025 Q4.
It contains 1,017,575 mosaic tiles, 4,070,300 Cloud-Optimized GeoTIFFs, and 607.5 TB.
See the [catalog README](../README.md) for provenance, access, licensing, and coverage limits.

## Tile layout

A mosaic tile is 10008 × 10008 pixels at 10 m, about 100 km square, projected in its MGRS cell's UTM zone.
Each tile directory contains a single-band COG for B02, B03, B04, and B08:

```
2024/Q2/31UFU_0_0/B02.tif
2024/Q2/31UFU_0_0/B03.tif
2024/Q2/31UFU_0_0/B04.tif
2024/Q2/31UFU_0_0/B08.tif
```

The following properties were measured with `gdalinfo` against the published file on 2026-09-28:

| Property | Value |
|---|---|
| Layout | `COG` |
| Size | 10008 × 10008 |
| Pixel size | 10 m |
| Data type | `Int16` |
| NoData | `-32768` |
| Compression | `DEFLATE`, `PREDICTOR=2` |
| Block size | 1024 × 1024 |
| Overviews | 5004, 2502, 1251, 626, 313 |
| CRS | the tile's own UTM zone, e.g. `EPSG:32631` |

The smallest of the five overview levels provides a 313 × 313 whole-tile preview.

## Reflectance values

Pixels are `int16` values scaled by 10000.
Multiply by 0.0001 to obtain surface reflectance.
The GeoTIFF does not record the scale, so a stored value of 1000 corresponds to reflectance of 0.1.

Nodata is `-32768`, and zero is a valid reflectance value.
A `> 0` mask discards valid dark pixels, including water, shadows, and burn scars.

## Bands

| Asset | Band | Common name | Center | Bandwidth |
|---|---|---|---|---|
| `B02` | Band 2 | blue | 0.493 µm | 0.065 µm |
| `B03` | Band 3 | green | 0.560 µm | 0.035 µm |
| `B04` | Band 4 | red | 0.665 µm | 0.031 µm |
| `B08` | Band 8 | NIR | 0.833 µm | 0.115 µm |

Red, green, and blue form a natural-color composite.
Compute NDVI from `B08` and `B04` as `(B08 - B04) / (B08 + B04)`.
The common scale factor cancels in the ratio, so the expression can use either scaled values or raw integers.

### Spectral bandwidths

The center wavelengths follow the upstream CDSE collection, while the bandwidths use ESA mission values.
The following CDSE values were read from `Sentinel-2_mosaic_2026_Q2_60XWF_1_0` on 2026-09-28:

| Band | CDSE publishes | ESA mission value | This catalog |
|---|---|---|---|
| B02 | 0.267 µm | 0.065 µm | 0.065 µm |
| B03 | 0.291 µm | 0.035 µm | 0.035 µm |
| B04 | 0.342 µm | 0.031 µm | 0.031 µm |
| B08 | 0.454 µm | 0.115 µm | 0.115 µm |

Each upstream value is close to 0.54 times its center wavelength, suggesting a generation error.
A red band 0.342 µm wide would span green through near infrared and overlap the other bands in this set.
The catalog uses the ESA mission bandwidths to correct that metadata.
For exact instrument response, use the ESA Sentinel-2 spectral response function tables.

## Omitted assets

The upstream product has seven assets per tile, and this mirror contains four.
It omits the following assets:

- `observations.tif` records the number of valid observations contributing to each composite pixel.
- `userdata.json` contains upstream per-tile metadata.
- `Product` contains the zipped whole-product archive.

The collection's `item_assets` describes only the four mirrored bands.
Observation counts require the original product from CDSE.

## Item index

The public collection, checked on 2026-10-20, registers 18 partitions with 521,378 item-index rows.
Its item links cover 2021 Q2 through 2025 Q4 except 2023 Q2.
Use the transfer manifests for quarters outside that registered coverage.

Each quarter's GeoParquet is stored at `quarter=YYYY.Qn/items.parquet` and sorted by `_tile`, `datetime`, then `_subtile`.
The `_subtile` field orders mosaic tiles sharing an MGRS cell, since `datetime` is constant within a quarter.
The column definitions appear under `table:columns` in [collection.json](collection.json) and are generated from `tools/schema.py` in the source repository.
The writer uses the same definitions when casting columns.

The per-quarter transfer manifests use `manifest/manifest_{year}_{Qn}.parquet`.
The [agent guide](AGENTS.md) provides queries tested against those files.

## Coverage by quarter

All 36 quarters are complete according to `_status/{year}/{Qn}/complete.json` markers read on 2026-09-28.
Counts range from 25,172 tiles (2017 Q3) to 30,668 (2019 Q1), reflecting expansion of the mosaic footprint.

The collection excludes 2026.
`2026/Q2/` completed on 2026-09-28 at 14:44 UTC, after the collection was written.
Its completion marker and manifest record 115,572 objects across 28,893 tiles, totaling 17.25 TB.
The files are readable, but the catalog still needs metadata describing their different block size.

The 2026 Q2 file `2026/Q2/31UFU_0_0/B04.tif` uses 256 × 256 internal blocks, compared with 1024 × 1024 for 2017–2025.
Its COG layout, DEFLATE compression and predictor, dimensions, type, nodata value, and overview levels are unchanged.
Windowed reads request more, smaller byte ranges for the same pixels.

`2026/Q1/` held 799 objects without a completion marker or manifest when inspected.
Do not use that unfinished transfer as data.

The additional `manifest_2026_Q2.parquet` brings the documented manifest glob count to 37 files.
Filter on `year <= 2025` to select the 36 quarters described by this collection.
