"""The renderer's canvas formula, and what it means to invert it.

``vc_render_tifxyz`` sizes the canvas it renders from the *stored* tifxyz grid::

    full_size = raw_points->size()                      // (height, width) of x.tif
    render_scale = tgt_scale * scale_seg * sA * ds_scale // all default to 1 * (2^-group)
    sx = render_scale / surf->_scale[0]                  // _scale[0] divides the WIDTH
    sy = render_scale / surf->_scale[1]
    width  = max(1, lround(width  * sx))
    height = max(1, lround(height * sy))

Source: ``volume-cartographer/apps/src/vc_render_tifxyz.cpp`` lines 1828-1845
(villa @ the commit recorded in the audit output). ``_scale`` is the ``scale``
array in the mesh's ``meta.json`` read as float32, which is why a mesh written
as 0.05 behaves as 0.05000000074505806 and a Python tool that divides in float64
lands one pixel short (villa #1699).

Inverting it is the whole audit question. Given a published surface volume and a
published mesh, two questions matter:

1. **At the documented default scale** (``tgt_scale=1``, ``scale_seg=1``, no
   affine, ``ds_scale = 2^-source_group``): does ``lround`` land exactly on the
   published ``canvas_size``? This is a yes/no fact about the published pair.
2. **At some scale**: is there *any* positive render scale that reproduces both
   axes at once? Eq. (:func:`feasible_interval`) is exact -- the interval where
   ``lround(stored * RS / scale) == target`` -- so the answer is analytical, not
   a search. An empty intersection means no isotropic render scale can produce
   that canvas from that mesh, whatever the pipeline did, and the size of the
   miss (:func:`best_residual`) says how far off it is.
"""

from __future__ import annotations

import math
import struct

# --------------------------------------------------------------------- float32


def f32(x: float) -> float:
    """Round a Python float to the nearest float32, as a C++ ``float`` would."""
    return struct.unpack("<f", struct.pack("<f", x))[0]


def lround(x: float) -> int:
    """C++ ``std::lround``: round half away from zero."""
    return math.floor(x + 0.5) if x >= 0 else math.ceil(x - 0.5)


# ----------------------------------------------------------------- the formula


def renderer_size(stored_hw: tuple[int, int], scale_xy: tuple[float, float], render_scale: float) -> tuple[int, int]:
    """``(height, width)`` the renderer would produce. ``scale_xy`` is meta.json ``scale``."""
    stored_h, stored_w = stored_hw
    sx_raw, sy_raw = scale_xy
    sx = render_scale / f32(sx_raw)  # width is divided by _scale[0]
    sy = render_scale / f32(sy_raw)
    return max(1, lround(stored_h * sy)), max(1, lround(stored_w * sx))


def model_render_scale(source_group: int | None) -> float:
    """The render scale of the documented default path: ``2^-source_group``.

    ``ds_scale = std::ldexp(1.0f, -group_idx)`` (vc_render_tifxyz.cpp:1527) and
    the writer records ``source_group = group_idx`` (Zarr.cpp:439). With the
    command-line defaults (``--scale 1``, ``--scale-segmentation 1``, no affine)
    this is the whole of ``render_scale``.
    """
    return 2.0 ** -int(source_group or 0)


# ----------------------------------------------------------- exact inversion


def feasible_interval(stored: int, scale: float, target: int) -> tuple[float, float] | None:
    """Positive render scales ``RS`` with ``max(1, lround(stored*RS/f32(scale))) == target``.

    ``lround`` rounds half away from zero, so the equality holds on
    ``[(target-0.5)*s/stored, (target+0.5)*s/stored)``. The ``max(1, ...)`` clamp
    makes the ``target == 1`` case one-sided: every ``RS`` below the upper bound
    clamps up to 1. If ``target == 1`` and ``1.5*s/stored`` would round to 1 or
    less, the interval is open at both ends in practice; we return it as
    ``[0, hi)`` and callers treat a lower bound of 0 as "arbitrarily small".
    """
    if target < 1 or stored <= 0:
        return None
    s = f32(scale)
    if not (s > 0):
        return None
    per_px = s / stored
    hi = (target + 0.5) * per_px
    lo = 0.0 if target == 1 else (target - 0.5) * per_px
    return (lo, hi)


def interval_intersect(a, b):
    if a is None or b is None:
        return None
    lo = max(a[0], b[0])
    hi = min(a[1], b[1])
    return (lo, hi) if hi > lo else None


def implied_scale(stored: int, scale: float, target: int) -> float:
    """The render scale that would make the axis land on ``target``, to the float."""
    if stored <= 0:
        return float("nan")
    return target * f32(scale) / stored


# ------------------------------------------------------------- pair verdicts

#: Render scales that a human would plausibly have typed on the command line.
CLEAN_SCALES = (1.0, 2.0, 4.0, 8.0, 0.5, 0.25, 0.125, 0.0625, 16.0, 3.0, 6.0, 12.0)


def classify_pair(
    stored_hw: tuple[int, int],
    scale_xy: tuple[float, float],
    target_hw: tuple[int, int],
    source_group: int | None = None,
    model_scale: float | None = None,
) -> dict:
    """Everything knowable about one (mesh variant, surface volume) pair.

    ``stored_hw`` is the mesh's ``x.tif`` ``(height, width)``; ``target_hw`` is
    the volume's published ``(height, width)``. Nothing here is a heuristic
    except :data:`CLEAN_SCALES`, which is only used to name a scale, never to
    decide reproducibility.
    """
    stored_h, stored_w = stored_hw
    target_h, target_w = target_hw
    sx, sy = scale_xy

    ms = model_render_scale(source_group) if model_scale is None else model_scale
    model_size = renderer_size(stored_hw, scale_xy, ms)
    size_at_1 = renderer_size(stored_hw, scale_xy, 1.0)

    i_h = implied_scale(stored_h, sy, target_h)
    i_w = implied_scale(stored_w, sx, target_w)
    if i_h > 0 and i_w > 0:
        iso_rel_diff = abs(i_h - i_w) / min(i_h, i_w)
    else:
        iso_rel_diff = float("inf")

    iv = interval_intersect(
        feasible_interval(stored_h, sy, target_h),
        feasible_interval(stored_w, sx, target_w),
    )
    exact_model = model_size == (target_h, target_w)
    clean = None
    if iv is not None:
        for cand in CLEAN_SCALES:
            if iv[0] <= cand < iv[1] or (iv[0] == 0.0 and cand < iv[1]):
                clean = cand
                break

    res = best_residual(stored_hw, scale_xy, target_hw, iv)
    return {
        "stored_h": stored_h,
        "stored_w": stored_w,
        "scale_x": sx,
        "scale_y": sy,
        "model_scale": ms,
        "model_h": model_size[0],
        "model_w": model_size[1],
        "size_at_scale_1_h": size_at_1[0],
        "size_at_scale_1_w": size_at_1[1],
        "exact_at_model_scale": exact_model,
        "exact_at_scale_1": (size_at_1[0], size_at_1[1]) == (target_h, target_w),
        "implied_scale_h": i_h,
        "implied_scale_w": i_w,
        "implied_scale_iso_rel_diff": iso_rel_diff,
        "reproducible_any_scale": iv is not None,
        "rs_lo": None if iv is None else iv[0],
        "rs_hi": None if iv is None else iv[1],
        "clean_scale": clean,
        "residual_h": res["residual_h"],
        "residual_w": res["residual_w"],
        "residual_max": res["residual_max"],
        "residual_rs": res["rs"],
    }


def best_residual(
    stored_hw: tuple[int, int],
    scale_xy: tuple[float, float],
    target_hw: tuple[int, int],
    iv: tuple[float, float] | None,
) -> dict:
    """The smallest miss in pixels over all render scales, and the scale achieving it.

    With a non-empty interval the answer is 0 (and this is the definition of
    "reproducible"). Otherwise the best achievable ``max(|dH|, |dW|)`` is
    attained at one of the interval breakpoints -- the objective is piecewise
    constant between breakpoints -- so we evaluate the candidates directly
    instead of scanning.
    """
    if iv is not None:
        return {"residual_h": 0, "residual_w": 0, "residual_max": 0, "rs": (iv[0] + iv[1]) / 2}

    stored_h, stored_w = stored_hw
    target_h, target_w = target_hw
    sx, sy = scale_xy

    bounds = [
        feasible_interval(stored_h, sy, target_h),
        feasible_interval(stored_w, sx, target_w),
    ]
    cands: set[float] = {implied_scale(stored_h, sy, target_h), implied_scale(stored_w, sx, target_w)}
    for b in bounds:
        if b:
            cands.add(b[0])
            cands.add(b[1])
    cands = {c for c in cands if math.isfinite(c) and c > 0}

    best = None
    for rs in sorted(cands):
        h, w = renderer_size(stored_hw, scale_xy, rs)
        dh, dw = abs(h - target_h), abs(w - target_w)
        key = (max(dh, dw), dh + dw)
        if best is None or key < best[0]:
            best = (key, dh, dw, rs)
    if best is None:
        return {"residual_h": None, "residual_w": None, "residual_max": None, "rs": None}
    _, dh, dw, rs = best
    return {"residual_h": dh, "residual_w": dw, "residual_max": max(dh, dw), "rs": rs}


# --------------------------------------------------------------- variant kinds

#: Mesh variant directory names, in the order the pipeline produces them.
VARIANT_INTERMEDIATE = ("tifxyz_original", "tifxyz_normalized", "tifxyz_flattened")


def mesh_variant_kind(mesh_dir: str) -> str:
    """Classify a mesh directory from the part of its path below ``.../mesh/``.

    ``intermediate/tifxyz_normalized`` and friends are the same-frame variants:
    the surface expressed in the frame of the scan it was traced on. A directory
    ending in ``-on-<volume_id>-<voxel>um.tifxyz`` is a *cross-scan* variant:
    the same surface transformed into another scan's frame, which is what a
    render of that other scan would have been made from.
    """
    d = mesh_dir.rstrip("/")
    if d.endswith("/mesh"):
        return "mesh-root"           # legacy layout: meta.json directly under mesh/
    rel = d.split("/mesh/", 1)[1] if "/mesh/" in d else d
    if not rel:
        return "mesh-root"
    parts = rel.split("/")
    if len(parts) == 1:
        leaf = parts[0]
        if leaf.endswith(".tifxyz") and "-on-" in leaf:
            return "transformed"
        # ``mesh/tifxyz`` is the legacy name for the traced grid: same frame,
        # older layout. It is *not* a cross-scan variant, so it stays in the
        # same-frame scope.
        return "legacy-tifxyz"
    if parts[0] == "intermediate":
        leaf = parts[-1]
        return leaf if leaf in VARIANT_INTERMEDIATE else f"intermediate/{leaf}"
    return "other"


def is_same_frame(kind: str) -> bool:
    """Same-frame variants only: the scope the 2026-09-07 measurement used.

    ``intermediate/tifxyz_*`` plus the legacy ``<seg>/mesh/tifxyz`` layout. The
    cross-scan ``-on-<volume>.tifxyz`` variants are excluded, which is what makes
    this scope comparable with the published 262/628.
    """
    return kind in VARIANT_INTERMEDIATE or kind in ("mesh-root", "legacy-tifxyz")
