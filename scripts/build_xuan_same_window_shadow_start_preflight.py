#!/usr/bin/env python3
"""Build the no-order same-window shadow start preflight packet.

This packet is intentionally not a live/deploy config. It binds the tier-A
same-window research candidates to a dry-run-only runner configuration, records
current local runner conflict checks, and leaves manual approval as the final
start blocker.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any


DEFAULT_DATA_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
DEFAULT_CONTRACT = DEFAULT_DATA_ROOT / "derived/contract_examples"
DEFAULT_DESIGN_PACKET = (
    DEFAULT_CONTRACT
    / "xuan_same_window_shadow_design_packet_latest/XUAN_SAME_WINDOW_SHADOW_DESIGN_PACKET_MANIFEST.json"
)
DEFAULT_TIER_A_CANDIDATES = (
    DEFAULT_CONTRACT / "xuan_same_window_shadow_design_packet_latest/tier_a_shadow_design_candidates.csv"
)
DEFAULT_TIERED_SCORECARD = (
    DEFAULT_CONTRACT
    / "xuan_same_window_handoff_tiered_scorecard_latest/XUAN_SAME_WINDOW_HANDOFF_TIERED_SCORECARD_MANIFEST.json"
)
DEFAULT_SHADOW_READINESS_GATE = (
    DEFAULT_CONTRACT
    / "xuan_same_window_shadow_readiness_gate_latest/XUAN_SAME_WINDOW_SHADOW_READINESS_GATE.json"
)
DEFAULT_START_PREFLIGHT_SPEC = (
    DEFAULT_CONTRACT
    / "xuan_same_window_shadow_start_preflight_spec_latest/XUAN_SAME_WINDOW_SHADOW_START_PREFLIGHT_SPEC.json"
)
DEFAULT_BTC_CANARY_PREFLIGHT = (
    DEFAULT_CONTRACT / "btc_same_window_residual_share_le_3pct_v1_canary_preflight_latest/manifest.json"
)
DEFAULT_OUTPUT_DIR = DEFAULT_CONTRACT / "xuan_same_window_no_order_shadow_start_preflight_latest"

FORBIDDEN_START_FLAGS = {
    "orders_allowed": True,
    "live_orders_allowed": True,
    "redeems_allowed": True,
    "cancels_allowed": True,
    "remote_runner_allowed": True,
    "private_truth_ready": True,
    "strategy_promotion_ready": True,
    "deployable": True,
}
CONFLICT_PROCESS_TOKENS = (
    "run_fastcancel_public_ws_shadow.py",
    "run_fastcancel_live_shadow_observer.py",
    "run_taker_buy_signal_public_ws_shadow.py",
    "xuan_same_window_no_order_shadow_runner",
)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_float(row: dict[str, str], key: str, default: float = 0.0) -> float:
    try:
        return float(row.get(key) or default)
    except ValueError:
        return default


def parse_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def load_candidates(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def deterministic_candidate_id(row: dict[str, str]) -> str:
    material = "|".join(
        [
            "xuan_same_window_no_order_shadow_v1",
            row.get("handoff_rank") or "",
            row.get("asset") or "",
            row.get("day") or "",
            row.get("condition_id") or "",
            row.get("slug") or "",
        ]
    )
    return "shadow_" + sha256_bytes(material.encode("utf-8"))[:24]


def process_conflict_check() -> dict[str, Any]:
    proc = subprocess.run(
        ["ps", "-axo", "pid=,command="],
        text=True,
        capture_output=True,
        check=False,
    )
    conflicts: list[dict[str, str]] = []
    if proc.returncode == 0:
        for raw in proc.stdout.splitlines():
            line = raw.strip()
            if not line:
                continue
            if "build_xuan_same_window_shadow_start_preflight.py" in line:
                continue
            for token in CONFLICT_PROCESS_TOKENS:
                if token in line:
                    pid, _, command = line.partition(" ")
                    conflicts.append({"pid": pid.strip(), "token": token, "command": command.strip()})
                    break
    return {
        "checked_utc": utc_now(),
        "command": "ps -axo pid=,command=",
        "returncode": proc.returncode,
        "passed": proc.returncode == 0 and not conflicts,
        "conflict_count": len(conflicts),
        "conflicts": conflicts,
        "stderr_tail": proc.stderr[-1000:] if proc.stderr else "",
    }


def candidate_summary(rows: list[dict[str, str]]) -> dict[str, Any]:
    assets = sorted({row.get("asset", "") for row in rows if row.get("asset")})
    days = sorted({row.get("day", "") for row in rows if row.get("day")})
    residual_shares = [parse_float(row, "residual_cost_share") for row in rows]
    residual_zero = [parse_float(row, "residual_zero_after_fee_pnl") for row in rows]
    max_pair_asks = [parse_float(row, "max_l1_pair_ask") for row in rows]
    last_offsets = [parse_float(row, "last_offset_s") for row in rows]
    gross_buy_cost = sum(parse_float(row, "gross_buy_cost") for row in rows)
    pair_after_fee = sum(parse_float(row, "pair_after_fee_pnl") for row in rows)
    zero_after_fee = sum(parse_float(row, "residual_zero_after_fee_pnl") for row in rows)
    xuan_after_fee = sum(parse_float(row, "xuan_after_fee_pnl") for row in rows)
    official_fee = sum(parse_float(row, "official_taker_fee") for row in rows)
    by_asset: dict[str, dict[str, Any]] = {}
    for asset in assets:
        asset_rows = [row for row in rows if row.get("asset") == asset]
        by_asset[asset] = {
            "candidate_count": len(asset_rows),
            "day_count": len({row.get("day") for row in asset_rows}),
            "gross_buy_cost": round(sum(parse_float(row, "gross_buy_cost") for row in asset_rows), 6),
            "pair_after_fee_pnl": round(sum(parse_float(row, "pair_after_fee_pnl") for row in asset_rows), 6),
            "residual_zero_after_fee_pnl": round(
                sum(parse_float(row, "residual_zero_after_fee_pnl") for row in asset_rows), 6
            ),
            "max_residual_cost_share": round(max(parse_float(row, "residual_cost_share") for row in asset_rows), 6),
        }
    return {
        "candidate_count": len(rows),
        "asset_count": len(assets),
        "assets": assets,
        "day_count": len(days),
        "days": days,
        "gross_buy_cost": round(gross_buy_cost, 6),
        "pair_after_fee_pnl": round(pair_after_fee, 6),
        "residual_zero_after_fee_pnl": round(zero_after_fee, 6),
        "xuan_after_fee_pnl": round(xuan_after_fee, 6),
        "official_taker_fee": round(official_fee, 6),
        "max_residual_cost_share": round(max(residual_shares) if residual_shares else 0.0, 6),
        "min_residual_zero_after_fee_pnl": round(min(residual_zero) if residual_zero else 0.0, 6),
        "max_l1_pair_ask": round(max(max_pair_asks) if max_pair_asks else 0.0, 6),
        "max_last_offset_s": round(max(last_offsets) if last_offsets else 0.0, 6),
        "by_asset": by_asset,
    }


def build_candidate_binding(rows: list[dict[str, str]], config_fingerprint: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: int(item.get("handoff_rank") or 0)):
        out.append(
            {
                "shadow_candidate_id": deterministic_candidate_id(row),
                "handoff_rank": row.get("handoff_rank"),
                "asset": row.get("asset"),
                "day": row.get("day"),
                "condition_id": row.get("condition_id"),
                "slug": row.get("slug"),
                "research_tier": row.get("research_tier"),
                "gross_buy_cost": row.get("gross_buy_cost"),
                "pair_after_fee_pnl": row.get("pair_after_fee_pnl"),
                "residual_zero_after_fee_pnl": row.get("residual_zero_after_fee_pnl"),
                "residual_cost_share": row.get("residual_cost_share"),
                "max_l1_pair_ask": row.get("max_l1_pair_ask"),
                "last_offset_s": row.get("last_offset_s"),
                "dry_run_only": "true",
                "orders_allowed": "false",
                "live_orders_allowed": "false",
                "config_fingerprint": config_fingerprint,
            }
        )
    return out


def build_checklist(
    config: dict[str, Any],
    rows: list[dict[str, str]],
    process_check: dict[str, Any],
    candidate_hash: str,
) -> list[dict[str, Any]]:
    checks = [
        (
            "candidate_csv_present",
            bool(rows),
            "tier-A candidate CSV loaded",
        ),
        (
            "candidate_csv_fingerprint_present",
            bool(candidate_hash),
            "candidate source sha256 recorded",
        ),
        (
            "dry_run_only_true",
            config.get("dry_run_only") is True,
            "runner config hard-codes dry_run_only=true",
        ),
        (
            "orders_allowed_false",
            config.get("orders_allowed") is False,
            "runner config hard-codes orders_allowed=false",
        ),
        (
            "live_orders_allowed_false",
            config.get("live_orders_allowed") is False,
            "runner config hard-codes live_orders_allowed=false",
        ),
        (
            "remote_runner_allowed_false",
            config.get("remote_runner_allowed") is False,
            "runner config is local-only",
        ),
        (
            "candidate_read_allowed_true",
            config.get("candidate_read_allowed") is True,
            "runner may read candidate packet for shadow observation",
        ),
        (
            "live_import_disabled",
            config.get("live_import_enabled") is False and config.get("candidate_import_allowed") is False,
            "no live/import/deploy path is enabled",
        ),
        (
            "stop_conditions_defined",
            bool(config.get("stop_conditions")),
            "stop conditions are present before approval",
        ),
        (
            "kill_switch_defined",
            bool(config.get("kill_switch")),
            "kill-switch controls are present before approval",
        ),
        (
            "active_runner_conflict_check_passed",
            bool(process_check.get("passed")),
            "current local process scan found no conflicting shadow runner",
        ),
        (
            "manual_approval_required",
            config.get("requires_manual_approval_before_start") is True,
            "start remains blocked until user explicitly approves",
        ),
        (
            "owner_truth_schema_future_only",
            config.get("owner_private_truth_ready") is False,
            "owner truth remains future-only and unavailable before execution",
        ),
    ]
    return [
        {
            "check": name,
            "passed": bool(passed),
            "detail": detail,
        }
        for name, passed, detail in checks
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--design-packet", type=Path, default=DEFAULT_DESIGN_PACKET)
    parser.add_argument("--tier-a-candidates", type=Path, default=DEFAULT_TIER_A_CANDIDATES)
    parser.add_argument("--tiered-scorecard", type=Path, default=DEFAULT_TIERED_SCORECARD)
    parser.add_argument("--shadow-readiness-gate", type=Path, default=DEFAULT_SHADOW_READINESS_GATE)
    parser.add_argument("--start-preflight-spec", type=Path, default=DEFAULT_START_PREFLIGHT_SPEC)
    parser.add_argument("--btc-canary-preflight", type=Path, default=DEFAULT_BTC_CANARY_PREFLIGHT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--per-market-cap-usd", type=float, default=25.0)
    parser.add_argument("--portfolio-cap-usd", type=float, default=1000.0)
    args = parser.parse_args()

    output_dir = args.output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    candidate_csv = args.tier_a_candidates.expanduser()
    rows = load_candidates(candidate_csv)
    summary = candidate_summary(rows)
    process_check = process_conflict_check()
    candidate_hash = sha256_file(candidate_csv) if candidate_csv.exists() else ""

    design_packet = read_json(args.design_packet.expanduser())
    tiered_scorecard = read_json(args.tiered_scorecard.expanduser())
    shadow_readiness_gate = read_json(args.shadow_readiness_gate.expanduser())
    start_preflight_spec = read_json(args.start_preflight_spec.expanduser())
    btc_canary_preflight = read_json(args.btc_canary_preflight.expanduser())

    config = {
        "schema_version": "xuan_same_window_no_order_shadow_runner_config_v1",
        "created_utc": utc_now(),
        "mode": "no_order_shadow_preflight_approval_required",
        "runner_profile_id": "xuan_same_window_no_order_shadow_v1",
        "candidate_source": {
            "csv": str(candidate_csv),
            "sha256": candidate_hash,
            "tier": "tier_a_pair_led_low_residual",
            "candidate_count": summary["candidate_count"],
            "assets": summary["assets"],
            "days": summary["days"],
        },
        "dry_run_only": True,
        "candidate_read_allowed": True,
        "candidate_import_allowed": False,
        "live_import_enabled": False,
        "orders_allowed": False,
        "live_orders_allowed": False,
        "redeems_allowed": False,
        "cancels_allowed": False,
        "remote_runner_allowed": False,
        "start_allowed_without_manual_approval": False,
        "requires_manual_approval_before_start": True,
        "owner_private_truth_ready": False,
        "historical_shadow_or_v1_is_private_truth": False,
        "risk_budget": {
            "per_market_cap_usd": args.per_market_cap_usd,
            "portfolio_cap_usd": args.portfolio_cap_usd,
            "capital_cap_requires_user_approval": True,
            "do_not_size_from_residual_settlement_pnl": True,
            "residual_settlement_pnl_is_strategy_edge": False,
        },
        "research_guards": {
            "require_research_tier": "tier_a_pair_led_low_residual",
            "require_residual_zero_after_fee_positive": True,
            "max_residual_cost_share": 0.05,
            "max_l1_pair_ask": 1.02,
            "max_last_offset_s": 120.0,
            "require_two_sided_market": True,
        },
        "stop_conditions": [
            {
                "name": "data_source_or_market_mapping_mismatch",
                "action": "stop_shadow_and_report",
            },
            {
                "name": "observed_pair_cost_above_research_ceiling",
                "threshold": "max_l1_pair_ask > 1.02 or residual_cost_share > 0.05",
                "action": "stop_shadow_and_report",
            },
            {
                "name": "latency_or_feed_gap",
                "threshold": "source_age/gap exceeds approved runtime threshold",
                "action": "stop_shadow_and_report",
            },
            {
                "name": "any_order_path_enabled",
                "threshold": "orders_allowed/live_orders_allowed/import_enabled becomes true",
                "action": "stop_shadow_and_report",
            },
            {
                "name": "owner_truth_or_promotion_claim_before_execution",
                "threshold": "private_truth_ready or strategy_promotion_ready becomes true before owner reconciliation",
                "action": "stop_shadow_and_report",
            },
        ],
        "kill_switch": {
            "manual_stop_required": True,
            "stop_file_path": str(output_dir / "STOP_REQUESTED"),
            "panic_on_any_order_send_attempt": True,
            "panic_on_live_import_enabled": True,
        },
        "log_plan": {
            "root": str(output_dir / "runtime_logs"),
            "events_jsonl": str(output_dir / "runtime_logs/shadow_events.jsonl"),
            "status_json": str(output_dir / "runtime_logs/shadow_status.json"),
            "orders_sent_initially": False,
        },
        "input_manifests": {
            "design_packet": str(args.design_packet.expanduser()),
            "tiered_scorecard": str(args.tiered_scorecard.expanduser()),
            "shadow_readiness_gate": str(args.shadow_readiness_gate.expanduser()),
            "start_preflight_spec": str(args.start_preflight_spec.expanduser()),
            "btc_canary_preflight": str(args.btc_canary_preflight.expanduser()),
        },
    }
    config_fingerprint = sha256_bytes(json.dumps(config, sort_keys=True).encode("utf-8"))
    config["config_fingerprint"] = config_fingerprint

    candidate_binding = build_candidate_binding(rows, config_fingerprint)
    checklist = build_checklist(config, rows, process_check, candidate_hash)
    validation_errors: list[str] = []
    for key, forbidden_value in FORBIDDEN_START_FLAGS.items():
        if config.get(key) is forbidden_value:
            validation_errors.append(f"{key}_must_not_be_{forbidden_value}")
    if not all(item["passed"] for item in checklist):
        validation_errors.extend([f"check_failed:{item['check']}" for item in checklist if not item["passed"]])
    if summary["max_residual_cost_share"] > 0.05:
        validation_errors.append("max_residual_cost_share_exceeds_0_05")
    if summary["min_residual_zero_after_fee_pnl"] <= 0:
        validation_errors.append("residual_zero_after_fee_not_positive_for_all_candidates")
    if summary["max_l1_pair_ask"] > 1.02:
        validation_errors.append("max_l1_pair_ask_exceeds_1_02")
    if summary["max_last_offset_s"] > 120.0:
        validation_errors.append("max_last_offset_s_exceeds_120")

    engineering_preflight_ready = not validation_errors
    remaining_blockers = ["manual_shadow_start_approval_missing"]
    if validation_errors:
        remaining_blockers.extend(validation_errors)

    config_path = output_dir / "xuan_same_window_no_order_shadow_runner_config.json"
    checklist_json_path = output_dir / "preflight_checklist.json"
    checklist_csv_path = output_dir / "preflight_checklist.csv"
    candidate_binding_path = output_dir / "candidate_binding.csv"
    process_check_path = output_dir / "active_runner_conflict_check.json"
    start_command_path = output_dir / "start_command_preview.txt"

    write_json(config_path, config)
    write_json(checklist_json_path, {"schema_version": "xuan_shadow_start_preflight_checklist_v1", "checks": checklist})
    write_csv(checklist_csv_path, checklist, ["check", "passed", "detail"])
    write_csv(
        candidate_binding_path,
        candidate_binding,
        [
            "shadow_candidate_id",
            "handoff_rank",
            "asset",
            "day",
            "condition_id",
            "slug",
            "research_tier",
            "gross_buy_cost",
            "pair_after_fee_pnl",
            "residual_zero_after_fee_pnl",
            "residual_cost_share",
            "max_l1_pair_ask",
            "last_offset_s",
            "dry_run_only",
            "orders_allowed",
            "live_orders_allowed",
            "config_fingerprint",
        ],
    )
    write_json(process_check_path, process_check)
    start_command_path.write_text(
        "\n".join(
            [
                "# Preview only. Do not run without explicit user approval.",
                "# No order/live/import path is enabled in the generated config.",
                "uv run python scripts/run_xuan_same_window_no_order_shadow.py \\",
                f"  --config {config_path}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    manifest = {
        "schema_version": "xuan_same_window_no_order_shadow_start_preflight_v1",
        "created_utc": utc_now(),
        "status": (
            "KEEP_XUAN_SAME_WINDOW_NO_ORDER_SHADOW_START_PREFLIGHT_ENGINEERING_READY_APPROVAL_REQUIRED"
            if engineering_preflight_ready
            else "BLOCKED_XUAN_SAME_WINDOW_NO_ORDER_SHADOW_START_PREFLIGHT_ENGINEERING_NOT_READY"
        ),
        "engineering_preflight_ready": engineering_preflight_ready,
        "shadow_start_ready": False,
        "manual_approval_required": True,
        "remaining_blockers": remaining_blockers,
        "validation_errors": validation_errors,
        "candidate_summary": summary,
        "active_runner_conflict_check": process_check,
        "runner_config": {
            "path": str(config_path),
            "sha256": sha256_file(config_path),
            "config_fingerprint": config_fingerprint,
            "dry_run_only": True,
            "orders_allowed": False,
            "live_orders_allowed": False,
            "candidate_import_allowed": False,
            "start_allowed_without_manual_approval": False,
        },
        "policy": {
            "research_ranking_material": True,
            "same_window_handoff_is_research_material_only": True,
            "residual_settlement_pnl_is_strategy_edge": False,
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "deployable": False,
            "live_orders_allowed": False,
            "historical_shadow_or_v1_is_private_truth": False,
            "requires_future_owner_execution_truth_for_promotion": True,
        },
        "inputs": {
            "design_packet": str(args.design_packet.expanduser()),
            "design_packet_status": design_packet.get("status") or "MISSING",
            "tier_a_candidates": str(candidate_csv),
            "tier_a_candidates_sha256": candidate_hash,
            "tiered_scorecard": str(args.tiered_scorecard.expanduser()),
            "tiered_scorecard_status": tiered_scorecard.get("status") or "MISSING",
            "shadow_readiness_gate": str(args.shadow_readiness_gate.expanduser()),
            "shadow_readiness_gate_status": shadow_readiness_gate.get("status") or "MISSING",
            "start_preflight_spec": str(args.start_preflight_spec.expanduser()),
            "start_preflight_spec_status": start_preflight_spec.get("status") or "MISSING",
            "btc_canary_preflight": str(args.btc_canary_preflight.expanduser()),
            "btc_canary_preflight_status": btc_canary_preflight.get("status") or "MISSING",
        },
        "outputs": {
            "runner_config": str(config_path),
            "preflight_checklist_json": str(checklist_json_path),
            "preflight_checklist_csv": str(checklist_csv_path),
            "candidate_binding_csv": str(candidate_binding_path),
            "active_runner_conflict_check": str(process_check_path),
            "start_command_preview": str(start_command_path),
        },
    }
    manifest_path = output_dir / "XUAN_SAME_WINDOW_NO_ORDER_SHADOW_START_PREFLIGHT.json"
    write_json(manifest_path, manifest)
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "engineering_preflight_ready": engineering_preflight_ready,
                "shadow_start_ready": False,
                "remaining_blockers": remaining_blockers,
                "candidate_summary": summary,
                "manifest": str(manifest_path),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if engineering_preflight_ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
