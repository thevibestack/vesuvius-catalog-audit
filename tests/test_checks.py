"""Tests for the checks, over hand-built inventory documents (no network)."""

import unittest

from vcaudit import checks, report


def mesh(dir_, kind, h, w, scale, in_catalogue=True):
    return {"dir": dir_, "kind": kind, "in_catalogue": in_catalogue, "catalogue_types": ["tifxyz-normalized"],
            "meta": {"scale": scale, "format": "tifxyz"}, "meta_error": None,
            "header": {"height": h, "width": w, "bits": 32, "samples": 1, "tiff_version": 42,
                       "compression": 1, "ifd_offset": 8, "n_entries": 10},
            "header_error": None}


def volume(dir_, canvas_wh, level0_shape, source_group=0, levels=None, units=("micrometer",) * 3,
           scale0=(2.401, 2.401, 2.401)):
    return {
        "dir": dir_, "in_catalogue": True, "catalogue_types": ["layers-zarr"],
        "zattrs": {
            "canvas_size": list(canvas_wh), "source_group": source_group,
            "source_zarr": "/volumes/x.zarr/",
            "multiscales": [{
                "axes": [{"name": n, "type": "space", "unit": u} for n, u in zip("zyx", units)],
                "datasets": [{"coordinateTransformations": [{"type": "scale", "scale": list(scale0)}]}],
            }],
        },
        "zattrs_error": None,
        "level0": {"shape": list(level0_shape), "chunks": [109, 128, 128], "dtype": "|u1", "fill_value": 0},
        "level0_error": None,
        "levels": levels if levels is not None else [
            {"level": 0, "exists": True, "n_data_keys": 100, "conclusive": True, "shape": list(level0_shape)},
            {"level": 1, "exists": True, "n_data_keys": 25, "conclusive": True, "shape": None},
        ],
    }


SEG = "PHercTEST/segments/20250101000000"


def inventory(meshes, volumes, prefix=SEG):
    return {
        "segments": [{"prefix": prefix, "sample": prefix.split("/")[0], "in_catalogue": True,
                      "errors": [], "meshes": meshes, "volumes": volumes}],
        "stats": {"segments": 1, "meshes": len(meshes), "volumes": len(volumes), "errors": 0},
    }


class Test1727(unittest.TestCase):
    def test_same_frame_only_fails_but_cross_scan_variant_reproduces(self):
        # A 4x cross-scan render: the same-frame grid is way too small, and the
        # published cross-scan variant matches exactly.
        same = mesh(f"{SEG}/mesh/intermediate/tifxyz_normalized", "tifxyz_normalized",
                    257, 205, [0.05, 0.05])
        cross = mesh(f"{SEG}/mesh/20250101000000-on-20250820154339-2.401um.tifxyz", "transformed",
                     926, 738, [0.05000000074505806, 0.05000000074505806])
        vol = volume(f"{SEG}/surface-volumes/2.401um-0.35m-77keV-volume-20250820154339.zarr",
                     (14760, 18520), (109, 18520, 14760))
        rows, pairs = checks.check_1727_canvas(inventory([same, cross], [vol]))
        self.assertEqual(len(pairs), 2)
        r = rows[0]
        self.assertEqual(r["scope_same_frame_verdict"], checks.NOT_REPRODUCED)
        self.assertEqual(r["scope_all_verdict"], checks.REPRODUCED_MODEL)
        self.assertEqual(r["scope_all_best_kind"], "transformed")
        self.assertTrue(r["canvas_matches_level0"])
        s = checks.summarize_1727(rows, pairs)
        self.assertEqual(s["gained_from_all_variants"], 1)
        self.assertEqual(s["gained_by_variant_kind"], {"transformed": 1})
        self.assertEqual(s["still_not_reproduced"], 0)

    def test_no_mesh_variants(self):
        vol = volume(f"{SEG}/surface-volumes/9.362um-1.2m-113keV-volume-20250804134230.zarr",
                     (6800, 7280), (28, 7280, 6800))
        rows, _ = checks.check_1727_canvas(inventory([], [vol]))
        self.assertEqual(rows[0]["verdict"], checks.NO_MESH)
        self.assertEqual(summarize(rows)["by_scope"]["all"]["volumes_tested"], 0)

    def test_mesh_without_header_is_not_counted_as_a_pair(self):
        broken = mesh(f"{SEG}/mesh/intermediate/tifxyz_original", "tifxyz_original", 0, 0, [0.05, 0.05])
        broken["header"] = None
        broken["header_error"] = "S3Error: boom"
        vol = volume(f"{SEG}/surface-volumes/2.401um-0.35m-77keV-volume-20250820154339.zarr",
                     (14760, 18520), (109, 18520, 14760))
        rows, pairs = checks.check_1727_canvas(inventory([broken], [vol]))
        self.assertEqual(pairs, [])
        self.assertEqual(rows[0]["verdict"], checks.NO_MESH)

    def test_canvas_disagreeing_with_level0_is_flagged(self):
        m = mesh(f"{SEG}/mesh/intermediate/tifxyz_normalized", "tifxyz_normalized", 926, 738, [0.05, 0.05])
        vol = volume(f"{SEG}/surface-volumes/x.zarr", (14760, 18520), (109, 1, 1))
        rows, _ = checks.check_1727_canvas(inventory([m], [vol]))
        self.assertFalse(rows[0]["canvas_matches_level0"])


def summarize(rows):
    return checks.summarize_1727(rows, [])


class Test1892(unittest.TestCase):
    def test_detects_a_pyramid_with_no_chunks(self):
        vol = volume(f"{SEG}/surface-volumes/1.129um-0.22m-59keV-volume-20260521123630-L1.zarr",
                     (28170, 30120), (116, 30120, 28170), source_group=1,
                     levels=[{"level": lv, "exists": True, "n_data_keys": 0, "conclusive": True,
                              "shape": None, "keys_seen": 1} for lv in range(6)])
        rows = checks.check_1892_empty_pyramid(inventory([], [vol]))
        self.assertEqual(rows[0]["verdict"], "no_chunks_at_any_level")
        self.assertEqual(rows[0]["empty_levels"], "0,1,2,3,4,5")
        self.assertEqual(rows[0]["levels_empty"], 6)
        self.assertEqual(rows[0]["level0_shape_zyx"], "116,30120,28170")

    def test_populated_pyramid_passes(self):
        vol = volume(f"{SEG}/surface-volumes/x.zarr", (100, 200), (10, 200, 100))
        rows = checks.check_1892_empty_pyramid(inventory([], [vol]))
        self.assertEqual(rows[0]["verdict"], "populated")

    def test_inconclusive_listing_is_not_called_empty(self):
        vol = volume(f"{SEG}/surface-volumes/x.zarr", (100, 200), (10, 200, 100),
                     levels=[{"level": 0, "exists": True, "n_data_keys": 0, "conclusive": False,
                              "shape": None, "keys_seen": 8}])
        rows = checks.check_1892_empty_pyramid(inventory([], [vol]))
        self.assertEqual(rows[0]["verdict"], "inconclusive")


class Test1893(unittest.TestCase):
    def test_micrometre_value_labelled_nanometer(self):
        vol = volume(f"{SEG}/surface-volumes/9.362um-1.2m-113keV-volume-20250521123630.zarr",
                     (6800, 7280), (28, 7280, 6800),
                     units=("nanometer",) * 3, scale0=(9.362, 9.362, 9.362))
        rows = checks.check_1893_voxel_unit(inventory([], [vol]))
        self.assertEqual(rows[0]["verdict"], "micrometre_value_labelled_nanometer")
        self.assertEqual(rows[0]["factor_if_converted"], 1000)

    def test_consistent_volume_passes(self):
        vol = volume(f"{SEG}/surface-volumes/2.401um-0.35m-77keV-volume-20250820154339.zarr",
                     (14760, 18520), (109, 18520, 14760))
        rows = checks.check_1893_voxel_unit(inventory([], [vol]))
        self.assertEqual(rows[0]["verdict"], "consistent")


class TestCatalogueChecks(unittest.TestCase):
    def test_1734_marks_the_scaled_sentinel(self):
        segment = {
            "id": "20260701183124",
            "original_volume_id": "20260411134726",
            "creation": {"date": "2026-07-01T18:31:24Z",
                         "metadata": {"bbox": [[-1.0, -1.0, -1.0], [100.0, 200.0, 300.0]]}},
            "properties": {
                "original_volume_downscale": 4,
                "volume_coverage": {"20260411134726": {
                    "bbox_transformed": [[-4.0, -4.0, -4.0], [400.0, 800.0, 1200.0]],
                    "overlap_ratio": 0.9996}},
            },
        }
        cat = {"samples": {"PHercParis4": {"scans": {}, "volumes": {},
                                           "segments": {"20260701183124": segment}}}}
        rows = checks.check_1734_bbox_marker(cat)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["exact_scaled_marker"])
        self.assertEqual(rows[0]["marker_axes"], "xyz")

    def test_1730_accepts_an_offset_with_a_z(self):
        cat = {"samples": {"PHerc0500P2": {
            "scans": {"S1": {"creation": {"date": "2025-07-01T00:00:00Z"}}},
            "volumes": {"V1": {"scan_id": "S1"}},
            "segments": {"20250611171318": {
                "id": "20250611171318", "original_volume_id": "V1",
                "creation": {"date": "2025-06-11T15:42:56+00:00Z", "metadata": {}},
                "properties": {},
            }},
        }}}
        rows = checks.check_1730_scan_after_segment(cat)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["scan_is_later_days"], 19)

    def test_1730_ignores_segments_created_after_the_scan(self):
        cat = {"samples": {"X": {
            "scans": {"S1": {"creation": {"date": "2025-01-01T00:00:00Z"}}},
            "volumes": {"V1": {"scan_id": "S1"}},
            "segments": {"20250102000000": {
                "id": "20250102000000", "original_volume_id": "V1",
                "creation": {"date": "2025-01-02T00:00:00Z", "metadata": {}}, "properties": {}}},
        }}}
        self.assertEqual(checks.check_1730_scan_after_segment(cat), [])


class TestReport(unittest.TestCase):
    def test_csv_rows_round_trip_without_crashing_on_none_and_float(self):
        import os
        import tempfile

        rows = [{"a": 1, "b": None, "c": float("nan"), "d": 0.05000000074505806, "e": [1, 2]}]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x.csv")
            report.write_csv(path, rows)
            with open(path) as fh:
                text = fh.read().splitlines()
        self.assertEqual(text[0], "a,b,c,d,e")
        self.assertEqual(text[1], "1,,nan,0.05,1;2")


if __name__ == "__main__":
    unittest.main()
