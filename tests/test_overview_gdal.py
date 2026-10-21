#!/usr/bin/env python3
"""Overview warps preserve completed output and never publish partial files."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import overview_gdal  # noqa: E402


class OverviewWarpTests(unittest.TestCase):
    def test_existing_zone_is_reused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "zone.tif"
            dest.write_bytes(b"completed zone")
            with patch.object(overview_gdal.subprocess, "run") as run:
                result = overview_gdal.warp_zone((32631, Path("source.vrt"), dest, 300))
            self.assertEqual(result, (32631, 0.0, ""))
            run.assert_not_called()
            self.assertEqual(dest.read_bytes(), b"completed zone")

    def test_retry_publishes_only_successful_warp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "zone.tif"
            attempts = []

            def warp(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
                self.assertFalse(dest.exists())
                output = Path(cmd[-1])
                self.assertFalse(output.exists())
                attempts.append(cmd)
                failed = len(attempts) == 1
                output.write_bytes(b"partial" if failed else b"completed zone")
                return subprocess.CompletedProcess(
                    cmd,
                    int(failed),
                    stdout="",
                    stderr="truncated read" if failed else "",
                )

            with (
                patch.object(overview_gdal.subprocess, "run", side_effect=warp),
                patch.object(overview_gdal.time, "sleep"),
            ):
                epsg, _, error = overview_gdal.warp_zone(
                    (32631, Path("source.vrt"), dest, 300)
                )
            self.assertEqual((epsg, error), (32631, ""))
            self.assertEqual(len(attempts), 2)
            self.assertEqual(dest.read_bytes(), b"completed zone")
            self.assertFalse(dest.with_name(f".{dest.name}.tmp").exists())

    def test_exhausted_retries_leave_no_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "zone.tif"

            def fail(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
                Path(cmd[-1]).write_bytes(b"partial")
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="bad read")

            with (
                patch.object(overview_gdal.subprocess, "run", side_effect=fail) as run,
                patch.object(overview_gdal.time, "sleep"),
            ):
                _, _, error = overview_gdal.warp_zone(
                    (32631, Path("source.vrt"), dest, 300)
                )
            self.assertEqual(error, "bad read")
            self.assertEqual(run.call_count, overview_gdal.ZONE_ATTEMPTS)
            self.assertFalse(dest.exists())
            self.assertFalse(dest.with_name(f".{dest.name}.tmp").exists())


if __name__ == "__main__":
    unittest.main()
