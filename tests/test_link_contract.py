#!/usr/bin/env python3
"""CI_LIGHT may skip declared PMTiles data, never broken catalog structure."""

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from test_links import check_links


class LinkContract(unittest.TestCase):
    def setUp(self):
        self.doc: dict = {
            "type": "Collection",
            "assets": {
                "footprints": {
                    "href": "./footprints.pmtiles",
                    "type": "application/vnd.pmtiles",
                    "roles": ["visual"],
                },
            },
            "links": [
                {
                    "rel": "pmtiles",
                    "href": "./footprints.pmtiles",
                    "type": "application/vnd.pmtiles",
                }
            ],
        }

    def errors(self, doc, ci_light=True):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
            base = Path(tmp)
            (base / "collection.json").write_text(json.dumps(doc))
            return check_links(base, ci_light)

    def test_declared_pmtiles_is_data_in_ci(self):
        self.assertEqual(self.errors(self.doc), [])

    def test_full_check_requires_pmtiles_bytes(self):
        errors = self.errors(self.doc, ci_light=False)
        self.assertEqual(len(errors), 2)
        self.assertIn("rel:pmtiles", errors[0])
        self.assertIn("asset footprints", errors[1])

    def test_structural_links_are_never_exempt(self):
        for rel in ("child", "item", "root", "parent", "describedby", "agents"):
            with self.subTest(rel=rel):
                doc = copy.deepcopy(self.doc)
                doc["links"][0]["rel"] = rel
                self.assertEqual(len(self.errors(doc)), 1)

    def test_pmtiles_requires_matching_visual_asset(self):
        for field, value in (
            ("href", "./other.pmtiles"),
            ("type", "application/octet-stream"),
            ("roles", ["data"]),
        ):
            with self.subTest(field=field):
                doc = copy.deepcopy(self.doc)
                doc["assets"]["footprints"][field] = value
                self.assertEqual(len(self.errors(doc)), 1)
        self.doc["assets"] = {}
        self.assertEqual(len(self.errors(self.doc)), 1)

    def test_pmtiles_requires_data_suffix_and_type(self):
        for field, value in (("href", "./missing.json"), ("type", "application/json")):
            with self.subTest(field=field):
                doc = copy.deepcopy(self.doc)
                doc["links"][0][field] = value
                doc["assets"]["footprints"][field] = value
                self.assertTrue(any("rel:pmtiles" in e for e in self.errors(doc)))

    def test_thumbnails_and_styles_still_require_files(self):
        for href in ("./thumbnail.webp", "./styles/default.json"):
            with self.subTest(href=href):
                doc = copy.deepcopy(self.doc)
                doc["assets"]["other"] = {"href": href}
                self.assertEqual(len(self.errors(doc)), 1)


if __name__ == "__main__":
    unittest.main()
