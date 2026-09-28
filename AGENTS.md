# AGENTS.md — sentinel-2-quarterly-mosaics

The repository that maintains the Portolan catalog for
`tge-labs/sentinel-2-quarterly-cloudless-mosaics` on Source Cooperative. This
file is for agents working **in the repository**. The published agent guide,
for agents **querying the data**, is `catalog/AGENTS.md`, and it is a different
document with a stricter rule.

## What this catalog is

A mirror, not an original publication. The European Space Agency produces the
Sentinel-2 Global Mosaics; the Copernicus Data Space Ecosystem publishes them;
Taylor Geospatial Engine hosts this copy. Every provider list keeps that order,
with the `host` role on Taylor Geospatial Engine and listed last. Never
describe this catalog as the source of the data.

The mirror is byte-identical for the four bands it carries. It does **not**
carry the upstream `observations.tif`, `userdata.json` or zipped `Product`
assets. Do not copy the upstream `item_assets` into this catalog: it describes
assets this mirror does not have.

## The publish boundary

`catalog/` is the published catalog. Everything in it is published, and
nothing outside it ever is. Do not move a file into `catalog/` to make it
publish, and do not add a path outside `publish_dir` to `tools/publish.py`.
The boundary is the only thing standing between a scratch file and a public
bucket, and it holds because it is structural rather than a list of
exclusions.

`tools/publish.py` is this catalog's only publisher. Do not use `portolan push`
here: it keeps its own `versions.json` state keyed on sha256, and the two
publishers do not interoperate.

## The catalog publishes into an occupied prefix

Unlike most Portolan catalogs, the write prefix already holds 607 TB written by
the transfer job: `{year}/{Qn}/{tile}/` imagery, `manifest/`, `_status/` and
`_benchmarks/`. `tools/publish.py` never deletes, which is what makes that safe.
Do not add a delete pass to it.

**Use `--force` for the dry run here.** `tools/publish.py` builds its change
index by paginating `list_objects_v2` over the whole write prefix. For a normal
catalog that is a few hundred keys. For this one it is over four million, and
the dry run appears to hang. `--force` skips the remote listing and reports what
would upload:

```bash
python3 tools/publish.py --force            # dry run, no listing
python3 tools/publish.py --force --confirm  # upload; needs AWS credentials
```

The cost of `--force` is that it re-uploads all 11 catalog files instead of only
the changed ones. At this size that is cheaper than the listing it avoids.

## Data never enters git

Never commit a GeoParquet, COG, PMTiles, Zarr, or COPC file. If a gate needs
bytes to check, generate them in CI. Git keeps every version of a binary
forever and deleting it later reclaims nothing.

## Do not fabricate file:size or file:checksum

An asset whose bytes are not published yet carries no `file:size` and no
`file:checksum`. Write the asset entry, leave both out, and let
`portolan check --fix` fill them once the bytes exist. A guessed size is a
claim the catalog cannot back, and a reader who validates against it gets a
failure that looks like corruption.

## The conformance allow-list

`ACCEPTED` in `tests/test_conformance.py` ships empty. Never add an entry
without a matching row in `docs/conformance.md` giving the rule, where it
fires, why it is accepted, and the issue tracking its removal.

## Published agent guides

Every claim in a `catalog/**/AGENTS.md` is either quoted from a source or
measured from the data. An invented join key or column name produces a
confident wrong answer that nothing downstream catches. Run every query you
document, against the published files, before you write it down.

## The links back to this repository

`catalog/catalog.json` carries a `vcs` link and an `issues` link, both
absolute, both naming `taylor-geospatial/sentinel-2-quarterly-mosaics`. The
repository sits outside the published catalog, so a relative href would resolve
against the public base URL and point at a bucket path that holds nothing.

The root also carries an absolute `self` link, which Portolan schema v0.2.0
recommends (PORTO-CORE-081) and v0.1.1 forbids. Every object in the tree
declares the v0.2.0 schema URI (PORTO-CORE-009). Do not mix the two versions,
and do not lower the `rashid` floor below 0.1.8, which is the first version
that accepts that link.
