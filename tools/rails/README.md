# The rails lane

Everything heavy in this catalog runs on rails, the TGI Slurm cluster.
The dataset is 607 TB behind an https endpoint and the maintainer is on a
domestic uplink, so nothing here is meant to run on a laptop except a
smoke test.

## One-time setup

```bash
ssh rails                       # Kerberos + Duo; you have to type it
git clone <this repo> ~/s2-mosaics-catalog
cd ~/s2-mosaics-catalog
micromamba create -y -f tools/rails/environment.yml \
    -p /u/cholmes/micromamba/envs/s2mosaics
```

Check the one thing that fails silently:

```bash
/u/cholmes/micromamba/envs/s2mosaics/bin/gdalinfo --formats | grep WEBP
```

The published browse overviews are WebP. `make_overview.py` falls back to
JPEG with a warning when the driver is missing, which would put the wrong
bytes in the catalog rather than stopping.

Credentials: the uploads use the `source-coop` profile in
`~/.aws/credentials`. The node's `[default]` profile is a different
account and gets AccessDenied on the `tge-labs` prefix. Nothing but the
upload steps needs an AWS identity; every read is anonymous https.

## The rehearsal

```bash
sbatch --export=ALL,SMOKE=1 tools/rails/quarter.sbatch
```

Builds six tiles of 2024 Q2 across two UTM zones under a `_smoke` key
prefix, at zoom 9, with 50 tiles measured for `valid_fraction`. It
exercises every step including the uploads, and it cannot touch the real
catalog. `DRY_RUN=1` prints the commands instead of running them.

## One quarter

```bash
sbatch --export=ALL,YEAR=2024,QUARTER=Q2 tools/rails/quarter.sbatch
```

This is the whole update path. Adding a new quarter when CDSE publishes
one, or repairing a quarter after an upstream reprocessing, is this and
nothing else. Every step skips its own output when it already exists, so
a job that hits the eight-hour wall is resubmitted unchanged.

Step 2 is a gate rather than a report: 200 sampled tiles are opened over
https, header only, and their real `proj:code`, `proj:shape` and
`proj:transform` are compared against what `tools/mgrs_grid.py` computed.
A quarter whose tiles disagree with their own COG headers does not get
published.

## The backfill

```bash
sbatch --array=0-31%4 tools/rails/backfill.sbatch
```

2018 Q1 through 2025 Q4, newest first, four at a time. Task 0 prints the
index-to-quarter map. A failed task is resubmitted alone:

```bash
sbatch --array=7 tools/rails/backfill.sbatch
```

The throttle is about the network, not the cores: four quarters is
already thousands of concurrent range reads against one host. 2017 is not
in the default list, and 2026 Q2 is excluded until its `complete.json`
marker appears.

## Where things live

| Path | What |
| --- | --- |
| `$WORK/quarter=YYYY.Qn/items.ndjson` | the staging table, ~130 MB a quarter |
| `$WORK/items/{year}/{Qn}/{tile}/` | item JSON, ~6 kB each |
| `$WORK/quarter=YYYY.Qn/overview-scratch/` | per-zone VRTs and warps; the resume point |
| `$WORK/coverage/YYYY.Qn.parquet` | one staged quarter of coverage rows |
| `$PUBLISH/` | exactly the bucket layout below the catalog prefix |

`$PUBLISH` is what `upload.py --data-dir` walks, so a file's path below it
*is* its object key. `TMPDIR` is on `/u`: a node's `/tmp` is a 64 GB
tmpfs, too small for an overview's scratch.

## Measured costs

From a full local build of 2024 Q2 (28,272 items) and a six-tile overview:

| Step | Cost |
| --- | --- |
| manifest download | 2.8 MB, ~11 s |
| `make_items` | 6.1 s, 129.7 MB of NDJSON, 28,272 item JSONs |
| `build_quarter` | 15.5 s, 5.3 MB of parquet, `gpio check all` clean |
| `make_coverage` (no valid_fraction) | ~75 s, 3.4 MB |
| `make_footprints` | 13 s, 10.2 MB of PMTiles |
| `make_overview` | dominated by reads: about 550 kB per tile per band at zoom 10 |

The overview is the only step whose cost scales with the data rather than
the item count. At zoom 10 it reads each tile's 16x internal overview,
626 x 626 pixels, for three bands: roughly 1.7 MB a tile, so about 48 GB
for a 28,272-tile quarter. That is the number to watch on the first real
run; `ZOOM=9` quarters it.

Item JSON upload is the other big one: 28,272 objects a quarter, a
million across the backfill. At 32 workers that is minutes per quarter,
and it is round trips rather than bytes.
