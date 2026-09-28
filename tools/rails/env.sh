# Sourced by every script in this directory. Sets the toolchain, the AWS
# defaults and the shared paths on /u.
#
# Every sbatch finds this file through $REPO, never through $BASH_SOURCE:
# Slurm copies a submitted script to a spool directory, so the script's
# own path says nothing about where the checkout is. $REPO is the submit
# directory under Slurm and ~/s2-mosaics-catalog otherwise; a laptop dry
# run sets REPO to its checkout.
#
# The toolchain is a micromamba env on shared /u, reachable from compute
# nodes without `module load`. See README.md for how to create it.
export PATH="${S2M_ENV:-/u/cholmes/micromamba/envs/s2mosaics}/bin:$PATH"
export AWS_DEFAULT_REGION=us-west-2
# Source Cooperative is a data proxy since its 0.3 CLI: this profile's
# credential_process issues proxy STS tokens that are valid only against
# https://data.source.coop, and the bucket is the account name
# (`tge-labs`) with the product as the key prefix. The endpoint itself
# comes from `endpoint_url` in catalog.publish.yaml, which every tool
# passes explicitly. See README.md for the one headless login.
# Building needs no AWS identity at all; every read is anonymous https.
export AWS_PROFILE="${AWS_PROFILE:-source-coop}"
# DuckDB, GDAL and Python all honour TZ. Every quarter boundary in this
# catalog is a UTC instant, and a node that thinks otherwise would write
# start_datetime an hour out.
export TZ=UTC

# DRY_RUN=1 prints each command instead of running it and creates nothing.
# SMOKE=1 keeps every path under a _smoke/ directory or key prefix and
# builds one small quarter over one small region.
export DRY_RUN="${DRY_RUN:-0}"
export SMOKE="${SMOKE:-0}"
export SMOKE_YEAR="${SMOKE_YEAR:-2024}"
export SMOKE_QUARTER="${SMOKE_QUARTER:-Q2}"
# Six tiles over the Netherlands and the German border: two UTM zones, so
# the zone-mosaic path is exercised, and about 2 MB of reads.
export SMOKE_TILES="${SMOKE_TILES:-31UFT_0_0,31UFU_0_0,31UGT_0_0,31UGU_0_0,32ULC_0_0,32ULD_0_0}"

# /tmp on a rails node is a 64 GB tmpfs. A quarter's staging file is
# ~130 MB and its overview scratch can be tens of GB, so both live on /u
# and DuckDB spills into $WORK/.duckdb-tmp rather than into /tmp.
export TMPDIR="${TMPDIR:-/u/cholmes/s2-mosaics/tmp}"

# Where the staging file, the item JSON and the overview scratch live
# between steps: shared project space, so a later job on another node can
# read what an earlier one wrote.
export WORK="${WORK:-/u/cholmes/s2-mosaics/work}"
# Where built files wait for their upload. Its layout IS the bucket
# layout below the catalog prefix, which is what upload.py --data-dir
# relies on.
export PUBLISH="${PUBLISH:-/u/cholmes/s2-mosaics/publish}"
export REPO="${REPO:-$HOME/s2-mosaics-catalog}"

export PUBLIC_BASE="${PUBLIC_BASE:-https://data.source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics}"
# upload.py --key-prefix: a directory inserted between the catalog prefix
# and the file's path. Empty for the real catalog.
export KEY_PREFIX="${KEY_PREFIX:-}"

# A job that builds for hours and then cannot upload has wasted the
# allocation, so the credentials are checked before any work starts.
# `source-coop creds` exits non-zero when the cached refresh token is
# gone, which is the one failure that needs a human and a browser.
check_creds() {
  if ! command -v source-coop >/dev/null; then
    echo "source-coop is not on PATH; see tools/rails/README.md" >&2
    return 1
  fi
  if ! source-coop creds >/dev/null 2>&1; then
    echo "source-coop has no usable cached credentials." >&2
    echo "Log in again: ssh -L 8400:127.0.0.1:8400 rails, then" >&2
    echo "  source-coop login --port 8400" >&2
    return 1
  fi
}

# Base resolution of the browse overview, as a Web Mercator zoom level.
# 9 is 305.75 m/px; each step up is four times the pixels and four times
# the reads (zoom 10 reads the 16x tile overview rather than the 32x:
# ~51 GB a quarter against ~13 GB).
#
# This is one number for all 36 quarters, and it is deliberate. The
# browse layer exists to be scrubbed through time, so two quarters at
# different base resolutions would make every transition between them
# look like a change in the scene. Raising it means rebuilding all 36,
# not just the next one.
export ZOOM="${ZOOM:-9}"
# How many tiles per quarter get their valid_fraction measured. Each one
# is a 313 x 313 read of the red band's smallest overview.
export VALID_FRACTION="${VALID_FRACTION:-2000}"

if [ "$SMOKE" = 1 ]; then
  WORK="$WORK/_smoke"
  PUBLISH="$PUBLISH/_smoke"
  KEY_PREFIX="_smoke"
  ZOOM=9
  VALID_FRACTION=50
fi

mkdir -p "$TMPDIR" "$WORK" "$PUBLISH" 2>/dev/null || true

# run CMD...: run it, or under DRY_RUN=1 print it, shell-quoted, and do
# nothing. A heredoc on stdin is consumed either way.
run() {
  if [ "$DRY_RUN" = 1 ]; then
    printf 'dry-run:'; printf ' %q' "$@"; printf '\n'
    return 0
  fi
  "$@"
}
