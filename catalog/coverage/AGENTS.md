# AGENTS.md — coverage

Guidance for AI agents and automated clients querying the coverage collection.

**One rule survives every edit to this file.** Every claim here is either quoted
from a source or measured from the data. If you cannot point at where a fact
came from, it does not belong in this file.

Read the [catalog agent guide](../AGENTS.md) first for the two DuckDB settings
these queries need and the manifest schema they fall back on.

## Status: nothing here is published yet

`tiles.parquet` and `footprints.pmtiles` **do not exist**. The collection
declares them so the contract is public, with no `file:size` and no
`file:checksum`, and `table:row_count` is 0.

You can tell this is the real state rather than a stale number, because a
published table would carry a non-zero `table:row_count` and both assets would
carry a size and a checksum. Check those three fields before trusting any query
in the next section.

Everything under "Available now" runs against the transfer manifests, which do
exist.

## Available now: the same questions, from the manifests

**Which quarters cover this tile.** The answer the coverage table will make
cheap. Today it costs a read of all 36 manifests, about 100 MB and roughly
7 minutes on a domestic connection. Run on 2026-09-28; returns 36 rows for this
tile.

```sql
INSTALL httpfs; LOAD httpfs;
SET s3_region = 'us-west-2';
SET s3_url_style = 'path';
SET http_timeout = 600000;
SET http_retries = 5;

SELECT year, quarter, sum(size_bytes) AS bytes_total
FROM read_parquet('s3://us-west-2.opendata.source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/manifest/manifest_*.parquet')
WHERE item_id LIKE '%_31UFU_0_0'
GROUP BY 1, 2
ORDER BY 1, 2;
```

**How full a tile is, roughly, from its size.** Until `valid_fraction` exists,
compressed size is the available proxy. Within one quarter, tiles that are
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
so a uniform desert tile also compresses small. `valid_fraction` will be the
measurement.

**Tile counts per quarter.** Cross-check against
`_status/{year}/{Qn}/complete.json`, which records the object count
independently. For 2024 Q2 both give 28,272 tiles and 113,088 objects.

```sql
SELECT year, quarter, count(DISTINCT item_id) AS tiles
FROM read_parquet('s3://us-west-2.opendata.source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/manifest/manifest_2024_Q[12].parquet')
GROUP BY 1, 2 ORDER BY 1, 2;
```

Returns `2024 Q1 → 29528`, `2024 Q2 → 28272`.

## Available after the first publish

`tiles.parquet` sorted by `(subtile, quarter)`, so one tile's 36-quarter history
is one range read, and `footprints.pmtiles` joined to it by `subtile`. Do not
write against either yet.

## Join keys

| From | Column | To | Column | Unique? |
|---|---|---|---|---|
| `coverage` | `subtile` | `footprints.pmtiles` | feature id | unique |
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

**A missing row means a missing tile, not an empty one.** If a tile has no row
for a quarter, the mosaic has no tile there for that quarter. That is different
from a tile that exists and is mostly nodata, which does have a row, with a low
`valid_fraction`. Both look like "no data" on a map and they mean different
things.

**Quarter counts stop at 36.** The record is 2017 Q1 through 2025 Q4. A tile
present in every quarter has 36 rows. 2026 is excluded from this catalog, so
nothing here will ever show a 2026 quarter, and a tile's absence from 2026 says
nothing about the upstream product.

**The geometry is the mosaic grid, not the MGRS grid.** Mosaic tiles align to
MGRS cells but subdivide some of them. Do not substitute a generic MGRS grid
polygon for a footprint from this collection: the cells that carry more than one
mosaic tile will not line up.
