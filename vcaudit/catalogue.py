"""The published catalogue document (``metadata.json``) and the date/shape joins.

``metadata.json`` at the root of the open-data bucket is the catalogue: samples,
scans, volumes and segments, each with its creation record, its stored bbox and
its published artifacts. Two of the audit's checks (#1730, #1734) read nothing
else, because both defects are *in the catalogue's own derived fields*.

The date parser is the one #1730's reproduction uses, and the reason is worth
keeping: five segments carry a creation date of the form
``2025-06-11T15:42:56+00:00Z`` -- an offset and a ``Z`` -- which
``datetime.fromisoformat`` rejects outright. A strict parser silently drops them
and the count comes out wrong.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from .s3 import AnonymousS3

CATALOGUE_KEY = "metadata.json"


def fetch_catalogue(s3: AnonymousS3) -> dict:
    import time

    cat = s3.get_json_gz_or_plain(CATALOGUE_KEY)
    cat["__fetched_at__"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    cat["__source__"] = s3.base_url + CATALOGUE_KEY
    return cat


def parse_date(value: str) -> datetime | None:
    """Tolerant ISO-8601 parse; ``None`` when the string is not a date at all."""
    if not isinstance(value, str):
        return None
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1]
    if not re.search(r"[+-]\d{2}:\d{2}$", s[10:]):
        s += "+00:00"
    try:
        return datetime.fromisoformat(s).astimezone(timezone.utc)
    except ValueError:
        return None


def segments_of(catalogue: dict):
    """Yield ``(sample, segment_id, segment_dict)``."""
    for sample, entry in (catalogue.get("samples") or {}).items():
        for sid, seg in (entry.get("segments") or {}).items():
            yield sample, sid, seg


def scans_of(catalogue: dict, sample: str) -> dict:
    return (catalogue["samples"][sample].get("scans") or {})


def volumes_of(catalogue: dict, sample: str) -> dict:
    return (catalogue["samples"][sample].get("volumes") or {})


# ---------------------------------------------------------------- #1730 join


def scan_after_segment_rows(catalogue: dict) -> list[dict]:
    """Segments whose declared ``original_volume_id`` was scanned *after* they were made.

    #1730's reproduction, verbatim in behaviour: join
    ``original_volume_id -> volume -> scan_id -> scan.creation.date`` and compare
    with the segment's own ``creation.date``. A segment with a scan that postdates
    it cannot be right as published.
    """
    rows = []
    for sample, sid, seg in segments_of(catalogue):
        scans = {k: parse_date((v.get("creation") or {}).get("date"))
                 for k, v in scans_of(catalogue, sample).items()}
        vols = {k: v.get("scan_id") for k, v in volumes_of(catalogue, sample).items()}
        ov = seg.get("original_volume_id")
        scan_id = vols.get(ov)
        seg_date = parse_date((seg.get("creation") or {}).get("date"))
        if not (ov and scan_id in scans and seg_date and scans[scan_id]):
            continue
        delta_days = (scans[scan_id] - seg_date).days
        if delta_days > 0:
            rows.append({
                "sample": sample,
                "segment": sid,
                "segment_created": (seg.get("creation") or {}).get("date"),
                "declared_volume": ov,
                "scan_id": scan_id,
                "scan_taken": (scans[scan_id].isoformat()),
                "scan_is_later_days": delta_days,
                "segment_bbox_zmax": bbox_zmax((seg.get("creation") or {}).get("metadata") or {}),
                "declared_volume_z": declared_volume_z(seg, catalogue, sample),
            })
    return rows


def scan_date_coverage(catalogue: dict) -> dict:
    """How many segments the #1730 date join could examine at all.

    Stated separately so the result reads "20 of N examined" and not "20 of 323":
    a segment the join cannot examine is not a segment it cleared. The segments
    whose ``creation.date`` is unparseable are named, because a strict parser
    drops them silently (5 PHerc0500P2 segments carry an offset *and* a ``Z``).
    """
    total = with_date = with_scan_date = 0
    unparseable: list[dict] = []
    no_volume: list[str] = []
    for sample, sid, seg in segments_of(catalogue):
        total += 1
        raw = (seg.get("creation") or {}).get("date")
        if parse_date(raw) is None:
            unparseable.append({"sample": sample, "segment": sid, "creation_date": raw})
            continue
        with_date += 1
        ov = seg.get("original_volume_id")
        scan_id = volumes_of(catalogue, sample).get(ov, {}).get("scan_id") if ov else None
        if not scan_id:
            no_volume.append(f"{sample}/segments/{sid}")
            continue
        scan = scans_of(catalogue, sample).get(scan_id) or {}
        if parse_date((scan.get("creation") or {}).get("date")) is not None:
            with_scan_date += 1
    return {
        "segments": total,
        "segments_with_creation_date": with_date,
        "segments_with_resolvable_scan_date": with_scan_date,
        "unparseable_creation_date": unparseable,
        "declared_volume_not_found": sorted(no_volume),
    }


def bbox_zmax(metadata: dict) -> float | None:
    bb = metadata.get("bbox")
    if isinstance(bb, list) and len(bb) == 2 and isinstance(bb[1], list) and len(bb[1]) >= 3:
        try:
            return float(bb[1][2])
        except (TypeError, ValueError):
            return None
    return None


def declared_volume_z(seg: dict, catalogue: dict, sample: str) -> int | None:
    """The z extent the catalogue itself records for the segment's volume."""
    ov = seg.get("original_volume_id")
    vol = volumes_of(catalogue, sample).get(ov) or {}
    shape = ((vol.get("properties") or {}).get("shape"))
    if isinstance(shape, list) and len(shape) >= 3 and isinstance(shape[0], int):
        return int(shape[0])
    return None


# ---------------------------------------------------------------- #1734 join


def bbox_marker_rows(catalogue: dict) -> list[dict]:
    """Segments whose stored bbox carries the tifxyz ``-1`` missing-point marker.

    #1734's reproduction: for every segment whose stored bbox has a ``-1`` on
    some lower-corner axis, compare ``stored * original_volume_downscale`` with
    the catalogue's derived ``volume_coverage[<own volume>].bbox_transformed``.
    Where they agree exactly, the derived field is the *sentinel scaled as if it
    were a coordinate* -- it is not a description of the transformed surface.
    """
    rows = []
    for sample, sid, seg in segments_of(catalogue):
        creation_meta = (seg.get("creation") or {}).get("metadata") or {}
        bb = creation_meta.get("bbox")
        if not (isinstance(bb, list) and len(bb) == 2 and isinstance(bb[0], list) and len(bb[0]) >= 3):
            continue
        try:
            lo = [float(v) for v in bb[0]]
        except (TypeError, ValueError):
            continue
        marker_axes = [i for i in range(3) if lo[i] == -1.0]
        if not marker_axes:
            continue
        props = seg.get("properties") or {}
        downscale = props.get("original_volume_downscale")
        ov = seg.get("original_volume_id")
        bt = ((props.get("volume_coverage") or {}).get(ov) or {}).get("bbox_transformed")
        row = {
            "sample": sample,
            "segment": sid,
            "stored_lo": lo,
            "marker_axes": "".join("xyz"[i] for i in marker_axes),
            "original_volume_downscale": downscale,
            "bbox_transformed_lo": None,
            "scaled_stored_lo": None,
            "exact_scaled_marker": False,
            "overlap_ratio": ((props.get("volume_coverage") or {}).get(ov) or {}).get("overlap_ratio"),
        }
        if downscale and bt and isinstance(bt, list) and len(bt) == 2:
            try:
                bt_lo = [float(v) for v in bt[0]]
                scaled = [lo[i] * float(downscale) for i in range(3)]
            except (TypeError, ValueError, IndexError):
                rows.append(row)
                continue
            row["bbox_transformed_lo"] = bt_lo
            row["scaled_stored_lo"] = scaled
            row["exact_scaled_marker"] = all(
                abs(scaled[i] - bt_lo[i]) < 1e-6 for i in marker_axes
            )
        rows.append(row)
    return rows


# --------------------------------------------------- catalogue/store drift


def declared_vs_stored_shape(catalogue: dict, inventory: dict) -> list[dict]:
    """Catalogue ``properties.shape`` vs the declared z extent of the volume it names.

    #1617 / #1730's overrun figure, from the catalogue alone: a segment's stored
    bbox reaching past the z extent of the volume the catalogue says it came
    from is a segment that reads past the end of that volume. The published
    x/y/z grids say the same thing more expensively; the catalogue already
    records both numbers, so the join is free.
    """
    rows = []
    for sample, entry in (catalogue.get("samples") or {}).items():
        segs = entry.get("segments") or {}
        vols = entry.get("volumes") or {}
        for sid, seg in segs.items():
            ov = seg.get("original_volume_id")
            vol = vols.get(ov) or {}
            shape = ((vol.get("properties") or {}).get("shape"))
            if not isinstance(shape, list) or len(shape) != 3:
                continue
            bbox = ((seg.get("creation") or {}).get("metadata") or {}).get("bbox")
            zmax = None
            if isinstance(bbox, list) and len(bbox) == 2:
                try:
                    zmax = float(bbox[1][2])
                except (TypeError, ValueError, IndexError):
                    zmax = None
            rows.append({
                "sample": sample,
                "segment": sid,
                "declared_volume": ov,
                "volume_shape_zyx": shape,
                "segment_stored_bbox_zmax": zmax,
                "z_overrun": None if zmax is None else zmax - shape[0],
            })
    return rows
