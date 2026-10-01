# vesuvius-catalog-audit

**Does the Vesuvius Challenge open-data catalogue reproduce from itself?**

This is an anonymous, read-only auditor for `s3://vesuvius-challenge-open-data`. It
walks the bucket, reads **metadata and file headers only** — `.zattrs`, `.zarray`,
`meta.json`, S3 listings, and the first bytes of each `tifxyz` grid — and asks, for
every published surface volume, whether its declared canvas can be reproduced from a
published mesh using the formula in `vc_render_tifxyz`. It then repeats the same
treatment for four other published-metadata defects. No credentials, no chunk bytes,
no GPU, stdlib only: it runs anywhere `python3` runs.

The point is not to list defects. It is to answer the question a consumer actually
has — *can I trust what this catalogue says* — with a per-artifact answer, on the
whole catalogue, reproducibly.

Written and run on 2026-10-01, before any fix, so that the numbers can be checked
against the same bucket state.

## Results at a glance

Read on **2026-10-01** — 323 segments, 1536 mesh variants, 697 surface volumes,
16,821 HTTP requests, 26.5 MB read, 0 collector errors, 2m57s on an M1 Mac mini.
Catalogue `metadata.json` fetched `2026-10-01T13:35:42Z`; formula quoted from
`ScrollPrize/villa` @ `7d6a82c71ed05ac1bd5c2e956d15400b564583d5`.

| # | what it asks | result |
|---|---|---|
| [#1727](https://github.com/ScrollPrize/villa/issues/1727) | is each volume reproducible from a published mesh? | **693 of 693 reproduce exactly** from a published mesh variant, at the render scale the volume's own `.zattrs` declares. 388 of them only when the cross-scan `-on-<volume>.tifxyz` variants are in scope; all 388 are reproduced by the variant whose name carries that volume's id and voxel size. **0 volumes are left unreproducible.** |
| [#1892](https://github.com/ScrollPrize/villa/issues/1892) | do the declared levels hold chunks? | 1 of the **693 Zarr volumes that declare levels** holds **no chunk at any of its 6 levels** (PHerc0814 `20260226123353`, 1.129um L1): every read returns `fill_value` with no error. (The other 4 of the 697 are **TIFF stacks**, which declare no Zarr levels at all.) |
| [#1893](https://github.com/ScrollPrize/villa/issues/1893) | does the unit label describe the value? | the code defect is live (the `--voxel-unit` default is still `nanometer` at `7d6a82c`), but **0 of 693 readable volumes declare `nanometer`** — the published corpus does not carry it. **4 volumes declare no unit at all** and a `scale` of 1,1,1 while their own name says the voxel size. |
| [#1734](https://github.com/ScrollPrize/villa/issues/1734) | is the `-1` marker scaled as a coordinate? | For **28 of 28** segments, the volume's **own** entry in `bbox_transformed` equals the stored bbox × `original_volume_downscale` exactly. Scope note: that is the self entry only — of the 140 `bbox_transformed` entries carried by those 28 segments, **112 are real transformations** to sibling volumes, which this check does not cover. |
| [#1730](https://github.com/ScrollPrize/villa/issues/1730) | do segments declare a volume scanned after them? | **20 of 323** segments examined (median 6 days later), and in 8 of them the stored bbox runs past the declared volume's z extent. |

Each number above is recomputable from the CSV and the raw documents in `audit/`,
and the #1727 result is cross-checked pair by pair against the earlier
2026-09-07 measurement — **1260 of 1260 pairs agree**. See [`FINDINGS.md`](FINDINGS.md)
for what each result means, how to verify it, and where the earlier measurement and
this one differ.

## Run it

Requires Python 3.9+ and nothing else. No `pip install`, no virtualenv, no AWS
credentials, no environment variables.

```console
$ python3 -m unittest discover -s tests     # 48 tests, ~0.3s, no network
$ python3 -m vcaudit all --outdir audit     # the whole audit, ~3 min
```

(or `make test` / `make audit`. The `Makefile` pins the villa commit and path.)

`all` = `collect` then `analyze`. They are separate on purpose: `collect` is the
only part that touches the network, and `analyze` reads the two documents it
produced, so a re-analysis costs nothing and an offline reviewer can re-run every
check from the JSON that shipped with the report.

```console
# 1. read the bucket (the only network step)
$ python3 -m vcaudit collect \
      --out audit/raw/inventory.json \
      --catalogue-out audit/raw/catalogue.json \
      --workers 24

# 2. re-derive every number from those two files (no network)
$ python3 -m vcaudit analyze \
      --inventory audit/raw/inventory.json \
      --catalogue audit/raw/catalogue.json \
      --villa-src /path/to/ScrollPrize/villa \
      --villa-commit 7d6a82c71ed05ac1bd5c2e956d15400b564583d5 \
      --outdir audit

# a quick partial walk, for a smoke test
$ python3 -m vcaudit all --outdir /tmp/smoke --limit 6 --workers 8
```

Useful flags: `--bucket-url` (another mirror), `--limit N` (first N segments only),
`--workers N` (concurrent segment walks), `--no-deep` (skip the per-level chunk
probe, for #1892), `--villa-src PATH` (quote the renderer's own lines for #1893;
without it that one check reports *not run*, it does not guess).

`analyze` accepts the `collect` output of a *different* run, including the one
committed here, so a reviewer can re-derive the published numbers without
re-reading the bucket.

## What it writes

| path | what it is |
|---|---|
| `audit/SUMMARY.md` | the generated report: every count, one section per issue |
| `audit/summary.json` | the same counts as data, plus the SHA-256 of both inputs |
| `audit/csv/1727_volumes.csv` | **one row per surface volume**: canvas vs level-0 shape, and for each of the two mesh scopes the verdict, the best-matching variant, the residual in pixels, the render scale that reproduces it |
| `audit/csv/1727_pairs.csv` | one row per (mesh variant × volume) pair actually tested — 3650 of them |
| `audit/csv/1727_crossscan_recovered.csv` | the 388 volumes the narrower scope called non-reproducible and a published cross-scan mesh reproduces exactly |
| `audit/csv/1892_empty_pyramid.csv` | per volume: declared levels, chunks seen per level, level-0 shape and dtype |
| `audit/csv/1893_voxel_unit.csv` | per volume: axis units, declared level-0 scale, and the µm figure in its own name |
| `audit/csv/1893_source_lines.csv` | the renderer/`Zarr.cpp` lines quoted for the source check |
| `audit/csv/1734_bbox_marker.csv` | the 28 segments whose stored bbox carries the marker, with the exact scaled value |
| `audit/csv/1730_scan_after_segment.csv` | the 20 segments, with both dates, the gap, and the z overrun |
| `audit/RUN-LOG.txt`, `audit/TESTS.txt` | the console output and test run of the published numbers |
| `audit/CROSSCHECK-2026-09-07.txt` | the pair-by-pair agreement with the earlier measurement |

`audit/raw/` holds the collected documents (`inventory.json`, `catalogue.json`).
They are large and regenerable, so they are git-ignored; `summary.json` records
their SHA-256 so a re-collection can be compared to the one behind these numbers.

`audit/SUMMARY.md` and the CSVs shipped here are from the single run logged in
`audit/RUN-LOG.txt` (its `generated_at` is that run). Re-running any command rewrites
them with a new timestamp; the counts should match, and a mismatch is worth
reporting, because the bucket is live.

## How the #1727 check works

`vc_render_tifxyz.cpp` derives a surface volume's canvas from the mesh's stored
grid size, the mesh's `scale` from `meta.json`, and the render scale:

```
full_size = max(1, lround(stored * (render_scale / float32(scale))))     # x and y
```

so the canvas is a *fingerprint* of the mesh and the render scale that produced the
volume. This audit reads, per volume, the published `canvas_size` and level-0 shape,
then inverts the formula over every published mesh variant of the volume's segment:
a pair matches exactly, or it does not — there is no tolerance and no fitting.

Two scopes are reported, and the difference between them is the finding:

- **`same_frame`** — only the variants traced on the segment's own scan
  (`intermediate/tifxyz_original`, `intermediate/tifxyz_normalized`, the legacy
  `mesh/tifxyz`). This is the scope the 2026-09-07 measurement used.
- **`all`** — every published variant, including the cross-scan
  `<scan>-on-<volume>-<voxel>um.tifxyz` directories.

Two details make the result checkable rather than merely plausible:

- **the scale the volume itself declares.** Each `.zattrs` carries `source_group`;
  the renderer writes a level-0 voxel of `base * 2^source_group`, so the render
  scale a reproduction must use is `2^-source_group` — read from the volume, not
  assumed.
- **whether the mesh names the volume.** A cross-scan variant's directory name is
  `<scan>-on-<volume_id>-<voxel>um.tifxyz`. For a volume whose reproduction comes
  from such a variant, this audit also checks that `<volume_id>` and `<voxel>` match
  the identity the volume's *own* name declares. All 388 recovered volumes pass:
  they are reproduced by the mesh that names them.

## Scope and limits

Honest boundaries, so the numbers are not read as more than they are:

- **Metadata and headers only.** No chunk bytes are downloaded, and therefore no
  *pixel* is ever compared. "Reproduced" means the declared canvas and level-0 shape
  are exactly what the formula yields for a published mesh at a declared scale — the
  strongest statement available without reading the voxels.
- **The walk covers `<segment>/mesh/` and `<segment>/surface-volumes/`**, across all
  323 segments. The catalogue also publishes ink-detection, 3d-ink-prediction and
  alpha-render artifacts; those are not audited here.
- **One `tifxyz` sample is read per grid** (the header of `x.tif`) to get the stored
  grid size; the point values are not read.
- **CPU only, single machine.** No GPU is used or needed; the whole audit is I/O.
- The reported numbers are a snapshot of a live bucket. Re-running reproduces the
  method, not necessarily the counts: the catalogue is still growing.

## Credit and provenance

The #1727 measurement and the five defect reports this audit responds to are the
work of the issue reporters in
[`ScrollPrize/villa`](https://github.com/ScrollPrize/villa/issues?q=is%3Aissue); the
2026-09-07 run published its per-pair data at
[`ge-al/vesuvius-cpu-repro`](https://github.com/ge-al/vesuvius-cpu-repro), which made
the pair-by-pair cross-check in `scripts/crosscheck_2026_09_07.py` possible. The
canvas formula is quoted from `vc_render_tifxyz.cpp` at the commit named in
`summary.json`, and the #1893 source check quotes that file's lines verbatim instead
of paraphrasing them.

## License

MIT — see [`LICENSE`](LICENSE).
