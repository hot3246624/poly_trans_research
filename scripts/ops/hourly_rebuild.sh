#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
UV_BIN="${POLYTRANS_UV_BIN:-python3}"
REPLAY_LOCK_PATH="${POLYTRANS_REPLAY_LOCK_PATH:-$ROOT_DIR/data/locks/replay_maintenance.lock}"
cd "$ROOT_DIR"

mkdir -p "$(dirname "$REPLAY_LOCK_PATH")"

if [[ "$UV_BIN" == */uv || "$UV_BIN" == "uv" ]]; then
  flock "$REPLAY_LOCK_PATH" "$UV_BIN" run python cfdata.py --log-level INFO build-replay-rolling --hours 24 --validate-latest
else
  flock "$REPLAY_LOCK_PATH" "$UV_BIN" cfdata.py --log-level INFO build-replay-rolling --hours 24 --validate-latest
fi
