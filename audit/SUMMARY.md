# Vesuvius open-data catalogue audit — generated summary

- run at: `2026-10-01T13:38:38Z`
- bucket: `https://vesuvius-challenge-open-data.s3.amazonaws.com/` (anonymous, metadata and headers only)
- catalogue `metadata.json` fetched: `2026-10-01T13:35:42Z`
- villa commit the formula is quoted from: `7d6a82c71ed05ac1bd5c2e956d15400b564583d5`
- auditor version: `1.0.0`

## What was read

| quantity | value |
|---|---|
| segment directories | 323 |
| mesh variants (meta.json + x.tif header) | 1536 |
| surface volumes (.zattrs + level-0 .zarray) | 697 |
| mesh variants the catalogue indexes | 1532 |
| surface volumes the catalogue indexes | 697 |
| HTTP requests | 16821 |
| bytes read | 26467289 |
| collector errors | 0 |

## Catalogue vs bucket

| quantity | value |
|---|---|
| segments in the catalogue | 323 |
| segment directories in the bucket | 323 |
| catalogue segments with no bucket directory | 0 |
| non-segment directories under `segments/` (ignored) | 4 |

## #1727 — surface volumes reproducible from a published mesh

| mesh-variant scope | volumes tested | reproduced at documented scale | reproduced at another scale | not reproducible | no mesh available |
|---|---|---|---|---|---|
| same_frame | 693 | 164 | 141 | 388 | 0 |
| all | 693 | 693 | 0 | 0 | 0 |

- volumes gained by widening the scope to every published variant: **388** (transformed: 388); of them, **388** are reproduced by a variant whose own name carries the volume's id and voxel size (388 comparable)
- still not reproducible under any published variant: **0** (none)
- `canvas_size` disagreeing with level 0's shape: 0
- volumes with no `.zattrs`: 4; with no level-0 `.zarray`: 4; not indexed by the catalogue: 0
- under the 2026-09-07 assumptions (same-frame variants only, render scale 1): **164 of 697** reproduce exactly; over every published variant: 581 of 697
- scales named by the other-scale reproductions: 4.0: 104, None: 30, 2.0: 7

## #1892 — declared pyramid levels with no chunks

| verdict | volumes |
|---|---|
| populated | 696 |
| no_chunks_at_any_level | 1 |

Volumes that declare levels and hold no chunk at any of them:

| volume | levels | level-0 shape zyx | dtype | fill_value | catalogue artifact types |
|---|---|---|---|---|---|
| PHerc0814/segments/20260226123353-auto_grown_20260226123353106/surface-volumes/1.129um-0.22m-59keV-volume-20260521123630-L1.zarr | 6 | 116,1940,4620 | |u1 | 0 | layers-zarr |

## #1893 — voxel size written under a contradicting unit label

| verdict | volumes |
|---|---|
| consistent | 689 |
| units_absent | 4 |

- source check: 18 matching lines quoted from the renderer (see `csv/1893_source_lines.csv`); the defect is in the default the code applies, not in any published volume's metadata

- 4 Zarr surface volumes declare their axes without any `unit`, and a `scale` of 1,1,1, while the volume's own name carries the voxel size (see `csv/1893_voxel_unit.csv`): a consumer that reads the physical pixel size from the metadata gets `1` where the name says 8.64 um

## #1734 — `bbox_transformed` built from the `-1` marker

| quantity | value |
|---|---|
| segments whose stored bbox carries the marker | 28 |
| of those, `bbox_transformed` == stored x downscale exactly | 28 |
| by sample | PHercParis4: 28 |

## #1730 — segments declaring a volume scanned after them

| quantity | value |
|---|---|
| segments | 20 |
| segments the date join could examine | 323 |
| by sample | PHerc1447: 11, PHerc0139: 8, PHerc0814: 1 |
| median days the scan is later | 6 |
| of those, stored bbox overruns the declared volume's z extent | 8 |

---

Every number above is computed from the files named in `audit/raw/` and the CSV files beside this document. Regenerate with `python -m vcaudit all --outdir audit`.
