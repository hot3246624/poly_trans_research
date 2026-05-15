#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${POLYTRANS_ROOT_DIR:-$HOME/poly_trans_research}"
SRC_DIR="${POLYTRANS_REPLAY_SRC_DIR:-$ROOT_DIR/data/replay}"
PUBLISH_DIR="${POLYTRANS_REPLAY_PUBLISH_DIR:-$ROOT_DIR/data/replay_published}"
HOT_DAYS="${POLYTRANS_REPLAY_PUBLISH_HOT_DAYS:-1}"
PUBLISH_BLOCKLIST_PATH="${POLYTRANS_REPLAY_PUBLISH_BLOCKLIST:-$ROOT_DIR/data/research_artifacts_blocklist.txt}"

mkdir -p "$PUBLISH_DIR"

is_hot_day() {
  local day="$1"
  local offset
  for ((offset = 0; offset < HOT_DAYS; offset++)); do
    if [ "$day" = "$(date -u -d "$offset day ago" +%F)" ]; then
      return 0
    fi
  done
  return 1
}

is_blocked_day() {
  local day="$1"
  [ -f "$PUBLISH_BLOCKLIST_PATH" ] || return 1
  python3 - "$PUBLISH_BLOCKLIST_PATH" "$day" "${day//-/}" <<'PY'
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

is_publishable_sqlite() {
  local db="$1"
  [ -f "$db" ] || return 1
  [ ! -e "$db-wal" ] || return 1
  [ ! -e "$db-shm" ] || return 1
  sqlite3 "$db" "select count(*) from sqlite_sequence where name in ('md_book_l1','md_book_l2','md_trades') and seq > 0;" 2>/dev/null |
    grep -qx 3
}

removed=0
published=0
unchanged=0
skipped_not_ready=0

shopt -s nullglob

for published_dir in "$PUBLISH_DIR"/20??-??-??; do
  [ -d "$published_dir" ] || continue
  day="$(basename "$published_dir")"
  src_db="$SRC_DIR/$day/crypto_5m.sqlite"
  if is_blocked_day "$day" || is_hot_day "$day" || ! is_publishable_sqlite "$src_db"; then
    rm -rf "$published_dir"
    removed=$((removed + 1))
  fi
done

for replay_day_dir in "$SRC_DIR"/20??-??-??; do
  [ -d "$replay_day_dir" ] || continue
  day="$(basename "$replay_day_dir")"
  src_db="$replay_day_dir/crypto_5m.sqlite"
  [ -f "$src_db" ] || continue
  if is_blocked_day "$day"; then
    skipped_not_ready=$((skipped_not_ready + 1))
    continue
  fi
  if is_hot_day "$day"; then
    continue
  fi
  if ! is_publishable_sqlite "$src_db"; then
    skipped_not_ready=$((skipped_not_ready + 1))
    continue
  fi

  dst_day_dir="$PUBLISH_DIR/$day"
  dst_db="$dst_day_dir/crypto_5m.sqlite"
  mkdir -p "$dst_day_dir"
  rm -f "$dst_db-wal" "$dst_db-shm"

  if [ -f "$dst_db" ] && [ "$src_db" -ef "$dst_db" ]; then
    unchanged=$((unchanged + 1))
    continue
  fi

  rm -f "$dst_db"
  ln "$src_db" "$dst_db"
  published=$((published + 1))
done

printf 'refresh_replay_published: published=%s unchanged=%s removed=%s skipped_not_ready=%s hot_days=%s\n' \
  "$published" "$unchanged" "$removed" "$skipped_not_ready" "$HOT_DAYS"
