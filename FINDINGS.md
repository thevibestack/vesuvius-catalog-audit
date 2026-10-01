# Findings

Every number here was produced by the code in this repository against the live
bucket on **2026-10-01**, and every one is recomputable from the CSV files in
`audit/`. The raw documents behind them are named in `audit/summary.json` with their
SHA-256. The generated report is `audit/SUMMARY.md`; this document says what the
numbers *mean* and how far they go.

Scale of the pass: **323 segments**, **1536 mesh variants**, **697 surface volumes**,
16,821 HTTP requests, 26.5 MB read, 0 collector errors.

---

## #1727 — the volumes are reproducible; the scope of the search was the problem

[Issue #1727](https://github.com/ScrollPrize/villa/issues/1727) reports that 366 of
628 published surface volumes "cannot be reproduced to the pixel from any published
mesh variant with `vc_render_tifxyz`'s canvas formula", and that "nothing in the
bucket says which transform or mesh revision produced the 366".

**What this audit measures on 2026-10-01:**

| mesh-variant scope | volumes tested | reproduced at the documented scale | at another scale | not reproducible | no mesh |
|---|---|---|---|---|---|
| same-frame variants only | 693 | 164 | 141 | 388 | 0 |
| **every published variant** | 693 | **693** | 0 | **0** | 0 |

The lower row is the whole catalogue: **every published surface volume whose canvas
is declared reproduces exactly** from a published mesh variant, at the render scale
`2^-source_group` that the volume's *own* `.zattrs` declares — read from the volume,
not assumed. Nothing is fitted, no tolerance is applied: the pair either lands on
the declared canvas or it does not, and for these 693 it lands.

**What the 388 recovered volumes are.** All 388 that the narrower scope calls
non-reproducible are reproduced by a **cross-scan** variant — a published
`<scan>-on-<volume_id>-<voxel>um.tifxyz` directory — and for **388 of 388** that
variant's own name carries both the volume id and the voxel size that the volume's
own name declares. That second check is the one that matters: it is not "some mesh
happens to give the right size", it is "the mesh that names this volume is the mesh
that reproduces it".

**A worked example, checkable by hand.** Segment
`PHerc0009B/segments/20250510172639` publishes two renders and four mesh variants.
The formula, the stored grid, the mesh's own `scale` and the volume's declared
canvas:

```
mesh  20250510172639-on-20250820154339-2.401um.tifxyz   stored grid 926 x 738   scale [0.05, 0.05]
volume  2.401um-0.35m-77keV-volume-20250820154339.zarr   canvas_size [14760, 18520]
render scale  2^-source_group = 2^-0 = 1
  height: lround(926 / float32(0.05)) = 18520   <- the declared canvas height
  width:  lround(738 / float32(0.05)) = 14760   <- the declared canvas width
```

Pulled apart for the whole segment — this is the discrimination that matters, since
"some mesh gives the right size" and "the mesh that names the volume gives the right
size" are different claims:

| mesh variant | 2.401um volume | 8.64um volume |
|---|---|---|
| `…-on-20250521125136-8.64um.tifxyz` | no | **yes** |
| `…-on-20250820154339-2.401um.tifxyz` | **yes** | no |
| `intermediate/tifxyz_normalized` | no | **yes** |
| `intermediate/tifxyz_original` | no | no |

Each cross-scan variant reproduces the one volume it names and not its sibling; the
2.401 µm render is only reachable through its own cross-scan mesh, which is exactly
why the same-frame scope could not see it. Reproduce all four numbers with:

```console
$ curl -s 'https://vesuvius-challenge-open-data.s3.amazonaws.com/PHerc0009B/segments/20250510172639/mesh/20250510172639-on-20250820154339-2.401um.tifxyz/meta.json'
$ curl -s 'https://vesuvius-challenge-open-data.s3.amazonaws.com/PHerc0009B/segments/20250510172639/surface-volumes/2.401um-0.35m-77keV-volume-20250820154339.zarr/.zattrs'
```

**Why the earlier measurement could not see them.** The 2026-09-07 run published its
inventory (`audit/inventory.json` in [ge-al/vesuvius-cpu-repro](https://github.com/ge-al/vesuvius-cpu-repro)):
315 segments, 593 mesh variants, and **0 of those 593 leaf names contain a dot**.
The walk skipped directory names containing `.`, which is precisely the shape of
every cross-scan variant (`….tifxyz`). This audit enumerates 1536 variants — the 903
cross-scan directories among them — so:
`audit/csv/1727_pairs.csv` (3650 pairs) is a superset of the 1260 pairs that run
published, and the 2390 extra pairs are all cross-scan.

The "non-integer implied render scale" and the median 10-px residual the issue
reports are the signature of measuring a cross-scan render against a *same-scan*
mesh. On the same-frame scope this audit reproduces that signature too (141 volumes
reproduce only at another scale; 104 of them at exactly 4.0, against their 103 — the
same finding, on a catalogue that has grown from 628 to 697 volumes).

**Comparability, stated plainly.** Under the earlier run's own assumptions
(same-frame variants, render scale 1) today's catalogue gives **164 of 697**; over
every published variant, 581 of 697. The earlier run's 262 of 628 is the same model
with a wider scale search on a smaller catalogue.

**What this does not prove.** That the *pixels* match. Reproducing a canvas is
necessary, not sufficient: confirming the voxels would mean reading the volume and
running the renderer. This audit is metadata-only by construction, and says so rather
than implying more. The remaining question for the project is therefore not "which
mesh produced the 366" — the bucket answers that, per volume, in
`audit/csv/1727_crossscan_recovered.csv` — but whether the pipeline that renders
should stop publishing a volume without naming the mesh it came from.

Also measured, and clean: `canvas_size` disagrees with level-0 shape for **0** of 697
volumes; **0** volumes are published without the catalogue indexing them; 4 volumes
have no `.zattrs`/level-0 `.zarray` and are TIFF stacks (`layers-tif`), not Zarr
stores, so there is no canvas to check.

---

## #1892 — one published volume reads as all-zeros, silently

[Issue #1892](https://github.com/ScrollPrize/villa/issues/1892) reports one store
whose six levels hold no chunks. **Reproduced, exactly one store, and no others:**

| volume | levels | chunks per level | level-0 shape zyx | dtype | fill_value |
|---|---|---|---|---|---|
| `PHerc0814/segments/20260226123353-auto_grown_20260226123353106/surface-volumes/1.129um-0.22m-59keV-volume-20260521123630-L1.zarr` | 6 | 0 at every level | 116, 1940, 4620 | `\|u1` | 0 |

Verdict tally over the 697 surface volumes: **696 populated, 1 with no chunks at any
level, 0 with chunks missing at some levels, 0 inconclusive.**

The method is what makes the "0 others" worth something. A Zarr group holds at most
three dot-keys (`.zarray`, `.zattrs`, `.zgroup`), and `.` sorts before every digit
and letter in S3's key ordering, so the metadata keys are always the first keys
listed. Each level is probed with an 8-key window: a non-dot key means data exists;
no non-dot key with an untruncated listing means the level is *conclusively* empty
(at most 3 dot-keys can exist, so 8 keys with no truncation is everything). A
truncated no-data listing is impossible and would be reported as inconclusive — it
did not occur. The check is a listing per level, not a download: no chunk byte was
read.

The consequence stands as the issue states it: a reader that trusts the header gets
a 116×1940×4620 surface volume of zeros with no error.

---

## #1893 — the code default is still wrong; the published corpus does not carry it

[Issue #1893](https://github.com/ScrollPrize/villa/issues/1893) reports that
`vc_render_tifxyz`, given no `--voxel-size`/`--voxel-unit`, takes a micrometre size
from `meta.json` and writes it under the flag's `nanometer` default. The issue's
evidence is the source, read rather than run.

**Source check, at `7d6a82c`** (the commit this audit quotes; 18 lines published
verbatim in `audit/csv/1893_source_lines.csv`):

```
vc_render_tifxyz.cpp:1349  ("voxel-unit", po::value<std::string>()->default_value("nanometer"),
                            "Unit of the number given to --voxel-size ... Sizes read from
                             volume metadata are always declared in micrometer.")
Zarr.cpp:379               if (physicalSizeKnown && !voxelUnit.empty()) ax["unit"] = voxelUnit;
```

The default is still `nanometer`, and the writer still applies whatever unit string
it is handed. **The defect is live in the code.**

**What the published bucket shows** (697 surface volumes):

| declared axis unit | volumes |
|---|---|
| `micrometer` (all three axes) | 689 |
| none at all | 4 |
| `nanometer` | **0** |

So no consumer of the published catalogue has to convert a 1000× error today: the
corpus contains no volume labelled `nanometer`. As corroboration that the values in
those volumes really are micrometres, the declared level-0 `scale` equals the µm
figure in the volume's own name for 577 of the 693.

This is worth having stated plainly in the issue: it separates "the writer's default
is wrong" (true, live, still worth fixing) from "the published data is wrong" (not
observable — either every published render passed the unit explicitly, or the
default path was never used for a published volume). It also means a fix needs no
data migration, only the code path.

---

## #1734 — the `-1` marker is published as a scaled coordinate

[Issue #1734](https://github.com/ScrollPrize/villa/issues/1734) reports that for the
28 PHercParis4 segments whose stored bbox carries the tifxyz `-1` missing-point
marker, the catalogue's `volume_coverage.bbox_transformed` is that marker multiplied
by `original_volume_downscale`.

**Independently reproduced: 28 segments carry the marker, and for 28 of 28 the
derived box equals the stored box × the segment's `original_volume_downscale`
exactly** (`audit/csv/1734_bbox_marker.csv`, per-segment stored and derived corners).
All 28 are PHercParis4.

The marker is not a coordinate, so what this field publishes is the sentinel pushed
through a scaling transform — a consumer that reads `bbox_transformed` gets a box
with a corner at `-4` where the documented "unset" test looks for `-1`.

---

## #1730 — 20 segments declare a volume that did not exist yet

[Issue #1730](https://github.com/ScrollPrize/villa/issues/1730) reports 20 segments
whose declared `original_volume_id` points at a volume scanned after the segment was
created. **Reproduced: 20 segments, and the same distribution.**

| quantity | value |
|---|---|
| segments declaring a later-scanned volume | 20 |
| segments the date join could examine | 323 |
| by sample | PHerc1447: 11, PHerc0139: 8, PHerc0814: 1 |
| median days the scan is later | 6 |
| of those, stored bbox runs past the declared volume's z extent | 8 |

The denominator is worth a note. The issue counts 20 of 306 (or 311), because 5
PHerc0500P2 segments carry a `creation.date` of the form
`2025-06-11T15:42:56+00:00Z` — an offset *and* a `Z` — which `datetime.fromisoformat`
rejects, and a strict parser drops them silently. This audit parses that form
(`tests/test_catalogue.py` pins it), so it can examine **323 of 323** segments and
report the result against the full population: a segment the join cannot examine is
not a segment it cleared. All 323 have a parseable creation date, a resolvable
volume, and a resolvable scan date.

---

## Not filed: 4 volumes whose unit is absent and whose `scale` is 1,1,1

Found while checking #1893, and reported here rather than quietly dropped. Four Zarr
surface volumes — `PHerc1447` segments `20250702235910`, `20250703025628`,
`20250703034159`, `20251105093211`, each `surface-volumes/8.64um-1.2m-116keV-volume-20250521151220.zarr`
— declare axes with **no `unit`** and a level-0 `scale` of `[1.0, 1.0, 1.0]`, while
the volume's own name states a voxel size of 8.64 µm:

```json
"axes": [{"name":"z","type":"space"},{"name":"y","type":"space"},{"name":"x","type":"space"}],
"datasets": [{"coordinateTransformations":[{"scale":[1.0,1.0,1.0],"type":"scale"}, …], "path":"0"}, …]
```

These `.zattrs` are written as compact JSON and their level-0 `.zarray` carries
`dimension_separator: "."`, unlike the other surface volumes (pretty-printed,
`"/"`), so they come from a different writer path. A consumer that reads the
physical pixel size from the metadata gets `1` (unitless) where the name says
8.64 µm — the same class as #1893, the opposite failure: the value is defaulted
instead of the unit. Verify with:

```console
$ curl -s 'https://vesuvius-challenge-open-data.s3.amazonaws.com/PHerc1447/segments/20250702235910-auto_grown_20250702235910292/surface-volumes/8.64um-1.2m-116keV-volume-20250521151220.zarr/.zattrs'
```

Not filed as an issue: the decision to publish something in the tracker is the
maintainers' and the reporter's, not this audit's.

---

## Cross-check against the 2026-09-07 measurement

The earlier run shipped its per-pair data, so the two implementations can be compared
pair by pair rather than in aggregate (`scripts/crosscheck_2026_09_07.py`,
output in `audit/CROSSCHECK-2026-09-07.txt`):

| comparison | result |
|---|---|
| pairs present in both | 1260 / 1260 |
| stored grid size (independent TIFF-header readers) | 1260 / 1260 agree |
| reference canvas | 1260 / 1260 agree |
| **match verdict** | **1260 / 1260 agree, 0 mismatches** |
| renderer size | 1250 / 1260, and the 10 differences are all explained |
| pairs they measured, absent here | 0 |
| pairs measured here, absent there | 2390 (all cross-scan, the scope they did not walk) |
| mesh directories in their inventory whose leaf name contains a dot | 0 of 593 |

The 10 renderer-size differences are not noise: they are the `meta.json` scale axis
ordering. `vc_render_tifxyz.cpp` divides the **width** by `_scale[0]` and the
**height** by `_scale[1]`; the earlier run's published data does the opposite. The
difference can only show up on grids whose two scale values differ, which is why it
is 10 anisotropic `tifxyz_original` pairs differing by 1–2 px and not 1260. The
earlier run's own review note says it found and corrected exactly this ("affected only
11 anisotropic `tifxyz_original` meshes by ≤ 3 px, and changed no counts"), so the
published `pairs.json` predates its own correction. This audit follows the source.

---

## How to check any of this

```console
$ python3 -m unittest discover -s tests        # 48 tests, no network, ~0.3s
$ python3 -m vcaudit analyze \
      --inventory audit/raw/inventory.json \
      --catalogue audit/raw/catalogue.json \
      --villa-src /path/to/ScrollPrize/villa --outdir /tmp/recheck
```

`analyze` never touches the network, so every number above can be re-derived from
the two collected documents; to re-read the bucket instead, run `vcaudit collect`
first (about 3 minutes, 26 MB, no credentials). Spot-check one segment end to end —
this is the exact command, and its output:

```console
$ python3 -m vcaudit collect --limit 1 --out /tmp/one/inventory.json --catalogue-out /tmp/one/catalogue.json
fetching the catalogue from https://vesuvius-challenge-open-data.s3.amazonaws.com/metadata.json
catalogue: 45 samples, 323 segments
  walked 1/1 segments in 14s (95 requests, 1.5 MB)
inventory: 1 segments, 4 mesh variants, 2 surface volumes, 0 errors
transport: 95 requests, 1.5 MB read, 17s wall
wrote /tmp/one/inventory.json and /tmp/one/catalogue.json
```
