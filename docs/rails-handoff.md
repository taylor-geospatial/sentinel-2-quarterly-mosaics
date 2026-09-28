# Rails handoff

Written 2026-09-28 by the laptop-side agent that deployed the lane and
ran the 2024 Q2 pilot build, for whoever picks this up on rails. It
assumes no memory of that session. `tools/rails/README.md` is the lane's
reference documentation; this file is the state of the world and the
things that are not in the code.

## Read this first: the git situation is multi-machine

The catalog now lives on three machines and they are not in sync.

* **origin** — `github.com/taylor-geospatial/sentinel-2-quarterly-mosaics`.
* **laptop** — pushed up to `7c1342a`, then made one further commit that
  is **still unpushed**: `9a821d3`, "Pin the browse overview to zoom 9,
  and add rio-cogeo to the env". Someone has to push it, or the rails
  session will never see it as a commit.
* **rails** — `~/s2-mosaics-catalog`, fast-forwarded to `7c1342a`, with
  **uncommitted working-tree edits** to `tools/rails/env.sh` and
  `tools/rails/environment.yml`.

Those uncommitted rails edits partly *are* `9a821d3`: on 2026-09-28 at
about 19:20 UTC the laptop `rsync`ed `tools/` to rails, which overwrote
both files with the edited versions before they were committed. Anything
the rails session has changed since then is layered on top.

**So: pull or rebase before doing anything else, expect a conflict in
`tools/rails/env.sh`, and keep both sides.** The two changes are
independent and both are wanted:

* from `9a821d3` — `export ZOOM="${ZOOM:-9}"` (it was 10) and
  `rio-cogeo` in `environment.yml`;
* from rails — the environment path fixes.

Resolving by taking one side wholesale will silently drop the other. In
particular, losing `ZOOM=9` does not fail anything; it just builds the
next quarter at a different resolution from the other 35 (see
[The zoom 9 decision](#the-zoom-9-decision)).

## What is deployed, and where

| Path on rails | What |
| --- | --- |
| `~/s2-mosaics-catalog` (`/u/cholmes/s2-mosaics-catalog`) | the git checkout; `$REPO` |
| `~/s2-mosaics-catalog/logs/` | Slurm job logs land here |
| `/u/cholmes/s2-mosaics/work` | `$WORK` — staging file, item JSON, overview scratch |
| `/u/cholmes/s2-mosaics/publish` | `$PUBLISH` — its layout **is** the bucket layout |
| `/u/cholmes/s2-mosaics/tmp` | `$TMPDIR` |
| `/u/cholmes/s2-mosaics/logs` | created, currently unused; the sbatch `--output` is relative to the submit dir, so logs really land in the repo's `logs/` |
| `/u/cholmes/micromamba/envs/s2mosaics` | the toolchain env for this catalog |
| `/u/cholmes/.local/bin/micromamba` | micromamba (`MAMBA_ROOT_PREFIX=/u/cholmes/micromamba`) |
| `/u/cholmes/.local/bin/source-coop` | source-coop CLI 0.3.0, **not logged in** |

Other micromamba envs on the box — `ftw`, `s2`, `node` — belong to other
work. **`node` has Node 22 and Claude Code in it; do not clobber it.**

### The `s2mosaics` env

Verified complete on 2026-09-28. Python 3.12, GDAL 3.13.3, geoparquet-io
(`gpio`) 1.5.0, DuckDB 1.5.6, rasterio 1.5.1, pyproj 3.8.0, shapely
2.1.2, pyarrow 25.0.0, boto3 1.43.75, rio-cogeo 7.0.2, tippecanoe, the
`pmtiles` Python package, numpy. `python3 -m pip check` is clean.

There is **no `aws` CLI** on rails. Nothing in the build path needs it;
only the credential smoke test in `tools/rails/README.md` does.

**WebP is present**, and it was checked properly rather than loosely.
The README's check is `gdalinfo --formats | grep WEBP`, which only proves
the standalone WEBP raster driver exists. What the overview actually
needs is WebP *compression* in the COG driver. Both hold here:

```bash
gdalinfo --format GTiff | grep -i webp      # WEBP among COMPRESSION values
gdalinfo --format COG   | grep -i webp      # same
```

Use the second form. `make_overview.py` falls back to JPEG with a warning
when it thinks WebP is missing, which would put the wrong bytes in the
catalog rather than stopping, so this is worth being exact about.

`rio-cogeo` was **not** in the env before 2026-09-28 and is not in the
`7c1342a` version of `environment.yml`. It was installed with:

```bash
export MAMBA_ROOT_PREFIX=/u/cholmes/micromamba
micromamba install -y -p /u/cholmes/micromamba/envs/s2mosaics -c conda-forge rio-cogeo
```

That is a pure addition — eight packages, no downgrade of GDAL or
rasterio — and it is what makes `rio cogeo validate` available on the
machine that builds the COGs.

## Running a quarter

Everything heavy goes through `sbatch`. The login node enforces an equal
share of 60 cores and reaps heavy processes, so do not run a build
interactively.

```bash
cd ~/s2-mosaics-catalog

# See the commands without running anything, and create nothing.
DRY_RUN=1 YEAR=2024 QUARTER=Q2 bash tools/rails/quarter.sbatch

# The rehearsal: six tiles over two UTM zones, under a _smoke key prefix.
sbatch --export=ALL,SMOKE=1 tools/rails/quarter.sbatch

# One real quarter, building only (no credentials needed).
sbatch --partition=cpu,cpu_amd --mem=64g \
  --export=ALL,YEAR=2024,QUARTER=Q2,UPLOAD=0,MEMORY=48GB \
  tools/rails/quarter.sbatch
```

`UPLOAD=0` is what makes a build possible on a machine with no Source
Cooperative credentials: it skips both upload steps *and* the credential
preflight, so the job does not refuse to start over an identity it will
never use. **Until rails is logged in, every run needs `UPLOAD=0`** —
without it `check_creds` fails the job in the first seconds.

`--partition=cpu,cpu_amd` lets Slurm take whichever frees first.
`--mem=64g` matters a great deal; see
[Memory is the binding constraint](#memory-is-the-binding-constraint).
`MEMORY=48GB` is the DuckDB ceiling handed to `build_quarter.py`; keep it
below the cgroup limit or a rebuild can be OOM-killed.

Monitoring:

```bash
squeue -u $USER
tail -f ~/s2-mosaics-catalog/logs/s2mos-quarter-<jobid>.out
sstat -j <jobid>.batch --format=MaxRSS,AveCPU     # while running
sacct -j <jobid> --format=JobID,State,Elapsed,MaxRSS,ReqMem,NodeList
```

The backfill is the same work as an array job:

```bash
sbatch --array=0-31%4 tools/rails/backfill.sbatch   # task 0 prints the map
sbatch --array=7 tools/rails/backfill.sbatch        # resubmit one failure
```

### What a rerun skips

Every step is idempotent, which is why a job that hits the eight-hour
wall is resubmitted unchanged. Concretely:

| Step | Skipped when |
| --- | --- |
| 1 `make_items` | `$WORK/quarter=YYYY.Qn/items.ndjson` exists (guard is in the sbatch) |
| 2 geometry gate | never — it is a gate, it runs every time |
| 3 `build_quarter` | `$PUBLISH/mosaics/quarter=YYYY.Qn/items.parquet` exists; `--force` overrides |
| 4 `make_overview` | per **zone**: any zone with `{epsg}_3857.tif` in the scratch dir is not re-warped, and any `{epsg}_{band}.vrt` is not re-staged |
| 6 `upload_items` | only with `--skip-existing`, which HEADs before putting |

The overview's resumability is the one that matters, because it is the
long pole. `--keep-scratch` (which `quarter.sbatch` passes) is what makes
it work: the per-zone VRTs and warped GeoTIFFs stay in
`$WORK/quarter=YYYY.Qn/overview-scratch/`. If the job dies half way
through the warps, resubmitting redoes only the zones that had not
finished. Do not clean that directory to save space mid-build.

The scratch stays on `/u` rather than node-local `/tmp` for exactly this
reason — a resubmitted job usually lands on a different node — and also
because `/tmp` on a compute node is a 64 GB tmpfs, too small for an
overview's scratch whatever `df` on the login node suggests.

## State of job 203689 — the 2024 Q2 pilot

Submitted 2026-09-28 19:28 UTC. **rails03, partition `cpu`, 32 CPUs,
64 GB, 8 h limit** (so it walls at 22:28 UTC), `UPLOAD=0`.

Last seen at **19:57 UTC: 76 of 119 zones warped**, peak RSS 56.5 GB,
no errors. The laptop's SSH ControlMaster died at that point and it could
not watch any further — the job itself was unaffected, because Slurm does
not care about the submitting session.

What had already completed:

| Step | Result |
| --- | --- |
| 1 `make_items` | **skipped** — reused the 129.7 MB `items.ndjson` and 166 MB of item JSON from cancelled job 202767 |
| 2 geometry gate | 200 tiles, **0 mismatches, 0 unreadable**, 7.7 s |
| 3 `build_quarter` | **skipped** — `items.parquet` already present and already gated |
| 4a overview staging | 357 band VRTs over 119 UTM zones, **597 s** |
| 4b warps | in progress, ~570–725 s per zone, 32 concurrent |

Reusing step 1's output is sound: the only commit between that run and
this one, `2eb2094`, changed `verify()` and argument parsing and did not
touch the item-writing path.

Earlier job numbers, so old logs are not mistaken for current state:
**202767** cancelled during overview staging, **203002** failed the
geometry gate on a transient read (since fixed), **203476** cancelled by
the laptop because a 120 GB memory request would not schedule.

### Validating the pilot when it finishes

A script is already staged at **`/u/cholmes/s2-mosaics/tmp/validate_pilot.sh`**
(read-only; run it with the `s2mosaics` env on PATH). It covers all of
the below and renders the PNG. What each check should say:

**1. `gpio check all` on `items.parquet`.** Expect **34 spec checks
passed**, GeoParquet 2.0.0, native GEOMETRY types, ZSTD on the geometry
column. It also emits two WARNINGs, and **both are deliberate design
choices, not defects** — do not "fix" them:

* *Row count per group outside the recommended 10,000–200,000.*
  `ROW_GROUP_SIZE` is 5,000 on purpose, so a single-tile lookup reads a
  fraction of the file. The reasoning is in `tools/schema.py`.
* *Poor spatial ordering (overlap ratio 1.00).* The sort key is
  `_tile,datetime,_subtile` — tile-major, so one tile's 36-quarter
  history is contiguous. That is the temporal-scrub query the whole
  catalog exists to serve. Hilbert ordering would optimise a query this
  catalog does not make.

**2. Row count and schema against the laptop smoke build.** The
reference file is on the **laptop** at
`/Users/cholmes/s2-mosaics-smoke/mosaics/quarter=2024.Q2/items.parquet`.
This comparison was already done and passed on 2026-09-28:

| | rails | laptop |
| --- | --- | --- |
| rows | 28,272 | 28,272 |
| row groups | 5 | 5 |
| columns | 26 | 26 |
| schema sha256 (first 16) | `9ab9514a96c1eb53` | `9ab9514a96c1eb53` |
| geo metadata version | 2.0.0 | 2.0.0 |
| bytes | 5,291,825 | 5,291,828 |

The **3-byte difference is fully explained** and is not a discrepancy to
chase: the Parquet footer's `created_by` records the DuckDB build hash,
which is `221e2d5` (7 chars) on rails and `069cc9f9b5` (10 chars) on the
laptop. Three characters, three bytes. Everything else is identical.

**3. `rio cogeo validate` on `overview.tif`.** Not yet run — the COG did
not exist when the session ended. `gdalinfo` should show EPSG:3857,
`COMPRESSION=WEBP`, four bands (R, G, B, alpha), and a pixel size of
**305.748113 m** — that number is zoom 9 and is the thing to check if you
suspect the zoom default was lost in a merge.

**4. The PNG render.** Render small and look at it:

```bash
gdal_translate -q -of PNG -outsize 2000 0 -r average \
  overview.tif /u/cholmes/s2-mosaics/tmp/pilot-overview.png
```

The laptop expected a copy at `/Users/cholmes/s2-mosaics-smoke/pilot-overview.png`.
It should look like a global cloudless land mosaic with transparent
ocean: continents in place, no black rectangular holes (a hole means a
zone failed to warp and got assembled anyway), no visible seams between
UTM zones, and no per-zone brightness steps — the stretch is fixed at
`0..2500 -> 0..255` with exponent 0.55 precisely so it cannot vary.

**5. Also worth running**, since they are cheap and independent:

```bash
python3 tools/mgrs_grid.py     # 340 measured origins, expect 0 disagreements
gpio check all /u/cholmes/s2-mosaics/publish/coverage/tiles.parquet
```

## Credentials: nothing on rails can upload yet

This is the live blocker on publishing.

* The source-coop CLI is logged in **on the laptop only** (`aws --profile
  source-coop` works there; the token refreshes for about a month).
* **Rails has the CLI (0.3.0) but no credentials.** Do not attempt the
  login unsupervised — it needs a browser and the user drives it.
* So every rails run is `UPLOAD=0` until that changes.

Source Cooperative is a **data proxy**, not ordinary S3, and three things
follow:

* endpoint `https://data.source.coop`, and every client must be given it
  explicitly;
* **the bucket is the account name** — bucket `tge-labs`, key prefix
  `sentinel-2-quarterly-cloudless-mosaics/`. The old addressing (bucket
  `us-west-2.opendata.source.coop` with the account as the first path
  segment) is gone, and `tools/publish.py` refuses a `write_prefix` in
  that shape;
* the credentials are proxy STS tokens (access key id starts `STSPRXY`)
  valid against that endpoint and nothing else.

The headless login, from `tools/rails/README.md` — on the **laptop**:

```bash
ssh -L 8400:127.0.0.1:8400 rails
```

then on **rails**, inside that session:

```bash
source-coop login --port 8400
```

It prints a URL; open it in the laptop's browser and sign in. The
redirect to `http://127.0.0.1:8400/callback` travels back down the
tunnel. Then write `~/.aws/config` on rails:

```ini
[profile source-coop]
region = us-west-2
credential_process = source-coop creds
endpoint_url = https://data.source.coop
```

**Log in with 0.3.0 or newer.** Older builds default `--scope` to
`openid` alone, and it is `offline_access` that puts a refresh token in
the cache; without it credentials expire in hours and every later job
fails on an expired token. Rails has no OS keyring, so the CLI falls back
to `~/.cache/source-coop/credentials/<role>.json` at mode 0600.

Nothing but the upload steps needs an identity. **Every read in this
pipeline is anonymous https.**

## Landmines

Things that cost real time to find. None of them are hypothetical.

### Memory is the binding constraint

This is the single most useful operational finding of the day.

`--mem=120g`, the default in `quarter.sbatch` and `backfill.sbatch`,
**does not schedule** on this cluster under normal load. Job 203476 sat
on `(Priority)` indefinitely while `squeue --start` kept producing
estimates that were already in the past. Cores were never the problem:
rails02 had 116 idle cores at the time but only 118 GB free against the
120 GB ask. Resubmitted at `--mem=64g`, the job started in about
20 seconds.

Measured against job 203689 at 32 concurrent warps:

| Phase | Peak RSS |
| --- | --- |
| items / gate / parquet | 1.5–1.8 GB |
| overview warps, 32 concurrent | **56.5 GB**, flat once it reached steady state |

So **64 GB is adequate but tight** — about 12% headroom — and 32 GB would
have been OOM-killed. Memory tracks warp concurrency, roughly 1 GB of
GDAL block cache per `gdalwarp` plus working buffers.

If a future quarter peaks higher than 56.5 GB, **do not just raise
`--mem`** — that is what makes the job unschedulable. Lower the warp
concurrency instead, which lowers the memory need proportionally:

```bash
sbatch --partition=cpu,cpu_amd --mem=64g \
  --export=ALL,YEAR=...,QUARTER=...,UPLOAD=0,JOBS=24 tools/rails/quarter.sbatch
```

`JOBS` sets warp concurrency directly. Staging concurrency is
`min(64, JOBS*4)`, so anything from `JOBS=16` upward still stages at the
full 64 and costs nothing in the phase that is pure network latency.
Something near `--mem=72g` would be a reasonable permanent default:
~30% headroom over measured peak, still far easier to schedule than 120.
That change is **not** committed — it should rest on one more quarter's
measurement.

An OOM kill is recoverable, incidentally: warped zones are kept, so a
resubmit redoes only what was unfinished.

### The proxy AccessDenied bursts — an open question, not a solved one

Two observations that disagree, and it matters that both are recorded.

* **From the laptop** (earlier session): twice, a burst of roughly forty
  requests was followed by about ten minutes in which *every* boto3 call
  from that process was answered `AccessDenied` — reads of
  unquestionably public objects, writes and deletes alike — while the
  same credentials kept working from the `aws` CLI at the same moment.
  The error came from the proxy, `application/xml` with an S3-shaped
  body, not from Cloudflare. It cleared on its own.
* **From a compute node** (job 203689, 2026-09-28): it **did not
  reproduce**. Overview staging sustained **84,816 tile-header opens in
  583 s, about 145 requests per second**, with zero denials.

So the earlier pattern may be client-specific (boto3 versus GDAL's
`/vsicurl`), path-specific (domestic uplink versus NCSA), or
credential-specific — the compute-node reads were anonymous, the laptop's
were signed. **Treat it as unresolved.** The signed-write path at high
request rates is the case that still has not been measured, and that is
exactly what the item upload will be: 28,272 objects a quarter, a million
across the backfill.

One important detail about how the compute-node result was established.
`make_overview.py`'s `run()` captures subprocess output and prints it
only on failure, so a denial that `gdalbuildvrt` merely *warned* about
would not appear in the job log — and `gdalbuildvrt` skips a source it
cannot open and carries on, which would silently leave a hole in the
overview. The log alone therefore proves nothing. What proves it is
counting the sources that landed in the VRTs:

```bash
S=/u/cholmes/s2-mosaics/work/quarter=YYYY.Qn/overview-scratch
for v in "$S"/*_B0*.vrt; do
  b=$(basename "$v" .vrt)
  echo "$b $(grep -c '<SourceFilename' "$v") $(wc -l < "$S/$b.txt")"
done
```

For 2024 Q2 this came back **84,816 sources against 84,816 listed**,
exactly 28,272 tiles × 3 bands, across all 357 VRTs. **Run this check on
every quarter before trusting its overview.** It is the only thing
standing between a proxy hiccup and a quietly incomplete browse layer.

### No `list_objects_v2` on the prefix

Listing is denied. `tools/publish.py` already survives it — it treats an
unlistable prefix as "everything changed" and re-uploads the whole of
`catalog/`, a few dozen small files. But there is **no cheap way to ask
the bucket what is published**, so the resume mechanism for the big lanes
is a HEAD per object (`upload_items.py --skip-existing`), not a listing.
That doubles the request count on a resumed run, which is why it is off
by default; a plain rerun simply overwrites, and that is correct, because
the item JSON is a pure function of the manifest.

### Cloudflare 403s Python's default User-Agent

`data.source.coop` sits behind Cloudflare, which rejects the default
`Python-urllib/*` User-Agent with **`403 error code: 1010`** — a client
fingerprint ban that looks exactly like a permissions failure and is not
one. Measured on objects that are unquestionably public, including the
transfer manifests. curl, DuckDB's httpfs, GDAL's `/vsicurl` and
`requests` all send their own User-Agent and are unaffected; bare
`urllib.request.urlopen` needs one set explicitly.

### The bucket is versioned

Responses carry `x-amz-version-id`. A delete leaves a marker, and a
re-uploaded quarter keeps its predecessor as an old version. Republishing
is safe but is not free of history.

### 2026 Q2 reads differently

Every quarter from 2017 to 2025 stores its COGs in 1024×1024 blocks;
2026 Q2, which finished transferring on 2026-09-28, uses 256×256. The
overview *levels* are unchanged — 2x through 32x, down to 313×313 — so
`overview_level()` picks the same one and the bytes read are the same.
Only the request count moves, and **GDAL's merging of consecutive ranges
absorbs most of it**: measured on 31UFU_0_0, reading the 32x overview took
2 range requests in 2024 Q2 and 3 in 2026 Q2, and the 16x overview the
same 2 against 3. No code changes are needed for it. The tile origins are
identical between the two vintages.

### Other environment gotchas

* `TZ=UTC` — every quarter boundary is a UTC instant and a node that
  thinks otherwise writes `start_datetime` an hour out. `env.sh` sets it.
* DuckDB must use `https://data.source.coop/...`, **never** `s3://`, on
  compute nodes: the IMDS endpoint is blackholed and the call hangs.
* `TMPDIR` lives on `/u`, not the node's 64 GB tmpfs.
* `make_items` writes 28,272 small files and `/u` is NFS over RDMA, so
  metadata operations dominate: **307 s on `/u` against 6 s on a laptop
  SSD**. Measured inside an allocation, writing 2,000 small files took
  0.054 s on node-local `/tmp` and 7.78 s on `/u` — 145x. Pointing
  `ITEMS` at node-local storage would reclaim about five minutes a
  quarter and three hours across the backfill. It is **not wired up**;
  it is the cheapest large win still on the table.

### The zoom 9 decision

The browse overview is built at **Web Mercator zoom 9, 305.75 m/px, for
every quarter**, and that uniformity is the point. The layer exists to be
scrubbed through time; a quarter built at a different base resolution
would make every transition into or out of it look like a change in the
scene rather than in the resolution. Raising it means rebuilding all 36,
not just the next one.

It is also much cheaper. At zoom 9 each tile is read at its 32x overview,
313×313 per band; at zoom 10 it is the 16x, 626×626, four times the
bytes — roughly **51 GB of reads per quarter against 13 GB**.

`ZOOM` defaults to 9 in `env.sh` as of commit `9a821d3`. Before that it
defaulted to 10 and only an exported variable held it to 9, which is a
bad way to carry a number that must be identical across 36 quarters.
**This is the change most likely to be lost in the merge described at the
top of this file.**

## Measured costs, 2024 Q2

One `cpu` node, 32 cores. Use these to sanity-check the pace of the next
quarter.

| Step | Time | Note |
| --- | --- | --- |
| manifest download | 2.7 s | 2.8 MB (but 98 s on one run — the uplink varies) |
| `make_items` | 307 s | filesystem metadata, not work; see above |
| geometry gate, 200 tiles | 7.7 s | 0 mismatches |
| `build_quarter` | 21.4 s | 5.3 MB, 5 row groups, `gpio check all` clean |
| **overview staging** | **597 s** | 357 band VRTs over 119 zones |
| overview warps | ~570–725 s per zone, 32 concurrent | 76/119 done at ~19 min in |
| `make_coverage` | ~75 s (laptop) | 3.4 MB |
| `make_footprints` | ~13 s (laptop) | 10.2 MB of PMTiles |

**Staging is the number that changed.** `gdalbuildvrt` opens every source
to read its georeferencing, and inside one call those reads are serial:
zone 32601's 138 tiles took about 60 s for one band, so roughly 360
(zone, band) pairs came to **about six hours** before a single pixel was
warped. The first pilot was cancelled there. Staging now runs on a pool
of `4x --jobs` capped at 64 — it is HTTP latency, not computation — and
the same work takes **about 10 minutes**. During it the job used 2 minutes
of CPU across 6 minutes of wall clock, which confirms it is network-bound.

Read volume for one quarter's overview at zoom 9: 84,816 header opens
(~1.4 GB at GDAL's 16 KB chunks) plus 28,272 × 3 × 313×313 × 2 bytes =
**16.6 GB uncompressed** of pixel data, less on the wire because the
source COGs are deflate-compressed.

Item JSON upload is the other large step whenever credentials arrive:
28,272 objects a quarter, a million across the backfill, bound by round
trips rather than bytes.

## What remains

1. **Finish and validate the 2024 Q2 pilot** — job 203689, per
   [State of job 203689](#state-of-job-203689--the-2024-q2-pilot). This
   is the ⛔ checkpoint in the plan: the pilot's items, parquet, overview
   COG and timings get reviewed before any fan-out.
2. **Resolve the git conflict** described at the top, and get `9a821d3`
   pushed so rails has the zoom-9 default as a commit rather than as an
   uncommitted edit that a careless `git checkout` would discard.
3. **Credentials**, user-driven: the port-forward login on rails, then a
   write/delete smoke test against the `_work/` key prefix. Until then
   every run is `UPLOAD=0` and nothing is published.
4. **The upload phase**, once 3 lands: `upload.py` for the parquet, COG,
   thumbnail, coverage and footprints, then `upload_items.py` for the
   28,272 item JSONs. This is where the signed-request rate meets the
   proxy for the first time, so **watch for the AccessDenied pattern
   here** — it is the measurement that is still missing.
5. **The backfill**: `sbatch --array=0-31%4 tools/rails/backfill.sbatch`,
   which covers **2018 Q1 through 2025 Q4 newest-first**, four at a time.
   The throttle is about the network, not the cores. Then **2017**
   separately — its tiles are the oldest processing and worth eyeballing
   on their own — and then **2026 Q2 last**, whose `complete.json` marker
   appeared on 2026-09-28 but whose 256×256 block size should be
   described in the collection metadata before it is published.

## Notes on the code, for whoever touches it next

Neither of these is a bug and neither was changed; they are judgement
calls worth knowing about.

* **`require_gdal()` in `make_overview.py`** tests `gdalinfo --formats`
  for the standalone WEBP driver, not the COG driver's compression list.
  In practice libwebp gates both, so it is unlikely to be wrong, and a
  COG write that did fail would exit rather than silently produce JPEG.
  But `gdalinfo --format COG` is the check that means what the README
  says it means.
* **The geometry gate samples with `random.seed(0)`**, so rerunning the
  same quarter always re-checks the same 200 tiles. That is deliberate —
  it makes a rerun idempotent — and different quarters draw different
  tiles, so coverage accumulates across the backfill. It does mean a
  rerun never widens coverage within one quarter.
