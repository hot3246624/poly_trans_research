#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${POLYTRANS_ROOT_DIR:-$HOME/poly_trans_research}"
UV_BIN="${POLYTRANS_UV_BIN:-$HOME/.local/bin/uv}"
ENV_FILE="${POLYTRANS_ENV_FILE:-config/research.all.public.env}"
LOG_DIR="${POLYTRANS_LOG_DIR:-data/logs}"
SIDECAR_LOG="$ROOT_DIR/$LOG_DIR/sidecar_all_15d_current.log"
REBUILD_LOG="$ROOT_DIR/$LOG_DIR/rebuild_all_15d_current.log"
HEALTH_LOG="$ROOT_DIR/$LOG_DIR/healthcheck_capture.log"
SIDECAR_PID_FILE="$ROOT_DIR/$LOG_DIR/sidecar_all_15d.pid"
REBUILD_PID_FILE="$ROOT_DIR/$LOG_DIR/rebuild_all_15d.pid"
RAW_ROOT_REL="${POLYTRANS_RAW_ROOT:-data/raw}"
REPLAY_ROOT_REL="${POLYTRANS_REPLAY_ROOT:-data/replay}"
MAX_RAW_STALE_SEC="${POLYTRANS_MAX_RAW_STALE_SEC:-600}"
MAX_REBUILD_LOG_STALE_SEC="${POLYTRANS_MAX_REBUILD_LOG_STALE_SEC:-9000}"
MAX_TOTAL_GB="${POLYTRANS_MAX_TOTAL_GB:-80}"
MIN_DISK_FREE_GB="${POLYTRANS_MIN_DISK_FREE_GB:-20}"
END_EPOCH_UTC="${POLYTRANS_END_EPOCH_UTC:-}"

mkdir -p "$ROOT_DIR/$LOG_DIR"
exec 9>"/tmp/polytrans_healthcheck.lock"
if ! flock -n 9; then
  exit 0
fi

timestamp() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

log() {
  printf '[%s] %s\n' "$(timestamp)" "$*" | tee -a "$HEALTH_LOG"
}

pid_running() {
  local pid_file="$1"
  [ -f "$pid_file" ] || return 1
  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  [ -n "$pid" ] || return 1
  kill -0 "$pid" 2>/dev/null
}

remaining_sec() {
  if [ -z "$END_EPOCH_UTC" ]; then
    echo 0
    return 0
  fi
  local now
  now="$(date -u +%s)"
  if [ "$now" -ge "$END_EPOCH_UTC" ]; then
    echo -1
  else
    echo $((END_EPOCH_UTC - now))
  fi
}

restart_sidecar() {
  local rem
  rem="$(remaining_sec)"
  if [ "$rem" -lt 0 ]; then
    log "sidecar restart skipped: run window ended"
    return 0
  fi

  pkill -f 'cfdata.py --log-level INFO capture-sidecar-env' 2>/dev/null || true
  sleep 1

  cd "$ROOT_DIR"
  if [ "$rem" -gt 0 ]; then
    nohup "$UV_BIN" run python cfdata.py --log-level INFO capture-sidecar-env \
      --env-file "$ENV_FILE" \
      --duration-sec "$rem" \
      >> "$SIDECAR_LOG" 2>&1 < /dev/null &
  else
    nohup "$UV_BIN" run python cfdata.py --log-level INFO capture-sidecar-env \
      --env-file "$ENV_FILE" \
      >> "$SIDECAR_LOG" 2>&1 < /dev/null &
  fi
  echo $! > "$SIDECAR_PID_FILE"
  log "sidecar restarted pid=$(cat "$SIDECAR_PID_FILE") remaining_sec=$rem"
}

restart_rebuild() {
  local rem
  rem="$(remaining_sec)"
  if [ "$rem" -lt 0 ]; then
    log "rebuild restart skipped: run window ended"
    return 0
  fi

  pkill -f 'build-replay-rolling --hours 24' 2>/dev/null || true
  sleep 1

  cd "$ROOT_DIR"
  if [ "$rem" -gt 0 ]; then
    nohup bash -lc '
      END_EPOCH_UTC='"$rem"' ; START_NOW=$(date -u +%s) ; STOP_AT=$((START_NOW + END_EPOCH_UTC))
      while [ "$(date -u +%s)" -lt "$STOP_AT" ]; do
        '"$UV_BIN"' run python cfdata.py --log-level INFO build-replay-rolling --hours 24
        DAY_UTC=$(date -u +%F)
        '"$UV_BIN"' run python cfdata.py --log-level INFO validate-replay --day "$DAY_UTC" || true
        sleep 3600
      done
    ' >> "$REBUILD_LOG" 2>&1 < /dev/null &
  else
    nohup bash -lc '
      while true; do
        '"$UV_BIN"' run python cfdata.py --log-level INFO build-replay-rolling --hours 24
        DAY_UTC=$(date -u +%F)
        '"$UV_BIN"' run python cfdata.py --log-level INFO validate-replay --day "$DAY_UTC" || true
        sleep 3600
      done
    ' >> "$REBUILD_LOG" 2>&1 < /dev/null &
  fi
  echo $! > "$REBUILD_PID_FILE"
  log "rebuild loop restarted pid=$(cat "$REBUILD_PID_FILE")"
}

check_raw_freshness() {
  local day raw_book now file_mtime age
  day="$(date -u +%F)"
  raw_book="$ROOT_DIR/$RAW_ROOT_REL/$day/market_ws/book.jsonl.gz"
  if [ ! -f "$raw_book" ]; then
    echo "missing"
    return 0
  fi
  now="$(date -u +%s)"
  file_mtime="$(stat -c %Y "$raw_book")"
  age=$((now - file_mtime))
  echo "$age"
}

check_rebuild_log_age() {
  if [ ! -f "$REBUILD_LOG" ]; then
    echo "missing"
    return 0
  fi
  local now file_mtime age
  now="$(date -u +%s)"
  file_mtime="$(stat -c %Y "$REBUILD_LOG")"
  age=$((now - file_mtime))
  echo "$age"
}

ensure_sidecar() {
  if ! pid_running "$SIDECAR_PID_FILE"; then
    log "sidecar pid missing/dead"
    restart_sidecar
    return 0
  fi

  local raw_age
  raw_age="$(check_raw_freshness)"
  if [ "$raw_age" = "missing" ]; then
    log "raw book file missing; restarting sidecar"
    restart_sidecar
    return 0
  fi
  if [ "$raw_age" -gt "$MAX_RAW_STALE_SEC" ]; then
    log "raw book stale age_sec=$raw_age > $MAX_RAW_STALE_SEC; restarting sidecar"
    restart_sidecar
    return 0
  fi

  log "sidecar healthy pid=$(cat "$SIDECAR_PID_FILE") raw_age_sec=$raw_age"
}

ensure_rebuild() {
  if ! pid_running "$REBUILD_PID_FILE"; then
    log "rebuild pid missing/dead"
    restart_rebuild
    return 0
  fi

  local log_age
  log_age="$(check_rebuild_log_age)"
  if [ "$log_age" = "missing" ]; then
    log "rebuild log missing; restarting rebuild loop"
    restart_rebuild
    return 0
  fi
  if [ "$log_age" -gt "$MAX_REBUILD_LOG_STALE_SEC" ]; then
    log "rebuild log stale age_sec=$log_age > $MAX_REBUILD_LOG_STALE_SEC; restarting rebuild loop"
    restart_rebuild
    return 0
  fi

  log "rebuild healthy pid=$(cat "$REBUILD_PID_FILE") log_age_sec=$log_age"
}

check_disk() {
  cd "$ROOT_DIR"
  if python3 scripts/ops/disk_guard.py \
    --path "$RAW_ROOT_REL" \
    --path "$REPLAY_ROOT_REL" \
    --max-total-gb "$MAX_TOTAL_GB" \
    --min-disk-free-gb "$MIN_DISK_FREE_GB" \
    >> "$HEALTH_LOG" 2>&1; then
    log "disk guard passed"
  else
    log "disk guard failed"
  fi
}

main() {
  if [ ! -x "$UV_BIN" ]; then
    log "uv missing at $UV_BIN"
    exit 2
  fi
  if [ ! -d "$ROOT_DIR" ]; then
    log "root dir missing: $ROOT_DIR"
    exit 2
  fi

  ensure_sidecar
  ensure_rebuild
  check_disk
}

main "$@"
