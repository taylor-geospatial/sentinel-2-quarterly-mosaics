#!/usr/bin/env python3
"""Template-setup gate. Delete this file once your catalog is real.

Three states, and only one of them is a failure:

- Untouched template: passes, and prints the SETUP.md checklist. A fresh
  clone is green, so your first pull request is not fighting the scaffolding.
- Half-edited: fails, naming the inconsistency. A real bucket with a sentinel
  public base publishes hrefs nobody can resolve, and that is worth catching.
- Finished: passes silently.

It also asserts the structural invariants a hand-edit can break.

Run: python3 tests/test_setup.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from publish import SENTINELS, load_config, unedited_sentinels  # noqa: E402

errors: list[str] = []


def err(message: str) -> None:
    errors.append(message)


config = load_config()
publish_dir = ROOT / config["publish_dir"]
root_json = publish_dir / "catalog.json"

# --- structural invariants, checked in every state ----------------------
if not publish_dir.is_dir():
    err(f"publish_dir names no directory: {config['publish_dir']}")
if not root_json.is_file():
    err(f"{config['publish_dir']}/catalog.json is missing")
for name in ("README.md", "AGENTS.md"):
    if not (publish_dir / name).is_file():
        err(f"{config['publish_dir']}/{name} is missing")

if root_json.is_file():
    doc = json.loads(root_json.read_text())
    if "assets" in doc:
        err("a Catalog carries no assets; move them onto a Collection")
    rels = [link.get("rel") for link in doc.get("links", [])]
    if "self" in rels:
        err("Portolan forbids a self link; a static catalog must be movable")
    if "root" not in rels:
        err("the root catalog has no rel:root link")
else:
    doc = {}

# --- placeholder state --------------------------------------------------
stale_config = unedited_sentinels(config)
catalog_untouched = doc.get("id") == "example-catalog"
# SETUP.md is excluded: it documents the marker, so it always matches itself,
# and step 9 has you delete it anyway.
todos = sorted(
    p.relative_to(ROOT).as_posix()
    for p in ROOT.rglob("*")
    if p.is_file()
    and p.suffix in {".md", ".json", ".yaml"}
    and ".git" not in p.parts
    and p.name != "SETUP.md"
    and "TODO(setup)" in p.read_text(errors="ignore")
)

fresh = len(stale_config) == len(SENTINELS) and catalog_untouched
done = not stale_config and not catalog_untouched and not todos

if not (fresh or done) and not errors:
    err(
        "setup is half-finished: "
        + (f"config sentinels left: {stale_config}. " if stale_config else "")
        + ("catalog.json still has id 'example-catalog'. "
           if catalog_untouched else "")
        + (f"TODO(setup) in: {', '.join(todos)}" if todos else "")
    )

if errors:
    print("\n".join(f"error  {e}" for e in errors))
    raise SystemExit(1)

if fresh:
    print("This is an unedited template. Work through SETUP.md, then delete")
    print("this file and drop it from tests/run_all.py.")
raise SystemExit(0)
