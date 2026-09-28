#!/bin/bash
# Build and publish one quarter. This is the whole update path: adding a
# new quarter to the catalog, or repairing one, is this job and nothing
# else.
#
#     ssh rails 'cd ~/s2-mosaics-catalog && \
#       sbatch --export=ALL,YEAR=2024,QUARTER=Q2 tools/rails/quarter.sbatch'
#
# Six steps, in order, each one skipped when its output already exists:
#
#   1. make_items       manifest -> items.ndjson + item JSON
#   2. make_items --verify  sampled COG headers vs the computed geometry
#   3. build_quarter    items.ndjson -> items.parquet, gpio-written
#   4. make_overview    tile overviews -> overview.tif + thumbnail.webp
#   5. make_coverage    manifest -> the coverage rollup, plus footprints
#   6. upload           parquet, COG, thumbnail, coverage, then item JSON
#
# Step 2 is a gate, not a report: a quarter whose sampled tiles disagree
# with their own COG headers is not published. Everything before the
# upload is idempotent, so a job that hits the wall clock is resubmitted
# unchanged and picks up where it stopped.
#
# DRY_RUN=1 prints the commands. SMOKE=1 builds $SMOKE_TILES of
# $SMOKE_YEAR $SMOKE_QUARTER under a _smoke key prefix, which is the
# rehearsal to run before the first real quarter.
#SBATCH --job-name=s2mos-quarter
#SBATCH --partition=cpu
#SBATCH --account=bgtj-tgirails
#SBATCH --time=08:00:00
#SBATCH --nodes=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=120g
#SBATCH --output=logs/s2mos-quarter-%j.out
set -euo pipefail
REPO="${REPO:-${SLURM_SUBMIT_DIR:-$HOME/s2-mosaics-catalog}}"
[ -f "$REPO/tools/rails/env.sh" ] || { echo "no checkout at $REPO; set REPO"; exit 1; }
source "$REPO/tools/rails/env.sh"
mkdir -p "$REPO/logs"

if [ "$SMOKE" = 1 ]; then YEAR="$SMOKE_YEAR"; QUARTER="$SMOKE_QUARTER"; fi
YEAR="${YEAR:?set YEAR=YYYY via sbatch --export=ALL,YEAR=...}"
QUARTER="${QUARTER:?set QUARTER=Qn via sbatch --export=ALL,QUARTER=...}"
PART="quarter=$YEAR.$QUARTER"
MEMORY="${MEMORY:-96GB}"
JOBS="${JOBS:-${SLURM_CPUS_PER_TASK:-16}}"
VERIFY="${VERIFY:-200}"
ITEMS="$WORK/items"

echo "=== $YEAR $QUARTER  work=$WORK publish=$PUBLISH zoom=$ZOOM smoke=$SMOKE"

# 1. Items. The manifest is a 2.8 MB download; everything else is derived.
if [ -s "$WORK/$PART/items.ndjson" ]; then
  echo "step 1: $WORK/$PART/items.ndjson exists, skipping"
else
  EXTRA=()
  [ "$SMOKE" = 1 ] && EXTRA+=(--tiles "$SMOKE_TILES")
  run python3 "$REPO/tools/make_items.py" --year "$YEAR" --quarter "$QUARTER" \
    --out "$WORK" --items-dir "$ITEMS" ${EXTRA[@]+"${EXTRA[@]}"}
fi

# 2. The gate. Sampled tiles, read over https, header only. --verify-only
#    writes nothing, so this does not rebuild the 130 MB staging file.
GATE=(--verify "$VERIFY" --verify-only)
[ "$SMOKE" = 1 ] && GATE+=(--tiles "$SMOKE_TILES")
run python3 "$REPO/tools/make_items.py" --year "$YEAR" --quarter "$QUARTER" \
  "${GATE[@]}"

# 3. The stac-geoparquet.
run python3 "$REPO/tools/build_quarter.py" --year "$YEAR" --quarter "$QUARTER" \
  --work "$WORK" --out "$PUBLISH" --memory "$MEMORY"

# 4. The browse layer and its thumbnail. The overview is the expensive
#    step; its per-zone GeoTIFFs are kept so a resubmit resumes.
run python3 "$REPO/tools/make_overview.py" --year "$YEAR" --quarter "$QUARTER" \
  --work "$WORK" --out "$PUBLISH" --zoom "$ZOOM" --jobs "$JOBS" --keep-scratch
run python3 "$REPO/tools/make_overview.py" --year "$YEAR" --quarter "$QUARTER" \
  --out "$PUBLISH" --thumbnail

# 5. Coverage: this quarter's rows, then the rollup and the tileset.
run python3 "$REPO/tools/make_coverage.py" --work "$WORK" --out "$PUBLISH" \
  --quarters "$YEAR.$QUARTER" --valid-fraction "$VALID_FRACTION" --jobs "$JOBS"
run python3 "$REPO/tools/make_footprints.py" --work "$WORK" --out "$PUBLISH"

# 6. Upload. Data files first: an item JSON that points at a parquet that
#    is not there yet is a worse intermediate state than the reverse.
UP=(
  "mosaics/$PART/items.parquet"
  "mosaics/$PART/overview.tif"
  "coverage/tiles.parquet"
  "coverage/footprints.pmtiles"
)
for f in "${UP[@]}"; do
  [ -s "$PUBLISH/$f" ] || { echo "missing $PUBLISH/$f"; exit 1; }
done
run python3 "$REPO/tools/rails/upload.py" --data-dir "$PUBLISH" \
  --key-prefix "$KEY_PREFIX" "${UP[@]}"
run python3 "$REPO/tools/rails/upload_items.py" --items-dir "$ITEMS" \
  --year "$YEAR" --quarter "$QUARTER" --key-prefix "$KEY_PREFIX" \
  --workers "${UPLOAD_WORKERS:-32}"

# The thumbnail is catalog metadata, not a data file, so it goes to the
# collection rather than the partition and upload.py's suffix allow-list
# does not cover it. It is copied into the checkout for tools/publish.py.
THUMB="$PUBLISH/mosaics/$PART/thumbnail.webp"
[ -s "$THUMB" ] && echo "thumbnail ready at $THUMB (publish with tools/publish.py)"

echo "=== $YEAR $QUARTER done"
