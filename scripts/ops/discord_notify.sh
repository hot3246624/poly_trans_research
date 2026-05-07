#!/usr/bin/env bash
set -euo pipefail

WEBHOOK_FILE_DEFAULT="$HOME/.config/polytrans/discord_webhook_url"
WEBHOOK_URL="${POLYTRANS_DISCORD_WEBHOOK_URL:-}"

if [ -z "$WEBHOOK_URL" ] && [ -f "$WEBHOOK_FILE_DEFAULT" ]; then
  WEBHOOK_URL="$(<"$WEBHOOK_FILE_DEFAULT")"
fi

if [ -z "$WEBHOOK_URL" ]; then
  echo "discord webhook not configured" >&2
  exit 2
fi

if [ "$#" -gt 0 ]; then
  MESSAGE="$*"
else
  MESSAGE="$(cat)"
fi

if [ -z "$MESSAGE" ]; then
  echo "message is empty" >&2
  exit 2
fi

PAYLOAD="$(
  python3 - "$MESSAGE" <<'PY'
import json
import sys

message = sys.argv[1]
print(json.dumps({"content": message}, ensure_ascii=False))
PY
)"

curl -fsS -H "Content-Type: application/json" -d "$PAYLOAD" "$WEBHOOK_URL" >/dev/null
