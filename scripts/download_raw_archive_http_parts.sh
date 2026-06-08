#!/usr/bin/env bash
set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-ec2-108-129-167-79.eu-west-1.compute.amazonaws.com}"
REMOTE_USER="${REMOTE_USER:-ubuntu}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/polymarket-Ireland.pem}"
REMOTE_ROOT="${REMOTE_ROOT:-/home/ubuntu/poly_trans_research/data/raw_cold_archive/zstd17_long31_valid_days}"
HTTP_ROOT="${HTTP_ROOT:-/home/ubuntu/poly_trans_research/data/exports/http_raw_dl_zstd17_long31_valid_days}"
HTTP_HOST="${HTTP_HOST:-108.129.167.79}"
HTTP_PORT="${HTTP_PORT:-8000}"
LOCAL_VOLUME="${LOCAL_VOLUME:-/Volumes/PolyData}"
LOCAL_ROOT="${LOCAL_ROOT:-/Volumes/PolyData/poly_raw_archive/zstd17_long31_valid_days}"
PARALLEL="${PARALLEL:-16}"
CHUNK_SIZE="${CHUNK_SIZE:-32M}"
SCP_CIPHER="${SCP_CIPHER:-aes128-gcm@openssh.com}"
SSH_BIND_ADDRESS="${SSH_BIND_ADDRESS:-}"
CURL_INTERFACE="${CURL_INTERFACE:-}"
CLEAN_REMOTE_PARTS="${CLEAN_REMOTE_PARTS:-1}"
REMOTE_VERIFY_ZSTD="${REMOTE_VERIFY_ZSTD:-1}"

if [[ $# -lt 1 ]]; then
  echo "usage: $0 YYYY-MM-DD [YYYY-MM-DD ...]" >&2
  exit 64
fi

remote="${REMOTE_USER}@${REMOTE_HOST}"
ssh_base=(
  ssh -F /dev/null
  -i "$SSH_KEY"
  -o BatchMode=yes
  -o StrictHostKeyChecking=accept-new
  -o ConnectTimeout=10
  -o ServerAliveInterval=30
  -o ServerAliveCountMax=10
  -o Compression=no
  -o IPQoS=throughput
  -c "$SCP_CIPHER"
)
if [[ -n "$SSH_BIND_ADDRESS" ]]; then
  ssh_base+=(-b "$SSH_BIND_ADDRESS")
fi

curl_base=(curl --noproxy '*' -4)
if [[ -n "$CURL_INTERFACE" ]]; then
  curl_base+=(--interface "$CURL_INTERFACE")
fi

if ! mount | grep -F " on $LOCAL_VOLUME " >/dev/null; then
  echo "Refusing to download: local volume is not mounted at $LOCAL_VOLUME" >&2
  exit 4
fi

ensure_http_server() {
  "${ssh_base[@]}" "$remote" "bash -s" -- "$HTTP_ROOT" "$HTTP_PORT" <<'REMOTE'
set -euo pipefail
http_root="$1"
http_port="$2"
mkdir -p "$http_root"
log_dir="/home/ubuntu/poly_trans_research/data/logs"
mkdir -p "$log_dir"
pid_file="$log_dir/raw_archive_http_server.pid"
if [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
  exit 0
fi
if ss -ltn "( sport = :$http_port )" | grep -q ":$http_port"; then
  echo "HTTP port $http_port is already in use but pid file is stale/missing" >&2
  exit 20
fi
nohup python3 -m http.server "$http_port" --bind 0.0.0.0 --directory "$http_root" \
  >> "$log_dir/raw_archive_http_server.log" 2>&1 &
echo $! > "$pid_file"
sleep 1
kill -0 "$(cat "$pid_file")"
REMOTE
}

download_day() {
  local day="$1"
  case "$day" in
    2026-05-14|2026-05-15|2026-05-19)
      echo "Refusing blocklisted day: $day" >&2
      return 3
      ;;
  esac

  local remote_dir="$REMOTE_ROOT/$day"
  local local_dir="$LOCAL_ROOT/$day"
  local http_base="http://$HTTP_HOST:$HTTP_PORT/$day"
  mkdir -p "$local_dir/parts"
  rm -f "$local_dir/.complete"

  ensure_http_server

  echo "remote_raw_http_prepare day=$day"
  "${ssh_base[@]}" "$remote" "bash -s" -- "$remote_dir" "$HTTP_ROOT" "$day" "$CHUNK_SIZE" "$REMOTE_VERIFY_ZSTD" <<'REMOTE'
set -euo pipefail
remote_dir="$1"
http_root="$2"
day="$3"
chunk_size="$4"
remote_verify_zstd="$5"
cd "$remote_dir"
test -f .complete
test -f MANIFEST.tsv
test -f SHA256SUMS
sha256sum -c SHA256SUMS
if [[ "$remote_verify_zstd" == "1" ]]; then
  find . -type f -name '*.zst' -print0 | xargs -0 -n1 zstd -tq --long=31
fi
rm -f PARTS_MANIFEST.tsv
mkdir -p .parts
while IFS= read -r rel; do
  part_dir=".parts/${rel}.parts"
  mkdir -p "$part_dir"
  if ! ls "$part_dir"/part_* >/dev/null 2>&1; then
    split -b "$chunk_size" -d -a 4 "$rel" "$part_dir/part_"
  fi
  for part in "$part_dir"/part_*; do
    printf '%s\t%s\t%s\n' "$rel" "${rel}.parts/$(basename "$part")" "$(stat -c %s "$part")" >> PARTS_MANIFEST.tsv
  done
done < <(find . -type f -name '*.zst' -printf '%P\n' | sort)
mkdir -p "$http_root/$day"
ln -sfn "$remote_dir/.parts" "$http_root/$day/parts"
ln -sf "$remote_dir/MANIFEST.tsv" "$http_root/$day/MANIFEST.tsv"
ln -sf "$remote_dir/SHA256SUMS" "$http_root/$day/SHA256SUMS"
ln -sf "$remote_dir/PARTS_MANIFEST.tsv" "$http_root/$day/PARTS_MANIFEST.tsv"
rm -rf "$http_root/$day/files"
mkdir -p "$http_root/$day/files"
awk '{p=$2; sub(/^\*/, "", p); sub(/^\.\//, "", p); if (p != "" && p !~ /\.zst$/) print p}' SHA256SUMS |
  while IFS= read -r rel; do
    mkdir -p "$http_root/$day/files/$(dirname "$rel")"
    ln -sf "$remote_dir/$rel" "$http_root/$day/files/$rel"
  done
REMOTE

  "${curl_base[@]}" -fsSL --connect-timeout 10 --retry 5 --retry-delay 2 \
    "$http_base/MANIFEST.tsv" -o "$local_dir/MANIFEST.tsv"
  "${curl_base[@]}" -fsSL --connect-timeout 10 --retry 5 --retry-delay 2 \
    "$http_base/SHA256SUMS" -o "$local_dir/SHA256SUMS"
  "${curl_base[@]}" -fsSL --connect-timeout 10 --retry 5 --retry-delay 2 \
    "$http_base/PARTS_MANIFEST.tsv" -o "$local_dir/PARTS_MANIFEST.tsv"
  awk '{p=$2; sub(/^\*/, "", p); sub(/^\.\//, "", p); if (p != "" && p !~ /\.zst$/) print p}' "$local_dir/SHA256SUMS" |
    while IFS= read -r rel; do
      mkdir -p "$local_dir/$(dirname "$rel")"
      "${curl_base[@]}" -fsSL --connect-timeout 10 --retry 5 --retry-delay 2 \
        "$http_base/files/$rel" -o "$local_dir/$rel"
    done

  awk -F '\t' '{print $2}' "$local_dir/PARTS_MANIFEST.tsv" > "$local_dir/parts.list"
  local first_part
  first_part="$(sed -n '1p' "$local_dir/parts.list")"
  echo "http_probe day=$day part=$first_part"
  "${curl_base[@]}" -fsSI --connect-timeout 10 --max-time 20 "$http_base/parts/$first_part" | sed -n '1,8p'

  export local_dir http_base CURL_INTERFACE
  local start
  start="$(date +%s)"
  xargs -n1 -P "$PARALLEL" bash -c '
    set -euo pipefail
    part="$1"
    expected="$(awk -F '\''\t'\'' -v p="$part" '\''$2 == p {print $3; exit}'\'' "$local_dir/PARTS_MANIFEST.tsv")"
    if [[ -z "$expected" ]]; then
      echo "missing expected size for $part" >&2
      exit 31
    fi
    dest="$local_dir/parts/$part"
    mkdir -p "$(dirname "$dest")"
    if [[ -f "$dest" ]] && [[ "$(stat -f %z "$dest")" == "$expected" ]]; then
      exit 0
    fi
    rm -f "$dest" "$dest.tmp"
    curl_args=(curl --noproxy "*" -4)
    if [[ -n "${CURL_INTERFACE:-}" ]]; then
      curl_args+=(--interface "$CURL_INTERFACE")
    fi
    curl_args+=(
      -fsSL
      --retry 8 --retry-delay 2 --retry-all-errors
      --connect-timeout 10
      --speed-limit 1024 --speed-time 180
      "$http_base/parts/$part" -o "$dest.tmp"
    )
    "${curl_args[@]}"
    got="$(stat -f %z "$dest.tmp")"
    if [[ "$got" != "$expected" ]]; then
      echo "size mismatch for $part: expected=$expected got=$got" >&2
      exit 32
    fi
    mv "$dest.tmp" "$dest"
  ' _ < "$local_dir/parts.list"
  local end bytes mibps
  end="$(date +%s)"
  bytes="$(find "$local_dir/parts" -type f -name 'part_*' -print0 | xargs -0 stat -f %z | awk '{s+=$1} END{print s+0}')"
  mibps="$(awk -v b="$bytes" -v s="$((end - start))" 'BEGIN{if (s > 0) printf "%.2f", b / 1048576 / s; else printf "0.00"}')"
  echo "raw_http_parts_download_done day=$day bytes=$bytes seconds=$((end - start)) MiBps=$mibps parallel=$PARALLEL"

  while IFS= read -r rel; do
    mkdir -p "$local_dir/$(dirname "$rel")"
    rm -f "$local_dir/$rel" "$local_dir/$rel.tmp"
    awk -F '\t' -v r="$rel" '$1 == r {print $2}' "$local_dir/PARTS_MANIFEST.tsv" |
      while IFS= read -r part; do
        cat "$local_dir/parts/$part" >> "$local_dir/$rel.tmp"
      done
    mv "$local_dir/$rel.tmp" "$local_dir/$rel"
  done < <(awk -F '\t' '{print $1}' "$local_dir/PARTS_MANIFEST.tsv" | sort -u)

  (cd "$local_dir" && shasum -a 256 -c SHA256SUMS)
  find "$local_dir" -type f -name '*.zst' -print0 | xargs -0 -n1 zstd -tq --long=31
  touch "$local_dir/.complete"
  rm -rf "$local_dir/parts" "$local_dir/parts.list" "$local_dir/PARTS_MANIFEST.tsv"
  local archive_bytes
  archive_bytes="$(find "$local_dir" -type f -name '*.zst' -print0 | xargs -0 stat -f %z | awk '{s+=$1} END{print s+0}')"
  echo "local_raw_archive_verified day=$day bytes=$archive_bytes path=$local_dir"

  if [[ "$CLEAN_REMOTE_PARTS" == "1" ]]; then
    "${ssh_base[@]}" "$remote" "rm -rf '$remote_dir/.parts' '$remote_dir/PARTS_MANIFEST.tsv'"
  fi
}

for day in "$@"; do
  download_day "$day"
done
