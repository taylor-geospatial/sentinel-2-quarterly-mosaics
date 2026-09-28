# Tile footprints and per-quarter coverage

What exists where, and how full it is. One row per mosaic tile per quarter, and
one polygon per mosaic tile.

Start at the [catalog README](../README.md) for the mirror story and the licence.
This page is about the derived statistics.

## Why this collection exists

The [`mosaics`](../mosaics/collection.json) collection is a million tiles across
36 quarters. Two questions come up before any real work, and neither should cost
a million-object listing or 36 raster opens:

- **Which quarters cover this place?** Not every tile exists in every quarter,
  and the mosaic footprint grew over the record.
- **Is this tile actually full?** A tile that was persistently cloudy for three
  months is mostly nodata. It exists, it is a valid COG, and it is nearly
  useless. Nothing in the filename says so.

`tiles.parquet` answers both from a single range read.

## The table

One row per (mosaic tile, quarter), sorted by tile and then quarter, so one
tile's whole 36-quarter history is contiguous. The full column list with types
is in [`collection.json`](collection.json) under `table:columns`; the parts worth
explaining here:

- **`subtile`** is the mosaic tile, `31UFU_0_0`. **`mgrs_tile`** is the grid
  cell, `31UFU`. They are not the same, and one cell can carry more than one
  mosaic tile. Join on `subtile`.
- **`bytes_total`** and the four per-band size columns come from the transfer
  manifests, which record the size of every object that landed. Size is a good
  cheap proxy for content: a nearly empty tile compresses to a fraction of a
  full one.
- **`valid_fraction`** is the real measure, from 0.0 to 1.0. It is counted on
  the smallest internal overview of the red band, 313 × 313 pixels, by counting
  pixels that are not `-32768`. That is a 0.1 megapixel read per tile instead of
  100 megapixels, which is the only reason it is affordable across a million
  tiles. It is an estimate at that resolution, not an exact count over the full
  raster, and it is NULL until the overview has been read.

## The footprints

`footprints.pmtiles` carries one polygon per mosaic tile on the true mosaic grid,
not a bounding envelope, with the count of quarters present on each feature.

The geometry is fixed. A client joins the coverage table onto it by `subtile`,
which means recomputing the statistics never rebuilds the tileset. Tiles that
cross the antimeridian are split at 180° rather than wrapped.

## Not published yet

**Neither data file exists.** `table:row_count` is 0, and both assets are
declared without `file:size` and `file:checksum` because there are no bytes to
measure. The schema in `collection.json` is the contract the generator writes
to, not a description of a file you can read today.

This is deliberate. The alternative — writing plausible sizes and checksums for
files that do not exist — produces a catalog that fails validation in a way that
looks like corruption, and there is no way for a reader to tell the difference.

Until it lands, the per-quarter transfer manifests at
`manifest/manifest_{year}_{Qn}.parquet` answer the presence and size questions.
[`AGENTS.md`](AGENTS.md) shows how, and says plainly which queries do not work
yet.

## License

Derived from the Copernicus Sentinel-2 Global Mosaics, under the
[Legal Notice on the Use of Copernicus Sentinel Data and Service Information](https://sentinels.copernicus.eu/documents/247904/690755/Sentinel_Data_Legal_Notice).
Contains modified Copernicus Sentinel data. Attribute the underlying imagery as
"Copernicus Sentinel data 2017–2025". The aggregation itself is the work of
Taylor Geospatial Engine and adds no restriction.
