#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODE_ROOT="${POLYTRANS_CODE_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
DATA_ROOT="${POLYTRANS_DATA_ROOT:-${POLYTRANS_ROOT_DIR:-$HOME/poly_trans_research}}"
UV_BIN="${POLYTRANS_UV_BIN:-$HOME/.local/bin/uv}"
REPLAY_LOCK_PATH="${POLYTRANS_REPLAY_LOCK_PATH:-$DATA_ROOT/data/locks/replay_maintenance.lock}"
TARGET_DAY="${POLYTRANS_TARGET_DAY:-$(date -u -d '1 day ago' +%F)}"
FORCE_REBUILD="${POLYTRANS_FORCE_REBUILD:-0}"
PUBLISH_HOT_DAYS="${POLYTRANS_REPLAY_PUBLISH_HOT_DAYS:-1}"
OUTCOME_FETCH_RETRIES="${POLYTRANS_OUTCOME_FETCH_RETRIES:-3}"
OUTCOME_TIMEOUT_SEC="${POLYTRANS_OUTCOME_TIMEOUT_SEC:-15}"
OUTCOME_SLEEP_SEC="${POLYTRANS_OUTCOME_SLEEP_SEC:-0.02}"
OUTCOME_REPORT_PATH="${POLYTRANS_OUTCOME_REPORT_PATH:-$DATA_ROOT/data/replay/audits/outcome_backfill_${TARGET_DAY}.json}"
VALIDATE_GAP_THRESHOLD_MS="${POLYTRANS_VALIDATE_GAP_THRESHOLD_MS:-0}"
BUILD_RESEARCH_ARTIFACTS="${POLYTRANS_BUILD_RESEARCH_ARTIFACTS:-1}"
REPLAY_BLOCKLIST_PATH="${POLYTRANS_REPLAY_BUILD_BLOCKLIST:-$DATA_ROOT/data/research_artifacts_blocklist.txt}"

RAW_ROOT="$DATA_ROOT/data/raw"
REPLAY_ROOT="$DATA_ROOT/data/replay"
PUBLISH_DIR="$DATA_ROOT/data/replay_published"
RAW_DAY_DIR="$RAW_ROOT/$TARGET_DAY"
PUBLISHED_DB="$PUBLISH_DIR/$TARGET_DAY/crypto_5m.sqlite"

cd "$CODE_ROOT"
mkdir -p "$(dirname "$REPLAY_LOCK_PATH")" "$(dirname "$OUTCOME_REPORT_PATH")"

build_research_artifacts() {
  if [ "$BUILD_RESEARCH_ARTIFACTS" != "1" ]; then
    return
  fi

  POLYTRANS_CODE_ROOT="$CODE_ROOT" \
  POLYTRANS_DATA_ROOT="$DATA_ROOT" \
  POLYTRANS_TARGET_DAY="$TARGET_DAY" \
  "$SCRIPT_DIR/build_day_research_artifacts.sh"
}

is_blocked_replay_day() {
  [ -f "$REPLAY_BLOCKLIST_PATH" ] || return 1
  python3 - "$REPLAY_BLOCKLIST_PATH" "$TARGET_DAY" "${TARGET_DAY//-/}" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
target_day = sys.argv[2]
label = sys.argv[3]

blocked = set()
for raw in path.read_text().splitlines():
    line = raw.split("#", 1)[0].strip()
    if line:
        blocked.add(line)

raise SystemExit(0 if target_day in blocked or label in blocked else 1)
PY
}

if is_blocked_replay_day; then
  echo "build_previous_day_replay_publish: target_day=$TARGET_DAY is blocked by $REPLAY_BLOCKLIST_PATH, skip replay/artifacts"
  exit 0
fi

if [ ! -d "$RAW_DAY_DIR" ]; then
  echo "build_previous_day_replay_publish: raw day missing for $TARGET_DAY, skip"
  exit 0
fi

run_cycle() {
  "$UV_BIN" run python cfdata.py --log-level INFO build-replay \
    --day "$TARGET_DAY" \
    --raw-root "$RAW_ROOT" \
    --replay-root "$REPLAY_ROOT"
  "$UV_BIN" run python cfdata.py --log-level INFO validate-replay \
    --day "$TARGET_DAY" \
    --replay-root "$REPLAY_ROOT" \
    --gap-threshold-ms "$VALIDATE_GAP_THRESHOLD_MS"
  "$UV_BIN" run python cfdata.py --log-level INFO backfill-market-outcomes \
    --days "$TARGET_DAY" \
    --replay-root "$REPLAY_ROOT" \
    --fetch-retries "$OUTCOME_FETCH_RETRIES" \
    --timeout-sec "$OUTCOME_TIMEOUT_SEC" \
    --sleep-sec "$OUTCOME_SLEEP_SEC" \
    --output "$OUTCOME_REPORT_PATH"
  POLYTRANS_ROOT_DIR="$DATA_ROOT" \
  POLYTRANS_REPLAY_PUBLISH_HOT_DAYS="$PUBLISH_HOT_DAYS" \
  "$SCRIPT_DIR/refresh_replay_published.sh"

  build_research_artifacts
}

exec 9>"$REPLAY_LOCK_PATH"
if ! flock -n 9; then
  echo "build_previous_day_replay_publish: replay maintenance lock busy for $TARGET_DAY, skip"
  exit 0
fi

if [ -f "$PUBLISHED_DB" ] && [ "$FORCE_REBUILD" != "1" ]; then
  build_research_artifacts
  echo "build_previous_day_replay_publish: published replay already exists for $TARGET_DAY after lock, skip"
  exit 0
fi

run_cycle
