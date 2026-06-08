#!/usr/bin/env python3
"""Build the BTC tiny-canary no-order shadow report package.

This is a local research artifact generator. It emits the three-file split
agreed with the runner side:

1. no_order_shadow_report.csv: exactly the 33 required columns, no audit fields.
2. no_order_shadow_audit_manifest.json: safety/source/hash evidence.
3. no_order_shadow_gate_summary.json: aggregate threshold evidence.

The script does not import candidates, load private keys, create orders, cancel,
redeem, start runners, or touch live/canary services. It uses the local
same-window handoff actions and local public L2 as-of mart to generate a public
book/latency/fillability proxy report. Market/token metadata is resolved from
the public Gamma API unless already cached.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import datetime as dt
import hashlib
import json
import math
import os
import shutil
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_DATA_ROOT = Path(os.environ.get("POLY_BT_ROOT", "/Users/hot/web3Scientist/poly_backtest_data"))
DEFAULT_CONTRACT_ROOT = DEFAULT_DATA_ROOT / "derived/contract_examples"
DEFAULT_IMPORT_CONTRACT = (
    DEFAULT_CONTRACT_ROOT
    / "btc_same_window_residual_share_le_3pct_v1_canary_preflight_latest"
    / "research_only_import_contract.csv"
)
DEFAULT_REQUIRED_COLUMNS = (
    DEFAULT_CONTRACT_ROOT
    / "xuan_btc_tiny_canary_shadow_evaluation_gate_spec_latest"
    / "required_shadow_report_columns.csv"
)
DEFAULT_SOURCE_SEMANTICS = (
    DEFAULT_CONTRACT_ROOT
    / "btc_same_window_residual_share_le_3pct_v1_canary_preflight_latest"
    / "source_semantics_contract.json"
)
DEFAULT_GATE_SPEC = (
    DEFAULT_CONTRACT_ROOT
    / "xuan_btc_tiny_canary_shadow_evaluation_gate_spec_latest"
    / "XUAN_BTC_TINY_CANARY_SHADOW_EVALUATION_GATE_SPEC.json"
)
DEFAULT_ACTIONS = (
    DEFAULT_CONTRACT_ROOT
    / "xuan_completion_candidate_rescore_latest"
    / "xuan_completion_candidate_same_window_handoff_actions.csv"
)
DEFAULT_L2_DUCKDB = (
    DEFAULT_CONTRACT_ROOT
    / "l2_top_aligned_mart_20260502_20260518_l2"
    / "l2_top_aligned_mart.duckdb"
)
DEFAULT_L2_MANIFEST = (
    DEFAULT_CONTRACT_ROOT
    / "l2_top_aligned_mart_20260502_20260518_l2"
    / "L2_TOP_ALIGNED_MART_MANIFEST.json"
)
DEFAULT_CAPITAL_LEDGER = (
    DEFAULT_CONTRACT_ROOT
    / "btc_same_window_residual_share_le_3pct_v1_canary_preflight_latest"
    / "filter_capital_ledger.csv"
)
DEFAULT_MICROSTRUCTURE_DAY_SUMMARY = (
    DEFAULT_CONTRACT_ROOT
    / "btc_same_window_residual_share_le_3pct_v1_canary_preflight_latest"
    / "microstructure_day_summary.csv"
)
DEFAULT_HAIRCUT_STRESS = (
    DEFAULT_CONTRACT_ROOT
    / "xuan_btc_tiny_canary_haircut_stress_latest"
    / "XUAN_BTC_TINY_CANARY_HAIRCUT_STRESS.json"
)
DEFAULT_OUTPUT_DIR = DEFAULT_CONTRACT_ROOT / "xuan_btc_tiny_canary_no_order_shadow_report_latest"

REPORT_NAME = "no_order_shadow_report.csv"
LEGACY_REPORT_NAME = "btc_same_window_tiny_canary_no_order_shadow_report.csv"
AUDIT_NAME = "no_order_shadow_audit_manifest.json"
AUDIT_LEGACY_NAME = "NO_ORDER_SHADOW_AUDIT_MANIFEST.json"
GATE_NAME = "no_order_shadow_gate_summary.json"
GATE_LEGACY_NAME = "NO_ORDER_SHADOW_GATE_SUMMARY.json"

BOOL_FALSE = {"", "0", "false", "f", "no", "n"}
BOOL_TRUE = {"1", "true", "t", "yes", "y"}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def utc_label() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def safe_copyfile(src: Path, dst: Path) -> None:
    # APFS is commonly case-insensitive, so lower/upper-case aliases can be the same file.
    if dst.exists() and src.samefile(dst):
        return
    shutil.copyfile(src, dst)


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv_rows(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def as_bool(value: Any) -> bool | None:
    text = str(value if value is not None else "").strip().lower()
    if text in BOOL_TRUE:
        return True
    if text in BOOL_FALSE:
        return False
    return None


def as_float(value: Any, default: float | None = None) -> float | None:
    if value in (None, ""):
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(out):
        return default
    return out


def as_int(value: Any, default: int = 0) -> int:
    parsed = as_float(value)
    return default if parsed is None else int(parsed)


def p95(values: list[float]) -> float | None:
    clean = sorted(v for v in values if not math.isnan(v))
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    pos = (len(clean) - 1) * 0.95
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return clean[lo]
    return clean[lo] * (hi - pos) + clean[hi] * (pos - lo)


def round6(value: float | None) -> float | None:
    return None if value is None else round(value, 6)


def bool_text(value: bool) -> str:
    return "true" if value else "false"


def required_columns(path: Path) -> list[str]:
    rows = read_csv_rows(path)
    if rows and "column" in rows[0]:
        return [row["column"] for row in rows]
    return [value for row in rows for value in row.values() if value]


def first_csv_row(path: Path) -> dict[str, str]:
    rows = read_csv_rows(path)
    return rows[0] if rows else {}


def load_haircut_metric(path: Path, scenario: str) -> float | None:
    payload = read_json(path)
    for row in payload.get("scenario_summary") or []:
        if row.get("scenario") == scenario:
            return as_float(row.get("total_pnl"))
    return None


def gamma_event(slug: str, timeout_s: float) -> list[dict[str, Any]]:
    url = f"https://gamma-api.polymarket.com/events?slug={slug}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload if isinstance(payload, list) else []


def parse_gamma_market(slug: str, condition_id: str, event_rows: list[dict[str, Any]]) -> dict[str, str] | None:
    condition_key = condition_id.lower()
    for event in event_rows:
        for market in event.get("markets") or []:
            market_condition = str(market.get("conditionId") or market.get("condition_id") or "").lower()
            if market_condition != condition_key:
                continue
            token_raw = market.get("clobTokenIds") or market.get("clob_token_ids") or []
            outcome_raw = market.get("outcomes") or []
            tokens = json.loads(token_raw) if isinstance(token_raw, str) else list(token_raw)
            outcomes = json.loads(outcome_raw) if isinstance(outcome_raw, str) else list(outcome_raw)
            if len(tokens) < 2 or len(outcomes) < 2:
                return None
            return {
                "market_id": str(market.get("id") or ""),
                "token_id_yes": str(tokens[0]),
                "token_id_no": str(tokens[1]),
                "outcome_yes": str(outcomes[0]),
                "outcome_no": str(outcomes[1]),
                "slug": slug,
                "condition_id": condition_id,
            }
    return None


def resolve_market_metadata(
    import_rows: list[dict[str, str]],
    cache_path: Path,
    timeout_s: float,
    sleep_s: float,
) -> tuple[dict[str, dict[str, str]], list[str]]:
    cached = read_json(cache_path) if cache_path.exists() else {}
    metadata: dict[str, dict[str, str]] = dict(cached.get("metadata_by_condition_id") or {})
    failures: list[str] = []
    for row in import_rows:
        condition_id = row["condition_id"]
        slug = row["slug"]
        if condition_id in metadata:
            continue
        try:
            parsed = parse_gamma_market(slug, condition_id, gamma_event(slug, timeout_s))
        except Exception as exc:  # fail-closed, but keep the specific reason
            failures.append(f"{slug}:gamma_error:{exc!r}")
            continue
        if not parsed or not parsed.get("market_id") or not parsed.get("token_id_yes") or not parsed.get("token_id_no"):
            failures.append(f"{slug}:metadata_missing_or_ambiguous")
            continue
        metadata[condition_id] = parsed
        if sleep_s > 0:
            time.sleep(sleep_s)
    write_json(
        cache_path,
        {
            "schema_version": "xuan_btc_tiny_canary_gamma_metadata_cache_v1",
            "created_utc": utc_now(),
            "metadata_by_condition_id": metadata,
            "failure_count": len(failures),
            "failures": failures,
        },
    )
    return metadata, failures


def load_l2_books(l2_duckdb: Path, condition_ids: list[str], min_ts: int, max_ts: int) -> dict[tuple[str, str], list[dict[str, Any]]]:
    import duckdb  # imported only when the generator actually runs

    con = duckdb.connect(str(l2_duckdb), read_only=True)
    try:
        con.execute("SET threads=1")
        con.execute("SET preserve_insertion_order=false")
        con.execute("SET memory_limit='2GB'")
        placeholders = ",".join(["?"] * len(condition_ids))
        query = f"""
            SELECT
              condition_id,
              market_side,
              recv_ms,
              source_ts_ms,
              raw_l2_age_ms,
              ask1_px,
              ask1_sz,
              raw_l2_ask1_sz,
              raw_l2_ask2_sz,
              raw_l2_ask3_sz,
              raw_l2_ask4_sz,
              raw_l2_ask5_sz
            FROM md_book_l2_top_aligned
            WHERE asset = 'BTC'
              AND condition_id IN ({placeholders})
              AND market_side IN ('YES', 'NO')
              AND recv_ms >= ?
              AND recv_ms <= ?
        """
        cur = con.execute(query, [*condition_ids, min_ts - 600_000, max_ts])
        buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
        while True:
            rows = cur.fetchmany(100_000)
            if not rows:
                break
            for row in rows:
                record = {
                    "condition_id": row[0],
                    "market_side": row[1],
                    "recv_ms": row[2],
                    "source_ts_ms": row[3],
                    "raw_l2_age_ms": row[4],
                    "ask1_px": row[5],
                    "ask1_sz": row[6],
                    "raw_l2_ask1_sz": row[7],
                    "raw_l2_ask2_sz": row[8],
                    "raw_l2_ask3_sz": row[9],
                    "raw_l2_ask4_sz": row[10],
                    "raw_l2_ask5_sz": row[11],
                }
                buckets.setdefault((str(row[0]).lower(), str(row[1]).upper()), []).append(record)
    finally:
        con.close()
    for records in buckets.values():
        records.sort(key=lambda item: int(item["recv_ms"]))
    return buckets


def asof(records: list[dict[str, Any]], planned_ts_ms: int) -> dict[str, Any] | None:
    if not records:
        return None
    recv = [int(row["recv_ms"]) for row in records]
    idx = bisect.bisect_right(recv, planned_ts_ms) - 1
    return records[idx] if idx >= 0 else None


def top5_qty(row: dict[str, Any] | None) -> float | None:
    if row is None:
        return None
    total = 0.0
    seen = False
    for key in ("raw_l2_ask1_sz", "raw_l2_ask2_sz", "raw_l2_ask3_sz", "raw_l2_ask4_sz", "raw_l2_ask5_sz"):
        value = as_float(row.get(key))
        if value is not None:
            total += value
            seen = True
    if not seen:
        return as_float(row.get("ask1_sz"))
    return total


def top1_qty(row: dict[str, Any] | None) -> float | None:
    if row is None:
        return None
    return as_float(row.get("raw_l2_ask1_sz"), as_float(row.get("ask1_sz")))


def build_threshold(name: str, required: Any, observed: Any, passed: bool, source: str) -> dict[str, Any]:
    return {
        "threshold_name": name,
        "required": required,
        "observed": observed,
        "passed": bool(passed),
        "source": source,
    }


def build_report(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    output_dir = args.output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = args.run_id or f"xuan_btc_tiny_canary_public_l2_proxy_no_order_shadow_{utc_label()}"

    required = required_columns(args.required_columns.expanduser())
    import_rows = read_csv_rows(args.import_contract.expanduser())
    import_by_condition = {row["condition_id"]: row for row in import_rows}
    condition_ids = list(import_by_condition)
    source_semantics = read_json(args.source_semantics.expanduser())
    gate_spec = read_json(args.gate_spec.expanduser())
    thresholds = dict(((gate_spec.get("shadow_evaluation_gate") or {}).get("pass_thresholds") or {}))
    capital = first_csv_row(args.capital_ledger.expanduser())
    day_rows = read_csv_rows(args.microstructure_day_summary.expanduser())

    safety_failures: list[str] = []
    for row in import_rows:
        for field in ("import_enabled", "candidate_import_allowed", "live_orders_allowed", "deployable"):
            if as_bool(row.get(field)) is not False:
                safety_failures.append(f"{row.get('condition_id')}:{field}_not_false")
        if as_bool(row.get("dry_run_only")) is not True:
            safety_failures.append(f"{row.get('condition_id')}:dry_run_only_not_true")

    source_fingerprints = sorted({row.get("source_dataset_fingerprint") for row in import_rows if row.get("source_dataset_fingerprint")})
    source_semantics_ids = sorted({row.get("source_semantics_contract_id") for row in import_rows if row.get("source_semantics_contract_id")})
    l2_contract_ids = sorted({row.get("l2_top_overlay_contract_id") for row in import_rows if row.get("l2_top_overlay_contract_id")})
    source_continuity_ok = (
        len(source_fingerprints) == 1
        and source_fingerprints[0] == source_semantics.get("source_dataset_fingerprint")
        and len(source_semantics_ids) == 1
        and source_semantics_ids[0] == source_semantics.get("source_semantics_contract_id")
        and len(l2_contract_ids) == 1
        and l2_contract_ids[0] == source_semantics.get("l2_top_overlay_contract_id")
    )

    metadata, metadata_failures = resolve_market_metadata(
        import_rows,
        args.gamma_cache.expanduser() if args.gamma_cache else output_dir / "gamma_market_metadata_cache.json",
        args.gamma_timeout_s,
        args.gamma_sleep_s,
    )

    action_rows = [
        row
        for row in read_csv_rows(args.handoff_actions.expanduser())
        if row.get("asset") == "BTC" and row.get("condition_id") in import_by_condition
    ]
    action_rows.sort(key=lambda row: (as_int(import_by_condition[row["condition_id"]].get("candidate_rank")), as_int(row.get("ts_ms"))))
    if not action_rows:
        raise RuntimeError("no BTC handoff action rows matched the import contract")

    planned_times = [as_int(row["ts_ms"]) for row in action_rows]
    l2_books = load_l2_books(args.l2_duckdb.expanduser(), condition_ids, min(planned_times), max(planned_times))

    actions_by_condition: dict[str, list[dict[str, str]]] = {}
    for row in action_rows:
        actions_by_condition.setdefault(row["condition_id"], []).append(row)
    final_action_ids = {
        rows[-1].get("action_id")
        for rows in actions_by_condition.values()
        if rows
    }

    report_rows: list[dict[str, Any]] = []
    missing_book_count = 0
    missing_pair_book_count = 0
    missing_metadata_count = 0
    book_ages: list[float] = []
    seed_ratios: list[float] = []
    top5_support_count = 0
    selected_l2_digest = hashlib.sha256()

    for action in action_rows:
        condition_id = action["condition_id"]
        contract = import_by_condition[condition_id]
        meta = metadata.get(condition_id) or {}
        if not meta:
            missing_metadata_count += 1
        side = str(action.get("side") or "").upper()
        opposite_side = str(action.get("opposite_side") or "").upper()
        planned_ts_ms = as_int(action.get("ts_ms"))
        side_book = asof(l2_books.get((condition_id.lower(), side), []), planned_ts_ms)
        opposite_book = asof(l2_books.get((condition_id.lower(), opposite_side), []), planned_ts_ms)
        if side_book is None:
            missing_book_count += 1
        if opposite_book is None:
            missing_pair_book_count += 1

        observed_ts_ms = side_book.get("recv_ms") if side_book else ""
        source_ts_ms = side_book.get("source_ts_ms") if side_book else None
        raw_age = as_float(side_book.get("raw_l2_age_ms")) if side_book else None
        book_age_ms = None
        if source_ts_ms not in (None, ""):
            book_age_ms = max(0.0, float(planned_ts_ms - int(source_ts_ms)))
        elif raw_age is not None:
            book_age_ms = max(0.0, raw_age)
        if book_age_ms is not None:
            book_ages.append(book_age_ms)

        depth = top5_qty(side_book)
        ask1_size = top1_qty(side_book)
        planned_qty = as_float(action.get("seed_qty"), 0.0) or 0.0
        seed_ratio = (planned_qty / depth) if depth and depth > 0 else None
        if seed_ratio is not None:
            seed_ratios.append(seed_ratio)
        would_fill_top1 = ask1_size is not None and ask1_size >= planned_qty
        would_fill_top5 = depth is not None and depth >= planned_qty
        if would_fill_top5:
            top5_support_count += 1

        side_ask = as_float(side_book.get("ask1_px")) if side_book else None
        opposite_ask = as_float(opposite_book.get("ask1_px")) if opposite_book else None
        pair_cost = side_ask + opposite_ask if side_ask is not None and opposite_ask is not None else None

        residual_qty_proxy = 0.0
        residual_cost_proxy = 0.0
        if action.get("action_id") in final_action_ids:
            yes_qty = as_float(action.get("inventory_yes_qty_after"), 0.0) or 0.0
            no_qty = as_float(action.get("inventory_no_qty_after"), 0.0) or 0.0
            yes_cost = as_float(action.get("inventory_yes_cost_after"), 0.0) or 0.0
            no_cost = as_float(action.get("inventory_no_cost_after"), 0.0) or 0.0
            residual_qty_proxy = abs(yes_qty - no_qty)
            residual_cost_proxy = abs(yes_cost - no_cost)

        if side_book:
            selected_l2_digest.update(
                json.dumps(
                    {
                        "condition_id": condition_id,
                        "side": side,
                        "planned_ts_ms": planned_ts_ms,
                        "recv_ms": side_book.get("recv_ms"),
                        "source_ts_ms": side_book.get("source_ts_ms"),
                        "ask1_px": side_book.get("ask1_px"),
                        "top5_qty": depth,
                    },
                    sort_keys=True,
                ).encode("utf-8")
            )

        row = {
            "run_id": run_id,
            "runner_profile_id": contract.get("runner_profile_id"),
            "filter_name": contract.get("filter_name"),
            "filter_version": contract.get("filter_version"),
            "candidate_id": contract.get("deterministic_candidate_id"),
            "candidate_rank": contract.get("candidate_rank"),
            "asset": contract.get("asset"),
            "day": contract.get("day"),
            "condition_id": condition_id,
            "slug": contract.get("slug"),
            "market_id": meta.get("market_id", ""),
            "token_id_yes": meta.get("token_id_yes", ""),
            "token_id_no": meta.get("token_id_no", ""),
            "planned_ts_ms": planned_ts_ms,
            "observed_ts_ms": observed_ts_ms,
            "latency_ms": "" if observed_ts_ms == "" else max(0, planned_ts_ms - int(observed_ts_ms)),
            "book_age_ms": "" if book_age_ms is None else round(book_age_ms, 6),
            "side": side,
            "planned_seed_px": action.get("seed_px"),
            "observed_best_ask": "" if side_ask is None else side_ask,
            "observed_top5_fillable_qty": "" if depth is None else round(depth, 12),
            "planned_seed_qty": action.get("seed_qty"),
            "seed_qty_over_top5_depth": "" if seed_ratio is None else round(seed_ratio, 12),
            "would_fill_top1": bool_text(would_fill_top1),
            "would_fill_top5": bool_text(would_fill_top5),
            "observed_pair_cost_proxy": "" if pair_cost is None else round(pair_cost, 12),
            "observed_fee_model": "public_l2_proxy_fee_model_from_research_gate",
            "observed_residual_qty_proxy": round(residual_qty_proxy, 12),
            "observed_residual_cost_proxy": round(residual_cost_proxy, 12),
            "orders_sent": "false",
            "cancels_sent": "false",
            "redeems_sent": "false",
            "live_orders_allowed": "false",
        }
        report_rows.append(row)

    main_report = output_dir / REPORT_NAME
    legacy_report = output_dir / LEGACY_REPORT_NAME
    write_csv_rows(main_report, report_rows, required)
    safe_copyfile(main_report, legacy_report)

    row_count = len(report_rows)
    candidate_count = len({row["candidate_id"] for row in report_rows if row.get("candidate_id")})
    day_count = len({row["day"] for row in report_rows if row.get("day")})
    action_match_count = row_count - missing_book_count
    action_match_rate = action_match_count / row_count if row_count else 0.0
    top5_support_rate = top5_support_count / row_count if row_count else 0.0
    book_age_p95 = p95(book_ages)
    max_seed_ratio = max(seed_ratios) if seed_ratios else None
    residual_cost_share = as_float(capital.get("residual_cost_share"))
    fee_1_50_pnl = load_haircut_metric(args.haircut_stress.expanduser(), "fee_1_50_zero_stress")
    pair_edge_50_pnl = load_haircut_metric(args.haircut_stress.expanduser(), "pair_edge_50pct_zero_stress")
    daily_negative_count = sum(1 for row in day_rows if (as_float(row.get("residual_zero_after_fee_pnl"), 0.0) or 0.0) < 0)

    threshold_rows = [
        build_threshold("minimum_candidates_observed", thresholds.get("minimum_candidates_observed"), candidate_count, candidate_count >= int(thresholds.get("minimum_candidates_observed", 0)), "main_csv"),
        build_threshold("minimum_days", thresholds.get("minimum_days"), day_count, day_count >= int(thresholds.get("minimum_days", 0)), "main_csv"),
        build_threshold("l2_or_live_book_action_match_rate", thresholds.get("l2_or_live_book_action_match_rate_min"), round6(action_match_rate), action_match_rate >= float(thresholds.get("l2_or_live_book_action_match_rate_min", 1.0)), "main_csv"),
        build_threshold("book_age_p95_ms", thresholds.get("book_age_p95_ms_max"), round6(book_age_p95), book_age_p95 is not None and book_age_p95 <= float(thresholds.get("book_age_p95_ms_max", 0.0)), "main_csv"),
        build_threshold("top5_supports_seed_qty_rate", thresholds.get("top5_supports_seed_qty_rate_min"), round6(top5_support_rate), top5_support_rate >= float(thresholds.get("top5_supports_seed_qty_rate_min", 1.0)), "main_csv"),
        build_threshold("seed_qty_over_top5_depth_max", thresholds.get("seed_qty_over_top5_depth_max"), round6(max_seed_ratio), max_seed_ratio is not None and max_seed_ratio <= float(thresholds.get("seed_qty_over_top5_depth_max", 0.0)), "main_csv"),
        build_threshold("observed_residual_cost_share", thresholds.get("observed_residual_cost_share_max"), round6(residual_cost_share), residual_cost_share is not None and residual_cost_share <= float(thresholds.get("observed_residual_cost_share_max", 0.0)), "capital_ledger"),
        build_threshold("fee_multiplier_stress_positive", f">0 at multiplier {thresholds.get('fee_multiplier_stress_required_positive')}", round6(fee_1_50_pnl), fee_1_50_pnl is not None and fee_1_50_pnl > 0, "haircut_stress"),
        build_threshold("observed_pair_edge_haircut_positive", f">0 at haircut floor {thresholds.get('observed_pair_edge_haircut_floor')}", round6(pair_edge_50_pnl), pair_edge_50_pnl is not None and pair_edge_50_pnl > 0, "haircut_stress"),
        build_threshold("daily_residual_zero_proxy_negative_count", thresholds.get("daily_residual_zero_proxy_negative_allowed"), daily_negative_count, daily_negative_count <= int(thresholds.get("daily_residual_zero_proxy_negative_allowed", 0)), "microstructure_day_summary"),
        build_threshold("no_order_safety_columns_false", "all false", "all false", True, "main_csv"),
        build_threshold("market_metadata_resolved", "all import contract rows", row_count - missing_metadata_count, missing_metadata_count == 0 and not metadata_failures, "gamma_metadata"),
        build_threshold("side_book_asof_resolved", "all action rows", action_match_count, missing_book_count == 0, "l2_top_aligned_mart"),
        build_threshold("pair_book_asof_resolved", "all action rows", row_count - missing_pair_book_count, missing_pair_book_count == 0, "l2_top_aligned_mart"),
    ]
    gate_passed = all(row["passed"] for row in threshold_rows) and not safety_failures and source_continuity_ok

    l2_stat = args.l2_duckdb.expanduser().stat()
    input_hashes = {
        "import_contract_sha256": sha256_file(args.import_contract.expanduser()),
        "required_shadow_report_columns_sha256": sha256_file(args.required_columns.expanduser()),
        "source_semantics_contract_sha256": sha256_file(args.source_semantics.expanduser()),
        "handoff_actions_sha256": sha256_file(args.handoff_actions.expanduser()),
        "l2_top_aligned_mart_manifest_sha256": sha256_file(args.l2_manifest.expanduser()) if args.l2_manifest.expanduser().exists() else "",
        "l2_duckdb_sha256": "",
        "l2_duckdb_sha256_note": "Skipped intentionally for 15GB local DuckDB; manifest hash plus selected row digest are recorded.",
        "selected_l2_action_rows_sha256": selected_l2_digest.hexdigest(),
    }

    summary = {
        "row_count": row_count,
        "candidate_count": candidate_count,
        "day_count": day_count,
        "action_match_count": action_match_count,
        "l2_or_live_book_action_match_rate": round6(action_match_rate),
        "book_age_p95_ms": round6(book_age_p95),
        "top5_support_count": top5_support_count,
        "top5_supports_seed_qty_rate": round6(top5_support_rate),
        "seed_qty_over_top5_depth_max": round6(max_seed_ratio),
        "observed_residual_cost_share": round6(residual_cost_share),
        "fee_1_50_zero_stress_after_fee_pnl": round6(fee_1_50_pnl),
        "pair_edge_50pct_zero_stress_after_fee_pnl": round6(pair_edge_50_pnl),
        "daily_residual_zero_negative_count": daily_negative_count,
        "missing_book_count": missing_book_count,
        "missing_pair_book_count": missing_pair_book_count,
        "missing_metadata_count": missing_metadata_count,
        "threshold_failure_count": sum(1 for row in threshold_rows if not row["passed"]),
        "evaluation_passed": gate_passed,
    }

    audit = {
        "schema_version": "xuan_btc_tiny_canary_no_order_shadow_audit_manifest_v1",
        "created_utc": utc_now(),
        "run_id": run_id,
        "status": "KEEP_NO_ORDER_SHADOW_AUDIT_READY" if gate_passed else "BLOCKED_NO_ORDER_SHADOW_AUDIT_FAIL_CLOSED",
        "main_report": {
            "path": str(main_report),
            "legacy_path": str(legacy_report),
            "sha256": sha256_file(main_report),
            "row_count": row_count,
            "column_count": len(required),
            "columns_match_required": True,
        },
        "input_hashes": input_hashes,
        "l2_duckdb_stat": {
            "path": str(args.l2_duckdb.expanduser()),
            "size_bytes": l2_stat.st_size,
            "mtime_ns": l2_stat.st_mtime_ns,
        },
        "source_continuity": {
            "passed": source_continuity_ok,
            "source_dataset_fingerprints": source_fingerprints,
            "source_semantics_contract_ids": source_semantics_ids,
            "l2_top_overlay_contract_ids": l2_contract_ids,
            "expected_source_dataset_fingerprint": source_semantics.get("source_dataset_fingerprint"),
            "expected_source_semantics_contract_id": source_semantics.get("source_semantics_contract_id"),
            "expected_l2_top_overlay_contract_id": source_semantics.get("l2_top_overlay_contract_id"),
        },
        "safety": {
            "no_order": True,
            "dry_run_only": True,
            "import_enabled": False,
            "candidate_import_allowed": False,
            "live_orders_allowed": False,
            "deployable": False,
            "private_key_loaded": False,
            "no_private_key_loaded": True,
            "order_client_type": "NullOrderClient",
            "null_order_client_or_stub": True,
            "orders_sent": False,
            "cancels_sent": False,
            "redeems_sent": False,
            "order_api_call_count": 0,
            "cancel_api_call_count": 0,
            "redeem_api_call_count": 0,
            "candidate_import_call_count": 0,
            "safety_failures": safety_failures,
        },
        "runner_config": {
            "runner_kind": "local_public_l2_proxy_artifact_generator",
            "network_used": "public_gamma_metadata_only",
            "orders_path_loaded": False,
            "candidate_import_path_loaded": False,
            "live_or_canary_started": False,
            "book_source": "local_l2_top_aligned_mart_asof",
            "latency_ms_semantics": "planned_ts_ms - observed_l2_recv_ms; non-negative historical as-of delta",
            "book_age_ms_semantics": "planned_ts_ms - l2_source_ts_ms; fallback raw_l2_age_ms",
            "residual_proxy_semantics": "market-final residual proxy emitted on the final action row for each condition_id",
        },
        "promotion_gate": {
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "live_orders_allowed": False,
            "future_owner_execution_reconciliation_required": True,
        },
        "limitations": [
            "This validates public book/latency/fillability proxy only.",
            "It cannot validate owner order acceptance, queue position, real fills, real fees, inventory, redeem, private truth, or promotion readiness.",
        ],
    }
    gate_summary = {
        "schema_version": "xuan_btc_tiny_canary_no_order_shadow_gate_summary_v1",
        "created_utc": utc_now(),
        "run_id": run_id,
        "status": (
            "KEEP_XUAN_BTC_TINY_CANARY_PUBLIC_L2_PROXY_NO_ORDER_SHADOW_GATE_PASS_RESEARCH_ONLY"
            if gate_passed
            else "BLOCKED_XUAN_BTC_TINY_CANARY_PUBLIC_L2_PROXY_NO_ORDER_SHADOW_GATE_FAIL_CLOSED"
        ),
        "evaluation_passed": gate_passed,
        "summary": summary,
        "thresholds": threshold_rows,
        "promotion_gate": audit["promotion_gate"],
        "limitations": audit["limitations"],
        "manifest_fingerprint": sha256_text(json.dumps(summary, ensure_ascii=False, sort_keys=True)),
    }

    audit_path = output_dir / AUDIT_NAME
    gate_path = output_dir / GATE_NAME
    write_json(audit_path, audit)
    write_json(gate_path, gate_summary)
    safe_copyfile(audit_path, output_dir / AUDIT_LEGACY_NAME)
    safe_copyfile(gate_path, output_dir / GATE_LEGACY_NAME)
    return 0 if gate_passed else 1, {
        "status": gate_summary["status"],
        "summary": summary,
        "outputs": {
            "main_report": str(main_report),
            "legacy_report": str(legacy_report),
            "audit_manifest": str(audit_path),
            "gate_summary": str(gate_path),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--import-contract", type=Path, default=DEFAULT_IMPORT_CONTRACT)
    parser.add_argument("--required-columns", type=Path, default=DEFAULT_REQUIRED_COLUMNS)
    parser.add_argument("--source-semantics", type=Path, default=DEFAULT_SOURCE_SEMANTICS)
    parser.add_argument("--gate-spec", type=Path, default=DEFAULT_GATE_SPEC)
    parser.add_argument("--handoff-actions", type=Path, default=DEFAULT_ACTIONS)
    parser.add_argument("--l2-duckdb", type=Path, default=DEFAULT_L2_DUCKDB)
    parser.add_argument("--l2-manifest", type=Path, default=DEFAULT_L2_MANIFEST)
    parser.add_argument("--capital-ledger", type=Path, default=DEFAULT_CAPITAL_LEDGER)
    parser.add_argument("--microstructure-day-summary", type=Path, default=DEFAULT_MICROSTRUCTURE_DAY_SUMMARY)
    parser.add_argument("--haircut-stress", type=Path, default=DEFAULT_HAIRCUT_STRESS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--gamma-cache", type=Path)
    parser.add_argument("--gamma-timeout-s", type=float, default=10.0)
    parser.add_argument("--gamma-sleep-s", type=float, default=0.05)
    parser.add_argument("--run-id")
    return parser.parse_args()


def main() -> int:
    rc, payload = build_report(parse_args())
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
