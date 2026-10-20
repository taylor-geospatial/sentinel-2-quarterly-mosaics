# RAILS processing

Run catalog processing on RAILS, the Slurm cluster used for this dataset.
The imagery spans 607 TB over HTTPS, so laptop runs should be limited to smoke tests.
The paths and measurements below come from the September 2026 setup.
See the [historical handoff](../../docs/rails-handoff.md) for the pilot's job state and unresolved operational issues.

## Setup

```bash
ssh rails                       # Kerberos + Duo; you have to type it
git clone <this repo> ~/s2-mosaics-catalog
cd ~/s2-mosaics-catalog
micromamba create -y -f tools/rails/environment.yml \
    -p /u/cholmes/micromamba/envs/s2mosaics
```

The overview COGs use JPEG compression with an internal transparency mask.
The WEBP driver is needed for thumbnails.
`make_overview.py` warns and falls back to a JPEG thumbnail if that driver is missing.
Check for the driver with:

```bash
/u/cholmes/micromamba/envs/s2mosaics/bin/gdalinfo --formats | grep WEBP
```

## Credentials

Source Cooperative CLI 0.3 uses proxy STS tokens whose access key IDs begin with `STSPRXY`.
They are valid against `https://data.source.coop`.
The bucket is `tge-labs`, and the object key begins with `sentinel-2-quarterly-cloudless-mosaics/`.
`tools/publish.py` rejects the former write-prefix form using bucket `us-west-2.opendata.source.coop` and an account-name prefix.

Clients must use the proxy endpoint.
The repository tools pass `endpoint_url` from `catalog.publish.yaml` explicitly.
Botocore also supports reading it from a profile.

The CLI installer places a static binary in `~/.local/bin` without requiring a Cargo toolchain:

```bash
curl --proto '=https' --tlsv1.2 -LsSf \
  https://github.com/source-cooperative/source-coop-cli/releases/latest/download/source-coop-cli-installer.sh | sh
source-coop --version          # must be 0.3.0 or newer
```

### Browser login

Forward the OAuth callback port from the laptop:

```bash
ssh -L 8400:127.0.0.1:8400 rails
```

In that RAILS session, run:

```bash
source-coop login --port 8400
```

Open the printed URL in the laptop's browser and sign in.
The tunnel forwards the redirect to `http://127.0.0.1:8400/callback` to the CLI on RAILS.
Then write the RAILS profile:

```ini
# ~/.aws/config
[profile source-coop]
region = us-west-2
credential_process = source-coop creds
endpoint_url = https://data.source.coop
```

Test a write and deletion under `_work/` before submitting upload jobs:

```bash
aws s3api put-object --profile source-coop --bucket tge-labs \
  --key sentinel-2-quarterly-cloudless-mosaics/_work/rails-write-test.txt \
  --body /dev/null
aws s3api delete-object --profile source-coop --bucket tge-labs \
  --key sentinel-2-quarterly-cloudless-mosaics/_work/rails-write-test.txt
```

RAILS has no OS keyring, so the CLI stores credentials at `~/.cache/source-coop/credentials/<role>.json` with mode 0600.
`source-coop creds` uses the cached refresh token for about a month.
Log in again when refresh fails.

Use CLI 0.3.0 or newer for login.
Older builds defaulted `--scope` to `openid` without `offline_access`.
Those builds did not cache a refresh token.
Those credentials expired after hours and caused later jobs to fail.
Check `source-coop --version` before login.
During setup, a 0.2.0 binary in `~/.cargo/bin` shadowed Homebrew's 0.3.0 installation on the maintainer's laptop.

Only upload steps need credentials.
All pipeline reads use anonymous HTTPS.

### Proxy behavior

Laptop tests confirmed `put_object`, `head_object`, `get_object`, and `delete_object` on the product prefix.
Uploaded objects appeared at their public `https://data.source.coop/tge-labs/...` URLs with identical bytes and the requested content type.
Responses included `x-amz-version-id`, indicating bucket versioning.
Deletion adds a marker, and overwriting an object retains its previous version.

`list_objects_v2` was denied in those tests.
When listing fails, `tools/publish.py` reports the failure and re-uploads the contents of `catalog/`.
Use `--force` to skip the remote listing, which can otherwise traverse more than four million objects.
`upload_items.py --skip-existing` checks each object with HEAD before uploading.

Twice during laptop testing, a burst of roughly forty requests preceded about ten minutes of `AccessDenied` responses to every boto3 call from that process.
Reads of public objects, writes, and deletes failed while the same credentials continued working with the AWS CLI.
The credentials had not expired, the signatures agreed, and the proxy returned an S3-shaped `application/xml` error rather than a Cloudflare response.
The failures cleared without intervention.

The cause and behavior of signed requests at RAILS upload rates remain unmeasured in these notes.
`upload_items.py` uses adaptive retries, continues after individual failures, and reports failed objects for a `--skip-existing` rerun.
Measure failures during the first upload of 28,272 objects, before relying on the pattern across a million-object backfill.

### Python HTTP requests

Cloudflare rejected the default `Python-urllib/*` User-Agent with `403 error code: 1010` in tests of public objects, including manifests.
Set a User-Agent explicitly when using `urllib.request.urlopen`.
`curl`, DuckDB's `httpfs`, GDAL's `/vsicurl`, and `requests` send their own User-Agent and were unaffected in those tests.

## Smoke test

```bash
sbatch --export=ALL,SMOKE=1 tools/rails/quarter.sbatch
```

The smoke job builds six 2024 Q2 tiles across two UTM zones under a `_smoke` prefix at zoom 9 and measures 50 tiles for `valid_fraction`.
It includes upload steps but uses a separate prefix from the catalog.
Set `DRY_RUN=1` to print commands without running them.

## Quarterly processing

```bash
sbatch --export=ALL,YEAR=2024,QUARTER=Q2 tools/rails/quarter.sbatch
```

Use this job to add a quarter or rebuild one after upstream reprocessing.
The pipeline reuses existing outputs where its individual steps permit resumption.
The [handoff's rerun table](../../docs/rails-handoff.md#what-a-rerun-skips) documents those conditions.

Step 2 opens headers for 200 sampled tiles over HTTPS and compares their `proj:code`, `proj:shape`, and `proj:transform` with `tools/mgrs_grid.py` results.
A mismatch prevents publication.

## Backfill

```bash
sbatch --array=0-31%4 tools/rails/backfill.sbatch
```

The array processes 2018 Q1 through 2025 Q4, newest first, with four quarters running concurrently.
Task 0 prints the index-to-quarter map.
Resubmit a failed task individually:

```bash
sbatch --array=7 tools/rails/backfill.sbatch
```

The concurrency limit controls network load, since four quarters can generate thousands of concurrent range reads against one host.
The default list excludes 2017 and 2026 Q2.
An earlier note said 2026 Q2 was awaiting `complete.json`, but the [catalog guide](../../catalog/README.md#coverage) records its completion on 2026-09-28.
Its different internal block size still needs to be described before including the quarter in the catalog.

## Paths

| Path | What |
| --- | --- |
| `$WORK/quarter=YYYY.Qn/items.ndjson` | the staging table, ~130 MB a quarter |
| `$WORK/items/{year}/{Qn}/{tile}/` | item JSON, ~6 kB each |
| `$WORK/quarter=YYYY.Qn/overview-scratch/` | per-zone VRTs and warps; the resume point |
| `$WORK/coverage/YYYY.Qn.parquet` | one staged quarter of coverage rows |
| `$PUBLISH/` | exactly the bucket layout below the catalog prefix |

`upload.py --data-dir` walks `$PUBLISH`, mapping each relative path to an object key.
`TMPDIR` is on `/u` because compute-node `/tmp` is a 64 GB tmpfs.
Overview scratch can occupy tens of gigabytes and must persist between jobs to reuse completed zone warps.

## Measured costs

These measurements used all 28,272 items for 2024 Q2 on one `cpu` node, rails01, with 32 cores and 120 GB.

| Step | On rails | On a laptop | Note |
| --- | --- | --- | --- |
| manifest download | 2.7 s | 96 s | 2.8 MB |
| `make_items` | 307 s | 6.1 s | filesystem metadata cost; see below |
| geometry gate, 200 tiles | 7.5 s | 18.7 s for 40 | 0 mismatches |
| `build_quarter` | 21.4 s | 16.2 s | 5.3 MB, 5 row groups, `gpio check all` clean |
| `make_coverage` | — | ~75 s | 3.4 MB |
| `make_footprints` | — | 13 s | 10.2 MB of PMTiles |

The original notes described the RAILS and laptop `build_quarter` outputs as byte-identical, with 28,272 rows, 28,204 MGRS cells, 5 row groups, 5.3 MB, and geo metadata 2.0.0.
The [later pilot comparison](../../docs/rails-handoff.md#validating-the-pilot-when-it-finishes) records a 3-byte difference in the Parquet `created_by` footer.
Use that comparison when checking output equivalence.

### Small-file writes

Writing 28,272 item JSONs to `/u` took 307 seconds, compared with 6 seconds on a laptop SSD.
`/u` uses NFS over RDMA, and filesystem metadata operations dominated the cost.
Writing 2,000 small files inside an allocation took:

    node-local /tmp   0.054 s
    /u                7.78 s

The measured difference was 145x, corresponding to about five minutes per quarter or three hours across the backfill.
The item JSONs total about 190 MB and are needed only for the same job's upload step, so node-local storage could reduce that cost.
The pilot used `/u`, and the node-local option has not been implemented.
Keep overview scratch on `/u` so it survives resubmission to a different node.

### Overview staging

`gdalbuildvrt` opens each source to read its georeferencing, serially within a call.
Zone 32601's 138 tiles took about 60 seconds for one band.
For roughly 360 zone-band pairs, staging alone was projected to take six hours before warping, and the first pilot was canceled during staging.
Staging now uses `4x --jobs` workers, capped at 64, to overlap HTTP waits.

At zoom 9, each tile is read at its 32x overview of 313 × 313 pixels per band.
Zoom 10 uses the 16x overview of 626 × 626 pixels, or four times the bytes.
The estimate is about 600 kB per band per tile, totaling roughly 51 GB per quarter at zoom 10 compared with 13 GB at zoom 9.

Item JSON uploads also take many requests, with 28,272 objects per quarter and about a million across the backfill.
Round-trip latency dominates their upload cost.
