#!/usr/bin/env python3
"""STAC validity, via stac-check.

Validates every STAC object under the published directory against the official
STAC 1.1.0 schemas. This answers a different question from rashid: stac-check
asks whether the JSON is valid STAC, rashid asks whether the catalog conforms
to Portolan. A catalog can pass one and fail the other, so both run.

Three notes on how this is wired:

- Per-file, with recursive=False. stac-validator's recursive mode trips over
  relative links in a directory tree, and per-file gives a usable error anyway.
- Best-practice notes print as warnings and never fail the build. Some of them
  contradict Portolan on purpose: stac-check recommends a rel:'self' link,
  which Portolan forbids, because a static catalog that hardcodes its own
  location cannot be mirrored or moved. rashid is the gate; this is advisory.
- One stac-check failure is exempted, and the exemption expires by itself. See
  below and docs/conformance.md.

## The exempted failure

stac-validator hardcodes the JSON Schema 2020-12 dialect and ignores the
$schema a schema declares. The Portolan profile schema declares draft-07 and
uses the draft-07 tuple form of `items` in `valid_bbox`. Under 2020-12 that
keyword takes a single schema, so the library hands a list to code expecting an
object and raises:

    'list' object has no attribute 'get'
    [Schema: https://schemas.portolan-sdi.org/portolan/vX.Y.Z/schema.json].
    Error in Extensions.

The schema is correct draft-07 and rashid validates it cleanly, so nothing on
the Portolan side changes. Tracked upstream at
https://github.com/stac-utils/stac-check/issues/159.

The exemption is narrow: that exact message, and only when the failing schema
is a Portolan profile schema. Every other stac-check error still fails.

## Why it expires

An exemption that outlives its bug is indistinguishable from a catalog that
never had the problem. So this gate also fails when the crash STOPS happening
on an object the bug can reach — stac-check has been fixed, and the exemption
has to go.

"Reachable" is exact: stac-validator extension-validates collections and items
only (validate.py, `stac_upper == "ITEM" or stac_upper == "COLLECTION"`).
Catalogs get the core schema alone and never crash. A catalog with no
collections yet has nothing reachable, so the expiry check stays quiet.

CI installs stac-check unpinned on purpose. That is what makes this work: the
next stac-check release flips this gate without anyone watching for it.

Fails when stac-check is absent. A skip reports a green run for objects
that no validator read. The failure message gives the install command.

Run: python3 tests/test_stac_valid.py
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from publish import load_config  # noqa: E402

INSTALL = "python -m pip install stac-check"


def fail(message: str) -> None:
    """Report the problem, name the fix, and exit non-zero."""
    print(f"error  {message}")
    print(f"       install stac-check with: {INSTALL}")
    raise SystemExit(1)


# No version floor, and no pin. The conformance gate takes a rashid floor
# because it asserts named rules. This gate asserts no stac-check rule. It
# depends on the opposite property. The exemption below expires when a new
# stac-check stops emitting the crash, so CI must take each new release. A pin
# or a floor with an upper bound holds the exemption open after the fix ships.
try:
    from stac_check.lint import Linter
except ImportError:
    fail("stac-check is not installed, so this gate checks nothing")

config = load_config()
BASE = ROOT / config["publish_dir"]

STAC_TYPES = {"Catalog", "Collection", "Feature"}

# stac-validator applies declared extension schemas to these types only.
EXTENSION_VALIDATED = {"Collection", "Feature"}

UPSTREAM_ISSUE = "https://github.com/stac-utils/stac-check/issues/159"

DIALECT_CRASH = "'list' object has no attribute 'get'"

# Any profile version. A vX.Y.Z bump must not silently turn the exemption into
# a hard failure that reads as "upstream is fixed".
PROFILE_SCHEMA = re.compile(
    r"https://schemas\.portolan-sdi\.org/portolan/v\d+\.\d+\.\d+/schema\.json"
)


def is_dialect_crash(message: str) -> bool:
    """The known stac-validator dialect crash on a Portolan profile schema."""
    return DIALECT_CRASH in message and bool(PROFILE_SCHEMA.search(message))


def declares_profile(doc: dict) -> bool:
    extensions = doc.get("stac_extensions")
    if not isinstance(extensions, list):
        return False
    return any(
        isinstance(uri, str) and PROFILE_SCHEMA.fullmatch(uri.rstrip("#"))
        for uri in extensions
    )


errors: list[str] = []
exempted: list[str] = []
reachable: list[str] = []
checked = 0

for path in sorted(BASE.rglob("*.json")):
    rel = path.relative_to(BASE)
    if any(part.startswith(".") for part in rel.parts):
        continue
    if path.name.endswith(".style.json") or "styles" in path.parts:
        continue
    try:
        doc = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        errors.append(f"{rel}: invalid JSON ({exc})")
        continue
    if not isinstance(doc, dict) or doc.get("type") not in STAC_TYPES:
        continue

    checked += 1
    if doc["type"] in EXTENSION_VALIDATED and declares_profile(doc):
        reachable.append(str(rel))

    linter = Linter(str(path), recursive=False)
    if not linter.valid_stac:
        if is_dialect_crash(linter.error_msg):
            exempted.append(str(rel))
            continue
        errors.append(f"{rel}: {linter.error_msg}")
        continue
    for note in linter.best_practices_msg[1:]:
        if note.strip():
            print(f"note   {rel}: {note.strip()}")

if exempted:
    for rel_str in exempted:
        print(f"exempt {rel_str}: known stac-check dialect crash")
    print(
        f"exempt {len(exempted)} object(s) carried the exemption for {UPSTREAM_ISSUE}"
    )

# The crash stopped on objects it can reach: stac-check is fixed.
expired = bool(reachable) and not exempted
if expired:
    print(
        f"error  stac-check no longer emits the Portolan dialect crash on "
        f"{len(reachable)} reachable object(s). Upstream is fixed. Delete the "
        f"exemption in tests/test_stac_valid.py and the section in "
        f"docs/conformance.md. Tracking: {UPSTREAM_ISSUE}"
    )

if errors:
    print("\n".join(f"error  {e}" for e in errors))

if errors or expired:
    raise SystemExit(1)
print(f"OK: {checked} STAC object(s) valid")
