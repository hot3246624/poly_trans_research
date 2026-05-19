#!/usr/bin/env bash
set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-ec2-108-129-167-79.eu-west-1.compute.amazonaws.com}"
REMOTE_USER="${REMOTE_USER:-ubuntu}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/polymarket-Ireland.pem}"
EXPECTED_CLIENT_IP="${EXPECTED_CLIENT_IP:-117.151.235.91}"
REMOTE_ROOT="${REMOTE_ROOT:-/home/ubuntu/poly_trans_research/data/exports/replay_cold_archive_valid_days}"
LOCAL_VOLUME="${LOCAL_VOLUME:-/Volumes/My Passport}"
LOCAL_ROOT="${LOCAL_ROOT:-/Volumes/My Passport/poly_replay_archive/_archives}"

SSH_BASE=(
  ssh -F /dev/null
  -i "$SSH_KEY"
  -o BatchMode=yes
  -o StrictHostKeyChecking=accept-new
  -o ServerAliveInterval=30
  -o ServerAliveCountMax=10
)

remote="${REMOTE_USER}@${REMOTE_HOST}"

if ! mount | grep -F " on $LOCAL_VOLUME " >/dev/null; then
  echo "Refusing to download: local volume is not mounted at $LOCAL_VOLUME" >&2
  exit 4
fi

mkdir -p "$LOCAL_ROOT"

client_ip="$("${SSH_BASE[@]}" "$remote" 'printf "%s\n" "${SSH_CONNECTION%% *}"')"
if [[ "$client_ip" != "$EXPECTED_CLIENT_IP" ]]; then
  echo "Refusing to download: remote sees client IP $client_ip, expected $EXPECTED_CLIENT_IP" >&2
  exit 2
fi

"${SSH_BASE[@]}" "$remote" "bash -s" <<'REMOTE'
set -euo pipefail
REMOTE_ROOT="${REMOTE_ROOT:-/home/ubuntu/poly_trans_research/data/exports/replay_cold_archive_valid_days}"
block_re='2026-05-14|2026-05-15|2026-05-19'
find "$REMOTE_ROOT" -mindepth 2 -maxdepth 2 -name .complete -printf '%h\n' \
  | sort \
  | grep -Ev "$block_re" \
  | while read -r day_dir; do
      day="$(basename "$day_dir")"
      echo "remote_verify $day"
      test -f "$day_dir/crypto_5m.sqlite.zst"
      test -f "$day_dir/MANIFEST.tsv"
      test -f "$day_dir/SHA256SUMS"
      (cd "$day_dir" && sha256sum -c SHA256SUMS)
      zstd -tq --long=31 "$day_dir/crypto_5m.sqlite.zst"
    done
REMOTE

rsync_rsh=(
  ssh -F /dev/null
  -i "$SSH_KEY"
  -o BatchMode=yes
  -o StrictHostKeyChecking=accept-new
  -o ServerAliveInterval=30
  -o ServerAliveCountMax=10
)

rsync -avP --partial --append-verify \
  -e "${rsync_rsh[*]}" \
  --include='*/' \
  --include='crypto_5m.sqlite.zst' \
  --include='MANIFEST.tsv' \
  --include='SHA256SUMS' \
  --include='.complete' \
  --exclude='*' \
  "$remote:$REMOTE_ROOT/" \
  "$LOCAL_ROOT/"

find "$LOCAL_ROOT" -mindepth 1 -maxdepth 1 -type d | sort | while read -r day_dir; do
  day="$(basename "$day_dir")"
  case "$day" in
    2026-05-14|2026-05-15|2026-05-19)
      echo "Refusing blocklisted local day: $day" >&2
      exit 3
      ;;
  esac
  [[ -f "$day_dir/.complete" ]] || continue
  echo "local_verify $day"
  (cd "$day_dir" && shasum -a 256 -c SHA256SUMS)
  zstd -tq --long=31 "$day_dir/crypto_5m.sqlite.zst"
done

du -sh "$LOCAL_ROOT"
