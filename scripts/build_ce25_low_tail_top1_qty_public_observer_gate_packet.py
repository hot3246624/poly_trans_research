#!/usr/bin/env python3
"""Build review-only CE25 low-tail top1-qty public observer gate packet."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
EXPORTS = ROOT / "data/exports"
DEFAULT_LEDGER_DIR = EXPORTS / "ce25_low_tail_top1_qty_candidate_ledger_20260606"
DEFAULT_OUTPUT_DIR = EXPORTS / "ce25_low_tail_top1_qty_public_observer_gate_packet_20260606"

STATUS_IMPLEMENTATION_REQUIRED = (
    "KEEP_CE25_LOW_TAIL_TOP1_QTY_PUBLIC_OBSERVER_GATE_PACKET_REVIEWED_IMPLEMENTATION_REQUIRED_NOT_EXECUTION_READY"
)
STATUS_RUNTIME_BOUND = (
    "KEEP_CE25_LOW_TAIL_TOP1_QTY_PUBLIC_OBSERVER_GATE_PACKET_REVIEWED_RUNTIME_BOUND_TARGET_INPUT_REQUIRED_NOT_EXECUTION_READY"
)
LEDGER_STATUS = "KEEP_CE25_LOW_TAIL_TOP1_QTY_NORMALIZED_CANDIDATE_LEDGER_REVIEW_REQUIRED_NOT_OOS_READY"
STRATEGY_ID = "CE25_BTC5M_LOW_PRICE_TAIL_SIDE_SPLIT_TOP1_QTY_V2"
OWNER_LINE = "CE25_LOW_TAIL_RESEARCH"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def non_claims() -> dict[str, bool]:
    return {
        "exact_approval_issued": False,
        "ws_started": False,
        "oos_authorized": False,
        "runner_authorized": False,
        "observer_authorized": False,
        "shared_ingress_used": False,
        "rest_book_evidence_used": False,
        "orders_authorized": False,
        "private_key_loaded": False,
        "candidate_import_authorized": False,
        "latest_pointer_update_authorized": False,
        "private_truth_ready": False,
        "strategy_promotion_ready": False,
        "live_ready": False,
        "deployable": False,
    }


def load_ledger_inputs(ledger_dir: Path) -> dict[str, Any]:
    paths = {
        "strategy_input": ledger_dir / "CE25_LOW_TAIL_TOP1_QTY_STRATEGY_INPUT.json",
        "hash_manifest": ledger_dir / "CE25_LOW_TAIL_TOP1_QTY_HASH_MANIFEST.json",
        "candidate_ledger": ledger_dir / "ce25_low_tail_top1_qty_candidate_ledger.csv",
        "leg_evidence": ledger_dir / "ce25_low_tail_top1_qty_leg_evidence.csv",
        "side_qty_summary": ledger_dir / "ce25_low_tail_top1_qty_side_qty_summary.csv",
        "overlay_audit": ledger_dir / "ce25_low_tail_top1_qty_overlay_freshness_action_audit.csv",
        "review_note": ledger_dir / "CE25_LOW_TAIL_TOP1_QTY_REVIEW_NOTE.md",
    }
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise SystemExit(json.dumps({"ok": False, "errors": ["missing_ledger_inputs"], "missing": missing}, indent=2))
    strategy = read_json(paths["strategy_input"])
    if strategy.get("status") != LEDGER_STATUS:
        raise SystemExit(json.dumps({"ok": False, "errors": ["ledger_status_mismatch"]}, indent=2))
    if strategy.get("strategy_id") != STRATEGY_ID or strategy.get("strategy_owner_line") != OWNER_LINE:
        raise SystemExit(json.dumps({"ok": False, "errors": ["strategy_or_owner_mismatch"]}, indent=2))
    if sha256_file(paths["candidate_ledger"]) != strategy.get("candidate_csv_sha256"):
        raise SystemExit(json.dumps({"ok": False, "errors": ["candidate_ledger_hash_mismatch"]}, indent=2))
    if sha256_file(paths["overlay_audit"]) != strategy.get("overlay_freshness_action_audit_csv_sha256"):
        raise SystemExit(json.dumps({"ok": False, "errors": ["overlay_audit_hash_mismatch"]}, indent=2))
    return {"paths": paths, "strategy": strategy}


def command_preview(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                "echo 'NOT_AUTHORIZED: CE25 low-tail top1-qty observer gate packet is review-only; no WS/OOS execution is authorized.' >&2",
                "exit 66",
                "",
            ]
        ),
        encoding="utf-8",
    )
    path.chmod(0o755)


def exact_approval_draft(path: Path, output_dir: Path, runtime_exists: bool) -> None:
    runtime_clause = (
        "The CE25 observer runtime hash must match this packet before any WS connection. "
        if runtime_exists
        else "The CE25 observer runtime is not yet implemented, so this draft cannot be issued. "
    )
    path.write_text(
        "\n".join(
            [
                "DRAFT_NOT_ISSUED",
                "",
                "I authorize exactly one CE25_BTC5M_LOW_PRICE_TAIL_SIDE_SPLIT_TOP1_QTY_V2 public/no-order observer-gate smoke run, "
                f"using review packet `{output_dir}`.",
                "",
                "This draft would authorize only one direct public CLOB market WebSocket connection for BTC 5m final-60s observation, "
                "with no orders, no private key, no candidate import, no shared-ingress, and no REST book evidence. It would not authorize "
                "OOS pass claims, canary/live/deploy/funding, latest pointer update, private_truth_ready, strategy_promotion_ready, "
                "live_ready, deployable, or any readiness/promotion/private-truth claim.",
                "",
                runtime_clause
                + "The future run must bind a reviewed current/live BTC 5m target CSV, use a fresh output directory, "
                "verify packet/source/runtime/target hashes before WS connection, require current/live BTC "
                "5m market coverage, collect final-60s evidence only, evaluate UP/DOWN side-split opportunities where executable side "
                "price is in [0.20, 0.35], require opposite top1 qty >= target_qty, paircap <= 0.965, official Polymarket fee formula "
                "stress at fee_rate=0.07, exactly one direct public CLOB WS, token-side top depth complete, no WS disconnect/reconnect, "
                "no REST/shared-ingress evidence, safety counters zero, and readiness flags false.",
                "",
                "Highest future success is capped at "
                "`KEEP_CE25_LOW_TAIL_TOP1_QTY_PUBLIC_OBSERVER_GATE_EVIDENCE_REVIEW_REQUIRED_NOT_OOS_READY`.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger-dir", type=Path, default=DEFAULT_LEDGER_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    ledger_dir = args.ledger_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    data = load_ledger_inputs(ledger_dir)
    strategy = data["strategy"]
    paths = data["paths"]
    output_dir.mkdir(parents=True, exist_ok=True)

    planned_observer = ROOT / "scripts/run_ce25_low_tail_top1_qty_public_ws_observer.py"
    supporting_reference_observer = ROOT / "scripts/run_btc_core_scoped_public_ws_no_order_observer.py"
    runtime_exists = planned_observer.is_file()
    status = STATUS_RUNTIME_BOUND if runtime_exists else STATUS_IMPLEMENTATION_REQUIRED
    runtime_hash = sha256_file(planned_observer) if runtime_exists else None
    packet = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": status,
        "strategy_owner_line": OWNER_LINE,
        "strategy_id": STRATEGY_ID,
        "strategy_version": strategy["strategy_version"],
        "scope": "review_only_public_no_order_final_60s_observer_gate_contract",
        "execution_approval": "NOT_ISSUED",
        "source_artifact_hashes": {
            name: {"path": str(path), "sha256": sha256_file(path), "size_bytes": path.stat().st_size}
            for name, path in paths.items()
        },
        "candidate_source_summary": {
            "historical_candidate_count": strategy["candidate_count"],
            "historical_unique_market_count": strategy["expected_market_count"],
            "branch_count": strategy["validation_summary"]["branch_count"],
            "official07_roi_est": strategy["validation_summary"]["official07_roi_est"],
            "top_overlay_required_count": strategy["overlay_freshness_summary"]["top_overlay_required_count"],
            "raw_l2_stale_pair_count": strategy["overlay_freshness_summary"]["raw_l2_stale_pair_count"],
            "interpretation": "Historical replay/L2 set defines the policy shape only; current/future public observation must rediscover opportunities live.",
        },
        "implementation_status": {
            "ce25_observer_gate_runtime": str(planned_observer),
            "runtime_exists": runtime_exists,
            "runtime_sha256": runtime_hash,
            "implementation_required_before_execution_approval": not runtime_exists,
            "target_universe_binding_required_before_execution_approval": True,
            "target_universe_requirement": (
                "Future smoke/OOS-style public evidence must bind a reviewed current/live BTC 5m target CSV. "
                "The historical 268-row ledger is policy source evidence only, not a live target universe."
            ),
            "reference_public_ws_observer": {
                "path": str(supporting_reference_observer),
                "sha256": sha256_file(supporting_reference_observer),
                "role": "reference transport/reporting implementation only; not CE25 gate-complete as-is",
            },
            "offline_self_test_contract": {
                "valid_command": (
                    f"python3 {planned_observer} --self-test valid --output-dir FRESH_DIR "
                    "--no-rest-book --no-shared-ingress --require-opportunity"
                ),
                "valid_expected_returncode": 0,
                "invalid_command": (
                    f"python3 {planned_observer} --self-test invalid --output-dir FRESH_DIR "
                    "--no-rest-book --no-shared-ingress --require-opportunity"
                ),
                "invalid_expected_returncode": 2,
                "invalid_expected_status": "BLOCKED_CE25_LOW_TAIL_TOP1_QTY_PUBLIC_OBSERVER_GATE_FAIL_CLOSED",
            },
        },
        "observer_gate_contract": {
            "asset": "BTC",
            "timeframe": "5m",
            "market_scope": "current_live_btc_5m_only",
            "historical_condition_ids_allowed_as_targets": False,
            "final_60s_only": True,
            "eligible_remaining_ms_range": [1, 60_000],
            "side_split_required": True,
            "sides": ["UP", "DOWN"],
            "target_qty_baseline": 5.0,
            "target_qty_capacity_lane": 8.0,
            "executable_side_price_range": [0.20, 0.35],
            "paircap": 0.965,
            "opposite_top1_qty_gte_target_qty_required": True,
            "same_row_and_entry_paircap_modes_reported_separately": True,
            "official_fee_formula": "fee = qty * fee_rate * price * (1 - price)",
            "official_fee_rate_stress": 0.07,
        },
        "public_observation_contract": {
            "transport": "direct_public_clob_ws",
            "max_ws_connections": 1,
            "shared_ingress_allowed": False,
            "rest_book_evidence_allowed": False,
            "private_key_allowed": False,
            "orders_allowed": False,
            "candidate_import_allowed": False,
            "token_side_top_depth_complete_required": True,
            "ws_disconnect_count_required": 0,
            "ws_reconnect_count_required": 0,
            "safety_counters_required": 0,
        },
        "required_outputs": [
            "CE25_LOW_TAIL_TOP1_QTY_PUBLIC_OBSERVER_EVENTS.jsonl",
            "CE25_LOW_TAIL_TOP1_QTY_PUBLIC_OBSERVER_OPPORTUNITIES.csv",
            "CE25_LOW_TAIL_TOP1_QTY_PUBLIC_OBSERVER_GATE_SUMMARY.json",
            "CE25_LOW_TAIL_TOP1_QTY_PUBLIC_OBSERVER_AUDIT_MANIFEST.json",
            "CE25_LOW_TAIL_TOP1_QTY_PUBLIC_OBSERVER_EVAL.json",
        ],
        "minimum_opportunity_columns": [
            "strategy_owner_line",
            "strategy_id",
            "candidate_id",
            "observed_ts_ms",
            "market_id",
            "slug",
            "window_start_ts_ms",
            "window_end_ts_ms",
            "remaining_ms",
            "side_split",
            "target_qty",
            "yes_top1_price",
            "yes_top1_qty",
            "no_top1_price",
            "no_top1_qty",
            "executable_side",
            "executable_side_price",
            "opposite_side",
            "opposite_top1_qty",
            "pair_cost",
            "paircap_pass",
            "top1_qty_gate_pass",
            "fee_rate_stress",
            "expected_fee",
            "orders_sent",
            "private_key_loaded",
            "live_orders_allowed",
        ],
        "fail_closed_if": [
            "source artifact hash drift",
            "CE25 observer runtime missing or hash unreviewed in execution packet",
            "current/live BTC 5m market unavailable",
            "WS connection count != 1",
            "shared-ingress or REST book used as evidence",
            "token-side top depth incomplete",
            "final-60s window not respected",
            "executable side price outside 0.20-0.35",
            "opposite top1 qty below target_qty",
            "paircost above 0.965",
            "WS disconnect/reconnect nonzero",
            "safety counter nonzero",
            "private/order/import/live/latest path touched",
            "readiness flag true",
        ],
        "next_required_step": (
            "bind reviewed current/live BTC 5m target universe and prepare separate public/no-order smoke approval packet"
            if runtime_exists
            else "implement_or_bind CE25-specific public WS observer gate, then prepare separate smoke approval packet"
        ),
        "highest_allowed_status": "KEEP_CE25_LOW_TAIL_TOP1_QTY_PUBLIC_OBSERVER_GATE_EVIDENCE_REVIEW_REQUIRED_NOT_OOS_READY",
        "non_claims": non_claims(),
    }

    threshold_spec = {
        "schema_version": 1,
        "status": "CE25_LOW_TAIL_TOP1_QTY_OBSERVER_GATE_THRESHOLDS_REVIEW_ONLY",
        "clean_smoke_thresholds_draft": {
            "min_current_live_btc_5m_markets": 1,
            "min_observed_final60_windows": 1,
            "ws_connection_count": 1,
            "rest_book_used": False,
            "shared_ingress_used": False,
            "ws_disconnect_count": 0,
            "ws_reconnect_count": 0,
            "safety_counters_nonzero": 0,
            "readiness_flags_all_false": True,
        },
        "opportunity_gate_thresholds": packet["observer_gate_contract"],
        "fail_closed_if": packet["fail_closed_if"],
        "non_claims": non_claims(),
    }

    command_body = {
        "status": "DRAFT_NOT_AUTHORIZED_TARGET_INPUT_REQUIRED" if runtime_exists else "DRAFT_NOT_AUTHORIZED_IMPLEMENTATION_REQUIRED",
        "sequence": [
            "verify packet/source hashes",
            "verify CE25 observer runtime hash",
            "verify reviewed current/live BTC 5m target CSV hash",
            "fail if output dir exists",
            "open exactly one direct public CLOB market WS",
            "track current/live BTC 5m market only",
            "evaluate final-60s side-split top1_qty opportunity gate",
            "write required report/audit/eval artifacts",
            "fail closed if any threshold or non-claim is violated",
        ],
        "not_authorized_example": (
            f"uv run --with requests --with websockets python {planned_observer} "
            "--target-csv REVIEWED_CURRENT_LIVE_BTC5M_TARGETS.csv "
            "--expected-target-csv-sha256 REVIEWED_TARGET_SHA256 "
            "--output-dir CE25_LOW_TAIL_TOP1_QTY_PUBLIC_OBSERVER_RUN_REVIEWED_TIMESTAMP "
            "--duration-sec 1800 --max-ws-connections 1 --final-window-sec 60 "
            "--price-lo 0.20 --price-hi 0.35 --paircap 0.965 --target-qty 5 --capacity-target-qty 8 "
            "--fee-rate 0.07 --no-rest-book --no-shared-ingress"
        ),
    }

    packet_path = output_dir / "CE25_LOW_TAIL_TOP1_QTY_PUBLIC_OBSERVER_GATE_PACKET.json"
    threshold_path = output_dir / "CE25_LOW_TAIL_TOP1_QTY_PUBLIC_OBSERVER_GATE_THRESHOLD_SPEC.json"
    command_body_path = output_dir / "FUTURE_COMMAND_BODY_NOT_AUTHORIZED.json"
    exact_approval_path = output_dir / "EXACT_APPROVAL_TEXT_DRAFT_NOT_ISSUED.txt"
    preview_path = output_dir / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    note_path = output_dir / "CE25_LOW_TAIL_TOP1_QTY_PUBLIC_OBSERVER_GATE_BOUNDARY_NOTE.md"
    manifest_path = output_dir / "CE25_LOW_TAIL_TOP1_QTY_PUBLIC_OBSERVER_GATE_HASH_MANIFEST.json"

    write_json(packet_path, packet)
    write_json(threshold_path, threshold_spec)
    write_json(command_body_path, command_body)
    exact_approval_draft(exact_approval_path, output_dir, runtime_exists)
    command_preview(preview_path)
    note_path.write_text(
        "\n".join(
            [
                "# CE25 Low-Tail Top1 Qty Public Observer Gate Packet",
                "",
                f"Status: `{status}`",
                "",
                "This packet is review-only. It freezes the final-60s public/no-order observer gate contract and binds the CE25-specific runtime when present.",
                "",
                "A future smoke packet still must bind a reviewed current/live BTC 5m target universe; historical CE25 replay rows are not live targets.",
                "",
                "No WS, OOS, order, private key, import, canary, live, deploy, funding, latest pointer, or readiness claim is authorized.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    artifacts = [
        packet_path,
        threshold_path,
        command_body_path,
        exact_approval_path,
        preview_path,
        note_path,
        *paths.values(),
    ]
    if runtime_exists:
        artifacts.append(planned_observer)
    manifest = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": status,
        "strategy_owner_line": OWNER_LINE,
        "strategy_id": STRATEGY_ID,
        "artifact_count": len(artifacts),
        "artifacts": [
            {"path": str(path), "sha256": sha256_file(path), "size_bytes": path.stat().st_size}
            for path in artifacts
        ],
        "non_claims": non_claims(),
    }
    write_json(manifest_path, manifest)

    print(
        json.dumps(
            {
                "ok": True,
                "status": status,
                "output_dir": str(output_dir),
                "runtime_exists": runtime_exists,
                "runtime_sha256": runtime_hash,
                "artifact_count": len(artifacts) + 1,
                "manifest_sha256": sha256_file(manifest_path),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
