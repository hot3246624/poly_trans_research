#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${POLYTRANS_ROOT_DIR:-$HOME/poly_trans_research}"
UV_BIN="${POLYTRANS_UV_BIN:-$HOME/.local/bin/uv}"
LOCK_PATH="${POLYTRANS_REPLAY_LOCK_PATH:-$ROOT_DIR/data/locks/replay_maintenance.lock}"
RAW_ROOT="${POLYTRANS_RAW_ROOT:-data/raw}"
REPLAY_ROOT="${POLYTRANS_REPLAY_ROOT:-data/replay}"
OUTPUT="${POLYTRANS_AUDIT_OUTPUT:-data/replay/audits/replay_audit_report.json}"
MARKDOWN_OUTPUT="${POLYTRANS_AUDIT_MARKDOWN_OUTPUT:-data/replay/audits/replay_audit_report.md}"
TIMEOUT_VALUE="${POLYTRANS_AUDIT_TIMEOUT:-45m}"
DAYS="${1:-${POLYTRANS_AUDIT_DAYS:-}}"

if [ -z "$DAYS" ]; then
  echo "usage: $0 YYYY-MM-DD[,YYYY-MM-DD...]"
  exit 2
fi

cd "$ROOT_DIR"
mkdir -p "$(dirname "$LOCK_PATH")" "$(dirname "$OUTPUT")"

timeout "$TIMEOUT_VALUE" flock -n "$LOCK_PATH" \
  nice -n 15 ionice -c2 -n7 \
  "$UV_BIN" run python cfdata.py --log-level INFO \
  audit-replay-market \
  --days "$DAYS" \
  --raw-root "$RAW_ROOT" \
  --replay-root "$REPLAY_ROOT" \
  --output "$OUTPUT" \
  --markdown-output "$MARKDOWN_OUTPUT"
