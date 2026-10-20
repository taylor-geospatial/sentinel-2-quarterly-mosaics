# Portolan conformance

CI checks this catalog with [rashid](https://github.com/portolan-sdi/rashid).
Run the same check locally with:

```bash
python3 tests/test_conformance.py
```

The check fails on any error-severity finding whose rule is absent from `ACCEPTED` in `tests/test_conformance.py`.
Each accepted rule requires a row below explaining where it occurs, why it is accepted, and which issue tracks its removal.

## Validator versions

The conformance check requires rashid `>=0.1.8,<0.2.0`.
It reads `rashid --version` and fails if the version is outside that range or the tool is absent.
For a missing installation, it prints the install command.

Rashid 0.1.8 is the first release that accepts the absolute root `self` link recommended by Portolan schema v0.2.0 (PORTO-CORE-081).
The catalog includes that link.
The check also asserts rules PTL-LNK-007, PTL-LNK-008, PTL-LNK-009, and PTL-AST-006, introduced in 0.1.5.
Older versions cannot validate those requirements.
The development dependencies and CI use the same version range.

The upper bound prevents an unreviewed 0.2 rule set from changing validation behavior.
Read the new rules and update both bounds when moving to 0.2.

## Accepted deviations

There are no accepted deviations.
`ACCEPTED` in `tests/test_conformance.py` remains empty.
The validator workarounds below do not add accepted catalog deviations.

## Coverage derivatives

The published `tiles.parquet` and `footprints.pmtiles` were downloaded and measured on 2026-10-20.
The table contains 912,320 rows across 32 quarters, from 2018 Q1 through 2025 Q4, and 34,423 mosaic tiles.
The PMTiles metadata reports the same footprint count.
The collection records the measured row count, extent, sizes, and checksums.
The coverage guides document that the table excludes 2017 imagery.

The thumbnail renders this table in Web Mercator using the default style's quarter-count fill colors, opacity, and background.
It shows tile presence rather than valid-pixel fraction.
Reproduce it with the pipeline environment in `tools/rails/environment.yml`:

```bash
mkdir -p .cache/coverage
curl --fail --location --retry 3 \
  https://data.source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/coverage/tiles.parquet \
  --output .cache/coverage/tiles.parquet
python3 tools/make_coverage_thumbnail.py --source .cache/coverage/tiles.parquet
```

The generator prints the source SHA-256, row and tile counts, quarters, and thumbnail size and SHA-256.
The source used for this thumbnail has SHA-256 `e413f9c88c6fe8b940fbd207494d1d12d37d0af5b9ceb74275477d7eb10bb38a`.
Refresh the collection metadata and thumbnail together after rebuilding coverage.
The thumbnail omits `file:size` and `file:checksum` until its bytes are published.
The data asset hashes describe files already in the public bucket.

### Data links in metadata checkouts

Data files remain outside Git.
`CI_LIGHT=1` exempts data asset hrefs and a `rel:pmtiles` link when a visual asset declares the same href and both use `application/vnd.pmtiles`.
The href must end in `.pmtiles`.
Undeclared PMTiles links and missing structural links, styles, or thumbnails still fail.
`tests/test_link_contract.py` checks these boundaries, including a structural link disguised with a data suffix.

Without `CI_LIGHT`, both the PMTiles link and its asset must exist locally.
These CI checks validate catalog structure rather than remote availability.
They change neither Portolan rules nor the validator allow-list.

<!--
When accepting a deviation, add a row and a section explaining it, using this format:

| Rule | Where | Why accepted | Tracking |
|---|---|---|---|
| PTL-VIZ-001 | all thumbnails | WebP is not yet permitted; the size saving is 4x | portolan-spec#121 |

Add the same rule ID to ACCEPTED in tests/test_conformance.py.
Both changes are required.
-->

## Validator workarounds

### stac-check dialect error

`tests/test_stac_valid.py` exempts this stac-check failure:

```
'list' object has no attribute 'get'
[Schema: https://schemas.portolan-sdi.org/portolan/vX.Y.Z/schema.json]. Error in Extensions.
```

The Portolan schema uses valid draft-07 syntax, which rashid validates successfully.
The underlying `stac-validator` library uses the JSON Schema 2020-12 dialect and ignores the declared `$schema`.
It crashes on the draft-07 tuple form of `items` in `valid_bbox`, whose meaning differs under 2020-12.
The issue is tracked in [stac-check#159](https://github.com/stac-utils/stac-check/issues/159) and [portolan-spec#157](https://github.com/portolan-sdi/portolan-spec/issues/157).

The exemption applies only to the exact message above when the failing schema is a Portolan profile schema.
Other stac-check errors fail the build.
The check prints how many objects received the exemption.

The check also fails when stac-check stops reporting this crash for a collection or item declaring the profile schema.
That failure requests removal of the exemption and this section.
CI installs stac-check without a version pin so an upstream fix triggers this check.

If stac-check is absent, the check fails and prints the install command.
Unlike the rashid check, it asserts no named stac-check rules and sets no version floor.
A version pin would retain the workaround after an upstream fix.
