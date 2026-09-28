#!/usr/bin/env python3
"""Where a mosaic tile is on the ground, from its id alone.

A mosaic tile id is `{MGRS}_{i}_{j}` -- `31UFU_0_0`, `60XWF_1_0`. This
module turns that string into the tile's CRS, its geotransform and its
WGS84 footprint, with no network call and no tiling-grid file. Every
other tool in `tools/` gets its geometry from here, so the item JSON,
the stac-geoparquet footprint and the coverage polygon cannot disagree.

Why this is derived rather than looked up
-----------------------------------------
The obvious source would be ESA's Sentinel-2 tiling-grid KML, but that is
a ~100 MB download that would have to travel to every machine that builds
a quarter, and it describes the 109,800 m L1C tiles rather than the
100,080 m mosaic tiles. So the rule was measured instead: the origin of
every tile in three complete MGRS bands was read out of the published
COG headers (`33U*` 39 tiles, `54H*` 49 tiles, `19F*` 39 tiles, plus 14
scattered tiles across zones 1, 7, 15, 17, 31, 54 and 60, both
hemispheres) and fitted, then re-checked against a stratified live
sample of one tile per zone-group and band. `grid_fixtures.json` holds
all 340 measured origins -- 55 UTM zones, all 20 latitude bands, both
hemispheres, the antimeridian tiles and every `i`/`j` value that occurs
-- and `python3 tools/mgrs_grid.py` replays them offline. The live check
is `make_items.py --verify N`.

The rule
--------
A tile's CRS is the UTM zone of its MGRS designator: `EPSG:326<zz>` for a
latitude band N..X, `EPSG:327<zz>` for C..M.

Take the MGRS 100 km square the designator names. Let `E` be its easting
(a multiple of 100,000, from the column letter) and `N` the northing of
its *upper* edge (from the row letter, disambiguated by the latitude
band). The tile's `0_0` origin is not that corner: the mosaic tile is
100,080 m across, 80 m wider than the square, and the grid shifts the
origin by a small amount that cycles with a period of three squares:

    ulx = E - 20 * ((3 - (E // 100_000) % 3) % 3)
    uly = N + 20 * ((N // 100_000) % 3)              northern hemisphere
    uly = N + 20 * (((N // 100_000) + 2) % 3)        southern hemisphere

so the correction is 0, 20 or 40 m, eastings always shifting west and
northings always shifting north. The hemispheres differ only in the phase
of the northing cycle, which is what two southern bands were probed to
establish. Nothing here is a rounding: the measured origins hit these
values exactly, to the metre, in all 141 tiles probed.

`i` and `j` then step whole tiles east and south:

    ulx += i * 100_080
    uly -= j * 100_080

Almost every tile is `_0_0`. In 2024 Q2, 28,201 of 28,272 are; the 71
that are not sit where a UTM zone's coverage runs past one tile width --
36 with `i=1` (all in zone 60, against the antimeridian) and 35 with
`j=1`.

Footprints
----------
The footprint is the 100.08 km square reprojected to WGS84. A straight
line in UTM is a curve in lon/lat, so each edge is densified before the
transform; with `EDGE_SEGMENTS` = 10 the remaining error is under two
metres even at the worst latitudes, against roughly 160 m for a bare
four-corner quadrilateral, which at 10 m pixels would be a visible lie.
Measured on tiles in zones 19, 31, 33 and 54: 158.7 m undensified, 6.4 m
at five segments, 1.6 m at ten, 0.4 m at twenty.

Tiles against the antimeridian reproject to a ring whose longitudes wrap.
`footprint()` unwraps the ring into a continuous longitude range first,
then cuts it at +/-180 and translates each piece back into range, giving
the MultiPolygon that CDSE publishes for the same tiles. A GeoJSON bbox
that crosses the antimeridian has `west > east` (RFC 7946 section 5.2),
and `bbox_of()` returns it that way.
"""
from __future__ import annotations

import functools
import math
import re

from pyproj import CRS, Transformer
from shapely.geometry import MultiPolygon, Polygon, box, mapping
from shapely.ops import unary_union

# The mosaic tile: 10,008 pixels of 10 m.
TILE_PX = 10_008
GSD = 10.0
TILE_M = TILE_PX * GSD          # 100,080 m

# Vertices per tile edge before the WGS84 transform. See "Footprints".
EDGE_SEGMENTS = 10

# MGRS column letters, by (zone - 1) % 3; row letters, shifted five places
# on even zones. I and O are absent from both, which is why these are
# written out rather than generated.
_COLUMN_SETS = ("ABCDEFGH", "JKLMNPQR", "STUVWXYZ")
_ROW_LETTERS = "ABCDEFGHJKLMNPQRSTUV"
_BANDS = "CDEFGHJKLMNPQRSTUVWX"

TILE_RE = re.compile(r"^(?P<zone>[0-9]{1,2})(?P<band>[C-HJ-NP-X])"
                     r"(?P<col>[A-HJ-NP-Z])(?P<row>[A-HJ-NP-V])"
                     r"_(?P<i>[0-9]+)_(?P<j>[0-9]+)$")
# The same, without the _i_j: the bare MGRS cell.
CELL_RE = re.compile(r"^(?P<zone>[0-9]{1,2})(?P<band>[C-HJ-NP-X])"
                     r"(?P<col>[A-HJ-NP-Z])(?P<row>[A-HJ-NP-V])$")


class TileError(ValueError):
    """A tile id this module cannot place."""


def parse(tile: str) -> tuple[int, str, str, str, int, int]:
    """`31UFU_0_0` -> (31, 'U', 'F', 'U', 0, 0). Raises TileError."""
    m = TILE_RE.match(tile)
    if not m:
        raise TileError(f"not a mosaic tile id: {tile!r}")
    zone = int(m["zone"])
    if not 1 <= zone <= 60:
        raise TileError(f"{tile}: UTM zone {zone} is out of range")
    return zone, m["band"], m["col"], m["row"], int(m["i"]), int(m["j"])


def cell_of(tile: str) -> str:
    """`31UFU_0_0` -> `31UFU`: the bare MGRS cell the tile sits in."""
    zone, band, col, row, _, _ = parse(tile)
    return f"{zone:02d}{band}{col}{row}"


def epsg_of(zone: int, band: str) -> int:
    """The tile's own UTM CRS. Bands C..M are south of the equator."""
    return (32600 if band >= "N" else 32700) + zone


def band_latitudes(band: str) -> tuple[float, float]:
    """The latitude range of an MGRS band. X is twelve degrees tall, not
    eight: it absorbs the missing Y/Z bands up to the 84 degree limit."""
    i = _BANDS.index(band)
    low = -80.0 + 8.0 * i
    return (low, 84.0) if band == "X" else (low, low + 8.0)


@functools.lru_cache(maxsize=256)
def _to_wgs84(epsg: int) -> Transformer:
    return Transformer.from_crs(CRS.from_epsg(epsg), CRS.from_epsg(4326),
                                always_xy=True)


def square_corner(zone: int, band: str, col: str, row: str) -> tuple[int, int, int]:
    """The MGRS 100 km square: (epsg, easting, northing of its top edge).

    The row letter fixes the northing only modulo 2,000,000 m, so the
    candidates two million metres apart are tested against the latitude
    band and the closest one wins. Candidates are ~18 degrees apart and a
    band is 8 degrees tall, so the choice is never close.
    """
    try:
        column_set = _COLUMN_SETS[(zone - 1) % 3]
        easting = (column_set.index(col) + 1) * 100_000
    except ValueError:
        raise TileError(
            f"zone {zone} has no column letter {col!r} "
            f"(its set is {_COLUMN_SETS[(zone - 1) % 3]})") from None
    shift = 5 if zone % 2 == 0 else 0
    base = ((_ROW_LETTERS.index(row) - shift) % 20) * 100_000

    epsg = epsg_of(zone, band)
    low, high = band_latitudes(band)
    want = (low + high) / 2.0
    transform = _to_wgs84(epsg)
    best, best_err = None, math.inf
    for k in range(6):
        south = base + k * 2_000_000
        if not 0 <= south < 10_000_000:
            continue
        _, lat = transform.transform(easting + 50_000, south + 50_000)
        err = abs(lat - want)
        if err < best_err:
            best, best_err = south, err
    if best is None:
        raise TileError(f"{zone}{band}{col}{row}: no northing candidate")
    return epsg, easting, best + 100_000


def origin(tile: str) -> tuple[int, float, float]:
    """(epsg, ulx, uly) of the tile's top-left pixel corner, in metres."""
    zone, band, col, row, i, j = parse(tile)
    epsg, easting, north = square_corner(zone, band, col, row)
    dx = -20 * ((3 - (easting // 100_000) % 3) % 3)
    phase = north // 100_000
    dy = 20 * (phase % 3 if band >= "N" else (phase + 2) % 3)
    return epsg, float(easting + dx + i * TILE_M), float(north + dy - j * TILE_M)


def transform_of(tile: str) -> list[float]:
    """The tile's `proj:transform`: nine numbers, row-major affine."""
    _, ulx, uly = origin(tile)
    return [GSD, 0.0, ulx, 0.0, -GSD, uly, 0.0, 0.0, 1.0]


def proj_bbox(tile: str) -> list[float]:
    """The tile's bbox in its own CRS: [minx, miny, maxx, maxy], metres."""
    _, ulx, uly = origin(tile)
    return [ulx, uly - TILE_M, ulx + TILE_M, uly]


def _ring(ulx: float, uly: float, segments: int) -> list[tuple[float, float]]:
    """The tile square as a closed ring, each edge cut into `segments`."""
    corners = [(ulx, uly), (ulx + TILE_M, uly),
               (ulx + TILE_M, uly - TILE_M), (ulx, uly - TILE_M)]
    pts = []
    for a, b in zip(corners, corners[1:] + corners[:1]):
        for s in range(segments):
            t = s / segments
            pts.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    pts.append(corners[0])
    return pts


def _unwrap(lons: list[float]) -> list[float]:
    """Make a ring's longitudes continuous by letting them run past 180.

    Every step of more than 180 degrees is a wrap, not a jump across the
    globe: no edge of a 100 km tile is that long. Adding the multiple of
    360 that removes the jump turns a ring that straddles the
    antimeridian into one that simply extends past it, which is a shape
    that can be cut.
    """
    out = [lons[0]]
    for lon in lons[1:]:
        step = lon - out[-1]
        out.append(lon - 360.0 * round(step / 360.0))
    return out


def footprint(tile: str, segments: int = EDGE_SEGMENTS) -> Polygon | MultiPolygon:
    """The tile's WGS84 footprint, split at the antimeridian if it crosses."""
    epsg, ulx, uly = origin(tile)
    xs, ys = zip(*_ring(ulx, uly, segments))
    lons, lats = _to_wgs84(epsg).transform(xs, ys)
    lons = _unwrap(list(lons))
    poly = Polygon(zip(lons, lats))
    if not poly.is_valid:
        poly = poly.buffer(0)
    west, _, east, _ = poly.bounds
    if west >= -180.0 and east <= 180.0:
        return poly
    parts = []
    for k in range(math.floor((west + 180.0) / 360.0),
                   math.floor((east + 180.0) / 360.0) + 1):
        piece = poly.intersection(box(-180.0 + 360.0 * k, -90.0,
                                      180.0 + 360.0 * k, 90.0))
        if piece.is_empty:
            continue
        parts.append(_translate(piece, -360.0 * k))
    merged = unary_union(parts)
    return merged if isinstance(merged, MultiPolygon) else MultiPolygon([merged])


def _translate(geom, xoff: float):
    if xoff == 0.0:
        return geom
    from shapely.affinity import translate
    return translate(geom, xoff=xoff)


def bbox_of(geom) -> list[float]:
    """The GeoJSON bbox of a footprint.

    A footprint that was split at the antimeridian gets the RFC 7946
    form, where `west` is the westernmost vertex *east* of the
    antimeridian and so compares greater than `east`. Writing the plain
    envelope instead would claim the whole Pacific.
    """
    west, south, east, north = geom.bounds
    if isinstance(geom, MultiPolygon) and len(geom.geoms) > 1:
        lons = [x for g in geom.geoms for x, _ in g.exterior.coords]
        if any(l > 0 for l in lons) and any(l < 0 for l in lons):
            west = min(l for l in lons if l >= 0)
            east = max(l for l in lons if l < 0)
    return [round(west, 7), round(south, 7), round(east, 7), round(north, 7)]


def _round(obj, places: int):
    if isinstance(obj, (list, tuple)):
        return [_round(o, places) for o in obj]
    return round(obj, places)


def geometry_of(tile: str, segments: int = EDGE_SEGMENTS,
                places: int = 6) -> tuple[dict, list[float]]:
    """(GeoJSON geometry, bbox) for one tile id.

    Coordinates are rounded to `places` decimals -- six is about 11 cm,
    two orders of magnitude finer than the densification error, and it
    keeps an item's geometry near 800 bytes instead of the 2 KB that
    full double precision spends on digits nobody can use.
    """
    geom = footprint(tile, segments)
    out = mapping(geom)
    out["coordinates"] = _round(out["coordinates"], places)
    return out, bbox_of(geom)


def crosses_antimeridian(tile: str) -> bool:
    """Does the tile's footprint need splitting?"""
    return isinstance(footprint(tile), MultiPolygon)


def check_fixtures(path: str | None = None) -> int:
    """Replay every measured origin in grid_fixtures.json. Returns the
    number that disagree, and prints each one."""
    import json
    import pathlib
    src = pathlib.Path(path or pathlib.Path(__file__).with_name("grid_fixtures.json"))
    fixtures = json.loads(src.read_text())
    bad = 0
    for tile, (epsg, ulx, uly) in sorted(fixtures.items()):
        got = origin(tile)
        if got != (epsg, ulx, uly):
            bad += 1
            print(f"  {tile}: measured {(epsg, ulx, uly)}, computed {got}")
    print(f"{len(fixtures)} measured tile origin(s), {bad} disagreement(s)")
    return bad


if __name__ == "__main__":
    import sys
    sys.exit(1 if check_fixtures() else 0)
