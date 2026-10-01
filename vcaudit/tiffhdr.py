"""Read only the header of a TIFF over the network: the grid shape of a tifxyz.

A tifxyz surface is published as three TIFFs (``x.tif``, ``y.tif``, ``z.tif``),
one float32 sample per grid point. The grid is the *stored* grid the renderer
sizes its canvas from, so all this module needs is ``ImageWidth``/``ImageLength``
from IFD 0 -- never the pixel data. On the largest published grids a full
``x.tif`` is hundreds of megabytes; the header is a couple of kilobytes.

Two details matter for staying cheap:

* The first IFD is not always at byte 8. Writers are free to put it *after* the
  pixel data, and some published ``x.tif`` files do exactly that, so the reader
  asks for a window *at* the IFD offset rather than a window from zero -- a
  "read the first N bytes" implementation pulls megabytes per file when it hits
  one of those.
* Only ``ImageWidth`` and ``ImageLength`` are read, and only from IFD 0.

Supports classic (version 42) and BigTIFF (version 43), both byte orders.
Deliberately has no dependency on ``tifffile``: an audit that needs a wheel to
read two integers is an audit most people will not run.
"""

from __future__ import annotations

import struct

TAG_IMAGE_WIDTH = 256
TAG_IMAGE_LENGTH = 257
TAG_BITS_PER_SAMPLE = 258
TAG_COMPRESSION = 259
TAG_SAMPLES_PER_PIXEL = 277

_TYPE_SHORT = 3
_TYPE_LONG = 4

#: Bytes fetched for the file header, enough to hold it in any layout.
_HEADER_BYTES = 512
#: Bytes fetched in one window when looking for the IFD.
_WINDOW_BYTES = 8 * 1024
#: Safety valve: never fetch more than this per file for a header.
_MAX_FETCH = 256 * 1024


class TiffHeaderError(RuntimeError):
    """The bytes are not a TIFF header we can read."""


def read_tiff_header(s3, key: str) -> dict:
    """Return ``{"height", "width", "samples", "bits", "tiff_version", ...}``.

    Two ranged GETs in the normal case: a small window for the file header, and
    (only if IFD 0 is beyond it) a window at the IFD offset.
    """
    head = s3.get(key, (0, _HEADER_BYTES - 1))
    if len(head) < 8:
        raise TiffHeaderError(f"{key}: {len(head)} bytes is too short for a TIFF header")

    byte_order = head[:2]
    if byte_order == b"II":
        endian = "<"
    elif byte_order == b"MM":
        endian = ">"
    else:
        raise TiffHeaderError(f"{key}: unknown byte order {byte_order!r}")

    version = struct.unpack_from(endian + "H", head, 2)[0]
    if version == 42:
        bigtiff = False
        ifd_offset = struct.unpack_from(endian + "I", head, 4)[0]
    elif version == 43:
        bigtiff = True
        ifd_offset = struct.unpack_from(endian + "Q", head, 8)[0]
    else:
        raise TiffHeaderError(f"{key}: TIFF version {version} is neither 42 nor 43")

    entry_size = 20 if bigtiff else 12
    n_field = 8 if bigtiff else 2
    tags, n_entries = _read_ifd(s3, key, head, ifd_offset, bigtiff, endian, entry_size, n_field)

    width, height = tags.get(TAG_IMAGE_WIDTH), tags.get(TAG_IMAGE_LENGTH)
    if width is None or height is None:
        raise TiffHeaderError(f"{key}: IFD 0 has no ImageWidth/ImageLength")

    return {
        "width": int(width),
        "height": int(height),
        "samples": int(tags.get(TAG_SAMPLES_PER_PIXEL, 1)),
        "bits": int(tags.get(TAG_BITS_PER_SAMPLE, 0)),
        "compression": int(tags.get(TAG_COMPRESSION, 1)),
        "tiff_version": version,
        "ifd_offset": ifd_offset,
        "n_entries": n_entries,
    }


def _read_ifd(s3, key, head: bytes, ifd_offset: int, bigtiff: bool, endian: str,
              entry_size: int, n_field: int, window: int = _WINDOW_BYTES):
    """Parse IFD 0, fetching a window *at* ``ifd_offset`` when it is not in ``head``."""
    if ifd_offset + n_field <= len(head):
        data, base = head, 0
    else:
        data, base = _fetch(s3, key, ifd_offset, window)

    def count_entries(buf, origin):
        if origin + n_field > len(buf):
            return None
        if bigtiff:
            return struct.unpack_from(endian + "Q", buf, origin)[0]
        return struct.unpack_from(endian + "H", buf, origin)[0]

    n = count_entries(data, ifd_offset - base)
    if n is None:
        raise TiffHeaderError(f"{key}: IFD offset {ifd_offset} is outside the window")
    # One refetch is enough: the entry count is now known exactly.
    if (ifd_offset - base) + n_field + n * entry_size > len(data):
        want = n_field + n * entry_size + 16
        data, base = _fetch(s3, key, ifd_offset, want)
        n = count_entries(data, 0) or n
    start = (ifd_offset - base) + n_field
    if start + n * entry_size > len(data):
        raise TiffHeaderError(f"{key}: IFD 0 declares {n} entries but the window holds "
                              f"{len(data)} bytes")

    tags: dict = {}
    for i in range(n):
        p = start + i * entry_size
        if bigtiff:
            tag, typ, count = struct.unpack_from(endian + "HHQ", data, p)
            inline = struct.unpack_from(endian + "Q", data, p + 12)[0]
            inline_bytes = 8
        else:
            tag, typ, count = struct.unpack_from(endian + "HHI", data, p)
            inline = struct.unpack_from(endian + "I", data, p + 8)[0]
            inline_bytes = 4
        if count != 1:
            continue  # width/height/samples are single-valued here
        if typ == _TYPE_SHORT and inline_bytes == 8:
            inline = inline & 0xFFFF if endian == "<" else inline >> 48
        elif typ == _TYPE_SHORT:
            inline = inline & 0xFFFF if endian == "<" else inline >> 16
        tags[tag] = inline
    return tags, n


def _fetch(s3, key: str, start: int, size: int) -> tuple[bytes, int]:
    size = max(16, min(size, _MAX_FETCH))
    return s3.get(key, (start, start + size - 1)), start
