#!/usr/bin/env python3
"""Build a supply-ceiling packet for executable taker pair edge in CE25 BTC5M."""

from __future__ import annotations

import csv
import hashlib
import json
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
BT_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
EXPORTS = ROOT / "data" / "exports"
OUT = EXPORTS / "ce25_btc5m_executable_taker_pair_edge_supply_packet_20260607"
CANDIDATE_BASE = (
    BT_ROOT
    / "derived/completion_candidate_pipeline_v1/local_20260502_20260518_paircap102/candidate_base.duckdb"
)
CANDIDATE_MANIFEST = CANDIDATE_BASE.parent / "CANDIDATE_BASE_MANIFEST.json"
ADAPTER_GRID_PACKET = (
    EXPORTS
    / "ce25_btc5m_executable_price_adapter_grid_packet_20260607"
    / "CE25_BTC5M_EXECUTABLE_PRICE_ADAPTER_GRID_PACKET.json"
)
BUILDER = ROOT / "scripts/build_ce25_btc5m_executable_taker_pair_edge_supply_packet.py"
VALIDATOR = ROOT / "scripts/validate_ce25_btc5m_research_packet_chain.py"

STATUS = (
    "BLOCKED_CE25_BTC5M_EXECUTABLE_TAKER_PAIR_EDGE_SUPPLY_CEILING_HIGH_PARTICIPATION_"
    "IMPOSSIBLE_NOT_OOS_READY"
)
FEE_RATE = 0.07


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def binding(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if path.exists():
        out.update({"sha256": sha256_file(path), "size": path.stat().st_size})
    return out


def fetch_one(con: duckdb.DuckDBPyConnection, sql: str) -> dict[str, Any]:
    cur = con.execute(sql)
    names = [item[0] for item in cur.description]
    row = cur.fetchone()
    return {name: normalize_value(value) for name, value in zip(names, row)}


def fetch_all(con: duckdb.DuckDBPyConnection, sql: str) -> list[dict[str, Any]]:
    cur = con.execute(sql)
    names = [item[0] for item in cur.description]
    return [
        {name: normalize_value(value) for name, value in zip(names, row)}
        for row in cur.fetchall()
    ]


def normalize_value(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, Decimal):
        return round(float(value), 6)
    return value


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_preview(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo 'NOT_AUTHORIZED: CE25 BTC5M executable taker pair-edge supply packet is review-only' >&2\n"
        "exit 66\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


BASE_CTE = f"""
with base as (
  select
    candidate_row_id,
    day,
    condition_id,
    slug,
    ts_ms,
    offset_s,
    side,
    side_alignment,
    public_trade_price,
    buy_vwap_10 as execution_px,
    opp_ask,
    buy_full_10,
    buy_filled_10,
    strict_l2_age_ms,
    buy_vwap_10 + opp_ask as raw_pair_cost,
    {FEE_RATE} * buy_vwap_10 * (1 - buy_vwap_10) as execution_fee_per_share,
    {FEE_RATE} * opp_ask * (1 - opp_ask) as opposite_fee_per_share,
    1 - buy_vwap_10 - opp_ask
      - {FEE_RATE} * buy_vwap_10 * (1 - buy_vwap_10)
      - {FEE_RATE} * opp_ask * (1 - opp_ask) as net_pair_edge_per_share,
    case
      when offset_s < 60 then '0-60'
      when offset_s < 120 then '60-120'
      when offset_s < 180 then '120-180'
      when offset_s < 240 then '180-240'
      else '240-300'
    end as offset_bucket,
    case
      when buy_vwap_10 < 0.10 then '05-10'
      when buy_vwap_10 < 0.20 then '10-20'
      when buy_vwap_10 < 0.35 then '20-35'
      when buy_vwap_10 < 0.50 then '35-50'
      when buy_vwap_10 < 0.65 then '50-65'
      when buy_vwap_10 < 0.80 then '65-80'
      else '80-90'
    end as execution_px_bucket
  from candidate_base
  where asset='BTC'
    and event_kind='public_trade'
    and public_trade_taker_side='SELL'
    and side in ('YES','NO')
    and offset_s >= 0
    and offset_s < 300
), eligible as (
  select *
  from base
  where buy_full_10
    and execution_px between 0.05 and 0.90
    and opp_ask is not null
)
"""


def build_packet() -> dict[str, Any]:
    con = duckdb.connect(str(CANDIDATE_BASE), read_only=True)
    total = fetch_one(
        con,
        BASE_CTE
        + """
        select
          (select count(*) from base) as scanned_candidate_rows,
          (select count(distinct condition_id) from base) as scanned_market_count,
          count(*) as eligible_rows,
          count(distinct condition_id) as eligible_market_count,
          sum((net_pair_edge_per_share > 0)::int) as positive_net_edge_rows,
          count(distinct case when net_pair_edge_per_share > 0 then condition_id end) as positive_net_edge_markets,
          sum((net_pair_edge_per_share > 0.005)::int) as edge_gt_0p5c_rows,
          count(distinct case when net_pair_edge_per_share > 0.005 then condition_id end) as edge_gt_0p5c_markets,
          sum((net_pair_edge_per_share > 0.01)::int) as edge_gt_1c_rows,
          count(distinct case when net_pair_edge_per_share > 0.01 then condition_id end) as edge_gt_1c_markets,
          sum((raw_pair_cost <= 0.98)::int) as raw_pair_cost_le_098_rows,
          count(distinct case when raw_pair_cost <= 0.98 then condition_id end) as raw_pair_cost_le_098_markets,
          sum((raw_pair_cost <= 1.00)::int) as raw_pair_cost_le_100_rows,
          count(distinct case when raw_pair_cost <= 1.00 then condition_id end) as raw_pair_cost_le_100_markets,
          avg(raw_pair_cost) as raw_pair_cost_avg,
          quantile_cont(raw_pair_cost, 0.5) as raw_pair_cost_p50,
          quantile_cont(raw_pair_cost, 0.9) as raw_pair_cost_p90,
          avg(net_pair_edge_per_share) as net_pair_edge_avg,
          quantile_cont(net_pair_edge_per_share, 0.5) as net_pair_edge_p50,
          quantile_cont(net_pair_edge_per_share, 0.9) as net_pair_edge_p90,
          max(net_pair_edge_per_share) as net_pair_edge_max
        from eligible
        """,
    )
    threshold_rows = fetch_all(
        con,
        BASE_CTE
        + """
        select
          threshold_type,
          threshold_value,
          row_count,
          market_count
        from (
          select 'raw_pair_cost_le' as threshold_type, 0.90 as threshold_value,
            sum((raw_pair_cost <= 0.90)::int) as row_count,
            count(distinct case when raw_pair_cost <= 0.90 then condition_id end) as market_count from eligible
          union all select 'raw_pair_cost_le', 0.92, sum((raw_pair_cost <= 0.92)::int),
            count(distinct case when raw_pair_cost <= 0.92 then condition_id end) from eligible
          union all select 'raw_pair_cost_le', 0.94, sum((raw_pair_cost <= 0.94)::int),
            count(distinct case when raw_pair_cost <= 0.94 then condition_id end) from eligible
          union all select 'raw_pair_cost_le', 0.96, sum((raw_pair_cost <= 0.96)::int),
            count(distinct case when raw_pair_cost <= 0.96 then condition_id end) from eligible
          union all select 'raw_pair_cost_le', 0.98, sum((raw_pair_cost <= 0.98)::int),
            count(distinct case when raw_pair_cost <= 0.98 then condition_id end) from eligible
          union all select 'raw_pair_cost_le', 1.00, sum((raw_pair_cost <= 1.00)::int),
            count(distinct case when raw_pair_cost <= 1.00 then condition_id end) from eligible
          union all select 'raw_pair_cost_le', 1.02, sum((raw_pair_cost <= 1.02)::int),
            count(distinct case when raw_pair_cost <= 1.02 then condition_id end) from eligible
          union all select 'net_pair_edge_gt', 0.00, sum((net_pair_edge_per_share > 0.00)::int),
            count(distinct case when net_pair_edge_per_share > 0.00 then condition_id end) from eligible
          union all select 'net_pair_edge_gt', 0.005, sum((net_pair_edge_per_share > 0.005)::int),
            count(distinct case when net_pair_edge_per_share > 0.005 then condition_id end) from eligible
          union all select 'net_pair_edge_gt', 0.01, sum((net_pair_edge_per_share > 0.01)::int),
            count(distinct case when net_pair_edge_per_share > 0.01 then condition_id end) from eligible
        )
        order by threshold_type, threshold_value
        """,
    )
    group_rows = fetch_all(
        con,
        BASE_CTE
        + """
        select
          'side_alignment' as group_type,
          side_alignment as group_value,
          count(*) as row_count,
          count(distinct condition_id) as market_count,
          sum((net_pair_edge_per_share > 0)::int) as positive_net_edge_rows,
          count(distinct case when net_pair_edge_per_share > 0 then condition_id end) as positive_net_edge_markets,
          avg(raw_pair_cost) as raw_pair_cost_avg,
          quantile_cont(raw_pair_cost,0.5) as raw_pair_cost_p50,
          max(net_pair_edge_per_share) as net_pair_edge_max
        from eligible group by 1,2
        union all
        select
          'offset_bucket', offset_bucket,
          count(*),
          count(distinct condition_id),
          sum((net_pair_edge_per_share > 0)::int),
          count(distinct case when net_pair_edge_per_share > 0 then condition_id end),
          avg(raw_pair_cost),
          quantile_cont(raw_pair_cost,0.5),
          max(net_pair_edge_per_share)
        from eligible group by 1,2
        union all
        select
          'execution_px_bucket', execution_px_bucket,
          count(*),
          count(distinct condition_id),
          sum((net_pair_edge_per_share > 0)::int),
          count(distinct case when net_pair_edge_per_share > 0 then condition_id end),
          avg(raw_pair_cost),
          quantile_cont(raw_pair_cost,0.5),
          max(net_pair_edge_per_share)
        from eligible group by 1,2
        order by group_type, group_value
        """,
    )
    top_edges = fetch_all(
        con,
        BASE_CTE
        + """
        select
          day,
          condition_id,
          slug,
          side,
          side_alignment,
          offset_s,
          execution_px,
          opp_ask,
          raw_pair_cost,
          execution_fee_per_share + opposite_fee_per_share as pair_fee_per_share,
          net_pair_edge_per_share
        from eligible
        order by net_pair_edge_per_share desc, ts_ms
        limit 50
        """,
    )
    con.close()
    scanned_markets = int(total["scanned_market_count"] or 0)
    positive_markets = int(total["positive_net_edge_markets"] or 0)
    positive_market_share = round(positive_markets / scanned_markets, 6) if scanned_markets else 0.0
    high_participation_possible = positive_market_share >= 0.80
    packet = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS,
        "source_bindings": {
            "candidate_base": binding(CANDIDATE_BASE),
            "candidate_base_manifest": binding(CANDIDATE_MANIFEST),
            "adapter_grid_packet": binding(ADAPTER_GRID_PACKET),
            "builder": binding(BUILDER),
            "validator": binding(VALIDATOR),
        },
        "method": {
            "mode": "executable_taker_pair_edge_supply_ceiling",
            "execution_px": "buy_vwap_10",
            "opposite_px": "opp_ask",
            "raw_pair_cost": "buy_vwap_10 + opp_ask",
            "official_fee_rate": FEE_RATE,
            "official_fee_formula": "fee = shares * fee_rate * price * (1 - price)",
            "net_pair_edge_per_share": "1 - execution_px - opposite_px - fee(execution_px) - fee(opposite_px)",
            "candidate_filter": "BTC public_trade SELL, offset_s in [0,300), side YES/NO, buy_full_10, execution_px 0.05..0.90",
        },
        "supply_ceiling": {
            **total,
            "positive_net_edge_market_share": positive_market_share,
            "high_participation_market_share_floor": 0.80,
            "high_participation_possible_under_taker_pair_edge": high_participation_possible,
        },
        "threshold_summary": threshold_rows,
        "group_summary": group_rows,
        "top_edge_examples": top_edges,
        "decision": {
            "high_participation_taker_backbone_blocked": not high_participation_possible,
            "primary_blocker": "executable_fee_inclusive_pair_edge_supply_is_too_sparse",
            "cooldown_size_merge_tuning_can_fix": False,
            "requires_maker_or_queue_edge_or_new_signal_family": True,
            "oos_discussion_allowed": False,
            "next_step": "new_strategy_family_or_maker_edge_research_packet",
        },
        "highest_allowed_status": "local research/review-only, not OOS-ready",
        "non_claims": {
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "deployable": False,
            "oos_authorized": False,
            "runner_authorized": False,
            "orders_authorized": False,
        },
    }
    return packet


def render_report(packet: dict[str, Any]) -> str:
    s = packet["supply_ceiling"]
    lines = [
        "# CE25 BTC5M Executable Taker Pair-Edge Supply Ceiling",
        "",
        f"Status: `{packet['status']}`",
        "",
        "## Result",
        "",
        "Fee-inclusive executable taker pair edge is too sparse to support a high-participation CE25 BTC5M backbone in the local Backtest V1 window.",
        "",
        f"- Scanned candidate rows: {s['scanned_candidate_rows']}",
        f"- Scanned markets: {s['scanned_market_count']}",
        f"- Eligible executable rows: {s['eligible_rows']}",
        f"- Positive net pair-edge rows: {s['positive_net_edge_rows']}",
        f"- Positive net pair-edge markets: {s['positive_net_edge_markets']}",
        f"- Positive market share: {s['positive_net_edge_market_share']:.6f}",
        f"- Raw pair-cost <= 0.98 rows: {s['raw_pair_cost_le_098_rows']}",
        f"- Raw pair-cost <= 1.00 rows: {s['raw_pair_cost_le_100_rows']}",
        f"- Median raw pair-cost: {s['raw_pair_cost_p50']:.6f}",
        f"- Median net pair edge/share: {s['net_pair_edge_p50']:.6f}",
        "",
        "## Decision",
        "",
        "Cooldown, size, merge/reuse, or cd5 fallback tuning cannot create pair-edge supply that is absent at executable taker prices. The next research step must be a new signal family or a maker/queue-edge packet, not OOS.",
    ]
    return "\n".join(lines) + "\n"


def write_sha256sums(root: Path, files: list[Path]) -> None:
    lines = [f"{sha256_file(path)}  {path.relative_to(root)}" for path in files]
    (root / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    packet = build_packet()
    packet_path = OUT / "CE25_BTC5M_EXECUTABLE_TAKER_PAIR_EDGE_SUPPLY_PACKET.json"
    report_path = OUT / "CE25_BTC5M_EXECUTABLE_TAKER_PAIR_EDGE_SUPPLY_REPORT.md"
    threshold_path = OUT / "ce25_btc5m_executable_taker_pair_edge_threshold_summary.csv"
    group_path = OUT / "ce25_btc5m_executable_taker_pair_edge_group_summary.csv"
    top_path = OUT / "ce25_btc5m_executable_taker_pair_edge_top_examples.csv"
    preview_path = OUT / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    write_json(packet_path, packet)
    report_path.write_text(render_report(packet), encoding="utf-8")
    write_csv(threshold_path, packet["threshold_summary"])
    write_csv(group_path, packet["group_summary"])
    write_csv(top_path, packet["top_edge_examples"])
    write_preview(preview_path)
    write_sha256sums(OUT, [packet_path, report_path, threshold_path, group_path, top_path, preview_path])
    print(json.dumps({"packet": str(packet_path), "status": packet["status"], "supply_ceiling": packet["supply_ceiling"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
