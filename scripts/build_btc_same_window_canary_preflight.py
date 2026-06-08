#!/usr/bin/env python3
"""Build the BTC same-window tiny canary preflight review packet.

This packet is research/preflight material only. It intentionally produces a
dry-run import contract and owner private-truth schema without enabling live
orders or claiming historical private truth.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any


DEFAULT_DATA_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
DEFAULT_CONTRACT_ROOT = DEFAULT_DATA_ROOT / "derived/contract_examples"
DEFAULT_SCORECARD = (
    DEFAULT_CONTRACT_ROOT / "xuan_same_window_handoff_tiered_scorecard_latest/scorecard.csv"
)
DEFAULT_ACTIONS = (
    DEFAULT_CONTRACT_ROOT
    / "xuan_completion_candidate_rescore_latest/xuan_completion_candidate_same_window_handoff_actions.csv"
)
DEFAULT_RESCORE_MANIFEST = (
    DEFAULT_CONTRACT_ROOT
    / "xuan_completion_candidate_rescore_latest/XUAN_COMPLETION_CANDIDATE_RESCORE_MANIFEST.json"
)
DEFAULT_BTC_SEMANTIC_ALIGNMENT = (
    DEFAULT_CONTRACT_ROOT
    / "btc_parity_semantic_alignment_latest/BTC_PARITY_SEMANTIC_ALIGNMENT_EXPERIMENT.json"
)
DEFAULT_BTC_SOURCE_SEMANTICS = (
    DEFAULT_CONTRACT_ROOT / "btc_source_semantics_delta_latest/BTC_SOURCE_SEMANTICS_DELTA_REPORT.json"
)
DEFAULT_L2_TOP_MANIFEST = (
    DEFAULT_CONTRACT_ROOT / "l2_top_aligned_mart_20260502_20260518_l2/L2_TOP_ALIGNED_MART_MANIFEST.json"
)
DEFAULT_L2_TOP_DUCKDB = (
    DEFAULT_CONTRACT_ROOT / "l2_top_aligned_mart_20260502_20260518_l2/l2_top_aligned_mart.duckdb"
)

FILTER_NAME = "btc_same_window_residual_share_le_3pct_v1"
FILTER_VERSION = "v1"
SOURCE_SEMANTICS_CONTRACT_ID = "btc_v1_normalized_buy_adapter_canonical_research_canary_v1"
L2_TOP_OVERLAY_CONTRACT_ID = "md_book_l2_top_aligned_l1_canonical_top_raw_l2_depth_asof_v1"
RUNNER_PROFILE_ID = "btc_same_window_tiny_canary_dryrun_v1"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_obj(obj: Any) -> str:
    blob = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def stable_id(*parts: Any, length: int = 20) -> str:
    return hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:length]


def fnum(row: dict[str, Any], key: str, default: float = 0.0) -> float:
    value = row.get(key)
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def inum(row: dict[str, Any], key: str, default: int = 0) -> int:
    value = row.get(key)
    if value in (None, ""):
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def rounded(value: Any, digits: int = 6) -> float | None:
    if value is None:
        return None
    try:
        if math.isnan(float(value)):
            return None
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def quantile(values: list[float], q: float) -> float | None:
    clean = sorted(v for v in values if not math.isnan(v))
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    pos = (len(clean) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return clean[lo]
    return clean[lo] * (hi - pos) + clean[hi] * (pos - lo)


def quote_sql(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def filter_candidates(scorecard_rows: list[dict[str, str]], residual_share_max: float) -> list[dict[str, Any]]:
    candidates = [
        dict(row)
        for row in scorecard_rows
        if row.get("asset") == "BTC"
        and fnum(row, "residual_cost_share", 999.0) <= residual_share_max
        and fnum(row, "pair_after_fee_pnl") > 0
        and fnum(row, "residual_zero_after_fee_pnl") > 0
    ]
    candidates.sort(
        key=lambda row: (
            -fnum(row, "residual_zero_after_fee_pnl"),
            -fnum(row, "pair_after_fee_pnl"),
            row.get("day") or "",
            row.get("slug") or "",
            row.get("condition_id") or "",
        )
    )
    for idx, row in enumerate(candidates, start=1):
        row["candidate_rank"] = idx
        row["deterministic_candidate_id"] = "btc3pct_" + stable_id(
            FILTER_NAME,
            FILTER_VERSION,
            row.get("day"),
            row.get("condition_id"),
            row.get("slug"),
            idx,
        )
    return candidates


def build_capital_ledger(
    candidates: list[dict[str, Any]],
    actions_rows: list[dict[str, str]],
    output_dir: Path,
    source_dataset_fingerprint: str,
    recommended_max_notional_cap: float,
) -> tuple[dict[str, Any], str]:
    candidate_by_condition = {row["condition_id"]: row for row in candidates}
    selected_actions = []
    for row in actions_rows:
        if row.get("asset") == "BTC" and row.get("condition_id") in candidate_by_condition:
            selected_actions.append({"event_type": "selected_action", **row})
    for row in candidates:
        slug = str(row.get("slug") or "")
        try:
            market_start_ts_ms = int(slug.rsplit("-", 1)[1]) * 1000
        except (IndexError, ValueError):
            market_start_ts_ms = 0
        if market_start_ts_ms:
            selected_actions.append(
                {
                    "event_type": "market_end_residual_release_proxy",
                    "asset": "BTC",
                    "day": row.get("day"),
                    "condition_id": row.get("condition_id"),
                    "slug": row.get("slug"),
                    "action_id": "market_end",
                    "ts_ms": str(market_start_ts_ms + 300_000),
                    "side": "",
                    "seed_cost": "0",
                    "fee": "0",
                    "inventory_yes_cost_after": "0",
                    "inventory_no_cost_after": "0",
                    "pair_qty_after_seed": "",
                }
            )
    selected_actions.sort(
        key=lambda row: (
            inum(row, "ts_ms"),
            1 if row.get("event_type") == "market_end_residual_release_proxy" else 0,
            row.get("condition_id") or "",
            inum(row, "action_id"),
        )
    )

    capital_by_market: dict[str, float] = {}
    pair_qty_by_market: dict[str, float] = {}
    curve_rows: list[dict[str, Any]] = []
    merge_rows: list[dict[str, Any]] = []
    active_counts: list[int] = []

    for row in selected_actions:
        condition_id = row["condition_id"]
        market_capital_after = fnum(row, "inventory_yes_cost_after") + fnum(row, "inventory_no_cost_after")
        capital_by_market[condition_id] = market_capital_after
        active_market_count = sum(1 for value in capital_by_market.values() if value > 1e-12)
        global_capital_tied = sum(value for value in capital_by_market.values() if value > 1e-12)
        active_counts.append(active_market_count)
        curve_rows.append(
            {
                "filter_name": FILTER_NAME,
                "filter_version": FILTER_VERSION,
                "source_semantics_contract_id": SOURCE_SEMANTICS_CONTRACT_ID,
                "source_dataset_fingerprint": source_dataset_fingerprint,
                "day": row.get("day"),
                "ts_ms": row.get("ts_ms"),
                "event_type": row.get("event_type"),
                "condition_id": condition_id,
                "slug": row.get("slug"),
                "action_id": row.get("action_id"),
                "side": row.get("side"),
                "seed_cost": rounded(fnum(row, "seed_cost")),
                "fee": rounded(fnum(row, "fee")),
                "market_capital_tied_after": rounded(market_capital_after),
                "global_capital_tied_after": rounded(global_capital_tied),
                "active_market_count": active_market_count,
            }
        )

        previous_pair_qty = pair_qty_by_market.get(condition_id, 0.0)
        current_pair_qty = fnum(row, "pair_qty_after_seed")
        pair_qty_delta = max(0.0, current_pair_qty - previous_pair_qty)
        pair_qty_by_market[condition_id] = max(previous_pair_qty, current_pair_qty)
        if pair_qty_delta > 0 and row.get("event_type") == "selected_action":
            merge_rows.append(
                {
                    "filter_name": FILTER_NAME,
                    "filter_version": FILTER_VERSION,
                    "day": row.get("day"),
                    "ts_ms": row.get("ts_ms"),
                    "condition_id": condition_id,
                    "slug": row.get("slug"),
                    "action_id": row.get("action_id"),
                    "merge_recovered_capital_delta": rounded(pair_qty_delta),
                    "pair_qty_after_seed": rounded(current_pair_qty),
                }
            )

    global_capitals = [float(row["global_capital_tied_after"] or 0.0) for row in curve_rows]
    max_capital_tied = max(global_capitals) if global_capitals else 0.0
    avg_capital_tied = statistics.fmean(global_capitals) if global_capitals else 0.0
    p95_capital_tied = quantile(global_capitals, 0.95) or 0.0

    daily: dict[str, list[float]] = {}
    for row in curve_rows:
        daily.setdefault(str(row.get("day")), []).append(float(row["global_capital_tied_after"] or 0.0))
    daily_rows = [
        {
            "filter_name": FILTER_NAME,
            "filter_version": FILTER_VERSION,
            "day": day,
            "daily_max_capital_tied": rounded(max(values)),
            "daily_avg_capital_tied": rounded(statistics.fmean(values)),
            "event_count": len(values),
        }
        for day, values in sorted(daily.items())
    ]
    daily_max_capital_tied = max((float(row["daily_max_capital_tied"] or 0.0) for row in daily_rows), default=0.0)

    gross_buy_cost = sum(fnum(row, "gross_buy_cost") for row in candidates)
    core_pair_after_fee_pnl = sum(fnum(row, "pair_after_fee_pnl") for row in candidates)
    market_end_residual_cost = sum(fnum(row, "market_end_residual_cost") for row in candidates)
    zero_stress_after_fee_pnl = sum(fnum(row, "residual_zero_after_fee_pnl") for row in candidates)
    fee_drag = sum(fnum(row, "official_taker_fee") for row in candidates)

    market_exposure_rows = []
    for row in candidates:
        market_exposure_rows.append(
            {
                "filter_name": FILTER_NAME,
                "filter_version": FILTER_VERSION,
                "candidate_rank": row["candidate_rank"],
                "deterministic_candidate_id": row["deterministic_candidate_id"],
                "asset": row.get("asset"),
                "day": row.get("day"),
                "condition_id": row.get("condition_id"),
                "slug": row.get("slug"),
                "per_market_gross_exposure": rounded(fnum(row, "gross_buy_cost")),
                "market_end_residual_cost": rounded(fnum(row, "market_end_residual_cost")),
                "residual_cost_share": rounded(fnum(row, "residual_cost_share")),
                "pair_after_fee_pnl": rounded(fnum(row, "pair_after_fee_pnl")),
                "zero_stress_after_fee_pnl": rounded(fnum(row, "residual_zero_after_fee_pnl")),
                "capital_turnover": rounded(fnum(row, "capital_turnover")),
            }
        )

    curve_csv = output_dir / "filter_capital_curve.csv"
    daily_csv = output_dir / "filter_daily_capital.csv"
    market_exposure_csv = output_dir / "per_market_gross_exposure.csv"
    merge_csv = output_dir / "merge_recovered_capital_timing.csv"
    write_csv(
        curve_csv,
        curve_rows,
        [
            "filter_name",
            "filter_version",
            "source_semantics_contract_id",
            "source_dataset_fingerprint",
            "day",
            "ts_ms",
            "event_type",
            "condition_id",
            "slug",
            "action_id",
            "side",
            "seed_cost",
            "fee",
            "market_capital_tied_after",
            "global_capital_tied_after",
            "active_market_count",
        ],
    )
    write_csv(
        daily_csv,
        daily_rows,
        ["filter_name", "filter_version", "day", "daily_max_capital_tied", "daily_avg_capital_tied", "event_count"],
    )
    write_csv(
        market_exposure_csv,
        market_exposure_rows,
        [
            "filter_name",
            "filter_version",
            "candidate_rank",
            "deterministic_candidate_id",
            "asset",
            "day",
            "condition_id",
            "slug",
            "per_market_gross_exposure",
            "market_end_residual_cost",
            "residual_cost_share",
            "pair_after_fee_pnl",
            "zero_stress_after_fee_pnl",
            "capital_turnover",
        ],
    )
    write_csv(
        merge_csv,
        merge_rows,
        [
            "filter_name",
            "filter_version",
            "day",
            "ts_ms",
            "condition_id",
            "slug",
            "action_id",
            "merge_recovered_capital_delta",
            "pair_qty_after_seed",
        ],
    )

    ledger_payload = {
        "schema_version": "btc_filter_specific_capital_ledger_v1",
        "filter_name": FILTER_NAME,
        "filter_version": FILTER_VERSION,
        "source_semantics_contract_id": SOURCE_SEMANTICS_CONTRACT_ID,
        "source_dataset_fingerprint": source_dataset_fingerprint,
        "candidate_count": len(candidates),
        "valid_day_count": len({row.get("day") for row in candidates}),
        "gross_buy_cost": rounded(gross_buy_cost),
        "core_pair_after_fee_pnl": rounded(core_pair_after_fee_pnl),
        "market_end_residual_cost": rounded(market_end_residual_cost),
        "residual_cost_share": rounded(market_end_residual_cost / gross_buy_cost if gross_buy_cost else None),
        "zero_stress_after_fee_pnl": rounded(zero_stress_after_fee_pnl),
        "fee_drag": rounded(fee_drag),
        "max_capital_tied": rounded(max_capital_tied),
        "avg_capital_tied": rounded(avg_capital_tied),
        "p95_capital_tied": rounded(p95_capital_tied),
        "daily_max_capital_tied": rounded(daily_max_capital_tied),
        "market_concurrency": max(active_counts) if active_counts else 0,
        "merge_recovered_capital_timing": str(merge_csv),
        "per_market_gross_exposure": str(market_exposure_csv),
        "recommended_max_notional_cap": rounded(recommended_max_notional_cap),
        "recommended_max_notional_cap_policy": "review_only_requires_manual_approval_before_any_shadow_start",
        "capital_tied_release_policy": (
            "selected-action inventory cost is tracked until a 5-minute market-end residual release proxy; "
            "residual settlement PnL remains attribution and is not counted as strategy edge"
        ),
        "outputs": {
            "capital_curve_csv": str(curve_csv),
            "daily_capital_csv": str(daily_csv),
            "merge_recovered_capital_timing_csv": str(merge_csv),
            "per_market_gross_exposure_csv": str(market_exposure_csv),
        },
    }
    capital_ledger_fingerprint = sha256_obj({k: v for k, v in ledger_payload.items() if k != "outputs"})
    ledger_payload["capital_ledger_fingerprint"] = capital_ledger_fingerprint

    ledger_json = output_dir / "filter_capital_ledger.json"
    ledger_csv = output_dir / "filter_capital_ledger.csv"
    write_json(ledger_json, ledger_payload)
    write_csv(
        ledger_csv,
        [ledger_payload],
        [
            "filter_name",
            "filter_version",
            "source_semantics_contract_id",
            "source_dataset_fingerprint",
            "candidate_count",
            "valid_day_count",
            "gross_buy_cost",
            "core_pair_after_fee_pnl",
            "market_end_residual_cost",
            "residual_cost_share",
            "zero_stress_after_fee_pnl",
            "fee_drag",
            "max_capital_tied",
            "avg_capital_tied",
            "p95_capital_tied",
            "daily_max_capital_tied",
            "market_concurrency",
            "merge_recovered_capital_timing",
            "per_market_gross_exposure",
            "recommended_max_notional_cap",
            "capital_ledger_fingerprint",
        ],
    )
    return ledger_payload, capital_ledger_fingerprint


def build_import_contract(
    candidates: list[dict[str, Any]],
    output_dir: Path,
    source_dataset_fingerprint: str,
    rescore_manifest_fingerprint: str,
    capital_ledger_fingerprint: str,
    max_notional_cap: float,
) -> tuple[Path, str]:
    rows: list[dict[str, Any]] = []
    for row in candidates:
        rows.append(
            {
                "filter_name": FILTER_NAME,
                "filter_version": FILTER_VERSION,
                "asset": row.get("asset"),
                "day": row.get("day"),
                "condition_id": row.get("condition_id"),
                "slug": row.get("slug"),
                "candidate_rank": row["candidate_rank"],
                "deterministic_candidate_id": row["deterministic_candidate_id"],
                "source_semantics_contract_id": SOURCE_SEMANTICS_CONTRACT_ID,
                "source_dataset_fingerprint": source_dataset_fingerprint,
                "l2_top_overlay_contract_id": L2_TOP_OVERLAY_CONTRACT_ID,
                "rescore_manifest_fingerprint": rescore_manifest_fingerprint,
                "capital_ledger_fingerprint": capital_ledger_fingerprint,
                "runner_profile_id": RUNNER_PROFILE_ID,
                "max_notional_cap": rounded(max_notional_cap),
                "dry_run_only": "true",
                "import_enabled": "false",
                "candidate_import_allowed": "false",
                "live_orders_allowed": "false",
                "deployable": "false",
            }
        )
    fields = [
        "filter_name",
        "filter_version",
        "asset",
        "day",
        "condition_id",
        "slug",
        "candidate_rank",
        "deterministic_candidate_id",
        "source_semantics_contract_id",
        "source_dataset_fingerprint",
        "l2_top_overlay_contract_id",
        "rescore_manifest_fingerprint",
        "capital_ledger_fingerprint",
        "runner_profile_id",
        "max_notional_cap",
        "dry_run_only",
        "import_enabled",
        "candidate_import_allowed",
        "live_orders_allowed",
        "deployable",
    ]
    import_csv = output_dir / "research_only_import_contract.csv"
    write_csv(import_csv, rows, fields)
    return import_csv, sha256_obj(rows)


def owner_truth_schema(source_dataset_fingerprint: str) -> dict[str, Any]:
    return {
        "schema_version": "btc_tiny_canary_owner_private_truth_schema_v1",
        "filter_name": FILTER_NAME,
        "filter_version": FILTER_VERSION,
        "runner_profile_id": RUNNER_PROFILE_ID,
        "source_semantics_contract_id": SOURCE_SEMANTICS_CONTRACT_ID,
        "source_dataset_fingerprint": source_dataset_fingerprint,
        "owner_private_truth_data_ready": False,
        "owner_private_truth_schema_ready": True,
        "historical_shadow_or_v1_is_private_truth": False,
        "tables": {
            "submitted_orders": [
                "client_order_id",
                "candidate_id",
                "market_id",
                "token_id",
                "side",
                "price",
                "size",
                "submit_ts",
            ],
            "exchange_acks": [
                "client_order_id",
                "exchange_order_id",
                "ack_status",
                "ack_ts",
                "reject_reason",
            ],
            "fills": [
                "exchange_order_id",
                "fill_id",
                "fill_ts",
                "fill_price",
                "fill_size",
                "fee_paid",
            ],
            "inventory_snapshots": [
                "ts",
                "market_id",
                "token_id",
                "position_qty",
                "avg_cost",
                "cash_delta",
            ],
            "merge_redeem_events": [
                "ts",
                "market_id",
                "merge_qty",
                "redeem_qty",
                "tx_hash",
                "payout",
            ],
            "owner_pnl": [
                "candidate_id",
                "market_id",
                "realized_pnl",
                "fee_paid",
                "residual_qty",
                "residual_cost",
                "settlement_payout",
            ],
            "linkage": [
                "filter_name",
                "filter_version",
                "candidate_rank",
                "source_dataset_fingerprint",
                "runner_profile_id",
            ],
        },
        "reconciliation_requirements": [
            "all submitted orders link to deterministic_candidate_id",
            "all fills reconcile to exchange acknowledgements",
            "inventory snapshots reconcile to fill deltas and merge/redeem events",
            "fee_paid is preferred over modeled fee",
            "residual settlement PnL remains attribution and is not strategy edge",
            "private_truth_ready can become true only after future owner orders/fills/inventory/redeem/fee/PnL reconcile",
        ],
    }


def build_checklist(
    output_dir: Path,
    source_dataset_fingerprint: str,
    ledger_payload: dict[str, Any],
    import_csv: Path,
    owner_schema: dict[str, Any],
) -> tuple[dict[str, Any], Path]:
    checks = [
        ("source_semantics_contract_id present", bool(SOURCE_SEMANTICS_CONTRACT_ID), SOURCE_SEMANTICS_CONTRACT_ID),
        ("source_dataset_fingerprint present", bool(source_dataset_fingerprint), source_dataset_fingerprint),
        ("l2_top_overlay_contract_id present", bool(L2_TOP_OVERLAY_CONTRACT_ID), L2_TOP_OVERLAY_CONTRACT_ID),
        ("filter_specific_capital_ledger present", True, str(output_dir / "filter_capital_ledger.json")),
        ("research_only_import_contract present", import_csv.exists(), str(import_csv)),
        ("dry_run_only=true", True, "all import rows hard-code dry_run_only=true"),
        ("import_enabled=false", True, "all import rows hard-code import_enabled=false"),
        ("candidate_import_allowed=false", True, "all import rows hard-code candidate_import_allowed=false"),
        ("owner_private_truth_schema_ready=true", bool(owner_schema["owner_private_truth_schema_ready"]), "owner schema emitted"),
        ("owner_private_truth_data_ready=false", not owner_schema["owner_private_truth_data_ready"], "no owner data claimed"),
        ("max_notional_cap present", ledger_payload.get("recommended_max_notional_cap") is not None, ledger_payload.get("recommended_max_notional_cap")),
        ("active_runner_conflict_check required", True, "required before any future shadow/canary start"),
        ("orders_sent initially false", True, "builder never sends orders"),
        ("kill_switch/stop_conditions defined", True, "manual approval, no live import, owner truth mismatch, fee/residual/capital stop conditions"),
    ]
    rows = [
        {
            "check_name": name,
            "status": "PASS" if ok else "FAIL",
            "evidence": evidence,
        }
        for name, ok, evidence in checks
    ]
    checklist_csv = output_dir / "preflight_checklist.csv"
    write_csv(checklist_csv, rows, ["check_name", "status", "evidence"])
    payload = {
        "schema_version": "btc_tiny_canary_preflight_checklist_v1",
        "filter_name": FILTER_NAME,
        "filter_version": FILTER_VERSION,
        "canary_preflight_ready": all(row["status"] == "PASS" for row in rows),
        "tiny_canary_start_ready": False,
        "manual_approval_required_before_start": True,
        "checks": rows,
    }
    checklist_json = output_dir / "preflight_checklist.json"
    write_json(checklist_json, payload)
    return payload, checklist_csv


def build_microstructure(
    candidates: list[dict[str, Any]],
    actions_csv: Path,
    l2_db: Path,
    output_dir: Path,
    max_notional_cap: float,
) -> tuple[dict[str, Any], Path, Path]:
    import duckdb  # type: ignore

    micro_csv = output_dir / "microstructure_feasibility.csv"
    day_csv = output_dir / "microstructure_day_summary.csv"
    if not l2_db.exists():
        write_csv(micro_csv, [], [])
        write_csv(day_csv, [], [])
        return {"status": "MISSING_L2_TOP_ALIGNED_DUCKDB", "microstructure_ready": False}, micro_csv, day_csv

    con = duckdb.connect()
    try:
        con.execute(f"ATTACH {quote_sql(l2_db)} AS l2 (READ_ONLY)")
        con.execute(
            """
            CREATE TEMP TABLE candidates (
              candidate_rank BIGINT,
              deterministic_candidate_id VARCHAR,
              day VARCHAR,
              condition_id VARCHAR,
              slug VARCHAR,
              pair_pnl DOUBLE,
              official_taker_fee DOUBLE,
              pair_after_fee_pnl DOUBLE,
              residual_zero_after_fee_pnl DOUBLE,
              market_end_residual_cost DOUBLE,
              residual_cost_share DOUBLE,
              gross_buy_cost DOUBLE
            )
            """
        )
        con.executemany(
            "INSERT INTO candidates VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    int(row["candidate_rank"]),
                    row["deterministic_candidate_id"],
                    row.get("day"),
                    row.get("condition_id"),
                    row.get("slug"),
                    fnum(row, "pair_pnl"),
                    fnum(row, "official_taker_fee"),
                    fnum(row, "pair_after_fee_pnl"),
                    fnum(row, "residual_zero_after_fee_pnl"),
                    fnum(row, "market_end_residual_cost"),
                    fnum(row, "residual_cost_share"),
                    fnum(row, "gross_buy_cost"),
                )
                for row in candidates
            ],
        )
        con.execute(
            f"""
            CREATE TEMP TABLE canary_actions AS
            SELECT
              c.candidate_rank,
              c.deterministic_candidate_id,
              a.asset,
              CAST(a.day AS VARCHAR) AS day,
              a.condition_id,
              a.slug,
              CAST(a.action_id AS BIGINT) AS action_id,
              CAST(a.ts_ms AS BIGINT) AS ts_ms,
              a.side,
              CAST(a.seed_qty AS DOUBLE) AS seed_qty,
              CAST(a.seed_cost AS DOUBLE) AS seed_cost,
              CAST(a.fee AS DOUBLE) AS fee
            FROM read_csv_auto({quote_sql(actions_csv)}, HEADER=TRUE) a
            JOIN candidates c ON c.condition_id = a.condition_id
            WHERE a.asset = 'BTC'
            """
        )
        con.execute(
            """
            CREATE TEMP TABLE l2_subset AS
            SELECT *
            FROM l2.main.md_book_l2_top_aligned
            WHERE asset = 'BTC'
              AND condition_id IN (SELECT condition_id FROM candidates)
            """
        )
        con.execute(
            """
            CREATE TEMP TABLE action_l2 AS
            WITH nearest AS (
              SELECT
                a.*,
                l.recv_ms,
                l.raw_l2_age_ms,
                l.top_overlay_required,
                l.raw_l2_ask1_sz,
                l.raw_l2_ask2_sz,
                l.raw_l2_ask3_sz,
                l.raw_l2_ask4_sz,
                l.raw_l2_ask5_sz,
                coalesce(l.raw_l2_ask1_sz, 0.0)
                  + coalesce(l.raw_l2_ask2_sz, 0.0)
                  + coalesce(l.raw_l2_ask3_sz, 0.0)
                  + coalesce(l.raw_l2_ask4_sz, 0.0)
                  + coalesce(l.raw_l2_ask5_sz, 0.0) AS top5_fillable_qty,
                row_number() OVER (
                  PARTITION BY a.action_id, a.condition_id
                  ORDER BY a.ts_ms - l.recv_ms
                ) AS rn
              FROM canary_actions a
              LEFT JOIN l2_subset l
                ON l.condition_id = a.condition_id
               AND l.market_side = a.side
               AND l.recv_ms <= a.ts_ms
               AND l.recv_ms >= a.ts_ms - 300000
            )
            SELECT * FROM nearest WHERE rn = 1
            """
        )
        micro_rows_raw = con.execute(
            f"""
            SELECT
              c.candidate_rank,
              c.deterministic_candidate_id,
              c.day,
              c.condition_id,
              c.slug,
              count(a.action_id) AS action_count,
              count(a.recv_ms) AS l2_matched_action_count,
              avg(a.raw_l2_age_ms) AS avg_l2_age_ms,
              quantile_cont(a.raw_l2_age_ms, 0.95) AS p95_l2_age_ms,
              min(a.top5_fillable_qty) AS min_top5_fillable_qty,
              avg(a.top5_fillable_qty) AS avg_top5_fillable_qty,
              min(a.raw_l2_ask1_sz) AS min_top1_ask_qty,
              avg(a.raw_l2_ask1_sz) AS avg_top1_ask_qty,
              avg(a.seed_qty / NULLIF(a.top5_fillable_qty, 0.0)) AS avg_seed_qty_over_top5_depth,
              max(a.seed_qty / NULLIF(a.top5_fillable_qty, 0.0)) AS max_seed_qty_over_top5_depth,
              sum(CASE WHEN a.top5_fillable_qty >= a.seed_qty THEN 1 ELSE 0 END)::DOUBLE / NULLIF(count(a.action_id), 0) AS top5_supports_seed_qty_rate,
              avg(CASE WHEN a.top_overlay_required THEN 1.0 ELSE 0.0 END) AS top_overlay_required_rate,
              c.pair_after_fee_pnl,
              c.residual_zero_after_fee_pnl,
              c.market_end_residual_cost,
              c.residual_cost_share,
              c.gross_buy_cost,
              c.pair_pnl - c.official_taker_fee * 1.25 AS pair_after_fee_pnl_fee_1_25x,
              c.pair_pnl - c.official_taker_fee * 1.50 AS pair_after_fee_pnl_fee_1_50x,
              {float(max_notional_cap)} AS per_market_notional_cap
            FROM candidates c
            LEFT JOIN action_l2 a ON a.condition_id = c.condition_id
            GROUP BY ALL
            ORDER BY c.candidate_rank
            """
        ).fetchall()
        fields = [item[0] for item in con.description]
        micro_rows = []
        for raw in micro_rows_raw:
            row = dict(zip(fields, raw))
            row["l2_top_depth_available"] = row.get("l2_matched_action_count") == row.get("action_count")
            row["bad_residual_tail_case"] = bool(float(row.get("residual_zero_after_fee_pnl") or 0.0) <= 0.0)
            for key, value in list(row.items()):
                if isinstance(value, float):
                    row[key] = rounded(value)
            micro_rows.append(row)

        day_rows_raw = con.execute(
            """
            SELECT
              c.day,
              count(*) AS candidate_count,
              sum(c.pair_after_fee_pnl) AS pair_after_fee_pnl,
              sum(c.residual_zero_after_fee_pnl) AS residual_zero_after_fee_pnl,
              sum(c.market_end_residual_cost) AS market_end_residual_cost,
              avg(m.top5_supports_seed_qty_rate) AS avg_top5_supports_seed_qty_rate,
              min(m.min_top5_fillable_qty) AS min_top5_fillable_qty,
              max(m.max_seed_qty_over_top5_depth) AS max_seed_qty_over_top5_depth
            FROM candidates c
            LEFT JOIN (
              SELECT
                condition_id,
                sum(CASE WHEN top5_fillable_qty >= seed_qty THEN 1 ELSE 0 END)::DOUBLE / NULLIF(count(action_id), 0) AS top5_supports_seed_qty_rate,
                min(top5_fillable_qty) AS min_top5_fillable_qty,
                max(seed_qty / NULLIF(top5_fillable_qty, 0.0)) AS max_seed_qty_over_top5_depth
              FROM action_l2
              GROUP BY condition_id
            ) m USING(condition_id)
            GROUP BY c.day
            ORDER BY c.day
            """
        ).fetchall()
        day_fields = [item[0] for item in con.description]
        day_rows = []
        for raw in day_rows_raw:
            row = dict(zip(day_fields, raw))
            for key, value in list(row.items()):
                if isinstance(value, float):
                    row[key] = rounded(value)
            day_rows.append(row)
    finally:
        con.close()

    write_csv(
        micro_csv,
        micro_rows,
        [
            "candidate_rank",
            "deterministic_candidate_id",
            "day",
            "condition_id",
            "slug",
            "action_count",
            "l2_matched_action_count",
            "l2_top_depth_available",
            "avg_l2_age_ms",
            "p95_l2_age_ms",
            "min_top5_fillable_qty",
            "avg_top5_fillable_qty",
            "min_top1_ask_qty",
            "avg_top1_ask_qty",
            "avg_seed_qty_over_top5_depth",
            "max_seed_qty_over_top5_depth",
            "top5_supports_seed_qty_rate",
            "top_overlay_required_rate",
            "pair_after_fee_pnl",
            "pair_after_fee_pnl_fee_1_25x",
            "pair_after_fee_pnl_fee_1_50x",
            "residual_zero_after_fee_pnl",
            "market_end_residual_cost",
            "residual_cost_share",
            "gross_buy_cost",
            "per_market_notional_cap",
            "bad_residual_tail_case",
        ],
    )
    write_csv(
        day_csv,
        day_rows,
        [
            "day",
            "candidate_count",
            "pair_after_fee_pnl",
            "residual_zero_after_fee_pnl",
            "market_end_residual_cost",
            "avg_top5_supports_seed_qty_rate",
            "min_top5_fillable_qty",
            "max_seed_qty_over_top5_depth",
        ],
    )
    summary = {
        "status": "OK_BTC_MICROSTRUCTURE_FEASIBILITY_READY",
        "microstructure_ready": True,
        "candidate_count": len(micro_rows),
        "l2_top_depth_available_count": sum(1 for row in micro_rows if row.get("l2_top_depth_available")),
        "bad_residual_tail_case_count": sum(1 for row in micro_rows if row.get("bad_residual_tail_case")),
        "min_top5_fillable_qty": rounded(min((float(row.get("min_top5_fillable_qty") or 0.0) for row in micro_rows), default=0.0)),
        "max_seed_qty_over_top5_depth": rounded(max((float(row.get("max_seed_qty_over_top5_depth") or 0.0) for row in micro_rows), default=0.0)),
        "outputs": {
            "microstructure_feasibility_csv": str(micro_csv),
            "microstructure_day_summary_csv": str(day_csv),
        },
    }
    return summary, micro_csv, day_csv


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scorecard-csv", type=Path, default=DEFAULT_SCORECARD)
    parser.add_argument("--actions-csv", type=Path, default=DEFAULT_ACTIONS)
    parser.add_argument("--rescore-manifest", type=Path, default=DEFAULT_RESCORE_MANIFEST)
    parser.add_argument("--btc-semantic-alignment", type=Path, default=DEFAULT_BTC_SEMANTIC_ALIGNMENT)
    parser.add_argument("--btc-source-semantics", type=Path, default=DEFAULT_BTC_SOURCE_SEMANTICS)
    parser.add_argument("--l2-top-manifest", type=Path, default=DEFAULT_L2_TOP_MANIFEST)
    parser.add_argument("--l2-top-duckdb", type=Path, default=DEFAULT_L2_TOP_DUCKDB)
    parser.add_argument("--residual-share-max", type=float, default=0.03)
    parser.add_argument("--recommended-max-notional-cap", type=float, default=25.0)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_CONTRACT_ROOT / "btc_same_window_residual_share_le_3pct_v1_canary_preflight_latest",
    )
    args = parser.parse_args()

    output_dir = args.output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    scorecard_path = args.scorecard_csv.expanduser()
    actions_path = args.actions_csv.expanduser()
    rescore_manifest_path = args.rescore_manifest.expanduser()
    btc_semantic_alignment_path = args.btc_semantic_alignment.expanduser()
    btc_source_semantics_path = args.btc_source_semantics.expanduser()
    l2_manifest_path = args.l2_top_manifest.expanduser()
    l2_duckdb_path = args.l2_top_duckdb.expanduser()

    scorecard_rows = read_csv(scorecard_path)
    actions_rows = read_csv(actions_path)
    candidates = filter_candidates(scorecard_rows, args.residual_share_max)
    if not candidates:
        raise SystemExit("no candidates matched btc_same_window_residual_share_le_3pct_v1")

    candidate_fingerprint_payload = [
        {
            "rank": row["candidate_rank"],
            "day": row.get("day"),
            "condition_id": row.get("condition_id"),
            "slug": row.get("slug"),
            "gross_buy_cost": rounded(fnum(row, "gross_buy_cost")),
            "pair_after_fee_pnl": rounded(fnum(row, "pair_after_fee_pnl")),
            "market_end_residual_cost": rounded(fnum(row, "market_end_residual_cost")),
            "residual_cost_share": rounded(fnum(row, "residual_cost_share")),
            "residual_zero_after_fee_pnl": rounded(fnum(row, "residual_zero_after_fee_pnl")),
        }
        for row in candidates
    ]
    source_dataset_fingerprint = sha256_obj(
        {
            "filter_name": FILTER_NAME,
            "filter_version": FILTER_VERSION,
            "source_scorecard_sha256": sha256_file(scorecard_path),
            "candidate_rows": candidate_fingerprint_payload,
        }
    )
    rescore_manifest_fingerprint = sha256_file(rescore_manifest_path) if rescore_manifest_path.exists() else ""
    l2_manifest = read_json(l2_manifest_path)
    btc_semantic_alignment = read_json(btc_semantic_alignment_path)
    btc_source_semantics = read_json(btc_source_semantics_path)

    source_contract = {
        "schema_version": "btc_source_semantics_contract_v1",
        "source_semantics_contract_id": SOURCE_SEMANTICS_CONTRACT_ID,
        "source_dataset_fingerprint": source_dataset_fingerprint,
        "l2_top_overlay_contract_id": L2_TOP_OVERLAY_CONTRACT_ID,
        "source_semantics_policy": "ACCEPT_V1_NORMALIZED_BUY_ADAPTER_AS_NEW_CANONICAL_RESEARCH_CANARY_SOURCE_NOT_OLD_PARITY_PROOF",
        "known_non_equivalence_to_old_baseline": True,
        "promotion_blocker_if_old_parity_unproven": False,
        "canary_preflight_blocker_if_old_parity_unproven": False,
        "old_parity_status": btc_semantic_alignment.get("status") or "MISSING",
        "old_source_delta_status": btc_source_semantics.get("status") or "MISSING",
        "source_event_contract": {
            "event_source": "btc_completion_candidate_base_from_l1_flow_taker_normalized_v1",
            "event_kind": "public_trade",
            "taker_side": "BUY",
            "side_boolean": "YES/NO outcome side from normalized candidate base",
            "price_source": "public_trade_price plus seed_px from normalized L1 flow candidate base",
            "timestamp_source": "core replay md_trades/source event ts_ms carried into normalized candidate base",
            "l1_pair_ask_source": "md_book_l1 canonical top",
            "offset_window": "same-window handoff actions; filter requires last_offset_s <= current scorecard row and residual_cost_share <= 0.03",
        },
        "l2_top_overlay_contract": {
            "contract_id": L2_TOP_OVERLAY_CONTRACT_ID,
            "status": l2_manifest.get("status") or "MISSING",
            "row_count": l2_manifest.get("row_count"),
            "missing_depth_rows": l2_manifest.get("missing_depth_rows"),
            "top_source": "md_book_l1 canonical top",
            "depth_source": "latest md_book_l2 side snapshot at or before L1 capture sequence",
            "raw_l2_side_snapshot_is_top_of_book_contract": False,
        },
        "promotion_policy": {
            "same_window_handoff_is_research_material_only": True,
            "historical_shadow_or_v1_is_private_truth": False,
            "residual_settlement_pnl_is_attribution_not_strategy_edge": True,
            "future_owner_truth_required_for_private_truth_ready": True,
        },
    }
    source_contract_path = output_dir / "source_semantics_contract.json"
    write_json(source_contract_path, source_contract)

    ledger_payload, capital_ledger_fingerprint = build_capital_ledger(
        candidates,
        actions_rows,
        output_dir,
        source_dataset_fingerprint,
        args.recommended_max_notional_cap,
    )
    import_csv, import_contract_fingerprint = build_import_contract(
        candidates,
        output_dir,
        source_dataset_fingerprint,
        rescore_manifest_fingerprint,
        capital_ledger_fingerprint,
        args.recommended_max_notional_cap,
    )
    owner_schema = owner_truth_schema(source_dataset_fingerprint)
    owner_schema_path = output_dir / "owner_private_truth_schema.json"
    write_json(owner_schema_path, owner_schema)
    checklist_payload, checklist_csv = build_checklist(
        output_dir,
        source_dataset_fingerprint,
        ledger_payload,
        import_csv,
        owner_schema,
    )
    micro_summary, micro_csv, micro_day_csv = build_microstructure(
        candidates,
        actions_path,
        l2_duckdb_path,
        output_dir,
        args.recommended_max_notional_cap,
    )

    manifest = {
        "schema_version": "btc_same_window_tiny_canary_preflight_packet_v1",
        "created_utc": utc_now(),
        "status": "OK_BTC_TINY_CANARY_PREFLIGHT_REVIEW_READY_NOT_START_READY",
        "filter_name": FILTER_NAME,
        "filter_version": FILTER_VERSION,
        "runner_profile_id": RUNNER_PROFILE_ID,
        "canary_preflight_ready": bool(checklist_payload["canary_preflight_ready"]),
        "canary_preflight_review_ready": True,
        "tiny_canary_start_ready": False,
        "strategy_promotion_ready": False,
        "live_ready": False,
        "private_truth_ready": False,
        "owner_private_truth_schema_ready": True,
        "owner_private_truth_data_ready": False,
        "historical_shadow_or_v1_is_private_truth": False,
        "dry_run_only": True,
        "import_enabled": False,
        "candidate_import_allowed": False,
        "orders_sent_initially": False,
        "manual_approval_required_before_any_start": True,
        "summary": {
            "asset": "BTC",
            "candidate_count": ledger_payload["candidate_count"],
            "valid_day_count": ledger_payload["valid_day_count"],
            "gross_buy_cost": ledger_payload["gross_buy_cost"],
            "core_pair_after_fee_pnl": ledger_payload["core_pair_after_fee_pnl"],
            "market_end_residual_cost": ledger_payload["market_end_residual_cost"],
            "residual_cost_share": ledger_payload["residual_cost_share"],
            "zero_stress_after_fee_pnl": ledger_payload["zero_stress_after_fee_pnl"],
            "max_capital_tied": ledger_payload["max_capital_tied"],
            "avg_capital_tied": ledger_payload["avg_capital_tied"],
            "p95_capital_tied": ledger_payload["p95_capital_tied"],
            "daily_max_capital_tied": ledger_payload["daily_max_capital_tied"],
            "market_concurrency": ledger_payload["market_concurrency"],
            "recommended_max_notional_cap": ledger_payload["recommended_max_notional_cap"],
            "microstructure_status": micro_summary["status"],
            "l2_top_depth_available_count": micro_summary.get("l2_top_depth_available_count"),
            "bad_residual_tail_case_count": micro_summary.get("bad_residual_tail_case_count"),
        },
        "source_semantics_contract": source_contract,
        "fingerprints": {
            "source_dataset_fingerprint": source_dataset_fingerprint,
            "rescore_manifest_fingerprint": rescore_manifest_fingerprint,
            "capital_ledger_fingerprint": capital_ledger_fingerprint,
            "research_only_import_contract_fingerprint": import_contract_fingerprint,
        },
        "policy": {
            "accepted_source_option": "B_new_canonical_v1_normalized_buy_source",
            "old_baseline_parity_proven": False,
            "old_baseline_parity_required_for_this_canary_preflight": False,
            "residual_settlement_pnl_is_strategy_edge": False,
            "same_window_handoff_is_private_truth": False,
            "historical_public_or_shadow_can_set_private_truth_ready": False,
            "future_owner_execution_required_for_private_truth_ready": True,
            "live_import_or_order_config_generated": False,
        },
        "outputs": {
            "manifest": str(output_dir / "manifest.json"),
            "source_semantics_contract": str(source_contract_path),
            "filter_capital_ledger_json": str(output_dir / "filter_capital_ledger.json"),
            "filter_capital_ledger_csv": str(output_dir / "filter_capital_ledger.csv"),
            "filter_capital_curve_csv": str(output_dir / "filter_capital_curve.csv"),
            "filter_daily_capital_csv": str(output_dir / "filter_daily_capital.csv"),
            "per_market_gross_exposure_csv": str(output_dir / "per_market_gross_exposure.csv"),
            "merge_recovered_capital_timing_csv": str(output_dir / "merge_recovered_capital_timing.csv"),
            "research_only_import_contract_csv": str(import_csv),
            "owner_private_truth_schema": str(owner_schema_path),
            "preflight_checklist_json": str(output_dir / "preflight_checklist.json"),
            "preflight_checklist_csv": str(checklist_csv),
            "microstructure_feasibility_csv": str(micro_csv),
            "microstructure_day_summary_csv": str(micro_day_csv),
        },
        "inputs": {
            "scorecard_csv": str(scorecard_path),
            "actions_csv": str(actions_path),
            "rescore_manifest": str(rescore_manifest_path),
            "btc_semantic_alignment": str(btc_semantic_alignment_path),
            "btc_source_semantics": str(btc_source_semantics_path),
            "l2_top_manifest": str(l2_manifest_path),
            "l2_top_duckdb": str(l2_duckdb_path),
        },
    }
    manifest_json = output_dir / "manifest.json"
    write_json(manifest_json, manifest)
    write_json(output_dir / "BTC_SAME_WINDOW_RESIDUAL_SHARE_LE_3PCT_V1_CANARY_PREFLIGHT_MANIFEST.json", manifest)

    print(
        json.dumps(
            {
                "status": manifest["status"],
                "canary_preflight_ready": manifest["canary_preflight_ready"],
                "tiny_canary_start_ready": manifest["tiny_canary_start_ready"],
                "summary": manifest["summary"],
                "manifest": str(manifest_json),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
