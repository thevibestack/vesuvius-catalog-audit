"""Command line: ``python -m vcaudit collect | analyze | all``."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time

from . import __version__, catalogue as catalogue_mod, checks, inventory as inv_mod, report
from .s3 import AnonymousS3, DEFAULT_BUCKET_URL


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def cmd_collect(args) -> int:
    s3 = AnonymousS3(args.bucket_url)
    t0 = time.time()
    print(f"fetching the catalogue from {s3.base_url}{catalogue_mod.CATALOGUE_KEY}")
    cat = catalogue_mod.fetch_catalogue(s3)
    report.write_json(args.catalogue_out, {k: v for k, v in cat.items() if not k.startswith("__")})
    report.write_json(args.catalogue_out + ".provenance.json",
                      {k: v for k, v in cat.items() if k.startswith("__")})
    n_seg = sum(len(v.get("segments") or {}) for v in cat.get("samples", {}).values())
    print(f"catalogue: {len(cat.get('samples', {}))} samples, {n_seg} segments")

    def progress(done, total, elapsed):
        print(f"  walked {done}/{total} segments in {elapsed:.0f}s "
              f"({s3.requests} requests, {s3.bytes_read / 1e6:.1f} MB)", flush=True)

    doc = inv_mod.collect(s3, cat, workers=args.workers, limit_segments=args.limit,
                          deep=not args.no_deep, progress=progress)
    doc["villa_commit"] = args.villa_commit
    report.write_json(args.out, doc)
    st = doc["stats"]
    print(f"inventory: {st['segments']} segments, {st['meshes']} mesh variants, "
          f"{st['volumes']} surface volumes, {st['errors']} errors")
    print(f"transport: {st['http_requests']} requests, {st['bytes_read'] / 1e6:.1f} MB read, "
          f"{time.time() - t0:.0f}s wall")
    print(f"wrote {args.out} and {args.catalogue_out}")
    return 0


def cmd_analyze(args) -> int:
    with open(args.inventory) as fh:
        inv = json.load(fh)
    with open(args.catalogue) as fh:
        cat = json.load(fh)
    prov_path = args.catalogue + ".provenance.json"
    prov = json.load(open(prov_path)) if os.path.exists(prov_path) else {}

    volume_rows, pair_rows = checks.check_1727_canvas(inv)
    rows_1892 = checks.check_1892_empty_pyramid(inv)
    rows_1893 = checks.check_1893_voxel_unit(inv)
    source_rows = checks.check_1893_source(args.villa_src)
    rows_1734 = checks.check_1734_bbox_marker(cat)
    rows_1730 = checks.check_1730_scan_after_segment(cat, inv)
    coverage_1730 = catalogue_mod.scan_date_coverage(cat)
    s1727 = checks.summarize_1727(volume_rows, pair_rows)

    report.write_csv(os.path.join(args.outdir, "csv", "1727_volumes.csv"), volume_rows)
    report.write_csv(os.path.join(args.outdir, "csv", "1727_pairs.csv"), pair_rows)
    report.write_csv(os.path.join(args.outdir, "csv", "1892_empty_pyramid.csv"), rows_1892)
    report.write_csv(os.path.join(args.outdir, "csv", "1893_voxel_unit.csv"), rows_1893)
    report.write_csv(os.path.join(args.outdir, "csv", "1893_source_lines.csv"), source_rows)
    report.write_csv(os.path.join(args.outdir, "csv", "1734_bbox_marker.csv"), rows_1734)
    report.write_csv(os.path.join(args.outdir, "csv", "1730_scan_after_segment.csv"), rows_1730)

    # The measurement's actionable output: the volumes the narrower scope called
    # non-reproducible and a published cross-scan variant reproduces exactly.
    recovered = [
        {k: r.get(k) for k in ("sample", "seg", "volume", "volume_dir", "canvas_size_wh",
                               "level0_zyx", "source_group",
                               "scope_all_best_variant", "scope_all_best_kind",
                               "scope_all_best_variant_names_volume",
                               "scope_all_best_rs_lo", "scope_all_best_rs_hi")}
        for r in volume_rows
        if r.get("scope_same_frame_verdict") == checks.NOT_REPRODUCED
        and r.get("scope_all_verdict") == checks.REPRODUCED_MODEL
    ]
    report.write_csv(os.path.join(args.outdir, "csv", "1727_crossscan_recovered.csv"), recovered)

    meta = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "bucket_url": inv.get("bucket_url") or DEFAULT_BUCKET_URL,
        "bucket": inv.get("bucket"),
        "catalogue_generated_at": prov.get("__fetched_at__"),
        "catalogue_source": prov.get("__source__"),
        "villa_commit": args.villa_commit or inv.get("villa_commit"),
        "version": __version__,
    }
    summary = {
        "meta": meta,
        "inventory_stats": inv.get("stats", {}),
        "checks": {
            "1727": s1727,
            "1892": _verdict_tally(rows_1892),
            "1893": _verdict_tally(rows_1893),
            "1893_source_lines": len(source_rows),
            "1734": {"marker_segments": len(rows_1734),
                     "exact_scaled_marker": sum(1 for r in rows_1734 if r["exact_scaled_marker"])},
            "1730": {"segments": len(rows_1730),
                     "with_z_overrun": sum(1 for r in rows_1730 if (r.get("z_overrun_px") or 0) > 0),
                     "coverage": coverage_1730},
        },
        "inputs": {
            "inventory": os.path.abspath(args.inventory),
            "inventory_sha256": _sha256(args.inventory),
            "catalogue": os.path.abspath(args.catalogue),
            "catalogue_sha256": _sha256(args.catalogue),
        },
    }
    report.write_json(os.path.join(args.outdir, "summary.json"), summary)
    md = report.summary_markdown(meta, s1727, rows_1892, rows_1893, rows_1734, rows_1730,
                                inv.get("stats", {}), inv.get("drift"), source_rows,
                                coverage_1730)
    path = os.path.join(args.outdir, "SUMMARY.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(md)
    print(md)
    print(f"wrote CSV files, summary.json and SUMMARY.md under {args.outdir}")
    return 0


def _verdict_tally(rows):
    out: dict = {}
    for r in rows:
        out[r["verdict"]] = out.get(r["verdict"], 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="vcaudit", description=__doc__)
    p.add_argument("--version", action="version", version=f"vcaudit {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="walk the bucket (metadata only) into a raw inventory")
    c.add_argument("--out", default="audit/raw/inventory.json")
    c.add_argument("--catalogue-out", default="audit/raw/catalogue.json")
    c.add_argument("--bucket-url", default=DEFAULT_BUCKET_URL)
    c.add_argument("--workers", type=int, default=24)
    c.add_argument("--limit", type=int, default=None, help="only the first N segments (smoke test)")
    c.add_argument("--no-deep", action="store_true", help="skip the per-level chunk probe")
    c.add_argument("--villa-commit", default=None)
    c.set_defaults(func=cmd_collect)

    a = sub.add_parser("analyze", help="run the checks over a saved inventory")
    a.add_argument("--inventory", default="audit/raw/inventory.json")
    a.add_argument("--catalogue", default="audit/raw/catalogue.json")
    a.add_argument("--outdir", default="audit")
    a.add_argument("--villa-commit", default=None)
    a.add_argument("--villa-src", default=None,
                   help="path to a ScrollPrize/villa checkout, for the #1893 source check")
    a.set_defaults(func=cmd_analyze)

    b = sub.add_parser("all", help="collect, then analyze")
    b.add_argument("--outdir", default="audit")
    b.add_argument("--bucket-url", default=DEFAULT_BUCKET_URL)
    b.add_argument("--workers", type=int, default=24)
    b.add_argument("--limit", type=int, default=None)
    b.add_argument("--no-deep", action="store_true")
    b.add_argument("--villa-commit", default=None)
    b.add_argument("--villa-src", default=None)
    b.set_defaults(func=cmd_all)

    args = p.parse_args(argv)
    return args.func(args)


def cmd_all(args) -> int:
    raw = os.path.join(args.outdir, "raw")
    rc = cmd_collect(argparse.Namespace(
        out=os.path.join(raw, "inventory.json"),
        catalogue_out=os.path.join(raw, "catalogue.json"),
        bucket_url=args.bucket_url, workers=args.workers, limit=args.limit,
        no_deep=args.no_deep, villa_commit=args.villa_commit,
    ))
    if rc:
        return rc
    return cmd_analyze(argparse.Namespace(
        inventory=os.path.join(raw, "inventory.json"),
        catalogue=os.path.join(raw, "catalogue.json"),
        outdir=args.outdir, villa_commit=args.villa_commit, villa_src=args.villa_src,
    ))


if __name__ == "__main__":
    sys.exit(main())
