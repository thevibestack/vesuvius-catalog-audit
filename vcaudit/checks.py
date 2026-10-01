"""The five checks, each a pure function over what the collectors read.

Every check returns rows with a flat, CSV-friendly schema. Nothing here reads
from the network: the split keeps "what is published" (collection) separate from
"what it means" (checks), so a reader can re-run the analysis over a saved
inventory without touching S3, and a reviewer can diff the two.
"""

from __future__ import annotations

from . import canvas, catalogue

#: The verdict a volume gets when a published mesh variant reproduces its canvas.
REPRODUCED_MODEL = "reproduced_at_documented_scale"
REPRODUCED_OTHER = "reproduced_at_another_scale"
NOT_REPRODUCED = "not_reproduced_from_any_published_variant"
NO_MESH = "no_mesh_variant_available"
NO_TARGET = "no_published_canvas"

#: ``<segment>_on-<volume_id>-<voxel>um`` is the cross-scan variant naming convention.
_TRANSFORMED_RE = r"-on-(?P<volid>\d+)-(?P<voxel>[0-9.]+)um\.tifxyz$"


def volume_name_fields(volume_name: str) -> dict:
    """``{"voxel_um": 2.401, "volume_id": "20250820154339"}`` from a volume name."""
    import re

    m = re.match(r"^(?P<voxel>[0-9.]+)um-[0-9.]+m-[0-9.]+keV-volume-(?P<volid>\d+)", volume_name)
    if not m:
        return {}
    return {"voxel_um": float(m.group("voxel")), "volume_id": m.group("volid")}


def variant_names_volume(mesh_dir: str, volume_name: str) -> bool | None:
    """Does the mesh variant's own name claim it belongs to this volume?

    A cross-scan variant is published as
    ``<segment>_on-<volume_id>-<voxel>um.tifxyz``: it names the volume it was
    transformed for. A published surface volume is named
    ``<voxel>um-<energy>m-<keV>keV-volume-<volume_id>[-L<level>].zarr``. When the
    variant that reproduces a volume's canvas carries the *same* volume id and
    the same voxel size as the volume itself, "this is the mesh the render was
    made from" stops being an inference from arithmetic and becomes a statement
    the two names agree on.

    ``None`` when either name does not carry the fields (nothing to compare).
    """
    import re

    f = volume_name_fields(volume_name)
    leaf = mesh_dir.rstrip("/").rsplit("/", 1)[-1]
    m = re.search(_TRANSFORMED_RE, leaf)
    if not f or not m:
        return None
    try:
        same_voxel = abs(float(m.group("voxel")) - f["voxel_um"]) <= 1e-6
    except ValueError:
        same_voxel = False
    return bool(m.group("volid") == f["volume_id"] and same_voxel)


# ------------------------------------------------------------------- #1727


def _pair_rows(seg: dict, vol: dict, mesh: dict, target_hw) -> dict:
    scale_xy = None
    if isinstance(mesh.get("meta"), dict):
        s = mesh["meta"].get("scale")
        if isinstance(s, (int, float)):
            scale_xy = (float(s), float(s))
        elif isinstance(s, list) and len(s) >= 2:
            scale_xy = (float(s[0]), float(s[1]))
        elif isinstance(s, list) and len(s) == 1:
            scale_xy = (float(s[0]), float(s[0]))
    header = mesh.get("header") or {}
    stored = (header.get("height"), header.get("width"))
    if not scale_xy or not stored[0] or not stored[1]:
        return {}

    zattrs = vol.get("zattrs") or {}
    sg = zattrs.get("source_group")
    sg = int(sg) if isinstance(sg, (int, float)) else None

    info = canvas.classify_pair(stored, scale_xy, target_hw, source_group=sg)
    vol_name = vol["dir"].rsplit("/", 1)[-1]
    row = {
        "seg": seg["prefix"],
        "sample": seg["sample"],
        "mesh_dir": mesh["dir"],
        "mesh_kind": mesh["kind"],
        "mesh_in_catalogue": mesh.get("in_catalogue"),
        "volume": vol_name,
        "volume_dir": vol["dir"],
        "source_group": sg,
        "variant_names_the_volume": variant_names_volume(mesh["dir"], vol_name),
        **info,
    }
    return row


def check_1727_canvas(inventory: dict) -> tuple[list[dict], list[dict]]:
    """Is each published surface volume reproducible from a published mesh?

    Returns ``(volume_rows, pair_rows)``. Every (mesh variant, volume) pair in a
    segment is tested, and each volume gets a verdict under two *scopes*:

    ``scope_same_frame``
        only ``intermediate/tifxyz_original|normalized|flattened`` -- the
        surface in the frame of the scan it was traced on. This is the scope of
        the 2026-09-07 measurement, which walked ``<seg>/mesh/`` and skipped
        directories whose names contain a dot.
    ``scope_all_variants``
        every published mesh variant, including the cross-scan
        ``<seg>/mesh/<id>-on-<volume_id>-<voxel>um.tifxyz`` directories.

    The difference between the two scopes is the finding, so both are reported.
    """
    volume_rows: list[dict] = []
    pair_rows: list[dict] = []

    for seg in inventory["segments"]:
        meshes = seg["meshes"]
        for vol in seg["volumes"]:
            zattrs = vol.get("zattrs") or {}
            cs = zattrs.get("canvas_size")
            l0 = (vol.get("level0") or {}).get("shape")
            target = None
            if isinstance(cs, list) and len(cs) == 2:
                target = (int(cs[1]), int(cs[0]))
            elif isinstance(l0, list) and len(l0) >= 2:
                target = (int(l0[-2]), int(l0[-1]))
            level0_hw = (int(l0[-2]), int(l0[-1])) if isinstance(l0, list) and len(l0) >= 2 else None

            base = {
                "seg": seg["prefix"],
                "sample": seg["sample"],
                "volume": vol["dir"].rsplit("/", 1)[-1],
                "volume_dir": vol["dir"],
                "store_format": "zarr" if vol["dir"].endswith(".zarr") else
                                ("tif-stack" if vol["dir"].endswith(".tifs") else "other"),
                "in_catalogue": vol.get("in_catalogue"),
                "catalogue_types": ",".join(vol.get("catalogue_types") or []),
                "canvas_size_wh": None if not isinstance(cs, list) else ",".join(str(v) for v in cs),
                "level0_zyx": None if not isinstance(l0, list) else ",".join(str(v) for v in l0),
                "canvas_matches_level0": (
                    None if not (target and level0_hw)
                    else (target[0] == level0_hw[0] and target[1] == level0_hw[1])
                ),
                "source_group": zattrs.get("source_group"),
                "source_zarr": zattrs.get("source_zarr"),
                "zattrs_ok": vol.get("zattrs") is not None,
                "level0_ok": vol.get("level0") is not None,
            }

            if target is None:
                volume_rows.append({**base, "verdict": NO_TARGET, "n_pairs": 0,
                                    "n_mesh_variants": len(meshes)})
                continue
            if not meshes:
                volume_rows.append({**base, "verdict": NO_MESH, "n_pairs": 0,
                                    "n_mesh_variants": 0})
                continue

            pairs = []
            for mesh in meshes:
                row = _pair_rows(seg, vol, mesh, target)
                if row:
                    pairs.append(row)
                    pair_rows.append(row)
            if not pairs:
                volume_rows.append({**base, "verdict": NO_MESH, "n_pairs": 0,
                                    "n_mesh_variants": len(meshes)})
                continue

            row = dict(base)
            row["n_pairs"] = len(pairs)
            row["n_mesh_variants"] = len(meshes)
            for scope, keep in (("same_frame", lambda p: canvas.is_same_frame(p["mesh_kind"])),
                                ("all", lambda p: True)):
                sub = [p for p in pairs if keep(p)]
                best, verdict = _verdict(sub)
                row[f"scope_{scope}_verdict"] = verdict if sub else NO_MESH
                row[f"scope_{scope}_n_pairs"] = len(sub)
                row[f"scope_{scope}_best_variant"] = None if not best else best["mesh_dir"]
                row[f"scope_{scope}_best_kind"] = None if not best else best["mesh_kind"]
                row[f"scope_{scope}_best_model_scale"] = None if not best else best["model_scale"]
                row[f"scope_{scope}_best_rs_lo"] = None if not best else best["rs_lo"]
                row[f"scope_{scope}_best_rs_hi"] = None if not best else best["rs_hi"]
                row[f"scope_{scope}_best_clean_scale"] = None if not best else best["clean_scale"]
                row[f"scope_{scope}_best_residual_px"] = None if not best else best["residual_max"]
                row[f"scope_{scope}_implied_rel_diff"] = None if not best else best["implied_scale_iso_rel_diff"]
                row[f"scope_{scope}_best_variant_names_volume"] = (
                    None if not best else best["variant_names_the_volume"])
            row["verdict"] = row["scope_all_verdict"]
            # The 2026-09-07 measurement assumed render scale 1 and tested only
            # the same-frame variants. Both assumptions are reported so the two
            # runs can be compared number for number.
            same_frame_pairs = [p for p in pairs if canvas.is_same_frame(p["mesh_kind"])]
            row["scope_same_frame_rs1_any_exact"] = any(
                p["exact_at_scale_1"] for p in same_frame_pairs) if same_frame_pairs else None
            row["scope_all_rs1_any_exact"] = any(p["exact_at_scale_1"] for p in pairs)
            row["variance_within_1px_only"] = (
                row["scope_all_verdict"] == NOT_REPRODUCED
                and (row.get("scope_all_best_residual_px") or 0) <= 1
            )
            volume_rows.append(row)

    return volume_rows, pair_rows


def _verdict(pairs: list[dict]):
    """Pick the best pair for a volume and turn it into a verdict."""
    if not pairs:
        return None, NO_MESH
    exact = [p for p in pairs if p["exact_at_model_scale"]]
    any_scale = [p for p in pairs if p["reproducible_any_scale"]]
    if exact:
        best = min(exact, key=lambda p: (len(p["mesh_kind"]), p["mesh_dir"]))
        return best, REPRODUCED_MODEL
    if any_scale:
        with_clean = [p for p in any_scale if p["clean_scale"] is not None]
        pool = with_clean or any_scale
        best = min(pool, key=lambda p: (p["rs_hi"] - p["rs_lo"]))
        return best, REPRODUCED_OTHER
    best = min(pairs, key=lambda p: (p["residual_max"], p["residual_h"] + p["residual_w"]))
    return best, NOT_REPRODUCED


# ------------------------------------------------------------------- #1892


def check_1892_empty_pyramid(inventory: dict) -> list[dict]:
    """Surface volumes that declare pyramid levels and hold no chunks at all.

    A reader that trusts ``.zarray`` sees a valid, correctly shaped dataset; the
    store is empty, so every chunk read returns ``fill_value`` and the failure is
    silent. The check reports, per volume, how many declared levels exist, how
    many are populated, and the shape a reader would get zeros for.
    """
    rows = []
    for seg in inventory["segments"]:
        for vol in seg["volumes"]:
            zattrs = vol.get("zattrs") or {}
            ms = zattrs.get("multiscales")
            n_declared = 0
            if isinstance(ms, list) and ms and isinstance(ms[0].get("datasets"), list):
                n_declared = len(ms[0]["datasets"])
            levels = vol.get("levels") or []
            present = [lv for lv in levels if lv.get("exists")]
            empty = [lv for lv in present if lv.get("n_data_keys") == 0 and lv.get("conclusive")]
            inconclusive = [lv for lv in present if not lv.get("conclusive")]
            l0 = (vol.get("level0") or {})
            if not levels:
                verdict = "not_probed"
            elif present and len(empty) == len(present):
                verdict = "no_chunks_at_any_level"
            elif empty:
                verdict = "chunks_missing_at_some_levels"
            elif inconclusive:
                verdict = "inconclusive"
            else:
                verdict = "populated"
            rows.append({
                "seg": seg["prefix"],
                "sample": seg["sample"],
                "volume": vol["dir"].rsplit("/", 1)[-1],
                "volume_dir": vol["dir"],
                "store_format": "zarr" if vol["dir"].endswith(".zarr") else
                                ("tif-stack" if vol["dir"].endswith(".tifs") else "other"),
                "in_catalogue": vol.get("in_catalogue"),
                "catalogue_types": ",".join(vol.get("catalogue_types") or []),
                "declared_levels": n_declared,
                "levels_present": len(present),
                "levels_empty": len(empty),
                "empty_levels": ",".join(str(lv["level"]) for lv in empty),
                "levels_inconclusive": len(inconclusive),
                "level0_shape_zyx": None if not l0.get("shape") else ",".join(str(v) for v in l0["shape"]),
                "level0_dtype": l0.get("dtype"),
                "level0_fill_value": l0.get("fill_value"),
                "level0_chunks": None if not l0.get("chunks") else ",".join(str(v) for v in l0["chunks"]),
                "verdict": verdict,
            })
    return rows


# ------------------------------------------------------------------- #1893


def check_1893_voxel_unit(inventory: dict) -> list[dict]:
    """Voxel sizes whose declared unit contradicts the value written beside it.

    ``vc_render_tifxyz`` takes the voxel size from the source volume's
    ``meta.json`` -- micrometres -- and writes it under the ``nanometer`` unit
    label its ``--voxel-unit`` flag defaults to. The published consequence is
    measurable: the declared scale equals the micrometre figure in the volume's
    own name while the axis unit says nanometre, so a consumer that converts
    units gets a pixel size 1000x too small.
    """
    rows = []
    for seg in inventory["segments"]:
        for vol in seg["volumes"]:
            zattrs = vol.get("zattrs") or {}
            ms = zattrs.get("multiscales")
            if not (isinstance(ms, list) and ms):
                continue
            entry = ms[0]
            units = [ax.get("unit") for ax in (entry.get("axes") or [])]
            datasets = entry.get("datasets") or []
            scale0 = None
            if datasets:
                ct = (datasets[0] or {}).get("coordinateTransformations") or []
                if ct and isinstance(ct[0].get("scale"), list):
                    scale0 = [float(v) for v in ct[0]["scale"]]
            name_um = None
            import re
            m = re.match(r"^([0-9.]+)um-", vol["dir"].rsplit("/", 1)[-1])
            if m:
                name_um = float(m.group(1))
            all_nm = bool(units) and all(u == "nanometer" for u in units if u)
            any_nm = any(u == "nanometer" for u in units if u)
            matches_name = None
            if scale0 is not None and name_um is not None:
                matches_name = all(abs(v - name_um) < 1e-9 for v in scale0)
            rows.append({
                "seg": seg["prefix"],
                "sample": seg["sample"],
                "volume": vol["dir"].rsplit("/", 1)[-1],
                "volume_dir": vol["dir"],
                "store_format": "zarr" if vol["dir"].endswith(".zarr") else
                                ("tif-stack" if vol["dir"].endswith(".tifs") else "other"),
                "declared_units": ",".join("" if u is None else u for u in units),
                "declared_scale_level0": None if scale0 is None else ",".join(f"{v:g}" for v in scale0),
                "name_voxel_um": name_um,
                "scale_matches_name_um": matches_name,
                "verdict": (
                    "micrometre_value_labelled_nanometer" if (any_nm and matches_name)
                    else "nanometer_unit_implausible_value" if (any_nm and scale0 and min(scale0) < 100)
                    else "units_absent" if not units or all(u is None for u in units)
                    else "consistent"
                ),
                "factor_if_converted": 1000 if (any_nm and matches_name) else None,
                "all_axes_nanometer": all_nm,
            })
    return rows


# ------------------------------------------------------------ catalogue-only


def check_1893_source(villa_src: str | None) -> list[dict]:
    """The #1893 defect as read from the renderer's source, line by line.

    The published volumes do not exhibit the defect (their ``--voxel-unit`` was
    passed explicitly), so the data-side check for #1893 comes back clean. The
    defect lives in the *default*: the unit label and the size come from
    different sources and nothing reconciles them. This check quotes the lines,
    with the file and line number, so the claim is readable rather than
    asserted.

    Pass the path to a ``villa`` checkout. Nothing is written or run; the file
    is only read.
    """
    if not villa_src:
        return []
    import os

    src = os.path.join(villa_src, "volume-cartographer/apps/src/vc_render_tifxyz.cpp")
    zarr = os.path.join(villa_src, "volume-cartographer/core/src/Zarr.cpp")
    patterns = [
        ("voxel-unit default", 'default_value("nanometer")'),
        ("size read from meta.json", "readVolumeVoxelSize"),
        ("unit passed to the writer", "voxelUnit"),
        ("unit parsed from meta.json", "voxel_unit"),
    ]
    rows = []
    for tag, needle in patterns:
        for path, kind in ((src, "vc_render_tifxyz.cpp"), (zarr, "Zarr.cpp")):
            if not os.path.exists(path):
                continue
            with open(path, errors="replace") as fh:
                for n, line in enumerate(fh, 1):
                    if needle in line:
                        rows.append({"file": kind, "line": n, "tag": tag,
                                     "code": line.strip()})
    return rows


def check_1734_bbox_marker(catalogue_doc: dict) -> list[dict]:
    """#1734: ``bbox_transformed`` is the ``-1`` sentinel scaled as a coordinate."""
    return catalogue.bbox_marker_rows(catalogue_doc)


def check_1730_scan_after_segment(catalogue_doc: dict, inventory: dict | None = None) -> list[dict]:
    """#1730: segments declaring a volume whose scan postdates the segment.

    The catalogue-only rows (which volume, how many days later) come from the
    date join; when an inventory is supplied each row also carries the z overrun
    of the segment's stored bbox against the volume shape the catalogue records,
    because that is the consequence a reader actually hits.
    """
    rows = catalogue.scan_after_segment_rows(catalogue_doc)
    if inventory is None:
        return rows
    overrun = {(r["sample"], r["segment"]): r for r in catalogue.declared_vs_stored_shape(catalogue_doc, inventory)}
    for row in rows:
        o = overrun.get((row["sample"], row["segment"])) or {}
        row["z_overrun_px"] = o.get("z_overrun")
        row["volume_shape_z"] = (o.get("volume_shape_zyx") or [None])[0]
    return rows


# ---------------------------------------------------------------- summaries


def summarize_1727(volume_rows: list[dict], pair_rows: list[dict]) -> dict:
    def count(scope: str, verdict: str) -> int:
        return sum(1 for r in volume_rows if r.get(f"scope_{scope}_verdict") == verdict)

    res = {
        "volumes": len(volume_rows),
        "pairs": len(pair_rows),
        "segments": len({r["seg"] for r in volume_rows}),
        "by_scope": {},
    }
    for scope in ("same_frame", "all"):
        single = [r for r in volume_rows if r.get(f"scope_{scope}_n_pairs")]
        res["by_scope"][scope] = {
            "volumes_tested": len(single),
            REPRODUCED_MODEL: count(scope, REPRODUCED_MODEL),
            REPRODUCED_OTHER: count(scope, REPRODUCED_OTHER),
            NOT_REPRODUCED: count(scope, NOT_REPRODUCED),
            NO_MESH: sum(1 for r in volume_rows if r.get(f"scope_{scope}_verdict") == NO_MESH),
        }
    # what the wider scope buys, and which variant kinds do the work
    gained = [
        r for r in volume_rows
        if r.get("scope_same_frame_verdict") in (NOT_REPRODUCED, NO_MESH)
        and r.get("scope_all_verdict") in (REPRODUCED_MODEL, REPRODUCED_OTHER)
    ]
    res["gained_from_all_variants"] = len(gained)
    kinds: dict[str, int] = {}
    for r in gained:
        k = r.get("scope_all_best_kind") or "?"
        kinds[k] = kinds.get(k, 0) + 1
    res["gained_by_variant_kind"] = kinds
    res["gained_best_variant_names_the_volume"] = sum(
        1 for r in gained if r.get("scope_all_best_variant_names_volume") is True)
    res["gained_best_variant_name_comparable"] = sum(
        1 for r in gained if r.get("scope_all_best_variant_names_volume") is not None)
    res["reproduced_model_variant_names_the_volume"] = sum(
        1 for r in volume_rows if r.get("scope_all_verdict") == REPRODUCED_MODEL
        and r.get("scope_all_best_variant_names_volume") is True)
    still = [r for r in volume_rows if r.get("scope_all_verdict") == NOT_REPRODUCED]
    res["still_not_reproduced"] = len(still)
    res["still_not_reproduced_by_sample"] = _tally(still, "sample")
    if still:
        resids = sorted(r["scope_all_best_residual_px"] for r in still if r.get("scope_all_best_residual_px") is not None)
        if resids:
            res["still_residual_px"] = {
                "n": len(resids),
                "median": resids[len(resids) // 2],
                "p90": resids[min(len(resids) - 1, int(0.9 * len(resids)))],
                "max": resids[-1],
            }
        rels = sorted(r["scope_all_implied_rel_diff"] for r in still
                      if r.get("scope_all_implied_rel_diff") is not None)
        if rels:
            res["still_implied_axis_rel_diff"] = {
                "median": rels[len(rels) // 2],
                "max": rels[-1],
            }
    res["reproduced_by_clean_scale"] = _tally(
        [r for r in volume_rows if r.get("scope_same_frame_verdict") == REPRODUCED_OTHER],
        "scope_same_frame_best_clean_scale",
    )
    res["canvas_mismatch_with_level0"] = sum(1 for r in volume_rows if r.get("canvas_matches_level0") is False)
    res["comparability_with_2026_09_07"] = {
        "volumes": len(volume_rows),
        "same_frame_scope_and_render_scale_1": sum(
            1 for r in volume_rows if r.get("scope_same_frame_rs1_any_exact")),
        "all_variants_render_scale_1": sum(
            1 for r in volume_rows if r.get("scope_all_rs1_any_exact")),
    }
    res["volumes_missing_zattrs"] = sum(1 for r in volume_rows if not r.get("zattrs_ok"))
    res["volumes_missing_level0"] = sum(1 for r in volume_rows if not r.get("level0_ok"))
    res["volumes_not_in_catalogue"] = sum(1 for r in volume_rows if r.get("in_catalogue") is False)
    return res


def _tally(rows: list[dict], key: str) -> dict:
    out: dict = {}
    for r in rows:
        k = r.get(key)
        k = "None" if k is None else str(k)
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))
