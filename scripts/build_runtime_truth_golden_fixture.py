#!/usr/bin/env python3
"""Build a small runtime smoke golden fixture from an action-level truth response.

The script intentionally refuses incomplete inputs by default.  It expects the
response to already contain replay/source-truth-linked action decisions and
strict rescue close rows.  It does not query raw data and does not rebuild
replay_store_v2.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import shutil
from pathlib import Path
from typing import Any


CASE_FIELDS = [
    "fixture_id",
    "case_type",
    "expected_decision",
    "expected_blocked_by",
    "action_id",
    "close_action_id",
    "source_action_id",
    "condition_id",
    "day",
    "decision_ts_ms",
    "market_side",
    "taker_side",
    "price",
    "size",
    "l1_source_ref",
    "l1_source_sequence_id",
    "l1_event_time_ms",
    "l1_recv_time_ms",
    "l1_age_ms",
    "l1_yes_bid_px",
    "l1_yes_bid_sz",
    "l1_yes_ask_px",
    "l1_yes_ask_sz",
    "l1_no_bid_px",
    "l1_no_bid_sz",
    "l1_no_ask_px",
    "l1_no_ask_sz",
    "l2_source_ref",
    "l2_source_sequence_id",
    "l2_event_time_ms",
    "l2_recv_time_ms",
    "l2_age_ms",
    "l2_market_side",
    "l2_bid1_px",
    "l2_bid1_sz",
    "l2_ask1_px",
    "l2_ask1_sz",
    "trade_source_ref",
    "trade_source_sequence_id",
    "trade_event_time_ms",
    "trade_recv_time_ms",
    "trade_age_ms",
    "trade_id",
    "residual_lot_linkage",
    "expected_fee_formula",
]

REQUIRED_BY_CASE = {
    "accepted_action": [
        "expected_decision",
        "action_id",
        "condition_id",
        "day",
        "decision_ts_ms",
        "l1_source_ref",
        "l1_source_sequence_id",
        "l1_event_time_ms",
        "l1_recv_time_ms",
        "l1_age_ms",
        "l2_source_ref",
        "l2_source_sequence_id",
        "l2_event_time_ms",
        "l2_recv_time_ms",
        "l2_age_ms",
        "trade_source_ref",
        "trade_source_sequence_id",
        "trade_event_time_ms",
        "trade_recv_time_ms",
        "trade_age_ms",
        "market_side",
        "taker_side",
        "price",
        "size",
    ],
    "blocked_action": [
        "expected_decision",
        "expected_blocked_by",
        "action_id",
        "condition_id",
        "day",
        "decision_ts_ms",
        "l1_source_ref",
        "l1_source_sequence_id",
        "l1_age_ms",
        "l2_source_ref",
        "l2_source_sequence_id",
        "l2_age_ms",
        "trade_source_ref",
        "trade_source_sequence_id",
        "trade_age_ms",
    ],
    "strict_rescue_close": [
        "expected_decision",
        "close_action_id",
        "source_action_id",
        "condition_id",
        "day",
        "decision_ts_ms",
        "l2_source_ref",
        "l2_source_sequence_id",
        "l2_age_ms",
        "trade_source_ref",
        "trade_source_sequence_id",
        "trade_age_ms",
        "market_side",
        "price",
        "size",
        "expected_fee_formula",
        "residual_lot_linkage",
    ],
}

ALIASES = {
    "decision_ts_ms": ("decision_ts_ms", "ts_ms", "close_ts_ms"),
    "expected_decision": ("expected_decision", "decision"),
    "expected_blocked_by": ("expected_blocked_by", "blocked_by"),
    "market_side": ("market_side", "side"),
    "size": ("size", "qty"),
    "price": ("price", "px"),
    "l1_source_ref": ("l1_source_ref", "source_l1_ref", "source_l1_id"),
    "l1_source_sequence_id": ("l1_source_sequence_id", "l1_capture_seq", "source_l1_sequence_id"),
    "l1_event_time_ms": ("l1_event_time_ms", "l1_source_ts_ms"),
    "l1_recv_time_ms": ("l1_recv_time_ms",),
    "l1_age_ms": ("l1_age_ms", "strict_l1_age_ms"),
    "l2_source_ref": ("l2_source_ref", "source_l2_ref", "source_l2_id"),
    "l2_source_sequence_id": ("l2_source_sequence_id", "l2_capture_seq", "source_l2_sequence_id"),
    "l2_event_time_ms": ("l2_event_time_ms", "l2_source_ts_ms"),
    "l2_recv_time_ms": ("l2_recv_time_ms",),
    "l2_age_ms": ("l2_age_ms", "first_l2_age_ms"),
    "trade_source_ref": ("trade_source_ref", "source_trade_ref", "source_trade_id"),
    "trade_source_sequence_id": ("trade_source_sequence_id", "trade_capture_seq", "source_trade_sequence_id"),
    "trade_event_time_ms": ("trade_event_time_ms", "trade_ts_ms", "source_trade_ts_ms"),
    "trade_recv_time_ms": ("trade_recv_time_ms",),
    "trade_age_ms": ("trade_age_ms",),
    "expected_fee_formula": ("expected_fee_formula", "official_fee_formula", "fee_formula"),
    "residual_lot_linkage": ("residual_lot_linkage", "residual_lot_ids"),
}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "t", "yes", "y", "accepted", "accept"}


def first_value(row: dict[str, Any], field: str) -> Any:
    for key in (field, *ALIASES.get(field, ())):
        value = row.get(key)
        if value not in (None, ""):
            return value
    return ""


def normalize_row(row: dict[str, Any], case_type: str, index: int) -> dict[str, Any]:
    out = {field: first_value(row, field) for field in CASE_FIELDS}
    out["fixture_id"] = f"{case_type}_{index:04d}"
    out["case_type"] = case_type
    if case_type == "accepted_action" and not out["expected_decision"]:
        out["expected_decision"] = "accepted"
    if case_type == "blocked_action" and not out["expected_decision"]:
        out["expected_decision"] = "blocked"
    if case_type == "strict_rescue_close" and not out["expected_decision"]:
        out["expected_decision"] = "strict_rescue_close"
    if not out["expected_fee_formula"]:
        out["expected_fee_formula"] = "fee = shares * fee_rate * price * (1 - price)"
    return out


def missing_fields(row: dict[str, Any]) -> list[str]:
    required = REQUIRED_BY_CASE[row["case_type"]]
    return [field for field in required if row.get(field) in (None, "", [])]


def build_cases(response_dir: Path, sample_size: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    action_rows = read_csv(response_dir / "action_decisions.csv")
    rescue_rows = read_csv(response_dir / "strict_rescue_closes.csv")
    residual_path = response_dir / "residual_fifo_lots.csv"
    residual_rows = read_csv(residual_path) if residual_path.exists() else []

    accepted = []
    blocked = []
    for row in action_rows:
        decision = str(row.get("expected_decision") or row.get("decision") or "").lower()
        accepted_flag = truthy(row.get("accepted")) or decision == "accepted"
        blocked_flag = (row.get("accepted") not in (None, "") and not truthy(row.get("accepted"))) or decision == "blocked"
        if accepted_flag and len(accepted) < sample_size:
            accepted.append(row)
        if blocked_flag and len(blocked) < sample_size:
            blocked.append(row)
        if len(accepted) >= sample_size and len(blocked) >= sample_size:
            break

    rescue = rescue_rows[:sample_size]
    cases: list[dict[str, Any]] = []
    for case_type, rows in (
        ("accepted_action", accepted),
        ("blocked_action", blocked),
        ("strict_rescue_close", rescue),
    ):
        for idx, row in enumerate(rows, 1):
            case = normalize_row(row, case_type, idx)
            if case_type == "strict_rescue_close" and not case.get("residual_lot_linkage"):
                source_action_id = str(case.get("source_action_id") or "")
                linked = [
                    {
                        "lot_id": r.get("lot_id") or r.get("source_action_id") or r.get("source_seed_action_id"),
                        "source_action_id": r.get("source_action_id") or r.get("source_seed_action_id"),
                        "remaining_qty": r.get("remaining_qty") or r.get("qty"),
                        "remaining_cost": r.get("remaining_cost") or r.get("cost"),
                        "close_action_id": case.get("close_action_id"),
                    }
                    for r in residual_rows
                    if source_action_id
                    and source_action_id
                    in {str(r.get("source_action_id") or ""), str(r.get("source_seed_action_id") or "")}
                ]
                case["residual_lot_linkage"] = json.dumps(linked, ensure_ascii=False, sort_keys=True)
            cases.append(case)

    summary = {
        "accepted_action": len(accepted),
        "blocked_action": len(blocked),
        "strict_rescue_close": len(rescue),
    }
    return cases, summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--response-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--sample-size", type=int, default=10)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--allow-incomplete", action="store_true")
    args = parser.parse_args()

    response_dir = args.response_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    cases, summary = build_cases(response_dir, args.sample_size)

    if summary != {key: args.sample_size for key in summary}:
        raise SystemExit(f"insufficient rows for requested fixture: {summary}")

    missing = {case["fixture_id"]: missing_fields(case) for case in cases}
    missing = {key: value for key, value in missing.items() if value}
    if missing and not args.allow_incomplete:
        raise SystemExit(
            "fixture is missing required fields; pass --allow-incomplete only for schema debugging: "
            + json.dumps(missing, ensure_ascii=False, sort_keys=True)
        )

    if output_dir.exists():
        if not args.force:
            raise FileExistsError(f"output exists: {output_dir}")
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    fixture_path = output_dir / "golden_cases.jsonl"
    with fixture_path.open("w", encoding="utf-8") as f:
        for case in cases:
            f.write(json.dumps(case, ensure_ascii=False, sort_keys=True) + "\n")

    manifest = {
        "created_at": utc_now(),
        "schema_version": "runtime_replay_truth_smoke_golden_fixture_v1",
        "response_dir": str(response_dir),
        "sample_size": args.sample_size,
        "row_count": len(cases),
        "case_counts": summary,
        "outputs": {"jsonl": "golden_cases.jsonl"},
        "missing_required_fields": missing,
        "allow_incomplete": bool(args.allow_incomplete),
    }
    (output_dir / "GOLDEN_FIXTURE_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

