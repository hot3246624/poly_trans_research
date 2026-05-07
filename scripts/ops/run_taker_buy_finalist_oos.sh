#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
UV_BIN="${POLYTRANS_UV_BIN:-$(command -v uv)}"
DAYS_CSV="${POLYTRANS_OOS_DAYS:-2026-05-02,2026-05-03,2026-05-04,2026-05-05}"
OUTPUT_TAG="${POLYTRANS_OOS_TAG:-20260507_0502_0505}"

if [ -z "$UV_BIN" ]; then
  echo "uv not found" >&2
  exit 127
fi

if [ -d "/mnt/poly-replay" ]; then
  DEFAULT_REPLAY_ROOT="/mnt/poly-replay"
else
  DEFAULT_REPLAY_ROOT="$ROOT_DIR/data/replay_published"
fi

REPLAY_ROOT="${POLYTRANS_REPLAY_ROOT:-$DEFAULT_REPLAY_ROOT}"
OUTPUT_DIR="${POLYTRANS_OOS_OUTPUT_DIR:-/tmp/taker_buy_finalist_oos_${OUTPUT_TAG}}"

for day in ${DAYS_CSV//,/ }; do
  db_path="$REPLAY_ROOT/$day/crypto_5m.sqlite"
  if [ ! -f "$db_path" ]; then
    echo "missing replay db: $db_path" >&2
    exit 3
  fi
done

if [ -e "$OUTPUT_DIR" ] && [ "${POLYTRANS_OOS_OVERWRITE:-0}" != "1" ]; then
  echo "output already exists: $OUTPUT_DIR" >&2
  echo "set POLYTRANS_OOS_OVERWRITE=1 to replace it" >&2
  exit 4
fi

rm -rf "$OUTPUT_DIR"
mkdir -p "$OUTPUT_DIR"

cd "$ROOT_DIR"
"$UV_BIN" run python scripts/validate_taker_buy_finalist_oos.py \
  --replay-root "$REPLAY_ROOT" \
  --days "$DAYS_CSV" \
  --output-dir "$OUTPUT_DIR"

echo "taker_buy_finalist_oos output: $OUTPUT_DIR"
