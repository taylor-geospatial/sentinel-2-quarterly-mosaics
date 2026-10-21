"""GDAL subprocesses for staging and warping mosaic overview tiles."""

import concurrent.futures as cf
import os
import subprocess
import sys
import time
from pathlib import Path

import mgrs_grid

# Bands in display order: red, green, blue.
RGB = ("B04", "B03", "B02")
STRETCH_MAX = 2500  # reflectance x 10000 that saturates to 255
STRETCH_EXPONENT = 0.55

GDAL_ENV = {
    # The bucket has no directory listing worth paying for, and a stray
    # sidecar probe per tile would be 28,000 wasted round trips.
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
    "GDAL_HTTP_MULTIPLEX": "YES",
    "GDAL_HTTP_VERSION": "2",
    "VSI_CACHE": "TRUE",
    "VSI_CACHE_SIZE": "268435456",
    "GDAL_CACHEMAX": "1024",
    # A quarter is tens of thousands of range reads against one host, and
    # some of them will be cut off in the middle -- measured, as
    # `TIFFFillTile: got 108718 bytes, expected 148406`, on the very
    # first local run. Without a retry that is a failed job hours in.
    "GDAL_HTTP_MAX_RETRY": "5",
    "GDAL_HTTP_RETRY_DELAY": "2",
    # And a transfer that stalls rather than fails would hang the job
    # forever: below 1 kB/s for 30 s, give up and let the retry handle it.
    "GDAL_HTTP_LOW_SPEED_LIMIT": "1000",
    "GDAL_HTTP_LOW_SPEED_TIME": "30",
}


def say(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def run(cmd: list[str], what: str) -> None:
    env = {**os.environ, **GDAL_ENV}
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if r.returncode != 0:
        print(r.stdout[-2000:], r.stderr[-2000:], file=sys.stderr)
        sys.exit(f"{what} failed: {' '.join(cmd[:3])} ...")


def _band_vrt(job: tuple[int, str, list[str], Path, str, int, str, int]) -> float:
    """One (zone, band) VRT. This is where a quarter's time goes.

    `gdalbuildvrt` opens every source to read its georeferencing, and a
    quarter is 28,272 tiles x 3 bands = 84,816 HTTP header reads. Inside
    one call they are serial: measured on rails, zone 32601's 138 tiles
    took about 60 seconds for one band, so the roughly 360 (zone, band)
    pairs of a global quarter would be six hours before a single pixel
    was warped -- past the job's wall clock. They are independent, so
    they run on a pool.
    """
    epsg, band, group, scratch, public_base, year, quarter, level = job
    dest = scratch / f"{epsg}_{band}.vrt"
    if dest.is_file():
        return 0.0
    t0 = time.monotonic()
    listing = scratch / f"{epsg}_{band}.txt"
    listing.write_text(
        "".join(
            f"/vsicurl/{public_base}/{year}/{quarter}/{t}/{band}.tif\n" for t in group
        )
    )
    tmp = scratch / f".{epsg}_{band}.tmp.vrt"
    cmd = [
        "gdalbuildvrt",
        "-q",
        "-overwrite",
        "-srcnodata",
        "-32768",
        "-vrtnodata",
        "-32768",
    ]
    if level >= 0:
        cmd += ["-oo", f"OVERVIEW_LEVEL={level}"]
    cmd += ["-input_file_list", str(listing), str(tmp)]
    run(cmd, f"gdalbuildvrt {epsg} {band}")
    os.replace(tmp, dest)
    return time.monotonic() - t0


def zone_vrts(
    tiles: list[str],
    scratch: Path,
    public_base: str,
    year: int,
    quarter: str,
    level: int,
    jobs: int,
) -> dict[int, Path]:
    """One Byte RGB VRT per UTM zone, stretched and ready to warp.

    The per-band VRTs are built on a pool of `jobs` workers because they
    are network-bound header reads, not computation; the stacking and the
    stretch that follow only open local VRTs and cost nothing.
    """
    by_epsg: dict[int, list[str]] = {}
    for tile in tiles:
        by_epsg.setdefault(mgrs_grid.origin(tile)[0], []).append(tile)

    work = [
        (epsg, band, group, scratch, public_base, year, quarter, level)
        for epsg, group in sorted(by_epsg.items())
        for band in RGB
    ]
    t0 = time.monotonic()
    built = 0
    with cf.ThreadPoolExecutor(jobs) as pool:
        for n, secs in enumerate(pool.map(_band_vrt, work), start=1):
            if secs:
                built += 1
            if n % 30 == 0:
                say(
                    f"  {n}/{len(work)} band VRT(s), "
                    f"{time.monotonic() - t0:,.0f}s elapsed"
                )
    say(
        f"  {len(work)} band VRT(s) over {len(by_epsg)} zone(s) "
        f"({built} built, {len(work) - built} reused), "
        f"{time.monotonic() - t0:,.1f}s"
    )

    out = {}
    for epsg in sorted(by_epsg):
        band_vrts = [str(scratch / f"{epsg}_{band}.vrt") for band in RGB]
        stacked = scratch / f"{epsg}_rgb16.vrt"
        run(
            ["gdalbuildvrt", "-q", "-overwrite", "-separate", str(stacked), *band_vrts],
            f"gdalbuildvrt -separate {epsg}",
        )
        byte = scratch / f"{epsg}_rgb8.vrt"
        run(
            [
                "gdal_translate",
                "-q",
                "-of",
                "VRT",
                "-ot",
                "Byte",
                "-scale",
                "0",
                str(STRETCH_MAX),
                "0",
                "255",
                "-exponent",
                str(STRETCH_EXPONENT),
                "-a_nodata",
                "0",
                str(stacked),
                str(byte),
            ],
            f"gdal_translate stretch {epsg}",
        )
        out[epsg] = byte
    return out


ZONE_ATTEMPTS = 4


def warp_zone(args: tuple[int, Path, Path, float]) -> tuple[int, float, str]:
    """One zone into EPSG:3857 on the zoom level's pixel grid.

    Retried, because the failure that actually happens is a truncated
    range read -- `TIFFFillTile: got 117126 bytes, expected 153855`,
    seen twice on a domestic uplink. The HTTP request succeeded with a
    206, so GDAL's own `GDAL_HTTP_MAX_RETRY` never sees it; only redoing
    the warp recovers. A zone that fails every attempt is returned rather
    than raised: one flaky zone should not throw away the hundred that
    worked, and the caller refuses to assemble an overview with a hole in
    it.
    """
    epsg, src, dst, res = args
    if dst.is_file():
        return epsg, 0.0, ""
    t0 = time.monotonic()
    tmp = dst.with_name(f".{dst.name}.tmp")
    last = ""
    for attempt in range(1, ZONE_ATTEMPTS + 1):
        tmp.unlink(missing_ok=True)
        r = subprocess.run(
            [
                "gdalwarp",
                "-q",
                "-overwrite",
                "-t_srs",
                "EPSG:3857",
                "-tr",
                str(res),
                str(res),
                "-tap",
                "-r",
                "average",
                "-srcnodata",
                "0",
                "-dstnodata",
                "0",
                "-wo",
                "NUM_THREADS=2",
                "-multi",
                "-of",
                "GTiff",
                "-co",
                "TILED=YES",
                "-co",
                "COMPRESS=ZSTD",
                "-co",
                "BIGTIFF=IF_SAFER",
                str(src),
                str(tmp),
            ],
            capture_output=True,
            text=True,
            env={**os.environ, **GDAL_ENV},
        )
        if r.returncode == 0 and tmp.is_file():
            os.replace(tmp, dst)
            return epsg, time.monotonic() - t0, ""
        last = (r.stderr or r.stdout)[-300:].strip()
        if attempt < ZONE_ATTEMPTS:
            say(
                f"  zone {epsg}: attempt {attempt} failed, retrying "
                f"({last.splitlines()[-1][:90] if last else 'no output'})"
            )
            time.sleep(5 * attempt)
    tmp.unlink(missing_ok=True)
    return epsg, time.monotonic() - t0, last or "gdalwarp failed"
