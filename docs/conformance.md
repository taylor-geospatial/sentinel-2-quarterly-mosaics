# Portolan Conformance

Conformance means passing [rashid](https://github.com/portolan-sdi/rashid),
not claiming to conform, so it runs in CI:

```bash
python3 tests/test_conformance.py
```

That gate fails on any error-severity finding whose rule is not listed below.
The list starts empty and it must never grow without a row here. A known
deviation with an issue number is a debt someone can pay off. A silently
widened allow-list is a false claim about what this catalog conforms to.

## The rashid version floor

The gate needs rashid `>=0.1.8,<0.2.0`. It reads `rashid --version` and fails
outside that range. It also fails when rashid is absent, and prints the install
command. A skip would report a green run for a catalog that no validator read.

The floor is 0.1.8 because that is the first rashid that accepts the absolute
root `self` link Portolan schema v0.2.0 recommends (PORTO-CORE-081). This
catalog carries that link, so an older rashid fails a root that is correct.
Rules PTL-LNK-007, PTL-LNK-008, PTL-LNK-009 and PTL-AST-006 arrived in 0.1.5
and the gate asserts all four; an older rashid reports a pass for a catalog it
never checked against them. The same range is in the CI install step.

The upper bound stops an unreviewed 0.2 rule set from changing what this gate
means. Raise both bounds together when you move to 0.2, and read the new rules
first.

This file also records workarounds for the other validator CI runs. Those are
not conformance debts, because the catalog is correct and the validator is not.
They live here so nobody has to read CI code to find out why a gate skips
something.

## Accepted deviations

None. `ACCEPTED` in `tests/test_conformance.py` is still empty, and the section
below is deliberately not an acceptance.

## Open gaps, not accepted

Two gates fail today. Both have the same cause: the `coverage` collection
declares the structure of data that the backfill has not produced yet. Neither
is in `ACCEPTED`, because widening the allow-list would turn "this catalog is
half-built" into "this catalog conforms", and nothing downstream could tell the
difference.

| Gate | Finding | Cause | Closes in |
|---|---|---|---|
| `test_links.py` | `coverage/collection.json: rel:pmtiles -> ./footprints.pmtiles does not exist` | The footprints tileset is not built yet. The link is required by PTL-VIZ-003 once the asset is declared, and the link gate has no data-suffix exemption for links, only for assets. | Phase 4, when `footprints.pmtiles` is generated and uploaded |
| `test_conformance.py` | `PTL-VIZ-001 coverage/collection.json: geospatial collection has no asset with the 'thumbnail' role` | A thumbnail for this collection has to be rendered from its own coverage statistics, which do not exist yet. Rendering something else would misrepresent what the collection shows. | Phase 4, with the styles and thumbnail pass |

`mosaics` passes both gates. Its thumbnail is rendered from published COGs, and
it declares no visualization derivative it cannot back.

The alternative was to hold the `coverage` collection out of `catalog/` until
its bytes exist, which would make CI green today at the cost of publishing no
contract for the table. Declaring the contract early is worth more than a green
check on a catalog that is openly mid-build, so long as the failures stay
visible. When Phase 4 lands, both rows here are deleted, not moved into
`ACCEPTED`.

<!--
When you accept one, add a row and a section explaining it, like this:

| Rule | Where | Why accepted | Tracking |
|---|---|---|---|
| PTL-VIZ-001 | all thumbnails | WebP is not yet permitted; the size saving is 4x | portolan-spec#121 |

Then add the rule id to ACCEPTED in tests/test_conformance.py. Both, or
neither.
-->

## Validator workarounds

### stac-check reports a dialect crash on every collection

`tests/test_stac_valid.py` exempts one stac-check failure:

```
'list' object has no attribute 'get'
[Schema: https://schemas.portolan-sdi.org/portolan/vX.Y.Z/schema.json]. Error in Extensions.
```

The Portolan schema is valid draft-07, and rashid validates catalogs against it
cleanly. `stac-validator`, which stac-check uses, hardcodes the JSON Schema
2020-12 dialect and ignores the `$schema` a schema declares. The profile schema
uses the draft-07 tuple form of `items` in `valid_bbox`, which means something
different under 2020-12, so the library raises instead of validating.

Tracked upstream at <https://github.com/stac-utils/stac-check/issues/159>,
and on the Portolan side at
<https://github.com/portolan-sdi/portolan-spec/issues/157>.

The exemption matches that exact message, and only when the failing schema is a
Portolan profile schema. Every other stac-check error still fails the build,
and the gate prints how many objects took the exemption.

The exemption expires on its own. The gate fails once stac-check stops emitting
the crash on a collection or item that declares the profile schema, and tells
you to delete both the exemption and this section. CI installs stac-check
unpinned, so the next release triggers that without anyone watching for it.

`tests/test_stac_valid.py` also fails when stac-check is absent, and prints the
install command. It takes no version floor and no pin. The rashid floor exists
because that gate asserts four named rules. This gate asserts no stac-check
rule. It needs the opposite property. A pin holds the exemption open after the
upstream fix ships.
