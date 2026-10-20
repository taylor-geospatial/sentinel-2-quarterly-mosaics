# AGENTS.md — sentinel-2-quarterly-mosaics

This repository maintains the Portolan catalog for `tge-labs/sentinel-2-quarterly-cloudless-mosaics` on Source Cooperative.
These instructions apply to agents editing the repository.
The published [catalog agent guide](catalog/AGENTS.md) applies to agents querying the data.

## Provenance

The European Space Agency produces the Sentinel-2 Global Mosaics, the Copernicus Data Space Ecosystem publishes them, and Taylor Geospatial Engine hosts this copy.
Keep that order in every provider list, with Taylor Geospatial Engine last and assigned the `host` role.
Do not describe this catalog as the source of the data.

The mirror is byte-identical for the four bands it carries.
It omits the upstream `observations.tif`, `userdata.json`, and zipped `Product` assets.
Do not copy the upstream `item_assets` into this catalog, because it describes assets this mirror does not have.

## Publishing

`catalog/` is the published catalog.
Everything in it is published, and nothing outside it is published.
Do not move a file into `catalog/` to make it publish, and do not add a path outside `publish_dir` to `tools/publish.py`.
Keep the boundary structural rather than replacing it with exclusions.

`tools/publish.py` is this catalog's only publisher.
Do not use `portolan push` here.
It maintains separate `versions.json` state keyed on sha256, and the two publishers do not interoperate.

The write prefix already holds 607 TB from the transfer job in `{year}/{Qn}/{tile}/`, `manifest/`, `_status/`, and `_benchmarks/`.
`tools/publish.py` never deletes remote objects.
Do not add a delete pass.
Removing a file from `catalog/` does not remove its published copy.

Use `--force` for both dry runs and uploads.
Without it, `tools/publish.py` builds its change index by paginating `list_objects_v2` over the whole write prefix.
A typical catalog has a few hundred keys, but this prefix has over four million, so the listing can take a long time.
`--force` skips the listing and re-uploads all catalog files.

```bash
python3 tools/publish.py --force            # dry run, no listing
python3 tools/publish.py --force --confirm  # upload; needs AWS credentials
```

Source Cooperative uses the account name as the bucket and the product slug as the key prefix.
The destination is `s3://tge-labs/sentinel-2-quarterly-cloudless-mosaics/` through `https://data.source.coop`.
`catalog.publish.yaml` sets that `endpoint_url`, and STS tokens from the `source-coop` CLI are valid only against that endpoint.

## Data files and metadata

Never commit GeoParquet, COG, PMTiles, Zarr, or COPC files.
Generate test data in CI when a check needs file contents.
Deleting a committed binary later does not remove it from Git history.

Leave `file:size` and `file:checksum` out of an asset entry until its bytes are published.
Let `portolan check --fix` fill them once the bytes exist.
Do not estimate either value, because readers use them to validate downloads.

## Checks

Run the development checks in [README.md](README.md#development) before handing off changes.
Keep `rashid>=0.1.8,<0.2.0` in the development dependencies and conformance gate.
Version 0.1.8 is the first release that accepts the absolute root `self` link recommended by Portolan schema v0.2.0.

`CI_LIGHT=1` skips data assets and PMTiles links backed by matching visual assets in a metadata-only checkout.
Structural links, styles, and thumbnails must still resolve.
Leave `CI_LIGHT` unset when the referenced data files are present locally.
The publisher's session tests use `boto3` without network calls or credentials.

`ACCEPTED` in `tests/test_conformance.py` is empty.
Never add an entry without a matching row in [docs/conformance.md](docs/conformance.md) identifying the rule, where it occurs, why it is accepted, and the issue tracking its removal.

## Published agent guides

Every claim in `catalog/**/AGENTS.md` must be quoted from a source or measured from the data.
Run every documented query against the published files before adding it.
Do not invent join keys, column names, or query results.
Preserve measurement dates when revising the guides.

## Repository links and schema

Keep absolute `vcs` and `issues` links in `catalog/catalog.json`, both naming `taylor-geospatial/sentinel-2-quarterly-mosaics`.
Relative links would resolve against the public bucket URL rather than the repository.

The root also has an absolute `self` link, which Portolan schema v0.2.0 recommends (PORTO-CORE-081) and v0.1.1 forbids.
Every object declares the v0.2.0 schema URI (PORTO-CORE-009).
Do not mix those versions or lower the `rashid` floor below 0.1.8.
