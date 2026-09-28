#!/bin/bash
# Run quarter.sbatch's work across many quarters as one array job.
#
#     ssh rails 'cd ~/s2-mosaics-catalog && \
#       sbatch --array=0-34%4 tools/rails/backfill.sbatch'
#
# The array index picks a quarter out of $QUARTERS, which defaults to
# 2018 Q1 through 2025 Q4 **newest first**: the recent quarters are the
# ones anyone will look at, and each one that lands is a usable catalog
# on its own. 2017 is deliberately not in the default list (its tiles are
# the oldest processing and worth eyeballing separately), and 2026 Q2 is
# excluded until its complete.json marker appears.
#
# `%4` is the throttle, and it is about the network rather than the
# cores: four quarters at once is already thousands of concurrent range
# reads against data.source.coop. Raise it only after watching what one
# quarter actually does to the uplink.
#
# Each task runs the same steps quarter.sbatch does, by sourcing it is
# not possible under Slurm, so it execs the same tools in the same order.
# Every step is idempotent, so a failed array task is resubmitted for its
# index alone:
#
#     sbatch --array=7 tools/rails/backfill.sbatch
#
# A task that fails does not stop its siblings. At the end, grep the logs
# for "done" and compare against the list this prints on task 0.
#SBATCH --job-name=s2mos-backfill
#SBATCH --partition=cpu
#SBATCH --account=bgtj-tgirails
#SBATCH --time=08:00:00
#SBATCH --nodes=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=120g
#SBATCH --output=logs/s2mos-backfill-%A_%a.out
set -euo pipefail
REPO="${REPO:-${SLURM_SUBMIT_DIR:-$HOME/s2-mosaics-catalog}}"
[ -f "$REPO/tools/rails/env.sh" ] || { echo "no checkout at $REPO; set REPO"; exit 1; }
source "$REPO/tools/rails/env.sh"
mkdir -p "$REPO/logs"

if [ -z "${QUARTERS:-}" ]; then
  QUARTERS=""
  for y in $(seq 2025 -1 2018); do
    for q in Q4 Q3 Q2 Q1; do QUARTERS="$QUARTERS $y.$q"; done
  done
fi
read -r -a LIST <<< "$QUARTERS"
IDX="${SLURM_ARRAY_TASK_ID:-0}"
if [ "$IDX" = 0 ]; then
  echo "backfill covers ${#LIST[@]} quarter(s), newest first:"
  printf '  %2d %s\n' $(for i in "${!LIST[@]}"; do echo "$i ${LIST[$i]}"; done)
fi
[ "$IDX" -lt "${#LIST[@]}" ] || { echo "index $IDX past the end"; exit 1; }
TARGET="${LIST[$IDX]}"
export YEAR="${TARGET%%.*}" QUARTER="${TARGET##*.}"
echo "=== array task $IDX -> $YEAR $QUARTER"

# One quarter is one invocation of the same script, so the steps, the
# skip-if-exists rules and the verification gate cannot drift between the
# incremental path and the backfill.
# Not wrapped in run(): quarter.sbatch reads DRY_RUN itself, so a dry run
# shows every step rather than the one line that would have started it.
bash "$REPO/tools/rails/quarter.sbatch"
