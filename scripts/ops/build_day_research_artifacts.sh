#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODE_ROOT="${POLYTRANS_CODE_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
DATA_ROOT="${POLYTRANS_DATA_ROOT:-${POLYTRANS_ROOT_DIR:-$HOME/poly_trans_research}}"
UV_BIN="${POLYTRANS_UV_BIN:-$HOME/.local/bin/uv}"
RESEARCH_SCRIPT_ROOT="${POLYTRANS_RESEARCH_SCRIPT_ROOT:-$DATA_ROOT/scripts}"
TARGET_DAY="${POLYTRANS_TARGET_DAY:-$(date -u -d '1 day ago' +%F)}"
LABEL="${POLYTRANS_ARTIFACT_LABEL:-${TARGET_DAY//-/}}"
MIN_FREE_GB="${POLYTRANS_ARTIFACT_MIN_FREE_GB:-120}"
VALIDATION_SAMPLES="${POLYTRANS_TAKER_CACHE_VALIDATION_SAMPLES:-1000}"
DUCKDB_THREADS="${POLYTRANS_ARTIFACT_DUCKDB_THREADS:-2}"
FORCE_ARTIFACTS="${POLYTRANS_FORCE_ARTIFACTS:-0}"
FORCE_ARGS=()
if [ "$FORCE_ARTIFACTS" = "1" ]; then
  FORCE_ARGS=(--force)
fi

REPLAY_ROOT="$DATA_ROOT/data/replay_published"
CACHE_ROOT="$DATA_ROOT/data/backtest_cache"
STORE_ROOT="$DATA_ROOT/data/verification_store"
LOCK_PATH="${POLYTRANS_ARTIFACT_LOCK_PATH:-$DATA_ROOT/data/locks/research_artifacts_${LABEL}.lock}"
V1_CACHE="$CACHE_ROOT/taker_buy_signal_core_v1_strict_l1/$LABEL"
V2_CACHE="$CACHE_ROOT/taker_buy_signal_core_v2_strict_l1/$LABEL"
COMPLETION_STORE="$STORE_ROOT/completion_unwind_event_store_v2/$LABEL"
SOURCE_DB="$REPLAY_ROOT/$TARGET_DAY/crypto_5m.sqlite"

json_error_count_zero() {
  local path="$1"
  python3 - "$path" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.is_file():
    raise SystemExit(1)
try:
    payload = json.loads(path.read_text())
except Exception:
    raise SystemExit(1)
raise SystemExit(0 if int(payload.get("error_count", -1)) == 0 else 1)
PY
}

require_source() {
  if [ ! -f "$SOURCE_DB" ]; then
    echo "build_day_research_artifacts: missing published replay for $TARGET_DAY: $SOURCE_DB" >&2
    exit 1
  fi

  python3 - "$SOURCE_DB" <<'PY'
import sqlite3
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    rows = dict(conn.execute("SELECT name, seq FROM sqlite_sequence").fetchall())
except sqlite3.Error as exc:
    raise SystemExit(f"published replay is not ready: {exc}") from exc
finally:
    try:
        conn.close()
    except Exception:
        pass

required = ("md_book_l1", "md_book_l2", "md_trades")
missing = [name for name in required if int(rows.get(name, 0) or 0) <= 0]
if missing:
    raise SystemExit(f"published replay is not ready: missing sqlite_sequence rows {missing}")
PY
}

build_v1_cache() {
  if [ "$FORCE_ARTIFACTS" != "1" ] && json_error_count_zero "$V1_CACHE/CACHE_VALIDATION.json"; then
    echo "build_day_research_artifacts: V1 strict cache already validated for $LABEL, skip"
    return
  fi

  "$UV_BIN" run python "$RESEARCH_SCRIPT_ROOT/build_taker_buy_cache_v1.py" \
    --replay-root "$REPLAY_ROOT" \
    --cache-root "$CACHE_ROOT" \
    --days "$TARGET_DAY" \
    --cache-name taker_buy_signal_core_v1_strict_l1 \
    --label "$LABEL" \
    --builder-script "$CODE_ROOT/scripts/build_taker_buy_signal_candidate_cache_stream.py" \
    --min-free-gb "$MIN_FREE_GB" \
    "${FORCE_ARGS[@]}"

  "$UV_BIN" run python "$RESEARCH_SCRIPT_ROOT/validate_taker_buy_cache_v1.py" \
    --cache-dir "$V1_CACHE" \
    --replay-root "$REPLAY_ROOT" \
    --samples "$VALIDATION_SAMPLES" \
    --require-strict-l1
}

build_v2_cache() {
  if [ "$FORCE_ARTIFACTS" != "1" ] && json_error_count_zero "$V2_CACHE/CACHE_VALIDATION_V2.json"; then
    echo "build_day_research_artifacts: V2 strict cache already validated for $LABEL, skip"
    return
  fi

  "$UV_BIN" run --with duckdb python "$RESEARCH_SCRIPT_ROOT/build_taker_buy_cache_v2.py" \
    --source-cache-dir "$V1_CACHE" \
    --cache-root "$CACHE_ROOT" \
    --cache-name taker_buy_signal_core_v2_strict_l1 \
    --label "$LABEL" \
    --min-free-gb "$MIN_FREE_GB" \
    --duckdb-threads "$DUCKDB_THREADS" \
    "${FORCE_ARGS[@]}"

  "$UV_BIN" run --with duckdb python "$RESEARCH_SCRIPT_ROOT/validate_taker_buy_cache_v2.py" \
    --v1-cache-dir "$V1_CACHE" \
    --v2-cache-dir "$V2_CACHE" \
    --samples "$VALIDATION_SAMPLES"
}

build_completion_store() {
  if [ "$FORCE_ARTIFACTS" != "1" ] && [ -f "$COMPLETION_STORE/EVENT_STORE_MANIFEST.json" ]; then
    echo "build_day_research_artifacts: completion unwind V2 store already published for $LABEL, skip"
    return
  fi

  "$UV_BIN" run --with duckdb python "$RESEARCH_SCRIPT_ROOT/build_completion_unwind_event_store_v2.py" \
    --replay-root "$REPLAY_ROOT" \
    --store-root "$STORE_ROOT" \
    --days "$TARGET_DAY" \
    --label "$LABEL" \
    --progress-every-markets 100 \
    --duckdb-threads "$DUCKDB_THREADS" \
    --min-free-gb "$MIN_FREE_GB" \
    "${FORCE_ARGS[@]}"
}

cd "$CODE_ROOT"
mkdir -p "$(dirname "$LOCK_PATH")"
exec 9>"$LOCK_PATH"
if ! flock -n 9; then
  echo "build_day_research_artifacts: artifact lock busy for $LABEL, skip"
  exit 0
fi

require_source
echo "build_day_research_artifacts: start target_day=$TARGET_DAY label=$LABEL"
build_v1_cache
build_v2_cache
build_completion_store
echo "build_day_research_artifacts: done target_day=$TARGET_DAY label=$LABEL"
