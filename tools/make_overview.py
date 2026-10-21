#!/usr/bin/env python3
"""Build one quarter's browse layer: an RGB Web-Mercator overview COG.

    python3 tools/make_overview.py --year 2024 --quarter Q2 \\
        --work /u/cholmes/s2-mosaics/work --out /u/cholmes/s2-mosaics/publish
    python3 tools/make_overview.py --year 2024 --quarter Q2 --work w --out p \\
        --bbox 4,51,7,54 --jobs 4            # a small region, for a smoke test
    python3 tools/make_overview.py --year 2024 --quarter Q2 --out p --thumbnail

Writes `{out}/mosaics/quarter=YYYY.Qn/overview.tif`, and with
`--thumbnail`, `thumbnail.webp` beside it from the COG that is already
there.

What it reads, and why it is affordable
---------------------------------------
Nothing reads a full-resolution tile. Each source is opened through
`OVERVIEW_LEVEL`, so GDAL sees only that tile's internal overview: at the
default zoom 10 (152.87 m/px) the 16x overview at 160 m is the coarsest
level still within `--oversample` of the target, so a 10,008 x 10,008
tile is read as 626 x 626. That is 1/256th of the pixels, and it is the
difference between a quarter's overview costing terabytes of reads and
costing gigabytes.

The three steps
---------------
1. One VRT per (UTM zone, band) over that zone's `/vsicurl` tiles, then a
   three-band VRT (B04, B03, B02), then a Byte VRT that applies the
   stretch. Zones are the unit because `gdalbuildvrt` needs one CRS and
   every tile is in its own UTM zone.
2. `gdalwarp` each zone's Byte VRT into EPSG:3857 on the exact
   Web-Mercator pixel grid of the chosen zoom level. These run in
   parallel, one process per zone, and a zone whose GeoTIFF already
   exists is skipped, so a job that ran out of time resumes where it
   stopped.
3. `gdalbuildvrt` over the warped zones, then one `gdal_translate -of
   COG` with `TILING_SCHEME=GoogleMapsCompatible`, which aligns the COG
   and its internal overviews to the XYZ tile grid a web client asks
   for.

Nodata is 0 throughout, not an alpha band, and that is deliberate.
`gdalbuildvrt -srcnodata 0` makes each source skip its nodata pixels, so
one zone's empty corner cannot erase the neighbouring zone's data where
their rectangles overlap -- which is exactly what an alpha band would do,
because a VRT paints sources in order and an alpha band is data. On the
way out, `-b mask` turns the nodata mask into an alpha band, and the COG
driver stores that alpha as the internal transparency mask that JPEG
compression requires.

The stretch
-----------
Reflectance x 10000 is mapped `0..2500 -> 0..255` with an exponent of
0.55, which lifts vegetation and water out of the bottom of the range
without blowing out cloud and snow. It is a fixed stretch, not a
per-quarter percentile, because the whole point of this layer is
scrubbing through 36 quarters: a stretch that moved between quarters
would make every transition look like a change in the scene.

The one cost of nodata-as-0 is that a pixel whose reflectance is exactly
zero in all three bands becomes transparent. With the exponent applied, a
reflectance of 1 already maps to 4, so this is genuinely only the
all-zero pixel, which is a sensor gap rather than a dark surface.

2026 Q2 reads differently
-------------------------
Every quarter from 2017 to 2025 stores its COGs in 1024 x 1024 blocks;
2026 Q2, which finished transferring on 2026-09-28, uses 256 x 256. The
overview *levels* are unchanged (2x through 32x, down to 313 x 313), so
`overview_level()` picks the same one and the bytes read are the same.
Only the request count moves, and GDAL's merging of consecutive ranges
absorbs most of it: measured on 31UFU_0_0, reading the 32x overview took
2 range requests in 2024 Q2 and 3 in 2026 Q2, and the 16x overview the
same 2 against 3. Nothing here assumes a block size, and the tile origins
are identical between the two vintages, so no code changes for it -- but
if a quarter ever needs a per-request budget, that is the difference.

Size
----
At zoom 10 the world is 262,144 px square, but the mosaic only covers
land and `SPARSE_OK` means empty blocks cost nothing on disk. Pass
`--zoom` to trade detail for bytes: each step down is a quarter of the
pixels.
"""

import argparse
import concurrent.futures as cf
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mgrs_grid  # noqa: E402
import schema  # noqa: E402
from overview_gdal import ZONE_ATTEMPTS, run, say, warp_zone, zone_vrts  # noqa: E402
from publish import load_config  # noqa: E402

# Web Mercator, at zoom 0, in metres per pixel of a 256 px tile.
Z0_RESOLUTION = 2 * 20_037_508.342789244 / 256
WORLD = 20_037_508.342789244


def require_gdal() -> dict[str, bool]:
    """Fail early if the toolchain is not there; report what it can do."""
    for tool in ("gdalbuildvrt", "gdalwarp", "gdal_translate", "gdalinfo"):
        if shutil.which(tool) is None:
            sys.exit(
                f"{tool} is not on PATH; this step needs GDAL "
                "(see tools/rails/environment.yml)"
            )
    formats = subprocess.run(
        ["gdalinfo", "--formats"], capture_output=True, text=True
    ).stdout
    return {"webp": "WEBP" in formats}


def resolution(zoom: int) -> float:
    return Z0_RESOLUTION / (2**zoom)


def overview_level(target: float, oversample: float) -> int:
    """Which internal overview of a source tile to open.

    The tiles carry overviews at 2x, 4x, 8x, 16x and 32x of 10 m. Take
    the coarsest one that is no more than `oversample` times coarser than
    the target resolution, so the read is as small as it can be without
    the output visibly softening. Returns a 0-based index into that list,
    which is what GDAL's `OVERVIEW_LEVEL` open option wants; -1 means
    full resolution.
    """
    best = -1
    for index, factor in enumerate((2, 4, 8, 16, 32)):
        if mgrs_grid.GSD * factor <= target * oversample:
            best = index
    return best


def tiles_of(
    work: Path,
    year: int,
    quarter: str,
    bbox: tuple[float, float, float, float] | None,
    only: list[str] | None = None,
) -> list[str]:
    """The quarter's tile ids, from the staging file make_items.py wrote.

    `--bbox` keeps only the tiles whose footprint meets it, and `--tiles`
    names them outright, which is how a smoke test builds two degrees of
    the Netherlands instead of the world.
    """
    if only:
        return sorted(set(only))
    staging = work / f"quarter={year}.{quarter}" / "items.ndjson"
    if not staging.is_file():
        sys.exit(f"{staging}: not there; run make_items.py first")
    tiles = []
    with staging.open() as fh:
        for line in fh:
            tiles.append(json.loads(line)["_subtile"])
    if bbox:
        from shapely.geometry import box

        want = box(*bbox)
        tiles = [t for t in tiles if mgrs_grid.footprint(t).intersects(want)]
    return sorted(set(tiles))


def build(
    year: int,
    quarter: str,
    work: Path,
    out: Path,
    zoom: int,
    oversample: float,
    jobs: int,
    bbox,
    keep_scratch: bool,
    only: list[str] | None = None,
    stage_jobs: int = 0,
) -> Path:
    public_base = load_config()["public_base"].rstrip("/")
    res = resolution(zoom)
    level = overview_level(res, oversample)
    # Staging is HTTP latency, not CPU, so it wants far more concurrency
    # than the warps do.
    stage_jobs = stage_jobs or min(64, jobs * 4)
    tiles = tiles_of(work, year, quarter, bbox, only)
    if not tiles:
        sys.exit("no tiles match; nothing to build")
    say(
        f"{year} {quarter}: {len(tiles):,} tile(s), zoom {zoom} "
        f"({res:,.2f} m/px), source overview level {level} "
        f"({mgrs_grid.GSD * 2 ** (level + 1):,.0f} m/px)"
    )

    part = out / schema.COLLECTION / f"quarter={year}.{quarter}"
    part.mkdir(parents=True, exist_ok=True)
    scratch = work / f"quarter={year}.{quarter}" / "overview-scratch"
    scratch.mkdir(parents=True, exist_ok=True)

    t0 = time.monotonic()
    vrts = zone_vrts(tiles, scratch, public_base, year, quarter, level, stage_jobs)
    say(
        f"{year} {quarter}: {len(vrts)} UTM zone(s) staged, "
        f"{time.monotonic() - t0:,.1f}s"
    )

    t0 = time.monotonic()
    work_items = [
        (epsg, src, scratch / f"{epsg}_3857.tif", res)
        for epsg, src in sorted(vrts.items())
    ]
    broken = []
    with cf.ThreadPoolExecutor(jobs) as pool:
        for epsg, secs, err in pool.map(warp_zone, work_items):
            if err:
                broken.append((epsg, err))
                print(
                    f"  zone {epsg}: FAILED after {ZONE_ATTEMPTS} attempts: {err}",
                    file=sys.stderr,
                )
            elif secs:
                say(f"  zone {epsg}: warped in {secs:,.1f}s")
    if broken:
        # Every warped zone is kept, so a resubmit redoes only these.
        sys.exit(
            f"{len(broken)} zone(s) did not warp: "
            f"{', '.join(str(e) for e, _ in broken)}. The overview "
            "would have a hole in it, so it was not assembled; the "
            "zones that succeeded are kept, so rerunning retries "
            "only these."
        )
    say(f"{year} {quarter}: all zones warped, {time.monotonic() - t0:,.1f}s")

    mosaic = scratch / "mosaic.vrt"
    run(
        [
            "gdalbuildvrt",
            "-q",
            "-overwrite",
            "-srcnodata",
            "0",
            "-vrtnodata",
            "0",
            str(mosaic),
            *[str(p) for _, _, p, _ in work_items],
        ],
        "gdalbuildvrt mosaic",
    )

    # Nodata did its job in the mosaic; from here the transparency has to
    # be an alpha band. `-b mask` turns the nodata mask into a real
    # fourth band before the compression touches anything. The COG
    # driver's own ADD_ALPHA does not help here: it only fires when the
    # driver reprojects, and by this point the mosaic is already on the
    # tiling scheme's grid.
    rgba = scratch / "rgba.vrt"
    run(
        [
            "gdal_translate",
            "-q",
            "-of",
            "VRT",
            "-b",
            "1",
            "-b",
            "2",
            "-b",
            "3",
            "-b",
            "mask",
            "-colorinterp",
            "red,green,blue,alpha",
            str(mosaic),
            str(rgba),
        ],
        "gdal_translate alpha",
    )

    # JPEG, not WebP and not a lossless codec, and the choice is about
    # the readers and the bytes. JPEG-in-TIFF is the most widely decoded
    # compression there is: every GDAL build, every geotiff.js, and the
    # browser COG renderers all read it. WebP-in-TIFF needs a GDAL built
    # with libwebp, and a reader without it shows an empty layer with no
    # error. Lossless was measured and rejected: DEFLATE came to 9.6 GB
    # a quarter against about 1.1 GB here, for a browse layer that was
    # lossy WebP before. The COG driver turns the alpha band into an
    # internal transparency mask, which mask-aware readers apply; a
    # reader that ignores masks shows black ocean rather than nothing.
    #
    # Quality is one number for all 36 quarters, like ZOOM: a quarter
    # compressed differently would look different mid-scrub.
    final = part / "overview.tif"
    tmp = final.with_name(f".{final.name}.tmp")
    t0 = time.monotonic()
    run(
        [
            "gdal_translate",
            "-q",
            "-of",
            "COG",
            str(rgba),
            str(tmp),
            "-co",
            "COMPRESS=JPEG",
            "-co",
            "OVERVIEW_COMPRESS=JPEG",
            "-co",
            "QUALITY=85",
            "-co",
            "OVERVIEW_QUALITY=85",
            "-co",
            "TILING_SCHEME=GoogleMapsCompatible",
            "-co",
            f"ZOOM_LEVEL={zoom}",
            # Dense, not sparse, and it was measured the hard way. A sparse
            # block reads as "tile not found", and @developmentseed/geotiff
            # (the Portolan Browser renderer) throws on it, which blanks the
            # whole viewport batch. An empty JPEG tile costs a few hundred
            # bytes; a sparse one costs a client.
            "-co",
            "RESAMPLING=AVERAGE",
            "-co",
            "OVERVIEW_RESAMPLING=AVERAGE",
            "-co",
            "BIGTIFF=IF_SAFER",
            "-co",
            "NUM_THREADS=ALL_CPUS",
        ],
        "gdal_translate COG",
    )
    os.replace(tmp, final)
    say(
        f"{year} {quarter}: {final} written, JPEG+mask, "
        f"{final.stat().st_size / 1e6:,.1f} MB, "
        f"{time.monotonic() - t0:,.1f}s"
    )
    if not keep_scratch:
        shutil.rmtree(scratch, ignore_errors=True)
    return final


def thumbnail(source: Path, dest: Path, width: int, webp: bool) -> None:
    """Downsample the overview COG to one small image for the card.

    Reads the COG's own overviews rather than its base level, so this
    costs a few megabytes however large the COG is.
    """
    if not source.is_file():
        sys.exit(f"{source}: build the overview first")
    tmp = dest.with_name(f".{dest.name}.tmp")
    fmt = "WEBP" if webp else "JPEG"
    run(
        [
            "gdal_translate",
            "-q",
            "-of",
            fmt,
            "-outsize",
            str(width),
            "0",
            "-r",
            "average",
            str(source),
            str(tmp),
        ],
        "gdal_translate thumbnail",
    )
    os.replace(tmp, dest)
    for stray in (
        dest.with_suffix(dest.suffix + ".aux.xml"),
        tmp.with_suffix(tmp.suffix + ".aux.xml"),
    ):
        stray.unlink(missing_ok=True)
    say(f"{dest}: {dest.stat().st_size / 1e3:,.0f} kB")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--quarter", required=True, choices=sorted(schema.QUARTER_MONTHS))
    ap.add_argument(
        "--work", help="where make_items.py wrote (not needed with --thumbnail alone)"
    )
    ap.add_argument("--out", required=True, help="the publish tree")
    ap.add_argument(
        "--zoom",
        type=int,
        default=10,
        help="Web Mercator zoom level of the base resolution (default 10, 152.87 m/px)",
    )
    ap.add_argument(
        "--oversample",
        type=float,
        default=1.1,
        help="how much coarser than the target a source overview may be (default 1.1)",
    )
    ap.add_argument(
        "--jobs", type=int, default=8, help="zones warped at once (default 8)"
    )
    ap.add_argument(
        "--stage-jobs",
        type=int,
        default=0,
        help="band VRTs staged at once; these are HTTP header "
        "reads, not computation, so the default is 4x "
        "--jobs capped at 64",
    )
    ap.add_argument(
        "--bbox",
        help="west,south,east,north in degrees; keep only the tiles that meet it",
    )
    ap.add_argument(
        "--tiles",
        help="comma-separated tile ids to build, instead of reading the staging file",
    )
    ap.add_argument(
        "--thumbnail",
        action="store_true",
        help="write thumbnail.webp from the overview COG",
    )
    ap.add_argument("--thumbnail-width", type=int, default=1024)
    ap.add_argument(
        "--keep-scratch",
        action="store_true",
        help="leave the per-zone VRTs and GeoTIFFs in place",
    )
    a = ap.parse_args(argv)

    caps = require_gdal()
    webp = caps["webp"]
    if not webp:
        say(
            "this GDAL has no WEBP driver; the thumbnail falls back to "
            "JPEG. The overview COG is DEFLATE and does not need it."
        )
    part = Path(a.out) / schema.COLLECTION / f"quarter={a.year}.{a.quarter}"
    bbox = tuple(float(v) for v in a.bbox.split(",")) if a.bbox else None

    only = [t.strip() for t in a.tiles.split(",")] if a.tiles else None
    if a.work or only:
        build(
            a.year,
            a.quarter,
            Path(a.work or a.out),
            Path(a.out),
            a.zoom,
            a.oversample,
            a.jobs,
            bbox,
            a.keep_scratch,
            only,
            a.stage_jobs,
        )
    if a.thumbnail:
        thumbnail(
            part / "overview.tif",
            part / ("thumbnail.webp" if webp else "thumbnail.jpg"),
            a.thumbnail_width,
            webp,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
