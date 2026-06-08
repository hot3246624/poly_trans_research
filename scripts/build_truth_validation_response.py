#!/usr/bin/env python3
"""Emit a promotion-gate truth validation response for candidate runs.

The response is intentionally conservative.  Public/proxy candidate outputs can
show a local research result; they cannot prove owner action/fill truth.  This
script packages that boundary into a small JSON/CSV contract so downstream
agents can stop waiting on ambiguous data.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any


PRIVATE_REPLAY_TABLES = ("own_order_events", "own_fill_events", "own_inventory_events", "user_ws_log")
PUBLIC_TRUTH_DATASETS = ("public_account_execution_truth_v1", "xuan_public_execution_truth_v1")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "check_id",
        "question",
        "required_truth",
        "observed_evidence",
        "status",
        "blocker",
        "next_required_input",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def rel_or_str(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def discover_public_truth(data_root: Path) -> dict[str, Any]:
    stores: dict[str, Any] = {}
    for dataset in PUBLIC_TRUTH_DATASETS:
        root = data_root / "verification_store" / dataset
        manifests = []
        for manifest_path in sorted(root.glob("*/EVENT_STORE_MANIFEST.json")):
            try:
                manifest = load_json(manifest_path)
            except (OSError, json.JSONDecodeError):
                continue
            outputs = manifest.get("outputs", {})
            manifests.append(
                {
                    "label": manifest_path.parent.name,
                    "path": str(manifest_path),
                    "days": manifest.get("days", []),
                    "row_count": outputs.get("row_count"),
                    "is_private_truth": bool(manifest.get("is_private_truth", False)),
                    "schema_version": manifest.get("schema_version"),
                    "source": manifest.get("source"),
                    "truth_level_counts": outputs.get("truth_level_counts", {}),
                    "order_type_counts": outputs.get("order_type_counts", {}),
                }
            )
        stores[dataset] = {
            "root": str(root),
            "manifest_count": len(manifests),
            "manifests": manifests,
            "has_private_truth": any(item["is_private_truth"] for item in manifests),
        }
    return stores


def sqlite_table_count(conn: sqlite3.Connection, table: str) -> int | None:
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", [table]).fetchone()
    if not exists:
        return None
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def probe_replay_sqlite(paths: list[Path]) -> list[dict[str, Any]]:
    probes = []
    for path in paths:
        row: dict[str, Any] = {"path": str(path), "exists": path.is_file(), "private_table_counts": {}}
        if not path.is_file():
            probes.append(row)
            continue
        conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro&immutable=1", uri=True)
        try:
            conn.execute("PRAGMA query_only = ON")
            for table in PRIVATE_REPLAY_TABLES:
                row["private_table_counts"][table] = sqlite_table_count(conn, table)
        finally:
            conn.close()
        row["has_private_rows"] = any((count or 0) > 0 for count in row["private_table_counts"].values())
        probes.append(row)
    return probes


def metric_float(metrics: dict[str, Any], key: str) -> float | None:
    value = metrics.get(key)
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def close_enough(a: float | None, b: float | None, tol: float) -> bool:
    return a is not None and b is not None and abs(a - b) <= tol


def status_for_residual(
    metrics: dict[str, Any],
    expected_qty: float | None,
    expected_cost: float | None,
    tol: float,
) -> tuple[str, dict[str, Any], str]:
    observed_qty = metric_float(metrics, "residual_qty")
    observed_cost = metric_float(metrics, "residual_cost")
    observed = {
        "observed_residual_qty": observed_qty,
        "observed_residual_cost": observed_cost,
        "expected_residual_qty": expected_qty,
        "expected_residual_cost": expected_cost,
        "tolerance": tol,
    }
    if expected_qty is None and expected_cost is None:
        return "UNKNOWN_NOT_DEPLOYABLE", observed, "No expected private/source-truth FIFO totals were supplied."
    qty_ok = expected_qty is None or close_enough(observed_qty, expected_qty, tol)
    cost_ok = expected_cost is None or close_enough(observed_cost, expected_cost, tol)
    if qty_ok and cost_ok:
        return (
            "PASS_LOCAL_RECONSTRUCTION_ONLY",
            observed,
            "Local FIFO totals match supplied expected totals, but this is still not private/source-truth proof.",
        )
    return "FAIL_CURRENT_RUN_MISMATCH", observed, "Current candidate run does not match the supplied FIFO truth totals."


def build_response(args: argparse.Namespace) -> dict[str, Any]:
    candidate_dir = args.candidate_result_dir.expanduser().resolve()
    result_manifest_path = candidate_dir / "RESULT_SUMMARY_MANIFEST.json"
    registry_manifest_path = candidate_dir / "CANDIDATE_REGISTRY_MANIFEST.json"
    result_manifest = load_json(result_manifest_path)
    registry_manifest = load_json(registry_manifest_path) if registry_manifest_path.exists() else {}
    data_root = Path(result_manifest["data_root"]).expanduser().resolve()
    metrics = result_manifest.get("core_metrics", {})
    public_truth = discover_public_truth(data_root)
    replay_probes = probe_replay_sqlite([path.expanduser().resolve() for path in args.replay_sqlite])
    replay_has_private = any(probe.get("has_private_rows") for probe in replay_probes)
    public_has_private = any(store["has_private_truth"] for store in public_truth.values())

    private_truth_available = replay_has_private or public_has_private or bool(args.private_truth_available)
    source_scope = {
        "candidate_result_dir": str(candidate_dir),
        "result_summary_manifest": str(result_manifest_path),
        "candidate_registry_manifest": str(registry_manifest_path) if registry_manifest_path.exists() else None,
        "data_root": str(data_root),
        "dataset_type": result_manifest.get("dataset_type"),
        "labels": result_manifest.get("labels", []),
        "days": result_manifest.get("days", []),
        "market_prefix": result_manifest.get("market_prefix", []),
        "assets": result_manifest.get("assets", []),
        "raw_scanned_by_candidate_run": bool(result_manifest.get("raw_scanned", False)),
        "replay_scanned_by_candidate_run": bool(result_manifest.get("replay_scanned", False)),
        "collector_scanned_by_candidate_run": bool(result_manifest.get("collector_scanned", False)),
        "public_account_execution_truth_v1_private_truth": bool(
            result_manifest.get("public_account_execution_truth_v1_private_truth", False)
        ),
        "private_truth_available": private_truth_available,
    }

    residual_status, residual_observed, residual_blocker = status_for_residual(
        metrics,
        args.expected_residual_qty,
        args.expected_residual_cost,
        args.tolerance,
    )

    checks = [
        {
            "check_id": "pre_action_surplus_budget_accept_block",
            "question": "surplus-budget action accept/block is reproducible from pre-action state",
            "required_truth": "private owner action log or replay-derived contract containing pre-action inventory, open orders, budget, accept/block reason, and stable action_id",
            "observed_evidence": json.dumps(
                {
                    "candidate_registry_semantics": registry_manifest.get("candidate_registry_semantics"),
                    "registry_row_count": registry_manifest.get("row_count"),
                    "current_registry_is_selected_post_action_only": True,
                    "private_truth_available": private_truth_available,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            "status": "UNKNOWN_NOT_DEPLOYABLE" if not private_truth_available else "NEEDS_FIELD_LEVEL_VALIDATION",
            "blocker": "Current registry has selected/post-action rows, not source/private pre-action accept/block evidence.",
            "next_required_input": "Export action_decisions.csv/db with pre_action_state, surplus_budget_state, accepted, blocked_by, and source event ids.",
        },
        {
            "check_id": "budgeted_residual_fifo_reconstruction",
            "question": "budgeted residual FIFO reconstruction matches requested residual_qty/residual_cost",
            "required_truth": "FIFO lot ledger linked to owner fills or replay-derived validated action contract",
            "observed_evidence": json.dumps(residual_observed, ensure_ascii=False, sort_keys=True),
            "status": residual_status,
            "blocker": residual_blocker,
            "next_required_input": "Export residual_fifo_lots.csv/db with lot_id, source_action_id, fill_id, qty, cost, remaining_qty, remaining_cost, and close/redeem linkage.",
        },
        {
            "check_id": "strict_second_leg_rescue_source_linkage",
            "question": "strict second-leg rescue close timing/cost/fee/source linkage is validated",
            "required_truth": "private owner fills or replay-derived strict rescue close rows linked to md_book_l2/md_trades/source ids and official fee calculation",
            "observed_evidence": json.dumps(
                {
                    "state_machine_config": result_manifest.get("config", {}),
                    "has_strict_rescue_metrics": any("rescue" in str(key).lower() for key in metrics),
                    "fee_model": metrics.get("fee_model") or result_manifest.get("config", {}).get("fee_model"),
                    "official_fee_formula": metrics.get("official_fee_formula")
                    or result_manifest.get("config", {}).get("official_fee_formula"),
                    "private_truth_available": private_truth_available,
                    "replay_private_probes": replay_probes,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            "status": "UNKNOWN_NOT_DEPLOYABLE" if not private_truth_available else "NEEDS_FIELD_LEVEL_VALIDATION",
            "blocker": "Current run has no strict rescue close ledger with source event linkage, and public/proxy truth cannot prove owner fills.",
            "next_required_input": "Export strict_rescue_closes.csv/db with close_ts_ms, source_l2_id/trade_id, fill_id/order_id, cost, qty, fee, and source_kind.",
        },
    ]

    promotion_gate_pass = all(row["status"].startswith("PASS") for row in checks) and private_truth_available
    return {
        "created_at": utc_now(),
        "schema_version": "truth_validation_response_v1",
        "status": "PASS_PROMOTION_GATE" if promotion_gate_pass else "UNKNOWN_NOT_DEPLOYABLE",
        "promotion_gate_pass": promotion_gate_pass,
        "source_scope": source_scope,
        "public_truth_sources": public_truth,
        "replay_private_probes": replay_probes,
        "current_candidate_metrics": {
            "status": metrics.get("status"),
            "result_classification": metrics.get("result_classification"),
            "deployable": metrics.get("deployable"),
            "can_support_strategy_promotion": metrics.get("can_support_strategy_promotion"),
            "residual_qty": metrics.get("residual_qty"),
            "residual_cost": metrics.get("residual_cost"),
            "gross_pnl": metrics.get("gross_pnl"),
            "net_pnl": metrics.get("net_pnl"),
            "fee_model": metrics.get("fee_model"),
        },
        "checks": checks,
        "required_contract_files": {
            "action_decisions": "pre-action accept/block ledger",
            "residual_fifo_lots": "budgeted FIFO lot ledger",
            "strict_rescue_closes": "second-leg rescue source-linkage ledger",
            "manifest": "days, labels, source_kind, is_private_truth, source replay/private export ids, row counts, schema",
        },
        "conclusion": (
            "The local candidate remains research-only.  The available public/proxy truth is useful for audit context, "
            "but it is not sufficient to promote combined surplus-budget + strict rescue without owner/private truth "
            "or an equivalent replay-derived source-truth contract."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-result-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--expected-residual-qty", type=float, default=None)
    parser.add_argument("--expected-residual-cost", type=float, default=None)
    parser.add_argument("--tolerance", type=float, default=1e-6)
    parser.add_argument("--replay-sqlite", type=Path, action="append", default=[])
    parser.add_argument("--private-truth-available", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    candidate_dir = args.candidate_result_dir.expanduser().resolve()
    out_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir is not None
        else candidate_dir.with_name(f"{candidate_dir.name}_truth_validation_response_v1")
    )
    if out_dir.exists():
        if not args.force:
            raise FileExistsError(f"output exists; pass --force to replace: {out_dir}")
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    response = build_response(args)
    write_json(out_dir / "truth_validation_response.json", response)
    write_json(
        out_dir / "VALIDATION_RESPONSE_MANIFEST.json",
        {
            "created_at": response["created_at"],
            "schema_version": response["schema_version"],
            "status": response["status"],
            "promotion_gate_pass": response["promotion_gate_pass"],
            "row_count": len(response["checks"]),
            "outputs": {
                "json": "truth_validation_response.json",
                "csv": "truth_validation_checks.csv",
                "readme": "README.md",
            },
            "source_scope": response["source_scope"],
        },
    )
    write_csv(out_dir / "truth_validation_checks.csv", response["checks"])
    (out_dir / "README.md").write_text(
        "# Truth Validation Response V1\n\n"
        "This directory is a compatibility response for promotion-gate review. "
        "It does not claim deployability unless `promotion_gate_pass=true` in "
        "`truth_validation_response.json`.\n\n"
        "Use `truth_validation_checks.csv` for the three requested checks and "
        "`VALIDATION_RESPONSE_MANIFEST.json` for row counts, scope, and paths.\n",
        encoding="utf-8",
    )
    print(json.dumps({"output_dir": str(out_dir), "status": response["status"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
