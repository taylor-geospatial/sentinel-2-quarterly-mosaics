# Sentinel-2 Quarterly Cloudless Mosaics

The [Portolan](https://www.portolan-sdi.org/) catalog for
[`tge-labs/sentinel-2-quarterly-cloudless-mosaics`](https://source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics)
on Source Cooperative: a full mirror of the Copernicus Sentinel-2 Global
Mosaics, 36 quarters from 2017 Q1 through 2025 Q4, 4,070,300 Cloud-Optimized
GeoTIFFs over 1,017,575 mosaic tiles, 607.5 TB.

This repository holds the catalog metadata and the tools that generate it. The
imagery is already in the bucket. Nothing here moves 607 TB.

**Start at [`catalog/README.md`](catalog/README.md)** for what the data is and
how to read it, or [`catalog/AGENTS.md`](catalog/AGENTS.md) if you are an agent
about to query it.

**`catalog/` is the published catalog.** Everything in it is published.
Everything outside it never is. That boundary is the whole publish contract,
and `tools/publish.py` has no flag or config key that widens it.

## Layout

| Path | What it is |
|---|---|
| `catalog/` | The published tree, synced 1:1 to the bucket prefix |
| `catalog/mosaics/` | The imagery collection: bands, item index, per-quarter partitions |
| `catalog/coverage/` | Tile footprints and per-tile, per-quarter statistics |
| `catalog.publish.yaml` | Where it publishes, and under what public URL |
| `tools/publish.py` | The metadata sync. Dry run by default |
| `tools/upload_data.py` | The data upload. Dry run by default |
| `tests/` | The gates CI runs on every pull request |
| `docs/conformance.md` | Any validator finding this catalog accepts, and why |

## What publishes where

The catalog publishes into the existing product root, beside the imagery it
describes:

```
s3://us-west-2.opendata.source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/
https://data.source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/
```

The `{year}/{Qn}/{tile}/` imagery directories, `manifest/`, `_status/` and
`_benchmarks/` were written by the transfer job and are not touched by this
repository. `tools/publish.py` never deletes, so it cannot disturb them.

## Publish

```bash
python3 tools/publish.py            # dry run: what would change
python3 tools/publish.py --confirm  # upload; needs AWS credentials
```

It never deletes. Removing a file from `catalog/` does not unpublish it, so
delete the object yourself if that is what you meant.

## Test

```bash
python3 -m venv .venv
.venv/bin/pip install 'rashid>=0.1.8,<0.2.0' stac-check
PATH="$PWD/.venv/bin:$PATH" python3 tests/run_all.py
```

| Gate | What it checks |
|---|---|
| `test_links.py` | Every relative link and asset href resolves |
| `test_publish.py` | Nothing outside `catalog/` can be uploaded |
| `test_upload_data.py` | Only staged files with an allowed suffix upload |
| `test_stac_valid.py` | Valid STAC 1.1.0, via `stac-check` |
| `test_conformance.py` | Portolan conformance, via `rashid` |

`rashid` 0.1.8 is the floor, because it is the first version that accepts the
absolute root `self` link that Portolan schema v0.2.0 recommends. CI installs
the same range the gate enforces.

Leave `CI_LIGHT` unset locally so the full link check runs. CI sets it, because
a fresh checkout has the metadata and not the 607 TB the asset hrefs point at.

## License

Apache-2.0, covering the tooling in this repository. The imagery carries the
[Copernicus Sentinel Data Legal Notice](https://sentinels.copernicus.eu/documents/247904/690755/Sentinel_Data_Legal_Notice),
which `catalog/README.md` states in full.
