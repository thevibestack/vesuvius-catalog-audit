"""The catalogue-side joins: date parsing, the #1730 coverage count, and the
directory-naming rules the inventory walk relies on.

These are regression guards for two things the published data actually does and a
naive implementation gets wrong: `creation.date` values of the form
`2025-06-11T15:42:56+00:00Z` (an offset *and* a `Z`, which `datetime.fromisoformat`
rejects), and `segments/raw`, a directory under `segments/` that is not a segment.
"""

from __future__ import annotations

import datetime
import unittest

from vcaudit import catalogue
from vcaudit.inventory import SEGMENT_DIR_RE


def _cat(segments, volumes=None, scans=None):
    return {"samples": {"PHercX": {
        "segments": segments,
        "volumes": volumes or {},
        "scans": scans or {},
    }}}


class TestParseDate(unittest.TestCase):
    def test_plain_z(self):
        self.assertEqual(catalogue.parse_date("2024-08-28T19:05:16Z"),
                         datetime.datetime(2024, 8, 28, 19, 5, 16, tzinfo=datetime.timezone.utc))

    def test_offset_and_z_both_present(self):
        """Regression: `+00:00Z` is published (5 PHerc0500P2 segments)."""
        self.assertEqual(catalogue.parse_date("2025-06-11T15:42:56+00:00Z"),
                         datetime.datetime(2025, 6, 11, 15, 42, 56, tzinfo=datetime.timezone.utc))

    def test_offset_and_microseconds(self):
        self.assertEqual(catalogue.parse_date("2025-12-06T18:45:43.153960+00:00").year, 2025)

    def test_naive_local_is_read_as_utc(self):
        self.assertIsNotNone(catalogue.parse_date("2025-06-11T15:42:56"))

    def test_not_a_date_is_none_not_an_exception(self):
        for bad in (None, "", "nonsense", 17, ["2025-01-01"]):
            self.assertIsNone(catalogue.parse_date(bad))


class TestScanDateCoverage(unittest.TestCase):
    def test_counts_only_segments_it_can_examine(self):
        cat = _cat(
            segments={
                "1": {"creation": {"date": "2025-01-01T00:00:00Z"}, "original_volume_id": "v1"},
                "2": {"creation": {"date": "2025-01-01T00:00:00Z"}, "original_volume_id": "v9"},
                "3": {"creation": {"date": "not-a-date"}, "original_volume_id": "v1"},
            },
            volumes={"v1": {"scan_id": "s1"}},
            scans={"s1": {"creation": {"date": "2025-01-02T00:00:00Z"}}},
        )
        cov = catalogue.scan_date_coverage(cat)
        self.assertEqual(cov["segments"], 3)
        self.assertEqual(cov["segments_with_creation_date"], 2)
        self.assertEqual(cov["segments_with_resolvable_scan_date"], 1)
        self.assertEqual(len(cov["unparseable_creation_date"]), 1)
        self.assertEqual(cov["declared_volume_not_found"], ["PHercX/segments/2"])

    def test_a_segment_with_no_scan_date_is_not_cleared(self):
        cat = _cat(
            segments={"1": {"creation": {"date": "2025-01-01T00:00:00Z"}, "original_volume_id": "v1"}},
            volumes={"v1": {"scan_id": "s1"}},
            scans={"s1": {"creation": {"date": None}}},
        )
        self.assertEqual(catalogue.scan_date_coverage(cat)["segments_with_resolvable_scan_date"], 0)
        self.assertEqual(catalogue.scan_after_segment_rows(cat), [])


class TestSegmentDirRule(unittest.TestCase):
    def test_accepts_timestamp_dirs_with_and_without_suffix(self):
        for name in ("20250510172639", "20250510172639-w025_2025010863",
                     "20250702235910-auto_grown_20250702235910292"):
            self.assertIsNotNone(SEGMENT_DIR_RE.match(name), name)

    def test_rejects_dirs_that_are_not_segments(self):
        for name in ("raw", "segments", "20250510172639abc", "abc20250510172639", "2025"):
            self.assertIsNone(SEGMENT_DIR_RE.match(name), name)


if __name__ == "__main__":
    unittest.main()
