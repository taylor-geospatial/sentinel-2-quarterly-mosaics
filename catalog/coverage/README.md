# Tile footprints and per-quarter coverage

This collection records tile presence and estimates valid-pixel coverage.
It contains one table row per mosaic tile per quarter and one footprint per mosaic tile.
See the [catalog README](../README.md) for provenance and licensing.

The [`mosaics`](../mosaics/collection.json) collection describes a million tiles across 36 quarters.
Its footprint expanded over time, and some tiles contain large nodata areas because of persistent clouds.
The coverage table provides presence and coverage information without opening each raster.

## Table columns

`tiles.parquet` is sorted by tile and quarter.
Each tile's available history is contiguous.
[collection.json](collection.json) lists the columns and types under `table:columns`.

| Column | Meaning |
|---|---|
| `subtile` | Mosaic tile identifier, such as `31UFU_0_0`, used for joins |
| `mgrs_tile` | MGRS cell identifier, such as `31UFU`; a cell can contain multiple mosaic tiles |
| `bytes_total` | Total compressed size from the transfer manifests; four additional columns record per-band sizes |
| `valid_fraction` | Estimated fraction of valid red-band pixels, from 0.0 to 1.0; NULL until measured |

`valid_fraction` counts pixels other than `-32768` in the smallest red-band overview, which is 313 × 313 pixels.
This reads about 0.1 megapixels per tile rather than 100 megapixels, but estimates coverage only at the overview resolution.
Compressed size can help rank nearly empty tiles, although scene content also affects compression.

## Footprints

`footprints.pmtiles` contains one polygon per mosaic tile using its footprint geometry, with a count of quarters present.
The geometry is fixed, and statistics join by `subtile`, so statistics can be recomputed without rebuilding the tileset.
Tiles crossing the antimeridian are split at 180°.

## Published coverage

The following measurements were verified against the public files on 2026-10-20:

| Measure | Value |
|---|---|
| Table rows | 912,320 |
| Distinct mosaic tiles | 34,423 |
| Quarters | 32, from 2018 Q1 through 2025 Q4 |
| Rows with measured `valid_fraction` | 64,000; the rest are NULL |
| `tiles.parquet` | 27,763,154 bytes |
| `footprints.pmtiles` | 13,475,623 bytes; 34,423 features |

The collection records sizes and SHA-256 multihashes measured from those published bytes.
The tileset's `footprints` layer uses a string `subtile` property for joins.

The 2017 imagery exists in the mirror but is absent from these derivatives.
Use the 2017 transfer manifests for presence and sizes.
A missing coverage row outside 2018–2025 does not establish that imagery is missing.
The [agent guide](AGENTS.md) includes a tested coverage query and manifest recipes.

The thumbnail uses the default style to show quarters present per mosaic tile.
It represents tile presence rather than valid-pixel coverage.
Regenerate it from the published table with `tools/make_coverage_thumbnail.py` in the source repository.
The [conformance notes](https://github.com/taylor-geospatial/sentinel-2-quarterly-mosaics/blob/main/docs/conformance.md) give the download and rendering commands.

## License

These derivatives use the Copernicus Sentinel-2 Global Mosaics under the [Legal Notice on the Use of Copernicus Sentinel Data and Service Information](https://sentinels.copernicus.eu/documents/247904/690755/Sentinel_Data_Legal_Notice).
They contain modified Copernicus Sentinel data.
Attribute the underlying imagery as "Copernicus Sentinel data 2017–2025".
Taylor Geospatial Engine's aggregation adds no restrictions.
