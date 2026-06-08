#!/usr/bin/env python3
"""Build a public no-order maker queue shadow staging packet for CE25 BTC5M."""

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
OUT = EXPORTS / "ce25_btc5m_maker_queue_public_shadow_staging_packet_20260608"

CANDIDATE_BASE = (
    BT_ROOT
    / "derived/completion_candidate_pipeline_v1/local_20260502_20260518_paircap102/candidate_base.duckdb"
)
CANDIDATE_MANIFEST = CANDIDATE_BASE.parent / "CANDIDATE_BASE_MANIFEST.json"
MAKER_QUEUE_DESIGN_PACKET = (
    EXPORTS
    / "ce25_btc5m_maker_queue_shadow_design_packet_20260608"
    / "CE25_BTC5M_MAKER_QUEUE_SHADOW_DESIGN_PACKET.json"
)
MAKER_SUPPLY_PACKET = (
    EXPORTS
    / "ce25_btc5m_maker_bid_edge_supply_packet_20260607"
    / "CE25_BTC5M_MAKER_BID_EDGE_SUPPLY_PACKET.json"
)
BUILDER = ROOT / "scripts/build_ce25_btc5m_maker_queue_public_shadow_staging_packet.py"
VALIDATOR = ROOT / "scripts/validate_ce25_btc5m_research_packet_chain.py"

STATUS = (
    "KEEP_CE25_BTC5M_MAKER_QUEUE_PUBLIC_SHADOW_STAGING_REVIEWED_PUBLIC_PROXY_ONLY_"
    "PRIVATE_TRUTH_REQUIRED_NOT_OOS_READY"
)
FEE_RATE = 0.07
FRESH_L1_AGE_MS_MAX = 500
FRESH_L2_AGE_MS_MAX = 500
ALIGN_LAG_MS_MAX = 500


BASE_CTE = f"""
with base as (
  select
    candidate_row_id,
    day,
    condition_id,
    slug,
    ts_ms,
    ts_iso,
    offset_s,
    side,
    side_alignment,
    strict_l1_recv_ms,
    strict_l1_age_ms,
    strict_l2_recv_ms,
    strict_l2_age_ms,
    abs(strict_l2_recv_ms - strict_l1_recv_ms) as align_lag_ms,
    public_trade_recv_ms,
    public_trade_recv_ms - strict_l2_recv_ms as touch_after_quote_ms,
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
    coalesce(side_bid_sz, 0.0) as visible_queue_ahead_qty,
    greatest(public_trade_size - coalesce(side_bid_sz, 0.0), 0.0) as queue_adjusted_hypothetical_fill_qty,
    case
      when strict_l1_age_ms <= {FRESH_L1_AGE_MS_MAX}
       and strict_l2_age_ms <= {FRESH_L2_AGE_MS_MAX}
       and abs(strict_l2_recv_ms - strict_l1_recv_ms) <= {ALIGN_LAG_MS_MAX}
      then true else false
    end as quote_fresh,
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
    and side_bid is not null
    and opp_bid is not null
), eligible_touch as (
  select *
  from base
  where quote_fresh
    and public_sell_touched_side_bid
    and touch_after_quote_ms >= 0
), queue_proxy as (
  select *
  from eligible_touch
  where queue_adjusted_hypothetical_fill_qty > 0
)
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def normalize_value(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, Decimal):
        return round(float(value), 6)
    return value


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_preview(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo 'NOT_AUTHORIZED: CE25 BTC5M maker queue public shadow staging is local replay/public-proxy only.' >&2\n"
        "exit 66\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def write_sha256sums(root: Path, files: list[Path]) -> None:
    lines = [f"{sha256_file(path)}  {path.relative_to(root)}" for path in files]
    (root / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def add_rates(aggregate: dict[str, Any]) -> None:
    scanned_rows = int(aggregate.get("scanned_rows") or 0)
    scanned_markets = int(aggregate.get("scanned_markets") or 0)
    eligible_touch_rows = int(aggregate.get("eligible_touch_rows") or 0)
    queue_fill_rows = int(aggregate.get("queue_fill_rows") or 0)
    positive_queue_markets = int(aggregate.get("positive_edge_queue_fill_markets") or 0)
    aggregate["quote_fresh_row_rate"] = round((aggregate["quote_fresh_rows"] or 0) / scanned_rows, 6)
    aggregate["touch_market_share"] = round((aggregate["touch_markets"] or 0) / scanned_markets, 6)
    aggregate["eligible_touch_market_share"] = round((aggregate["eligible_touch_markets"] or 0) / scanned_markets, 6)
    aggregate["queue_fill_row_share_of_eligible_touch"] = (
        round(queue_fill_rows / eligible_touch_rows, 6) if eligible_touch_rows else 0.0
    )
    aggregate["queue_fill_market_share"] = round((aggregate["queue_fill_markets"] or 0) / scanned_markets, 6)
    aggregate["positive_edge_queue_fill_market_share"] = (
        round(positive_queue_markets / scanned_markets, 6) if scanned_markets else 0.0
    )


def build_packet() -> dict[str, Any]:
    design = load_json(MAKER_QUEUE_DESIGN_PACKET)
    maker_supply = load_json(MAKER_SUPPLY_PACKET)
    con = duckdb.connect(str(CANDIDATE_BASE), read_only=True)
    aggregate = fetch_one(
        con,
        BASE_CTE
        + """
        select
          (select count(*) from base) as scanned_rows,
          (select count(distinct condition_id) from base) as scanned_markets,
          (select count(*) from base where quote_fresh) as quote_fresh_rows,
          (select count(distinct condition_id) from base where quote_fresh) as quote_fresh_markets,
          (select count(*) from base where public_sell_touched_side_bid) as touch_rows,
          (select count(distinct condition_id) from base where public_sell_touched_side_bid) as touch_markets,
          (select count(*) from base where public_sell_touched_side_bid and touch_after_quote_ms < 0) as pre_quote_touch_reject_count,
          (select count(*) from base where not quote_fresh) as stale_reject_count,
          (select count(*) from base where side_bid_sz is null) as depth_missing_reject_count,
          (select count(*) from eligible_touch) as eligible_touch_rows,
          (select count(distinct condition_id) from eligible_touch) as eligible_touch_markets,
          count(*) as queue_fill_rows,
          count(distinct condition_id) as queue_fill_markets,
          sum((maker_net_pair_edge_per_share > 0)::int) as positive_edge_queue_fill_rows,
          count(distinct case when maker_net_pair_edge_per_share > 0 then condition_id end) as positive_edge_queue_fill_markets,
          sum(queue_adjusted_hypothetical_fill_qty) as queue_adjusted_fill_qty_sum,
          sum(case when maker_net_pair_edge_per_share > 0 then queue_adjusted_hypothetical_fill_qty else 0 end) as positive_edge_queue_adjusted_fill_qty_sum,
          sum(queue_adjusted_hypothetical_fill_qty * maker_net_pair_edge_per_share) as queue_adjusted_net_edge_qty_sum,
          avg(queue_adjusted_hypothetical_fill_qty) as queue_adjusted_fill_qty_avg,
          quantile_cont(queue_adjusted_hypothetical_fill_qty, 0.5) as queue_adjusted_fill_qty_p50,
          quantile_cont(queue_adjusted_hypothetical_fill_qty, 0.9) as queue_adjusted_fill_qty_p90,
          quantile_cont(queue_adjusted_hypothetical_fill_qty, 0.99) as queue_adjusted_fill_qty_p99,
          quantile_cont(touch_after_quote_ms, 0.5) as touch_after_quote_ms_p50,
          quantile_cont(touch_after_quote_ms, 0.9) as touch_after_quote_ms_p90,
          quantile_cont(touch_after_quote_ms, 0.99) as touch_after_quote_ms_p99,
          quantile_cont(align_lag_ms, 0.99) as align_lag_ms_p99,
          quantile_cont(strict_l1_age_ms, 0.99) as strict_l1_age_ms_p99,
          quantile_cont(strict_l2_age_ms, 0.99) as strict_l2_age_ms_p99
        from queue_proxy
        """,
    )
    add_rates(aggregate)
    model_summary = [
        {
            "model_id": "TOUCH_ONLY_NOT_FILL_PROOF",
            "rows": aggregate["touch_rows"],
            "markets": aggregate["touch_markets"],
            "private_truth_ready": False,
        },
        {
            "model_id": "FRESH_POSTQUOTE_TOUCH_PROXY",
            "rows": aggregate["eligible_touch_rows"],
            "markets": aggregate["eligible_touch_markets"],
            "private_truth_ready": False,
        },
        {
            "model_id": "VISIBLE_DEPTH_ADJUSTED_QUEUE_PROXY",
            "rows": aggregate["queue_fill_rows"],
            "markets": aggregate["queue_fill_markets"],
            "private_truth_ready": False,
        },
        {
            "model_id": "POSITIVE_EDGE_VISIBLE_DEPTH_ADJUSTED_QUEUE_PROXY",
            "rows": aggregate["positive_edge_queue_fill_rows"],
            "markets": aggregate["positive_edge_queue_fill_markets"],
            "private_truth_ready": False,
        },
    ]
    group_summary = fetch_all(
        con,
        BASE_CTE
        + """
        select
          'side_alignment' as group_type,
          side_alignment as group_value,
          count(*) as queue_fill_rows,
          count(distinct condition_id) as queue_fill_markets,
          sum((maker_net_pair_edge_per_share > 0)::int) as positive_edge_queue_fill_rows,
          count(distinct case when maker_net_pair_edge_per_share > 0 then condition_id end) as positive_edge_queue_fill_markets,
          sum(queue_adjusted_hypothetical_fill_qty) as queue_adjusted_fill_qty_sum,
          quantile_cont(queue_adjusted_hypothetical_fill_qty, 0.9) as queue_adjusted_fill_qty_p90,
          quantile_cont(maker_net_pair_edge_per_share, 0.5) as maker_net_pair_edge_p50
        from queue_proxy group by 1, 2
        union all
        select
          'offset_bucket',
          offset_bucket,
          count(*),
          count(distinct condition_id),
          sum((maker_net_pair_edge_per_share > 0)::int),
          count(distinct case when maker_net_pair_edge_per_share > 0 then condition_id end),
          sum(queue_adjusted_hypothetical_fill_qty),
          quantile_cont(queue_adjusted_hypothetical_fill_qty, 0.9),
          quantile_cont(maker_net_pair_edge_per_share, 0.5)
        from queue_proxy group by 1, 2
        union all
        select
          'side_bid_bucket',
          side_bid_bucket,
          count(*),
          count(distinct condition_id),
          sum((maker_net_pair_edge_per_share > 0)::int),
          count(distinct case when maker_net_pair_edge_per_share > 0 then condition_id end),
          sum(queue_adjusted_hypothetical_fill_qty),
          quantile_cont(queue_adjusted_hypothetical_fill_qty, 0.9),
          quantile_cont(maker_net_pair_edge_per_share, 0.5)
        from queue_proxy group by 1, 2
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
          public_trade_size,
          side_bid,
          side_bid_sz,
          opp_bid,
          opp_bid_sz,
          maker_bid_pair_cost,
          maker_net_pair_edge_per_share,
          touch_after_quote_ms,
          strict_l1_age_ms,
          strict_l2_age_ms,
          align_lag_ms,
          queue_adjusted_hypothetical_fill_qty
        from queue_proxy
        where maker_net_pair_edge_per_share > 0
        order by queue_adjusted_hypothetical_fill_qty desc, maker_net_pair_edge_per_share desc
        limit 100
        """,
    )
    con.close()

    packet = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS,
        "source_bindings": {
            "candidate_base": binding(CANDIDATE_BASE),
            "candidate_base_manifest": binding(CANDIDATE_MANIFEST),
            "maker_queue_shadow_design_packet": binding(MAKER_QUEUE_DESIGN_PACKET),
            "maker_bid_edge_supply_packet": binding(MAKER_SUPPLY_PACKET),
            "builder": binding(BUILDER),
            "validator": binding(VALIDATOR),
        },
        "method": {
            "mode": "local_replay_public_no_order_maker_queue_shadow_staging",
            "public_proxy_only": True,
            "private_truth_ready": False,
            "official_fee_rate": FEE_RATE,
            "official_fee_formula": "fee = shares * fee_rate * price * (1 - price)",
            "candidate_filter": "BTC public_trade SELL, offset_s in [0,300), side YES/NO, bid fields present",
            "freshness_thresholds": {
                "strict_l1_age_ms_max": FRESH_L1_AGE_MS_MAX,
                "strict_l2_age_ms_max": FRESH_L2_AGE_MS_MAX,
                "align_lag_ms_max": ALIGN_LAG_MS_MAX,
            },
            "quote_time_proxy": "strict_l2_recv_ms",
            "touch_after_quote_ms": "public_trade_recv_ms - strict_l2_recv_ms",
            "touch_rule": "public_trade_price <= side_bid",
            "visible_depth_adjustment": "max(public_trade_size - side_bid_sz, 0)",
            "private_truth_boundary": "Public touch and visible-depth-adjusted hypothetical fill are not proof our maker order would fill.",
            "design_packet_status": design.get("status"),
            "maker_supply_status": maker_supply.get("status"),
        },
        "aggregate": aggregate,
        "queue_model_summary": model_summary,
        "group_summary": group_summary,
        "top_queue_proxy_examples": top_examples,
        "decision": {
            "public_no_order_queue_shadow_staging_prepared": True,
            "queue_proxy_supports_next_review": True,
            "private_truth_unblocked": False,
            "maker_fill_proven": False,
            "queue_priority_proven": False,
            "orders_authorized": False,
            "oos_discussion_allowed": False,
            "primary_blocker": "own_order_queue_priority_and_fill_telemetry_missing",
            "next_step": "review_public_queue_proxy_then_prepare_private_maker_shadow_requirements_or_new_strategy_family",
        },
        "highest_allowed_status": "local research/public-proxy review-only, not OOS-ready",
        "non_claims": {
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "deployable": False,
            "oos_ready": False,
            "oos_authorized": False,
            "runner_authorized": False,
            "ws_authorized": False,
            "orders_authorized": False,
            "maker_fill_proven": False,
            "queue_priority_proven": False,
        },
    }
    return packet


def render_report(packet: dict[str, Any]) -> str:
    a = packet["aggregate"]
    lines = [
        "# CE25 BTC5M Maker Queue Public Shadow Staging",
        "",
        f"Status: `{packet['status']}`",
        "",
        "## Result",
        "",
        "Local replay public no-order queue proxies support continued maker/queue research, but they do not prove our maker order would fill.",
        "",
        f"- Scanned rows: {a['scanned_rows']}",
        f"- Scanned markets: {a['scanned_markets']}",
        f"- Fresh quote rows: {a['quote_fresh_rows']}",
        f"- Touch markets: {a['touch_markets']}",
        f"- Eligible fresh post-quote touch markets: {a['eligible_touch_markets']}",
        f"- Visible-depth-adjusted queue proxy rows: {a['queue_fill_rows']}",
        f"- Visible-depth-adjusted queue proxy markets: {a['queue_fill_markets']}",
        f"- Positive-edge queue proxy rows: {a['positive_edge_queue_fill_rows']}",
        f"- Positive-edge queue proxy markets: {a['positive_edge_queue_fill_markets']}",
        f"- Positive-edge queue proxy market share: {a['positive_edge_queue_fill_market_share']}",
        f"- Touch-after-quote p99 ms: {a['touch_after_quote_ms_p99']}",
        f"- Align lag p99 ms: {a['align_lag_ms_p99']}",
        "",
        "## Boundary",
        "",
        "The packet is public-proxy only. Public SELL touches and visible-depth deductions are not private fill truth. No WS, OOS, runner, private key, order, cancel, canary, live, deploy, or latest pointer action was authorized or performed.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    packet = build_packet()
    packet_path = OUT / "CE25_BTC5M_MAKER_QUEUE_PUBLIC_SHADOW_STAGING_PACKET.json"
    report_path = OUT / "CE25_BTC5M_MAKER_QUEUE_PUBLIC_SHADOW_STAGING_REPORT.md"
    model_path = OUT / "ce25_btc5m_maker_queue_model_summary.csv"
    group_path = OUT / "ce25_btc5m_maker_queue_group_summary.csv"
    top_path = OUT / "ce25_btc5m_maker_queue_top_proxy_examples.csv"
    preview_path = OUT / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"

    write_json(packet_path, packet)
    report_path.write_text(render_report(packet), encoding="utf-8")
    write_csv(model_path, packet["queue_model_summary"])
    write_csv(group_path, packet["group_summary"])
    write_csv(top_path, packet["top_queue_proxy_examples"])
    write_preview(preview_path)
    write_sha256sums(OUT, [packet_path, report_path, model_path, group_path, top_path, preview_path])
    print(
        json.dumps(
            {
                "packet": str(packet_path),
                "status": packet["status"],
                "aggregate": packet["aggregate"],
                "next_step": packet["decision"]["next_step"],
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
