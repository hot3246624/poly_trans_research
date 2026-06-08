#!/usr/bin/env python3
"""Validate CE25 target_qty=8 candidate ledger against replay source rows.

This is a local public/replay source bridge. It does not fetch live data,
load private keys, import candidates, place orders, or claim readiness.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb


DEFAULT_STRATEGY_INPUT = Path(
    "/Users/hot/web3Scientist/poly_trans_research/data/exports/"
    "ce25_high_price_top1_qty_target_qty8_candidate_ledger_20260604/"
    "CE25_HIGH_PRICE_TOP1_QTY_TARGET_QTY8_STRATEGY_INPUT.json"
)
DEFAULT_OUTPUT_DIR = Path(
    "/Users/hot/web3Scientist/poly_trans_research/data/exports/"
    "ce25_high_price_top1_qty_target_qty8_source_bridge_20260604"
)
DEFAULT_CANDIDATE_BASE_DUCKDB = Path(
    "/Users/hot/web3Scientist/poly_backtest_data/derived/"
    "completion_candidate_pipeline_v1/local_20260502_20260518_paircap102/candidate_base.duckdb"
)
DEFAULT_L2_MART_DUCKDB = Path(
    "/Users/hot/web3Scientist/poly_backtest_data/derived/"
    "contract_examples/l2_top_aligned_mart_20260502_20260518_l2/l2_top_aligned_mart.duckdb"
)

EXPECTED_STATUS = "KEEP_CE25_TARGET_QTY8_NORMALIZED_CANDIDATE_LEDGER_REVIEW_REQUIRED_NOT_OOS_READY"
OUTPUT_STATUS = "KEEP_CE25_TARGET_QTY8_SOURCE_BRIDGE_VALIDATED_REVIEW_REQUIRED_NOT_OOS_READY"
STRATEGY_ID = "CE25_BTC5M_HIGH_PRICE_TOP1_QTY_GATE_V1"
STRATEGY_VERSION = "target_qty8_l2_clean_review_v1"
OWNER_LINE = "CE25_HIGH_PRICE_RESEARCH"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def qlit(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def stable_candidate_id(source_candidate_id: str) -> str:
    digest = hashlib.sha256(f"{STRATEGY_ID}|{STRATEGY_VERSION}|{source_candidate_id}".encode()).hexdigest()
    return f"ce25_tq8_{digest[:24]}"


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def close_enough(a: Any, b: Any, eps: float = 1e-9) -> bool:
    return abs(as_float(a) - as_float(b)) <= eps


def load_duckdb_rows(path: Path, ids: list[int]) -> dict[int, dict[str, Any]]:
    if not ids:
        return {}
    con = duckdb.connect(str(path), read_only=True)
    try:
        id_csv = ",".join(str(int(x)) for x in sorted(set(ids)))
        rows = con.execute(f"SELECT * FROM candidate_base WHERE candidate_row_id IN ({id_csv})").fetchall()
        cols = [d[0] for d in con.description]
    finally:
        con.close()
    return {int(row[cols.index("candidate_row_id")]): dict(zip(cols, row)) for row in rows}


def load_l2_rows(path: Path, leg_rows: list[dict[str, str]]) -> dict[tuple[str, str, int], dict[str, Any]]:
    keys = {
        (row["condition_id"], row["leg_side"], as_int(row["l1_source_row_id"]))
        for row in leg_rows
        if row.get("condition_id") and row.get("leg_side") and row.get("l1_source_row_id")
    }
    if not keys:
        return {}
    con = duckdb.connect(str(path), read_only=True)
    try:
        con.execute(
            """
            CREATE TEMP TABLE wanted(
              condition_id VARCHAR,
              market_side VARCHAR,
              l1_source_row_id BIGINT
            )
            """
        )
        con.executemany("INSERT INTO wanted VALUES (?, ?, ?)", [(a, b, c) for a, b, c in keys])
        rows = con.execute(
            """
            SELECT m.*
            FROM md_book_l2_top_aligned m
            JOIN wanted w
              ON m.condition_id = w.condition_id
             AND m.market_side = w.market_side
             AND m.l1_source_row_id = w.l1_source_row_id
            """
        ).fetchall()
        cols = [d[0] for d in con.description]
    finally:
        con.close()
    return {
        (str(row[cols.index("condition_id")]), str(row[cols.index("market_side")]), int(row[cols.index("l1_source_row_id")])): dict(
            zip(cols, row)
        )
        for row in rows
    }


def non_claims_false(payload: dict[str, Any]) -> bool:
    non_claims = payload.get("non_claims") if isinstance(payload.get("non_claims"), dict) else {}
    return all(non_claims.get(key) is False for key in ("private_truth_ready", "strategy_promotion_ready", "live_ready", "deployable"))


def side_matches_candidate_base(cb: dict[str, Any], leg_side: str, leg_price: str) -> bool:
    if cb.get("side") == leg_side and close_enough(cb.get("side_ask"), leg_price, 1e-6):
        return True
    if cb.get("opposite_side") == leg_side and close_enough(cb.get("opp_ask"), leg_price, 1e-6):
        return True
    return False


def build_audit(args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]], list[str], list[Path]]:
    strategy_path = args.strategy_input.expanduser().resolve()
    strategy = read_json(strategy_path)
    errors: list[str] = []
    artifacts: list[Path] = [strategy_path]

    if strategy.get("status") != EXPECTED_STATUS:
        errors.append("strategy_status_mismatch")
    if strategy.get("strategy_id") != STRATEGY_ID:
        errors.append("strategy_id_mismatch")
    if strategy.get("strategy_owner_line") != OWNER_LINE:
        errors.append("owner_line_mismatch")
    if not non_claims_false(strategy):
        errors.append("strategy_non_claims_not_false")
    if strategy.get("source_semantics", {}).get("binding_status") != "REPLAY_BOUND_NOT_OOS_READY":
        errors.append("binding_status_mismatch")

    root = strategy_path.parent
    ledger_path = (root / str(strategy.get("candidate_csv"))).resolve()
    artifacts.append(ledger_path)
    if sha256_file(ledger_path) != strategy.get("candidate_csv_sha256"):
        errors.append("ledger_sha256_mismatch")

    source_artifacts = strategy.get("source_artifacts") if isinstance(strategy.get("source_artifacts"), dict) else {}
    source_action_path = Path(source_artifacts.get("source_book_shadow_actions_csv", "")).expanduser().resolve()
    l2_action_path = Path(source_artifacts.get("l2_action_evidence_csv", "")).expanduser().resolve()
    l2_leg_path = Path(source_artifacts.get("l2_leg_evidence_csv", "")).expanduser().resolve()
    l2_manifest_path = Path(source_artifacts.get("l2_validation_manifest", "")).expanduser().resolve()
    source_manifest_path = Path(source_artifacts.get("source_book_shadow_manifest", "")).expanduser().resolve()
    artifacts.extend([source_action_path, l2_action_path, l2_leg_path, l2_manifest_path, source_manifest_path])
    for key, path in (
        ("source_book_shadow_actions_csv_sha256", source_action_path),
        ("l2_action_evidence_csv_sha256", l2_action_path),
        ("l2_leg_evidence_csv_sha256", l2_leg_path),
        ("l2_validation_manifest_sha256", l2_manifest_path),
        ("source_book_shadow_manifest_sha256", source_manifest_path),
    ):
        if not path.is_file():
            errors.append(f"missing_artifact:{path}")
        elif sha256_file(path) != source_artifacts.get(key):
            errors.append(f"artifact_hash_mismatch:{key}")

    ledger_rows = read_csv(ledger_path)
    source_rows = read_csv(source_action_path)
    l2_action_rows = read_csv(l2_action_path)
    leg_rows = read_csv(l2_leg_path)
    source_by_candidate = {row["candidate_id"]: row for row in source_rows}
    l2_action_by_candidate = {row["candidate_id"]: row for row in l2_action_rows}
    legs_by_candidate: dict[str, list[dict[str, str]]] = {}
    for row in leg_rows:
        legs_by_candidate.setdefault(row["candidate_id"], []).append(row)

    candidate_row_ids: list[int] = []
    for row in ledger_rows:
        candidate_row_ids.append(as_int(row.get("first_source_candidate_row_id")))
        candidate_row_ids.append(as_int(row.get("completion_source_candidate_row_id")))
    candidate_base_rows = load_duckdb_rows(args.candidate_base_duckdb.expanduser().resolve(), candidate_row_ids)
    l2_mart_rows = load_l2_rows(args.l2_mart_duckdb.expanduser().resolve(), leg_rows)

    row_audit: list[dict[str, Any]] = []
    for ledger in ledger_rows:
        source_candidate_id = ledger.get("source_candidate_id", "")
        row_errors: list[str] = []
        source = source_by_candidate.get(source_candidate_id)
        l2_action = l2_action_by_candidate.get(source_candidate_id)
        legs = sorted(legs_by_candidate.get(source_candidate_id, []), key=lambda r: r.get("leg_role", ""))
        if stable_candidate_id(source_candidate_id) != ledger.get("candidate_id"):
            row_errors.append("stable_candidate_id_mismatch")
        if source is None:
            row_errors.append("missing_source_action")
            source = {}
        if l2_action is None:
            row_errors.append("missing_l2_action")
            l2_action = {}
        if len(legs) != 2:
            row_errors.append("missing_two_leg_evidence")

        for field in ("condition_id", "slug", "day", "policy_id", "branch_id"):
            if source.get(field) != ledger.get(field):
                row_errors.append(f"source_{field}_mismatch")
            if l2_action.get(field) != ledger.get(field):
                row_errors.append(f"l2_action_{field}_mismatch")
        for field in ("paired_qty", "buy_actual_est", "cash_pnl_est"):
            if not close_enough(source.get(field), ledger.get(field), 1e-6):
                row_errors.append(f"source_{field}_mismatch")
            if not close_enough(l2_action.get(field), ledger.get(field), 1e-6):
                row_errors.append(f"l2_action_{field}_mismatch")
        if not close_enough(source.get("pair_cost"), ledger.get("l1_pair_cost"), 1e-6):
            row_errors.append("source_pair_cost_mismatch")
        if source.get("first_leg_side") != ledger.get("first_leg_side"):
            row_errors.append("first_leg_side_mismatch")
        if source.get("completion_leg_side") != ledger.get("completion_leg_side"):
            row_errors.append("completion_leg_side_mismatch")
        if not close_enough(source.get("first_leg_price"), ledger.get("first_leg_price"), 1e-6):
            row_errors.append("first_leg_price_mismatch")
        if not close_enough(source.get("completion_leg_price"), ledger.get("completion_leg_price"), 1e-6):
            row_errors.append("completion_leg_price_mismatch")

        for role, side_field, price_field, row_id_field in (
            ("first", "first_leg_side", "first_leg_price", "first_source_candidate_row_id"),
            ("completion", "completion_leg_side", "completion_leg_price", "completion_source_candidate_row_id"),
        ):
            matching_legs = [leg for leg in legs if leg.get("leg_role") == role]
            if len(matching_legs) != 1:
                row_errors.append(f"{role}_leg_count_mismatch")
                continue
            leg = matching_legs[0]
            if leg.get("leg_side") != ledger.get(side_field):
                row_errors.append(f"{role}_leg_side_mismatch")
            if not close_enough(leg.get("leg_price"), ledger.get(price_field), 1e-6):
                row_errors.append(f"{role}_leg_price_mismatch")
            if not close_enough(leg.get("leg_qty"), ledger.get("paired_qty"), 1e-6):
                row_errors.append(f"{role}_leg_qty_mismatch")
            source_row_id = as_int(source.get(row_id_field) or ledger.get(row_id_field))
            cb = candidate_base_rows.get(source_row_id)
            if cb is None:
                row_errors.append(f"{role}_candidate_base_row_missing")
            else:
                if cb.get("condition_id") != ledger.get("condition_id"):
                    row_errors.append(f"{role}_candidate_base_condition_mismatch")
                if cb.get("slug") != ledger.get("slug"):
                    row_errors.append(f"{role}_candidate_base_slug_mismatch")
                if cb.get("day") != ledger.get("day"):
                    row_errors.append(f"{role}_candidate_base_day_mismatch")
                if not side_matches_candidate_base(cb, str(ledger.get(side_field)), str(ledger.get(price_field))):
                    row_errors.append(f"{role}_candidate_base_side_price_mismatch")
                if as_int(cb.get("strict_l1_row_id")) != as_int(leg.get("l1_source_row_id")):
                    row_errors.append(f"{role}_strict_l1_row_id_mismatch")
                if as_int(cb.get("strict_l2_row_id")) != as_int(leg.get("preferred_l2_source_row_id")):
                    row_errors.append(f"{role}_strict_l2_row_id_mismatch")
            l2_key = (str(leg.get("condition_id")), str(leg.get("leg_side")), as_int(leg.get("l1_source_row_id")))
            l2 = l2_mart_rows.get(l2_key)
            if l2 is None:
                row_errors.append(f"{role}_l2_mart_row_missing")
            else:
                for l2_field, leg_field in (
                    ("recv_ms", "l2_top_recv_ms"),
                    ("raw_l2_source_row_id", "raw_l2_source_row_id"),
                    ("raw_l2_age_ms", "raw_l2_age_ms"),
                    ("ask1_px", "ask1_px"),
                    ("ask1_sz", "ask1_sz"),
                    ("raw_l2_ask2_px", "raw_l2_ask2_px"),
                    ("raw_l2_ask2_sz", "raw_l2_ask2_sz"),
                    ("raw_l2_ask3_px", "raw_l2_ask3_px"),
                    ("raw_l2_ask3_sz", "raw_l2_ask3_sz"),
                    ("raw_l2_ask4_px", "raw_l2_ask4_px"),
                    ("raw_l2_ask4_sz", "raw_l2_ask4_sz"),
                    ("raw_l2_ask5_px", "raw_l2_ask5_px"),
                    ("raw_l2_ask5_sz", "raw_l2_ask5_sz"),
                ):
                    if not close_enough(l2.get(l2_field), leg.get(leg_field), 1e-6):
                        row_errors.append(f"{role}_l2_{l2_field}_mismatch")
                if str(l2.get("top_overlay_required")).lower() != str(leg.get("top_overlay_required")).lower():
                    row_errors.append(f"{role}_top_overlay_required_mismatch")

        row_audit.append(
            {
                "candidate_id": ledger.get("candidate_id"),
                "source_candidate_id": source_candidate_id,
                "condition_id": ledger.get("condition_id"),
                "slug": ledger.get("slug"),
                "day": ledger.get("day"),
                "first_source_candidate_row_id": ledger.get("first_source_candidate_row_id"),
                "completion_source_candidate_row_id": ledger.get("completion_source_candidate_row_id"),
                "leg_evidence_count": len(legs),
                "row_error_count": len(row_errors),
                "row_status": "PASS" if not row_errors else "FAIL",
                "row_errors": ";".join(row_errors),
            }
        )

    summary = {
        "status": OUTPUT_STATUS if not errors and not any(r["row_error_count"] for r in row_audit) else "BLOCKED_CE25_SOURCE_BRIDGE_DRIFT",
        "strategy_input": str(strategy_path),
        "candidate_count": len(ledger_rows),
        "market_count": len({row.get("condition_id") for row in ledger_rows}),
        "source_action_count": len(source_rows),
        "l2_action_count": len(l2_action_rows),
        "leg_evidence_count": len(leg_rows),
        "candidate_base_rows_loaded": len(candidate_base_rows),
        "l2_mart_rows_loaded": len(l2_mart_rows),
        "row_error_count": sum(as_int(row["row_error_count"]) for row in row_audit),
        "failed_row_count": sum(1 for row in row_audit if row["row_status"] != "PASS"),
        "top_overlay_required_count": sum(1 for row in ledger_rows if str(row.get("top_overlay_required")).lower() == "true"),
        "raw_l2_age_ok_pair_count": sum(1 for row in ledger_rows if str(row.get("raw_l2_age_ok_pair")).lower() == "true"),
        "errors": errors,
        "non_claims": {
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "deployable": False,
            "canary_authorized": False,
            "orders_authorized": False,
        },
    }
    return summary, row_audit, errors, artifacts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy-input", type=Path, default=DEFAULT_STRATEGY_INPUT)
    parser.add_argument("--candidate-base-duckdb", type=Path, default=DEFAULT_CANDIDATE_BASE_DUCKDB)
    parser.add_argument("--l2-mart-duckdb", type=Path, default=DEFAULT_L2_MART_DUCKDB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    summary, row_audit, errors, artifacts = build_audit(args)

    row_audit_path = output_dir / "ce25_target_qty8_source_bridge_row_audit.csv"
    summary_path = output_dir / "CE25_TARGET_QTY8_SOURCE_BRIDGE_SUMMARY.json"
    note_path = output_dir / "CE25_TARGET_QTY8_SOURCE_BRIDGE_REVIEW_NOTE.md"
    manifest_path = output_dir / "CE25_TARGET_QTY8_SOURCE_BRIDGE_HASH_MANIFEST.json"

    write_csv(
        row_audit_path,
        row_audit,
        [
            "candidate_id",
            "source_candidate_id",
            "condition_id",
            "slug",
            "day",
            "first_source_candidate_row_id",
            "completion_source_candidate_row_id",
            "leg_evidence_count",
            "row_error_count",
            "row_status",
            "row_errors",
        ],
    )
    write_json(summary_path, summary)
    note_path.write_text(
        "\n".join(
            [
                "# CE25 Target Qty 8 Source Bridge",
                "",
                f"Status: `{summary['status']}`",
                "",
                f"- candidates: {summary['candidate_count']}",
                f"- markets: {summary['market_count']}",
                f"- leg evidence rows: {summary['leg_evidence_count']}",
                f"- candidate_base rows loaded: {summary['candidate_base_rows_loaded']}",
                f"- L2 mart rows loaded: {summary['l2_mart_rows_loaded']}",
                f"- failed rows: {summary['failed_row_count']}",
                "",
                "Boundary: local public/replay source bridge only; no private truth, no OOS, no live/deploy/order claim.",
                "",
            ]
        )
    )

    all_artifacts = [row_audit_path, summary_path, note_path] + artifacts
    strategy_payload = read_json(args.strategy_input.expanduser().resolve())
    source_artifacts = strategy_payload.get("source_artifacts") if isinstance(strategy_payload.get("source_artifacts"), dict) else {}
    source_created_at = ""
    l2_manifest_ref = source_artifacts.get("l2_validation_manifest")
    if l2_manifest_ref:
        try:
            source_created_at = str(read_json(Path(l2_manifest_ref).expanduser().resolve()).get("created_at") or "")
        except Exception:
            source_created_at = ""
    manifest = {
        "schema_version": 1,
        "created_at": source_created_at or datetime.now(timezone.utc).isoformat(),
        "status": summary["status"],
        "summary_sha256": sha256_file(summary_path),
        "row_audit_sha256": sha256_file(row_audit_path),
        "artifacts": [
            {"path": str(path), "sha256": sha256_file(path), "size_bytes": path.stat().st_size}
            for path in all_artifacts
        ],
        "non_claims": summary["non_claims"],
    }
    write_json(manifest_path, manifest)

    print(
        json.dumps(
            {
                "ok": summary["status"] == OUTPUT_STATUS,
                "status": summary["status"],
                "output_dir": str(output_dir),
                "candidate_count": summary["candidate_count"],
                "failed_row_count": summary["failed_row_count"],
                "row_error_count": summary["row_error_count"],
                "manifest_sha256": sha256_file(manifest_path),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if summary["status"] == OUTPUT_STATUS else 2


if __name__ == "__main__":
    raise SystemExit(main())
