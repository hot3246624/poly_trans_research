#!/usr/bin/env python3
"""Build offline fixture-based telemetry contract review for NAGI maker shadow."""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
EXPORTS = ROOT / "data" / "exports"
OUT = EXPORTS / "nagi_private_maker_shadow_telemetry_contract_packet_20260608"
FIXTURES = OUT / "fixtures"
RESULTS = OUT / "results"
BUILDER = ROOT / "scripts/build_nagi_private_maker_shadow_telemetry_contract_packet.py"

APPROVAL_PACKET = (
    EXPORTS
    / "nagi_private_maker_shadow_approval_packet_20260608"
    / "NAGI_PRIVATE_MAKER_SHADOW_APPROVAL_PACKET.json"
)
TELEMETRY_VALIDATOR = ROOT / "scripts/validate_nagi_private_maker_shadow_telemetry.py"
KILL_SWITCH_EVALUATOR = ROOT / "scripts/evaluate_nagi_private_maker_shadow_kill_switch.py"

STATUS = (
    "KEEP_NAGI_PRIVATE_MAKER_SHADOW_TELEMETRY_CONTRACT_REVIEWED_FIXTURES_ONLY_"
    "PRIVATE_SAMPLE_REQUIRED_NOT_OOS_READY"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def binding(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if path.is_file():
        out.update({"sha256": sha256_file(path), "size": path.stat().st_size})
    return out


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_sha256sums(root: Path, files: list[Path]) -> None:
    lines = [f"{sha256_file(path)}  {path.relative_to(root)}" for path in files]
    (root / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


DECISION_FIELDS = [
    "decision_id",
    "condition_id",
    "decision_ts_ms",
    "remaining_ms",
    "variant_id",
    "side",
    "asset_id",
    "bid_px",
    "opp_bid_px",
    "pair_cost_at_decision",
    "l1_age_ms",
    "l2_age_ms",
    "align_lag_ms",
]
ORDER_FIELDS = [
    "decision_id",
    "client_order_id",
    "order_id",
    "submit_ts_ms",
    "ack_ts_ms",
    "post_only_flag",
    "price",
    "size",
    "status",
    "cancel_ack_ts_ms",
    "remaining_open_qty",
]
FILL_FIELDS = [
    "decision_id",
    "client_order_id",
    "order_id",
    "trade_id",
    "fill_ts_ms",
    "maker_or_taker",
    "fill_px",
    "fill_qty",
    "fee_paid",
    "fee_rate_bps",
]
INVENTORY_FIELDS = [
    "condition_id",
    "asset_id",
    "outcome",
    "source_kind",
    "size",
    "recv_ms",
    "drift_flag",
]


def base_rows(markets: int = 100, fills_per_market: int = 5) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    decisions: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []
    fills: list[dict[str, Any]] = []
    inventory: list[dict[str, Any]] = []
    seq = 0
    for market_idx in range(markets):
        condition_id = f"fixture_condition_{market_idx:03d}"
        inventory.append(
            {
                "condition_id": condition_id,
                "asset_id": f"asset_yes_{market_idx:03d}",
                "outcome": "YES",
                "source_kind": "RECONCILED",
                "size": "0",
                "recv_ms": 1000000000 + market_idx,
                "drift_flag": "false",
            }
        )
        for fill_idx in range(fills_per_market):
            seq += 1
            decision_id = f"d{seq:05d}"
            client_order_id = f"nagi_shadow_fixture_{seq:05d}"
            order_id = f"o{seq:05d}"
            ts = 1000000000 + seq * 1000
            decisions.append(
                {
                    "decision_id": decision_id,
                    "condition_id": condition_id,
                    "decision_ts_ms": ts,
                    "remaining_ms": 45000,
                    "variant_id": "up_35_50_all__pc0.995__qmin0",
                    "side": "YES",
                    "asset_id": f"asset_yes_{market_idx:03d}",
                    "bid_px": 0.42,
                    "opp_bid_px": 0.57,
                    "pair_cost_at_decision": 0.99,
                    "l1_age_ms": 10,
                    "l2_age_ms": 10,
                    "align_lag_ms": 10,
                }
            )
            orders.append(
                {
                    "decision_id": decision_id,
                    "client_order_id": client_order_id,
                    "order_id": order_id,
                    "submit_ts_ms": ts + 5,
                    "ack_ts_ms": ts + 30,
                    "post_only_flag": "true",
                    "price": 0.42,
                    "size": 1.0,
                    "status": "filled",
                    "cancel_ack_ts_ms": "",
                    "remaining_open_qty": "0",
                }
            )
            fills.append(
                {
                    "decision_id": decision_id,
                    "client_order_id": client_order_id,
                    "order_id": order_id,
                    "trade_id": f"t{seq:05d}",
                    "fill_ts_ms": ts + 60,
                    "maker_or_taker": "MAKER",
                    "fill_px": 0.42,
                    "fill_qty": 1.0,
                    "fee_paid": 0,
                    "fee_rate_bps": 0,
                }
            )
    return decisions, orders, fills, inventory


def mutate_case(case_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], bool]:
    decisions, orders, fills, inventory = base_rows()
    expect_pass = False
    if case_id == "pass_contract":
        expect_pass = True
    elif case_id == "fail_taker_fill":
        fills[0]["maker_or_taker"] = "TAKER"
    elif case_id == "fail_nonzero_fee":
        fills[0]["fee_paid"] = "0.01"
    elif case_id == "fail_stale_book":
        decisions[0]["l1_age_ms"] = 501
    elif case_id == "fail_missing_ack":
        orders[0]["ack_ts_ms"] = ""
    elif case_id == "fail_missing_cancel_or_full_fill":
        orders[0]["status"] = "open"
        orders[0]["remaining_open_qty"] = "1"
    elif case_id == "fail_pair_cost_breach":
        decisions[0]["pair_cost_at_decision"] = 1.0
    elif case_id == "fail_residual_breach":
        for idx in range(26):
            condition_id = f"extra_residual_condition_{idx:03d}"
            decisions.append(
                {
                    "decision_id": f"resid{idx:03d}",
                    "condition_id": condition_id,
                    "decision_ts_ms": 2000000000 + idx,
                    "remaining_ms": 45000,
                    "variant_id": "up_35_50_all__pc0.995__qmin0",
                    "side": "YES",
                    "asset_id": f"asset_resid_{idx:03d}",
                    "bid_px": 0.42,
                    "opp_bid_px": 0.57,
                    "pair_cost_at_decision": 0.99,
                    "l1_age_ms": 10,
                    "l2_age_ms": 10,
                    "align_lag_ms": 10,
                }
            )
    elif case_id == "fail_missing_columns":
        decisions[0].pop("variant_id", None)
    elif case_id == "fail_inventory_drift":
        inventory[0]["drift_flag"] = "true"
    else:
        raise ValueError(case_id)
    return decisions, orders, fills, inventory, expect_pass


def run_command(args: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(args, cwd=str(ROOT), text=True, capture_output=True, check=False)
    return proc.returncode, proc.stdout, proc.stderr


def run_case(case_id: str) -> dict[str, Any]:
    case_dir = FIXTURES / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    decisions, orders, fills, inventory, expect_pass = mutate_case(case_id)
    write_csv(case_dir / "decisions.csv", decisions, DECISION_FIELDS)
    write_csv(case_dir / "orders.csv", orders, ORDER_FIELDS)
    write_csv(case_dir / "fills.csv", fills, FILL_FIELDS)
    write_csv(case_dir / "inventory.csv", inventory, INVENTORY_FIELDS)

    out_json = RESULTS / f"{case_id}.telemetry_review.json"
    cmd = [
        "python3",
        str(TELEMETRY_VALIDATOR),
        "--packet",
        str(APPROVAL_PACKET),
        "--decisions-csv",
        str(case_dir / "decisions.csv"),
        "--orders-csv",
        str(case_dir / "orders.csv"),
        "--fills-csv",
        str(case_dir / "fills.csv"),
        "--inventory-csv",
        str(case_dir / "inventory.csv"),
        "--out-json",
        str(out_json),
    ]
    code, stdout, stderr = run_command(cmd)
    review = json.loads(out_json.read_text(encoding="utf-8"))

    kill_out = RESULTS / f"{case_id}.kill_switch_eval.json"
    kill_cmd = [
        "python3",
        str(KILL_SWITCH_EVALUATOR),
        "--telemetry-review-json",
        str(out_json),
        "--out-json",
        str(kill_out),
    ]
    kill_code, kill_stdout, kill_stderr = run_command(kill_cmd)
    kill_review = json.loads(kill_out.read_text(encoding="utf-8"))

    outcome_ok = (code == 0 and review.get("ok") is True) if expect_pass else (code == 2 and review.get("ok") is False)
    kill_expected_ok = expect_pass
    kill_ok = (kill_code == 0 and kill_review.get("ok") is True) if kill_expected_ok else (kill_code == 2 and kill_review.get("ok") is False)
    return {
        "case_id": case_id,
        "expect_pass": expect_pass,
        "telemetry_exit_code": code,
        "telemetry_status": review.get("status"),
        "telemetry_ok": review.get("ok"),
        "telemetry_issues": review.get("issues"),
        "telemetry_out_json": str(out_json),
        "kill_exit_code": kill_code,
        "kill_status": kill_review.get("status"),
        "kill_ok": kill_review.get("ok"),
        "kill_triggers": kill_review.get("triggers"),
        "kill_out_json": str(kill_out),
        "contract_case_ok": outcome_ok and kill_ok,
        "stdout_tail": stdout[-500:],
        "stderr_tail": stderr[-500:],
        "kill_stdout_tail": kill_stdout[-500:],
        "kill_stderr_tail": kill_stderr[-500:],
    }


def write_preview(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo 'NOT_AUTHORIZED: telemetry contract fixtures are offline review only.' >&2\n"
        "exit 66\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    FIXTURES.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)

    case_ids = [
        "pass_contract",
        "fail_taker_fill",
        "fail_nonzero_fee",
        "fail_stale_book",
        "fail_missing_ack",
        "fail_missing_cancel_or_full_fill",
        "fail_pair_cost_breach",
        "fail_residual_breach",
        "fail_missing_columns",
        "fail_inventory_drift",
    ]
    case_results = [run_case(case_id) for case_id in case_ids]
    all_cases_ok = all(row["contract_case_ok"] for row in case_results)

    packet = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS if all_cases_ok else "BLOCKED_NAGI_PRIVATE_MAKER_SHADOW_TELEMETRY_CONTRACT_FIXTURE_FAILURE",
        "ok": all_cases_ok,
        "case_results": case_results,
        "decision": {
            "fixtures_only": True,
            "private_sample_present": False,
            "telemetry_contract_reviewed": all_cases_ok,
            "execution_ready": False,
            "orders_authorized": False,
            "next_step": "implement dry-run-only order-client adapter or collect exact-approved private maker telemetry sample; do not claim OOS/readiness",
        },
        "source_bindings": {
            "approval_packet": binding(APPROVAL_PACKET),
            "telemetry_validator": binding(TELEMETRY_VALIDATOR),
            "kill_switch_evaluator": binding(KILL_SWITCH_EVALUATOR),
            "builder": binding(BUILDER),
        },
        "non_claims": {
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "deployable": False,
            "oos_ready": False,
            "orders_authorized": False,
            "cancels_authorized": False,
            "private_key_authorized": False,
            "ws_authorized": False,
        },
    }

    packet_path = OUT / "NAGI_PRIVATE_MAKER_SHADOW_TELEMETRY_CONTRACT_PACKET.json"
    report_path = OUT / "NAGI_PRIVATE_MAKER_SHADOW_TELEMETRY_CONTRACT_REPORT.md"
    preview_path = OUT / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"

    write_json(packet_path, packet)
    report_path.write_text(
        "\n".join(
            [
                "# NAGI Private Maker Shadow Telemetry Contract",
                "",
                f"Status: `{packet['status']}`",
                "",
                "This is fixtures-only offline review. It contains no private sample and authorizes no execution.",
                "",
                "## Fixture Results",
                "",
                *[
                    f"- `{row['case_id']}`: telemetry_ok={row['telemetry_ok']} kill_ok={row['kill_ok']} contract_case_ok={row['contract_case_ok']}"
                    for row in case_results
                ],
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    write_preview(preview_path)

    files = [packet_path, report_path, preview_path]
    files.extend(sorted(RESULTS.glob("*.json")))
    write_sha256sums(OUT, files)
    print(json.dumps({"packet": str(packet_path), "status": packet["status"], "ok": packet["ok"]}, indent=2, sort_keys=True))
    return 0 if all_cases_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
