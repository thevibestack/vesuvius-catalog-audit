#!/usr/bin/env python3
"""Cross-check this audit against the 2026-09-07 measurement it extends.

The measurement published with issue #1727 shipped its per-pair data as
``audit/pairs.json`` in ``ge-al/vesuvius-cpu-repro``. That makes the two runs
comparable row by row instead of only in totals: for every (mesh variant,
surface volume) pair the earlier run recorded, this script recomputes the pair
from *this* run's pair table and reports agreement on

* the stored grid it read (``stored``),
* the size the renderer produces at render scale 1 (``renderer``), and
* the reference canvas it compared against (``ref``).

Disagreement is reported as rows, never smoothed over: a pair the earlier run
measured and this run does not have is listed, and so is the reverse.

Usage::

    python scripts/crosscheck_2026_09_07.py \\
        --theirs /path/to/vesuvius-cpu-repro/audit/pairs.json \\
        --mine audit/csv/1727_pairs.csv \\
        --theirs-inventory /path/to/vesuvius-cpu-repro/audit/inventory.json
"""

from __future__ import annotations

import argparse
import csv
import json
import sys


def key(seg: str, mesh_label: str, volume: str) -> tuple:
    return (seg, normalize_label(mesh_label), volume)


def normalize_label(label: str) -> str:
    """Drop a redundant leading ``mesh/`` so the two runs' labels line up.

    The earlier walk labels a legacy ``<seg>/mesh/tifxyz`` directory
    ``mesh/tifxyz``; this one labels it ``tifxyz``. Same directory, same pair --
    only the label differs, so it is normalised rather than reported as a
    difference.
    """
    s = label.strip("/")
    return s[len("mesh/"):] if s.startswith("mesh/") else s


def mesh_label(mesh_dir: str) -> str:
    """``.../mesh/intermediate/tifxyz_normalized`` -> ``intermediate/tifxyz_normalized``."""
    d = mesh_dir.rstrip("/")
    return normalize_label(d.split("/mesh/", 1)[1] if "/mesh/" in d else d)


def _lround(x: float) -> int:
    import math

    return math.floor(x + 0.5)


def _is_axis_order_difference(m: dict, t: dict) -> bool:
    """Is the disagreement the ``meta.json`` scale axis ordering?

    ``vc_render_tifxyz.cpp`` divides the **width** by ``_scale[0]`` and the
    **height** by ``_scale[1]``. The earlier run's published ``pairs.json``
    divides height by ``scale[0]`` and width by ``scale[1]`` -- its own review
    note says so ("``analyze.py`` originally read ``meta.json``'s ``scale`` as
    ``[y, x]`` ... Corrected; it affected only 11 anisotropic
    ``tifxyz_original`` meshes by <= 3 px"). For meshes whose two scale values
    are equal the order cannot matter, which is why the difference shows up on
    anistropic grids only. Reproducing their number with the swapped indices is
    what identifies the difference as that one, and not something else.
    """
    try:
        h, w = int(m["stored_h"]), int(m["stored_w"])
        sx, sy = float(m["scale_x"]), float(m["scale_y"])
    except (TypeError, ValueError):
        return False

    def f32(x):
        import struct

        return struct.unpack("<f", struct.pack("<f", x))[0]

    swapped = (_lround(h / f32(sx)), _lround(w / f32(sy)))
    return swapped == (int(t["renderer"][0]), int(t["renderer"][1]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--theirs", required=True, help="their audit/pairs.json")
    ap.add_argument("--mine", required=True, help="this run's audit/csv/1727_pairs.csv")
    ap.add_argument("--mine-volumes", default=None,
                    help="this run's audit/csv/1727_volumes.csv, for the reference canvas")
    ap.add_argument("--theirs-inventory", default=None, help="their audit/inventory.json")
    args = ap.parse_args(argv)

    theirs = json.load(open(args.theirs))
    mine = {}
    with open(args.mine) as fh:
        for row in csv.DictReader(fh):
            mine[key(row["seg"], mesh_label(row["mesh_dir"]), row["volume"])] = row

    canvas_of = {}
    if args.mine_volumes:
        with open(args.mine_volumes) as fh:
            for row in csv.DictReader(fh):
                canvas_of[row["volume_dir"]] = row.get("canvas_size_wh")

    both = agree_stored = agree_renderer = agree_ref = agree_match = 0
    missing_here, only_here, mismatches = [], [], []
    axis_order: list = []
    unexplained: list = []
    for t in theirs:
        k = key(t["seg"], t["mesh"], t["sv"])
        m = mine.get(k)
        if m is None:
            missing_here.append(k)
            continue
        both += 1
        stored_mine = [int(m["stored_h"]), int(m["stored_w"])]
        if stored_mine == [int(t["stored"][0]), int(t["stored"][1])]:
            agree_stored += 1
        if [int(m["size_at_scale_1_h"]), int(m["size_at_scale_1_w"])] == \
                [int(t["renderer"][0]), int(t["renderer"][1])]:
            agree_renderer += 1
        elif _is_axis_order_difference(m, t):
            axis_order.append((k, [m["size_at_scale_1_h"], m["size_at_scale_1_w"]], t["renderer"],
                               [m["stored_h"], m["stored_w"]], [m["scale_x"], m["scale_y"]]))
        else:
            unexplained.append((k, [m["size_at_scale_1_h"], m["size_at_scale_1_w"]], t["renderer"]))
        ref_mine = canvas_of.get(m["volume_dir"])
        if ref_mine and [int(x) for x in ref_mine.split(",")][::-1] == [int(t["ref"][0]), int(t["ref"][1])]:
            agree_ref += 1
        if str(m["exact_at_scale_1"]).lower() == str(bool(t["match_renderer"])).lower():
            agree_match += 1
        else:
            mismatches.append((k, m["exact_at_scale_1"], t["match_renderer"],
                               stored_mine, t["stored"], [m["model_h"], m["model_w"]], t["renderer"]))

    mine_keys = set(mine)
    theirs_keys = {key(t["seg"], t["mesh"], t["sv"]) for t in theirs}
    only_here = sorted(mine_keys - theirs_keys)

    print(f"their pairs:            {len(theirs)}")
    print(f"this run's pairs:       {len(mine)}")
    print(f"pairs in both:          {both}")
    print(f"  stored grid agrees:   {agree_stored}/{both}")
    print(f"  renderer size agrees: {agree_renderer}/{both}")
    print(f"  reference canvas:     {agree_ref}/{both}")
    print(f"  match verdict agrees: {agree_match}/{both}")
    print(f"  renderer-size differences elsewhere explained as the earlier run's "
          f"scale axis ordering: {len(axis_order)}/{both - agree_renderer}")
    for k, mine_size, their_size, stored, scale in axis_order[:5]:
        print(f"     ~ {k[1]:26s} stored={stored} scale={scale} mine={mine_size} theirs={their_size}")
    if unexplained:
        print(f"  UNEXPLAINED renderer-size differences: {len(unexplained)}")
        for u in unexplained[:10]:
            print("     ?", u)
    print(f"pairs they measured, absent here: {len(missing_here)}")
    for k in missing_here[:10]:
        print("   -", k)
    print(f"pairs measured here, absent there: {len(only_here)}")
    for k in only_here[:10]:
        print("   +", k)
    print(f"verdict mismatches: {len(mismatches)}")
    for m in mismatches[:10]:
        print("   !", m)

    if args.theirs_inventory:
        inv = json.load(open(args.theirs_inventory))
        n_mesh = sum(len(s["meshes"]) for s in inv)
        n_sv = sum(len(s["surface_volumes"]) for s in inv)
        kinds = {}
        for s in inv:
            for mesh in s["meshes"]:
                leaf = mesh["dir"].rstrip("/").rsplit("/", 1)[-1]
                kinds["dotted (.tifxyz)" if "." in leaf else "plain"] = \
                    kinds.get("dotted (.tifxyz)", 0) + 1
        print()
        print(f"their inventory: {len(inv)} segments, {n_mesh} mesh variants, {n_sv} surface volumes")
        print(f"  mesh directories whose leaf name contains a dot: {kinds.get('dotted (.tifxyz)', 0)}"
              f" of {n_mesh}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
