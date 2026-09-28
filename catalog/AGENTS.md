# AGENTS.md — Sentinel-2 Quarterly Cloudless Mosaics

Guidance for AI agents and automated clients working with this catalog.

**One rule survives every edit to this file.** Every claim here is either quoted
from a source or measured from the data. If you cannot point at where a fact
came from, it does not belong in this file. An agent acting on an invented join
key or an invented column name produces a confident wrong answer, and nothing
downstream catches it.

## What this catalog holds

Public root:
`https://data.source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/catalog.json`

Quarterly cloud-free Sentinel-2 composites at 10 m, 36 quarters from 2017 Q1
through 2025 Q4. Two collections:

- **`mosaics`** — the imagery. 1,017,575 mosaic tiles, 4,070,300 single-band
  Cloud-Optimized GeoTIFFs, 607.5 TB. Read
  [`mosaics/AGENTS.md`](mosaics/AGENTS.md) before querying it.
- **`coverage`** — per-tile, per-quarter statistics and tile footprints. Read
  [`coverage/AGENTS.md`](coverage/AGENTS.md).

This is a **mirror**. The European Space Agency produces the Sentinel-2 Global
Mosaics and the Copernicus Data Space Ecosystem publishes them. Nothing is
filtered, reclassified or recomputed here. Do not describe this catalog as the
source.

## What exists today, and what does not

The imagery is complete and readable now. The **item index and the coverage
tables are not published yet**: `mosaics` carries `table:row_count: 0` and
`partition:file_count: 0`, and the `coverage` data assets are declared without
`file:size` or `file:checksum` because the bytes do not exist.

Until they land, the authority on what exists is the per-quarter transfer
manifests, which **do** exist today:

```
https://data.source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/manifest/manifest_{year}_{Qn}.parquet
```

Thirty-six of them, one per finished quarter. Every recipe below runs against
those. A recipe that needs `items.parquet` is marked as unavailable, not
written speculatively.

## Keys are constructible

No index lookup is needed to read a tile. The key is:

```
{year}/{Qn}/{MGRS}_{i}_{j}/{band}.tif
```

with `band` one of `B02`, `B03`, `B04`, `B08`. The STAC item id for the same
tile is `Sentinel-2_mosaic_{YYYY}_Q{n}_{MGRS}_{i}_{j}`, so the id and the key
convert to each other with string operations alone.

Verified on 2026-09-28, `HTTP/2 200` with `accept-ranges: bytes` and
`access-control-allow-origin: *`:

```
https://data.source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/2024/Q2/31UFU_0_0/B04.tif
```

## Three settings, or your queries fail

All three were hit and fixed while writing this file. None is optional.

**1. `SET s3_endpoint = 'data.source.coop'`.** Source Cooperative is a data
proxy, not a plain S3 bucket. The bucket is the account name, `tge-labs`, and
the product slug is the key prefix, so an `s3://` read has to be pointed at the
proxy host. No credentials are needed: the data is public and anonymous reads
work.

**2. `SET s3_url_style = 'path'` for every `s3://` read.** The proxy does not
serve virtual-host-style subdomains, so without this DuckDB invents a hostname
that does not exist:

```
IO Error: Could not resolve hostname error for HTTP HEAD to
'https://tge-labs.data.source.coop/sentinel-2-quarterly-cloudless-mosaics/manifest/manifest_2024_Q2.parquet'
```

`SET s3_region` is **not** needed against the proxy. Measured on 2026-09-28: the
same query succeeds with the region unset and fails with the URL style unset.

**3. Raise `http_timeout`.** Each manifest is a single Parquet row group of
about 2.8 MB, so any query reads the whole file; there is no column or row-group
pruning to fall back on. On a slow link the default timeout aborts mid-read and
DuckDB reports it as corruption rather than as a timeout:

```
Invalid Error: Out of buffer
Invalid Error: TProtocolException: Invalid data
```

Neither message means the file is damaged. `SET http_timeout = 600000;` and
`SET http_retries = 5;` fix it.

## Worked queries

Every query below was run against the published files on 2026-09-28, and the
row counts shown are what came back.

**One quarter, over `https://`.** DuckDB reads a single named file over HTTPS
without credentials.

```sql
INSTALL httpfs; LOAD httpfs;
SET http_timeout = 600000;
SET http_retries = 5;

SELECT count(*) AS n_cogs,
       count(DISTINCT item_id) AS n_tiles,
       round(sum(size_bytes) / 1e12, 2) AS tb
FROM read_parquet('https://data.source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/manifest/manifest_2024_Q2.parquet');
```

Returns `113088 | 28272 | 16.65`, which matches that quarter's
`_status/2024/Q2/complete.json` exactly.

**More than one quarter: use `s3://`.** DuckDB cannot expand a glob over plain
`https://`, because it has no way to list. The anonymous S3 door can.

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

Returns `2024 Q1 → 29528` and `2024 Q2 → 28272`, matching both completion
markers.

**One tile through all 36 quarters, with the URL to read.** This is the query
the catalog exists to make cheap.

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

Returns 36 rows, one per quarter, 2017 Q1 through 2025 Q4. It took about
several minutes on a domestic connection, because the glob pulls all 36 manifests
(roughly 100 MB) and none of them can be pruned. Cache the result rather than
running it twice. Once `coverage/tiles.parquet` publishes, that collection
answers the same question from one range read.

## The manifest schema

Measured with `DESCRIBE` against
`manifest/manifest_2024_Q2.parquet` on 2026-09-28.

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

A public URL is
`'https://data.source.coop/' || destination_bucket || '/' || destination_key`.
There is **no geometry column**: the manifests are a transfer record, not a
spatial index.

## Join keys

| From | Column | To | Column | Unique? |
|---|---|---|---|---|
| manifests | `item_id` | `mosaics` index | `id` | unique per quarter |
| `mosaics` index | `_subtile` | `coverage` | `subtile` | not unique: one row per quarter |
| `coverage` | `subtile` | `footprints.pmtiles` | feature id | unique |
| `mosaics` index | `_tile` | MGRS grid | cell id | not unique |

**`_tile` and `_subtile` are different things and mixing them loses rows.**
`_tile` is the MGRS cell, `31UFU`. `_subtile` is the mosaic tile,
`31UFU_0_0`. One MGRS cell can carry more than one mosaic tile: in 2024 Q2,
28,272 mosaic tiles sit in 28,204 distinct MGRS cells, so 68 cells carry more
than one. Join on `_subtile` unless you actually mean the grid cell.

## Quirks that produce silently wrong answers

**The reflectance scale is not in the file.** Pixels are `int16` scaled by
10000. Multiply by 0.0001. GDAL reports no scale factor, so nothing warns you:
you get plausible-looking values around 1000 instead of 0.1 and no error.

**Nodata is `-32768`, not 0.** Zero is a legitimate reflectance value. A mask
built as `value > 0` throws away real dark pixels and keeps nothing it should.
Test against `-32768`.

**There is no single CRS.** Every tile is in the UTM zone of its MGRS cell.
Counting the zones present in 2024 Q2 gives **119 distinct EPSG codes**
(`EPSG:326xx` north, `EPSG:327xx` south). Any operation across more than one
tile has to reproject. Do not assume neighbouring tiles share a projection, and
do not compute an area or a distance without checking which CRS you are in.

**A quarter is a window, not an instant.** `datetime` on an item is the first
day of the quarter. It is not an acquisition time, and no single acquisition
time exists: the pixel is a percentile over three months of observations. Use
`start_datetime` and `end_datetime`.

**`_benchmarks/` is not imagery.** 3,224 fixed-size `.bin` files from throughput
tests, 481.1 GB, named after real tile ids, for example
`_benchmarks/20260922-copy-c128/eodata-transfer-1/Sentinel-2_mosaic_2024_Q2_16QDE_0_0/B02.bin`.
A recursive listing or a `**` glob picks them up and a naive size total counts
them as data. Exclude the prefix.

**A glob over the manifests now matches 37 files, not 36.** `2026/Q2/` finished
transferring on 2026-09-28 at 14:44 UTC, after this catalog's collections were
written, and it has a completion marker and a `manifest_2026_Q2.parquet`:
115,572 objects across 28,893 tiles, 17.25 TB. This catalog still describes 36
quarters, 2017 Q1 through 2025 Q4. So `manifest_*.parquet` silently returns a
quarter the collections do not cover. **Filter on `year <= 2025`** when you mean
the described record. Every glob recipe above does.

**2026 Q2 tiles are built differently.** They use 256 × 256 internal blocks
where 2017 through 2025 use 1024 × 1024, measured with `gdalinfo` on
`2026/Q2/31UFU_0_0/B04.tif` on 2026-09-28. Everything else is identical: COG
layout, DEFLATE with a horizontal predictor, 10008 × 10008, `int16`, nodata
`-32768`, five overviews. Nothing breaks, but a windowed read costs more,
smaller range requests for the same pixels. That difference is why the quarter
is not folded in yet rather than any doubt about the bytes.

**`2026/Q1/` is an unfinished transfer.** 799 objects, no completion marker, no
manifest. Do not read it as data, and do not treat its absence from a manifest
glob as evidence the quarter does not exist upstream.

**Python's `urllib` gets 403 on public objects.** The CDN in front of this
bucket rejects the default `Python-urllib/3.x` User-Agent with
`HTTP Error 403: Forbidden`, including on objects that are unquestionably
public. It is not a permissions problem, the object is not missing, and no
credential will fix it. Measured on 2026-09-28 against
`_status/2024/Q2/complete.json`: the default User-Agent returns 403 and the
identical request with `User-Agent: curl/8.0` returns 200.

```python
import urllib.request
req = urllib.request.Request(url, headers={"User-Agent": "my-tool/1.0"})
body = urllib.request.urlopen(req).read()   # any non-default UA works
```

`curl`, DuckDB's `httpfs`, GDAL's `/vsicurl` and `requests` all send their own
User-Agent and are unaffected, which is why every recipe here works. This only
bites hand-rolled `urllib` code, and it looks exactly like an access failure
when it happens.

**A finished quarter is a snapshot, not a permanent record.** Upstream
reprocesses old quarters, so a quarter copied here can drift from its CDSE
original. Compare manifests rather than assuming immutability.

**The upstream band widths are wrong, and this catalog does not copy them.**
CDSE items publish an `eo:full_width_half_max` for each band that is roughly
0.54 times the centre wavelength, which is not a spectral bandwidth at all.
Read on 2026-09-28 from item `Sentinel-2_mosaic_2026_Q2_60XWF_1_0`:

| Band | CDSE `full_width_half_max` | ESA mission value | Used here |
|---|---|---|---|
| B02 | 0.267 µm | 0.065 µm | 0.065 µm |
| B03 | 0.291 µm | 0.035 µm | 0.035 µm |
| B04 | 0.342 µm | 0.031 µm | 0.031 µm |
| B08 | 0.454 µm | 0.115 µm | 0.115 µm |

A 0.342 µm wide "red" band would run from green into the near infrared. This
catalog publishes the ESA mission bandwidths instead. It is the one field where
this mirror deliberately disagrees with its source. Do not reconcile it back
against CDSE.

## Structure

Assets and structural links resolve relative to the object that carries them.
The root carries an absolute `self` link, which Portolan schema v0.2.0
recommends. Collections do not, so a client tracks its own location.

Human-facing pages live under `source.coop`; machine reads go to
`data.source.coop`. They serve the same objects.
