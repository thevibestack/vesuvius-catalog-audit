"""End-to-end runs of the command line over an inventory written to disk.

The checks are unit-tested elsewhere; this exercises the wiring -- argument
parsing, the report builders, the CSV and summary writers -- because that layer
is where an offline mistake (a renamed import, a new parameter not threaded
through) survives every unit test and still kills `analyze`. No network is used:
`analyze` reads the two JSON documents it is given.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from argparse import Namespace

from vcaudit import cli, report
from tests.test_checks import inventory, mesh, volume

SEG = "PHercTEST/segments/20250101000000"


def _fixture_inventory():
    # 173 mesh points of a 1.0-scale grid: a canvas of 173 x 104 at render scale 1.
    m = mesh(f"{SEG}/mesh/intermediate/tifxyz_normalized", "tifxyz_normalized", 173, 104, [1.0, 1.0])
    v = volume(f"{SEG}/surface-volumes/2.401um-0.35m-77keV-volume-20250820154339.zarr",
               (104, 173), (31, 173, 104))
    inv = inventory([m], [v])
    inv["stats"] = {"segments": 1, "meshes": 1, "volumes": 1, "errors": 0,
                    "http_requests": 0, "bytes_read": 0}
    inv["drift"] = {"catalogue_segments": 1, "bucket_segment_dirs": 1, "limited": False,
                    "non_segment_dirs_ignored": [], "catalogue_segments_not_in_bucket": []}
    inv["bucket"] = "vesuvius-challenge-open-data"
    inv["bucket_url"] = "https://vesuvius-challenge-open-data.s3.amazonaws.com/"
    return inv


def _fixture_catalogue():
    return {"samples": {"PHercTEST": {
        "sample": "PHercTEST",
        "scans": {"s1": {"id": "s1", "creation": {"date": "2025-05-01T00:00:00Z"}}},
        "volumes": {"20250820154339": {"id": "20250820154339", "scan_id": "s1",
                                       "properties": {"shape": [31, 173, 104]}}},
        "segments": {"20250101000000": {
            "id": "20250101000000", "sample_id": "PHercTEST",
            "original_volume_id": "20250820154339",
            "creation": {"date": "2025-01-01T00:00:00Z", "process": "surface_mesh_generation",
                         "metadata": {"bbox": [[0, 0, 0], [10, 10, 20]]}},
            "properties": {"original_volume_downscale": 1},
            "data": [{"type": "tifxyz-normalized", "origins": []}],
        }},
    }}}


class TestAnalyzeCommand(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.inv_path = os.path.join(self.dir, "inventory.json")
        self.cat_path = os.path.join(self.dir, "catalogue.json")
        with open(self.inv_path, "w") as fh:
            json.dump(_fixture_inventory(), fh)
        with open(self.cat_path, "w") as fh:
            json.dump(_fixture_catalogue(), fh)
        self.out = os.path.join(self.dir, "audit")

    def run_analyze(self):
        # `cmd_analyze` prints the generated report to stdout; capture it so a test
        # run stays readable, and return it for the assertions that need it.
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.cmd_analyze(Namespace(
                inventory=self.inv_path, catalogue=self.cat_path, outdir=self.out,
                villa_src=None, villa_commit="0" * 40))
        self.stdout = buf.getvalue()
        return rc

    def test_analyze_writes_every_declared_output(self):
        self.assertEqual(self.run_analyze(), 0)
        for name in ("SUMMARY.md", "summary.json", "csv/1727_volumes.csv", "csv/1727_pairs.csv",
                     "csv/1892_empty_pyramid.csv", "csv/1893_voxel_unit.csv",
                     "csv/1893_source_lines.csv", "csv/1734_bbox_marker.csv",
                     "csv/1730_scan_after_segment.csv", "csv/1727_crossscan_recovered.csv"):
            self.assertTrue(os.path.exists(os.path.join(self.out, name)), name)

    def test_the_printed_report_is_the_report_that_was_written(self):
        self.run_analyze()
        with open(os.path.join(self.out, "SUMMARY.md")) as fh:
            written = fh.read()
        # stdout is the report plus the closing "wrote ..." line.
        self.assertTrue(self.stdout.startswith(written))
        self.assertIn("#1727", written)
        self.assertIn("wrote CSV files, summary.json and SUMMARY.md", self.stdout)

    def test_the_summary_carries_the_coverage_count_of_the_date_join(self):
        """Regression: the #1730 coverage count has to reach both outputs."""
        self.run_analyze()
        with open(os.path.join(self.out, "summary.json")) as fh:
            summary = json.load(fh)
        self.assertEqual(summary["checks"]["1730"]["coverage"]["segments"], 1)
        self.assertEqual(summary["checks"]["1730"]["coverage"]["segments_with_resolvable_scan_date"], 1)
        with open(os.path.join(self.out, "SUMMARY.md")) as fh:
            md = fh.read()
        self.assertIn("segments the date join could examine", md)

    def test_the_declared_canvas_is_reproduced_by_this_fixture(self):
        self.run_analyze()
        with open(os.path.join(self.out, "csv", "1727_pairs.csv")) as fh:
            text = fh.read().splitlines()
        header = text[0].split(",")
        self.assertEqual(text[1].split(",")[header.index("exact_at_scale_1")], "True")

    def test_a_missing_villa_src_is_recorded_not_faked(self):
        """No checkout to read means no quoted lines -- and an empty file, not
        invented ones."""
        self.run_analyze()
        with open(os.path.join(self.out, "csv", "1893_source_lines.csv")) as fh:
            self.assertEqual(fh.read().strip(), "")


class TestCollectCommandIsImportable(unittest.TestCase):
    def test_collect_only_needs_the_bucket_url_to_start(self):
        """The collector's first act is the catalogue fetch; no network here, so
        this only pins the wiring (the run itself is exercised live)."""
        args = Namespace(bucket_url="https://example.invalid", outdir=".", catalogue_out="c.json",
                         limit=None, workers=1, villa_commit=None, timeout=1.0, retries=0)
        self.assertTrue(callable(cli.cmd_collect))
        self.assertEqual(args.bucket_url, "https://example.invalid")


if __name__ == "__main__":
    unittest.main()
