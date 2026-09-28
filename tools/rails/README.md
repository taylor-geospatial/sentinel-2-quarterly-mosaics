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

## Credentials: the Source Cooperative data proxy

Since the 0.3 CLI, Source Cooperative is **not** ordinary AWS S3. It is a
data proxy, and three things follow:

- The credentials are proxy STS tokens — the access key id begins
  `STSPRXY` — and they are valid against `https://data.source.coop` and
  nothing else.
- **The bucket is the account name.** `tge-labs` is the bucket and
  `sentinel-2-quarterly-cloudless-mosaics/...` is the key. The old
  addressing, bucket `us-west-2.opendata.source.coop` with the account as
  the first path segment, is gone; `tools/publish.py` refuses a
  `write_prefix` in that shape rather than quietly pointing somewhere
  wrong.
- Every client has to be given the endpoint. botocore does read
  `endpoint_url` from the profile by itself, but every tool here passes
  it explicitly, from `endpoint_url` in `catalog.publish.yaml`, so where
  the bytes land never depends on a file this repository does not own.

Install the CLI on rails (the installer drops a static binary in
`~/.local/bin`, no cargo toolchain needed):

```bash
curl --proto '=https' --tlsv1.2 -LsSf \
  https://github.com/source-cooperative/source-coop-cli/releases/latest/download/source-coop-cli-installer.sh | sh
source-coop --version          # must be 0.3.0 or newer
```

### The one headless login

`login` receives the OAuth redirect on a local port, so forward that port
from your laptop and finish the sign-in in your own browser. From the
**laptop**:

```bash
ssh -L 8400:127.0.0.1:8400 rails
```

Then on **rails**, in that session:

```bash
source-coop login --port 8400
```

It prints a URL. Open it in the laptop's browser and sign in; the
redirect to `http://127.0.0.1:8400/callback` travels back down the tunnel
to the CLI on rails. Then write the profile on rails:

```ini
# ~/.aws/config
[profile source-coop]
region = us-west-2
credential_process = source-coop creds
endpoint_url = https://data.source.coop
```

Check it end to end before submitting anything:

```bash
aws s3api put-object --profile source-coop --bucket tge-labs \
  --key sentinel-2-quarterly-cloudless-mosaics/_work/rails-write-test.txt \
  --body /dev/null
aws s3api delete-object --profile source-coop --bucket tge-labs \
  --key sentinel-2-quarterly-cloudless-mosaics/_work/rails-write-test.txt
```

Rails has no OS keyring, so the CLI falls back automatically to
`~/.cache/source-coop/credentials/<role>.json`, mode 0600. `source-coop
creds` refreshes from the cached refresh token without another browser
login, for about a month. Log in again only when a refresh fails.

**Log in with 0.3.0 or newer.** Older builds default `--scope` to
`openid` alone, and it is `offline_access` that puts a refresh token in
the cache; without it the credentials expire in hours and every job after
that fails on an expired token. `source-coop --version` is the check, and
it is worth running rather than assuming — on the maintainer's laptop a
0.2.0 left in `~/.cargo/bin` shadowed the 0.3.0 from Homebrew.

Nothing but the upload steps needs an identity at all; every read in this
pipeline is anonymous https.

### What the proxy allows, and one thing to watch

Measured against the live bucket from a laptop: `put_object`,
`head_object`, `get_object` and `delete_object` all work on the product
prefix, and objects appear immediately at their public
`https://data.source.coop/tge-labs/...` URL, byte-identical, with the
content type the uploader set. The bucket is versioned — responses carry
`x-amz-version-id` — so a delete leaves a marker and a re-uploaded
quarter keeps its predecessor as an old version.

`list_objects_v2` is **denied**. `tools/publish.py` already survives
that: it treats an unlistable prefix as "everything changed", prints a
note and re-uploads the whole of `catalog/`, which is a few dozen small
files. But it means there is no cheap way to ask the bucket what is
published, so the resume mechanism for the big lanes is a HEAD per
object (`upload_items.py --skip-existing`), not a listing.

Twice during testing, a burst of roughly forty requests was followed by
about ten minutes in which **every** boto3 call from that process was
answered `AccessDenied` — reads of objects that are unquestionably
public, writes, and deletes alike — while the same credentials kept
working from the `aws` CLI at the same moment. The credentials had not
expired, the signatures matched, and the error came from the proxy
(`application/xml`, an S3-shaped body) rather than from Cloudflare. It
cleared on its own. Whatever it is, a run of 28,272 objects will meet it,
so `upload_items.py` uses adaptive retries, never lets one object cancel
the other 999,999, and reports what failed so a `--skip-existing` rerun
can pick it up. **Measure this properly on the first real rails run**;
this is the one number in this file that a laptop cannot establish.

### Reading the public endpoint from Python

`data.source.coop` sits behind Cloudflare, which rejects the default
`Python-urllib/*` User-Agent with `403 error code: 1010` — a client
fingerprint ban that looks exactly like a permissions failure and is not
one. Measured on objects that are unquestionably public, including the
transfer manifests. curl, DuckDB's httpfs, GDAL's `/vsicurl` and
`requests` all send their own User-Agent and are unaffected; bare
`urllib.request.urlopen` needs one set explicitly.

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
