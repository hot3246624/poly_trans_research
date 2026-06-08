#!/usr/bin/env python3
"""Build a sanitized BTC core ex-ante action ledger.

The ledger removes outcome fields from the historical BTC candidate registry and
keeps only metadata plus approved ex-ante controller inputs. It is intended as
the next local replay-verifier input, not as OOS/live authorization.
"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
BT_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
OUT = ROOT / "data" / "exports" / "btc_core_exante_action_ledger_20260605"

LEAKAGE_PACKET = (
    ROOT
    / "data"
    / "exports"
    / "btc_core_exante_controller_leakage_audit_packet_20260605"
    / "BTC_CORE_EXANTE_CONTROLLER_LEAKAGE_AUDIT_PACKET.json"
)
BTC_CANDIDATE_REGISTRY = (
    BT_ROOT
    / "derived"
    / "contract_examples"
    / "btc_completion_state_machine_from_l1_flow_taker_normalized_v1"
    / "candidate_registry.csv"
)
BTC_SM_MANIFEST = BTC_CANDIDATE_REGISTRY.parent / "RESULT_SUMMARY_MANIFEST.json"

STATUS = "KEEP_BTC_CORE_EXANTE_ACTION_LEDGER_PREPARED_REVIEW_ONLY_NOT_OOS_READY"

METADATA_COLUMNS = [
    "candidate_id",
    "action_id",
    "config_name",
    "candidate_row_id",
    "source_label",
    "day",
    "condition_id",
    "slug",
    "ts_iso",
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def non_claims() -> dict[str, bool]:
    return {
        "private_truth_ready": False,
        "strategy_promotion_ready": False,
        "live_ready": False,
        "deployable": False,
        "oos_authorized": False,
        "runner_authorized": False,
        "orders_authorized": False,
        "canary_authorized": False,
    }


def row_hash(row: dict[str, str], columns: list[str]) -> str:
    payload = "\x1f".join(str(row.get(col, "")) for col in columns)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    leakage = read_json(LEAKAGE_PACKET)
    manifest = read_json(BTC_SM_MANIFEST)
    controller_inputs = list(leakage["field_contract"]["proposed_controller_inputs"])
    forbidden = set(leakage["field_contract"]["forbidden_outcome_fields"])
    output_columns = METADATA_COLUMNS + controller_inputs + ["source_row_hash", "exante_row_hash"]
    source_hash_columns = None
    rows_written = 0
    condition_ids: set[str] = set()
    days: set[str] = set()
    forbidden_seen_in_output = sorted(forbidden & set(output_columns))
    if forbidden_seen_in_output:
        raise SystemExit(f"forbidden fields in output schema: {forbidden_seen_in_output}")

    ledger_path = OUT / "btc_core_exante_action_ledger.csv"
    with BTC_CANDIDATE_REGISTRY.open(newline="", encoding="utf-8") as src, ledger_path.open(
        "w", newline="", encoding="utf-8"
    ) as dst:
        reader = csv.DictReader(src)
        if reader.fieldnames is None:
            raise SystemExit("candidate registry has no header")
        source_hash_columns = list(reader.fieldnames)
        missing = [col for col in METADATA_COLUMNS + controller_inputs if col not in reader.fieldnames]
        if missing:
            raise SystemExit(f"missing required source columns: {missing}")
        writer = csv.DictWriter(dst, fieldnames=output_columns)
        writer.writeheader()
        for row in reader:
            condition_ids.add(row["condition_id"])
            days.add(row["day"])
            out = {col: row.get(col, "") for col in METADATA_COLUMNS + controller_inputs}
            out["source_row_hash"] = row_hash(row, source_hash_columns)
            out["exante_row_hash"] = row_hash(out, METADATA_COLUMNS + controller_inputs)
            writer.writerow(out)
            rows_written += 1

    audit = {
        "schema_version": 1,
        "status": STATUS,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_registry": {
            "path": str(BTC_CANDIDATE_REGISTRY),
            "sha256": sha256_file(BTC_CANDIDATE_REGISTRY),
            "source_row_count": rows_written,
        },
        "output_ledger": {
            "path": str(ledger_path),
            "sha256": sha256_file(ledger_path),
            "row_count": rows_written,
            "column_count": len(output_columns),
            "columns": output_columns,
            "condition_count": len(condition_ids),
            "days": sorted(days),
        },
        "leakage_packet": {"path": str(LEAKAGE_PACKET), "sha256": sha256_file(LEAKAGE_PACKET)},
        "btc_state_machine_manifest": {
            "path": str(BTC_SM_MANIFEST),
            "sha256": sha256_file(BTC_SM_MANIFEST),
            "status": manifest["status"],
        },
        "forbidden_fields": sorted(forbidden),
        "forbidden_fields_in_output": forbidden_seen_in_output,
        "leakage_contract_passed": not forbidden_seen_in_output and rows_written == int(leakage["decision_probe"]["action_count"]),
        "next_required_action": "build a local replay verifier that consumes this ledger and reproduces historical action_id sequence without winner/outcome fields",
        "highest_allowed_status": STATUS,
        "non_claims": non_claims(),
    }
    audit_path = OUT / "BTC_CORE_EXANTE_ACTION_LEDGER_AUDIT.json"
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    note_path = OUT / "BTC_CORE_EXANTE_ACTION_LEDGER_NOTE.md"
    note_path.write_text(
        "\n".join(
            [
                "# BTC Core Ex-Ante Action Ledger",
                "",
                f"Status: `{STATUS}`",
                "",
                f"Rows: `{rows_written}`. Conditions: `{len(condition_ids)}`.",
                "",
                "The ledger strips outcome fields such as `winner_side`. It is suitable for local replay-verifier development, not for OOS/live claims.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    preview_path = OUT / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    preview_path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                "echo 'NOT_AUTHORIZED: local replay verifier is not executed by this ledger export.' >&2",
                "exit 66",
                "",
            ]
        ),
        encoding="utf-8",
    )

    artifacts = [
        ledger_path,
        audit_path,
        note_path,
        preview_path,
        LEAKAGE_PACKET,
        BTC_CANDIDATE_REGISTRY,
        BTC_SM_MANIFEST,
        Path(__file__).resolve(),
    ]
    hash_manifest = {
        "schema_version": 1,
        "status": STATUS,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "artifacts": [
            {"path": str(path), "sha256": sha256_file(path), "size_bytes": path.stat().st_size}
            for path in artifacts
            if path.exists()
        ],
        "ledger_sha256": sha256_file(ledger_path),
        "audit_sha256": sha256_file(audit_path),
        "non_claims": non_claims(),
    }
    hash_path = OUT / "BTC_CORE_EXANTE_ACTION_LEDGER_HASH_MANIFEST.json"
    hash_path.write_text(json.dumps(hash_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "ok": True,
                "status": STATUS,
                "output_dir": str(OUT),
                "row_count": rows_written,
                "condition_count": len(condition_ids),
                "ledger_sha256": sha256_file(ledger_path),
                "manifest_sha256": sha256_file(hash_path),
                "leakage_contract_passed": audit["leakage_contract_passed"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
