# AGENTS.md — coverage

Guidance for AI agents and automated clients querying the coverage collection.

**One rule survives every edit to this file.** Every claim here is either quoted
from a source or measured from the data. If you cannot point at where a fact
came from, it does not belong in this file.

Read the [catalog agent guide](../AGENTS.md) first for the two DuckDB settings
these queries need and the manifest schema they fall back on.

## Published coverage

Measured against the public files on 2026-10-20: `tiles.parquet` has 912,320
rows, 34,423 distinct `subtile` values, and 32 quarters from `2018.Q1` through
`2025.Q4`. There are no duplicate `(subtile, quarter)` pairs. `valid_fraction`
is measured for 64,000 rows and NULL for the rest.

The PMTiles metadata reports 34,423 features in the `footprints` layer, with
`subtile`, `mgrs_tile`, `quarters_present`, `first_quarter` and `last_quarter`
properties. Join on the string `subtile` property. Quarter counts range from
1 to 32. The collection records measured sizes and checksums for both files.

**One tile's published coverage history.** Run against the public file on
2026-10-20; returns 32 rows for this tile, `2018.Q1` through `2025.Q4`.

```sql
INSTALL httpfs; LOAD httpfs;
SET http_timeout = 600000;
SET http_retries = 5;

SELECT quarter, bytes_total, valid_fraction
FROM read_parquet('https://data.source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/coverage/tiles.parquet')
WHERE subtile = '31UFU_0_0'
ORDER BY quarter;
```

The 2017 imagery exists but is absent from these coverage derivatives. Use
the manifests below when the question includes 2017.

## Manifest recipes, including 2017

**Which quarters cover this tile.** Query the coverage table for 2018–2025.
Including 2017 costs a read of all 36 manifests, about 100 MB and
roughly several minutes on a domestic connection. Run on 2026-09-28; returns
36 rows for this tile.

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

**How full a tile is, roughly, from its size.** Where `valid_fraction` is NULL,
compressed size is an available proxy. Within one quarter, tiles that are
mostly nodata compress far smaller than full ones.

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

This ranks the emptiest tiles of the quarter. Treat it as a ranking, not a
measurement: compression ratio depends on scene content as well as on nodata,
so a uniform desert tile also compresses small. Use `valid_fraction` where
it has been measured.

**Tile counts per quarter.** Cross-check against
`_status/{year}/{Qn}/complete.json`, which records the object count
independently. For 2024 Q2 both give 28,272 tiles and 113,088 objects.

Needs the same `s3_endpoint` and `s3_url_style` settings as the first query
above; an `s3://` read fails without them.

```sql
SELECT year, quarter, count(DISTINCT item_id) AS tiles
FROM read_parquet('s3://tge-labs/sentinel-2-quarterly-cloudless-mosaics/manifest/manifest_2024_Q[12].parquet')
GROUP BY 1, 2 ORDER BY 1, 2;
```

Returns `2024 Q1 → 29528`, `2024 Q2 → 28272`.

## Join keys

| From | Column | To | Column | Unique? |
|---|---|---|---|---|
| `coverage` | `subtile` | `footprints.pmtiles` | `subtile` property | many table rows per footprint |
| `coverage` | `subtile` + `quarter` | itself | — | the table's unique key |
| `coverage` | `subtile` | `mosaics` index | `_subtile` | not unique on either side |
| `coverage` | `mgrs_tile` | MGRS grid | cell id | not unique |

`subtile` is `31UFU_0_0`. `mgrs_tile` is `31UFU`. In 2024 Q2 there are 28,272
mosaic tiles across 28,204 MGRS cells, so 68 cells carry more than one tile.
Joining on `mgrs_tile` when you meant `subtile` duplicates rows and no error
tells you.

## Quirks that produce silently wrong answers

**`valid_fraction` is an estimate, and NULL is not zero.** It is measured at
313 × 313, one thousandth of the tile's pixel count, so a thin feature can round
away. NULL means the overview has not been read, not that the tile is empty.
Filter `valid_fraction IS NOT NULL` before aggregating, or your averages are
computed over whichever tiles happened to be processed.

**Size is not coverage.** `bytes_total` reflects compressibility, which is
driven by scene content as much as by how much of the tile is real. Use it to
rank, not to quantify.

**Check the table's temporal extent before interpreting a missing row.** The
published table covers 2018–2025; it cannot answer presence questions for 2017.
Within its covered quarters, a missing row is different
from a tile that exists and is mostly nodata, which does have a row, with a low
`valid_fraction`. Both look like "no data" on a map and they mean different
things.

**Coverage counts stop at 32.** The imagery record this catalog describes is
2017 Q1 through 2025 Q4, but the coverage table starts in 2018. A tile present
in every covered quarter has 32 rows. `2026/Q2/` finished transferring on
2026-09-28 and has its own completion marker and manifest, but it is not in
this catalog: its tiles use 256 × 256
internal blocks rather than 1024 × 1024, and that has to be described before the
quarter is folded in. A glob over `manifest/manifest_*.parquet` therefore
matches 37 files; filter on `year <= 2025` to match this catalog. A tile's
absence from 2026 here says nothing about the upstream product.

**The geometry is the mosaic grid, not the MGRS grid.** Mosaic tiles align to
MGRS cells but subdivide some of them. Do not substitute a generic MGRS grid
polygon for a footprint from this collection: the cells that carry more than one
mosaic tile will not line up.
