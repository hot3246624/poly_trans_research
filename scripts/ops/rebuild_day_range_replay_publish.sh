#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${POLYTRANS_ROOT_DIR:-$HOME/poly_trans_research}"
PUBLISH_HOT_DAYS="${POLYTRANS_REPLAY_PUBLISH_HOT_DAYS:-1}"
VALIDATE_GAP_THRESHOLD_MS="${POLYTRANS_VALIDATE_GAP_THRESHOLD_MS:-0}"

if [ "$#" -lt 1 ]; then
  echo "usage: $0 YYYY-MM-DD [YYYY-MM-DD ...]" >&2
  exit 2
fi

cd "$ROOT_DIR"

for day in "$@"; do
  echo "=== rebuild_day_range_replay_publish: $day start $(date -u --iso-8601=seconds) ==="
  POLYTRANS_ROOT_DIR="$ROOT_DIR" \
  POLYTRANS_TARGET_DAY="$day" \
  POLYTRANS_FORCE_REBUILD=1 \
  POLYTRANS_REPLAY_PUBLISH_HOT_DAYS="$PUBLISH_HOT_DAYS" \
  POLYTRANS_VALIDATE_GAP_THRESHOLD_MS="$VALIDATE_GAP_THRESHOLD_MS" \
  "$SCRIPT_DIR/build_previous_day_replay_publish.sh"
  echo "=== rebuild_day_range_replay_publish: $day done $(date -u --iso-8601=seconds) ==="
done
