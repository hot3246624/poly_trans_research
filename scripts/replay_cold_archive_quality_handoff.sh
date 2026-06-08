#!/usr/bin/env bash
set -euo pipefail

DATA="${DATA:-/home/ubuntu/poly_trans_research/data}"
OUT="${OUT:-$DATA/exports/replay_cold_archive_valid_days}"
LOG="${LOG:-$DATA/logs/replay_cold_archive_valid_days.log}"
PIDFILE="${PIDFILE:-$DATA/logs/replay_cold_archive_valid_days.pid}"
WAIT_DAY="${WAIT_DAY:-2026-05-18}"
THREADS="${THREADS:-2}"
LONG_WINDOW="${LONG_WINDOW:-31}"

log() {
  printf "[%s] %s\n" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$LOG"
}

archive_day() {
  local d="$1"
  local src="$DATA/replay_published/$d/crypto_5m.sqlite"
  local dir="$OUT/$d"
  local final="$dir/crypto_5m.sqlite.zst"
  local tmp="$final.tmp"
  local free_g src_bytes out_bytes ratio_bp

  mkdir -p "$dir"
  if [ -f "$dir/.complete" ] && [ -f "$final" ]; then
    log "skip_complete day=$d"
    return 0
  fi
  if [ ! -f "$src" ]; then
    log "skip_absent day=$d src=$src"
    return 0
  fi

  free_g=$(df -BG "$DATA" | awk 'NR==2 {gsub(/G/,"",$4); print $4}')
  if [ "${free_g:-0}" -lt 90 ]; then
    log "stop_low_disk day=$d free_g=$free_g"
    exit 3
  fi

  src_bytes=$(stat -c%s "$src")
  log "start_quality day=$d src_bytes=$src_bytes free_g=$free_g threads=$THREADS level=19 long=$LONG_WINDOW"
  rm -f "$tmp" "$dir/.complete"
  printf "day\tsource_path\tsource_bytes\tarchive\n%s\t%s\t%s\t%s\n" "$d" "$src" "$src_bytes" "$final" > "$dir/MANIFEST.tsv"

  if ! zstd -19 "-T$THREADS" "--long=$LONG_WINDOW" -q -c "$src" > "$tmp"; then
    log "quality_failed_retry_t1 day=$d"
    rm -f "$tmp"
    zstd -19 -T1 "--long=$LONG_WINDOW" -q -c "$src" > "$tmp"
  fi

  zstd -tq "--long=$LONG_WINDOW" "$tmp"
  mv "$tmp" "$final"
  (cd "$dir" && sha256sum crypto_5m.sqlite.zst MANIFEST.tsv > SHA256SUMS)
  out_bytes=$(stat -c%s "$final")
  ratio_bp=$(( out_bytes * 10000 / src_bytes ))
  touch "$dir/.complete"
  log "complete day=$d src_bytes=$src_bytes out_bytes=$out_bytes ratio_bp=$ratio_bp"
}

main() {
  local old_pid
  old_pid="$(cat "$PIDFILE" 2>/dev/null || true)"
  log "quality_handoff_wait old_pid=${old_pid:-none} wait_for=$WAIT_DAY threads=$THREADS level=19 long=$LONG_WINDOW"

  if [ -n "${old_pid:-}" ] && kill -0 "$old_pid" 2>/dev/null; then
    while kill -0 "$old_pid" 2>/dev/null; do
      if [ -f "$OUT/$WAIT_DAY/.complete" ]; then
        log "quality_handoff_stop_old old_pid=$old_pid after=$WAIT_DAY"
        pkill -TERM -P "$old_pid" 2>/dev/null || true
        kill -TERM "$old_pid" 2>/dev/null || true
        sleep 3
        pkill -KILL -P "$old_pid" 2>/dev/null || true
        kill -KILL "$old_pid" 2>/dev/null || true
        break
      fi
      sleep 20
    done
  fi

  echo "$$" > "$PIDFILE"
  log "runner_quality_start threads=$THREADS level=19 long=$LONG_WINDOW"
  find "$OUT" -type f -name "*.tmp" -print -delete | while read -r f; do log "remove_tmp path=$f"; done

  local days=("$@")
  if [ "${#days[@]}" -eq 0 ]; then
    days=(
      2026-05-16 2026-05-17 2026-05-18
      2026-05-09 2026-05-10 2026-05-11 2026-05-12 2026-05-13
      2026-05-02 2026-05-03 2026-05-04 2026-05-05 2026-05-06 2026-05-07 2026-05-08
    )
  fi

  local d
  for d in "${days[@]}"; do
    case "$d" in
      2026-05-14|2026-05-15|2026-05-19)
        log "skip_blocklisted day=$d"
        ;;
      *)
        archive_day "$d"
        ;;
    esac
  done
  log "runner_quality_done"
}

main "$@"
