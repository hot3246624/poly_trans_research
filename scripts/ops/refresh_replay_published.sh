#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${POLYTRANS_ROOT_DIR:-$HOME/poly_trans_research}"
SRC_DIR="${POLYTRANS_REPLAY_SRC_DIR:-$ROOT_DIR/data/replay}"
PUBLISH_DIR="${POLYTRANS_REPLAY_PUBLISH_DIR:-$ROOT_DIR/data/replay_published}"
HOT_DAYS="${POLYTRANS_REPLAY_PUBLISH_HOT_DAYS:-2}"

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

removed=0
published=0
unchanged=0

shopt -s nullglob

for published_dir in "$PUBLISH_DIR"/20??-??-??; do
  [ -d "$published_dir" ] || continue
  day="$(basename "$published_dir")"
  src_db="$SRC_DIR/$day/crypto_5m.sqlite"
  if is_hot_day "$day" || [ ! -f "$src_db" ]; then
    rm -rf "$published_dir"
    removed=$((removed + 1))
  fi
done

for replay_day_dir in "$SRC_DIR"/20??-??-??; do
  [ -d "$replay_day_dir" ] || continue
  day="$(basename "$replay_day_dir")"
  src_db="$replay_day_dir/crypto_5m.sqlite"
  [ -f "$src_db" ] || continue
  if is_hot_day "$day"; then
    continue
  fi

  dst_day_dir="$PUBLISH_DIR/$day"
  dst_db="$dst_day_dir/crypto_5m.sqlite"
  mkdir -p "$dst_day_dir"

  if [ -f "$dst_db" ] && [ "$src_db" -ef "$dst_db" ]; then
    unchanged=$((unchanged + 1))
    continue
  fi

  rm -f "$dst_db"
  ln "$src_db" "$dst_db"
  published=$((published + 1))
done

printf 'refresh_replay_published: published=%s unchanged=%s removed=%s hot_days=%s\n' \
  "$published" "$unchanged" "$removed" "$HOT_DAYS"
