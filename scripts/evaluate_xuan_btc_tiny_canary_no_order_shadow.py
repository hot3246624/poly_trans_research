#!/usr/bin/env python3
"""Evaluate the BTC tiny-canary no-order shadow runner report.

This is a post-run gate. It consumes the public/no-order runner report and the
shadow evaluation spec, normalizes the observed telemetry, emits threshold and
stop-condition evidence, and never promotes historical/public observations into
owner private truth.
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
DEFAULT_GATE_SPEC = (
    DEFAULT_CONTRACT_ROOT
    / "xuan_btc_tiny_canary_shadow_evaluation_gate_spec_latest"
    / "XUAN_BTC_TINY_CANARY_SHADOW_EVALUATION_GATE_SPEC.json"
)
DEFAULT_REQUIRED_COLUMNS_CSV = (
    DEFAULT_CONTRACT_ROOT
    / "xuan_btc_tiny_canary_shadow_evaluation_gate_spec_latest"
    / "required_shadow_report_columns.csv"
)
DEFAULT_SHADOW_REPORT_DIR = DEFAULT_CONTRACT_ROOT / "xuan_btc_tiny_canary_no_order_shadow_report_latest"
DEFAULT_REAL_SHADOW_REPORT_DIR = DEFAULT_CONTRACT_ROOT / "xuan_same_window_no_order_shadow_real_runner_report_latest"
DEFAULT_SHADOW_REPORT = (
    DEFAULT_SHADOW_REPORT_DIR / "btc_same_window_tiny_canary_no_order_shadow_report.csv"
)
DEFAULT_SHADOW_REPORT_PRIMARY = DEFAULT_SHADOW_REPORT_DIR / "no_order_shadow_report.csv"
DEFAULT_REAL_SHADOW_REPORT_PRIMARY = DEFAULT_REAL_SHADOW_REPORT_DIR / "no_order_shadow_report.csv"
DEFAULT_AUDIT_MANIFEST = DEFAULT_SHADOW_REPORT_DIR / "no_order_shadow_audit_manifest.json"
DEFAULT_AUDIT_MANIFEST_LEGACY = DEFAULT_SHADOW_REPORT_DIR / "NO_ORDER_SHADOW_AUDIT_MANIFEST.json"
DEFAULT_GATE_SUMMARY = DEFAULT_SHADOW_REPORT_DIR / "no_order_shadow_gate_summary.json"
DEFAULT_GATE_SUMMARY_LEGACY = DEFAULT_SHADOW_REPORT_DIR / "NO_ORDER_SHADOW_GATE_SUMMARY.json"
DEFAULT_APPROVAL_PACKET = (
    DEFAULT_CONTRACT_ROOT
    / "xuan_same_window_no_order_shadow_manual_approval_packet_latest"
    / "XUAN_SAME_WINDOW_NO_ORDER_SHADOW_MANUAL_APPROVAL_PACKET.json"
)
DEFAULT_OUTPUT_DIR = DEFAULT_CONTRACT_ROOT / "xuan_btc_tiny_canary_no_order_shadow_eval_latest"
REAL_RUNNER_KIND = "real_readonly_ws_no_order_observer"
PUBLIC_L2_PROXY_KIND = "public_l2_proxy"

NORMALIZED_FIELDS = [
    "row_index",
    "run_id",
    "runner_profile_id",
    "filter_name",
    "filter_version",
    "candidate_id",
    "candidate_rank",
    "asset",
    "day",
    "condition_id",
    "slug",
    "market_id",
    "token_id_yes",
    "token_id_no",
    "planned_ts_ms",
    "observed_ts_ms",
    "latency_ms",
    "book_age_ms",
    "side",
    "planned_seed_px",
    "observed_best_ask",
    "observed_top5_fillable_qty",
    "planned_seed_qty",
    "seed_qty_over_top5_depth",
    "would_fill_top1",
    "would_fill_top5",
    "observed_pair_cost_proxy",
    "observed_fee_model",
    "observed_residual_qty_proxy",
    "observed_residual_cost_proxy",
    "orders_sent",
    "cancels_sent",
    "redeems_sent",
    "live_orders_allowed",
    "action_observed",
    "top5_supports_seed_qty",
]
THRESHOLD_FIELDS = ["threshold_name", "required", "observed", "passed", "severity", "detail"]
STOP_EVENT_FIELDS = ["event_name", "severity", "row_index", "candidate_id", "day", "condition_id", "detail"]

TRUE_VALUES = {"1", "true", "yes", "y", "t"}
FALSE_VALUES = {"0", "false", "no", "n", "f", ""}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def first_existing_path(*paths: Path) -> Path:
    for path in paths:
        if path.expanduser().exists():
            return path.expanduser()
    return paths[0].expanduser()


def nested_get(payload: dict[str, Any], names: tuple[str, ...]) -> Any:
    for section in ("summary", "aggregate_metrics", "metrics", ""):
        scope: Any = payload if not section else payload.get(section)
        if not isinstance(scope, dict):
            continue
        for name in names:
            if name in scope:
                return scope.get(name)
    return None


def read_required_columns_csv(path: Path) -> list[str]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if rows and "ordinal" in rows[0] and "column" in rows[0]:
        rows.sort(key=lambda row: int(row.get("ordinal") or 0))
        return [row.get("column", "") for row in rows if row.get("column")]
    return [value for row in rows for value in row.values() if value]


def nested_find(payload: dict[str, Any], dotted: str) -> Any:
    current: Any = payload
    for part in dotted.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def nested_find_any(payload: dict[str, Any], dotted_paths: tuple[str, ...]) -> Any:
    for dotted in dotted_paths:
        value = nested_find(payload, dotted)
        if value not in (None, ""):
            return value
    return None


def classify_report_kind(audit_manifest: dict[str, Any], gate_summary: dict[str, Any]) -> tuple[str, str]:
    explicit = nested_find_any(
        audit_manifest,
        ("report_kind", "runtime_config.report_kind", "main_report.report_kind"),
    ) or nested_find_any(gate_summary, ("report_kind", "summary.report_kind"))
    runner_kind = nested_find_any(audit_manifest, ("runner_kind", "runtime_config.runner_kind"))
    if explicit:
        return str(explicit), str(runner_kind or "")
    if runner_kind == REAL_RUNNER_KIND:
        return REAL_RUNNER_KIND, str(runner_kind)
    status_blob = " ".join(
        str(value or "")
        for value in (
            audit_manifest.get("status"),
            gate_summary.get("status"),
            nested_find(audit_manifest, "runtime_config.book_source"),
            nested_find(audit_manifest, "book_audit.transport"),
        )
    ).lower()
    if "public_l2_proxy" in status_blob or "l2_proxy" in status_blob:
        return PUBLIC_L2_PROXY_KIND, str(runner_kind or "")
    if "public" in status_blob and "proxy" in status_blob:
        return PUBLIC_L2_PROXY_KIND, str(runner_kind or "")
    return "unknown", str(runner_kind or "")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def parse_bool(value: Any) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if text in TRUE_VALUES:
        return True
    if text in FALSE_VALUES:
        return False
    return False


def parse_optional_bool(value: Any) -> bool | None:
    if value in (None, ""):
        return None
    text = str(value if value is not None else "").strip().lower()
    if text == "":
        return None
    if text in TRUE_VALUES:
        return True
    if text in FALSE_VALUES:
        return False
    return None


def fnum(value: Any, default: float | None = None) -> float | None:
    if value in (None, ""):
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(out):
        return default
    return out


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


def percentile(values: list[float], q: float) -> float | None:
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


def read_shadow_report(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            return list(reader), list(reader.fieldnames or [])
    if suffix in {".jsonl", ".ndjson"}:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        columns = sorted({key for row in rows for key in row})
        return rows, columns
    if suffix == ".json":
        data = read_json(path)
        if isinstance(data, list):
            rows = data
        elif isinstance(data, dict):
            payload = data.get("rows") or data.get("report_rows") or data.get("observations")
            rows = payload if isinstance(payload, list) else [data]
        else:
            rows = []
        columns = sorted({key for row in rows if isinstance(row, dict) for key in row})
        return [dict(row) for row in rows if isinstance(row, dict)], columns
    raise ValueError(f"unsupported shadow report extension: {path.suffix}")


def rank_key(row: dict[str, Any]) -> tuple[int, str, str, str]:
    rank = fnum(row.get("candidate_rank"), 0.0) or 0.0
    return (int(rank), str(row.get("day") or ""), str(row.get("condition_id") or ""), str(row.get("candidate_id") or ""))


def normalize_rows(rows: list[dict[str, Any]], required_columns: list[str]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for idx, row in enumerate(sorted(rows, key=rank_key), start=1):
        out = {field: row.get(field, "") for field in required_columns}
        for key, value in row.items():
            if key not in out:
                out[key] = value
        out["row_index"] = idx
        observed_best_ask = fnum(row.get("observed_best_ask"))
        top5_qty = fnum(row.get("observed_top5_fillable_qty"))
        planned_qty = fnum(row.get("planned_seed_qty"))
        seed_ratio = fnum(row.get("seed_qty_over_top5_depth"))
        if seed_ratio is None and top5_qty and planned_qty is not None:
            seed_ratio = planned_qty / top5_qty if top5_qty > 0 else None
            out["seed_qty_over_top5_depth"] = "" if seed_ratio is None else round(seed_ratio, 12)
        fill_top5 = parse_optional_bool(row.get("would_fill_top5"))
        if fill_top5 is None and top5_qty is not None and planned_qty is not None:
            fill_top5 = top5_qty >= planned_qty
            out["would_fill_top5"] = "true" if fill_top5 else "false"
        out["action_observed"] = bool(
            row.get("observed_ts_ms")
            and row.get("market_id")
            and observed_best_ask is not None
            and top5_qty is not None
        )
        out["top5_supports_seed_qty"] = bool(fill_top5)
        normalized.append(out)
    return normalized


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


def optional_metric_sum(rows: list[dict[str, Any]], names: tuple[str, ...]) -> tuple[str | None, float | None]:
    for name in names:
        values = [fnum(row.get(name)) for row in rows if row.get(name) not in (None, "")]
        clean = [value for value in values if value is not None]
        if clean:
            return name, sum(clean)
    return None, None


def build_stop_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    hard_false_flags = ("orders_sent", "cancels_sent", "redeems_sent", "live_orders_allowed")
    promotion_flags = (
        "private_truth_ready",
        "owner_private_truth_ready",
        "strategy_promotion_ready",
        "promotion_ready",
        "deployable",
        "live_ready",
    )
    match_false_flags = (
        "candidate_mapping_match",
        "source_dataset_fingerprint_match",
        "source_semantics_contract_id_match",
        "candidate_source_fingerprint_match",
    )
    for row in rows:
        row_index = row.get("row_index", "")
        base = {
            "row_index": row_index,
            "candidate_id": row.get("candidate_id", ""),
            "day": row.get("day", ""),
            "condition_id": row.get("condition_id", ""),
        }
        for flag in hard_false_flags:
            if parse_bool(row.get(flag)):
                events.append(
                    {
                        **base,
                        "event_name": f"{flag}_true_in_no_order_shadow",
                        "severity": "stop",
                        "detail": f"{flag} must remain false in no-order shadow evaluation",
                    }
                )
        for flag in promotion_flags:
            if parse_bool(row.get(flag)):
                events.append(
                    {
                        **base,
                        "event_name": f"{flag}_claim_before_owner_reconciliation",
                        "severity": "stop",
                        "detail": f"{flag} cannot be true before future owner private-truth reconciliation",
                    }
                )
        for flag in match_false_flags:
            value = parse_optional_bool(row.get(flag))
            if value is False:
                events.append(
                    {
                        **base,
                        "event_name": f"{flag}_false",
                        "severity": "stop",
                        "detail": f"{flag} indicates source/candidate mapping mismatch",
                    }
                )
    return events


def evaluate_rows(
    normalized: list[dict[str, Any]],
    source_columns: list[str],
    required_columns: list[str],
    thresholds: dict[str, Any],
    gate_summary: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    failures: list[dict[str, Any]] = []
    missing_columns = sorted(set(required_columns) - set(source_columns))
    extra_columns = [column for column in source_columns if column not in required_columns]
    exact_columns = source_columns == required_columns
    add_threshold(
        failures,
        "required_shadow_report_columns_present",
        "all required columns",
        ",".join(missing_columns) if missing_columns else "all present",
        not missing_columns,
        "runner report must contain every required spec column",
    )
    add_threshold(
        failures,
        "required_shadow_report_columns_exact",
        "exact required columns in exact order, no extras",
        "exact" if exact_columns else f"missing={','.join(missing_columns)} extra={','.join(extra_columns)}",
        exact_columns,
        "runner report main CSV must be the strict 33-column schema with no audit/source extras",
    )

    row_count = len(normalized)
    candidate_count = len({row.get("candidate_id") for row in normalized if row.get("candidate_id")})
    day_count = len({row.get("day") for row in normalized if row.get("day")})
    market_count = len({row.get("market_id") for row in normalized if row.get("market_id")})
    action_match_count = sum(1 for row in normalized if row.get("action_observed") is True)
    action_match_rate = action_match_count / row_count if row_count else 0.0
    book_ages = [value for value in (fnum(row.get("book_age_ms")) for row in normalized) if value is not None]
    latencies = [value for value in (fnum(row.get("latency_ms")) for row in normalized) if value is not None]
    book_age_p50 = percentile(book_ages, 0.50)
    book_age_p95 = p95(book_ages)
    book_age_max = max(book_ages) if book_ages else None
    latency_p50 = percentile(latencies, 0.50)
    latency_p95 = p95(latencies)
    latency_max = max(latencies) if latencies else None
    top5_support_count = sum(1 for row in normalized if row.get("top5_supports_seed_qty") is True)
    top5_support_rate = top5_support_count / row_count if row_count else 0.0
    seed_ratios = [value for value in (fnum(row.get("seed_qty_over_top5_depth")) for row in normalized) if value is not None]
    max_seed_ratio = max(seed_ratios) if seed_ratios else None
    pair_cost = sum(value for value in (fnum(row.get("observed_pair_cost_proxy"), 0.0) for row in normalized) if value)
    residual_cost = sum(
        value for value in (fnum(row.get("observed_residual_cost_proxy"), 0.0) for row in normalized) if value
    )
    sidecar_residual_cost_share = fnum(
        nested_get(gate_summary or {}, ("observed_residual_cost_share", "residual_cost_share"))
    )
    residual_cost_share = sidecar_residual_cost_share
    if residual_cost_share is None:
        residual_cost_share = residual_cost / pair_cost if pair_cost > 0 else None

    add_threshold(
        failures,
        "minimum_candidates_observed",
        thresholds.get("minimum_candidates_observed"),
        candidate_count,
        candidate_count >= int(thresholds.get("minimum_candidates_observed", 0)),
        "distinct candidate_id count observed in runner report",
    )
    add_threshold(
        failures,
        "minimum_days",
        thresholds.get("minimum_days"),
        day_count,
        day_count >= int(thresholds.get("minimum_days", 0)),
        "distinct day count observed in runner report",
    )
    add_threshold(
        failures,
        "l2_or_live_book_action_match_rate",
        thresholds.get("l2_or_live_book_action_match_rate_min"),
        round(action_match_rate, 6),
        action_match_rate >= float(thresholds.get("l2_or_live_book_action_match_rate_min", 1.0)),
        "proxy derived from observed_ts_ms, market_id, observed_best_ask, and top5 depth presence",
    )
    add_threshold(
        failures,
        "book_age_p95_ms",
        thresholds.get("book_age_p95_ms_max"),
        None if book_age_p95 is None else round(book_age_p95, 6),
        book_age_p95 is not None and book_age_p95 <= float(thresholds.get("book_age_p95_ms_max", 0.0)),
        "p95 book_age_ms from observed no-order report",
    )
    add_threshold(
        failures,
        "top5_supports_seed_qty_rate",
        thresholds.get("top5_supports_seed_qty_rate_min"),
        round(top5_support_rate, 6),
        top5_support_rate >= float(thresholds.get("top5_supports_seed_qty_rate_min", 1.0)),
        "fraction of rows where would_fill_top5 is true or derived top5 depth >= planned seed qty",
    )
    add_threshold(
        failures,
        "seed_qty_over_top5_depth_max",
        thresholds.get("seed_qty_over_top5_depth_max"),
        None if max_seed_ratio is None else round(max_seed_ratio, 6),
        max_seed_ratio is not None and max_seed_ratio <= float(thresholds.get("seed_qty_over_top5_depth_max", 0.0)),
        "maximum seed_qty_over_top5_depth observed or derived",
    )
    add_threshold(
        failures,
        "observed_residual_cost_share",
        thresholds.get("observed_residual_cost_share_max"),
        None if residual_cost_share is None else round(residual_cost_share, 6),
        residual_cost_share is not None
        and residual_cost_share <= float(thresholds.get("observed_residual_cost_share_max", 0.0)),
        "gate-summary observed_residual_cost_share when present; otherwise sum(row residual proxy) / sum(row pair cost proxy)",
    )

    fee_metric_name, fee_metric = optional_metric_sum(
        normalized,
        (
            "fee_1_50_zero_stress_after_fee_pnl",
            "observed_fee_1_50_zero_stress_after_fee_pnl",
            "fee_multiplier_1_5_shadow_proxy_pnl",
            "fee_1_50_shadow_proxy_pnl",
        ),
    )
    if fee_metric is None:
        fee_metric = fnum(
            nested_get(
                gate_summary or {},
                (
                    "fee_1_50_zero_stress_after_fee_pnl",
                    "observed_fee_1_50_zero_stress_after_fee_pnl",
                    "fee_multiplier_1_5_shadow_proxy_pnl",
                    "fee_1_50_shadow_proxy_pnl",
                ),
            )
        )
        if fee_metric is not None:
            fee_metric_name = "gate_summary_fee_1_50_zero_stress_after_fee_pnl"
    add_threshold(
        failures,
        "fee_multiplier_stress_positive",
        f">0 at multiplier {thresholds.get('fee_multiplier_stress_required_positive')}",
        "missing_metric" if fee_metric is None else round(fee_metric, 6),
        fee_metric is not None and fee_metric > 0,
        (
            "requires one of fee_1_50_zero_stress_after_fee_pnl,"
            " observed_fee_1_50_zero_stress_after_fee_pnl,"
            " fee_multiplier_1_5_shadow_proxy_pnl, fee_1_50_shadow_proxy_pnl,"
            " or gate summary aggregate metric"
            if fee_metric is None
            else f"summed optional metric column {fee_metric_name}"
        ),
    )

    haircut_metric_name, haircut_metric = optional_metric_sum(
        normalized,
        (
            "pair_edge_50pct_zero_stress_after_fee_pnl",
            "observed_pair_edge_50pct_zero_stress_after_fee_pnl",
            "pair_edge_haircut_0_5_pnl",
        ),
    )
    if haircut_metric is None:
        haircut_metric = fnum(
            nested_get(
                gate_summary or {},
                (
                    "pair_edge_50pct_zero_stress_after_fee_pnl",
                    "observed_pair_edge_50pct_zero_stress_after_fee_pnl",
                    "pair_edge_haircut_0_5_pnl",
                ),
            )
        )
        if haircut_metric is not None:
            haircut_metric_name = "gate_summary_pair_edge_50pct_zero_stress_after_fee_pnl"
    add_threshold(
        failures,
        "observed_pair_edge_haircut_positive",
        f">0 at haircut floor {thresholds.get('observed_pair_edge_haircut_floor')}",
        "missing_metric" if haircut_metric is None else round(haircut_metric, 6),
        haircut_metric is not None and haircut_metric > 0,
        (
            "requires one of pair_edge_50pct_zero_stress_after_fee_pnl,"
            " observed_pair_edge_50pct_zero_stress_after_fee_pnl, pair_edge_haircut_0_5_pnl,"
            " or gate summary aggregate metric"
            if haircut_metric is None
            else f"summed optional metric column {haircut_metric_name}"
        ),
    )

    daily_metric_names = (
        "daily_residual_zero_proxy_pnl",
        "observed_residual_zero_after_fee_pnl",
        "residual_zero_after_fee_pnl",
    )
    daily_values: dict[str, float] = {}
    daily_metric_used: str | None = None
    for metric_name in daily_metric_names:
        rows_with_metric = [row for row in normalized if row.get(metric_name) not in (None, "")]
        if rows_with_metric:
            daily_metric_used = metric_name
            for row in rows_with_metric:
                day = str(row.get("day") or "")
                daily_values[day] = daily_values.get(day, 0.0) + (fnum(row.get(metric_name), 0.0) or 0.0)
            break
    negative_daily_count = sum(1 for value in daily_values.values() if value < 0)
    if daily_metric_used is None:
        sidecar_negative_daily_count = nested_get(
            gate_summary or {},
            (
                "daily_residual_zero_negative_count",
                "daily_residual_zero_proxy_negative_count",
                "observed_daily_residual_zero_negative_count",
            ),
        )
        if sidecar_negative_daily_count not in (None, ""):
            negative_daily_count = int(fnum(sidecar_negative_daily_count, 0.0) or 0.0)
            daily_metric_used = "gate_summary_daily_residual_zero_negative_count"
    add_threshold(
        failures,
        "daily_residual_zero_proxy_negative_count",
        thresholds.get("daily_residual_zero_proxy_negative_allowed"),
        "missing_metric" if daily_metric_used is None else negative_daily_count,
        daily_metric_used is not None
        and negative_daily_count <= int(thresholds.get("daily_residual_zero_proxy_negative_allowed", 0)),
        (
            "requires daily_residual_zero_proxy_pnl, observed_residual_zero_after_fee_pnl, or residual_zero_after_fee_pnl"
            if daily_metric_used is None
            else f"daily aggregation of optional metric column {daily_metric_used}"
        ),
    )

    stop_events = build_stop_events(normalized)
    add_threshold(
        failures,
        "orders_sent_required_false",
        thresholds.get("orders_sent_required"),
        any(parse_bool(row.get("orders_sent")) for row in normalized),
        not any(parse_bool(row.get("orders_sent")) for row in normalized),
        "orders_sent must stay false in no-order shadow",
    )
    add_threshold(
        failures,
        "cancels_sent_required_false",
        False,
        any(parse_bool(row.get("cancels_sent")) for row in normalized),
        not any(parse_bool(row.get("cancels_sent")) for row in normalized),
        "cancels_sent must stay false in no-order shadow",
    )
    add_threshold(
        failures,
        "redeems_sent_required_false",
        False,
        any(parse_bool(row.get("redeems_sent")) for row in normalized),
        not any(parse_bool(row.get("redeems_sent")) for row in normalized),
        "redeems_sent must stay false in no-order shadow",
    )
    add_threshold(
        failures,
        "live_orders_allowed_required_false",
        thresholds.get("live_orders_allowed_required"),
        any(parse_bool(row.get("live_orders_allowed")) for row in normalized),
        not any(parse_bool(row.get("live_orders_allowed")) for row in normalized),
        "live_orders_allowed must stay false in no-order shadow",
    )

    summary = {
        "row_count": row_count,
        "candidate_count": candidate_count,
        "market_count": market_count,
        "day_count": day_count,
        "action_match_count": action_match_count,
        "book_action_match_rate": round(action_match_rate, 6),
        "l2_or_live_book_action_match_rate": round(action_match_rate, 6),
        "latency_p50_ms": None if latency_p50 is None else round(latency_p50, 6),
        "latency_p95_ms": None if latency_p95 is None else round(latency_p95, 6),
        "latency_max_ms": None if latency_max is None else round(latency_max, 6),
        "book_age_p50_ms": None if book_age_p50 is None else round(book_age_p50, 6),
        "book_age_p95_ms": None if book_age_p95 is None else round(book_age_p95, 6),
        "book_age_max_ms": None if book_age_max is None else round(book_age_max, 6),
        "top5_support_count": top5_support_count,
        "top5_supports_seed_qty_rate": round(top5_support_rate, 6),
        "seed_qty_over_top5_depth_max": None if max_seed_ratio is None else round(max_seed_ratio, 6),
        "observed_pair_cost_proxy_sum": round(pair_cost, 6),
        "observed_residual_cost_proxy_sum": round(residual_cost, 6),
        "observed_residual_cost_share": None if residual_cost_share is None else round(residual_cost_share, 6),
        "fee_stress_metric_column": fee_metric_name,
        "fee_stress_metric_sum": None if fee_metric is None else round(fee_metric, 6),
        "pair_edge_haircut_metric_column": haircut_metric_name,
        "pair_edge_haircut_metric_sum": None if haircut_metric is None else round(haircut_metric, 6),
        "daily_residual_zero_metric_column": daily_metric_used,
        "daily_residual_zero_negative_count": None if daily_metric_used is None else negative_daily_count,
        "stop_condition_event_count": len(stop_events),
    }
    return summary, failures, stop_events


def add_sidecar_validations(
    threshold_rows: list[dict[str, Any]],
    audit_manifest_path: Path,
    audit_manifest: dict[str, Any],
    audit_load_error: str,
    gate_summary_path: Path,
    gate_summary: dict[str, Any],
    gate_summary_load_error: str,
    shadow_report: Path,
    approval_packet_path: Path,
    approval_packet: dict[str, Any],
    required_columns_csv: Path,
    required_columns_from_csv: list[str],
    required_columns_from_spec: list[str],
    normalized: list[dict[str, Any]],
) -> None:
    audit_present = audit_manifest_path.exists()
    gate_present = gate_summary_path.exists()
    add_threshold(
        threshold_rows,
        "audit_manifest_present",
        str(audit_manifest_path),
        "present" if audit_present else "missing",
        audit_present and not audit_load_error,
        "three-file no-order package requires the audit manifest sidecar",
    )
    add_threshold(
        threshold_rows,
        "gate_summary_present",
        str(gate_summary_path),
        "present" if gate_present else "missing",
        gate_present and not gate_summary_load_error,
        "three-file no-order package requires the aggregate gate summary sidecar",
    )
    add_threshold(
        threshold_rows,
        "required_columns_csv_present",
        str(required_columns_csv),
        "present" if required_columns_csv.exists() else "missing",
        required_columns_csv.exists() and len(required_columns_from_csv) == 33,
        "strict schema is sourced from required_shadow_report_columns.csv",
    )
    add_threshold(
        threshold_rows,
        "required_columns_csv_matches_gate_spec",
        "required CSV columns equal JSON gate spec columns",
        "match" if required_columns_from_csv == required_columns_from_spec else "mismatch",
        bool(required_columns_from_csv) and required_columns_from_csv == required_columns_from_spec,
        "required_shadow_report_columns.csv must remain the same 33-column contract as the gate spec",
    )

    safety = audit_manifest.get("safety") if isinstance(audit_manifest.get("safety"), dict) else {}
    source_continuity = (
        audit_manifest.get("source_continuity")
        if isinstance(audit_manifest.get("source_continuity"), dict)
        else {}
    )
    main_report = audit_manifest.get("main_report") if isinstance(audit_manifest.get("main_report"), dict) else {}
    runtime_config = (
        audit_manifest.get("runtime_config") if isinstance(audit_manifest.get("runtime_config"), dict) else {}
    )
    candidate_binding = (
        audit_manifest.get("candidate_binding")
        if isinstance(audit_manifest.get("candidate_binding"), dict)
        else {}
    )
    input_hashes = audit_manifest.get("input_hashes") if isinstance(audit_manifest.get("input_hashes"), dict) else {}
    book_audit = audit_manifest.get("book_audit") if isinstance(audit_manifest.get("book_audit"), dict) else {}
    resolver_audit = (
        audit_manifest.get("resolver_audit")
        if isinstance(audit_manifest.get("resolver_audit"), dict)
        else {}
    )
    report_kind, runner_kind = classify_report_kind(audit_manifest, gate_summary)
    call_count_fields = (
        "order_api_call_count",
        "cancel_api_call_count",
        "redeem_api_call_count",
        "candidate_import_call_count",
    )
    calls_zero = all(field in safety and fnum(safety.get(field), 1.0) == 0.0 for field in call_count_fields)
    false_safety_fields = (
        "import_enabled",
        "candidate_import_allowed",
        "live_orders_allowed",
        "deployable",
        "private_key_loaded",
        "orders_sent",
        "cancels_sent",
        "redeems_sent",
    )
    false_safety_ok = all(field in safety and parse_optional_bool(safety.get(field)) is False for field in false_safety_fields)
    null_client_ok = bool(safety.get("null_order_client_or_stub")) or safety.get("order_client_type") == "NullOrderClient"
    no_private_key_ok = (
        "no_private_key_loaded" in safety
        and parse_optional_bool(safety.get("no_private_key_loaded")) is True
    ) or ("private_key_loaded" in safety and parse_optional_bool(safety.get("private_key_loaded")) is False)
    report_sha = sha256_file(shadow_report) if shadow_report.exists() else ""
    audit_report_sha = str(main_report.get("sha256") or "")
    required_columns_sha = sha256_file(required_columns_csv) if required_columns_csv.exists() else ""

    add_threshold(
        threshold_rows,
        "audit_manifest_runner_kind_real_readonly",
        REAL_RUNNER_KIND,
        runner_kind or "missing",
        audit_present and runner_kind == REAL_RUNNER_KIND,
        "audit manifest must declare runner_kind=real_readonly_ws_no_order_observer",
    )
    add_threshold(
        threshold_rows,
        "report_kind_real_runner_not_public_l2_proxy",
        f"{REAL_RUNNER_KIND}, not {PUBLIC_L2_PROXY_KIND}",
        report_kind,
        audit_present and report_kind == REAL_RUNNER_KIND,
        "public_l2_proxy is historical proxy evidence only and cannot satisfy the real runner gate",
    )
    add_threshold(
        threshold_rows,
        "audit_manifest_real_ws_transport_declared",
        "book_ws_used=true or transport contains ws",
        f"book_ws_used={runtime_config.get('book_ws_used')} transport={book_audit.get('transport')}",
        audit_present
        and (
            parse_optional_bool(runtime_config.get("book_ws_used")) is True
            or "ws" in str(book_audit.get("transport") or "").lower()
        ),
        "real read-only WS no-order evaluator requires a real WS observation path, not only historical/public REST proxy",
    )

    add_threshold(
        threshold_rows,
        "audit_manifest_import_and_live_disabled",
        "import_enabled=false,candidate_import_allowed=false,live/deployable/order side effects false",
        "ok" if false_safety_ok else "bad_safety_flag",
        audit_present and false_safety_ok,
        "audit manifest safety flags must stay false outside the strict 33-column main CSV",
    )
    add_threshold(
        threshold_rows,
        "audit_manifest_call_counts_zero",
        "order/cancel/redeem/import API call counts all zero",
        "ok" if calls_zero else "nonzero_call_count",
        audit_present and calls_zero,
        "no-order package must prove no order/cancel/redeem/import calls",
    )
    add_threshold(
        threshold_rows,
        "audit_manifest_null_order_client_no_private_key",
        "NullOrderClient/stub and no private key",
        "ok" if null_client_ok and no_private_key_ok else "missing_stub_or_private_key_flag",
        audit_present and null_client_ok and no_private_key_ok,
        "runner package must prove private keys were not loaded and order client is stubbed",
    )
    add_threshold(
        threshold_rows,
        "audit_manifest_source_fingerprint_continuity",
        "source_continuity.passed=true",
        source_continuity.get("passed"),
        audit_present and source_continuity.get("passed") is True,
        "source fingerprint continuity belongs in the audit manifest, not in the main CSV",
    )
    add_threshold(
        threshold_rows,
        "audit_manifest_main_report_hash_match",
        "audit main_report.sha256 == main CSV sha256",
        "match" if audit_report_sha and audit_report_sha == report_sha else "mismatch_or_missing",
        audit_present and bool(audit_report_sha) and audit_report_sha == report_sha,
        "audit manifest must bind the exact main report file",
    )
    add_threshold(
        threshold_rows,
        "audit_manifest_required_columns_hash_match",
        required_columns_sha,
        input_hashes.get("required_shadow_report_columns_sha256"),
        audit_present
        and bool(required_columns_sha)
        and input_hashes.get("required_shadow_report_columns_sha256") == required_columns_sha,
        "audit manifest must bind required_shadow_report_columns.csv",
    )
    add_threshold(
        threshold_rows,
        "runtime_candidate_binding_btc_only_52_rows",
        "row_count=52 asset_scope=['BTC']",
        f"row_count={candidate_binding.get('row_count')} asset_scope={candidate_binding.get('asset_scope')}",
        audit_present
        and int(fnum(candidate_binding.get("row_count"), -1.0) or -1) == 52
        and candidate_binding.get("asset_scope") == ["BTC"],
        "real no-order package must bind the BTC-only 52-row runtime candidate package",
    )

    expected_fingerprints = (
        approval_packet.get("runtime_binding_fingerprints")
        if isinstance(approval_packet.get("runtime_binding_fingerprints"), dict)
        else {}
    )
    fingerprint_pairs = {
        "runtime_config_sha256": input_hashes.get("runtime_config_sha256"),
        "runtime_candidate_binding_sha256": input_hashes.get("runtime_candidate_binding_sha256"),
        "source_semantics_contract_sha256": input_hashes.get("source_semantics_contract_sha256"),
        "source_import_contract_sha256": input_hashes.get("source_import_contract_sha256"),
    }
    fingerprint_mismatches = [
        key
        for key, observed in fingerprint_pairs.items()
        if not observed or expected_fingerprints.get(key) != observed
    ]
    add_threshold(
        threshold_rows,
        "approval_packet_runtime_fingerprint_continuity",
        "runtime config/candidate binding/source semantics/import contract hashes match approval packet",
        "match" if not fingerprint_mismatches else ",".join(fingerprint_mismatches),
        approval_packet_path.exists() and bool(expected_fingerprints) and not fingerprint_mismatches,
        "audit manifest input_hashes must bind the approved BTC-only runtime package",
    )

    resolver_errors = resolver_audit.get("errors") if isinstance(resolver_audit.get("errors"), list) else []
    resolved_markets = (
        resolver_audit.get("resolved_markets") if isinstance(resolver_audit.get("resolved_markets"), list) else []
    )
    resolver_by_slug: dict[str, dict[str, Any]] = {}
    ambiguous_slugs: set[str] = set()
    for item in resolved_markets:
        if not isinstance(item, dict):
            continue
        slug = str(item.get("slug") or "")
        if not slug:
            continue
        if slug in resolver_by_slug and resolver_by_slug[slug] != item:
            ambiguous_slugs.add(slug)
        resolver_by_slug[slug] = item
    resolver_row_errors: list[str] = []
    distinct_market_slugs = {str(row.get("slug") or "") for row in normalized if row.get("slug")}
    for row in normalized:
        slug = str(row.get("slug") or "")
        market_id = str(row.get("market_id") or "")
        token_yes = str(row.get("token_id_yes") or "")
        token_no = str(row.get("token_id_no") or "")
        condition_id = str(row.get("condition_id") or "")
        if not market_id or not token_yes or not token_no:
            resolver_row_errors.append(f"row={row.get('row_index')} missing_market_or_token")
            continue
        if condition_id and market_id and condition_id != market_id:
            resolver_row_errors.append(f"row={row.get('row_index')} condition_id_market_id_mismatch")
        resolved = resolver_by_slug.get(slug)
        if not resolved:
            resolver_row_errors.append(f"row={row.get('row_index')} slug_not_resolved")
            continue
        expected_market = str(resolved.get("market_id") or "")
        expected_yes = str(resolved.get("yes_asset_id") or resolved.get("token_id_yes") or "")
        expected_no = str(resolved.get("no_asset_id") or resolved.get("token_id_no") or "")
        if market_id != expected_market or token_yes != expected_yes or token_no != expected_no:
            resolver_row_errors.append(f"row={row.get('row_index')} resolver_tuple_mismatch")
    resolver_full_coverage = (
        audit_present
        and resolver_audit.get("passed") is True
        and not resolver_errors
        and not ambiguous_slugs
        and not resolver_row_errors
        and distinct_market_slugs.issubset(set(resolver_by_slug))
    )
    add_threshold(
        threshold_rows,
        "resolver_audit_full_row_market_token_coverage",
        "every row market/token tuple resolved, unambiguous, and consistent",
        "ok" if resolver_full_coverage else f"errors={len(resolver_errors)} row_errors={len(resolver_row_errors)} ambiguous={len(ambiguous_slugs)}",
        resolver_full_coverage,
        "market_id/token_id_yes/token_id_no must be present and match resolver audit for every row",
    )

    ws_disconnect_count = fnum(
        nested_find_any(
            audit_manifest,
            (
                "runtime_config.ws_disconnect_count",
                "book_audit.ws_disconnect_count",
                "transport.ws_disconnect_count",
            ),
        )
    )
    add_threshold(
        threshold_rows,
        "ws_disconnect_count_zero",
        0,
        "missing" if ws_disconnect_count is None else int(ws_disconnect_count),
        ws_disconnect_count is not None and ws_disconnect_count == 0,
        "WS disconnects must be reported and zero; missing disconnect telemetry is fail-closed",
    )
    book_failures = book_audit.get("book_failures") if isinstance(book_audit.get("book_failures"), list) else []
    add_threshold(
        threshold_rows,
        "book_audit_failures_empty",
        "[]",
        len(book_failures),
        audit_present and not book_failures,
        "book failures must be explicit and empty",
    )

    gate_thresholds = gate_summary.get("thresholds") if isinstance(gate_summary.get("thresholds"), list) else []
    gate_failed = [
        row.get("threshold_name")
        for row in gate_thresholds
        if isinstance(row, dict) and row.get("passed") is not True
    ]
    promotion_gate = gate_summary.get("promotion_gate") if isinstance(gate_summary.get("promotion_gate"), dict) else {}
    promotion_false = all(
        parse_optional_bool(promotion_gate.get(field)) is False
        for field in ("private_truth_ready", "strategy_promotion_ready", "live_ready", "live_orders_allowed")
    )
    add_threshold(
        threshold_rows,
        "gate_summary_evaluation_passed",
        "evaluation_passed=true and no failed aggregate thresholds",
        gate_summary.get("evaluation_passed"),
        gate_present and gate_summary.get("evaluation_passed") is True and not gate_failed,
        "aggregate threshold evidence belongs in gate summary sidecar",
    )
    add_threshold(
        threshold_rows,
        "gate_summary_promotion_private_live_false",
        "private/promotion/live gates false",
        "ok" if promotion_false else "bad_promotion_gate",
        gate_present and promotion_false,
        "no-order shadow cannot claim private truth, promotion readiness, or live readiness",
    )

    required_summary_metrics = {
        "row_count": ("summary.row_count", "row_count"),
        "candidate_count": ("summary.candidate_count", "summary.observed_candidate_count", "candidate_count"),
        "market_count": ("summary.market_count", "summary.observed_market_count", "market_count"),
        "book_action_match_rate": (
            "summary.book_action_match_rate",
            "summary.l2_or_live_book_action_match_rate",
            "book_action_match_rate",
        ),
        "latency_p50_ms": ("summary.latency_p50_ms", "latency_p50_ms"),
        "latency_p95_ms": ("summary.latency_p95_ms", "latency_p95_ms"),
        "latency_max_ms": ("summary.latency_max_ms", "latency_max_ms"),
        "book_age_p50_ms": ("summary.book_age_p50_ms", "book_age_p50_ms"),
        "book_age_p95_ms": ("summary.book_age_p95_ms", "book_age_p95_ms"),
        "book_age_max_ms": ("summary.book_age_max_ms", "book_age_max_ms"),
        "top5_supports_seed_qty_rate": ("summary.top5_supports_seed_qty_rate", "top5_supports_seed_qty_rate"),
        "seed_qty_over_top5_depth_max": ("summary.seed_qty_over_top5_depth_max", "seed_qty_over_top5_depth_max"),
        "observed_pair_cost_proxy": (
            "summary.observed_pair_cost_proxy",
            "summary.observed_pair_cost_proxy_sum",
            "observed_pair_cost_proxy",
        ),
        "observed_residual_cost_proxy": (
            "summary.observed_residual_cost_proxy",
            "summary.observed_residual_cost_proxy_sum",
            "observed_residual_cost_proxy",
        ),
        "threshold_failures": ("threshold_failures", "summary.threshold_failures", "summary.threshold_failure_count"),
    }
    missing_summary_metrics = [
        name for name, paths in required_summary_metrics.items() if nested_find_any(gate_summary, paths) in (None, "")
    ]
    add_threshold(
        threshold_rows,
        "gate_summary_required_real_ws_metrics_present",
        ",".join(required_summary_metrics),
        "all_present" if not missing_summary_metrics else ",".join(missing_summary_metrics),
        gate_present and not missing_summary_metrics,
        "gate summary must publish the real WS/public-book aggregate telemetry contract",
    )


def append_fail_closed_stop_events(
    stop_events: list[dict[str, Any]],
    threshold_rows: list[dict[str, Any]],
    audit_manifest: dict[str, Any],
) -> None:
    fail_closed_thresholds = {
        "required_shadow_report_columns_exact",
        "audit_manifest_runner_kind_real_readonly",
        "report_kind_real_runner_not_public_l2_proxy",
        "audit_manifest_real_ws_transport_declared",
        "audit_manifest_import_and_live_disabled",
        "audit_manifest_call_counts_zero",
        "audit_manifest_null_order_client_no_private_key",
        "audit_manifest_source_fingerprint_continuity",
        "approval_packet_runtime_fingerprint_continuity",
        "resolver_audit_full_row_market_token_coverage",
        "ws_disconnect_count_zero",
        "book_audit_failures_empty",
        "minimum_candidates_observed",
        "minimum_days",
        "l2_or_live_book_action_match_rate",
        "book_age_p95_ms",
        "top5_supports_seed_qty_rate",
        "seed_qty_over_top5_depth_max",
        "observed_residual_cost_share",
        "gate_summary_required_real_ws_metrics_present",
    }
    for row in threshold_rows:
        name = str(row.get("threshold_name") or "")
        if name in fail_closed_thresholds and str(row.get("passed")) != "true":
            stop_events.append(
                {
                    "event_name": f"fail_closed_threshold:{name}",
                    "severity": "stop",
                    "row_index": "",
                    "candidate_id": "",
                    "day": "",
                    "condition_id": "",
                    "detail": row.get("detail", ""),
                }
            )

    resolver = audit_manifest.get("resolver_audit") if isinstance(audit_manifest.get("resolver_audit"), dict) else {}
    for err in resolver.get("errors") or []:
        stop_events.append(
            {
                "event_name": "resolver_audit_error",
                "severity": "stop",
                "row_index": "",
                "candidate_id": "",
                "day": "",
                "condition_id": "",
                "detail": str(err),
            }
        )
    book_audit = audit_manifest.get("book_audit") if isinstance(audit_manifest.get("book_audit"), dict) else {}
    for err in book_audit.get("book_failures") or []:
        stop_events.append(
            {
                "event_name": "book_audit_failure",
                "severity": "stop",
                "row_index": "",
                "candidate_id": "",
                "day": "",
                "condition_id": "",
                "detail": str(err),
            }
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gate-spec", type=Path, default=DEFAULT_GATE_SPEC)
    parser.add_argument("--required-columns-csv", type=Path, default=DEFAULT_REQUIRED_COLUMNS_CSV)
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--shadow-report", type=Path)
    parser.add_argument("--audit-manifest", type=Path)
    parser.add_argument("--gate-summary", type=Path)
    parser.add_argument("--approval-packet", type=Path, default=DEFAULT_APPROVAL_PACKET)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    gate_spec = args.gate_spec.expanduser()
    required_columns_csv = args.required_columns_csv.expanduser()
    if args.report_dir:
        report_dir = args.report_dir.expanduser()
    elif args.shadow_report:
        report_dir = args.shadow_report.expanduser().parent
    elif DEFAULT_REAL_SHADOW_REPORT_PRIMARY.exists():
        report_dir = DEFAULT_REAL_SHADOW_REPORT_DIR
    else:
        report_dir = DEFAULT_SHADOW_REPORT_DIR

    if args.shadow_report:
        shadow_report = args.shadow_report.expanduser()
    else:
        shadow_report = report_dir / "no_order_shadow_report.csv"
        if not shadow_report.exists() and report_dir == DEFAULT_SHADOW_REPORT_DIR and DEFAULT_SHADOW_REPORT.exists():
            shadow_report = DEFAULT_SHADOW_REPORT
    audit_manifest_path = (
        args.audit_manifest.expanduser()
        if args.audit_manifest
        else first_existing_path(report_dir / "no_order_shadow_audit_manifest.json", report_dir / "NO_ORDER_SHADOW_AUDIT_MANIFEST.json")
    )
    gate_summary_path = (
        args.gate_summary.expanduser()
        if args.gate_summary
        else first_existing_path(report_dir / "no_order_shadow_gate_summary.json", report_dir / "NO_ORDER_SHADOW_GATE_SUMMARY.json")
    )
    approval_packet_path = args.approval_packet.expanduser()
    output_dir = args.output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    spec = read_json(gate_spec)
    evaluation_gate = spec.get("shadow_evaluation_gate") or {}
    required_columns_from_spec = list(evaluation_gate.get("required_shadow_report_columns") or [])
    required_columns_from_csv = read_required_columns_csv(required_columns_csv)
    required_columns = required_columns_from_csv or required_columns_from_spec
    thresholds = dict(evaluation_gate.get("pass_thresholds") or {})

    normalized_path = output_dir / "observed_shadow_report_normalized.csv"
    failures_path = output_dir / "threshold_failures.csv"
    stop_events_path = output_dir / "stop_condition_events.csv"
    manifest_path = output_dir / "XUAN_BTC_TINY_CANARY_NO_ORDER_SHADOW_EVAL.json"

    normalized: list[dict[str, Any]] = []
    threshold_rows: list[dict[str, Any]] = []
    stop_events: list[dict[str, Any]] = []
    summary: dict[str, Any] = {
        "row_count": 0,
        "candidate_count": 0,
        "day_count": 0,
        "stop_condition_event_count": 0,
    }
    report_present = shadow_report.exists()
    report_load_error = ""
    audit_load_error = ""
    gate_summary_load_error = ""
    audit_manifest: dict[str, Any] = {}
    gate_summary: dict[str, Any] = {}
    approval_packet = read_json(approval_packet_path)
    if audit_manifest_path.exists():
        try:
            audit_manifest = read_json(audit_manifest_path)
        except Exception as exc:  # pragma: no cover - artifact corruption handling
            audit_load_error = repr(exc)
    if gate_summary_path.exists():
        try:
            gate_summary = read_json(gate_summary_path)
        except Exception as exc:  # pragma: no cover - artifact corruption handling
            gate_summary_load_error = repr(exc)
    source_columns: list[str] = []

    if not report_present:
        add_threshold(
            threshold_rows,
            "shadow_report_present",
            str(shadow_report),
            "missing",
            False,
            "runner no-order shadow report has not been emitted yet",
        )
        status = "BLOCKED_XUAN_BTC_TINY_CANARY_NO_ORDER_SHADOW_REPORT_MISSING"
    else:
        try:
            report_rows, source_columns = read_shadow_report(shadow_report)
            normalized = normalize_rows(report_rows, required_columns)
            summary, threshold_rows, stop_events = evaluate_rows(
                normalized,
                source_columns,
                required_columns,
                thresholds,
                gate_summary,
            )
            add_sidecar_validations(
                threshold_rows,
                audit_manifest_path,
                audit_manifest,
                audit_load_error,
                gate_summary_path,
                gate_summary,
                gate_summary_load_error,
                shadow_report,
                approval_packet_path,
                approval_packet,
                required_columns_csv,
                required_columns_from_csv,
                required_columns_from_spec,
                normalized,
            )
            append_fail_closed_stop_events(stop_events, threshold_rows, audit_manifest)
            failed_thresholds = [row for row in threshold_rows if str(row.get("passed")) != "true"]
            report_kind, runner_kind = classify_report_kind(audit_manifest, gate_summary)
            real_runner_evaluated = report_kind == REAL_RUNNER_KIND and not failed_thresholds and not stop_events
            status = (
                "KEEP_XUAN_BTC_TINY_CANARY_REAL_READONLY_WS_NO_ORDER_SHADOW_EVALUATED_PROMOTION_BLOCKED_OWNER_TRUTH"
                if real_runner_evaluated
                else "BLOCKED_XUAN_BTC_TINY_CANARY_NO_ORDER_SHADOW_EVAL_FAILED"
            )
        except Exception as exc:  # pragma: no cover - defensive artifact path handling
            report_load_error = repr(exc)
            add_threshold(
                threshold_rows,
                "shadow_report_readable",
                "csv/json/jsonl",
                report_load_error,
                False,
                "runner no-order shadow report could not be parsed",
            )
            status = "BLOCKED_XUAN_BTC_TINY_CANARY_NO_ORDER_SHADOW_REPORT_UNREADABLE"

    normalized_fields = list(NORMALIZED_FIELDS)
    optional_fields = sorted(
        {
            key
            for row in normalized
            for key in row
            if key not in normalized_fields
        }
    )
    write_csv(normalized_path, normalized, normalized_fields + optional_fields)
    write_csv(failures_path, threshold_rows, THRESHOLD_FIELDS)
    write_csv(stop_events_path, stop_events, STOP_EVENT_FIELDS)

    failed_thresholds = [row for row in threshold_rows if str(row.get("passed")) != "true"]
    report_kind, runner_kind = classify_report_kind(audit_manifest, gate_summary)
    no_order_shadow_real_runner_evaluated = (
        status
        == "KEEP_XUAN_BTC_TINY_CANARY_REAL_READONLY_WS_NO_ORDER_SHADOW_EVALUATED_PROMOTION_BLOCKED_OWNER_TRUTH"
    )
    public_l2_proxy_evaluated = report_kind == PUBLIC_L2_PROXY_KIND and not no_order_shadow_real_runner_evaluated
    manifest = {
        "schema_version": "xuan_btc_tiny_canary_no_order_shadow_eval_v1",
        "created_utc": utc_now(),
        "status": status,
        "report_kind": report_kind,
        "runner_kind": runner_kind,
        "no_order_shadow_real_runner_evaluated": no_order_shadow_real_runner_evaluated,
        "public_l2_proxy_evaluated": public_l2_proxy_evaluated,
        "gate_spec": {
            "path": str(gate_spec),
            "sha256": sha256_file(gate_spec) if gate_spec.exists() else "",
            "schema_version": spec.get("schema_version"),
            "status": spec.get("status"),
        },
        "required_columns_csv": {
            "path": str(required_columns_csv),
            "present": required_columns_csv.exists(),
            "sha256": sha256_file(required_columns_csv) if required_columns_csv.exists() else "",
            "column_count": len(required_columns_from_csv),
        },
        "input_shadow_report": {
            "path": str(shadow_report),
            "present": report_present,
            "sha256": sha256_file(shadow_report) if report_present else "",
            "columns": source_columns,
            "load_error": report_load_error,
        },
        "input_audit_manifest": {
            "path": str(audit_manifest_path),
            "present": audit_manifest_path.exists(),
            "sha256": sha256_file(audit_manifest_path) if audit_manifest_path.exists() else "",
            "load_error": audit_load_error,
        },
        "input_gate_summary": {
            "path": str(gate_summary_path),
            "present": gate_summary_path.exists(),
            "sha256": sha256_file(gate_summary_path) if gate_summary_path.exists() else "",
            "load_error": gate_summary_load_error,
        },
        "input_approval_packet": {
            "path": str(approval_packet_path),
            "present": approval_packet_path.exists(),
            "sha256": sha256_file(approval_packet_path) if approval_packet_path.exists() else "",
            "status": approval_packet.get("status"),
        },
        "outputs": {
            "manifest": str(manifest_path),
            "observed_shadow_report_normalized_csv": str(normalized_path),
            "observed_shadow_report_normalized_sha256": sha256_file(normalized_path),
            "threshold_failures_csv": str(failures_path),
            "threshold_failures_sha256": sha256_file(failures_path),
            "stop_condition_events_csv": str(stop_events_path),
            "stop_condition_events_sha256": sha256_file(stop_events_path),
        },
        "summary": {
            **summary,
            "threshold_count": len(threshold_rows),
            "threshold_failure_count": len(failed_thresholds),
            "failed_thresholds": [row.get("threshold_name") for row in failed_thresholds],
            "stop_condition_event_count": len(stop_events),
            "evaluation_passed": no_order_shadow_real_runner_evaluated,
            "no_order_shadow_real_runner_evaluated": no_order_shadow_real_runner_evaluated,
            "public_l2_proxy_evaluated": public_l2_proxy_evaluated,
        },
        "policy": {
            "phase": "no_order_shadow_post_run_evaluation",
            "research_only": True,
            "dry_run_only": True,
            "import_enabled": False,
            "orders_allowed": False,
            "live_orders_allowed": False,
            "candidate_import_allowed": False,
            "private_truth_ready": False,
            "owner_private_truth_data_ready": False,
            "strategy_promotion_ready": False,
            "deployable": False,
            "historical_shadow_or_v1_is_private_truth": False,
            "residual_settlement_pnl_is_strategy_edge": False,
        },
        "promotion_gate": {
            "canary_preflight_review_ready": no_order_shadow_real_runner_evaluated,
            "strategy_promotion_ready": False,
            "private_truth_ready": False,
            "live_ready": False,
            "live_orders_allowed": False,
            "future_owner_execution_reconciliation_required": True,
        },
        "notes": [
            "This evaluator is post-run no-order shadow telemetry only.",
            "It cannot validate owner order acceptance, fills, actual paid fees, inventory, redeem, or private truth.",
            "Missing optional stress/PnL columns are threshold failures, not inferred passes.",
        ],
        "manifest_fingerprint": sha256_text(json.dumps(summary, ensure_ascii=False, sort_keys=True)),
    }
    write_json(manifest_path, manifest)
    print(json.dumps({"status": status, "summary": manifest["summary"], "manifest": str(manifest_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
