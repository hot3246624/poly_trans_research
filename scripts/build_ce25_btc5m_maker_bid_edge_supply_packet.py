#!/usr/bin/env python3
"""Build a maker/bid-edge supply packet for CE25 BTC5M local research."""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
BT_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
EXPORTS = ROOT / "data" / "exports"
OUT = EXPORTS / "ce25_btc5m_maker_bid_edge_supply_packet_20260607"
CANDIDATE_BASE = (
    BT_ROOT
    / "derived/completion_candidate_pipeline_v1/local_20260502_20260518_paircap102/candidate_base.duckdb"
)
CANDIDATE_MANIFEST = CANDIDATE_BASE.parent / "CANDIDATE_BASE_MANIFEST.json"
TAKER_SUPPLY_PACKET = (
    EXPORTS
    / "ce25_btc5m_executable_taker_pair_edge_supply_packet_20260607"
    / "CE25_BTC5M_EXECUTABLE_TAKER_PAIR_EDGE_SUPPLY_PACKET.json"
)
BUILDER = ROOT / "scripts/build_ce25_btc5m_maker_bid_edge_supply_packet.py"
VALIDATOR = ROOT / "scripts/validate_ce25_btc5m_research_packet_chain.py"

STATUS = "KEEP_CE25_BTC5M_MAKER_BID_EDGE_SUPPLY_REVIEWED_QUEUE_TRUTH_REQUIRED_NOT_OOS_READY"
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


def normalize_value(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, Decimal):
        return round(float(value), 6)
    return value


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
    public_trade_size,
    side_bid,
    side_bid_sz,
    opp_bid,
    opp_bid_sz,
    side_bid + opp_bid as maker_bid_pair_cost,
    {FEE_RATE} * side_bid * (1 - side_bid) as side_fee_per_share,
    {FEE_RATE} * opp_bid * (1 - opp_bid) as opposite_fee_per_share,
    1 - side_bid - opp_bid
      - {FEE_RATE} * side_bid * (1 - side_bid)
      - {FEE_RATE} * opp_bid * (1 - opp_bid) as maker_net_pair_edge_per_share,
    public_trade_price <= side_bid + 1e-9 as public_sell_touched_side_bid,
    public_trade_price <= side_bid + 0.005 as public_sell_within_0p5c_side_bid,
    public_trade_price <= side_bid + 0.01 as public_sell_within_1c_side_bid,
    case
      when offset_s < 60 then '0-60'
      when offset_s < 120 then '60-120'
      when offset_s < 180 then '120-180'
      when offset_s < 240 then '180-240'
      else '240-300'
    end as offset_bucket,
    case
      when side_bid < 0.10 then '01-10'
      when side_bid < 0.20 then '10-20'
      when side_bid < 0.35 then '20-35'
      when side_bid < 0.50 then '35-50'
      when side_bid < 0.65 then '50-65'
      when side_bid < 0.80 then '65-80'
      else '80-99'
    end as side_bid_bucket
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
  where side_bid is not null
    and opp_bid is not null
    and side_bid between 0.01 and 0.99
    and opp_bid between 0.01 and 0.99
)
"""


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_preview(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo 'NOT_AUTHORIZED: CE25 BTC5M maker/bid edge supply packet is review-only' >&2\n"
        "exit 66\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def build_packet() -> dict[str, Any]:
    con = duckdb.connect(str(CANDIDATE_BASE), read_only=True)
    supply = fetch_one(
        con,
        BASE_CTE
        + """
        select
          (select count(*) from base) as scanned_candidate_rows,
          (select count(distinct condition_id) from base) as scanned_market_count,
          count(*) as eligible_rows,
          count(distinct condition_id) as eligible_market_count,
          sum((maker_net_pair_edge_per_share > 0)::int) as positive_edge_rows,
          count(distinct case when maker_net_pair_edge_per_share > 0 then condition_id end) as positive_edge_markets,
          sum((maker_net_pair_edge_per_share > 0.005)::int) as edge_gt_0p5c_rows,
          count(distinct case when maker_net_pair_edge_per_share > 0.005 then condition_id end) as edge_gt_0p5c_markets,
          sum((maker_net_pair_edge_per_share > 0.01)::int) as edge_gt_1c_rows,
          count(distinct case when maker_net_pair_edge_per_share > 0.01 then condition_id end) as edge_gt_1c_markets,
          sum(public_sell_touched_side_bid::int) as public_sell_touched_side_bid_rows,
          count(distinct case when public_sell_touched_side_bid then condition_id end) as public_sell_touched_side_bid_markets,
          sum((maker_net_pair_edge_per_share > 0 and public_sell_touched_side_bid)::int) as positive_edge_touch_rows,
          count(distinct case when maker_net_pair_edge_per_share > 0 and public_sell_touched_side_bid then condition_id end) as positive_edge_touch_markets,
          avg(maker_bid_pair_cost) as maker_bid_pair_cost_avg,
          quantile_cont(maker_bid_pair_cost, 0.5) as maker_bid_pair_cost_p50,
          quantile_cont(maker_bid_pair_cost, 0.9) as maker_bid_pair_cost_p90,
          avg(maker_net_pair_edge_per_share) as maker_net_pair_edge_avg,
          quantile_cont(maker_net_pair_edge_per_share, 0.5) as maker_net_pair_edge_p50,
          quantile_cont(maker_net_pair_edge_per_share, 0.9) as maker_net_pair_edge_p90,
          max(maker_net_pair_edge_per_share) as maker_net_pair_edge_max
        from eligible
        """,
    )
    threshold_summary = fetch_all(
        con,
        BASE_CTE
        + """
        select *
        from (
          select 'maker_net_pair_edge_gt' as threshold_type, 0.00 as threshold_value,
            sum((maker_net_pair_edge_per_share > 0.00)::int) as row_count,
            count(distinct case when maker_net_pair_edge_per_share > 0.00 then condition_id end) as market_count,
            sum((maker_net_pair_edge_per_share > 0.00 and public_sell_touched_side_bid)::int) as touch_row_count,
            count(distinct case when maker_net_pair_edge_per_share > 0.00 and public_sell_touched_side_bid then condition_id end) as touch_market_count
          from eligible
          union all select 'maker_net_pair_edge_gt', 0.005,
            sum((maker_net_pair_edge_per_share > 0.005)::int),
            count(distinct case when maker_net_pair_edge_per_share > 0.005 then condition_id end),
            sum((maker_net_pair_edge_per_share > 0.005 and public_sell_touched_side_bid)::int),
            count(distinct case when maker_net_pair_edge_per_share > 0.005 and public_sell_touched_side_bid then condition_id end)
          from eligible
          union all select 'maker_net_pair_edge_gt', 0.01,
            sum((maker_net_pair_edge_per_share > 0.01)::int),
            count(distinct case when maker_net_pair_edge_per_share > 0.01 then condition_id end),
            sum((maker_net_pair_edge_per_share > 0.01 and public_sell_touched_side_bid)::int),
            count(distinct case when maker_net_pair_edge_per_share > 0.01 and public_sell_touched_side_bid then condition_id end)
          from eligible
          union all select 'maker_net_pair_edge_gt', 0.02,
            sum((maker_net_pair_edge_per_share > 0.02)::int),
            count(distinct case when maker_net_pair_edge_per_share > 0.02 then condition_id end),
            sum((maker_net_pair_edge_per_share > 0.02 and public_sell_touched_side_bid)::int),
            count(distinct case when maker_net_pair_edge_per_share > 0.02 and public_sell_touched_side_bid then condition_id end)
          from eligible
          union all select 'maker_bid_pair_cost_le', 0.96,
            sum((maker_bid_pair_cost <= 0.96)::int),
            count(distinct case when maker_bid_pair_cost <= 0.96 then condition_id end),
            sum((maker_bid_pair_cost <= 0.96 and public_sell_touched_side_bid)::int),
            count(distinct case when maker_bid_pair_cost <= 0.96 and public_sell_touched_side_bid then condition_id end)
          from eligible
          union all select 'maker_bid_pair_cost_le', 0.98,
            sum((maker_bid_pair_cost <= 0.98)::int),
            count(distinct case when maker_bid_pair_cost <= 0.98 then condition_id end),
            sum((maker_bid_pair_cost <= 0.98 and public_sell_touched_side_bid)::int),
            count(distinct case when maker_bid_pair_cost <= 0.98 and public_sell_touched_side_bid then condition_id end)
          from eligible
          union all select 'maker_bid_pair_cost_le', 1.00,
            sum((maker_bid_pair_cost <= 1.00)::int),
            count(distinct case when maker_bid_pair_cost <= 1.00 then condition_id end),
            sum((maker_bid_pair_cost <= 1.00 and public_sell_touched_side_bid)::int),
            count(distinct case when maker_bid_pair_cost <= 1.00 and public_sell_touched_side_bid then condition_id end)
          from eligible
        )
        order by threshold_type, threshold_value
        """,
    )
    group_summary = fetch_all(
        con,
        BASE_CTE
        + """
        select
          'side_alignment' as group_type,
          side_alignment as group_value,
          count(*) as row_count,
          count(distinct condition_id) as market_count,
          sum((maker_net_pair_edge_per_share > 0)::int) as positive_edge_rows,
          count(distinct case when maker_net_pair_edge_per_share > 0 then condition_id end) as positive_edge_markets,
          sum((maker_net_pair_edge_per_share > 0 and public_sell_touched_side_bid)::int) as positive_edge_touch_rows,
          count(distinct case when maker_net_pair_edge_per_share > 0 and public_sell_touched_side_bid then condition_id end) as positive_edge_touch_markets,
          avg(maker_bid_pair_cost) as maker_bid_pair_cost_avg,
          quantile_cont(maker_net_pair_edge_per_share,0.5) as maker_net_pair_edge_p50
        from eligible group by 1,2
        union all
        select
          'offset_bucket',
          offset_bucket,
          count(*),
          count(distinct condition_id),
          sum((maker_net_pair_edge_per_share > 0)::int),
          count(distinct case when maker_net_pair_edge_per_share > 0 then condition_id end),
          sum((maker_net_pair_edge_per_share > 0 and public_sell_touched_side_bid)::int),
          count(distinct case when maker_net_pair_edge_per_share > 0 and public_sell_touched_side_bid then condition_id end),
          avg(maker_bid_pair_cost),
          quantile_cont(maker_net_pair_edge_per_share,0.5)
        from eligible group by 1,2
        union all
        select
          'side_bid_bucket',
          side_bid_bucket,
          count(*),
          count(distinct condition_id),
          sum((maker_net_pair_edge_per_share > 0)::int),
          count(distinct case when maker_net_pair_edge_per_share > 0 then condition_id end),
          sum((maker_net_pair_edge_per_share > 0 and public_sell_touched_side_bid)::int),
          count(distinct case when maker_net_pair_edge_per_share > 0 and public_sell_touched_side_bid then condition_id end),
          avg(maker_bid_pair_cost),
          quantile_cont(maker_net_pair_edge_per_share,0.5)
        from eligible group by 1,2
        order by group_type, group_value
        """,
    )
    top_examples = fetch_all(
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
          public_trade_price,
          side_bid,
          side_bid_sz,
          opp_bid,
          opp_bid_sz,
          maker_bid_pair_cost,
          side_fee_per_share + opposite_fee_per_share as pair_fee_per_share,
          maker_net_pair_edge_per_share,
          public_sell_touched_side_bid
        from eligible
        order by maker_net_pair_edge_per_share desc, ts_ms
        limit 50
        """,
    )
    con.close()
    scanned_markets = int(supply["scanned_market_count"] or 0)
    positive_markets = int(supply["positive_edge_markets"] or 0)
    positive_touch_markets = int(supply["positive_edge_touch_markets"] or 0)
    supply["positive_edge_market_share"] = round(positive_markets / scanned_markets, 6) if scanned_markets else 0.0
    supply["positive_edge_touch_market_share"] = (
        round(positive_touch_markets / scanned_markets, 6) if scanned_markets else 0.0
    )
    packet = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS,
        "source_bindings": {
            "candidate_base": binding(CANDIDATE_BASE),
            "candidate_base_manifest": binding(CANDIDATE_MANIFEST),
            "taker_supply_packet": binding(TAKER_SUPPLY_PACKET),
            "builder": binding(BUILDER),
            "validator": binding(VALIDATOR),
        },
        "method": {
            "mode": "maker_bid_edge_supply_upper_bound",
            "maker_bid_price": "side_bid",
            "opposite_maker_bid_price": "opp_bid",
            "maker_bid_pair_cost": "side_bid + opp_bid",
            "official_fee_rate": FEE_RATE,
            "official_fee_formula": "fee = shares * fee_rate * price * (1 - price)",
            "maker_net_pair_edge_per_share": "1 - side_bid - opp_bid - fee(side_bid) - fee(opp_bid)",
            "public_sell_touch_proxy": "public_trade_taker_side=SELL and public_trade_price <= side_bid",
            "candidate_filter": "BTC public_trade SELL, offset_s in [0,300), side YES/NO, bid fields present",
            "private_truth_boundary": "Public SELL touch is not proof our maker order would fill; queue priority, placement timing, cancellation, and self order telemetry are missing.",
            "conservative_fee_note": "This upper-bound model still charges official fee on both hypothetical maker-filled legs.",
        },
        "supply_ceiling": supply,
        "threshold_summary": threshold_summary,
        "group_summary": group_summary,
        "top_edge_examples": top_examples,
        "decision": {
            "maker_bid_edge_supply_exists": True,
            "maker_bid_edge_supply_can_support_research": True,
            "taker_backbone_replacement_without_queue_truth_allowed": False,
            "primary_blocker": "queue_priority_and_private_fill_truth_missing",
            "requires_private_or_shadow_maker_fill_evidence": True,
            "oos_discussion_allowed": False,
            "next_step": "maker_queue_shadow_design_packet_or_new_strategy_family_packet",
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
            "maker_fill_proven": False,
        },
    }
    return packet


def render_report(packet: dict[str, Any]) -> str:
    s = packet["supply_ceiling"]
    lines = [
        "# CE25 BTC5M Maker/Bid Edge Supply",
        "",
        f"Status: `{packet['status']}`",
        "",
        "## Result",
        "",
        "Unlike executable taker pair edge, maker/bid pair-edge supply exists in the local Backtest V1 window. This is an upper bound only: public SELL touches do not prove our order would have queue priority or fill.",
        "",
        f"- Scanned candidate rows: {s['scanned_candidate_rows']}",
        f"- Scanned markets: {s['scanned_market_count']}",
        f"- Eligible bid rows: {s['eligible_rows']}",
        f"- Positive maker net edge rows: {s['positive_edge_rows']}",
        f"- Positive maker net edge markets: {s['positive_edge_markets']}",
        f"- Positive maker edge market share: {s['positive_edge_market_share']:.6f}",
        f"- Positive maker edge rows with public SELL touch proxy: {s['positive_edge_touch_rows']}",
        f"- Positive maker edge markets with public SELL touch proxy: {s['positive_edge_touch_markets']}",
        f"- Maker bid pair-cost p50: {s['maker_bid_pair_cost_p50']:.6f}",
        f"- Maker net pair edge/share p50: {s['maker_net_pair_edge_p50']:.6f}",
        "",
        "## Decision",
        "",
        "This justifies a maker/queue-edge research packet, not OOS. The next packet must define shadow/private fill evidence requirements, queue assumptions, cancellation bounds, and non-claims.",
    ]
    return "\n".join(lines) + "\n"


def write_sha256sums(root: Path, files: list[Path]) -> None:
    lines = [f"{sha256_file(path)}  {path.relative_to(root)}" for path in files]
    (root / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    packet = build_packet()
    packet_path = OUT / "CE25_BTC5M_MAKER_BID_EDGE_SUPPLY_PACKET.json"
    report_path = OUT / "CE25_BTC5M_MAKER_BID_EDGE_SUPPLY_REPORT.md"
    threshold_path = OUT / "ce25_btc5m_maker_bid_edge_threshold_summary.csv"
    group_path = OUT / "ce25_btc5m_maker_bid_edge_group_summary.csv"
    top_path = OUT / "ce25_btc5m_maker_bid_edge_top_examples.csv"
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
