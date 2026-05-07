#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPS_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DATA_ROOT="${POLYTRANS_DATA_ROOT:-$HOME/poly_trans_research}"
PIDFILE="${POLYTRANS_COMPACT_PIDFILE:-$DATA_ROOT/data/logs/rebuild_compact_20260427_20260430.pid}"
POLL_SEC="${POLYTRANS_WATCH_POLL_SEC:-60}"
OOS_DAYS="${POLYTRANS_OOS_DAYS:-2026-05-02,2026-05-03,2026-05-04,2026-05-05}"
OOS_TAG="${POLYTRANS_OOS_TAG:-20260507_0502_0505}"
OOS_OUTPUT_DIR="${POLYTRANS_OOS_OUTPUT_DIR:-/tmp/taker_buy_finalist_oos_${OOS_TAG}}"
RESTORE_PREVDAY_CRON_LINE="${POLYTRANS_RESTORE_PREVDAY_CRON_LINE:-}"
HOSTNAME_VALUE="$(hostname)"

notify() {
  "$SCRIPT_DIR/discord_notify.sh" "$*"
}

if [ ! -f "$PIDFILE" ]; then
  notify "poly_trans_research: compact rebuild pidfile missing on ${HOSTNAME_VALUE}: ${PIDFILE}"
  exit 3
fi

TARGET_PID="$(<"$PIDFILE")"

while kill -0 "$TARGET_PID" 2>/dev/null; do
  sleep "$POLL_SEC"
done

if [ -n "$RESTORE_PREVDAY_CRON_LINE" ]; then
  ( crontab -l 2>/dev/null | grep -v 'build_previous_day_replay_publish.sh' ; echo "$RESTORE_PREVDAY_CRON_LINE" ) | crontab -
fi

if ! POLYTRANS_REPLAY_ROOT="$DATA_ROOT/data/replay_published" \
  POLYTRANS_OOS_DAYS="$OOS_DAYS" \
  POLYTRANS_OOS_TAG="$OOS_TAG" \
  POLYTRANS_OOS_OUTPUT_DIR="$OOS_OUTPUT_DIR" \
  POLYTRANS_OOS_OVERWRITE=1 \
  "$SCRIPT_DIR/run_taker_buy_finalist_oos.sh"; then
  notify "poly_trans_research: compact rebuild finished on ${HOSTNAME_VALUE}, but finalist OOS failed. output=${OOS_OUTPUT_DIR}"
  exit 4
fi

notify "poly_trans_research: compact rebuild and finalist OOS finished on ${HOSTNAME_VALUE}. output=${OOS_OUTPUT_DIR}"
