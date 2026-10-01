"""Write the audit's outputs: CSV per check, a JSON of the headline counts, and a summary."""

from __future__ import annotations

import csv
import json
import os
import time


def write_csv(path: str, rows: list[dict]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cols: list[str] = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: _cell(r.get(k)) for k in cols})


def _cell(v):
    if isinstance(v, float):
        if v != v:  # NaN
            return "nan"
        return f"{v:.6g}"
    if isinstance(v, (list, tuple)):
        return ";".join(str(x) for x in v)
    if v is None:
        return ""
    return v


def write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(obj, fh, indent=1, sort_keys=False)
        fh.write("\n")


def _table(rows: list[tuple], header: tuple) -> str:
    out = ["| " + " | ".join(str(h) for h in header) + " |",
           "|" + "|".join("---" for _ in header) + "|"]
    for r in rows:
        out.append("| " + " | ".join("" if c is None else str(c) for c in r) + " |")
    return "\n".join(out)


#: Verdict names shared with :mod:`vcaudit.checks` (kept as literals so this
#: module can format a summary read back from JSON without importing the checks).
REPRODUCED_MODEL_KEY = "reproduced_at_documented_scale"
REPRODUCED_OTHER_KEY = "reproduced_at_another_scale"
NOT_REPRODUCED_KEY = "not_reproduced_from_any_published_variant"
NO_MESH_KEY = "no_mesh_variant_available"


def summary_markdown(meta: dict, s1727: dict, rows_1892: list[dict], rows_1893: list[dict],
                     rows_1734: list[dict], rows_1730: list[dict], inventory_stats: dict,
                     drift: dict | None = None, source_rows: list[dict] | None = None,
                     coverage_1730: dict | None = None) -> str:
    """The generated half of the report: counts, only from what was measured."""
    source_rows = source_rows or []
    lines = [
        "# Vesuvius open-data catalogue audit — generated summary",
        "",
        f"- run at: `{meta['generated_at']}`",
        f"- bucket: `{meta['bucket_url']}` (anonymous, metadata and headers only)",
        f"- catalogue `metadata.json` fetched: `{meta.get('catalogue_generated_at')}`",
        f"- villa commit the formula is quoted from: `{meta.get('villa_commit')}`",
        f"- auditor version: `{meta.get('version')}`",
        "",
        "## What was read",
        "",
        _table(
            [
                ("segment directories", inventory_stats.get("segments")),
                ("mesh variants (meta.json + x.tif header)", inventory_stats.get("meshes")),
                ("surface volumes (.zattrs + level-0 .zarray)", inventory_stats.get("volumes")),
                ("mesh variants the catalogue indexes", inventory_stats.get("meshes_in_catalogue")),
                ("surface volumes the catalogue indexes", inventory_stats.get("volumes_in_catalogue")),
                ("HTTP requests", inventory_stats.get("http_requests")),
                ("bytes read", inventory_stats.get("bytes_read")),
                ("collector errors", inventory_stats.get("errors")),
            ],
            ("quantity", "value"),
        ),
        "",
    ]
    if drift:
        rows = [("segments in the catalogue", drift.get("catalogue_segments")),
                ("segment directories in the bucket", drift.get("bucket_segment_dirs"))]
        if drift.get("limited"):
            rows.append(("catalogue segments with no bucket directory",
                         "not computed: the walk was limited to a sample"))
        else:
            rows.append(("catalogue segments with no bucket directory",
                         len(drift.get("catalogue_segments_not_in_bucket") or [])))
        ignored = drift.get("non_segment_dirs_ignored") or []
        if ignored:
            rows.append((f"non-segment directories under `segments/` (ignored)",
                         len(ignored)))
        lines += ["## Catalogue vs bucket", "", _table(rows, ("quantity", "value")), ""]
    lines += [
        "## #1727 — surface volumes reproducible from a published mesh",
        "",
        _table(
            [
                (scope, d["volumes_tested"], d[REPRODUCED_MODEL_KEY], d[REPRODUCED_OTHER_KEY],
                 d[NOT_REPRODUCED_KEY], d.get(NO_MESH_KEY))
                for scope, d in s1727["by_scope"].items()
            ],
            ("mesh-variant scope", "volumes tested", "reproduced at documented scale",
             "reproduced at another scale", "not reproducible", "no mesh available"),
        ),
        "",
        f"- volumes gained by widening the scope to every published variant: "
        f"**{s1727['gained_from_all_variants']}** "
        f"({_kv(s1727['gained_by_variant_kind'])}); of them, "
        f"**{s1727.get('gained_best_variant_names_the_volume')}** are reproduced by a variant whose "
        f"own name carries the volume's id and voxel size "
        f"({s1727.get('gained_best_variant_name_comparable')} comparable)",
        f"- still not reproducible under any published variant: **{s1727['still_not_reproduced']}** "
        f"({_kv(s1727['still_not_reproduced_by_sample'])})",
        f"- `canvas_size` disagreeing with level 0's shape: {s1727['canvas_mismatch_with_level0']}",
        f"- volumes with no `.zattrs`: {s1727['volumes_missing_zattrs']}; "
        f"with no level-0 `.zarray`: {s1727['volumes_missing_level0']}; "
        f"not indexed by the catalogue: {s1727['volumes_not_in_catalogue']}",
    ]
    if "still_residual_px" in s1727:
        r = s1727["still_residual_px"]
        lines.append(f"- residual of the closest achievable canvas over the still-not-reproducible "
                     f"(n={r['n']}): median {r['median']} px, p90 {r['p90']} px, max {r['max']} px")
    if "still_implied_axis_rel_diff" in s1727:
        r = s1727["still_implied_axis_rel_diff"]
        lines.append(f"- for those, the render scale implied by height and by width differ by a "
                     f"median {r['median'] * 100:.3f} %, max {r['max'] * 100:.3f} %")
    if s1727.get("comparability_with_2026_09_07"):
        c = s1727["comparability_with_2026_09_07"]
        lines.append(
            f"- under the 2026-09-07 assumptions (same-frame variants only, render scale 1): "
            f"**{c['same_frame_scope_and_render_scale_1']} of {c['volumes']}** reproduce exactly; "
            f"over every published variant: {c['all_variants_render_scale_1']} of {c['volumes']}")
    if s1727.get("reproduced_by_clean_scale"):
        lines.append(f"- scales named by the other-scale reproductions: "
                     f"{_kv(s1727['reproduced_by_clean_scale'])}")

    lines += [
        "",
        "## #1892 — declared pyramid levels with no chunks",
        "",
        _table(
            [(k, v) for k, v in _verdicts(rows_1892).items()],
            ("verdict", "volumes"),
        ),
    ]
    empty = [r for r in rows_1892 if r["verdict"] == "no_chunks_at_any_level"]
    if empty:
        lines += ["", "Volumes that declare levels and hold no chunk at any of them:", ""]
        lines.append(_table(
            [(r["volume_dir"], r["declared_levels"], r["level0_shape_zyx"], r["level0_dtype"],
              r["level0_fill_value"], r["catalogue_types"]) for r in empty],
            ("volume", "levels", "level-0 shape zyx", "dtype", "fill_value", "catalogue artifact types"),
        ))

    lines += [
        "",
        "## #1893 — voxel size written under a contradicting unit label",
        "",
        _table([(k, v) for k, v in _verdicts(rows_1893).items()], ("verdict", "volumes")),
        "",
        (f"- source check: {len(source_rows)} matching lines quoted from the renderer "
         f"(see `csv/1893_source_lines.csv`); the defect is in the default the code applies, "
         f"not in any published volume's metadata"
         if source_rows else
         "- source check: not run (pass `--villa-src <checkout of ScrollPrize/villa>`)"),
    ]
    bad = [r for r in rows_1893 if r["verdict"] == "micrometre_value_labelled_nanometer"]
    missing = [r for r in rows_1893 if r["verdict"] == "units_absent"]
    if missing:
        lines += ["",
                  f"- {len(missing)} Zarr surface volumes declare their axes without any `unit`, "
                  f"and a `scale` of 1,1,1, while the volume's own name carries the voxel size "
                  f"(see `csv/1893_voxel_unit.csv`): a consumer that reads the physical pixel size "
                  f"from the metadata gets `1` where the name says 8.64 um"]
    if bad:
        lines += ["", "Volumes whose declared scale equals the micrometre figure in their own name "
                      "while the axis unit says nanometre:", ""]
        lines.append(_table(
            [(r["volume_dir"], r["declared_scale_level0"], r["name_voxel_um"], r["declared_units"])
             for r in bad],
            ("volume", "declared scale", "name voxel (um)", "declared units"),
        ))

    lines += [
        "",
        "## #1734 — `bbox_transformed` built from the `-1` marker",
        "",
        _table(
            [("segments whose stored bbox carries the marker", len(rows_1734)),
             ("of those, `bbox_transformed` == stored x downscale exactly", sum(1 for r in rows_1734 if r["exact_scaled_marker"])),
             ("by sample", _kv(_tally(rows_1734, "sample")))],
            ("quantity", "value"),
        ),
        "",
        "## #1730 — segments declaring a volume scanned after them",
        "",
        _table(
            [("segments", len(rows_1730)),
             ("segments the date join could examine", (coverage_1730 or {}).get(
                 "segments_with_resolvable_scan_date", "not computed")),
             ("by sample", _kv(_tally(rows_1730, "sample"))),
             ("median days the scan is later", _median([r["scan_is_later_days"] for r in rows_1730])),
             ("of those, stored bbox overruns the declared volume's z extent",
              sum(1 for r in rows_1730 if (r.get("z_overrun_px") or 0) > 0))],
            ("quantity", "value"),
        ),
        "",
        "---",
        "",
        "Every number above is computed from the files named in `audit/raw/` and the CSV files "
        "beside this document. Regenerate with `python -m vcaudit all --outdir audit`.",
        "",
    ]
    return "\n".join(lines)


REPRODUCED_MODEL_KEY = "reproduced_at_documented_scale"
REPRODUCED_OTHER_KEY = "reproduced_at_another_scale"
NOT_REPRODUCED_KEY = "not_reproduced_from_any_published_variant"


def _kv(d: dict) -> str:
    if not d:
        return "none"
    return ", ".join(f"{k}: {v}" for k, v in d.items())


def _verdicts(rows: list[dict]) -> dict:
    out: dict = {}
    for r in rows:
        out[r["verdict"]] = out.get(r["verdict"], 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def _tally(rows: list[dict], key: str) -> dict:
    out: dict = {}
    for r in rows:
        k = str(r.get(key))
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def _median(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    vals = sorted(vals)
    return vals[len(vals) // 2]
