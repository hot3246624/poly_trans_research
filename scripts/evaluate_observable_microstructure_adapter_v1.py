#!/usr/bin/env python3
"""Evaluate public no-submit observable microstructure evidence.

This is a Backtest V1 research/preflight adapter. It consumes the strategy-side
collector contract plus a no-submit public/orderbook run packet and emits a
fail-closed gate. It never promotes public book observations into owner private
truth, live readiness, deployability, or strategy promotion.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
from typing import Any


DEFAULT_DATA_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
DEFAULT_CONTRACT_ROOT = DEFAULT_DATA_ROOT / "derived/contract_examples"
DEFAULT_LOCALAGG_ROOT = Path("/Users/hot/web3Scientist/pm_as_ofi-localagg")
DEFAULT_CONTRACT = (
    DEFAULT_LOCALAGG_ROOT
    / "data/exports/research_v4_book_touch_first_collector_contract_packet_20260617T_v4_observable"
    / "collector_contract.json"
)
DEFAULT_INPUT_DIR = (
    DEFAULT_LOCALAGG_ROOT
    / "data/exports/research_v3_no_submit_public_orderbook_remote_20260617T040342Z"
)
DEFAULT_OUTPUT_DIR = DEFAULT_CONTRACT_ROOT / "observable_microstructure_adapter_v1_latest"

STATUS_OK = "OK_RESEARCH_OBSERVABLE_MICROSTRUCTURE_READY_PROMOTION_BLOCKED_PRIVATE_TRUTH"
STATUS_BLOCKED = "BLOCKED_OBSERVABLE_MICROSTRUCTURE_ADAPTER_V1_FAIL_CLOSED"

NORMALIZED_FIELDS = [
    "adapter_kind",
    "evidence_level",
    "source_packet_hash",
    "shadow_run_tag",
    "candidate_id",
    "lane_id",
    "edge",
    "market_slug",
    "condition_id",
    "asset",
    "market_start_ts_ms",
    "decision_ts_ms",
    "offset_s",
    "side",
    "token_id",
    "trigger_source",
    "trigger_reason",
    "intended_limit_price",
    "intended_order_qty",
    "best_bid",
    "best_ask",
    "top5_bid_depth",
    "top5_ask_depth",
    "queue_ahead_proxy_qty",
    "fillable_qty_proxy",
    "pair_cost_proxy_at_decision",
    "hypothetical_fill_qty",
    "hypothetical_non_fill_qty",
    "market_fill_retention_flag",
    "qty_fill_conversion",
    "residual_qty_proxy",
    "residual_cost_proxy",
    "fee_after_pnl_proxy",
    "non_claims.private_truth",
    "non_claims.order_execution",
]
THRESHOLD_FIELDS = ["threshold_name", "required", "observed", "passed", "severity", "detail"]
STOP_EVENT_FIELDS = ["event_name", "severity", "scope", "row_index", "market_slug", "candidate_id", "detail"]

TRUE_VALUES = {"1", "true", "yes", "y", "t"}
FALSE_VALUES = {"0", "false", "no", "n", "f", ""}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_csv(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    if not path.exists():
        return [], []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader), list(reader.fieldnames or [])


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def stable_hash(payload: Any) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def first_existing(input_dir: Path, names: tuple[str, ...]) -> Path:
    for name in names:
        path = input_dir / name
        if path.exists():
            return path
    return input_dir / names[0]


def fnum(value: Any, default: float | None = None) -> float | None:
    if value in (None, ""):
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(out) or math.isinf(out):
        return default
    return out


def as_bool(value: Any) -> bool | None:
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    if text in TRUE_VALUES:
        return True
    if text in FALSE_VALUES:
        return False
    return None


def bool_bad_true(value: Any) -> bool:
    return as_bool(value) is True


def add_threshold(
    rows: list[dict[str, Any]],
    name: str,
    required: Any,
    observed: Any,
    passed: bool,
    detail: str,
    severity: str = "fail",
) -> None:
    rows.append(
        {
            "threshold_name": name,
            "required": required,
            "observed": observed,
            "passed": str(bool(passed)).lower(),
            "severity": "" if passed else severity,
            "detail": detail,
        }
    )


def add_stop(
    rows: list[dict[str, Any]],
    event_name: str,
    detail: str,
    severity: str = "stop",
    scope: str = "global",
    row_index: Any = "",
    market_slug: Any = "",
    candidate_id: Any = "",
) -> None:
    rows.append(
        {
            "event_name": event_name,
            "severity": severity,
            "scope": scope,
            "row_index": row_index,
            "market_slug": market_slug,
            "candidate_id": candidate_id,
            "detail": detail,
        }
    )


def exact_schema(observed: list[str], expected: list[str]) -> tuple[bool, list[str], list[str]]:
    missing = [field for field in expected if field not in observed]
    extra = [field for field in observed if field not in expected]
    return observed == expected, missing, extra


def non_claims_false(payload: dict[str, Any]) -> tuple[bool, list[str]]:
    non_claims = payload.get("non_claims") or {}
    bad: list[str] = []
    for key in (
        "private_truth",
        "order_execution",
        "maker_fill_truth",
        "strategy_promotion",
        "live",
        "deployable",
        "canary",
    ):
        if as_bool(non_claims.get(key)) is True:
            bad.append(f"non_claims.{key}")
    for key in ("private_truth_ready", "strategy_promotion_ready", "live_ready", "live_orders_allowed", "deployable"):
        if as_bool(payload.get(key)) is True:
            bad.append(key)
    return not bad, bad


def markets_with_both_tokens_first120(book_rows: list[dict[str, Any]]) -> int:
    sides_by_market: dict[str, set[str]] = {}
    for row in book_rows:
        offset = fnum(row.get("offset_s"))
        if offset is None or offset < 0 or offset >= 120:
            continue
        market = str(row.get("market_slug") or row.get("condition_id") or "")
        side = str(row.get("outcome_side") or "").upper()
        token = str(row.get("token_id") or "")
        if not market or side not in {"YES", "NO"} or not token:
            continue
        sides_by_market.setdefault(market, set()).add(side)
    return sum(1 for sides in sides_by_market.values() if {"YES", "NO"} <= sides)


def choose_metric(scorecard: dict[str, Any], keys: tuple[str, ...], fallback: float | None = None) -> float | None:
    for key in keys:
        value = fnum(scorecard.get(key))
        if value is not None:
            return value
    return fallback


def normalize_candidate_rows(
    rows: list[dict[str, Any]],
    evidence_level: str,
    source_packet_hash: str,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        out = {
            "adapter_kind": "runtime_no_submit_observable_adapter_v1",
            "evidence_level": evidence_level,
            "source_packet_hash": source_packet_hash,
            "shadow_run_tag": row.get("shadow_run_tag", ""),
            "candidate_id": row.get("candidate_id") or row.get("client_order_id") or row.get("intended_client_order_id") or "",
            "lane_id": row.get("lane_id", ""),
            "edge": row.get("edge", ""),
            "market_slug": row.get("market_slug", ""),
            "condition_id": row.get("condition_id", ""),
            "asset": row.get("asset", ""),
            "market_start_ts_ms": row.get("market_start_ts_ms", ""),
            "decision_ts_ms": row.get("decision_ts_ms") or row.get("observation_ts_ms") or "",
            "offset_s": row.get("offset_s", ""),
            "side": row.get("side", ""),
            "token_id": row.get("token_id", ""),
            "trigger_source": row.get("trigger_source") or row.get("lane_id") or "public_orderbook_no_submit",
            "trigger_reason": row.get("trigger_reason", ""),
            "intended_limit_price": row.get("intended_limit_price") or row.get("limit_price") or "",
            "intended_order_qty": row.get("intended_order_qty") or row.get("order_qty") or "",
            "best_bid": row.get("best_bid", ""),
            "best_ask": row.get("best_ask", ""),
            "top5_bid_depth": row.get("top5_bid_depth", ""),
            "top5_ask_depth": row.get("top5_ask_depth", ""),
            "queue_ahead_proxy_qty": row.get("queue_ahead_proxy_qty") or row.get("queue_proxy_open") or "",
            "fillable_qty_proxy": row.get("fillable_qty_proxy", ""),
            "pair_cost_proxy_at_decision": row.get("pair_cost_proxy") or row.get("pair_cost_at_decision") or "",
            "hypothetical_fill_qty": row.get("hypothetical_fill_qty") or row.get("filled_qty") or "",
            "hypothetical_non_fill_qty": row.get("hypothetical_non_fill_qty", ""),
            "market_fill_retention_flag": row.get("market_fill_retention_flag") or row.get("public_touch_seen") or "",
            "qty_fill_conversion": row.get("qty_fill_conversion") or row.get("public_touch_to_own_fill_conversion") or "",
            "residual_qty_proxy": row.get("residual_qty_proxy") or row.get("residual_cost_rate") or "",
            "residual_cost_proxy": row.get("residual_cost_proxy") or row.get("residual_cost") or "",
            "fee_after_pnl_proxy": row.get("fee_after_pnl_proxy") or row.get("realized_maker_edge_after_fees") or "",
            "non_claims.private_truth": "false",
            "non_claims.order_execution": "false",
        }
        normalized.append(out)
    return normalized


def summarize(
    contract: dict[str, Any],
    scorecard: dict[str, Any],
    candidate_rows: list[dict[str, Any]],
    book_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    discovered = choose_metric(scorecard, ("markets_discovered", "discovered_markets"))
    candidate_markets = len({row.get("market_slug") for row in candidate_rows if row.get("market_slug")})
    filled_markets = len(
        {
            row.get("market_slug")
            for row in candidate_rows
            if row.get("market_slug") and (fnum(row.get("hypothetical_fill_qty"), 0.0) or 0.0) > 0
        }
    )
    intended_qty = sum(fnum(row.get("intended_order_qty"), fnum(row.get("order_qty"), 0.0)) or 0.0 for row in candidate_rows)
    filled_qty = sum(
        fnum(row.get("hypothetical_fill_qty"), fnum(row.get("filled_qty"), 0.0)) or 0.0 for row in candidate_rows
    )
    nonfill_qty = sum(fnum(row.get("hypothetical_non_fill_qty"), 0.0) or 0.0 for row in candidate_rows)
    pair_cost_values = [
        value
        for value in (
            fnum(row.get("pair_cost_proxy"), fnum(row.get("pair_cost_at_decision")))
            for row in candidate_rows
        )
        if value is not None
    ]
    fee_after_values = [
        value
        for value in (
            fnum(row.get("fee_after_pnl_proxy"), fnum(row.get("realized_maker_edge_after_fees")))
            for row in candidate_rows
        )
        if value is not None
    ]
    observed_markets = choose_metric(
        scorecard,
        ("markets_with_both_tokens_first120",),
        float(markets_with_both_tokens_first120(book_rows)),
    )
    markets_discovered = discovered if discovered is not None else float(candidate_markets)
    scorecard_filled_markets = fnum(scorecard.get("hypothetical_filled_markets"))
    intent_coverage = choose_metric(
        scorecard,
        ("intent_market_coverage_over_discovered",),
        (candidate_markets / markets_discovered) if markets_discovered else None,
    )
    filled_coverage = choose_metric(
        scorecard,
        ("filled_market_coverage_over_discovered",),
        (scorecard_filled_markets / markets_discovered)
        if scorecard_filled_markets is not None and markets_discovered
        else (filled_markets / markets_discovered)
        if markets_discovered
        else None,
    )
    retention = choose_metric(
        scorecard,
        ("market_fill_retention",),
        (filled_markets / candidate_markets) if candidate_markets else None,
    )
    conversion = choose_metric(
        scorecard,
        ("qty_fill_conversion",),
        (filled_qty / intended_qty) if intended_qty else None,
    )
    pair_cost = choose_metric(
        scorecard,
        ("avg_pair_cost_proxy", "pair_cost_proxy"),
        (sum(pair_cost_values) / len(pair_cost_values)) if pair_cost_values else None,
    )
    residual = choose_metric(
        scorecard,
        ("residual_qty_proxy", "residual_proxy"),
        (nonfill_qty / intended_qty) if intended_qty else None,
    )
    fee_after = choose_metric(
        scorecard,
        ("fee_after_pnl_proxy",),
        sum(fee_after_values) if fee_after_values else None,
    )
    return {
        "collector_version": scorecard.get("collector_version") or contract.get("collector_version"),
        "evidence_level": scorecard.get("evidence_level")
        or contract.get("evidence_level")
        or "review_only_no_submit_public_orderbook_observation",
        "markets_discovered": None if markets_discovered is None else int(markets_discovered),
        "book_snapshot_rows": int(scorecard.get("book_snapshot_rows") or len(book_rows)),
        "markets_with_both_tokens_first120": None if observed_markets is None else int(observed_markets),
        "candidate_rows": int(scorecard.get("candidate_rows") or len(candidate_rows)),
        "candidate_market_count": candidate_markets,
        "filled_market_count": int(scorecard.get("hypothetical_filled_markets") or filled_markets),
        "intent_market_coverage_over_discovered": None if intent_coverage is None else round(intent_coverage, 6),
        "filled_market_coverage_over_discovered": None if filled_coverage is None else round(filled_coverage, 6),
        "market_fill_retention": None if retention is None else round(retention, 6),
        "qty_fill_conversion": None if conversion is None else round(conversion, 6),
        "avg_pair_cost_proxy": None if pair_cost is None else round(pair_cost, 6),
        "residual_qty_proxy": None if residual is None else round(residual, 6),
        "fee_after_pnl_proxy": None if fee_after is None else round(fee_after, 6),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--book-snapshot-csv", type=Path)
    parser.add_argument("--candidate-csv", type=Path)
    parser.add_argument("--run-scorecard", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    input_dir = args.input_dir.expanduser()
    contract_path = args.contract.expanduser()
    book_snapshot_csv = (
        args.book_snapshot_csv.expanduser()
        if args.book_snapshot_csv
        else first_existing(input_dir, ("book_snapshot.csv", "book_snapshots.csv", "normalized_book_snapshots.csv"))
    )
    candidate_csv = (
        args.candidate_csv.expanduser()
        if args.candidate_csv
        else first_existing(input_dir, ("book_touch_candidate.csv", "book_touch_candidates.csv", "no_submit_shadow_validator_like.csv"))
    )
    run_scorecard_path = (
        args.run_scorecard.expanduser()
        if args.run_scorecard
        else first_existing(input_dir, ("scorecard.json", "remote_scorecard.json"))
    )

    contract = read_json(contract_path)
    scorecard = read_json(run_scorecard_path)
    book_rows, book_columns = read_csv(book_snapshot_csv)
    candidate_rows, candidate_columns = read_csv(candidate_csv)
    expected_book_columns = list(((contract.get("schemas") or {}).get("book_snapshot_csv")) or [])
    expected_candidate_columns = list(((contract.get("schemas") or {}).get("book_touch_candidate_csv")) or [])
    expected_scorecard_fields = list(((contract.get("schemas") or {}).get("run_scorecard_required_fields")) or [])
    gates = contract.get("gates") or {}

    thresholds: list[dict[str, Any]] = []
    stops: list[dict[str, Any]] = []

    add_threshold(
        thresholds,
        "collector_contract_present",
        str(contract_path),
        "present" if contract_path.exists() else "missing",
        bool(contract),
        "strategy-side collector contract must be present",
    )
    add_threshold(
        thresholds,
        "run_scorecard_present",
        str(run_scorecard_path),
        "present" if run_scorecard_path.exists() else "missing",
        bool(scorecard),
        "run scorecard must be present",
    )
    add_threshold(
        thresholds,
        "book_snapshot_csv_present",
        str(book_snapshot_csv),
        "present" if book_snapshot_csv.exists() else "missing",
        book_snapshot_csv.exists(),
        "normalized first-120s book snapshot CSV is required",
    )
    add_threshold(
        thresholds,
        "book_touch_candidate_csv_present",
        str(candidate_csv),
        "present" if candidate_csv.exists() else "missing",
        candidate_csv.exists(),
        "derived book-touch candidate CSV is required",
    )

    book_exact, book_missing, book_extra = exact_schema(book_columns, expected_book_columns)
    candidate_exact, candidate_missing, candidate_extra = exact_schema(candidate_columns, expected_candidate_columns)
    add_threshold(
        thresholds,
        "book_snapshot_csv_schema_exact",
        f"{len(expected_book_columns)} contract columns",
        "exact" if book_exact else f"missing={book_missing} extra={book_extra}",
        bool(expected_book_columns) and book_exact,
        "book snapshot CSV must exactly match collector_contract.schemas.book_snapshot_csv",
    )
    add_threshold(
        thresholds,
        "book_touch_candidate_csv_schema_exact",
        f"{len(expected_candidate_columns)} contract columns",
        "exact" if candidate_exact else f"missing={candidate_missing} extra={candidate_extra}",
        bool(expected_candidate_columns) and candidate_exact,
        "candidate CSV must exactly match collector_contract.schemas.book_touch_candidate_csv",
    )

    missing_scorecard_fields = [field for field in expected_scorecard_fields if field not in scorecard]
    add_threshold(
        thresholds,
        "run_scorecard_required_fields_present",
        ",".join(expected_scorecard_fields),
        "all_present" if not missing_scorecard_fields else ",".join(missing_scorecard_fields),
        bool(expected_scorecard_fields) and not missing_scorecard_fields,
        "run scorecard must expose every contract-required aggregate field",
    )

    contract_nonclaims_ok, contract_bad_claims = non_claims_false(contract)
    scorecard_nonclaims_ok, scorecard_bad_claims = non_claims_false(scorecard)
    add_threshold(
        thresholds,
        "collector_contract_non_claims_false",
        "private/order/live/deploy/promotion claims all false",
        "ok" if contract_nonclaims_ok else ",".join(contract_bad_claims),
        contract_nonclaims_ok,
        "contract cannot authorize or claim private/live/deploy/promotion readiness",
    )
    add_threshold(
        thresholds,
        "run_scorecard_non_claims_false",
        "private/order/live/deploy/promotion claims all false",
        "ok" if scorecard_nonclaims_ok else ",".join(scorecard_bad_claims),
        scorecard_nonclaims_ok,
        "no-submit run cannot claim private/live/deploy/promotion readiness",
    )
    for bad in contract_bad_claims:
        add_stop(stops, "collector_contract_private_or_live_claim", bad)
    for bad in scorecard_bad_claims:
        add_stop(stops, "run_scorecard_private_or_live_claim", bad)

    safety_counter = 0
    for idx, row in enumerate(candidate_rows, start=1):
        for field in ("submit_allowed", "sign_allowed", "cancel_allowed"):
            if bool_bad_true(row.get(field)):
                safety_counter += 1
                add_stop(
                    stops,
                    f"{field}_true_in_candidate_csv",
                    f"{field} must remain false in no-submit public/orderbook observation",
                    scope="candidate",
                    row_index=idx,
                    market_slug=row.get("market_slug", ""),
                    candidate_id=row.get("candidate_id", ""),
                )
    for idx, row in enumerate(book_rows, start=1):
        for field in ("submit_allowed", "sign_allowed", "cancel_allowed"):
            if bool_bad_true(row.get(field)):
                safety_counter += 1
                add_stop(
                    stops,
                    f"{field}_true_in_book_snapshot_csv",
                    f"{field} must remain false in no-submit public/orderbook observation",
                    scope="book_snapshot",
                    row_index=idx,
                    market_slug=row.get("market_slug", ""),
                    candidate_id="",
                )
    add_threshold(
        thresholds,
        "submit_sign_cancel_safety_counter_zero",
        0,
        safety_counter,
        safety_counter == 0,
        "submit_allowed/sign_allowed/cancel_allowed must never be true",
    )

    output_hashes = scorecard.get("output_hashes") or {}
    missing_output_hash_values = [
        key
        for key, item in output_hashes.items()
        if not isinstance(item, dict) or not item.get("sha256")
    ]
    add_threshold(
        thresholds,
        "run_scorecard_output_hashes_present",
        "non-empty output_hashes with sha256 values",
        "missing" if not output_hashes else ",".join(missing_output_hash_values) or "present",
        bool(output_hashes) and not missing_output_hash_values,
        "raw JSONL and normalized CSV hashes must be declared by the no-submit packet",
    )

    summary = summarize(contract, scorecard, candidate_rows, book_rows)
    add_threshold(
        thresholds,
        "min_observed_markets",
        gates.get("min_observed_markets"),
        summary["markets_with_both_tokens_first120"],
        summary["markets_with_both_tokens_first120"] is not None
        and summary["markets_with_both_tokens_first120"] >= int(gates.get("min_observed_markets", 0)),
        "both YES and NO token books must be observed in [0,120s) for enough markets",
    )
    add_threshold(
        thresholds,
        "intent_market_coverage_over_discovered",
        gates.get("intent_market_coverage_over_discovered_gte"),
        summary["intent_market_coverage_over_discovered"],
        summary["intent_market_coverage_over_discovered"] is not None
        and summary["intent_market_coverage_over_discovered"]
        >= float(gates.get("intent_market_coverage_over_discovered_gte", 1.0)),
        "candidate intent coverage over discovered market universe",
    )
    add_threshold(
        thresholds,
        "filled_market_coverage_over_discovered",
        gates.get("filled_market_coverage_over_discovered_gte"),
        summary["filled_market_coverage_over_discovered"],
        summary["filled_market_coverage_over_discovered"] is not None
        and summary["filled_market_coverage_over_discovered"]
        >= float(gates.get("filled_market_coverage_over_discovered_gte", 1.0)),
        "hypothetically filled market coverage over discovered market universe",
    )
    add_threshold(
        thresholds,
        "market_fill_retention",
        gates.get("market_fill_retention_gte"),
        summary["market_fill_retention"],
        summary["market_fill_retention"] is not None
        and summary["market_fill_retention"] >= float(gates.get("market_fill_retention_gte", 1.0)),
        "hypothetically filled markets divided by intent markets",
    )
    add_threshold(
        thresholds,
        "qty_fill_conversion",
        gates.get("qty_fill_conversion_gte"),
        summary["qty_fill_conversion"],
        summary["qty_fill_conversion"] is not None
        and summary["qty_fill_conversion"] >= float(gates.get("qty_fill_conversion_gte", 1.0)),
        "hypothetical filled quantity divided by intended quantity",
    )
    add_threshold(
        thresholds,
        "pair_cost_proxy",
        gates.get("pair_cost_proxy_lte"),
        summary["avg_pair_cost_proxy"],
        summary["avg_pair_cost_proxy"] is not None
        and summary["avg_pair_cost_proxy"] <= float(gates.get("pair_cost_proxy_lte", 0.0)),
        "average pair cost proxy must stay below contract cap",
    )
    add_threshold(
        thresholds,
        "residual_qty_proxy",
        gates.get("residual_proxy_lte"),
        summary["residual_qty_proxy"],
        summary["residual_qty_proxy"] is not None
        and summary["residual_qty_proxy"] <= float(gates.get("residual_proxy_lte", 0.0)),
        "residual quantity proxy must stay below contract cap",
    )
    add_threshold(
        thresholds,
        "fee_after_pnl_proxy_positive",
        ">0",
        summary["fee_after_pnl_proxy"],
        summary["fee_after_pnl_proxy"] is not None and summary["fee_after_pnl_proxy"] > 0,
        "fee-after PnL proxy must be positive",
    )

    failed = [row for row in thresholds if row["passed"] != "true"]
    evaluation_passed = not failed and not stops
    source_packet_hash = stable_hash(
        {
            "contract_sha256": sha256_file(contract_path),
            "run_scorecard_sha256": sha256_file(run_scorecard_path),
            "book_snapshot_csv_sha256": sha256_file(book_snapshot_csv),
            "candidate_csv_sha256": sha256_file(candidate_csv),
        }
    )
    normalized = normalize_candidate_rows(candidate_rows, summary["evidence_level"], source_packet_hash)
    status = STATUS_OK if evaluation_passed else STATUS_BLOCKED

    output_dir = args.output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": "observable_microstructure_adapter_v1_eval",
        "created_utc": utc_now(),
        "status": status,
        "evaluation_passed": evaluation_passed,
        "adapter_kind": "runtime_no_submit_observable_adapter_v1",
        "evidence_level": summary["evidence_level"],
        "review_only": True,
        "research_observable_microstructure_ready": evaluation_passed,
        "canary_preflight_material_ready": evaluation_passed,
        "private_truth_ready": False,
        "strategy_promotion_ready": False,
        "live_ready": False,
        "live_orders_allowed": False,
        "deployable": False,
        "order_execution": False,
        "maker_fill_truth": False,
        "summary": {
            **summary,
            "threshold_failure_count": len(failed),
            "stop_condition_event_count": len(stops),
            "failed_thresholds": [row["threshold_name"] for row in failed],
        },
        "policy": {
            "public_orderbook_observation_is_private_truth": False,
            "hypothetical_fill_is_owner_fill_truth": False,
            "can_set_private_truth_ready": False,
            "can_set_strategy_promotion_ready": False,
            "can_authorize_live_or_deploy": False,
            "residual_settlement_pnl_is_strategy_edge": False,
        },
        "inputs": {
            "collector_contract": str(contract_path),
            "input_dir": str(input_dir),
            "book_snapshot_csv": str(book_snapshot_csv),
            "book_touch_candidate_csv": str(candidate_csv),
            "run_scorecard": str(run_scorecard_path),
        },
        "input_hashes": {
            "collector_contract_sha256": sha256_file(contract_path),
            "book_snapshot_csv_sha256": sha256_file(book_snapshot_csv),
            "book_touch_candidate_csv_sha256": sha256_file(candidate_csv),
            "run_scorecard_sha256": sha256_file(run_scorecard_path),
            "source_packet_hash": source_packet_hash,
        },
    }
    write_json(output_dir / "OBSERVABLE_MICROSTRUCTURE_ADAPTER_V1_EVAL.json", manifest)
    write_csv(output_dir / "observable_microstructure_candidate_rows.csv", normalized, NORMALIZED_FIELDS)
    write_csv(output_dir / "threshold_failures.csv", thresholds, THRESHOLD_FIELDS)
    write_csv(output_dir / "stop_condition_events.csv", stops, STOP_EVENT_FIELDS)
    print(json.dumps({"status": status, "summary": manifest["summary"]}, indent=2, sort_keys=True))
    return 0 if evaluation_passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
