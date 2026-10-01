"""Walk the published bucket and record what is there -- names and headers only.

The unit of work is one *segment directory* (``<sample>/segments/<id>``). For
each, this module records:

* every **mesh variant** below ``<seg>/mesh/`` that has a ``meta.json`` and an
  ``x.tif``: its kind (same-frame or cross-scan), its ``meta.json`` ``scale``,
  and the header-derived ``(height, width)`` of its grid;
* every **surface volume** below ``<seg>/surface-volumes/``: its ``.zattrs``
  (``canvas_size``, ``source_group``, ``source_zarr``, ``multiscales``), its
  level-0 ``.zarray`` (shape, chunks, fill_value, dtype), and whether each
  pyramid level actually holds any chunk object;
* the segment's catalogue entry, when the catalogue has one.

Enumeration is driven by the *bucket*, not the catalogue: a mesh directory the
catalogue does not index is still published, and the question "is this volume
reproducible from what is published" has to be asked against what is published.
Where the two disagree, the disagreement is reported (``in_catalogue``).
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import re
import time
from dataclasses import dataclass, field

from . import canvas
from .s3 import AnonymousS3, NotFound, S3Error
from .tiffhdr import read_tiff_header

#: ``1.129um-0.22m-59keV-volume-20260521123630-L1.zarr`` -> 1.129
VOLUME_NAME_RE = re.compile(r"^(?P<voxel>[0-9.]+)um-(?P<energy>[0-9.]+)m-(?P<kev>[0-9.]+)keV-volume-(?P<vol>[0-9]+)(?P<suffix>.*)$")

#: A segment directory is a 14-digit timestamp, optionally suffixed.
SEGMENT_DIR_RE = re.compile(r"^\d{14}(-|$)")


@dataclass
class MeshVariant:
    dir: str
    kind: str
    meta: dict | None = None
    meta_error: str | None = None
    header: dict | None = None
    header_error: str | None = None
    in_catalogue: bool = False
    catalogue_types: list[str] = field(default_factory=list)

    @property
    def stored_hw(self) -> tuple[int, int] | None:
        if not self.header:
            return None
        return (self.header["height"], self.header["width"])

    @property
    def scale_xy(self) -> tuple[float, float] | None:
        if not self.meta:
            return None
        s = self.meta.get("scale")
        if isinstance(s, (int, float)):
            return (float(s), float(s))
        if isinstance(s, list) and len(s) >= 2:
            return (float(s[0]), float(s[1]))
        if isinstance(s, list) and len(s) == 1:
            return (float(s[0]), float(s[0]))
        return None


@dataclass
class SurfaceVolume:
    dir: str                      # full prefix, no trailing slash
    zattrs: dict | None = None
    zattrs_error: str | None = None
    level0: dict | None = None    # parsed 0/.zarray
    level0_error: str | None = None
    levels: list[dict] = field(default_factory=list)   # per-level {level, n_data_keys, conclusive, shape, chunks}
    in_catalogue: bool = False
    catalogue_types: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.dir.rstrip("/").rsplit("/", 1)[-1]

    @property
    def segment_prefix(self) -> str:
        return self.dir.split("/surface-volumes/")[0]

    @property
    def target_hw(self) -> tuple[int, int] | None:
        """Published canvas as ``(height, width)``.

        ``canvas_size`` is written as ``[width, height]`` (Zarr.cpp:448) and is
        the field the renderer wrote; level 0's array shape is ``[z, y, x]``.
        The audit uses ``canvas_size`` when present and cross-checks it against
        level 0, recording any disagreement rather than picking silently.
        """
        cs = (self.zattrs or {}).get("canvas_size")
        if isinstance(cs, list) and len(cs) == 2:
            return (int(cs[1]), int(cs[0]))
        if self.level0 and isinstance(self.level0.get("shape"), list) and len(self.level0["shape"]) >= 2:
            return (int(self.level0["shape"][-2]), int(self.level0["shape"][-1]))
        return None

    @property
    def level0_hw(self) -> tuple[int, int] | None:
        sh = (self.level0 or {}).get("shape")
        if isinstance(sh, list) and len(sh) >= 2:
            return (int(sh[-2]), int(sh[-1]))
        return None

    @property
    def source_group(self) -> int | None:
        sg = (self.zattrs or {}).get("source_group")
        return int(sg) if isinstance(sg, (int, float)) else None

    @property
    def declared_voxel_um(self) -> float | None:
        m = VOLUME_NAME_RE.match(self.name)
        return float(m.group("voxel")) if m else None


@dataclass
class Segment:
    prefix: str
    sample: str
    meshes: list[MeshVariant] = field(default_factory=list)
    volumes: list[SurfaceVolume] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    in_catalogue: bool = False


# ----------------------------------------------------------------- the walking


def discover_segments(s3: AnonymousS3) -> tuple[list[tuple[str, str]], list[str]]:
    """``([(sample, segment_prefix)], ignored_prefixes)``.

    A segment directory is named ``<14-digit timestamp>[-<suffix>]``. Anything
    else under ``<sample>/segments/`` (``segments/raw`` exists) is not a segment
    and is returned separately rather than silently walked as one.
    """
    keys, prefixes, truncated = s3.list_objects("", max_keys=1000, delimiter="/")
    samples = sorted({p.rstrip("/") for p in prefixes})
    out: list[tuple[str, str]] = []
    ignored: list[str] = []
    with cf.ThreadPoolExecutor(max_workers=16) as ex:
        def per_sample(sample: str):
            _, seg_prefixes, _ = s3.list_objects(f"{sample}/segments/", max_keys=1000, delimiter="/")
            return sample, sorted(p.rstrip("/") for p in seg_prefixes)

        for sample, segs in ex.map(per_sample, samples):
            for seg in segs:
                leaf = seg.rsplit("/", 1)[-1]
                if SEGMENT_DIR_RE.match(leaf):
                    out.append((sample, seg))
                else:
                    ignored.append(seg)
    return out, sorted(ignored)


def _mesh_dirs(s3: AnonymousS3, seg: str) -> list[str]:
    """Mesh directories below ``<seg>/mesh/`` that actually hold ``meta.json``.

    Found by *prefix* walking with ``delimiter='/'`` rather than by listing every
    key: a segment's ``mesh/`` tree holds a handful of directories and each holds
    four files, while ``surface-volumes/`` holds tens of thousands of chunk
    objects. Listing flat responses is the difference between a few kilobytes
    and a few hundred megabytes per segment.
    """
    dirs: set[str] = set()
    keys, prefixes, _ = s3.list_objects(f"{seg}/mesh/", delimiter="/")
    if any(k.endswith("/meta.json") for k in keys):
        dirs.add(f"{seg}/mesh")          # legacy layout: meta.json directly under mesh/
    for p in prefixes:
        sub_keys, sub_prefixes, _ = s3.list_objects(p, delimiter="/")
        if any(k.endswith("/meta.json") for k in sub_keys):
            dirs.add(p.rstrip("/"))
        for sp in sub_prefixes:
            k2, _, _ = s3.list_objects(sp, delimiter="/")
            if any(x.endswith("/meta.json") for x in k2):
                dirs.add(sp.rstrip("/"))
    return sorted(dirs)


def _surface_volume_dirs(s3: AnonymousS3, seg: str) -> list[str]:
    """Surface-volume Zarr roots below ``<seg>/surface-volumes/``."""
    _, prefixes, _ = s3.list_objects(f"{seg}/surface-volumes/", delimiter="/")
    return sorted(p.rstrip("/") for p in prefixes)


def _probe_levels(s3: AnonymousS3, vol: SurfaceVolume) -> list[dict]:
    """Per-level "does this level hold any chunk object?" probe.

    Levels are read from ``multiscales[0].datasets`` when present, else from 0
    upward until a level has no ``.zarray``. Level 0 and 1 are probed for every
    volume (a pyramid whose *second* level is empty breaks readers that pick a
    level by voxel size); when either comes back empty, every declared level is
    probed so the report says how deep the hole goes.
    """
    n_declared = 0
    ms = (vol.zattrs or {}).get("multiscales")
    if isinstance(ms, list) and ms and isinstance(ms[0], dict) and isinstance(ms[0].get("datasets"), list):
        n_declared = len(ms[0]["datasets"])

    out: list[dict] = []
    for level in range(max(n_declared, 2)):
        try:
            za = s3.get_json(f"{vol.dir}/{level}/.zarray")
        except NotFound:
            out.append({"level": level, "exists": False, "n_data_keys": 0, "conclusive": True, "shape": None})
            if level >= 2:
                break
            continue
        n_data, conclusive, keys = s3.probe_data_keys(f"{vol.dir}/{level}/")
        out.append({
            "level": level,
            "exists": True,
            "n_data_keys": n_data,
            "conclusive": conclusive,
            "shape": za.get("shape"),
            "keys_seen": len(keys),
        })

    empties = [lv["level"] for lv in out if lv["exists"] and lv["n_data_keys"] == 0 and lv["conclusive"]]
    if empties and n_declared > len(out):
        for level in range(len(out), n_declared):
            try:
                za = s3.get_json(f"{vol.dir}/{level}/.zarray")
            except NotFound:
                out.append({"level": level, "exists": False, "n_data_keys": 0, "conclusive": True, "shape": None})
                continue
            n_data, conclusive, keys = s3.probe_data_keys(f"{vol.dir}/{level}/")
            out.append({"level": level, "exists": True, "n_data_keys": n_data,
                        "conclusive": conclusive, "shape": za.get("shape"), "keys_seen": len(keys)})
    return out


def walk_segment(s3: AnonymousS3, sample: str, seg: str, catalogue_seg: dict | None, deep: bool = True) -> Segment:
    """Everything published under one segment directory."""
    s = Segment(prefix=seg, sample=sample, in_catalogue=catalogue_seg is not None)

    known_mesh: dict[str, list[str]] = {}
    known_vol: dict[str, list[str]] = {}
    if catalogue_seg:
        for art in catalogue_seg.get("data") or []:
            for o in art.get("origins") or []:
                p = (o.get("path") or "").rstrip("/")
                if "/mesh/" in p:
                    known_mesh.setdefault(p, []).append(art["type"])
                if "/surface-volumes/" in p:
                    known_vol.setdefault(p, []).append(art["type"])

    # --- meshes -------------------------------------------------------------
    try:
        mesh_dirs = _mesh_dirs(s3, seg)
    except (S3Error, NotFound) as exc:
        mesh_dirs = []
        s.errors.append(f"list mesh: {exc}")
    for d in mesh_dirs:
        mv = MeshVariant(dir=d, kind=canvas.mesh_variant_kind(d))
        mv.in_catalogue = d in known_mesh
        mv.catalogue_types = known_mesh.get(d, [])
        try:
            mv.meta = s3.get_json(f"{d}/meta.json")
        except (S3Error, NotFound) as exc:
            mv.meta_error = str(exc)
        if deep:
            try:
                mv.header = read_tiff_header(s3, f"{d}/x.tif")
            except Exception as exc:  # noqa: BLE001 - reported verbatim, never guessed
                mv.header_error = f"{type(exc).__name__}: {exc}"
        else:
            try:
                s3.head(f"{d}/x.tif")
                mv.header = {"present": True}
            except Exception as exc:  # noqa: BLE001
                mv.header_error = f"{type(exc).__name__}: {exc}"
        s.meshes.append(mv)

    # --- surface volumes ----------------------------------------------------
    try:
        vol_dirs = _surface_volume_dirs(s3, seg)
    except (S3Error, NotFound) as exc:
        vol_dirs = []
        s.errors.append(f"list surface-volumes: {exc}")
    for d in vol_dirs:
        sv = SurfaceVolume(dir=d)
        sv.in_catalogue = d in known_vol
        sv.catalogue_types = known_vol.get(d, [])
        try:
            sv.zattrs = s3.get_json(f"{d}/.zattrs")
        except (S3Error, NotFound) as exc:
            sv.zattrs_error = str(exc)
        try:
            sv.level0 = s3.get_json(f"{d}/0/.zarray")
        except (S3Error, NotFound) as exc:
            sv.level0_error = str(exc)
        if deep:
            try:
                sv.levels = _probe_levels(s3, sv)
            except (S3Error, NotFound) as exc:
                s.errors.append(f"levels {name}: {exc}")
        s.volumes.append(sv)

    return s


def collect(
    s3: AnonymousS3,
    catalogue: dict,
    workers: int = 24,
    limit_segments: int | None = None,
    deep: bool = True,
    progress=None,
) -> dict:
    """Walk the whole published catalogue. Returns the raw inventory document."""
    segments, ignored_dirs = discover_segments(s3)
    if limit_segments:
        segments = segments[:limit_segments]
    cat_segments: dict[str, dict] = {}
    for sample, entry in (catalogue.get("samples") or {}).items():
        for sid, seg in (entry.get("segments") or {}).items():
            cat_segments[f"{sample}/segments/{sid}"] = seg

    def catalogue_for(seg_prefix: str) -> dict | None:
        """Match a bucket directory to its catalogue entry.

        The bucket names a segment directory ``<id>-<suffix>`` (``-w025_...``,
        ``-auto_grown_...``) while the catalogue keys it by ``<id>`` alone, so an
        exact-key lookup silently reports every suffixed segment as "not in the
        catalogue". Match the exact key first, then the id before the first
        hyphen.
        """
        if seg_prefix in cat_segments:
            return cat_segments[seg_prefix]
        sample, _, dirname = seg_prefix.partition("/segments/")
        return cat_segments.get(f"{sample}/segments/{dirname.split('-')[0]}")

    out: list[Segment] = []
    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(walk_segment, s3, sample, seg, catalogue_for(seg), deep): seg
                for sample, seg in segments}
        done = 0
        for fut in cf.as_completed(futs):
            seg_prefix = futs[fut]
            try:
                out.append(fut.result())
            except Exception as exc:  # noqa: BLE001 - a failed segment is data too
                out.append(Segment(prefix=seg_prefix, sample=seg_prefix.split("/")[0],
                                   errors=[f"{type(exc).__name__}: {exc}"]))
            done += 1
            if progress and (done % 25 == 0 or done == len(futs)):
                progress(done, len(futs), time.time() - t0)
    out.sort(key=lambda s: s.prefix)

    walked = {s.prefix for s in out}
    matched = set()
    for s in out:
        if s.in_catalogue:
            seg_dir = s.prefix.partition("/segments/")[2]
            matched.add(f"{s.sample}/segments/{seg_dir.split('-')[0]}")
    catalogue_only = sorted(set(cat_segments) - matched)

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "bucket": s3.bucket,
        "bucket_url": s3.base_url,
        "catalogue_generated_at": catalogue.get("__fetched_at__"),
        "segments": [segment_to_json(s) for s in out],
        "drift": {
            "catalogue_segments": len(cat_segments),
            "bucket_segment_dirs": len(walked),
            "limited": bool(limit_segments),
            "non_segment_dirs_ignored": ignored_dirs,
            "catalogue_segments_not_in_bucket": catalogue_only,
        },
        "stats": {
            "segments": len(out),
            "meshes": sum(len(s.meshes) for s in out),
            "meshes_in_catalogue": sum(1 for s in out for m in s.meshes if m.in_catalogue),
            "volumes": sum(len(s.volumes) for s in out),
            "volumes_in_catalogue": sum(1 for s in out for v in s.volumes if v.in_catalogue),
            "errors": sum(len(s.errors) for s in out),
            "http_requests": s3.requests,
            "bytes_read": s3.bytes_read,
        },
    }


def segment_to_json(s: Segment) -> dict:
    return {
        "prefix": s.prefix,
        "sample": s.sample,
        "in_catalogue": s.in_catalogue,
        "errors": s.errors,
        "meshes": [
            {
                "dir": m.dir, "kind": m.kind, "in_catalogue": m.in_catalogue,
                "catalogue_types": m.catalogue_types, "meta": m.meta, "meta_error": m.meta_error,
                "header": m.header, "header_error": m.header_error,
            }
            for m in s.meshes
        ],
        "volumes": [
            {
                "dir": v.dir, "in_catalogue": v.in_catalogue, "catalogue_types": v.catalogue_types,
                "zattrs": v.zattrs, "zattrs_error": v.zattrs_error,
                "level0": v.level0, "level0_error": v.level0_error, "levels": v.levels,
            }
            for v in s.volumes
        ],
    }


def load_inventory(path) -> dict:
    with open(path) as fh:
        return json.load(fh)
