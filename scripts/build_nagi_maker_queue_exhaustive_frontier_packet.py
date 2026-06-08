#!/usr/bin/env python3
"""Build a broad local public-proxy maker-queue frontier for NAGI-style BTC5M."""

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
OUT = EXPORTS / "nagi_maker_queue_exhaustive_frontier_packet_20260608"

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
RESIDUAL_MATRIX_PACKET = (
    EXPORTS
    / "nagi_last60_midprice_maker_queue_residual_matrix_packet_20260608"
    / "NAGI_LAST60_MIDPRICE_MAKER_QUEUE_RESIDUAL_MATRIX_PACKET.json"
)
APPROVAL_PACKET = (
    EXPORTS
    / "nagi_private_maker_shadow_approval_packet_20260608"
    / "NAGI_PRIVATE_MAKER_SHADOW_APPROVAL_PACKET.json"
)
BUILDER = ROOT / "scripts/build_nagi_maker_queue_exhaustive_frontier_packet.py"

STATUS = (
    "KEEP_NAGI_MAKER_QUEUE_EXHAUSTIVE_FRONTIER_REVIEWED_"
    "MAKER_FEE0_EDGE_STRUCTURE_MAPPED_PRIVATE_TRUTH_REQUIRED_NOT_OOS_READY"
)
FEE_RATE_CRYPTO_TAKER = 0.07

TIME_WINDOWS = [
    ("first60", 0.0, 60.0),
    ("m60_120", 60.0, 120.0),
    ("m120_180", 120.0, 180.0),
    ("m180_240", 180.0, 240.0),
    ("last60", 240.0, 300.0),
    ("last120", 180.0, 300.0),
    ("last180", 120.0, 300.0),
    ("full300", 0.0, 300.0),
]
PRICE_BANDS = [
    ("p20_35", 0.20, 0.35),
    ("p35_425", 0.35, 0.425),
    ("p425_50", 0.425, 0.50),
    ("p35_50", 0.35, 0.50),
    ("p45_55", 0.45, 0.55),
    ("p50_575", 0.50, 0.575),
    ("p575_65", 0.575, 0.65),
    ("p50_65", 0.50, 0.65),
    ("p65_80", 0.65, 0.80),
]
SIDES = ["YES", "NO"]
PAIR_CAPS = [1.000, 0.995, 0.990, 0.985, 0.980, 0.975, 0.970]
QUEUE_MIN_QTYS = [0.0, 1.0, 5.0, 10.0, 25.0]


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
    return out


def q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


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


def write_sha256sums(root: Path, files: list[Path]) -> None:
    lines = [f"{sha256_file(path)}  {path.relative_to(root)}" for path in files]
    (root / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_preview(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo 'NOT_AUTHORIZED: NAGI exhaustive frontier is local public-proxy review only.' >&2\n"
        "exit 66\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def variant_values_sql() -> str:
    rows: list[str] = []
    for time_id, start_s, end_s in TIME_WINDOWS:
        for band_id, px_lo, px_hi in PRICE_BANDS:
            for side in SIDES:
                for pair_cap in PAIR_CAPS:
                    for queue_min_qty in QUEUE_MIN_QTYS:
                        variant_id = (
                            f"{time_id}__{side.lower()}__{band_id}"
                            f"__pc{pair_cap:.3f}__qmin{queue_min_qty:g}"
                        )
                        rows.append(
                            "("
                            + ", ".join(
                                [
                                    q(variant_id),
                                    q(time_id),
                                    f"{start_s:.6f}",
                                    f"{end_s:.6f}",
                                    q(band_id),
                                    q(side),
                                    f"{px_lo:.6f}",
                                    f"{px_hi:.6f}",
                                    f"{pair_cap:.6f}",
                                    f"{queue_min_qty:.6f}",
                                ]
                            )
                            + ")"
                        )
    return (
        "variants(variant_id, time_id, start_s, end_s, band_id, side, px_lo, px_hi, pair_cap, queue_min_qty) as (values\n  "
        + ",\n  ".join(rows)
        + "\n)"
    )


def build_frontier(con: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    sql = (
        "with "
        + variant_values_sql()
        + f"""
, base as (
  select
    condition_id,
    candidate_row_id,
    offset_s,
    side,
    side_bid,
    side_bid_sz,
    opp_bid,
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
    and side in ('YES','NO')
    and side_bid is not null
    and opp_bid is not null
    and strict_l1_age_ms <= 500
    and strict_l2_age_ms <= 500
    and abs(strict_l2_recv_ms - strict_l1_recv_ms) <= 500
    and public_trade_recv_ms - strict_l2_recv_ms >= 0
    and public_trade_price <= side_bid + 1e-9
), joined as (
  select
    v.*,
    b.condition_id,
    b.candidate_row_id,
    b.pair_cost,
    b.net_edge_fee0,
    b.net_edge_taker_fee07,
    b.queue_adjusted_hypothetical_fill_qty,
    b.public_trade_size,
    b.touch_after_quote_ms,
    b.align_lag_ms
  from variants v
  join base b
    on b.side = v.side
   and b.offset_s >= v.start_s
   and b.offset_s < v.end_s
   and b.side_bid >= v.px_lo
   and b.side_bid < v.px_hi
   and b.pair_cost <= v.pair_cap + 1e-9
), queue as (
  select *
  from joined
  where queue_adjusted_hypothetical_fill_qty > queue_min_qty
)
select
  j.variant_id,
  j.time_id,
  j.start_s,
  j.end_s,
  j.band_id,
  j.side,
  j.px_lo,
  j.px_hi,
  j.pair_cap,
  j.queue_min_qty,
  count(*) as eligible_touch_rows,
  count(distinct j.condition_id) as eligible_touch_markets,
  count(q.condition_id) as queue_rows,
  count(distinct q.condition_id) as queue_markets,
  sum((q.net_edge_fee0 > 0)::int) as queue_pos_fee0_rows,
  count(distinct case when q.net_edge_fee0 > 0 then q.condition_id end) as queue_pos_fee0_markets,
  sum((q.net_edge_taker_fee07 > 0)::int) as queue_pos_taker_fee07_rows,
  count(distinct case when q.net_edge_taker_fee07 > 0 then q.condition_id end) as queue_pos_taker_fee07_markets,
  sum(q.queue_adjusted_hypothetical_fill_qty) as queue_qty_sum,
  sum(q.queue_adjusted_hypothetical_fill_qty * q.net_edge_fee0) as queue_edge_qty_sum_fee0,
  sum(q.queue_adjusted_hypothetical_fill_qty * q.net_edge_taker_fee07) as queue_edge_qty_sum_taker_fee07,
  avg(q.pair_cost) as queue_pair_cost_avg,
  quantile_cont(q.pair_cost, 0.5) as queue_pair_cost_p50,
  avg(q.net_edge_fee0) as queue_net_edge_fee0_avg,
  quantile_cont(q.net_edge_fee0, 0.5) as queue_net_edge_fee0_p50,
  quantile_cont(q.queue_adjusted_hypothetical_fill_qty, 0.5) as queue_qty_p50,
  quantile_cont(q.queue_adjusted_hypothetical_fill_qty, 0.9) as queue_qty_p90,
  quantile_cont(q.touch_after_quote_ms, 0.99) as touch_after_quote_ms_p99,
  quantile_cont(q.align_lag_ms, 0.99) as align_lag_ms_p99,
  case when count(*) > 0 then count(q.condition_id)::double / count(*) else 0 end as queue_row_share,
  case when count(distinct j.condition_id) > 0 then count(distinct q.condition_id)::double / count(distinct j.condition_id) else 0 end as queue_market_share
from joined j
left join queue q
  on q.variant_id = j.variant_id
 and q.candidate_row_id = j.candidate_row_id
group by
  j.variant_id, j.time_id, j.start_s, j.end_s, j.band_id, j.side, j.px_lo, j.px_hi, j.pair_cap, j.queue_min_qty
order by queue_edge_qty_sum_fee0 desc nulls last, queue_markets desc
"""
    )
    rows = fetch_all(con, sql)
    for row in rows:
        edge_fee0 = float(row.get("queue_edge_qty_sum_fee0") or 0.0)
        edge_taker = float(row.get("queue_edge_qty_sum_taker_fee07") or 0.0)
        q_markets = int(row.get("queue_markets") or 0)
        q_share = float(row.get("queue_market_share") or 0.0)
        row["passes_fee0_scale_gate"] = edge_fee0 > 0 and q_markets >= 250 and q_share >= 0.20
        row["passes_fee0_high_coverage_gate"] = edge_fee0 > 0 and q_markets >= 500 and q_share >= 0.20
        row["survives_taker_fee07_scale_gate"] = edge_taker > 0 and q_markets >= 100 and q_share >= 0.20
        row["nagi_anchor_like"] = (
            row["time_id"] == "last60"
            and row["side"] == "YES"
            and abs(float(row["px_lo"]) - 0.35) < 1e-9
            and abs(float(row["px_hi"]) - 0.50) < 1e-9
        )
        row["private_truth_ready"] = False
        row["oos_ready"] = False
    return rows


def best_by(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    return max(rows, key=lambda r: float(r.get(key) or -1e18))


def top_rows(rows: list[dict[str, Any]], limit: int = 80) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda r: float(r.get("queue_edge_qty_sum_fee0") or -1e18), reverse=True)[:limit]


def grouped_frontier(rows: list[dict[str, Any]], group_keys: list[str], metric: str) -> list[dict[str, Any]]:
    best: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        key = tuple(row.get(k) for k in group_keys)
        if key not in best or float(row.get(metric) or -1e18) > float(best[key].get(metric) or -1e18):
            best[key] = row
    out: list[dict[str, Any]] = []
    for key, row in sorted(best.items()):
        item = {k: v for k, v in zip(group_keys, key)}
        item.update(
            {
                "variant_id": row["variant_id"],
                "queue_markets": row["queue_markets"],
                "queue_market_share": row["queue_market_share"],
                "queue_edge_qty_sum_fee0": row["queue_edge_qty_sum_fee0"],
                "queue_edge_qty_sum_taker_fee07": row["queue_edge_qty_sum_taker_fee07"],
                "queue_pair_cost_p50": row["queue_pair_cost_p50"],
                "passes_fee0_high_coverage_gate": row["passes_fee0_high_coverage_gate"],
            }
        )
        out.append(item)
    return out


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fee0_scale = [r for r in rows if r["passes_fee0_scale_gate"]]
    fee0_high = [r for r in rows if r["passes_fee0_high_coverage_gate"]]
    taker_scale = [r for r in rows if r["survives_taker_fee07_scale_gate"]]
    anchor = [r for r in rows if r["nagi_anchor_like"]]
    return {
        "variant_count": len(rows),
        "fee0_scale_pass_count": len(fee0_scale),
        "fee0_high_coverage_pass_count": len(fee0_high),
        "taker_fee07_scale_pass_count": len(taker_scale),
        "best_fee0_edge_variant": best_by(rows, "queue_edge_qty_sum_fee0"),
        "best_taker_fee07_edge_variant": best_by(rows, "queue_edge_qty_sum_taker_fee07"),
        "best_queue_markets_variant": best_by(rows, "queue_markets"),
        "best_nagi_anchor_like_variant": best_by(anchor, "queue_edge_qty_sum_fee0") if anchor else None,
        "frontier_by_time": grouped_frontier(rows, ["time_id"], "queue_edge_qty_sum_fee0"),
        "frontier_by_time_side": grouped_frontier(rows, ["time_id", "side"], "queue_edge_qty_sum_fee0"),
        "frontier_by_band_side": grouped_frontier(rows, ["band_id", "side"], "queue_edge_qty_sum_fee0"),
    }


def render_report(packet: dict[str, Any]) -> str:
    s = packet["summary"]
    best = s["best_fee0_edge_variant"]
    anchor = s.get("best_nagi_anchor_like_variant") or {}
    lines = [
        "# NAGI Maker Queue Exhaustive Frontier",
        "",
        f"Status: `{packet['status']}`",
        "",
        "## Result",
        "",
        f"- Variants scanned: {s['variant_count']}",
        f"- Fee0 scale pass count: {s['fee0_scale_pass_count']}",
        f"- Fee0 high-coverage pass count: {s['fee0_high_coverage_pass_count']}",
        f"- Taker-fee07 scale pass count: {s['taker_fee07_scale_pass_count']}",
        f"- Best fee0 variant: `{best['variant_id']}`",
        f"- Best fee0 queue markets: {best['queue_markets']}",
        f"- Best fee0 queue edge qty sum: {best['queue_edge_qty_sum_fee0']}",
        f"- Best fee0 pair cost p50: {best['queue_pair_cost_p50']}",
    ]
    if anchor:
        lines.extend(
            [
                f"- Best NAGI-anchor-like variant: `{anchor['variant_id']}`",
                f"- Anchor queue markets: {anchor['queue_markets']}",
                f"- Anchor queue edge qty sum fee0: {anchor['queue_edge_qty_sum_fee0']}",
            ]
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "This is local public-proxy review only. It maps where maker/no-fee edge appears in local replay. It does not prove our maker fill, queue priority, private truth, OOS, or deployability.",
            "",
        ]
    )
    return "\n".join(lines)


def build_packet() -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    con = duckdb.connect(str(CANDIDATE_BASE), read_only=True)
    rows = build_frontier(con)
    con.close()
    summary = summarize(rows)
    top = top_rows(rows)
    packet = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS,
        "source_bindings": {
            "candidate_base": binding(CANDIDATE_BASE),
            "candidate_manifest": binding(CANDIDATE_MANIFEST),
            "nagi_pivot_packet": binding(PIVOT_PACKET),
            "nagi_residual_matrix_packet": binding(RESIDUAL_MATRIX_PACKET),
            "nagi_approval_packet": binding(APPROVAL_PACKET),
            "builder": binding(BUILDER),
        },
        "method": {
            "mode": "local_public_no_order_exhaustive_maker_queue_frontier",
            "public_proxy_only": True,
            "private_truth_ready": False,
            "time_windows": TIME_WINDOWS,
            "price_bands": PRICE_BANDS,
            "sides": SIDES,
            "pair_caps": PAIR_CAPS,
            "queue_min_qtys": QUEUE_MIN_QTYS,
            "fee_boundary": "maker fee 0 versus crypto taker fee 0.07",
            "freshness": "strict_l1_age_ms<=500, strict_l2_age_ms<=500, L1/L2 align<=500, post-quote SELL touch",
            "queue_proxy": "public SELL touch at/through bid minus visible bid depth",
        },
        "summary": summary,
        "top_fee0_variants": top,
        "decision": {
            "frontier_mapped": True,
            "maker_fee0_edge_structure_exists": summary["fee0_scale_pass_count"] > 0,
            "taker_fee07_scale_edge_exists": summary["taker_fee07_scale_pass_count"] > 0,
            "private_truth_unblocked": False,
            "orders_authorized": False,
            "oos_discussion_allowed": False,
            "next_step": "compare frontier against NAGI public profile and private-maker-shadow constraints; do not execute",
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
    return packet, rows, top


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    packet, rows, top = build_packet()
    packet_path = OUT / "NAGI_MAKER_QUEUE_EXHAUSTIVE_FRONTIER_PACKET.json"
    all_csv = OUT / "nagi_maker_queue_exhaustive_frontier.csv"
    top_csv = OUT / "nagi_maker_queue_exhaustive_frontier_top.csv"
    report_path = OUT / "NAGI_MAKER_QUEUE_EXHAUSTIVE_FRONTIER_REPORT.md"
    preview = OUT / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    write_json(packet_path, packet)
    write_csv(all_csv, rows)
    write_csv(top_csv, top)
    report_path.write_text(render_report(packet), encoding="utf-8")
    write_preview(preview)
    write_sha256sums(OUT, [packet_path, all_csv, top_csv, report_path, preview])
    print(json.dumps({"packet": str(packet_path), "status": packet["status"], "summary": packet["summary"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
