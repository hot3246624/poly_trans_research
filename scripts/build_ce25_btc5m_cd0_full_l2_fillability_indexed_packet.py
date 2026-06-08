#!/usr/bin/env python3
"""Build a full indexed L2 fillability packet for CE25 BTC5M cd0."""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
BT_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
EXPORTS = ROOT / "data" / "exports"
OUT = EXPORTS / "ce25_btc5m_cd0_full_l2_fillability_indexed_packet_20260607"
JOIN_DIR = (
    BT_ROOT
    / "derived/completion_candidate_pipeline_v1/ce25_btc5m_cd0_full_l2_fillability_indexed_join_20260607"
)
JOIN_DB = JOIN_DIR / "cd0_l2_fillability_join.duckdb"
FULL_DIR = (
    BT_ROOT
    / "derived/completion_candidate_pipeline_v1/ce25_btc5m_local_cd0_watch_full_artifact_bulkcopy_20260607"
    / "broad_qty5_pc102_seed300_cd0_imb250_rage30_rcost050_full_5m"
)
ACTION_DB = FULL_DIR / "state_machine_results.duckdb"
L2_MART = BT_ROOT / "derived/contract_examples/l2_top_aligned_mart_20260502_20260518_l2/l2_top_aligned_mart.duckdb"
L2_MANIFEST = BT_ROOT / "derived/contract_examples/l2_top_aligned_mart_20260502_20260518_l2/L2_TOP_ALIGNED_MART_MANIFEST.json"
L2_PROBE_PACKET = (
    EXPORTS
    / "ce25_btc5m_cd0_l2_fillability_probe_packet_20260607"
    / "CE25_BTC5M_CD0_L2_FILLABILITY_PROBE_PACKET.json"
)
BUILDER = ROOT / "scripts/build_ce25_btc5m_cd0_full_l2_fillability_indexed_packet.py"
VALIDATOR = ROOT / "scripts/validate_ce25_btc5m_research_packet_chain.py"

STATUS = (
    "KEEP_CE25_BTC5M_CD0_FULL_L2_FILLABILITY_INDEXED_JOIN_REVIEWED_PRICE_FILLABILITY_BLOCKED_"
    "NOT_OOS_READY"
)


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


def binding(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if path.exists():
        out.update({"sha256": sha256_file(path), "size": path.stat().st_size})
    return out


def quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


JOIN_SELECT = """
with joined as (
  select
    a.action_id,
    a.day,
    a.condition_id,
    a.slug,
    a.side,
    a.ts_ms,
    a.seed_px,
    a.seed_qty,
    a.seed_cost,
    m.source_ts_ms,
    m.raw_l2_age_ms,
    m.ask1_px,
    m.ask1_sz,
    m.raw_l2_ask1_px,
    m.raw_l2_ask1_sz,
    m.raw_l2_ask2_px,
    m.raw_l2_ask2_sz,
    m.raw_l2_ask3_px,
    m.raw_l2_ask3_sz,
    m.raw_l2_ask4_px,
    m.raw_l2_ask4_sz,
    m.raw_l2_ask5_px,
    m.raw_l2_ask5_sz,
    a.ts_ms - m.source_ts_ms as align_lag_ms
  from actions_day a
  asof left join l2_day m
    on a.condition_id=m.condition_id
   and a.side=m.market_side
   and a.ts_ms >= m.source_ts_ms
), calc as (
  select
    *,
    coalesce(raw_l2_ask1_sz,0) as ask1_cum_sz,
    coalesce(raw_l2_ask1_sz,0)+coalesce(raw_l2_ask2_sz,0)+coalesce(raw_l2_ask3_sz,0) as top3_cum_sz,
    coalesce(raw_l2_ask1_sz,0)+coalesce(raw_l2_ask2_sz,0)+coalesce(raw_l2_ask3_sz,0)+coalesce(raw_l2_ask4_sz,0)+coalesce(raw_l2_ask5_sz,0) as top5_cum_sz,
    (
      least(seed_qty, coalesce(raw_l2_ask1_sz,0))*coalesce(raw_l2_ask1_px,0) +
      least(greatest(seed_qty-coalesce(raw_l2_ask1_sz,0),0), coalesce(raw_l2_ask2_sz,0))*coalesce(raw_l2_ask2_px,0) +
      least(greatest(seed_qty-coalesce(raw_l2_ask1_sz,0)-coalesce(raw_l2_ask2_sz,0),0), coalesce(raw_l2_ask3_sz,0))*coalesce(raw_l2_ask3_px,0) +
      least(greatest(seed_qty-coalesce(raw_l2_ask1_sz,0)-coalesce(raw_l2_ask2_sz,0)-coalesce(raw_l2_ask3_sz,0),0), coalesce(raw_l2_ask4_sz,0))*coalesce(raw_l2_ask4_px,0) +
      least(greatest(seed_qty-coalesce(raw_l2_ask1_sz,0)-coalesce(raw_l2_ask2_sz,0)-coalesce(raw_l2_ask3_sz,0)-coalesce(raw_l2_ask4_sz,0),0), coalesce(raw_l2_ask5_sz,0))*coalesce(raw_l2_ask5_px,0)
    ) / nullif(seed_qty,0) as top5_vwap
  from joined
)
select
  action_id,
  day,
  condition_id,
  slug,
  side,
  ts_ms,
  seed_px,
  seed_qty,
  seed_cost,
  source_ts_ms,
  align_lag_ms,
  raw_l2_age_ms,
  raw_l2_ask1_px,
  raw_l2_ask1_sz,
  top3_cum_sz,
  top5_cum_sz,
  top5_vwap,
  raw_l2_ask1_px - seed_px as ask1_slippage,
  top5_vwap - seed_px as top5_vwap_slippage,
  source_ts_ms is not null as joined,
  raw_l2_ask1_sz >= seed_qty as ask1_size_ge_seed,
  top3_cum_sz >= seed_qty as top3_size_ge_seed,
  top5_cum_sz >= seed_qty as top5_size_ge_seed,
  raw_l2_ask1_sz >= seed_qty and raw_l2_ask1_px <= seed_px as ask1_full_at_or_better,
  top5_cum_sz >= seed_qty and top5_vwap <= seed_px as top5_full_at_or_better,
  top5_cum_sz >= seed_qty and top5_vwap <= seed_px + 0.01 as top5_full_within_1c,
  top5_cum_sz >= seed_qty and top5_vwap <= seed_px + 0.02 as top5_full_within_2c,
  top5_cum_sz >= seed_qty and top5_vwap <= seed_px + 0.05 as top5_full_within_5c,
  top5_cum_sz >= seed_qty and top5_vwap <= seed_px + 0.10 as top5_full_within_10c
from calc
"""


def write_preview(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo 'NOT_AUTHORIZED: CE25 BTC5M full L2 fillability indexed packet is review-only' >&2\n"
        "exit 66\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def fetch_one(con: duckdb.DuckDBPyConnection, sql: str) -> dict[str, Any]:
    cur = con.execute(sql)
    row = cur.fetchone()
    names = [item[0] for item in cur.description]
    return {name: (round(value, 6) if isinstance(value, float) else value) for name, value in zip(names, row)}


def fetch_all_dicts(con: duckdb.DuckDBPyConnection, sql: str) -> list[dict[str, Any]]:
    cur = con.execute(sql)
    names = [item[0] for item in cur.description]
    return [
        {name: (round(value, 6) if isinstance(value, float) else value) for name, value in zip(names, row)}
        for row in cur.fetchall()
    ]


def build_join() -> dict[str, Any]:
    if JOIN_DIR.exists():
        if not JOIN_DB.exists():
            raise FileExistsError(f"join output exists without expected DuckDB: {JOIN_DIR}")
        return summarize_existing_join(reused=True, elapsed_s=0.0)
    JOIN_DIR.mkdir(parents=True)
    con = duckdb.connect(str(JOIN_DB))
    con.execute(f"attach {quote(ACTION_DB)} as actiondb (read_only)")
    con.execute(f"attach {quote(L2_MART)} as l2db (read_only)")
    days = [row[0] for row in con.execute("select distinct day from actiondb.actions order by day").fetchall()]
    started = time.perf_counter()
    day_rows: list[dict[str, Any]] = []
    table_created = False
    for day in days:
        day_started = time.perf_counter()
        con.execute("drop table if exists actions_day")
        con.execute("drop table if exists l2_day")
        con.execute(f"create temp table actions_day as select * from actiondb.actions where day={quote(day)}")
        con.execute(
            f"""
            create temp table l2_day as
            select condition_id, market_side, source_ts_ms, ask1_px, ask1_sz,
                   raw_l2_ask1_px, raw_l2_ask1_sz, raw_l2_ask2_px, raw_l2_ask2_sz,
                   raw_l2_ask3_px, raw_l2_ask3_sz, raw_l2_ask4_px, raw_l2_ask4_sz,
                   raw_l2_ask5_px, raw_l2_ask5_sz, raw_l2_age_ms
            from l2db.md_book_l2_top_aligned
            where asset='BTC'
              and day={quote(day)}
              and condition_id in (select distinct condition_id from actions_day)
            """
        )
        if not table_created:
            con.execute(f"create table joined_fillability as {JOIN_SELECT}")
            table_created = True
        else:
            con.execute(f"insert into joined_fillability {JOIN_SELECT}")
        day_summary = fetch_one(
            con,
            f"""
            select
              {quote(day)} as day,
              count(*) as action_rows,
              sum(joined::int) as joined_rows,
              sum(ask1_size_ge_seed::int) as ask1_size_ge_seed,
              sum(top5_size_ge_seed::int) as top5_size_ge_seed,
              sum(ask1_full_at_or_better::int) as ask1_full_at_or_better,
              sum(top5_full_at_or_better::int) as top5_full_at_or_better,
              sum(top5_full_within_1c::int) as top5_full_within_1c,
              sum(top5_full_within_2c::int) as top5_full_within_2c,
              sum(top5_full_within_5c::int) as top5_full_within_5c,
              sum(top5_full_within_10c::int) as top5_full_within_10c,
              quantile_cont(align_lag_ms,0.99) as align_lag_ms_p99,
              max(align_lag_ms) as align_lag_ms_max,
              quantile_cont(raw_l2_age_ms,0.99) as raw_l2_age_ms_p99,
              max(raw_l2_age_ms) as raw_l2_age_ms_max,
              quantile_cont(top5_vwap_slippage,0.5) as top5_vwap_slippage_p50,
              quantile_cont(top5_vwap_slippage,0.99) as top5_vwap_slippage_p99,
              max(top5_vwap_slippage) as top5_vwap_slippage_max
            from joined_fillability
            where day={quote(day)}
            """,
        )
        day_summary["elapsed_s"] = round(time.perf_counter() - day_started, 3)
        day_summary["l2_day_rows"] = con.execute("select count(*) from l2_day").fetchone()[0]
        day_rows.append(day_summary)
    elapsed = time.perf_counter() - started
    per_day_csv = JOIN_DIR / "per_day_fillability_summary.csv"
    with per_day_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(day_rows[0].keys()))
        writer.writeheader()
        writer.writerows(day_rows)
    con.execute(f"copy joined_fillability to {quote(JOIN_DIR / 'joined_fillability.parquet')} (format parquet, compression zstd)")
    aggregate = fetch_one(
        con,
        """
        select
          count(*) as action_rows,
          sum(joined::int) as joined_rows,
          sum(ask1_size_ge_seed::int) as ask1_size_ge_seed,
          sum(top3_size_ge_seed::int) as top3_size_ge_seed,
          sum(top5_size_ge_seed::int) as top5_size_ge_seed,
          sum(ask1_full_at_or_better::int) as ask1_full_at_or_better,
          sum(top5_full_at_or_better::int) as top5_full_at_or_better,
          sum(top5_full_within_1c::int) as top5_full_within_1c,
          sum(top5_full_within_2c::int) as top5_full_within_2c,
          sum(top5_full_within_5c::int) as top5_full_within_5c,
          sum(top5_full_within_10c::int) as top5_full_within_10c,
          quantile_cont(align_lag_ms,0.5) as align_lag_ms_p50,
          quantile_cont(align_lag_ms,0.99) as align_lag_ms_p99,
          max(align_lag_ms) as align_lag_ms_max,
          quantile_cont(raw_l2_age_ms,0.5) as raw_l2_age_ms_p50,
          quantile_cont(raw_l2_age_ms,0.99) as raw_l2_age_ms_p99,
          max(raw_l2_age_ms) as raw_l2_age_ms_max,
          quantile_cont(ask1_slippage,0.5) as ask1_slippage_p50,
          quantile_cont(ask1_slippage,0.99) as ask1_slippage_p99,
          max(ask1_slippage) as ask1_slippage_max,
          quantile_cont(top5_vwap_slippage,0.5) as top5_vwap_slippage_p50,
          quantile_cont(top5_vwap_slippage,0.9) as top5_vwap_slippage_p90,
          quantile_cont(top5_vwap_slippage,0.99) as top5_vwap_slippage_p99,
          max(top5_vwap_slippage) as top5_vwap_slippage_max
        from joined_fillability
        """,
    )
    con.close()
    summary = summarize_existing_join(reused=False, elapsed_s=elapsed)
    summary["per_day"] = day_rows
    return summary


def summarize_existing_join(*, reused: bool, elapsed_s: float) -> dict[str, Any]:
    con = duckdb.connect(str(JOIN_DB), read_only=True)
    aggregate = fetch_one(
        con,
        """
        select
          count(*) as action_rows,
          sum(joined::int) as joined_rows,
          sum(ask1_size_ge_seed::int) as ask1_size_ge_seed,
          sum(top3_size_ge_seed::int) as top3_size_ge_seed,
          sum(top5_size_ge_seed::int) as top5_size_ge_seed,
          sum(ask1_full_at_or_better::int) as ask1_full_at_or_better,
          sum(top5_full_at_or_better::int) as top5_full_at_or_better,
          sum(top5_full_within_1c::int) as top5_full_within_1c,
          sum(top5_full_within_2c::int) as top5_full_within_2c,
          sum(top5_full_within_5c::int) as top5_full_within_5c,
          sum(top5_full_within_10c::int) as top5_full_within_10c,
          quantile_cont(align_lag_ms,0.5) as align_lag_ms_p50,
          quantile_cont(align_lag_ms,0.99) as align_lag_ms_p99,
          max(align_lag_ms) as align_lag_ms_max,
          quantile_cont(raw_l2_age_ms,0.5) as raw_l2_age_ms_p50,
          quantile_cont(raw_l2_age_ms,0.99) as raw_l2_age_ms_p99,
          max(raw_l2_age_ms) as raw_l2_age_ms_max,
          quantile_cont(ask1_slippage,0.5) as ask1_slippage_p50,
          quantile_cont(ask1_slippage,0.99) as ask1_slippage_p99,
          max(ask1_slippage) as ask1_slippage_max,
          quantile_cont(top5_vwap_slippage,0.5) as top5_vwap_slippage_p50,
          quantile_cont(top5_vwap_slippage,0.9) as top5_vwap_slippage_p90,
          quantile_cont(top5_vwap_slippage,0.99) as top5_vwap_slippage_p99,
          max(top5_vwap_slippage) as top5_vwap_slippage_max
        from joined_fillability
        """,
    )
    per_day = fetch_all_dicts(
        con,
        """
        select
          day,
          count(*) as action_rows,
          sum(joined::int) as joined_rows,
          sum(ask1_size_ge_seed::int) as ask1_size_ge_seed,
          sum(top5_size_ge_seed::int) as top5_size_ge_seed,
          sum(ask1_full_at_or_better::int) as ask1_full_at_or_better,
          sum(top5_full_at_or_better::int) as top5_full_at_or_better,
          sum(top5_full_within_1c::int) as top5_full_within_1c,
          sum(top5_full_within_2c::int) as top5_full_within_2c,
          sum(top5_full_within_5c::int) as top5_full_within_5c,
          sum(top5_full_within_10c::int) as top5_full_within_10c,
          quantile_cont(align_lag_ms,0.99) as align_lag_ms_p99,
          max(align_lag_ms) as align_lag_ms_max,
          quantile_cont(raw_l2_age_ms,0.99) as raw_l2_age_ms_p99,
          max(raw_l2_age_ms) as raw_l2_age_ms_max,
          quantile_cont(top5_vwap_slippage,0.5) as top5_vwap_slippage_p50,
          quantile_cont(top5_vwap_slippage,0.99) as top5_vwap_slippage_p99,
          max(top5_vwap_slippage) as top5_vwap_slippage_max
        from joined_fillability
        group by day
        order by day
        """,
    )
    worst_days = fetch_all_dicts(
        con,
        """
        with day_summary as (
          select day, count(*) as action_rows,
                 sum(top5_size_ge_seed::int) as top5_size_ge_seed,
                 sum(top5_full_within_10c::int) as top5_full_within_10c,
                 sum(top5_full_at_or_better::int) as top5_full_at_or_better
          from joined_fillability
          group by day
        )
        select day, action_rows, top5_size_ge_seed, top5_full_within_10c, top5_full_at_or_better,
               round(top5_full_within_10c / nullif(action_rows,0), 6) as within_10c_rate
        from day_summary
        order by within_10c_rate asc, day
        limit 5
        """,
    )
    days = [row["day"] for row in per_day]
    con.close()
    return {
        "join_db": str(JOIN_DB),
        "joined_parquet": str(JOIN_DIR / "joined_fillability.parquet"),
        "per_day_csv": str(JOIN_DIR / "per_day_fillability_summary.csv"),
        "day_count": len(days),
        "days": days,
        "elapsed_s": round(elapsed_s, 3),
        "reused_existing_join": reused,
        "aggregate": aggregate,
        "per_day": per_day,
        "worst_days_by_top5_within_10c": worst_days,
    }


def rate(n: Any, d: Any) -> float:
    return round(float(n or 0) / float(d or 1), 6)


def render_report(packet: dict[str, Any]) -> str:
    agg = packet["full_indexed_join"]["aggregate"]
    rows = agg["action_rows"]
    return "\n".join(
        [
            "# CE25 BTC5M cd0 Full L2 Fillability Indexed Join Packet",
            "",
            f"Status: `{packet['status']}`",
            "",
            "## Full Join",
            "",
            f"- action rows / joined rows: `{rows}` / `{agg['joined_rows']}`",
            f"- ask1 size >= seed qty: `{agg['ask1_size_ge_seed']}` ({rate(agg['ask1_size_ge_seed'], rows):.2%})",
            f"- top5 size >= seed qty: `{agg['top5_size_ge_seed']}` ({rate(agg['top5_size_ge_seed'], rows):.2%})",
            f"- top5 full at replay seed price or better: `{agg['top5_full_at_or_better']}` ({rate(agg['top5_full_at_or_better'], rows):.2%})",
            f"- top5 full within seed+10c: `{agg['top5_full_within_10c']}` ({rate(agg['top5_full_within_10c'], rows):.2%})",
            f"- top5 VWAP slippage p50/p90/p99/max: `{agg['top5_vwap_slippage_p50']}` / `{agg['top5_vwap_slippage_p90']}` / `{agg['top5_vwap_slippage_p99']}` / `{agg['top5_vwap_slippage_max']}`",
            f"- align lag p50/p99/max ms: `{agg['align_lag_ms_p50']}` / `{agg['align_lag_ms_p99']}` / `{agg['align_lag_ms_max']}`",
            f"- raw L2 age p50/p99/max ms: `{agg['raw_l2_age_ms_p50']}` / `{agg['raw_l2_age_ms_p99']}` / `{agg['raw_l2_age_ms_max']}`",
            "",
            "## Decision",
            "",
            "The full indexed join passes depth-size coverage, but fails price fillability at the replay seed price. cd0 must not proceed to OOS without a price/fill model revision.",
            "",
            "## Non-Claims",
            "",
            "- oos_authorized=false",
            "- runner_authorized=false",
            "- private_truth_ready=false",
            "- strategy_promotion_ready=false",
            "- live_ready=false",
            "- deployable=false",
        ]
    ) + "\n"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    join = build_join()
    agg = join["aggregate"]
    rows = int(agg["action_rows"])
    top5_size_rate = rate(agg["top5_size_ge_seed"], rows)
    top5_at_or_better_rate = rate(agg["top5_full_at_or_better"], rows)
    top5_within_10c_rate = rate(agg["top5_full_within_10c"], rows)
    packet = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS,
        "scope": "full indexed local L2 fillability review over all cd0 actions; no OOS/live authority",
        "source_bindings": {
            "l2_probe_packet": binding(L2_PROBE_PACKET),
            "action_duckdb": binding(ACTION_DB),
            "l2_top_aligned_mart": binding(L2_MART),
            "l2_top_aligned_manifest": binding(L2_MANIFEST),
            "builder": binding(BUILDER),
            "validator": binding(VALIDATOR),
        },
        "full_indexed_join": {
            **join,
            "join_db_binding": binding(JOIN_DB),
            "joined_parquet_binding": binding(Path(join["joined_parquet"])),
            "per_day_csv_binding": binding(Path(join["per_day_csv"])),
        },
        "fillability_rates": {
            "joined_rate": rate(agg["joined_rows"], rows),
            "ask1_size_ge_seed_rate": rate(agg["ask1_size_ge_seed"], rows),
            "top5_size_ge_seed_rate": top5_size_rate,
            "top5_full_at_or_better_rate": top5_at_or_better_rate,
            "top5_full_within_1c_rate": rate(agg["top5_full_within_1c"], rows),
            "top5_full_within_2c_rate": rate(agg["top5_full_within_2c"], rows),
            "top5_full_within_5c_rate": rate(agg["top5_full_within_5c"], rows),
            "top5_full_within_10c_rate": top5_within_10c_rate,
        },
        "decision": {
            "full_indexed_join_complete": int(agg["joined_rows"]) == rows,
            "depth_size_coverage_pass": top5_size_rate >= 0.999,
            "price_fillability_at_seed_price_pass": top5_at_or_better_rate >= 0.95,
            "price_fillability_within_10c_watch": top5_within_10c_rate >= 0.95,
            "primary_blocker": "price_fillability_at_replay_seed_price",
            "next_packet": "cd0_price_fill_model_revision_or_seed_price_alignment_packet",
            "oos_discussion_allowed": False,
        },
        "outputs": {
            "packet": "CE25_BTC5M_CD0_FULL_L2_FILLABILITY_INDEXED_PACKET.json",
            "report": "CE25_BTC5M_CD0_FULL_L2_FILLABILITY_INDEXED_REPORT.md",
            "command_preview": "COMMAND_PREVIEW_NOT_AUTHORIZED.sh",
            "sha256sums": "SHA256SUMS.txt",
        },
        "non_claims": {
            "oos_authorized": False,
            "runner_authorized": False,
            "orders_authorized": False,
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "deployable": False,
        },
        "highest_allowed_status": STATUS,
    }
    packet_path = OUT / "CE25_BTC5M_CD0_FULL_L2_FILLABILITY_INDEXED_PACKET.json"
    report_path = OUT / "CE25_BTC5M_CD0_FULL_L2_FILLABILITY_INDEXED_REPORT.md"
    preview_path = OUT / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    write_json(packet_path, packet)
    report_path.write_text(render_report(packet), encoding="utf-8")
    write_preview(preview_path)
    files = [packet_path, report_path, preview_path]
    (OUT / "SHA256SUMS.txt").write_text(
        "".join(f"{sha256_file(path)}  {path.relative_to(OUT)}\n" for path in files),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": STATUS,
                "output_dir": str(OUT),
                "packet": str(packet_path),
                "joined_rows": agg["joined_rows"],
                "top5_size_ge_seed_rate": top5_size_rate,
                "top5_full_at_or_better_rate": top5_at_or_better_rate,
                "top5_full_within_10c_rate": top5_within_10c_rate,
                "primary_blocker": packet["decision"]["primary_blocker"],
                "sha256sums": str(OUT / "SHA256SUMS.txt"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
