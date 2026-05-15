#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${POLYTRANS_ROOT_DIR:-$HOME/poly_trans_research}"
DATA_DIR="$ROOT_DIR/data"
BLOCKLIST_PATH="${POLYTRANS_RESEARCH_ARTIFACT_BLOCKLIST:-$DATA_DIR/research_artifacts_blocklist.txt}"
DRY_RUN="${POLYTRANS_CLEANUP_DRY_RUN:-0}"
TODAY_UTC="$(date -u +%F)"
MANIFEST_DIR="$DATA_DIR/deleted_manifests"

[ -f "$BLOCKLIST_PATH" ] || exit 0
mkdir -p "$MANIFEST_DIR"

normalize_day() {
  local item="$1"
  if [[ "$item" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
    printf '%s\n' "$item"
  elif [[ "$item" =~ ^[0-9]{8}$ ]]; then
    printf '%s-%s-%s\n' "${item:0:4}" "${item:4:2}" "${item:6:2}"
  else
    return 1
  fi
}

run_rm() {
  local path="$1"
  [ -e "$path" ] || return 0
  if [ "$DRY_RUN" = "1" ]; then
    echo "dry_run delete $path"
  else
    rm -rf "$path"
    echo "deleted $path"
  fi
}

mapfile -t days < <(
  while IFS= read -r raw; do
    line="${raw%%#*}"
    line="$(echo "$line" | xargs)"
    [ -n "$line" ] || continue
    normalize_day "$line" || true
  done < "$BLOCKLIST_PATH" | sort -u
)

for day in "${days[@]}"; do
  [ -n "$day" ] || continue
  if [[ "$day" > "$TODAY_UTC" || "$day" == "$TODAY_UTC" ]]; then
    echo "cleanup_blocked_research_days: skip active/future blocked day $day"
    continue
  fi

  label="${day//-/}"
  manifest="$MANIFEST_DIR/blocked_${label}_cleanup_$(date -u +%Y%m%dT%H%M%SZ).txt"
  {
    echo "blocked_day=$day"
    echo "label=$label"
    echo "dry_run=$DRY_RUN"
    echo "reason=day is present in $BLOCKLIST_PATH"
    echo "started_at_utc=$(date -u +%FT%TZ)"
  } > "$manifest"

  run_rm "$DATA_DIR/raw/$day" | tee -a "$manifest"
  run_rm "$DATA_DIR/replay/$day" | tee -a "$manifest"
  run_rm "$DATA_DIR/replay_published/$day" | tee -a "$manifest"
  run_rm "$DATA_DIR/quarantine/degraded_artifacts/$label" | tee -a "$manifest"

  shopt -s nullglob
  for path in "$DATA_DIR/backtest_cache"/*/"$label" "$DATA_DIR/verification_store"/*/"$label"; do
    run_rm "$path" | tee -a "$manifest"
  done
  shopt -u nullglob

  echo "finished_at_utc=$(date -u +%FT%TZ)" >> "$manifest"
done
