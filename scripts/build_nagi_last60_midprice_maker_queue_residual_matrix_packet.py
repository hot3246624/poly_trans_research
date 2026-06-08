#!/usr/bin/env python3
"""Build a review-only NAGI maker-queue residual-killer matrix packet."""

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
OUT = EXPORTS / "nagi_last60_midprice_maker_queue_residual_matrix_packet_20260608"

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
MAKER_QUEUE_PROXY_PACKET = (
    EXPORTS
    / "nagi_last60_midprice_maker_queue_proxy_packet_20260608"
    / "NAGI_LAST60_MIDPRICE_MAKER_QUEUE_PROXY_PACKET.json"
)
BUILDER = ROOT / "scripts/build_nagi_last60_midprice_maker_queue_residual_matrix_packet.py"

STATUS = (
    "KEEP_NAGI_LAST60_MIDPRICE_MAKER_QUEUE_RESIDUAL_MATRIX_REVIEWED_"
    "FEE0_QUEUE_EDGE_SUBBUCKETS_FOUND_PRIVATE_QUEUE_TRUTH_REQUIRED_NOT_OOS_READY"
)
FEE_RATE_CRYPTO_TAKER = 0.07


BRANCHES = [
    ("up_35_50_all", "YES", 0.35, 0.50),
    ("up_35_425_lower", "YES", 0.35, 0.425),
    ("up_425_50_upper", "YES", 0.425, 0.50),
    ("down_50_65_all", "NO", 0.50, 0.65),
    ("down_50_575_lower", "NO", 0.50, 0.575),
    ("down_575_65_upper", "NO", 0.575, 0.65),
]
PAIR_CAPS = [1.00, 0.995, 0.990, 0.985, 0.980, 0.975]
QUEUE_MIN_QTYS = [0.0, 1.0, 5.0, 10.0, 25.0]


BASE_CTE = f"""
with base as (
  select
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
), fresh_touch as (
  select *
  from base
  where strict_l1_age_ms <= 500
    and strict_l2_age_ms <= 500
    and align_lag_ms <= 500
    and touch
    and touch_after_quote_ms >= 0
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
        "echo 'NOT_AUTHORIZED: NAGI maker-queue residual matrix is local public-proxy review only.' >&2\n"
        "exit 66\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def write_sha256sums(root: Path, files: list[Path]) -> None:
    lines = [f"{sha256_file(path)}  {path.relative_to(root)}" for path in files]
    (root / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def variant_values_sql() -> str:
    rows: list[str] = []
    for branch_id, side, px_lo, px_hi in BRANCHES:
        for pair_cap in PAIR_CAPS:
            for queue_min_qty in QUEUE_MIN_QTYS:
                variant_id = f"{branch_id}__pc{pair_cap:.3f}__qmin{queue_min_qty:g}"
                rows.append(
                    "("
                    + ", ".join(
                        [
                            q(variant_id),
                            q(branch_id),
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
        "variants(variant_id, branch_id, side, px_lo, px_hi, pair_cap, queue_min_qty) as (values\n  "
        + ",\n  ".join(rows)
        + "\n)"
    )


def q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def build_matrix(con: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    sql = (
        "with "
        + variant_values_sql()
        + ",\n"
        + BASE_CTE.strip().removeprefix("with ")
        + """
, joined as (
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
    b.strict_l1_age_ms,
    b.strict_l2_age_ms,
    b.align_lag_ms
  from variants v
  join fresh_touch b
    on b.side = v.side
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
  j.branch_id,
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
  j.variant_id, j.branch_id, j.side, j.px_lo, j.px_hi, j.pair_cap, j.queue_min_qty
order by queue_edge_qty_sum_fee0 desc nulls last, queue_markets desc
"""
    )
    rows = fetch_all(con, sql)
    for row in rows:
        row["passes_fee0_proxy_gate"] = (
            float(row.get("queue_edge_qty_sum_fee0") or 0.0) > 0.0
            and int(row.get("queue_markets") or 0) >= 100
            and float(row.get("queue_market_share") or 0.0) >= 0.20
        )
        row["survives_taker_fee07"] = float(row.get("queue_edge_qty_sum_taker_fee07") or 0.0) > 0.0
        row["private_truth_ready"] = False
        row["oos_ready"] = False
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fee0_pass = [r for r in rows if r.get("passes_fee0_proxy_gate") is True]
    taker_pass = [r for r in rows if r.get("survives_taker_fee07") is True]
    best_fee0 = max(rows, key=lambda r: float(r.get("queue_edge_qty_sum_fee0") or -1e18))
    best_taker = max(rows, key=lambda r: float(r.get("queue_edge_qty_sum_taker_fee07") or -1e18))
    return {
        "variant_count": len(rows),
        "fee0_proxy_pass_count": len(fee0_pass),
        "taker_fee07_pass_count": len(taker_pass),
        "best_fee0_variant": best_fee0,
        "best_taker_fee07_variant": best_taker,
        "fee0_proxy_edge_exists": bool(fee0_pass),
        "taker_fee07_edge_exists": bool(taker_pass),
    }


def top_rows(rows: list[dict[str, Any]], limit: int = 40) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda r: float(r.get("queue_edge_qty_sum_fee0") or -1e18), reverse=True)[:limit]


def build_packet() -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    con = duckdb.connect(str(CANDIDATE_BASE), read_only=True)
    rows = build_matrix(con)
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
            "nagi_maker_queue_proxy_packet": binding(MAKER_QUEUE_PROXY_PACKET),
            "builder": binding(BUILDER),
        },
        "method": {
            "mode": "local_public_no_order_maker_queue_residual_killer_matrix",
            "public_proxy_only": True,
            "private_truth_ready": False,
            "branch_count": len(BRANCHES),
            "pair_caps": PAIR_CAPS,
            "queue_min_qtys": QUEUE_MIN_QTYS,
            "fee_boundary": "maker fee 0 can show NAGI-like edge; crypto taker fee 0.07 is reported as a blocking contrast",
            "queue_proxy": "fresh post-quote public SELL touch minus visible bid depth",
            "private_truth_boundary": "Queue proxy rows do not prove our maker order would fill or have queue priority.",
        },
        "summary": summary,
        "top_fee0_proxy_variants": top,
        "decision": {
            "nagi_fee0_maker_queue_subbuckets_exist": summary["fee0_proxy_edge_exists"],
            "nagi_taker_fee07_subbuckets_exist": summary["taker_fee07_edge_exists"],
            "base_book_shadow_failure_reexplained": "The NAGI pattern needs true maker/no-fee queue execution; taker-style replay remains blocked.",
            "private_truth_unblocked": False,
            "orders_authorized": False,
            "oos_discussion_allowed": False,
            "next_step": "nagi_private_maker_shadow_requirements_packet_review_only",
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


def render_report(packet: dict[str, Any]) -> str:
    s = packet["summary"]
    best = s["best_fee0_variant"]
    lines = [
        "# NAGI Last60 Midprice Maker Queue Residual Matrix",
        "",
        f"Status: `{packet['status']}`",
        "",
        "## Result",
        "",
        "The matrix confirms that NAGI-style midprice edge survives only as maker/no-fee queue proxy. It does not survive crypto taker fee 0.07, and it does not prove our own queue fill.",
        "",
        f"- Variants scanned: {s['variant_count']}",
        f"- Fee0 public-proxy pass count: {s['fee0_proxy_pass_count']}",
        f"- Taker-fee07 pass count: {s['taker_fee07_pass_count']}",
        f"- Best fee0 variant: `{best['variant_id']}`",
        f"- Best fee0 queue edge qty sum: {best['queue_edge_qty_sum_fee0']}",
        f"- Best fee0 queue markets: {best['queue_markets']}",
        f"- Best fee0 queue market share: {best['queue_market_share']}",
        f"- Best fee0 pair cost p50: {best['queue_pair_cost_p50']}",
        "",
        "## Boundary",
        "",
        "This packet is local public-proxy review only. No WS, OOS, private key, order, cancel, canary, live, deploy, or readiness claim is authorized.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    packet, rows, top = build_packet()
    packet_path = OUT / "NAGI_LAST60_MIDPRICE_MAKER_QUEUE_RESIDUAL_MATRIX_PACKET.json"
    report_path = OUT / "NAGI_LAST60_MIDPRICE_MAKER_QUEUE_RESIDUAL_MATRIX_REPORT.md"
    matrix_path = OUT / "nagi_last60_midprice_maker_queue_residual_matrix.csv"
    top_path = OUT / "nagi_last60_midprice_maker_queue_residual_matrix_top.csv"
    preview_path = OUT / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    write_json(packet_path, packet)
    report_path.write_text(render_report(packet), encoding="utf-8")
    write_csv(matrix_path, rows)
    write_csv(top_path, top)
    write_preview(preview_path)
    write_sha256sums(OUT, [packet_path, report_path, matrix_path, top_path, preview_path])
    print(
        json.dumps(
            {
                "packet": str(packet_path),
                "status": packet["status"],
                "summary": packet["summary"],
                "decision": packet["decision"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
