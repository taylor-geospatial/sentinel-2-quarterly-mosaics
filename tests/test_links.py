#!/usr/bin/env python3
"""Every relative link and asset href resolves to a file that exists.

This catches the most common hand-edit mistake: adding a child link before the
directory it points at exists. Dependency-free and offline, so it runs in
milliseconds on a clean checkout.

CI clones the metadata and not the bytes, because .gitignore keeps data out of
git. Set CI_LIGHT=1 there. It exempts asset hrefs with a data suffix and
PMTiles links backed by a matching visual asset. Every structural link still
resolves. Leave CI_LIGHT unset locally to check every relative href on disk.

Run: python3 tests/test_links.py
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from publish import load_config  # noqa: E402

# The exemption reads the suffix and nothing else. A directory rule or a path
# prefix rule widens on its own as the catalog grows. This tuple does not.
DATA_SUFFIXES = (
    ".parquet", ".pmtiles", ".tif", ".tiff", ".copc.laz", ".laz", ".gpkg",
    ".zarr", ".geojsonl", ".shp", ".zip",
)


def is_remote(href: str) -> bool:
    return "://" in href or href.startswith(("#", "mailto:"))


def is_data_asset(href: str, ci_light: bool) -> bool:
    """True when href points at a file kept out of git on purpose."""
    return ci_light and href.lower().endswith(DATA_SUFFIXES)


def is_data_link(link: dict, assets: dict, ci_light: bool) -> bool:
    """Only a PMTiles discovery link backed by the same declared visual asset."""
    href = link.get("href", "")
    media_type = "application/vnd.pmtiles"
    return (
        ci_light
        and link.get("rel") == "pmtiles"
        and href.lower().endswith(".pmtiles")
        and link.get("type") == media_type
        and any(
            asset.get("href") == href
            and asset.get("type") == media_type
            and "visual" in asset.get("roles", [])
            for asset in assets.values()
        )
    )


def stac_documents(base: Path, errors: list[str]) -> list[Path]:
    """Every STAC object under the published directory."""
    out = []
    for path in sorted(base.rglob("*.json")):
        if any(part.startswith(".") for part in path.relative_to(base).parts):
            continue
        if path.name.endswith(".style.json") or "styles" in path.parts:
            continue
        try:
            doc = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            errors.append(f"{path.relative_to(base)}: invalid JSON ({exc})")
            continue
        if isinstance(doc, dict) and doc.get("type") in {
            "Catalog", "Collection", "Feature"
        }:
            out.append(path)
    return out


def check_links(base: Path, ci_light: bool = False) -> list[str]:
    """Check a catalog tree; return errors without changing any files."""
    errors: list[str] = []
    documents = stac_documents(base, errors)
    checked = skipped = 0
    for path in documents:
        doc = json.loads(path.read_text())
        rel_path = path.relative_to(base)
        assets = doc.get("assets") or {}
        for link in doc.get("links", []):
            href = link.get("href", "")
            if not href or is_remote(href):
                continue
            if is_data_link(link, assets, ci_light):
                skipped += 1
                continue
            checked += 1
            if not (path.parent / href).resolve().exists():
                errors.append(
                    f"{rel_path}: rel:{link.get('rel')} -> {href} does not exist"
                )

        for key, asset in assets.items():
            href = asset.get("href", "")
            if not href or is_remote(href):
                continue
            if is_data_asset(href, ci_light):
                skipped += 1
                continue
            checked += 1
            if not (path.parent / href).resolve().exists():
                errors.append(f"{rel_path}: asset {key} -> {href} does not exist")

    if skipped:
        print(
            f"note   {skipped} data href(s) not checked: CI_LIGHT is set and the "
            "bytes live in\n       object storage, not git. Run without CI_LIGHT "
            "locally to check them."
        )
    if not errors:
        print(f"OK: {checked} relative href(s) across {len(documents)} object(s)")
    return errors


if __name__ == "__main__":
    config = load_config()
    errors = check_links(ROOT / config["publish_dir"],
                         os.environ.get("CI_LIGHT") == "1")
    if errors:
        print("\n".join(f"error  {e}" for e in errors))
        raise SystemExit(1)
