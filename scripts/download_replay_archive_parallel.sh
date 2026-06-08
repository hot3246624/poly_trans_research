#!/usr/bin/env bash
set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-ec2-108-129-167-79.eu-west-1.compute.amazonaws.com}"
REMOTE_USER="${REMOTE_USER:-ubuntu}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/polymarket-Ireland.pem}"
EXPECTED_CLIENT_IP="${EXPECTED_CLIENT_IP:-117.151.235.91}"
REMOTE_ROOT="${REMOTE_ROOT:-/home/ubuntu/poly_trans_research/data/exports/replay_cold_archive_valid_days}"
LOCAL_VOLUME="${LOCAL_VOLUME:-/Volumes/PolyData}"
LOCAL_ROOT="${LOCAL_ROOT:-/Volumes/PolyData/poly_replay_archive/_archives}"
PARALLEL="${PARALLEL:-8}"
CHUNK_SIZE="${CHUNK_SIZE:-32M}"
SCP_CIPHER="${SCP_CIPHER:-aes128-gcm@openssh.com}"

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
scp_base=(
  scp -O -q -p
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

if ! mount | grep -F " on $LOCAL_VOLUME " >/dev/null; then
  echo "Refusing to download: local volume is not mounted at $LOCAL_VOLUME" >&2
  exit 4
fi
mkdir -p "$LOCAL_ROOT"

client_ip="$("${ssh_base[@]}" "$remote" 'printf "%s\n" "${SSH_CONNECTION%% *}"')"
if [[ "$client_ip" != "$EXPECTED_CLIENT_IP" ]]; then
  echo "Refusing to download: remote sees client IP $client_ip, expected $EXPECTED_CLIENT_IP" >&2
  exit 2
fi
echo "direct_ip_ok $client_ip"

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
  mkdir -p "$local_dir/parts"
  rm -f "$local_dir/.complete" "$local_dir/parts/"*.tmp

  echo "remote_prepare $day"
  "${ssh_base[@]}" "$remote" "bash -s" -- "$remote_dir" "$CHUNK_SIZE" <<'REMOTE'
set -euo pipefail
remote_dir="$1"
chunk_size="$2"
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
cd .parts
for f in part_*; do
  stat -c '%n %s' "$f"
done
REMOTE

  "${ssh_base[@]}" "$remote" "cd '$remote_dir/.parts' && for f in part_*; do stat -c '%n %s' \"\$f\"; done" \
    > "$local_dir/remote_parts.tsv"
  awk '{print $1}' "$local_dir/remote_parts.tsv" > "$local_dir/parts.list"
  "${scp_base[@]}" "$remote:$remote_dir/MANIFEST.tsv" "$local_dir/MANIFEST.tsv"
  "${scp_base[@]}" "$remote:$remote_dir/SHA256SUMS" "$local_dir/SHA256SUMS"

  export SSH_KEY REMOTE_HOST REMOTE_USER SCP_CIPHER remote_dir local_dir
  local start
  start="$(date +%s)"
  xargs -n1 -P "$PARALLEL" bash -c '
    set -euo pipefail
    part="$1"
    expected="$(grep -E "^${part}[[:space:]]" "$local_dir/remote_parts.tsv" | awk "{print \$2}")"
    if [[ -z "$expected" ]]; then
      echo "missing expected size for $part" >&2
      exit 31
    fi
    dest="$local_dir/parts/$part"
    if [[ -f "$dest" ]] && [[ "$(stat -f %z "$dest")" == "$expected" ]]; then
      exit 0
    fi
    rm -f "$dest" "$dest.tmp"
    scp -O -q -p \
      -i "$SSH_KEY" \
      -o BatchMode=yes \
      -o StrictHostKeyChecking=accept-new \
      -o ConnectTimeout=10 \
      -o ServerAliveInterval=30 \
      -o ServerAliveCountMax=10 \
      -o Compression=no \
      -o IPQoS=throughput \
      -c "$SCP_CIPHER" \
      "${REMOTE_USER}@${REMOTE_HOST}:${remote_dir}/.parts/${part}" "$dest.tmp"
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
  echo "parallel_download_done day=$day bytes=$bytes seconds=$((end - start)) MiBps=$mibps parallel=$PARALLEL"

  rm -f "$local_dir/crypto_5m.sqlite.zst" "$local_dir/crypto_5m.sqlite.zst.tmp"
  while IFS= read -r part; do
    cat "$local_dir/parts/$part" >> "$local_dir/crypto_5m.sqlite.zst.tmp"
  done < "$local_dir/parts.list"
  mv "$local_dir/crypto_5m.sqlite.zst.tmp" "$local_dir/crypto_5m.sqlite.zst"

  (cd "$local_dir" && shasum -a 256 -c SHA256SUMS)
  zstd -tq --long=31 "$local_dir/crypto_5m.sqlite.zst"
  touch "$local_dir/.complete"
  rm -rf "$local_dir/parts" "$local_dir/parts.list" "$local_dir/remote_parts.tsv"
  "${ssh_base[@]}" "$remote" "rm -rf '$remote_dir/.parts'"
  stat -f 'local_archive_bytes=%z path=%N' "$local_dir/crypto_5m.sqlite.zst"
}

for day in "$@"; do
  download_day "$day"
done

du -sh "$LOCAL_ROOT"
