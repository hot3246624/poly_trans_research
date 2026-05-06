#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${POLYTRANS_ROOT_DIR:-$HOME/poly_trans_research}"
UV_BIN="${POLYTRANS_UV_BIN:-$HOME/.local/bin/uv}"
REPLAY_LOCK_PATH="${POLYTRANS_REPLAY_LOCK_PATH:-$ROOT_DIR/data/locks/replay_maintenance.lock}"
OUTCOME_DAYS_BACK="${POLYTRANS_OUTCOME_DAYS_BACK:-5}"
OUTCOME_SYMBOLS="${POLYTRANS_OUTCOME_SYMBOLS:-}"
OUTCOME_FETCH_RETRIES="${POLYTRANS_OUTCOME_FETCH_RETRIES:-3}"
OUTCOME_TIMEOUT_SEC="${POLYTRANS_OUTCOME_TIMEOUT_SEC:-15}"
OUTCOME_SLEEP_SEC="${POLYTRANS_OUTCOME_SLEEP_SEC:-0.02}"
OUTCOME_REPORT_PATH="${POLYTRANS_OUTCOME_REPORT_PATH:-$ROOT_DIR/data/replay/audits/outcome_backfill_recent.json}"

cd "$ROOT_DIR"
mkdir -p "$(dirname "$REPLAY_LOCK_PATH")" "$(dirname "$OUTCOME_REPORT_PATH")"

if [ "$OUTCOME_DAYS_BACK" -lt 1 ]; then
  OUTCOME_DAYS_BACK=1
fi

recent_days_csv() {
  local idx
  local out=()
  for ((idx = OUTCOME_DAYS_BACK - 1; idx >= 0; idx--)); do
    out+=("$(date -u -d "$idx day ago" +%F)")
  done
  local joined
  joined="$(IFS=,; echo "${out[*]}")"
  printf '%s\n' "$joined"
}

run_outcome_backfill() {
  local days_csv
  days_csv="$(recent_days_csv)"
  local cmd=(
    "$UV_BIN" run python cfdata.py --log-level INFO backfill-market-outcomes
    --days "$days_csv"
    --replay-root data/replay
    --fetch-retries "$OUTCOME_FETCH_RETRIES"
    --timeout-sec "$OUTCOME_TIMEOUT_SEC"
    --sleep-sec "$OUTCOME_SLEEP_SEC"
    --output "$OUTCOME_REPORT_PATH"
  )
  if [ -n "$OUTCOME_SYMBOLS" ]; then
    cmd+=(--symbols "$OUTCOME_SYMBOLS")
  fi
  "${cmd[@]}"
}

run_cycle() {
  "$UV_BIN" run python cfdata.py --log-level INFO build-replay-rolling --hours 24 --validate-latest
  run_outcome_backfill
  scripts/ops/refresh_replay_published.sh
}

exec 9>"$REPLAY_LOCK_PATH"
flock 9
run_cycle
