#!/usr/bin/env python3
"""Render the coverage thumbnail from tiles.parquet and the default style.

Download the published coverage table, then run:

    python3 tools/make_coverage_thumbnail.py --source /path/to/tiles.parquet

Uses pyarrow, shapely, pyproj, numpy and rasterio from the pipeline environment.
No data file is added to git. The output is a Web Mercator world map using
the default style's background, quarter-count fill colours and opacity. At
this scale the style's minzoom=4 outlines are hidden.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import rasterio
import shapely
from pyproj import Transformer
from rasterio.features import rasterize
from rasterio.transform import from_bounds

ROOT = Path(__file__).resolve().parent.parent
WORLD = 20037508.342789244


def coverage(source: Path) -> tuple[list[bytes], list[int], int, list[str]]:
    """One geometry and distinct-quarter count per subtile, in stable order."""
    geometries: dict[str, bytes] = {}
    quarters: dict[str, set[str]] = {}
    rows = 0
    for batch in pq.ParquetFile(source).iter_batches(
        columns=["subtile", "quarter", "geometry"]
    ):
        tiles, dates, shapes = (column.to_pylist() for column in batch.columns)
        for tile, quarter, geometry in zip(tiles, dates, shapes):
            geometries.setdefault(tile, geometry)
            quarters.setdefault(tile, set()).add(quarter)
            rows += 1
    if not rows:
        raise ValueError("The coverage table is empty")
    # Paint the highest counts last where adjacent mosaic cells overlap.
    order = sorted(geometries, key=lambda tile: (len(quarters[tile]), tile))
    return (
        [geometries[tile] for tile in order],
        [len(quarters[tile]) for tile in order],
        rows,
        sorted(set().union(*quarters.values())),
    )


def rgb(colour: str) -> np.ndarray:
    """A style's hex RGB colour as floating-point channels."""
    return np.array([int(colour[i:i + 2], 16) for i in (1, 3, 5)], dtype=float)


def render(source: Path, style_path: Path, output: Path, size: int) -> dict:
    """Render measured coverage with the collection's quarter-count palette."""
    geometries, counts, rows, quarters = coverage(source)
    style = json.loads(style_path.read_text())
    layers = {layer["id"]: layer for layer in style["layers"]}
    background = rgb(layers["background"]["paint"]["background-color"])
    paint = layers["footprints-fill"]["paint"]
    step = paint["fill-color"]
    if step[:2] != ["step", ["get", "quarters_present"]]:
        raise ValueError("Expected a step style on quarters_present")
    opacity = paint["fill-opacity"]
    colours = [background]
    for count in range(1, max(counts) + 1):
        colour = step[2]
        for threshold, value in zip(step[3::2], step[4::2]):
            if count >= threshold:
                colour = value
        colours.append(opacity * rgb(colour) + (1 - opacity) * background)
    palette = np.rint(colours).astype("uint8")

    project = Transformer.from_crs(4326, 3857, always_xy=True)
    shapes = shapely.transform(
        shapely.from_wkb(geometries), project.transform, interleaved=False
    )
    grid = rasterize(
        zip(shapes, counts), out_shape=(size, size), fill=0, dtype="uint16",
        transform=from_bounds(-WORLD, -WORLD, WORLD, WORLD, size, size),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        output, "w", driver="WEBP", width=size, height=size, count=3,
        dtype="uint8", lossless=True,
    ) as dst:
        dst.write(palette[grid].transpose(2, 0, 1))
    return {
        "rows": rows, "subtiles": len(counts), "quarters": quarters,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "thumbnail_bytes": output.stat().st_size,
        "thumbnail_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--style", type=Path,
                        default=ROOT / "catalog/coverage/styles/default.json")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "catalog/coverage/thumbnail.webp")
    parser.add_argument("--size", type=int, default=1024)
    args = parser.parse_args()
    if args.size <= 0:
        parser.error("--size must be positive")
    print(json.dumps(render(args.source, args.style, args.output, args.size),
                     indent=2))


if __name__ == "__main__":
    main()
