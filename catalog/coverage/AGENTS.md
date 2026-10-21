# AGENTS.md — coverage

These instructions apply to AI agents and automated clients querying the coverage collection.
Every claim in this guide must be quoted from a source or measured from the data.
Do not add a claim without its source or document a query without running it against the published files.

Read the [catalog agent guide](../AGENTS.md) for DuckDB settings and manifest columns.

## Published coverage

Measurements of the public `tiles.parquet` on 2026-10-20 found 912,320 rows, 34,423 distinct `subtile` values, and 32 quarters from `2018.Q1` through `2025.Q4`.
There were no duplicate `(subtile, quarter)` pairs.
`valid_fraction` was measured for 64,000 rows and NULL for the rest.

The PMTiles metadata reports 34,423 features in the `footprints` layer.
Each feature has `subtile`, `mgrs_tile`, `quarters_present`, `first_quarter`, and `last_quarter` properties.
Join on the string `subtile` property.
Quarter counts range from 1 to 32, and the collection records measured sizes and checksums for both files.

The following query ran against the public file on 2026-10-20 and returned 32 rows for `2018.Q1` through `2025.Q4`:

```sql
INSTALL httpfs; LOAD httpfs;
SET http_timeout = 600000;
SET http_retries = 5;

SELECT quarter, bytes_total, valid_fraction
FROM read_parquet('https://data.source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/coverage/tiles.parquet')
WHERE subtile = '31UFU_0_0'
ORDER BY quarter;
```

The 2017 imagery is absent from these coverage derivatives.
Use the manifests when the question includes 2017.

## Manifest queries

### Tile history including 2017

The coverage table provides the 2018–2025 history.
Including 2017 requires reading the 36 manifests, about 100 MB, which took several minutes on a domestic connection.
This query returned 36 rows on 2026-09-28:

```sql
INSTALL httpfs; LOAD httpfs;
SET s3_endpoint = 'data.source.coop';
SET s3_url_style = 'path';
SET http_timeout = 600000;
SET http_retries = 5;

SELECT year, quarter, sum(size_bytes) AS bytes_total
FROM read_parquet('s3://tge-labs/sentinel-2-quarterly-cloudless-mosaics/manifest/manifest_*.parquet')
WHERE item_id LIKE '%_31UFU_0_0'
  AND year <= 2025   -- 2026 Q2 landed after this catalog was written
GROUP BY 1, 2
ORDER BY 1, 2;
```

### Compressed tile size

Where `valid_fraction` is NULL, compressed size can help identify tiles with little valid data.
Within a quarter, mostly nodata tiles compress to smaller files than full tiles.

```sql
INSTALL httpfs; LOAD httpfs;
SET http_timeout = 600000;

SELECT regexp_extract(item_id, 'Q[1-4]_(.*)$', 1) AS subtile,
       sum(size_bytes) AS bytes_total
FROM read_parquet('https://data.source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/manifest/manifest_2024_Q2.parquet')
GROUP BY 1
ORDER BY bytes_total
LIMIT 20;
```

The query ranks tiles by compressed size.
Compression also depends on scene content, so a uniform desert tile can produce a small file.
Use measured `valid_fraction` values to estimate coverage.

### Tile counts per quarter

Compare these counts with `_status/{year}/{Qn}/complete.json`, which records object counts independently.
For 2024 Q2, both give 28,272 tiles and 113,088 objects.
The query requires the same `s3_endpoint` and `s3_url_style` settings as the tile-history query.

```sql
SELECT year, quarter, count(DISTINCT item_id) AS tiles
FROM read_parquet('s3://tge-labs/sentinel-2-quarterly-cloudless-mosaics/manifest/manifest_2024_Q[12].parquet')
GROUP BY 1, 2 ORDER BY 1, 2;
```

The results were `2024 Q1 → 29528` and `2024 Q2 → 28272`.

## Join keys

| From | Column | To | Column | Unique? |
|---|---|---|---|---|
| `coverage` | `subtile` | `footprints.pmtiles` | `subtile` property | many table rows per footprint |
| `coverage` | `subtile` + `quarter` | itself | — | the table's unique key |
| `coverage` | `subtile` | `mosaics` index | `_subtile` | not unique on either side |
| `coverage` | `mgrs_tile` | MGRS grid | cell id | not unique |

`subtile` identifies a mosaic tile, such as `31UFU_0_0`, and `mgrs_tile` identifies its MGRS cell, such as `31UFU`.
The 2024 Q2 measurement counted 28,272 mosaic tiles in 28,204 MGRS cells and reported 68 cells with more than one tile.
Joining on `mgrs_tile` when mosaic tiles are intended can duplicate rows.

## Interpretation limits

### Valid fraction

`valid_fraction` is estimated at 313 × 313 pixels, about one thousandth of a tile's pixel count.
Thin features can disappear at that resolution.
NULL means the overview has not been read.
Use `valid_fraction IS NOT NULL` when selecting measured rows for aggregation, and account for which tiles have been processed.

`bytes_total` also reflects scene compressibility.
Use it to rank tiles, not to quantify the fraction of valid pixels.

### Temporal coverage

The published table covers 2018–2025 and cannot establish tile presence in 2017.
Within the covered quarters, a missing row means something different from a present tile with little valid data.
The latter has a row with a low `valid_fraction` when measured.
Both can appear empty on a map.

A tile present in all covered quarters has 32 rows, even though the catalog's imagery record starts in 2017.
`2026/Q2/` finished transferring on 2026-09-28 and has a completion marker and manifest, but remains outside this catalog.
Its 256 × 256 internal blocks differ from the earlier 1024 × 1024 blocks and need to be described before the quarter is included.
The additional manifest brings the documented glob count to 37 files.
Filter on `year <= 2025` to match the catalog's imagery extent.
An absent 2026 coverage row says nothing about the upstream product.

### Footprints

The footprints follow mosaic tiles, which align to MGRS cells but subdivide some of them.
Do not substitute a generic MGRS grid polygon for a mosaic footprint where a cell contains multiple mosaic tiles.
