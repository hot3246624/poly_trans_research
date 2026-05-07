#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_ROOT="${POLYTRANS_DATA_ROOT:-$HOME/poly_trans_research}"
PIDFILE="${POLYTRANS_COMPACT_PIDFILE:-$DATA_ROOT/data/logs/rebuild_compact_20260428_20260430.pid}"
LOGFILE="${POLYTRANS_MONITOR_LOGFILE:-$DATA_ROOT/data/logs/rebuild_compact_20260428_20260430.log}"
STATE_DIR="${POLYTRANS_MONITOR_STATE_DIR:-$DATA_ROOT/data/logs/.monitor}"
STATE_KEY="${POLYTRANS_MONITOR_STATE_KEY:-compact_rebuild_health}"
STALL_SEC="${POLYTRANS_MONITOR_STALL_SEC:-900}"
MIN_FREE_GB="${POLYTRANS_MONITOR_MIN_FREE_GB:-120}"
EXPECTED_DAY_REGEX="${POLYTRANS_MONITOR_EXPECTED_DAY_REGEX:-}"

HOSTNAME_VALUE="$(hostname)"
STATE_PREFIX="$STATE_DIR/$STATE_KEY"
STALL_MARKER="${STATE_PREFIX}.stall"
DISK_MARKER="${STATE_PREFIX}.disk"
DAY_MARKER="${STATE_PREFIX}.day"
LOCK_FILE="/tmp/polytrans_monitor_compact_rebuild_health.lock"

mkdir -p "$STATE_DIR"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  exit 0
fi

notify() {
  "$SCRIPT_DIR/discord_notify.sh" "$*"
}

marker_set() {
  [ -f "$1" ]
}

marker_on() {
  : >"$1"
}

marker_off() {
  rm -f "$1"
}

latest_progress_line() {
  if [ ! -f "$LOGFILE" ]; then
    return 0
  fi
  grep 'replay build progress:' "$LOGFILE" | tail -n 1 || true
}

if [ ! -f "$PIDFILE" ]; then
  exit 0
fi

TARGET_PID="$(tr -d '[:space:]' <"$PIDFILE")"
if [ -z "$TARGET_PID" ]; then
  rm -f "$PIDFILE"
  marker_off "$STALL_MARKER"
  marker_off "$DISK_MARKER"
  marker_off "$DAY_MARKER"
  exit 0
fi

if ! kill -0 "$TARGET_PID" 2>/dev/null; then
  rm -f "$PIDFILE"
  marker_off "$STALL_MARKER"
  marker_off "$DISK_MARKER"
  marker_off "$DAY_MARKER"
  exit 0
fi

if [ -f "$LOGFILE" ]; then
  now_epoch="$(date -u +%s)"
  log_mtime="$(stat -c %Y "$LOGFILE")"
  log_age_sec=$((now_epoch - log_mtime))
  if [ "$log_age_sec" -gt "$STALL_SEC" ]; then
    if ! marker_set "$STALL_MARKER"; then
      progress="$(latest_progress_line)"
      notify "poly_trans_research: compact rebuild stalled on ${HOSTNAME_VALUE}. pid=${TARGET_PID} log_age_sec=${log_age_sec} logfile=${LOGFILE} ${progress}"
      marker_on "$STALL_MARKER"
    fi
  else
    marker_off "$STALL_MARKER"
  fi
fi

free_gb="$(df -BG "$DATA_ROOT" | awk 'END {gsub(/G/, "", $4); print $4}')"
if [ "$free_gb" -lt "$MIN_FREE_GB" ]; then
  if ! marker_set "$DISK_MARKER"; then
    notify "poly_trans_research: compact rebuild low disk on ${HOSTNAME_VALUE}. free_gb=${free_gb} threshold_gb=${MIN_FREE_GB} path=${DATA_ROOT}"
    marker_on "$DISK_MARKER"
  fi
else
  marker_off "$DISK_MARKER"
fi

if [ -n "$EXPECTED_DAY_REGEX" ]; then
  current_days="$(
    ps -eo command \
      | sed -n 's/.*cfdata.py --log-level INFO build-replay --day \([0-9-]\{10\}\).*/\1/p' \
      | sort -u
  )"
  unexpected_days="$(
    printf '%s\n' "$current_days" | sed '/^$/d' | grep -Ev "$EXPECTED_DAY_REGEX" || true
  )"
  if [ -n "$unexpected_days" ]; then
    if ! marker_set "$DAY_MARKER"; then
      notify "poly_trans_research: compact rebuild unexpected build day on ${HOSTNAME_VALUE}. expected_regex=${EXPECTED_DAY_REGEX} actual=$(printf '%s' "$unexpected_days" | tr '\n' ',')"
      marker_on "$DAY_MARKER"
    fi
  else
    marker_off "$DAY_MARKER"
  fi
fi

