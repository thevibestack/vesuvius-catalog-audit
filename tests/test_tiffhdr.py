"""Tests for the TIFF header reader. No network: a fake client serves bytes."""

from __future__ import annotations

import struct
import unittest

from vcaudit.tiffhdr import TiffHeaderError, read_tiff_header


def build_classic_tiff(width: int, height: int, big_endian: bool = False, samples: int = 1,
                       ifd_offset: int = 8, total_size: int | None = None) -> bytes:
    """A minimal, valid TIFF: header + one IFD (+ padding) + one byte of pixel data."""
    endian = ">" if big_endian else "<"
    tags = [
        (256, 4, 1, width),    # ImageWidth LONG
        (257, 4, 1, height),   # ImageLength LONG
        (258, 3, 1, 32),       # BitsPerSample SHORT
        (277, 3, 1, samples),  # SamplesPerPixel SHORT
    ]
    tags.sort()
    ifd_size = 2 + len(tags) * 12 + 4
    pixel_offset = ifd_offset + ifd_size
    size = total_size if total_size is not None else pixel_offset + 1
    out = bytearray(b"\x00" * size)
    out[0:2] = b"MM" if big_endian else b"II"
    struct.pack_into(endian + "H", out, 2, 42)
    struct.pack_into(endian + "I", out, 4, ifd_offset)
    pos = ifd_offset
    struct.pack_into(endian + "H", out, pos, len(tags))
    pos += 2
    for tag, typ, count, value in tags:
        struct.pack_into(endian + "HHI", out, pos, tag, typ, count)
        if typ == 4:
            struct.pack_into(endian + "I", out, pos + 8, value)
        else:
            struct.pack_into(endian + "H", out, pos + 8, value)
        pos += 12
    struct.pack_into(endian + "I", out, pos, 0)  # next IFD
    return bytes(out)


class FakeS3:
    """Records the ranges asked for, so we can assert we never pull whole files."""

    def __init__(self, blob: bytes):
        self.blob = blob
        self.ranges = []

    def get(self, key, byte_range=None):
        self.ranges.append(byte_range)
        if byte_range is None:
            return self.blob
        return self.blob[byte_range[0]:byte_range[1] + 1]

    def bytes_served(self) -> int:
        return sum(len(self.blob[r[0]:r[1] + 1]) for r in self.ranges if r)


class TestTiffHeader(unittest.TestCase):
    def test_reads_shape_little_endian(self):
        client = FakeS3(build_classic_tiff(207, 260))
        info = read_tiff_header(client, "x.tif")
        self.assertEqual((info["height"], info["width"]), (260, 207))
        self.assertEqual(info["bits"], 32)
        self.assertEqual(info["tiff_version"], 42)

    def test_reads_shape_big_endian(self):
        client = FakeS3(build_classic_tiff(4096, 512, big_endian=True, samples=3))
        info = read_tiff_header(client, "x.tif")
        self.assertEqual((info["height"], info["width"]), (512, 4096))
        self.assertEqual(info["samples"], 3)

    def test_reads_a_file_whose_ifd_sits_after_the_pixel_data(self):
        # Some published x.tif files put IFD 0 after hundreds of megabytes of
        # pixel data; the header must still cost a couple of kilobytes.
        blob = build_classic_tiff(2817, 3012, ifd_offset=64 << 20, total_size=(64 << 20) + 4096)
        client = FakeS3(blob)
        info = read_tiff_header(client, "x.tif")
        self.assertEqual((info["height"], info["width"]), (3012, 2817))
        self.assertLess(client.bytes_served(), 64 * 1024,
                        "reading a header must not pull the pixel data")

    def test_header_cost_is_bounded(self):
        blob = build_classic_tiff(207, 260) + b"\x00" * (5 << 20)
        client = FakeS3(blob)
        read_tiff_header(client, "x.tif")
        self.assertGreater(len(client.ranges), 0)
        self.assertLess(client.bytes_served(), 128 * 1024)

    def test_rejects_non_tiff(self):
        client = FakeS3(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        with self.assertRaises(TiffHeaderError):
            read_tiff_header(client, "x.tif")

    def test_rejects_truncated_header(self):
        client = FakeS3(b"II")
        with self.assertRaises(TiffHeaderError):
            read_tiff_header(client, "x.tif")


if __name__ == "__main__":
    unittest.main()
