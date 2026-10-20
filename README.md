# Sentinel-2 Quarterly Cloudless Mosaics

This repository maintains the [Portolan](https://www.portolan-sdi.org/) catalog and processing tools for [`tge-labs/sentinel-2-quarterly-cloudless-mosaics`](https://source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics) on Source Cooperative.
The mirror contains four bands of the Copernicus Sentinel-2 Global Mosaics for 36 quarters, from 2017 Q1 through 2025 Q4.
It holds 4,070,300 Cloud-Optimized GeoTIFFs across 1,017,575 mosaic tiles, totaling 607.5 TB.

The European Space Agency produces the mosaics, the Copernicus Data Space Ecosystem publishes them, and Taylor Geospatial Engine hosts this copy.
The imagery is available over HTTPS without credentials.
See the [data guide](catalog/README.md) for access examples, coverage, and provenance, or the [catalog agent guide](catalog/AGENTS.md) for tested queries.
Open the [map viewer](http://research.taylorgeospatial.org/sentinel-2-quarterly-mosaics/) to browse the imagery.
The [viewer source](apps/viewer) includes its [local development server](apps/viewer/devserver.py).

## Development

Use Python 3.12, matching CI.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install --group dev
ruff check .
ruff format --check .
ty check
CI_LIGHT=1 python tests/run_all.py
```

Leave `CI_LIGHT` unset when the referenced data files are present locally to run the full link check.
The [conformance notes](docs/conformance.md) explain the metadata-only checks and validator exceptions.
For imagery processing and backfills, use the [RAILS setup and job instructions](tools/rails/README.md).

## Publish

`tools/publish.py` uploads the contents of `catalog/` to the existing product prefix.
It never deletes remote objects.
Use `--force` to skip listing the prefix's more than four million objects.

```bash
python3 tools/publish.py --force            # dry run: what would upload
python3 tools/publish.py --force --confirm  # upload; needs AWS credentials
```

[`catalog.publish.yaml`](catalog.publish.yaml) defines the destination and the `https://data.source.coop` endpoint.
See the [RAILS credential setup](tools/rails/README.md#credentials) for authentication and [repository agent instructions](AGENTS.md) for publishing constraints.
Removing a local file does not remove its published copy.

## Repository layout

| Path | Contents |
|---|---|
| `catalog/` | Published metadata, data guides, and collection definitions |
| `catalog/mosaics/` | Imagery collection and per-quarter partitions |
| `catalog/coverage/` | Tile footprints and per-tile, per-quarter statistics |
| `apps/viewer/` | Static map viewer |
| `tools/` | Catalog generation, data processing, and upload tools |
| `tests/` | Catalog and tooling checks |

## License

The repository tooling uses Apache-2.0.
The imagery uses the [Copernicus Sentinel Data Legal Notice](https://sentinels.copernicus.eu/documents/247904/690755/Sentinel_Data_Legal_Notice), summarized in the [data guide](catalog/README.md#license).
