#!/usr/bin/env python3
"""Build a review-only NAGI last60 midprice maker-queue proxy packet."""

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
OUT = EXPORTS / "nagi_last60_midprice_maker_queue_proxy_packet_20260608"

CANDIDATE_BASE = (
    BT_ROOT
    / "derived/completion_candidate_pipeline_v1/local_20260502_20260518_paircap102/candidate_base.duckdb"
)
CANDIDATE_MANIFEST = CANDIDATE_BASE.parent / "CANDIDATE_BASE_MANIFEST.json"
PIVOT_PACKET = (
    EXPORTS
    / "nagi_last60_midprice_fastpair_pivot_packet_20260608"
    / "NAGI_LAST60_MIDPRICE_FASTPAIR_PIVOT_PACKET.json"
)
BUILDER = ROOT / "scripts/build_nagi_last60_midprice_maker_queue_proxy_packet.py"

STATUS = (
    "KEEP_NAGI_LAST60_MIDPRICE_MAKER_QUEUE_PROXY_REVIEWED_FEE0_EDGE_EXISTS_"
    "OFFICIAL_TAKER_FEE_BLOCKED_PRIVATE_QUEUE_TRUTH_REQUIRED_NOT_OOS_READY"
)
FEE_RATE_CRYPTO_TAKER = 0.07


SQL = f"""
with base as (
  select
    case
      when side='YES' and side_bid >= 0.35 and side_bid < 0.50 then 'nagi_up_35_50_maker_bid'
      when side='NO' and side_bid >= 0.50 and side_bid < 0.65 then 'nagi_down_50_65_maker_bid'
      else null
    end as branch,
    day,
    condition_id,
    slug,
    candidate_row_id,
    offset_s,
    side,
    side_bid,
    side_bid_sz,
    opp_bid,
    opp_bid_sz,
    public_trade_price,
    public_trade_size,
    strict_l1_age_ms,
    strict_l2_age_ms,
    abs(strict_l2_recv_ms - strict_l1_recv_ms) as align_lag_ms,
    public_trade_recv_ms - strict_l2_recv_ms as touch_after_quote_ms,
    public_trade_price <= side_bid + 1e-9 as touch,
    greatest(public_trade_size - coalesce(side_bid_sz, 0.0), 0.0) as queue_adjusted_hypothetical_fill_qty,
    side_bid + opp_bid as pair_cost,
    1 - side_bid - opp_bid as net_edge_fee0,
    1 - side_bid - opp_bid
      - {FEE_RATE_CRYPTO_TAKER} * side_bid * (1 - side_bid)
      - {FEE_RATE_CRYPTO_TAKER} * opp_bid * (1 - opp_bid) as net_edge_taker_fee07
  from candidate_base
  where asset='BTC'
    and event_kind='public_trade'
    and public_trade_taker_side='SELL'
    and offset_s >= 240
    and offset_s < 300
    and side in ('YES','NO')
    and side_bid is not null
    and opp_bid is not null
), filtered as (
  select * from base where branch is not null
), eligible as (
  select *
  from filtered
  where strict_l1_age_ms <= 500
    and strict_l2_age_ms <= 500
    and align_lag_ms <= 500
    and touch
    and touch_after_quote_ms >= 0
), queue as (
  select * from eligible where queue_adjusted_hypothetical_fill_qty > 0
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


def binding(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if path.is_file():
        out.update({"sha256": sha256_file(path), "size": path.stat().st_size})
    elif path.is_dir():
        out.update({"is_dir": True})
    return out


def normalize(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, Decimal):
        return round(float(value), 6)
    return value


def fetch_all(con: duckdb.DuckDBPyConnection, sql: str) -> list[dict[str, Any]]:
    cur = con.execute(sql)
    names = [item[0] for item in cur.description]
    return [{name: normalize(value) for name, value in zip(names, row)} for row in cur.fetchall()]


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_preview(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo 'NOT_AUTHORIZED: NAGI maker queue proxy packet is local public-proxy review only.' >&2\n"
        "exit 66\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def write_sha256sums(root: Path, files: list[Path]) -> None:
    lines = [f"{sha256_file(path)}  {path.relative_to(root)}" for path in files]
    (root / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_packet() -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    con = duckdb.connect(str(CANDIDATE_BASE), read_only=True)
    branch_summary = fetch_all(
        con,
        SQL
        + """
        select
          branch,
          count(*) as filtered_rows,
          count(distinct condition_id) as filtered_markets,
          sum(touch::int) as touch_rows,
          count(distinct case when touch then condition_id end) as touch_markets,
          (select count(*) from eligible e where e.branch=filtered.branch) as eligible_rows,
          (select count(distinct condition_id) from eligible e where e.branch=filtered.branch) as eligible_markets,
          (select count(*) from queue q where q.branch=filtered.branch) as queue_rows,
          (select count(distinct condition_id) from queue q where q.branch=filtered.branch) as queue_markets,
          (select sum((net_edge_taker_fee07 > 0)::int) from queue q where q.branch=filtered.branch) as queue_pos_taker_fee07_rows,
          (select count(distinct case when net_edge_taker_fee07 > 0 then condition_id end) from queue q where q.branch=filtered.branch) as queue_pos_taker_fee07_markets,
          (select sum((net_edge_fee0 > 0)::int) from queue q where q.branch=filtered.branch) as queue_pos_fee0_rows,
          (select count(distinct case when net_edge_fee0 > 0 then condition_id end) from queue q where q.branch=filtered.branch) as queue_pos_fee0_markets,
          avg(pair_cost) as pair_cost_avg,
          quantile_cont(pair_cost, 0.5) as pair_cost_p50,
          quantile_cont(net_edge_taker_fee07, 0.5) as net_edge_taker_fee07_p50,
          quantile_cont(net_edge_fee0, 0.5) as net_edge_fee0_p50,
          (select sum(queue_adjusted_hypothetical_fill_qty * net_edge_taker_fee07) from queue q where q.branch=filtered.branch) as queue_edge_qty_sum_taker_fee07,
          (select sum(queue_adjusted_hypothetical_fill_qty * net_edge_fee0) from queue q where q.branch=filtered.branch) as queue_edge_qty_sum_fee0,
          (select quantile_cont(queue_adjusted_hypothetical_fill_qty, 0.5) from queue q where q.branch=filtered.branch) as queue_qty_p50,
          (select quantile_cont(queue_adjusted_hypothetical_fill_qty, 0.9) from queue q where q.branch=filtered.branch) as queue_qty_p90,
          (select quantile_cont(touch_after_quote_ms, 0.99) from queue q where q.branch=filtered.branch) as touch_after_quote_ms_p99
        from filtered
        group by branch
        order by branch
        """,
    )
    examples = fetch_all(
        con,
        SQL
        + """
        select
          branch,
          day,
          condition_id,
          slug,
          side,
          offset_s,
          side_bid,
          side_bid_sz,
          opp_bid,
          opp_bid_sz,
          public_trade_price,
          public_trade_size,
          pair_cost,
          net_edge_fee0,
          net_edge_taker_fee07,
          queue_adjusted_hypothetical_fill_qty,
          touch_after_quote_ms,
          strict_l1_age_ms,
          strict_l2_age_ms,
          align_lag_ms
        from queue
        order by queue_adjusted_hypothetical_fill_qty desc, net_edge_fee0 desc
        limit 100
        """,
    )
    con.close()
    branches_by_id = {row["branch"]: row for row in branch_summary}
    fee0_positive = all(float(row.get("queue_edge_qty_sum_fee0") or 0.0) > 0.0 for row in branch_summary)
    taker07_positive = any(float(row.get("queue_edge_qty_sum_taker_fee07") or 0.0) > 0.0 for row in branch_summary)
    packet = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS,
        "source_bindings": {
            "candidate_base": binding(CANDIDATE_BASE),
            "candidate_manifest": binding(CANDIDATE_MANIFEST),
            "nagi_pivot_packet": binding(PIVOT_PACKET),
            "builder": binding(BUILDER),
        },
        "method": {
            "mode": "local_public_no_order_maker_queue_proxy",
            "polymarket_fee_docs_url": "https://docs.polymarket.com/trading/fees",
            "fee_interpretation": "Crypto taker fee rate is 0.07; maker fee rate is 0. NAGI learning is only coherent as maker/queue research.",
            "public_proxy_only": True,
            "private_truth_ready": False,
            "branch_definitions": {
                "nagi_up_35_50_maker_bid": "BTC 5m last60, side YES/UP, side_bid in [0.35, 0.50)",
                "nagi_down_50_65_maker_bid": "BTC 5m last60, side NO/DOWN, side_bid in [0.50, 0.65)",
            },
            "queue_proxy": "public SELL trade at or through bid, post quote, fresh L1/L2, minus visible bid depth",
            "freshness": {
                "strict_l1_age_ms_max": 500,
                "strict_l2_age_ms_max": 500,
                "align_lag_ms_max": 500,
            },
            "private_truth_boundary": "Public touch and visible-depth-adjusted queue proxy are not proof our maker order would fill.",
        },
        "branch_summary": branch_summary,
        "branches_by_id": branches_by_id,
        "top_queue_proxy_examples": examples,
        "decision": {
            "nagi_maker_queue_proxy_fee0_edge_exists": fee0_positive,
            "nagi_maker_queue_proxy_survives_taker_fee07": taker07_positive,
            "requires_true_maker_execution": True,
            "private_truth_unblocked": False,
            "orders_authorized": False,
            "oos_discussion_allowed": False,
            "next_step": "nagi_private_maker_shadow_requirements_packet_or_local_residual_killer_matrix_review_only",
        },
        "highest_allowed_status": "local public-proxy review-only, not OOS-ready",
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
    return packet, branch_summary, examples


def render_report(packet: dict[str, Any]) -> str:
    lines = [
        "# NAGI Last60 Midprice Maker Queue Proxy",
        "",
        f"Status: `{packet['status']}`",
        "",
        "## Result",
        "",
        "NAGI becomes coherent only as maker/queue research. In local public no-order queue proxy, both NAGI midprice branches have positive fee0 queue-edge, while taker-fee 0.07 kills the same branches.",
        "",
    ]
    for row in packet["branch_summary"]:
        lines.extend(
            [
                f"### {row['branch']}",
                "",
                f"- Filtered markets: {row['filtered_markets']}",
                f"- Eligible post-quote touch markets: {row['eligible_markets']}",
                f"- Queue proxy markets: {row['queue_markets']}",
                f"- Queue positive fee0 markets: {row['queue_pos_fee0_markets']}",
                f"- Queue positive taker-fee07 markets: {row['queue_pos_taker_fee07_markets']}",
                f"- Queue edge qty sum fee0: {row['queue_edge_qty_sum_fee0']}",
                f"- Queue edge qty sum taker fee07: {row['queue_edge_qty_sum_taker_fee07']}",
                f"- Touch-after-quote p99 ms: {row['touch_after_quote_ms_p99']}",
                "",
            ]
        )
    lines.extend(
        [
            "## Boundary",
            "",
            "This is public-proxy only. It does not prove our maker order would fill, does not authorize WS/OOS/orders, and does not make the strategy ready.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    packet, branch_summary, examples = build_packet()
    packet_path = OUT / "NAGI_LAST60_MIDPRICE_MAKER_QUEUE_PROXY_PACKET.json"
    report_path = OUT / "NAGI_LAST60_MIDPRICE_MAKER_QUEUE_PROXY_REPORT.md"
    summary_path = OUT / "nagi_last60_midprice_maker_queue_branch_summary.csv"
    examples_path = OUT / "nagi_last60_midprice_maker_queue_top_examples.csv"
    preview_path = OUT / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    write_json(packet_path, packet)
    report_path.write_text(render_report(packet), encoding="utf-8")
    write_csv(summary_path, branch_summary)
    write_csv(examples_path, examples)
    write_preview(preview_path)
    write_sha256sums(OUT, [packet_path, report_path, summary_path, examples_path, preview_path])
    print(json.dumps({"packet": str(packet_path), "status": packet["status"], "decision": packet["decision"], "branch_summary": branch_summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
