#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

ENV_FILE="${1:-config/research.env}"

python3 cfdata.py --log-level INFO capture-sidecar-env --env-file "$ENV_FILE" --duration-sec 3600

DAY_UTC="$(date -u +%F)"
python3 cfdata.py --log-level INFO build-replay --day "$DAY_UTC"
python3 cfdata.py --log-level INFO audit-startup --day "$DAY_UTC" --output "data/replay/$DAY_UTC/startup_audit.json"
