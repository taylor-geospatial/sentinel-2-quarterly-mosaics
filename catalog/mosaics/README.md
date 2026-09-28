# Quarterly cloudless mosaic tiles

The imagery. Every tile of the Copernicus Sentinel-2 Global Mosaics for 36
quarters, 2017 Q1 through 2025 Q4: 1,017,575 mosaic tiles, 4,070,300
Cloud-Optimized GeoTIFFs, 607.5 TB.

Start at the [catalog README](../README.md) for the mirror story, the licence and
the bucket-level warnings. This page is about the rasters.

## One tile

A mosaic tile is 10008 × 10008 pixels at 10 m: about 100 km square, one
MGRS grid cell, projected in that cell's UTM zone. Four single-band COGs sit in
one directory:

```
2024/Q2/31UFU_0_0/B02.tif
2024/Q2/31UFU_0_0/B03.tif
2024/Q2/31UFU_0_0/B04.tif
2024/Q2/31UFU_0_0/B08.tif
```

Measured with `gdalinfo` against the published file on 2026-09-28:

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

Five overview levels mean a whole-tile preview costs a 313 × 313 read. That is
what makes a browse layer over a million tiles affordable.

## Reading a value

Pixels are `int16`, scaled by 10000. **Multiply by 0.0001 for surface
reflectance.** The scale is not written into the GeoTIFF, so nothing applies it
for you and nothing warns you. A reader that skips it gets values around 1000
where reflectance should be 0.1.

NoData is `-32768`. Zero is a real reflectance value, so a `> 0` mask silently
discards dark water, shadow and burn scars.

## The bands

| Asset | Band | Common name | Centre | Bandwidth |
|---|---|---|---|---|
| `B02` | Band 2 | blue | 0.493 µm | 0.065 µm |
| `B03` | Band 3 | green | 0.560 µm | 0.035 µm |
| `B04` | Band 4 | red | 0.665 µm | 0.031 µm |
| `B08` | Band 8 | NIR | 0.833 µm | 0.115 µm |

Red, green and blue give a natural-colour composite. `B08` with `B04` gives
NDVI, which is the usual reason this band set exists:
`(B08 - B04) / (B08 + B04)`, computed after applying the scale, or on the raw
integers since the scale cancels in the ratio.

### A note on the bandwidths

The centre wavelengths above match the upstream CDSE collection. The bandwidths
do not.

The `eo:full_width_half_max` values CDSE publishes for this product are wrong.
Read from item `Sentinel-2_mosaic_2026_Q2_60XWF_1_0` on 2026-09-28:

| Band | CDSE publishes | ESA mission value | This catalog |
|---|---|---|---|
| B02 | 0.267 µm | 0.065 µm | 0.065 µm |
| B03 | 0.291 µm | 0.035 µm | 0.035 µm |
| B04 | 0.342 µm | 0.031 µm | 0.031 µm |
| B08 | 0.454 µm | 0.115 µm | 0.115 µm |

Every upstream value is close to 0.54 times its own centre wavelength, which is
the signature of a generation bug rather than a measurement. Taken literally
they are absurd: a red band 0.342 µm wide would span from green through the near
infrared, overlapping every other band in the set.

This catalog publishes the ESA mission bandwidths instead. It is the one field
where this mirror deliberately disagrees with its source, recorded here so that
nobody "corrects" it back.

If you need the instrument response exactly, take it from the ESA Sentinel-2
spectral response function tables rather than from any STAC collection,
including this one.

## What this mirror does not carry

The upstream product has seven assets per tile. This mirror copied four. It does
**not** carry:

- `observations.tif` — the per-pixel count of valid observations that went into
  the composite. If you need to know how well-supported a pixel is, this is the
  band that tells you, and it is not here.
- `userdata.json` — upstream per-tile metadata.
- `Product` — the zipped whole-product archive.

The `item_assets` in `collection.json` therefore lists four assets, not seven. Do
not copy the upstream `item_assets` in: it describes files this mirror does not
have, and a client that trusts it will request 404s.

## The item index

One GeoParquet per quarter, at `quarter=YYYY.Qn/items.parquet`, sorted by
`_tile`, then `datetime`, then `_subtile`, so one tile's 36-quarter history is a
contiguous run. The `_subtile` tiebreak matters because `datetime` is constant
within a quarter file and some MGRS cells hold more than one mosaic tile;
without it those rows would have no defined order.

The columns and their types are in [`collection.json`](collection.json) under
`table:columns`. That array is generated from `tools/schema.py`, the same list
the writer casts to, so the documentation and the file cannot disagree.

**It is not published yet.** `table:row_count` is 0 and `partition:file_count`
is 0 until the first backfill lands. Each quarter gains an `item` link as its
partition is written, so the collection states what it actually holds at any
point in the backfill rather than promising the whole set up front.

Until then, use the per-quarter transfer manifests at
`manifest/manifest_{year}_{Qn}.parquet`. [`AGENTS.md`](AGENTS.md) has worked
queries against them, every one of which was run before it was written down.

## Coverage by quarter

All 36 quarters are complete, per their `_status/{year}/{Qn}/complete.json`
markers read on 2026-09-28. Tile counts run from 25,172 (2017 Q3) to 30,668
(2019 Q1); the growth over the record is the mosaic footprint expanding, not
gaps in the early years.

2026 is **not** in this collection. The `2026/Q1/` and `2026/Q2/` prefixes hold
an unfinished transfer with no completion marker and an unverified tile
structure. They are excluded until they finish and are checked.
