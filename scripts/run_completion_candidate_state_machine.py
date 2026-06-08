#!/usr/bin/env python3
"""Run a residual/cooldown state machine over a materialized candidate base.

The default mode intentionally reproduces the local completion-store-only
passive/passive residual-cooldown probe, but with engineering-grade manifests:
candidate-level registry, strict-cache coverage checks, public-audit coverage
checks, and explicit non-deployable scope labels.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import shutil
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

try:
    import duckdb
except ModuleNotFoundError as exc:  # pragma: no cover - operational guard
    raise SystemExit("duckdb is required. Run with `uv run --with duckdb python ...`.") from exc


REGISTRY_DATASET_TYPE = "completion_unwind_state_machine_candidate_registry_v2"
SUMMARY_DATASET_TYPE = "completion_unwind_state_machine_result_summary_v2"
COMPLIANCE_DATASET_TYPE = "completion_unwind_state_machine_compliance_v1"
BLOCKED_LABELS = {"20260514", "20260515", "20260519"}
BLOCKED_DAYS = {"2026-05-14", "2026-05-15", "2026-05-19"}
DUST = 1e-9
OFFICIAL_CLOB_FEE_FORMULA = "fee = shares * fee_rate * price * (1 - price)"
OFFICIAL_CLOB_FEE_SOURCE = "https://docs.polymarket.com/trading/fees"


@dataclass
class Lot:
    qty: float
    px: float
    ts_ms: int
    side: str
    seed_action_id: int
    candidate_row_id: int


@dataclass
class MarketState:
    day: str
    slug: str
    winner_side: str | None
    inv: dict[str, deque[Lot]]
    last_seed_ts_ms: int = -(10**18)
    last_ts_ms: int = 0
    active: bool = False


@dataclass(frozen=True)
class SizingOverride:
    override_id: str
    target_qty: float | None
    max_open_cost: float | None
    enabled: bool
    source_row_number: int
    source_key_type: str
    source_key: str


@dataclass(frozen=True)
class EffectiveSizing:
    target_qty: float
    max_open_cost: float
    override_id: str | None
    override_key_type: str | None
    override_key: str | None
    enabled: bool


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def quote_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def quote_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    if fieldnames is None:
        fieldnames = []
        seen: set[str] = set()
        for row in rows:
            for key in row:
                if key not in seen:
                    seen.add(key)
                    fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def infer_duckdb_type(field: str, rows: list[dict[str, Any]]) -> str:
    """Infer a stable DuckDB type from Python values, avoiding CSV auto-casts.

    DuckDB's CSV sniffer treats YES/NO as BOOLEAN. That is unsafe for strategy
    side fields, where YES/NO are Polymarket outcome labels and must stay text.
    """

    force_text = {
        "candidate_id",
        "config_name",
        "source_label",
        "day",
        "condition_id",
        "slug",
        "ts_iso",
        "side",
        "opposite_side",
        "winner_side",
        "side_alignment",
        "candidate_reason",
        "blocked_by",
        "decision_scope",
        "sizing_override_id",
        "sizing_override_key_type",
        "sizing_override_key",
    }
    if field in force_text or field.endswith("_id") and field != "action_id":
        return "VARCHAR"
    for row in rows:
        value = row.get(field)
        if value is None:
            continue
        if isinstance(value, bool):
            return "BOOLEAN"
        if isinstance(value, int):
            return "BIGINT"
        if isinstance(value, float):
            return "DOUBLE"
        return "VARCHAR"
    if field.endswith("_count") or field.endswith("_actions") or field.endswith("_ms"):
        return "BIGINT"
    if field in {"deployable", "strict_cache_day_covered", "public_audit_day_covered"}:
        return "BOOLEAN"
    return "DOUBLE"


def create_table_from_rows(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    rows: list[dict[str, Any]],
    fieldnames: list[str],
) -> None:
    columns = [(field, infer_duckdb_type(field, rows)) for field in fieldnames]
    column_sql = ", ".join(f"{quote_ident(field)} {duckdb_type}" for field, duckdb_type in columns)
    conn.execute(f"CREATE TABLE {quote_ident(table)} ({column_sql})")
    if not rows:
        return
    placeholders = ", ".join("?" for _ in columns)
    insert_sql = f"INSERT INTO {quote_ident(table)} VALUES ({placeholders})"
    conn.executemany(insert_sql, [tuple(row.get(field) for field, _ in columns) for row in rows])


def create_table_from_csv(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    csv_path: Path,
    rows: list[dict[str, Any]],
    fieldnames: list[str],
) -> None:
    columns = [(field, infer_duckdb_type(field, rows)) for field in fieldnames]
    column_sql = ", ".join(f"{quote_ident(field)} {duckdb_type}" for field, duckdb_type in columns)
    conn.execute(f"CREATE TABLE {quote_ident(table)} ({column_sql})")
    if not rows:
        return
    conn.execute(
        f"COPY {quote_ident(table)} FROM {quote_literal(csv_path)} "
        "(HEADER TRUE, DELIMITER ',', QUOTE '\"', ESCAPE '\"')"
    )


def iso_ms(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def as_float(value: Any, default: float = math.nan) -> float:
    if value is None:
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def pct(num: float, den: float) -> float:
    return round(num / den, 6) if abs(den) > DUST else 0.0


def parse_optional_positive_float(value: Any, field: str, row_number: int) -> float | None:
    if value in (None, ""):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric at sizing override row {row_number}") from exc
    if not math.isfinite(out) or out <= 0:
        raise ValueError(f"{field} must be positive at sizing override row {row_number}")
    return out


def parse_optional_bool(value: Any, field: str, row_number: int, default: bool = True) -> bool:
    if value in (None, ""):
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "t", "yes", "y"}:
        return True
    if text in {"0", "false", "f", "no", "n"}:
        return False
    raise ValueError(f"{field} must be boolean at sizing override row {row_number}")


def load_sizing_overrides_csv(path: Path | None) -> dict[str, SizingOverride]:
    if path is None:
        return {}
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"missing sizing overrides CSV: {resolved}")
    overrides: dict[str, SizingOverride] = {}
    with resolved.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"empty sizing overrides CSV: {resolved}")
        supported_key_fields = ("candidate_row_id", "condition_id", "slug")
        missing_keys = [field for field in supported_key_fields if field not in reader.fieldnames]
        if len(missing_keys) == len(supported_key_fields):
            raise ValueError(
                "sizing overrides CSV must include at least one of candidate_row_id, condition_id, slug"
            )
        for row_number, row in enumerate(reader, start=2):
            target_qty = parse_optional_positive_float(row.get("target_qty"), "target_qty", row_number)
            max_open_cost = parse_optional_positive_float(row.get("max_open_cost"), "max_open_cost", row_number)
            enabled = parse_optional_bool(row.get("enabled"), "enabled", row_number, default=True)
            if enabled and target_qty is None and max_open_cost is None:
                raise ValueError(
                    f"sizing override row {row_number} must set target_qty or max_open_cost when enabled=true"
                )
            override_id = (
                row.get("sizing_override_id")
                or row.get("override_id")
                or row.get("schedule_id")
                or f"override_row_{row_number}"
            )
            override_id = str(override_id).strip()
            if not override_id:
                raise ValueError(f"sizing override row {row_number} has blank override id")
            key_values = [
                (field, str(row.get(field) or "").strip())
                for field in supported_key_fields
                if field in row and str(row.get(field) or "").strip()
            ]
            if not key_values:
                raise ValueError(
                    f"sizing override row {row_number} must populate candidate_row_id, condition_id, or slug"
                )
            for key_type, key_value in key_values:
                lookup_key = f"{key_type}:{key_value}"
                if lookup_key in overrides:
                    raise ValueError(f"duplicate sizing override key {lookup_key} at row {row_number}")
                overrides[lookup_key] = SizingOverride(
                    override_id=override_id,
                    target_qty=target_qty,
                    max_open_cost=max_open_cost,
                    enabled=enabled,
                    source_row_number=row_number,
                    source_key_type=key_type,
                    source_key=key_value,
                )
    return overrides


def resolve_sizing_override(row: dict[str, Any], overrides: dict[str, SizingOverride]) -> SizingOverride | None:
    if not overrides:
        return None
    for key_type in ("candidate_row_id", "condition_id", "slug"):
        value = row.get(key_type)
        if value in (None, ""):
            continue
        item = overrides.get(f"{key_type}:{value}")
        if item is not None:
            return item
    return None


def effective_sizing_for_row(args: argparse.Namespace, row: dict[str, Any]) -> EffectiveSizing:
    override = resolve_sizing_override(row, getattr(args, "sizing_overrides", {}))
    if override is None:
        return EffectiveSizing(
            target_qty=float(args.target_qty),
            max_open_cost=float(args.max_open_cost),
            override_id=None,
            override_key_type=None,
            override_key=None,
            enabled=True,
        )
    return EffectiveSizing(
        target_qty=float(override.target_qty if override.target_qty is not None else args.target_qty),
        max_open_cost=float(override.max_open_cost if override.max_open_cost is not None else args.max_open_cost),
        override_id=override.override_id,
        override_key_type=override.source_key_type,
        override_key=override.source_key,
        enabled=override.enabled,
    )


def official_clob_taker_fee(shares: float, price: float, fee_rate: float) -> float:
    if shares <= DUST or price < 0.0 or price > 1.0 or fee_rate <= 0.0:
        return 0.0
    return shares * fee_rate * price * (1.0 - price)


def seed_fill_fee(qty: float, price: float, args: argparse.Namespace) -> float:
    if args.fee_model == "none":
        return 0.0
    if args.fee_model == "official_taker":
        return official_clob_taker_fee(qty, price, args.official_fee_rate)
    if args.fee_model == "flat_notional":
        return qty * price * args.flat_notional_fee_rate
    raise ValueError(f"unknown fee model: {args.fee_model}")


def percentile(values: list[float], q: float) -> float | None:
    vals = sorted(v for v in values if math.isfinite(v))
    if not vals:
        return None
    if len(vals) == 1:
        return round(vals[0], 6)
    pos = (len(vals) - 1) * q / 100.0
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return round(vals[lo], 6)
    weight = pos - lo
    return round(vals[lo] * (1 - weight) + vals[hi] * weight, 6)


def summarize(values: Iterable[float | None]) -> dict[str, Any]:
    vals = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return {
        "count": len(vals),
        "avg": round(sum(vals) / len(vals), 6) if vals else None,
        "p10": percentile(vals, 10),
        "p50": percentile(vals, 50),
        "p90": percentile(vals, 90),
        "min": round(min(vals), 6) if vals else None,
        "max": round(max(vals), 6) if vals else None,
    }


def candidate_base_dir(path: Path) -> Path:
    return path if path.is_dir() else path.parent


def load_candidate_manifest(path: Path) -> dict[str, Any]:
    manifest_path = candidate_base_dir(path) / "CANDIDATE_BASE_MANIFEST.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"missing candidate base manifest: {manifest_path}")
    return load_json(manifest_path)


def output_dir_for(args: argparse.Namespace, manifest: dict[str, Any]) -> Path:
    if args.output_dir:
        return Path(args.output_dir).expanduser().resolve()
    root = Path(str(manifest["data_root"]))
    run_id = f"state_machine_{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    return root / "derived" / "completion_candidate_pipeline_v1" / run_id


def stable_id(*parts: Any, length: int = 20) -> str:
    h = hashlib.sha1()
    for part in parts:
        h.update(str(part).encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()[:length]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def other(side: str) -> str:
    return "NO" if side == "YES" else "YES"


def lot_qty(lots: deque[Lot]) -> float:
    return sum(lot.qty for lot in lots)


def lot_cost(lots: deque[Lot]) -> float:
    return sum(lot.qty * lot.px for lot in lots)


def aged_lot_cost(lots: deque[Lot], ts_ms: int, age_s: float) -> float:
    if age_s <= 0:
        return lot_cost(lots)
    cutoff_ms = age_s * 1000.0
    return sum(lot.qty * lot.px for lot in lots if ts_ms - lot.ts_ms >= cutoff_ms)


def new_metrics() -> defaultdict[str, float]:
    return defaultdict(float)


def ensure_side_state() -> dict[str, deque[Lot]]:
    return {"YES": deque(), "NO": deque()}


def pair_inventory(state: MarketState, metrics: defaultdict[str, float], ts_ms: int, day_metrics: defaultdict[str, defaultdict[str, float]]) -> dict[str, float]:
    yes = state.inv["YES"]
    no = state.inv["NO"]
    paired_qty = 0.0
    pair_cost_sum = 0.0
    pair_actions = 0
    while yes and no:
        a = yes[0]
        b = no[0]
        take = min(a.qty, b.qty)
        if take <= DUST:
            break
        pair_cost = a.px + b.px
        older_ts = min(a.ts_ms, b.ts_ms)
        pair_actions += 1
        paired_qty += take
        pair_cost_sum += take * pair_cost
        metrics["pair_actions"] += 1
        metrics["pair_qty"] += take
        metrics["pair_cost_sum"] += take * pair_cost
        metrics["net_pair_cost_sum"] += take * pair_cost
        metrics["pair_pnl"] += take * (1.0 - pair_cost)
        metrics["pair_delay_ms"] += take * max(0, ts_ms - older_ts)
        dm = day_metrics[state.day]
        dm["pair_actions"] += 1
        dm["pair_qty"] += take
        dm["pair_cost_sum"] += take * pair_cost
        dm["pair_pnl"] += take * (1.0 - pair_cost)
        dm["pair_delay_ms"] += take * max(0, ts_ms - older_ts)
        a.qty -= take
        b.qty -= take
        if a.qty <= DUST:
            yes.popleft()
        if b.qty <= DUST:
            no.popleft()
    return {
        "paired_qty": paired_qty,
        "pair_actions": float(pair_actions),
        "pair_cost_wavg": (pair_cost_sum / paired_qty) if paired_qty else math.nan,
    }


def settle_market(
    condition_id: str,
    state: MarketState,
    metrics: defaultdict[str, float],
    day_metrics: defaultdict[str, defaultdict[str, float]],
    residual_rows: list[dict[str, Any]],
) -> None:
    if not state.active:
        return
    pair_inventory(state, metrics, state.last_ts_ms, day_metrics)
    metrics["active_markets"] += 1
    day_metrics[state.day]["active_markets"] += 1
    winner = state.winner_side
    for side in ("YES", "NO"):
        for lot in state.inv[side]:
            if lot.qty <= DUST:
                continue
            cost = lot.qty * lot.px
            payout = lot.qty if winner == side else 0.0
            pnl = payout - cost
            metrics["residual_qty"] += lot.qty
            metrics["residual_cost"] += cost
            metrics["residual_settle_payout"] += payout
            metrics["residual_settle_pnl"] += pnl
            dm = day_metrics[state.day]
            dm["residual_qty"] += lot.qty
            dm["residual_cost"] += cost
            dm["residual_settle_payout"] += payout
            dm["residual_settle_pnl"] += pnl
            residual_rows.append(
                {
                    "condition_id": condition_id,
                    "day": state.day,
                    "slug": state.slug,
                    "winner_side": winner,
                    "side": side,
                    "qty": round(lot.qty, 6),
                    "px": round(lot.px, 6),
                    "cost": round(cost, 6),
                    "payout": round(payout, 6),
                    "pnl": round(pnl, 6),
                    "source_seed_action_id": lot.seed_action_id,
                    "candidate_row_id": lot.candidate_row_id,
                    "age_s": round(max(0, state.last_ts_ms - lot.ts_ms) / 1000.0, 6),
                }
            )


def finish_metrics(metrics: defaultdict[str, float], action_rows: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    buy_qty = metrics["gross_buy_qty"]
    buy_cost = metrics["gross_buy_cost"]
    pair_qty = metrics["pair_qty"]
    actual_pnl = metrics["pair_pnl"] + metrics["residual_settle_pnl"]
    fee = metrics["total_fee"]
    fee_after_pnl = actual_pnl - fee
    worst_residual_pnl = metrics["pair_pnl"] - metrics["residual_cost"]
    flat_residual_pnl = metrics["pair_pnl"]
    stress_shares = 2 * pair_qty + metrics["residual_qty"]
    out = {
        "active_markets": int(metrics["active_markets"]),
        "candidate_count": int(metrics["candidate_count"]),
        "selected_candidate_count": int(metrics["seed_actions"]),
        "seed_actions": int(metrics["seed_actions"]),
        "pair_actions": int(metrics["pair_actions"]),
        "cycles": int(metrics["pair_actions"]),
        "gross_buy_qty": round(buy_qty, 6),
        "gross_buy_cost": round(buy_cost, 6),
        "pair_qty": round(pair_qty, 6),
        "weighted_pair_cost": round(metrics["pair_cost_sum"] / pair_qty, 6) if pair_qty else None,
        "net_pair_cost_wavg": round(metrics["net_pair_cost_sum"] / pair_qty, 6) if pair_qty else None,
        "pair_delay_wavg_s": round(metrics["pair_delay_ms"] / pair_qty / 1000.0, 6) if pair_qty else 0.0,
        "pair_share_rate": pct(2 * pair_qty, buy_qty),
        "rounds_per_market": pct(metrics["pair_actions"], metrics["active_markets"]),
        "pair_pnl": round(metrics["pair_pnl"], 6),
        "gross_pnl": round(actual_pnl, 6),
        "net_pnl": round(fee_after_pnl, 6),
        "fee_model": args.fee_model,
        "official_fee_formula": OFFICIAL_CLOB_FEE_FORMULA if args.fee_model == "official_taker" else None,
        "official_fee_source": OFFICIAL_CLOB_FEE_SOURCE if args.fee_model == "official_taker" else None,
        "official_fee_rate": round(args.official_fee_rate, 8) if args.fee_model == "official_taker" else None,
        "flat_notional_fee_rate": round(args.flat_notional_fee_rate, 8) if args.fee_model == "flat_notional" else None,
        "official_taker_fee": round(fee, 6) if args.fee_model == "official_taker" else 0.0,
        "fee283": None,
        "fee_after_pnl": round(fee_after_pnl, 6),
        "actual_settle_pnl": round(actual_pnl, 6),
        "actual_settle_roi": pct(actual_pnl, buy_cost),
        "net_roi": pct(fee_after_pnl, buy_cost),
        "residual_qty": round(metrics["residual_qty"], 6),
        "residual_cost": round(metrics["residual_cost"], 6),
        "residual_settle_payout": round(metrics["residual_settle_payout"], 6),
        "residual_settle_pnl": round(metrics["residual_settle_pnl"], 6),
        "residual_qty_rate": pct(metrics["residual_qty"], buy_qty),
        "qty_residual_rate": pct(metrics["residual_qty"], buy_qty),
        "residual_cost_rate": pct(metrics["residual_cost"], buy_cost),
        "cost_residual_rate": pct(metrics["residual_cost"], buy_cost),
        "worst_residual_net_pnl": round(worst_residual_pnl, 6),
        "worst_residual_roi": pct(worst_residual_pnl, buy_cost),
        "flat_residual_net_pnl": round(flat_residual_pnl, 6),
        "flat_residual_roi": pct(flat_residual_pnl, buy_cost),
        "stress100_actual_pnl": round(actual_pnl - 0.01 * stress_shares, 6),
        "stress100_worst_pnl": round(worst_residual_pnl - 0.01 * stress_shares, 6),
        "seed_block_alignment": int(metrics["seed_block_alignment"]),
        "seed_block_offset": int(metrics["seed_block_offset"]),
        "seed_block_price_band": int(metrics["seed_block_price_band"]),
        "seed_block_l1_pair_cap": int(metrics["seed_block_l1_pair_cap"]),
        "seed_block_cooldown": int(metrics["seed_block_cooldown"]),
        "seed_block_target": int(metrics["seed_block_target"]),
        "seed_block_imbalance_qty": int(metrics["seed_block_imbalance_qty"]),
        "seed_block_imbalance_cost": int(metrics["seed_block_imbalance_cost"]),
        "seed_block_residual_cooldown": int(metrics["seed_block_residual_cooldown"]),
        "seed_block_sizing_override_disabled": int(metrics["seed_block_sizing_override_disabled"]),
        "sizing_override_match_rows": int(metrics["sizing_override_match_rows"]),
        "seed_px_distribution": summarize([as_float(row.get("seed_px")) for row in action_rows]),
        "offset_s_distribution": summarize([as_float(row.get("offset_s")) for row in action_rows]),
    }
    out["result_classification"] = classify_completion_only(out)
    out["status"] = out["result_classification"]
    out["deployable"] = False
    out["can_support_strategy_promotion"] = False
    out["conclusion_scope"] = (
        "local completion-store event-layer research only; strict-cache/public-audit coverage is compliance metadata, "
        "not private owner-trade truth; not source-of-truth replay; not deployable or canary-ready"
    )
    return out


def classify_completion_only(metrics: dict[str, Any]) -> str:
    if int(metrics.get("seed_actions") or 0) <= 0 or int(metrics.get("pair_actions") or 0) <= 0:
        return "DISCARD"
    if float(metrics.get("fee_after_pnl") or 0.0) > 0 and float(metrics.get("stress100_worst_pnl") or 0.0) > 0:
        return "PASS_LOCAL_COMPLETION_RESEARCH_ONLY"
    if float(metrics.get("fee_after_pnl") or 0.0) <= 0:
        return "DISCARD"
    return "UNKNOWN_NOT_DEPLOYABLE"


def candidate_query(args: argparse.Namespace) -> str:
    taker_filter = ""
    if args.public_trade_taker_side != "ANY":
        taker_filter = f"AND public_trade_taker_side = '{args.public_trade_taker_side}'"
    return f"""
    SELECT
      candidate_row_id,
      source_label,
      day,
      event_kind,
      event_id,
      condition_id,
      slug,
      ts_ms,
      offset_s,
      side,
      opposite_side,
      winner_side,
      side_alignment,
      l1_pair_ask,
      public_trade_taker_side,
      public_trade_price,
      public_trade_size,
      candidate_reason
    FROM candidate_base
    WHERE event_kind = 'public_trade'
      {taker_filter}
      AND side IN ('YES', 'NO')
      AND offset_s >= {float(args.offset_min_s)}
      AND offset_s < {float(args.offset_max_s)}
    ORDER BY condition_id, ts_ms, candidate_row_id
    """


def run_passive_redeem(
    args: argparse.Namespace,
    conn: duckdb.DuckDBPyConnection,
    manifest: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    cur = conn.execute(candidate_query(args))
    cols = [desc[0] for desc in cur.description]
    states: dict[str, MarketState] = {}
    metrics = new_metrics()
    day_metrics: defaultdict[str, defaultdict[str, float]] = defaultdict(new_metrics)
    actions: list[dict[str, Any]] = []
    residual_rows: list[dict[str, Any]] = []
    market_seen: set[str] = set()
    config_name = (
        f"dpass_{args.alignment}_e{int(round(args.edge * 1000)):03d}_t{int(args.target_qty):g}"
        f"_px{int(round(args.seed_px_lo * 1000)):03d}_{int(round(args.seed_px_hi * 1000)):03d}"
        f"_imb{int(round(args.imbalance_qty_cap * 100)):03d}"
        f"_rc{int(round(args.residual_cooldown_age_s)):02d}_{int(round(args.residual_cooldown_cost_cap * 100)):03d}"
    )
    if getattr(args, "sizing_overrides_sha256", None):
        config_name += f"_szov{str(args.sizing_overrides_sha256)[:8]}"
    action_id = 0
    candidate_base_manifest = candidate_base_dir(args.candidate_base_dir) / "CANDIDATE_BASE_MANIFEST.json"

    while True:
        batch = cur.fetchmany(50_000)
        if not batch:
            break
        for raw in batch:
            row = dict(zip(cols, raw))
            metrics["candidate_count"] += 1
            condition_id = str(row["condition_id"])
            market_seen.add(condition_id)
            side = str(row["side"] or "")
            if side not in ("YES", "NO"):
                continue
            ts_ms = int(row["ts_ms"] or 0)
            state = states.get(condition_id)
            if state is None:
                state = MarketState(
                    day=str(row["day"]),
                    slug=str(row["slug"]),
                    winner_side=str(row["winner_side"]) if row["winner_side"] else None,
                    inv=ensure_side_state(),
                )
                states[condition_id] = state
            elif state.winner_side is None and row["winner_side"]:
                state.winner_side = str(row["winner_side"])
            state.last_ts_ms = max(state.last_ts_ms, ts_ms)

            alignment = str(row["side_alignment"] or "")
            if args.alignment != "all" and alignment != args.alignment:
                metrics["seed_block_alignment"] += 1
                continue
            offset_s = as_float(row["offset_s"])
            if not (args.offset_min_s <= offset_s < args.seed_offset_max_s):
                metrics["seed_block_offset"] += 1
                continue
            trade_px = as_float(row["public_trade_price"])
            trade_size = as_float(row["public_trade_size"], 0.0)
            if trade_size <= DUST or math.isnan(trade_px):
                continue
            if not (args.seed_px_lo <= trade_px <= args.seed_px_hi):
                metrics["seed_block_price_band"] += 1
                continue
            l1_pair = as_float(row["l1_pair_ask"])
            if math.isnan(l1_pair) or l1_pair > args.seed_l1_pair_cap + 1e-12:
                metrics["seed_block_l1_pair_cap"] += 1
                continue
            effective_sizing = effective_sizing_for_row(args, row)
            if effective_sizing.override_id is not None:
                metrics["sizing_override_match_rows"] += 1
            if not effective_sizing.enabled:
                metrics["seed_block_sizing_override_disabled"] += 1
                continue
            if ts_ms - state.last_seed_ts_ms < int(args.cooldown_s * 1000):
                metrics["seed_block_cooldown"] += 1
                continue

            inv = state.inv
            same_qty = lot_qty(inv[side])
            opp_qty = lot_qty(inv[other(side)])
            aged_cost = aged_lot_cost(inv["YES"], ts_ms, args.residual_cooldown_age_s) + aged_lot_cost(
                inv["NO"], ts_ms, args.residual_cooldown_age_s
            )
            if aged_cost > args.residual_cooldown_cost_cap + 1e-12 and same_qty + args.dust_qty >= opp_qty:
                metrics["seed_block_residual_cooldown"] += 1
                continue
            if same_qty >= effective_sizing.target_qty - args.dust_qty:
                metrics["seed_block_target"] += 1
                continue
            same_cost = lot_cost(inv[side])
            opp_cost = lot_cost(inv[other(side)])
            if max(0.0, same_cost - opp_cost) > args.imbalance_cost_cap + 1e-12:
                metrics["seed_block_imbalance_cost"] += 1
                continue
            seed_px = max(args.min_seed_px, trade_px - args.edge)
            open_cost = lot_cost(inv["YES"]) + lot_cost(inv["NO"])
            imbalance_room = args.imbalance_qty_cap - max(0.0, same_qty - opp_qty)
            if imbalance_room <= args.dust_qty:
                metrics["seed_block_imbalance_qty"] += 1
                continue
            qty = min(
                args.max_seed_qty,
                trade_size * args.fill_haircut,
                effective_sizing.target_qty - same_qty,
                (effective_sizing.max_open_cost - open_cost) / max(seed_px, 1e-9),
                imbalance_room,
            )
            if qty <= args.dust_qty:
                continue
            fee = seed_fill_fee(qty, seed_px, args)

            action_id += 1
            candidate_id = stable_id(candidate_base_manifest, config_name, row["candidate_row_id"], action_id)
            inv[side].append(
                Lot(
                    qty=qty,
                    px=seed_px,
                    ts_ms=ts_ms,
                    side=side,
                    seed_action_id=action_id,
                    candidate_row_id=int(row["candidate_row_id"]),
                )
            )
            state.last_seed_ts_ms = ts_ms
            state.active = True
            metrics["seed_actions"] += 1
            metrics["gross_buy_qty"] += qty
            metrics["gross_buy_cost"] += qty * seed_px
            metrics["total_fee"] += fee
            dm = day_metrics[state.day]
            dm["candidate_count"] += 1
            dm["seed_actions"] += 1
            dm["gross_buy_qty"] += qty
            dm["gross_buy_cost"] += qty * seed_px
            dm["total_fee"] += fee
            pair_delta = pair_inventory(state, metrics, ts_ms, day_metrics)
            actions.append(
                {
                    "candidate_id": candidate_id,
                    "action_id": action_id,
                    "config_name": config_name,
                    "candidate_row_id": int(row["candidate_row_id"]),
                    "source_label": row["source_label"],
                    "day": row["day"],
                    "condition_id": condition_id,
                    "slug": row["slug"],
                    "ts_ms": ts_ms,
                    "ts_iso": iso_ms(ts_ms),
                    "offset_s": round(offset_s, 6),
                    "side": side,
                    "opposite_side": other(side),
                    "winner_side": row["winner_side"],
                    "side_alignment": alignment,
                    "candidate_reason": row["candidate_reason"],
                    "public_trade_price": round(trade_px, 6),
                    "public_trade_size": round(trade_size, 6),
                    "l1_pair_ask": round(l1_pair, 6),
                    "edge": args.edge,
                    "seed_px": round(seed_px, 6),
                    "seed_qty": round(qty, 6),
                    "seed_cost": round(qty * seed_px, 6),
                    "target_qty_effective": round(effective_sizing.target_qty, 6),
                    "max_open_cost_effective": round(effective_sizing.max_open_cost, 6),
                    "sizing_override_id": effective_sizing.override_id,
                    "sizing_override_key_type": effective_sizing.override_key_type,
                    "sizing_override_key": effective_sizing.override_key,
                    "fee_model": args.fee_model,
                    "official_taker_fee": round(fee, 6) if args.fee_model == "official_taker" else 0.0,
                    "fee": round(fee, 6),
                    "pair_qty_after_seed": round(pair_delta["paired_qty"], 6),
                    "pair_actions_after_seed": int(pair_delta["pair_actions"]),
                    "pair_cost_wavg_after_seed": round(pair_delta["pair_cost_wavg"], 6)
                    if math.isfinite(pair_delta["pair_cost_wavg"])
                    else None,
                    "inventory_yes_qty_after": round(lot_qty(inv["YES"]), 6),
                    "inventory_no_qty_after": round(lot_qty(inv["NO"]), 6),
                    "inventory_yes_cost_after": round(lot_cost(inv["YES"]), 6),
                    "inventory_no_cost_after": round(lot_cost(inv["NO"]), 6),
                    "blocked_by": "selected",
                    "decision_scope": "PASS_LOCAL_COMPLETION_RESEARCH_ONLY" if action_id else "UNKNOWN_NOT_DEPLOYABLE",
                    "deployable": False,
                }
            )

    for condition_id, state in states.items():
        settle_market(condition_id, state, metrics, day_metrics, residual_rows)

    daily_rows: list[dict[str, Any]] = []
    source_day_counts = dict(conn.execute("SELECT day, COUNT(*) FROM candidate_base GROUP BY 1 ORDER BY 1").fetchall())
    for day in sorted(set(source_day_counts) | set(day_metrics)):
        item = day_metrics[day]
        buy_qty = item["gross_buy_qty"]
        buy_cost = item["gross_buy_cost"]
        pair_qty = item["pair_qty"]
        actual_pnl = item["pair_pnl"] + item["residual_settle_pnl"]
        fee = item["total_fee"]
        worst_pnl = item["pair_pnl"] - item["residual_cost"]
        stress_shares = 2 * pair_qty + item["residual_qty"]
        daily_rows.append(
            {
                "day": day,
                "candidate_count": int(source_day_counts.get(day, 0)),
                "active_markets": int(item["active_markets"]),
                "seed_actions": int(item["seed_actions"]),
                "pair_actions": int(item["pair_actions"]),
                "gross_buy_qty": round(buy_qty, 6),
                "gross_buy_cost": round(buy_cost, 6),
                "pair_qty": round(pair_qty, 6),
                "pair_cost_wavg": round(item["pair_cost_sum"] / pair_qty, 6) if pair_qty else None,
                "pair_pnl": round(item["pair_pnl"], 6),
                "actual_settle_pnl": round(actual_pnl, 6),
                "official_taker_fee": round(fee, 6) if args.fee_model == "official_taker" else 0.0,
                "fee283": None,
                "fee_after_pnl": round(actual_pnl - fee, 6),
                "worst_residual_net_pnl": round(worst_pnl, 6),
                "stress100_worst_pnl": round(worst_pnl - 0.01 * stress_shares, 6),
                "residual_qty": round(item["residual_qty"], 6),
                "residual_cost": round(item["residual_cost"], 6),
                "qty_residual_rate": pct(item["residual_qty"], buy_qty),
                "cost_residual_rate": pct(item["residual_cost"], buy_cost),
            }
        )

    metrics["candidate_markets_seen"] = len(market_seen)
    final_metrics = finish_metrics(metrics, actions, args)
    final_metrics["worst_day_fee_after_pnl"] = (
        round(min(float(row["fee_after_pnl"]) for row in daily_rows), 6) if daily_rows else None
    )
    return actions, final_metrics, daily_rows, residual_rows


def schema_rows(conn: duckdb.DuckDBPyConnection, table: str) -> list[dict[str, Any]]:
    rows = conn.execute(f"DESCRIBE {quote_ident(table)}").fetchall()
    return [{"name": row[0], "type": row[1]} for row in rows]


def compact_day(day: str) -> str:
    return day.replace("-", "")


def discover_manifest_days(root: Path, manifest_name: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not root.is_dir():
        return out
    for manifest_path in sorted(root.glob(f"*/{manifest_name}")):
        label = manifest_path.parent.name
        if label in BLOCKED_LABELS:
            continue
        try:
            manifest = load_json(manifest_path)
        except json.JSONDecodeError:
            continue
        days = [str(day) for day in manifest.get("days", [])]
        if any(day in BLOCKED_DAYS or compact_day(day) in BLOCKED_LABELS for day in days):
            continue
        out.append({"label": label, "path": str(manifest_path), "days": days, "manifest": manifest})
    return out


def strict_cache_compliance(data_root: Path, days: list[str]) -> dict[str, Any]:
    root = data_root / "backtest_cache" / "taker_buy_signal_core_v2_strict_l1"
    manifests = discover_manifest_days(root, "CACHE_MANIFEST.json")
    day_to_labels: dict[str, list[str]] = defaultdict(list)
    validation_errors: dict[str, Any] = {}
    row_counts: dict[str, int] = {}
    for item in manifests:
        label = item["label"]
        row_counts[label] = int(item["manifest"].get("outputs", {}).get("row_count") or 0)
        validation_path = root / label / "CACHE_VALIDATION_V2.json"
        if validation_path.exists():
            validation = load_json(validation_path)
            validation_errors[label] = int(validation.get("error_count") or 0)
        else:
            validation_errors[label] = "missing_validation"
        for day in item["days"]:
            day_to_labels[day].append(label)
    missing = [day for day in days if day not in day_to_labels]
    bad_validations = {k: v for k, v in validation_errors.items() if v not in (0,)}
    return {
        "dataset_type": "taker_buy_signal_core_v2_strict_l1",
        "root": str(root),
        "covered_days": sorted(day for day in days if day in day_to_labels),
        "missing_days": missing,
        "day_to_labels": {day: sorted(day_to_labels.get(day, [])) for day in days},
        "row_counts_by_label": row_counts,
        "validation_error_count_by_label": validation_errors,
        "coverage_pass": not missing,
        "validation_pass": not bad_validations,
        "pass": not missing and not bad_validations,
    }


def public_audit_compliance(data_root: Path, days: list[str]) -> dict[str, Any]:
    root = data_root / "verification_store" / "public_account_execution_truth_v1"
    manifests = discover_manifest_days(root, "EVENT_STORE_MANIFEST.json")
    day_to_labels: dict[str, list[str]] = defaultdict(list)
    row_counts: dict[str, int] = {}
    event_kind_counts: dict[str, Any] = {}
    for item in manifests:
        label = item["label"]
        outputs = item["manifest"].get("outputs", {})
        row_counts[label] = int(outputs.get("row_count") or 0)
        event_kind_counts[label] = outputs.get("event_kind_counts", {})
        for day in item["days"]:
            day_to_labels[day].append(label)
    missing = [day for day in days if day not in day_to_labels]
    return {
        "dataset_type": "public_account_execution_truth_v1",
        "root": str(root),
        "covered_days": sorted(day for day in days if day in day_to_labels),
        "missing_days": missing,
        "day_to_labels": {day: sorted(day_to_labels.get(day, [])) for day in days},
        "row_counts_by_label": row_counts,
        "event_kind_counts_by_label": event_kind_counts,
        "coverage_pass": not missing,
        "is_private_truth": False,
        "pass": bool(manifests),
    }


def annotate_registry_compliance(
    out_dir: Path,
    data_root: Path,
    days: list[str],
    public_window_ms: int,
) -> dict[str, Any]:
    db_path = out_dir / "state_machine_results.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("ALTER TABLE candidate_registry ADD COLUMN strict_cache_day_covered BOOLEAN DEFAULT FALSE")
    con.execute("ALTER TABLE candidate_registry ADD COLUMN public_audit_day_covered BOOLEAN DEFAULT FALSE")
    con.execute("ALTER TABLE candidate_registry ADD COLUMN public_audit_nearby_fill_count BIGINT DEFAULT 0")
    strict = strict_cache_compliance(data_root, days)
    public = public_audit_compliance(data_root, days)
    strict_days = strict["covered_days"]
    public_days = public["covered_days"]
    con.execute(
        "UPDATE candidate_registry SET strict_cache_day_covered = CAST(day AS VARCHAR) IN (SELECT UNNEST(?))",
        [strict_days],
    )
    con.execute(
        "UPDATE candidate_registry SET public_audit_day_covered = CAST(day AS VARCHAR) IN (SELECT UNNEST(?))",
        [public_days],
    )
    public_root = data_root / "verification_store" / "public_account_execution_truth_v1"
    public_dbs: list[Path] = []
    for item in discover_manifest_days(public_root, "EVENT_STORE_MANIFEST.json"):
        db_name = item["manifest"].get("outputs", {}).get("duckdb", "event_store.duckdb")
        db = public_root / item["label"] / str(db_name)
        if db.exists():
            public_dbs.append(db)
    nearby_rows = 0
    if public_dbs:
        union_sql = []
        for idx, db in enumerate(public_dbs):
            alias = f"pa_{idx}"
            con.execute(f"ATTACH {quote_literal(db)} AS {quote_ident(alias)} (READ_ONLY)")
            union_sql.append(f"SELECT * FROM {quote_ident(alias)}.public_account_execution_events")
        con.execute(f"CREATE TEMP TABLE public_audit AS {' UNION ALL '.join(union_sql)}")
        con.execute(
            """
            CREATE TEMP TABLE public_nearby AS
            SELECT
              r.candidate_id,
              COUNT(*) AS nearby_fill_count
            FROM candidate_registry r
            JOIN public_audit p
              ON p.event_kind = 'fill'
             AND p.condition_id = r.condition_id
             AND CAST(p.day AS VARCHAR) = CAST(r.day AS VARCHAR)
             AND p.side = r.side
             AND ABS(CAST(p.event_ts_ms AS BIGINT) - CAST(r.ts_ms AS BIGINT)) <= ?
            GROUP BY 1
            """,
            [public_window_ms],
        )
        nearby_rows = int(con.execute("SELECT COUNT(*) FROM public_nearby").fetchone()[0])
        con.execute(
            """
            UPDATE candidate_registry
            SET public_audit_nearby_fill_count = COALESCE(
              (SELECT nearby_fill_count FROM public_nearby p WHERE p.candidate_id = candidate_registry.candidate_id),
              0
            )
            """
        )
    con.execute(f"COPY candidate_registry TO {quote_literal(out_dir / 'candidate_registry.csv')} (HEADER, DELIMITER ',')")
    con.execute(f"COPY candidate_registry TO {quote_literal(out_dir / 'candidate_registry.parquet')} (FORMAT PARQUET, COMPRESSION ZSTD)")
    registry_schema = schema_rows(con, "candidate_registry")
    registry_count = int(con.execute("SELECT COUNT(*) FROM candidate_registry").fetchone()[0])
    registry_counts = {
        "strict_cache_day_covered_rows": int(
            con.execute("SELECT COUNT(*) FROM candidate_registry WHERE strict_cache_day_covered").fetchone()[0]
        ),
        "public_audit_day_covered_rows": int(
            con.execute("SELECT COUNT(*) FROM candidate_registry WHERE public_audit_day_covered").fetchone()[0]
        ),
        "public_audit_nearby_fill_rows": int(
            con.execute("SELECT COUNT(*) FROM candidate_registry WHERE public_audit_nearby_fill_count > 0").fetchone()[0]
        ),
    }
    con.close()
    return {
        "strict_cache": strict,
        "public_account_audit": public,
        "public_audit_window_ms": public_window_ms,
        "public_audit_nearby_candidate_rows": nearby_rows,
        "candidate_registry_row_count": registry_count,
        "candidate_registry_counts": registry_counts,
        "candidate_registry_schema": registry_schema,
        "compliance_pass": strict["pass"] and public["pass"],
        "promotion_gate_pass": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-base-dir", "--candidate-base", dest="candidate_base_dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--mode", choices=["passive_redeem"], default="passive_redeem")
    parser.add_argument("--edge", type=float, default=0.055)
    parser.add_argument("--target-qty", type=float, default=5.0)
    parser.add_argument(
        "--sizing-overrides-csv",
        type=Path,
        default=None,
        help=(
            "Optional CSV keyed by condition_id, slug, or candidate_row_id with target_qty/max_open_cost overrides. "
            "Used for review-only replay of public-profile sizing schedules."
        ),
    )
    parser.add_argument("--alignment", choices=["all", "high", "low"], default="all")
    parser.add_argument("--seed-px-lo", type=float, default=0.05)
    parser.add_argument("--seed-px-hi", type=float, default=0.90)
    parser.add_argument("--fill-haircut", type=float, default=0.25)
    parser.add_argument("--max-seed-qty", type=float, default=60.0)
    parser.add_argument("--max-open-cost", type=float, default=250.0)
    parser.add_argument("--min-seed-px", type=float, default=0.01)
    parser.add_argument("--seed-offset-max-s", type=float, default=120.0)
    parser.add_argument("--seed-l1-pair-cap", type=float, default=1.02)
    parser.add_argument("--cooldown-s", type=float, default=5.0)
    parser.add_argument("--imbalance-qty-cap", type=float, default=1.25)
    parser.add_argument("--imbalance-cost-cap", type=float, default=1_000_000_000.0)
    parser.add_argument("--residual-cooldown-age-s", type=float, default=30.0)
    parser.add_argument("--residual-cooldown-cost-cap", type=float, default=0.5)
    parser.add_argument("--fee-model", choices=["none", "official_taker", "flat_notional"], default="none")
    parser.add_argument("--official-fee-rate", type=float, default=None)
    parser.add_argument("--flat-notional-fee-rate", type=float, default=0.0)
    parser.add_argument("--dust-qty", type=float, default=1.0)
    parser.add_argument("--offset-min-s", type=float, default=0.0)
    parser.add_argument("--offset-max-s", type=float, default=300.0)
    parser.add_argument("--public-trade-taker-side", choices=["SELL", "BUY", "ANY"], default="SELL")
    parser.add_argument("--public-audit-window-ms", type=int, default=1000)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.fee_model == "official_taker":
        if args.official_fee_rate is None:
            raise SystemExit("--official-fee-rate is required when --fee-model=official_taker")
        if args.official_fee_rate < 0:
            raise SystemExit("--official-fee-rate must be non-negative")
    else:
        args.official_fee_rate = 0.0
    if args.flat_notional_fee_rate < 0:
        raise SystemExit("--flat-notional-fee-rate must be non-negative")
    if args.target_qty <= 0:
        raise SystemExit("--target-qty must be positive")
    if args.max_open_cost <= 0:
        raise SystemExit("--max-open-cost must be positive")
    args.sizing_overrides = load_sizing_overrides_csv(args.sizing_overrides_csv)
    args.sizing_overrides_key_count = len(args.sizing_overrides)
    if args.sizing_overrides_csv is not None:
        args.sizing_overrides_csv = args.sizing_overrides_csv.expanduser().resolve()
        args.sizing_overrides_sha256 = sha256_file(args.sizing_overrides_csv)
    else:
        args.sizing_overrides_sha256 = None

    started = time.perf_counter()
    base_dir = args.candidate_base_dir.expanduser().resolve()
    manifest = load_candidate_manifest(base_dir)
    data_root = Path(str(manifest["data_root"])).expanduser().resolve()
    db_path = base_dir / str(manifest.get("outputs", {}).get("duckdb", "candidate_base.duckdb"))
    if not db_path.is_file():
        raise FileNotFoundError(f"missing candidate_base duckdb: {db_path}")
    out_dir = output_dir_for(args, manifest)
    if out_dir.exists():
        if not args.force:
            raise FileExistsError(f"output exists; pass --force to replace: {out_dir}")
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    conn = duckdb.connect(str(db_path), read_only=True)
    actions, metrics, daily_rows, residual_rows = run_passive_redeem(args, conn, manifest)
    conn.close()

    action_fields = [
        "candidate_id",
        "action_id",
        "config_name",
        "candidate_row_id",
        "source_label",
        "day",
        "condition_id",
        "slug",
        "ts_ms",
        "ts_iso",
        "offset_s",
        "side",
        "opposite_side",
        "winner_side",
        "side_alignment",
        "candidate_reason",
        "public_trade_price",
        "public_trade_size",
        "l1_pair_ask",
        "edge",
        "seed_px",
        "seed_qty",
        "seed_cost",
        "target_qty_effective",
        "max_open_cost_effective",
        "sizing_override_id",
        "sizing_override_key_type",
        "sizing_override_key",
        "fee_model",
        "official_taker_fee",
        "fee",
        "pair_qty_after_seed",
        "pair_actions_after_seed",
        "pair_cost_wavg_after_seed",
        "inventory_yes_qty_after",
        "inventory_no_qty_after",
        "inventory_yes_cost_after",
        "inventory_no_cost_after",
        "blocked_by",
        "decision_scope",
        "deployable",
    ]
    daily_fields = [
        "day",
        "candidate_count",
        "active_markets",
        "seed_actions",
        "pair_actions",
        "gross_buy_qty",
        "gross_buy_cost",
        "pair_qty",
        "pair_cost_wavg",
        "pair_pnl",
        "actual_settle_pnl",
        "official_taker_fee",
        "fee283",
        "fee_after_pnl",
        "worst_residual_net_pnl",
        "stress100_worst_pnl",
        "residual_qty",
        "residual_cost",
        "qty_residual_rate",
        "cost_residual_rate",
    ]
    residual_fields = [
        "condition_id",
        "day",
        "slug",
        "winner_side",
        "side",
        "qty",
        "px",
        "cost",
        "payout",
        "pnl",
        "source_seed_action_id",
        "candidate_row_id",
        "age_s",
    ]
    actions_csv = out_dir / "actions.csv"
    summary_csv = out_dir / "summary_by_day.csv"
    registry_csv = out_dir / "candidate_registry.csv"
    residual_csv = out_dir / "residual_lots.csv"
    write_csv(actions_csv, actions, action_fields)
    write_csv(summary_csv, daily_rows, daily_fields)
    write_csv(registry_csv, actions, action_fields)
    write_csv(residual_csv, residual_rows, residual_fields)

    output_db = out_dir / "state_machine_results.duckdb"
    conn = duckdb.connect(str(output_db))
    create_table_from_csv(conn, "actions", actions_csv, actions, action_fields)
    create_table_from_csv(conn, "summary_by_day", summary_csv, daily_rows, daily_fields)
    create_table_from_csv(conn, "candidate_registry", registry_csv, actions, action_fields)
    create_table_from_csv(conn, "residual_lots", residual_csv, residual_rows, residual_fields)
    conn.execute(f"COPY actions TO {quote_literal(out_dir / 'actions.parquet')} (FORMAT PARQUET, COMPRESSION ZSTD)")
    conn.execute(f"COPY summary_by_day TO {quote_literal(out_dir / 'summary_by_day.parquet')} (FORMAT PARQUET, COMPRESSION ZSTD)")
    conn.execute(f"COPY candidate_registry TO {quote_literal(out_dir / 'candidate_registry.parquet')} (FORMAT PARQUET, COMPRESSION ZSTD)")
    conn.execute(f"COPY residual_lots TO {quote_literal(out_dir / 'residual_lots.parquet')} (FORMAT PARQUET, COMPRESSION ZSTD)")
    initial_registry_schema = schema_rows(conn, "candidate_registry")
    conn.close()

    days = [str(day) for day in manifest["days"]]
    compliance = annotate_registry_compliance(out_dir, data_root, days, int(args.public_audit_window_ms))
    elapsed = time.perf_counter() - started
    common_scope = {
        "data_root": manifest["data_root"],
        "source_dataset_type": manifest["dataset_type"],
        "dataset_type": "completion_unwind_event_store_v2_candidate_base",
        "candidate_base_manifest": str(base_dir / "CANDIDATE_BASE_MANIFEST.json"),
        "labels": manifest["labels"],
        "days": manifest["days"],
        "market_prefix": manifest["market_prefix"],
        "assets": manifest["assets"],
        "excluded_labels_or_days": manifest["excluded_labels_or_days"],
        "public_account_execution_truth_v1_included": True,
        "public_account_execution_truth_v1_private_truth": False,
        "raw_scanned": False,
        "replay_scanned": False,
        "collector_scanned": False,
    }
    config = {
        "mode": args.mode,
        "edge": args.edge,
        "target_qty": args.target_qty,
        "sizing_overrides_csv": str(args.sizing_overrides_csv) if args.sizing_overrides_csv else None,
        "sizing_overrides_sha256": args.sizing_overrides_sha256,
        "sizing_overrides_key_count": args.sizing_overrides_key_count,
        "sizing_override_key_priority": ["candidate_row_id", "condition_id", "slug"],
        "alignment": args.alignment,
        "seed_px_lo": args.seed_px_lo,
        "seed_px_hi": args.seed_px_hi,
        "fill_haircut": args.fill_haircut,
        "max_seed_qty": args.max_seed_qty,
        "max_open_cost": args.max_open_cost,
        "seed_offset_max_s": args.seed_offset_max_s,
        "seed_l1_pair_cap": args.seed_l1_pair_cap,
        "cooldown_s": args.cooldown_s,
        "imbalance_qty_cap": args.imbalance_qty_cap,
        "imbalance_cost_cap": args.imbalance_cost_cap,
        "residual_cooldown_age_s": args.residual_cooldown_age_s,
        "residual_cooldown_cost_cap": args.residual_cooldown_cost_cap,
        "fee_model": args.fee_model,
        "official_fee_formula": OFFICIAL_CLOB_FEE_FORMULA if args.fee_model == "official_taker" else None,
        "official_fee_source": OFFICIAL_CLOB_FEE_SOURCE if args.fee_model == "official_taker" else None,
        "official_fee_rate": args.official_fee_rate if args.fee_model == "official_taker" else None,
        "flat_notional_fee_rate": args.flat_notional_fee_rate if args.fee_model == "flat_notional" else None,
        "offset_min_s": args.offset_min_s,
        "offset_max_s": args.offset_max_s,
        "public_trade_taker_side": args.public_trade_taker_side,
    }
    outputs = {
        "duckdb": "state_machine_results.duckdb",
        "actions_table": "actions",
        "summary_by_day_table": "summary_by_day",
        "candidate_registry_table": "candidate_registry",
        "residual_lots_table": "residual_lots",
        "actions_csv": "actions.csv",
        "summary_by_day_csv": "summary_by_day.csv",
        "candidate_registry_csv": "candidate_registry.csv",
        "residual_lots_csv": "residual_lots.csv",
        "actions_parquet": "actions.parquet",
        "summary_by_day_parquet": "summary_by_day.parquet",
        "candidate_registry_parquet": "candidate_registry.parquet",
        "residual_lots_parquet": "residual_lots.parquet",
    }
    result_manifest = {
        "created_at": utc_now(),
        "schema_version": "result_summary_v2",
        "schema_contract": "explicit_duckdb_schema_v1",
        "dataset_type": SUMMARY_DATASET_TYPE,
        **common_scope,
        "status": metrics["status"],
        "result_classification": metrics["result_classification"],
        "can_support_strategy_promotion": False,
        "row_count": len(actions),
        "summary_by_day_row_count": len(daily_rows),
        "config": config,
        "core_metrics": metrics,
        "compliance_summary": {
            "strict_cache_pass": compliance["strict_cache"]["pass"],
            "public_account_audit_present": compliance["public_account_audit"]["pass"],
            "public_account_audit_coverage_pass": compliance["public_account_audit"]["coverage_pass"],
            "public_account_audit_missing_days": compliance["public_account_audit"]["missing_days"],
            "promotion_gate_pass": False,
        },
        "outputs": outputs,
        "elapsed_s": round(elapsed, 3),
    }
    registry_manifest = {
        "created_at": utc_now(),
        "schema_version": "candidate_registry_v2",
        "schema_contract": "explicit_duckdb_schema_v1",
        "dataset_type": REGISTRY_DATASET_TYPE,
        **common_scope,
        "row_count": len(actions),
        "candidate_registry_semantics": "one row per selected seed candidate/action with deterministic candidate_id and post-action inventory state",
        "initial_schema": initial_registry_schema,
        "schema": compliance["candidate_registry_schema"],
        "outputs": {
            "duckdb": "state_machine_results.duckdb",
            "duckdb_table": "candidate_registry",
            "csv": "candidate_registry.csv",
            "parquet": "candidate_registry.parquet",
        },
        "row_counts": compliance["candidate_registry_counts"],
        "elapsed_s": round(elapsed, 3),
    }
    compliance_manifest = {
        "created_at": utc_now(),
        "schema_version": "compliance_v1",
        "dataset_type": COMPLIANCE_DATASET_TYPE,
        **common_scope,
        "strict_cache": compliance["strict_cache"],
        "public_account_audit": compliance["public_account_audit"],
        "public_audit_window_ms": compliance["public_audit_window_ms"],
        "candidate_registry_counts": compliance["candidate_registry_counts"],
        "compliance_pass": compliance["compliance_pass"],
        "promotion_gate_pass": False,
        "scope_statement": (
            "PASS_LOCAL_COMPLETION_RESEARCH_ONLY can support local descriptive research only. "
            "It is not deployable, not public-account-truth validated as private truth, and not source-of-truth replay verified."
        ),
        "elapsed_s": round(elapsed, 3),
    }
    write_json(out_dir / "RESULT_SUMMARY_MANIFEST.json", result_manifest)
    write_json(out_dir / "CANDIDATE_REGISTRY_MANIFEST.json", registry_manifest)
    write_json(out_dir / "COMPLIANCE_MANIFEST.json", compliance_manifest)
    print(
        json.dumps(
            {
                "output_dir": str(out_dir),
                "result_summary_manifest": str(out_dir / "RESULT_SUMMARY_MANIFEST.json"),
                "candidate_registry_manifest": str(out_dir / "CANDIDATE_REGISTRY_MANIFEST.json"),
                "compliance_manifest": str(out_dir / "COMPLIANCE_MANIFEST.json"),
                "row_count": len(actions),
                "status": metrics["status"],
                "core_metrics": metrics,
                "compliance_summary": result_manifest["compliance_summary"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
