#!/usr/bin/env python3
"""Run every gate. Exit non-zero if any fails.

The list is explicit rather than globbed so that the gates are visible in one
place and adding one is a deliberate edit. Run from anywhere:

    python3 tests/run_all.py
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

TESTS = [
    "test_setup.py",       # delete this one once setup is done
    "test_links.py",
    "test_publish.py",
    "test_upload_data.py",
    "test_stac_valid.py",
    "test_conformance.py",
]

failed = []
for name in TESTS:
    path = HERE / name
    if not path.exists():
        continue
    print(f"\n=== {name} " + "=" * (60 - len(name)), flush=True)
    if subprocess.run([sys.executable, str(path)]).returncode != 0:
        failed.append(name)

print()
if failed:
    print("FAILED: " + ", ".join(failed))
    raise SystemExit(1)
print("all gates passed")
