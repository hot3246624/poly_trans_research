#!/usr/bin/env python3
"""Prepare BTC core ex-ante controller and leakage audit packet.

This packet turns the strongest Backtest V1 lane into a field-level contract:
which fields a future controller may consume, which are simulator state, and
which are forbidden outcome labels. It is review-only and does not execute OOS.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
BT_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
OUT = ROOT / "data" / "exports" / "btc_core_exante_controller_leakage_audit_packet_20260605"

SHORTLIST_PACKET = (
    ROOT
    / "data"
    / "exports"
    / "xuan_backtest_v1_strategy_shortlist_20260605"
    / "XUAN_BACKTEST_V1_STRATEGY_SHORTLIST_PACKET.json"
)
BTC_SM_DIR = BT_ROOT / "derived" / "contract_examples" / "btc_completion_state_machine_from_l1_flow_taker_normalized_v1"
BTC_SM_MANIFEST = BTC_SM_DIR / "RESULT_SUMMARY_MANIFEST.json"
BTC_CANDIDATE_REGISTRY = BTC_SM_DIR / "candidate_registry.csv"
BTC_ACTIONS = BTC_SM_DIR / "actions.csv"
BTC_SUMMARY_BY_DAY = BTC_SM_DIR / "summary_by_day.csv"
BTC_COMPLIANCE = BTC_SM_DIR / "COMPLIANCE_MANIFEST.json"
BTC_CANDIDATE_BASE = (
    BT_ROOT
    / "derived"
    / "contract_examples"
    / "btc_completion_candidate_base_from_l1_flow_taker_normalized_v1"
    / "CANDIDATE_BASE_MANIFEST.json"
)

STATUS = "KEEP_BTC_CORE_EXANTE_CONTROLLER_LEAKAGE_AUDIT_PACKET_PREPARED_REVIEW_ONLY_NOT_OOS_READY"

FIELD_CLASS = {
    "REVIEW_METADATA": {
        "candidate_id",
        "action_id",
        "config_name",
        "candidate_row_id",
        "source_label",
        "day",
        "condition_id",
        "slug",
        "ts_iso",
    },
    "EX_ANTE_CONTROLLER_INPUT": {
        "ts_ms",
        "offset_s",
        "side",
        "opposite_side",
        "side_alignment",
        "candidate_reason",
        "public_trade_price",
        "public_trade_size",
        "l1_pair_ask",
        "edge",
        "seed_px",
        "seed_qty",
        "seed_cost",
        "fee_model",
        "official_taker_fee",
        "fee",
    },
    "SIMULATED_STATE_AFTER_ACTION": {
        "pair_qty_after_seed",
        "pair_actions_after_seed",
        "pair_cost_wavg_after_seed",
        "inventory_yes_qty_after",
        "inventory_no_qty_after",
        "inventory_yes_cost_after",
        "inventory_no_cost_after",
        "blocked_by",
    },
    "RESEARCH_COVERAGE_AUDIT": {
        "strict_cache_day_covered",
        "public_audit_day_covered",
        "public_audit_nearby_fill_count",
        "deployable",
        "decision_scope",
    },
    "OUTCOME_FORBIDDEN": {
        "winner_side",
    },
}

PROPOSED_CONTROLLER_INPUTS = sorted(FIELD_CLASS["EX_ANTE_CONTROLLER_INPUT"])
FORBIDDEN_FIELDS = sorted(FIELD_CLASS["OUTCOME_FORBIDDEN"])


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def fnum(value: str | None) -> float:
    if value in (None, ""):
        return 0.0
    return float(value)


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


def field_contract(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    columns = list(rows[0].keys()) if rows else []
    class_by_field = {}
    for class_name, fields in FIELD_CLASS.items():
        for field in fields:
            class_by_field[field] = class_name
    out = []
    for field in columns:
        class_name = class_by_field.get(field, "UNCLASSIFIED_REVIEW_REQUIRED")
        out.append(
            {
                "field": field,
                "field_class": class_name,
                "controller_may_consume": str(class_name == "EX_ANTE_CONTROLLER_INPUT").lower(),
                "must_not_consume_for_controller": str(class_name in {"OUTCOME_FORBIDDEN", "UNCLASSIFIED_REVIEW_REQUIRED"}).lower(),
                "notes": (
                    "winner/outcome label; forbidden for ex-ante controller"
                    if class_name == "OUTCOME_FORBIDDEN"
                    else "allowed pre-action/search-safe input"
                    if class_name == "EX_ANTE_CONTROLLER_INPUT"
                    else "audit or simulator metadata, not a direct external signal"
                ),
            }
        )
    return out


def bucket_seed_px(value: float) -> str:
    bins = [
        (0.0, 0.10, "00_10"),
        (0.10, 0.20, "10_20"),
        (0.20, 0.35, "20_35"),
        (0.35, 0.50, "35_50"),
        (0.50, 0.65, "50_65"),
        (0.65, 0.80, "65_80"),
        (0.80, 0.90, "80_90"),
        (0.90, 1.01, "90_100"),
    ]
    for lo, hi, label in bins:
        if lo <= value < hi:
            return label
    return "OUT_OF_RANGE"


def bucket_offset(value: float) -> str:
    if value < 30:
        return "000_030s"
    if value < 60:
        return "030_060s"
    if value < 90:
        return "060_090s"
    if value <= 120:
        return "090_120s"
    return "GT_120s"


def exante_bucket_summary(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str, str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "action_count": 0,
            "seed_cost": 0.0,
            "official_taker_fee": 0.0,
            "seed_qty": 0.0,
            "pair_action_count_after_seed_sum": 0.0,
        }
    )
    for row in rows:
        key = (
            row.get("side_alignment", ""),
            row.get("side", ""),
            bucket_seed_px(fnum(row.get("seed_px"))),
            bucket_offset(fnum(row.get("offset_s"))),
        )
        item = buckets[key]
        item["action_count"] += 1
        item["seed_cost"] += fnum(row.get("seed_cost"))
        item["official_taker_fee"] += fnum(row.get("official_taker_fee"))
        item["seed_qty"] += fnum(row.get("seed_qty"))
        item["pair_action_count_after_seed_sum"] += fnum(row.get("pair_actions_after_seed"))
    out = []
    for (alignment, side, seed_px_bucket, offset_bucket), values in sorted(
        buckets.items(), key=lambda kv: (-kv[1]["action_count"], kv[0])
    ):
        out.append(
            {
                "side_alignment": alignment,
                "side": side,
                "seed_px_bucket": seed_px_bucket,
                "offset_bucket": offset_bucket,
                "action_count": values["action_count"],
                "seed_cost": round(values["seed_cost"], 6),
                "official_taker_fee": round(values["official_taker_fee"], 6),
                "seed_qty": round(values["seed_qty"], 6),
                "pair_action_count_after_seed_sum": round(values["pair_action_count_after_seed_sum"], 6),
            }
        )
    return out


def decision_probe(rows: list[dict[str, str]]) -> dict[str, Any]:
    blocked = Counter(row.get("blocked_by", "") for row in rows)
    scope = Counter(row.get("decision_scope", "") for row in rows)
    deployable = Counter(row.get("deployable", "") for row in rows)
    strict_covered = sum(1 for row in rows if row.get("strict_cache_day_covered") == "true")
    public_covered = sum(1 for row in rows if row.get("public_audit_day_covered") == "true")
    forbidden_present = [field for field in FORBIDDEN_FIELDS if field in rows[0]]
    forbidden_in_inputs = sorted(set(PROPOSED_CONTROLLER_INPUTS) & set(FORBIDDEN_FIELDS))
    return {
        "action_count": len(rows),
        "condition_count": len({row["condition_id"] for row in rows}),
        "day_count": len({row["day"] for row in rows}),
        "blocked_by_counts": dict(blocked),
        "decision_scope_counts": dict(scope),
        "deployable_value_counts": dict(deployable),
        "strict_cache_day_covered_count": strict_covered,
        "public_audit_day_covered_count": public_covered,
        "forbidden_fields_present_in_source": forbidden_present,
        "forbidden_fields_in_proposed_controller_inputs": forbidden_in_inputs,
        "leakage_contract_passed": not forbidden_in_inputs,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = read_csv(BTC_CANDIDATE_REGISTRY)
    manifest = read_json(BTC_SM_MANIFEST)
    shortlist = read_json(SHORTLIST_PACKET)
    probe = decision_probe(rows)
    fields = field_contract(rows)
    buckets = exante_bucket_summary(rows)

    field_csv = OUT / "btc_core_controller_field_contract.csv"
    bucket_csv = OUT / "btc_core_exante_bucket_summary.csv"
    write_csv(field_csv, fields)
    write_csv(bucket_csv, buckets)

    forbidden_csv = OUT / "btc_core_forbidden_outcome_fields.csv"
    write_csv(
        forbidden_csv,
        [
            {
                "field": field,
                "reason": "outcome/future label; present in source for audit but forbidden for controller input",
                "controller_may_consume": "false",
            }
            for field in FORBIDDEN_FIELDS
        ],
    )

    packet = {
        "schema_version": 1,
        "status": STATUS,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": "BTC_CORE_COMPLETION_V1 ex-ante controller/leakage audit; review-only",
        "shortlist_anchor": {
            "path": str(SHORTLIST_PACKET),
            "sha256": sha256_file(SHORTLIST_PACKET),
            "primary_lane": shortlist["decision"]["primary_lane"],
        },
        "source_anchors": {
            "btc_state_machine_manifest": {
                "path": str(BTC_SM_MANIFEST),
                "sha256": sha256_file(BTC_SM_MANIFEST),
                "status": manifest["status"],
            },
            "btc_candidate_registry": {
                "path": str(BTC_CANDIDATE_REGISTRY),
                "sha256": sha256_file(BTC_CANDIDATE_REGISTRY),
                "row_count": len(rows),
            },
            "btc_actions": {"path": str(BTC_ACTIONS), "sha256": sha256_file(BTC_ACTIONS)},
            "btc_summary_by_day": {"path": str(BTC_SUMMARY_BY_DAY), "sha256": sha256_file(BTC_SUMMARY_BY_DAY)},
            "btc_compliance": {"path": str(BTC_COMPLIANCE), "sha256": sha256_file(BTC_COMPLIANCE)},
            "btc_candidate_base_manifest": {"path": str(BTC_CANDIDATE_BASE), "sha256": sha256_file(BTC_CANDIDATE_BASE)},
        },
        "core_metrics": manifest["core_metrics"],
        "field_contract": {
            "proposed_controller_inputs": PROPOSED_CONTROLLER_INPUTS,
            "forbidden_outcome_fields": FORBIDDEN_FIELDS,
            "field_contract_csv": str(field_csv),
            "forbidden_outcome_fields_csv": str(forbidden_csv),
        },
        "decision_probe": probe,
        "exante_bucket_summary_csv": str(bucket_csv),
        "acceptance_gates_for_next_step": [
            "future replay runner consumes only EX_ANTE_CONTROLLER_INPUT plus required metadata keys",
            "winner_side and any settlement/outcome/pnl labels are blocked at load time",
            "candidate action replay reproduces action_id sequence or reports exact drift",
            "all readiness flags remain false until owner/private truth exists",
        ],
        "next_packet": "BTC_CORE_COMPLETION_V1_LOCAL_REPLAY_RUNNER_SPEC_REVIEW_ONLY",
        "highest_allowed_status": STATUS,
        "non_claims": non_claims(),
    }
    packet_path = OUT / "BTC_CORE_EXANTE_CONTROLLER_LEAKAGE_AUDIT_PACKET.json"
    packet_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    note_path = OUT / "BTC_CORE_EXANTE_CONTROLLER_LEAKAGE_AUDIT_NOTE.md"
    note_path.write_text(
        "\n".join(
            [
                "# BTC Core Ex-Ante Controller Leakage Audit",
                "",
                f"Status: `{STATUS}`",
                "",
                "The source candidate registry contains `winner_side` for audit, but the proposed controller input set excludes it. The next step is a local replay-runner spec that consumes only allowed fields and reproduces historical selected actions before any OOS discussion.",
                "",
                "No OOS, runner, observer, live, canary, private truth, promotion, or deployable claim is authorized.",
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
                "echo 'NOT_AUTHORIZED: BTC core replay runner implementation is not executed by this leakage audit.' >&2",
                "echo 'Next reviewed step: implement/read-only local replay-runner spec over allowed ex-ante fields only.' >&2",
                "exit 66",
                "",
            ]
        ),
        encoding="utf-8",
    )

    artifacts = [
        packet_path,
        note_path,
        field_csv,
        bucket_csv,
        forbidden_csv,
        preview_path,
        SHORTLIST_PACKET,
        BTC_SM_MANIFEST,
        BTC_CANDIDATE_REGISTRY,
        BTC_ACTIONS,
        BTC_SUMMARY_BY_DAY,
        BTC_COMPLIANCE,
        BTC_CANDIDATE_BASE,
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
        "packet_sha256": sha256_file(packet_path),
        "field_contract_csv_sha256": sha256_file(field_csv),
        "bucket_summary_csv_sha256": sha256_file(bucket_csv),
        "non_claims": non_claims(),
    }
    hash_path = OUT / "BTC_CORE_EXANTE_CONTROLLER_LEAKAGE_AUDIT_HASH_MANIFEST.json"
    hash_path.write_text(json.dumps(hash_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "ok": True,
                "status": STATUS,
                "output_dir": str(OUT),
                "packet_sha256": sha256_file(packet_path),
                "manifest_sha256": sha256_file(hash_path),
                "action_count": probe["action_count"],
                "condition_count": probe["condition_count"],
                "leakage_contract_passed": probe["leakage_contract_passed"],
                "forbidden_fields_present_in_source": probe["forbidden_fields_present_in_source"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
