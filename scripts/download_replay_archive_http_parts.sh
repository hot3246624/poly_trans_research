#!/usr/bin/env bash
set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-ec2-108-129-167-79.eu-west-1.compute.amazonaws.com}"
REMOTE_USER="${REMOTE_USER:-ubuntu}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/polymarket-Ireland.pem}"
REMOTE_ROOT="${REMOTE_ROOT:-/home/ubuntu/poly_trans_research/data/exports/replay_cold_archive_valid_days}"
HTTP_ROOT="${HTTP_ROOT:-/home/ubuntu/poly_trans_research/data/exports/http_dl_20260518_8f3c2a91}"
HTTP_HOST="${HTTP_HOST:-108.129.167.79}"
HTTP_PORT="${HTTP_PORT:-8000}"
LOCAL_VOLUME="${LOCAL_VOLUME:-/Volumes/PolyData}"
LOCAL_ROOT="${LOCAL_ROOT:-/Volumes/PolyData/poly_replay_archive/_archives}"
PARALLEL="${PARALLEL:-16}"
CHUNK_SIZE="${CHUNK_SIZE:-32M}"
SCP_CIPHER="${SCP_CIPHER:-aes128-gcm@openssh.com}"
SSH_BIND_ADDRESS="${SSH_BIND_ADDRESS:-}"
CURL_INTERFACE="${CURL_INTERFACE:-}"
RESUME_PARTS="${RESUME_PARTS:-0}"

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

curl_base=(
  curl --noproxy '*' -4
)
if [[ -n "$CURL_INTERFACE" ]]; then
  curl_base+=(--interface "$CURL_INTERFACE")
fi

if ! mount | grep -F " on $LOCAL_VOLUME " >/dev/null; then
  echo "Refusing to download: local volume is not mounted at $LOCAL_VOLUME" >&2
  exit 4
fi

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
  if [[ "$RESUME_PARTS" != "1" ]]; then
    rm -f "$local_dir/parts/"*.tmp
  fi

  echo "remote_http_prepare $day"
  "${ssh_base[@]}" "$remote" "bash -s" -- "$remote_dir" "$HTTP_ROOT" "$day" "$CHUNK_SIZE" <<'REMOTE'
set -euo pipefail
remote_dir="$1"
http_root="$2"
day="$3"
chunk_size="$4"
cd "$remote_dir"
test -f .complete
test -f crypto_5m.sqlite.zst
test -f MANIFEST.tsv
test -f SHA256SUMS
sha256sum -c SHA256SUMS
zstd -tq --long=31 crypto_5m.sqlite.zst
mkdir -p .parts
if ! ls .parts/part_* >/dev/null 2>&1; then
  split -b "$chunk_size" -d -a 4 crypto_5m.sqlite.zst .parts/part_
fi
mkdir -p "$http_root/$day"
ln -sfn "$remote_dir/.parts" "$http_root/$day/parts"
ln -sf "$remote_dir/MANIFEST.tsv" "$http_root/$day/MANIFEST.tsv"
ln -sf "$remote_dir/SHA256SUMS" "$http_root/$day/SHA256SUMS"
cd .parts
for f in part_*; do
  stat -c '%n %s' "$f"
done
REMOTE

  "${ssh_base[@]}" "$remote" "cd '$remote_dir/.parts' && for f in part_*; do stat -c '%n %s' \"\$f\"; done" \
    > "$local_dir/remote_parts.tsv"
  awk '{print $1}' "$local_dir/remote_parts.tsv" > "$local_dir/parts.list"

  "${curl_base[@]}" -fsSL --connect-timeout 10 --retry 5 --retry-delay 2 \
    "$http_base/MANIFEST.tsv" -o "$local_dir/MANIFEST.tsv"
  "${curl_base[@]}" -fsSL --connect-timeout 10 --retry 5 --retry-delay 2 \
    "$http_base/SHA256SUMS" -o "$local_dir/SHA256SUMS"

  echo "http_probe $day"
  "${curl_base[@]}" -fsSI --connect-timeout 10 --max-time 20 "$http_base/parts/part_0000" | sed -n '1,8p'

  export local_dir http_base CURL_INTERFACE RESUME_PARTS
  local start
  start="$(date +%s)"
  xargs -n1 -P "$PARALLEL" bash -c '
    set -euo pipefail
    part="$1"
    expected="$(awk -v p="$part" "\$1 == p {print \$2}" "$local_dir/remote_parts.tsv")"
    if [[ -z "$expected" ]]; then
      echo "missing expected size for $part" >&2
      exit 31
    fi
    dest="$local_dir/parts/$part"
    if [[ -f "$dest" ]] && [[ "$(stat -f %z "$dest")" == "$expected" ]]; then
      exit 0
    fi
    rm -f "$dest"
    resume_args=()
    if [[ "${RESUME_PARTS:-0}" == "1" && -f "$dest.tmp" ]]; then
      tmp_size="$(stat -f %z "$dest.tmp")"
      if (( tmp_size > 0 && tmp_size < expected )); then
        resume_args=(-C -)
      elif [[ "$tmp_size" == "$expected" ]]; then
        mv "$dest.tmp" "$dest"
        exit 0
      else
        rm -f "$dest.tmp"
      fi
    else
      rm -f "$dest.tmp"
    fi
    curl_args=(curl --noproxy "*" -4)
    if [[ -n "${CURL_INTERFACE:-}" ]]; then
      curl_args+=(--interface "$CURL_INTERFACE")
    fi
    curl_args+=(
      -fsSL
      --retry 8 --retry-delay 2 --retry-all-errors
      --connect-timeout 10
      --speed-limit 1024 --speed-time 180
    )
    if ((${#resume_args[@]})); then
      curl_args+=("${resume_args[@]}")
    fi
    curl_args+=("$http_base/parts/$part" -o "$dest.tmp")
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
  echo "http_parts_download_done day=$day bytes=$bytes seconds=$((end - start)) MiBps=$mibps parallel=$PARALLEL"

  rm -f "$local_dir/crypto_5m.sqlite.zst" "$local_dir/crypto_5m.sqlite.zst.tmp"
  while IFS= read -r part; do
    cat "$local_dir/parts/$part" >> "$local_dir/crypto_5m.sqlite.zst.tmp"
  done < "$local_dir/parts.list"
  mv "$local_dir/crypto_5m.sqlite.zst.tmp" "$local_dir/crypto_5m.sqlite.zst"

  (cd "$local_dir" && shasum -a 256 -c SHA256SUMS)
  zstd -tq --long=31 "$local_dir/crypto_5m.sqlite.zst"
  touch "$local_dir/.complete"
  rm -rf "$local_dir/parts" "$local_dir/parts.list" "$local_dir/remote_parts.tsv" "$local_dir/crypto_5m.sqlite.zst.stream"
  stat -f 'local_archive_bytes=%z path=%N' "$local_dir/crypto_5m.sqlite.zst"
}

for day in "$@"; do
  download_day "$day"
done
