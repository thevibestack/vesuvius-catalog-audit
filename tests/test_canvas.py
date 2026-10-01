"""Tests for the canvas formula and its inversion.

The two published cases below are real: they are the (mesh variant, surface
volume) pairs used in the round-0 measurement and in issue #1727's example, kept
here as regression anchors so a change in the arithmetic is caught rather than
discovered in the report.
"""

import math
import random
import unittest

from vcaudit import canvas


class TestRounding(unittest.TestCase):
    def test_lround_is_half_away_from_zero(self):
        self.assertEqual(canvas.lround(2.5), 3)
        self.assertEqual(canvas.lround(-2.5), -3)
        self.assertEqual(canvas.lround(2.4), 2)
        self.assertEqual(canvas.lround(0.5), 1)

    def test_f32_matches_cpp_float(self):
        self.assertEqual(canvas.f32(0.05), 0.05000000074505806)
        self.assertEqual(canvas.f32(0.05000000074505806), 0.05000000074505806)

    def test_f32_scale_changes_the_result_where_float64_would_not(self):
        # villa #1699: 0.05 stored as float32 is slightly above 0.05, so a
        # float64 division lands one pixel short of the renderer's canvas.
        stored, target = 4040, 4299
        renderer = canvas.renderer_size((stored, stored), (0.05, 0.05), 1.0)
        self.assertEqual(renderer[0], canvas.lround(stored / canvas.f32(0.05)))


class TestPublishedCases(unittest.TestCase):
    def test_phoerc0139_scale_1_pair(self):
        # reference measurement, pairs.json: mesh 354x331 @0.05 at scale 1 -> 7080x6620
        self.assertEqual(canvas.renderer_size((354, 331), (0.05, 0.05), 1.0), (7080, 6620))

    def test_cross_scan_variant_reproduces_the_volume_exactly(self):
        # PHerc0009B 2.401um volume, canvas [W=14760, H=18520], from
        # mesh/20250510172639-on-20250820154339-2.401um.tifxyz (H=926, W=738).
        stored, scale, target = (926, 738), (0.05000000074505806, 0.05000000074505806), (18520, 14760)
        info = canvas.classify_pair(stored, scale, target, source_group=0)
        self.assertTrue(info["exact_at_model_scale"])
        self.assertTrue(info["reproducible_any_scale"])
        # the implied scale is 1 up to float32 noise in the stored scale
        self.assertLess(abs(info["implied_scale_h"] - 1.0), 1e-6)
        self.assertEqual(info["residual_max"], 0)

    def test_source_group_folds_into_the_render_scale(self):
        # PHerc0139 1.129um L1 volume: same mesh at half the render scale.
        stored, scale, target = (3012, 2817), (0.05000000074505806, 0.05000000074505806), (30120, 28170)
        info = canvas.classify_pair(stored, scale, target, source_group=1)
        self.assertTrue(info["exact_at_model_scale"])
        self.assertEqual(info["model_scale"], 0.5)
        without = canvas.classify_pair(stored, scale, target, source_group=0)
        self.assertFalse(without["exact_at_model_scale"])

    def test_issue_1727_example_is_not_isotropically_reproducible(self):
        # #1727: mesh 354x331 @ 0.05 -> volume 30120 (H) x 28170 (W); the scale
        # implied by the height and by the width differ.
        stored, scale, target = (354, 331), (0.05, 0.05), (30120, 28170)
        info = canvas.classify_pair(stored, scale, target, source_group=0)
        self.assertFalse(info["exact_at_model_scale"])
        self.assertFalse(info["reproducible_any_scale"] or info["residual_max"] == 0)
        self.assertGreater(info["implied_scale_iso_rel_diff"], 0)
        self.assertAlmostEqual(info["implied_scale_h"], 4.2542, places=3)
        self.assertAlmostEqual(info["implied_scale_w"], 4.2553, places=3)
        self.assertGreater(info["residual_max"], 0)


class TestIntervalInversion(unittest.TestCase):
    def test_interval_matches_a_brute_force_scan(self):
        """The analytical interval must agree with scanning render scales."""
        rng = random.Random(20261001)
        for _ in range(300):
            stored = rng.randint(2, 900)
            scale = rng.choice([0.05, 0.05000000074505806, 0.0125, 0.2, 1.0, 0.03125])
            target = max(1, int(stored / canvas.f32(scale) * rng.uniform(0.2, 5.0)))
            iv = canvas.feasible_interval(stored, scale, target)
            for k in range(1, 400):
                rs = k * 0.01
                got = canvas.renderer_size((stored, stored), (scale, scale), rs)[0]
                inside = iv is not None and (iv[0] <= rs < iv[1] or (iv[0] == 0.0 and rs < iv[1]))
                self.assertEqual(got == target, inside,
                                 f"stored={stored} scale={scale} target={target} rs={rs}")

    def test_intersection_empty_when_axes_disagree(self):
        iv_h = canvas.feasible_interval(354, 0.05, 30120)
        iv_w = canvas.feasible_interval(331, 0.05, 28170)
        self.assertIsNone(canvas.interval_intersect(iv_h, iv_w))

    def test_residual_is_zero_exactly_when_reproducible(self):
        rng = random.Random(7)
        for _ in range(200):
            stored = rng.randint(10, 500)
            scale = 0.05
            target = canvas.renderer_size((stored, stored), (scale, scale), 1.0)[0]
            info = canvas.classify_pair((stored, stored), (scale, scale), (target, target))
            self.assertTrue(info["reproducible_any_scale"])
            self.assertEqual(info["clean_scale"], 1.0)
            self.assertEqual(info["residual_max"], 0)
            # An isotropic pair is always reproducible at *some* scale (the
            # interval scales with the target), so a shifted target is a case
            # for "another scale", not for "not reproducible" -- and the scale
            # it needs is no longer 1.
            offset = canvas.classify_pair((stored, stored), (scale, scale), (target + 7, target + 7))
            self.assertFalse(offset["exact_at_model_scale"])
            self.assertTrue(offset["reproducible_any_scale"])
            self.assertIsNone(offset["clean_scale"])

    def test_anisotropic_mismatch_is_not_reproducible(self):
        # A grid whose height and width disagree on the implied scale has no
        # isotropic render scale that satisfies both axes at once.
        info = canvas.classify_pair((354, 331), (0.05, 0.05), (30120, 28170))
        self.assertFalse(info["reproducible_any_scale"])
        self.assertGreater(info["residual_max"], 0)
        self.assertIsNone(info["rs_lo"])
        self.assertGreater(info["residual_rs"], 0)

    def test_target_one_is_clamped(self):
        iv = canvas.feasible_interval(10, 0.05, 1)
        self.assertIsNotNone(iv)
        self.assertEqual(iv[0], 0.0)
        self.assertAlmostEqual(iv[1], 1.5 * canvas.f32(0.05) / 10)


class TestVariantKind(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(canvas.mesh_variant_kind("PHerc1/segments/1/mesh/intermediate/tifxyz_normalized"),
                         "tifxyz_normalized")
        self.assertEqual(canvas.mesh_variant_kind("PHerc1/segments/1/mesh/intermediate/tifxyz_flattened"),
                         "tifxyz_flattened")
        self.assertEqual(canvas.mesh_variant_kind("PHerc1/segments/1/mesh/1-on-20250820154339-2.401um.tifxyz"),
                         "transformed")
        self.assertEqual(canvas.mesh_variant_kind("PHerc1/segments/1/mesh"), "mesh-root")

    def test_same_frame_scope_excludes_cross_scan_variants(self):
        self.assertTrue(canvas.is_same_frame("tifxyz_normalized"))
        self.assertTrue(canvas.is_same_frame("mesh-root"))
        self.assertFalse(canvas.is_same_frame("transformed"))


if __name__ == "__main__":
    unittest.main()
