#!/usr/bin/env python3
"""Build a price/fill comparison packet for the CE25 BTC5M broad cd5 baseline."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
BT_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
EXPORTS = ROOT / "data" / "exports"
OUT = EXPORTS / "ce25_btc5m_broad_cd5_price_fill_model_comparison_packet_20260607"
ACTION_DB = (
    BT_ROOT
    / "derived/completion_candidate_pipeline_v1/ce25_btc5m_local_residual_rule_replay_20260607"
    / "broad_qty5_pc102_seed300_cd5_imb250_rage30_rcost050_full_5m/state_machine_results.duckdb"
)
L2_MART = BT_ROOT / "derived/contract_examples/l2_top_aligned_mart_20260502_20260518_l2/l2_top_aligned_mart.duckdb"
CD0_PRICE_PACKET = (
    EXPORTS
    / "ce25_btc5m_cd0_price_fill_model_revision_packet_20260607"
    / "CE25_BTC5M_CD0_PRICE_FILL_MODEL_REVISION_PACKET.json"
)
BUILDER = ROOT / "scripts/build_ce25_btc5m_broad_cd5_price_fill_model_comparison_packet.py"
VALIDATOR = ROOT / "scripts/validate_ce25_btc5m_research_packet_chain.py"

STATUS = "BLOCKED_CE25_BTC5M_BROAD_CD5_PRICE_FILL_MODEL_L2_EXECUTABLE_PRICE_NEGATIVE_PNL_NOT_OOS_READY"
FEE_RATE = 0.07
OFFICIAL_FEE_FORMULA = "fee = shares * fee_rate * price * (1 - price)"


@dataclass
class Lot:
    qty: float
    px: float
    side: str


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


def quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def official_fee(qty: float, px: float) -> float:
    if qty <= 0 or not math.isfinite(px) or px < 0 or px > 1:
        return 0.0
    return qty * FEE_RATE * px * (1.0 - px)


def choose_fill_px(model_id: str, row: dict[str, Any]) -> float | None:
    seed_px = float(row["seed_px"])
    if model_id == "baseline_replay_seed_px":
        return seed_px
    if model_id == "l2_top5_vwap_all_available":
        return float(row["top5_vwap"]) if row["top5_size_ge_seed"] else None
    if model_id == "l2_top5_vwap_within_seed_plus_10c_only":
        return float(row["top5_vwap"]) if row["top5_full_within_10c"] else None
    if model_id == "l2_ask1_px_when_ask1_size_ge_seed":
        return float(row["raw_l2_ask1_px"]) if row["ask1_size_ge_seed"] else None
    raise ValueError(f"unknown model_id {model_id}")


def load_rows() -> list[dict[str, Any]]:
    con = duckdb.connect(":memory:")
    con.execute(f"attach {quote(ACTION_DB)} as actiondb (read_only)")
    con.execute(f"attach {quote(L2_MART)} as l2db (read_only)")
    cur = con.execute(
        """
        with joined as (
          select
            a.action_id,
            a.condition_id,
            a.slug,
            a.day,
            a.side,
            a.winner_side,
            a.ts_ms,
            a.seed_qty,
            a.seed_px,
            m.source_ts_ms,
            m.raw_l2_age_ms,
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
          from actiondb.actions a
          asof left join l2db.md_book_l2_top_aligned m
            on a.condition_id=m.condition_id
           and a.side=m.market_side
           and a.ts_ms >= m.source_ts_ms
          where m.asset='BTC'
        ), calc as (
          select
            *,
            coalesce(raw_l2_ask1_sz,0) as ask1_cum_sz,
            coalesce(raw_l2_ask1_sz,0)+coalesce(raw_l2_ask2_sz,0)+coalesce(raw_l2_ask3_sz,0)+
              coalesce(raw_l2_ask4_sz,0)+coalesce(raw_l2_ask5_sz,0) as top5_cum_sz,
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
          *,
          raw_l2_ask1_sz >= seed_qty as ask1_size_ge_seed,
          top5_cum_sz >= seed_qty as top5_size_ge_seed,
          top5_cum_sz >= seed_qty and top5_vwap <= seed_px + 0.10 as top5_full_within_10c
        from calc
        order by ts_ms, action_id
        """
    )
    names = [item[0] for item in cur.description]
    rows = [dict(zip(names, row)) for row in cur.fetchall()]
    con.close()
    return rows


def run_model(model_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    inv: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"YES": deque(), "NO": deque(), "winner": None, "active": False}
    )
    metrics: defaultdict[str, float] = defaultdict(float)
    fills = 0
    skipped = 0
    residual_lots = 0

    for row in rows:
        px = choose_fill_px(model_id, row)
        if px is None or not math.isfinite(px):
            skipped += 1
            continue
        condition_id = row["condition_id"]
        side = row["side"]
        qty = float(row["seed_qty"])
        state = inv[condition_id]
        state["winner"] = row["winner_side"]
        state["active"] = True
        state[side].append(Lot(qty=qty, px=px, side=side))
        fills += 1
        metrics["gross_buy_qty"] += qty
        metrics["gross_buy_cost"] += qty * px
        metrics["official_taker_fee"] += official_fee(qty, px)

        yes = state["YES"]
        no = state["NO"]
        while yes and no:
            a = yes[0]
            b = no[0]
            take = min(a.qty, b.qty)
            if take <= 1e-9:
                break
            pair_cost = a.px + b.px
            metrics["pair_qty"] += take
            metrics["pair_actions"] += 1
            metrics["pair_cost_sum"] += take * pair_cost
            metrics["pair_pnl"] += take * (1.0 - pair_cost)
            a.qty -= take
            b.qty -= take
            if a.qty <= 1e-9:
                yes.popleft()
            if b.qty <= 1e-9:
                no.popleft()

    for state in inv.values():
        if not state["active"]:
            continue
        metrics["active_markets"] += 1
        winner = state["winner"]
        for side in ("YES", "NO"):
            for lot in state[side]:
                if lot.qty <= 1e-9:
                    continue
                cost = lot.qty * lot.px
                payout = lot.qty if side == winner else 0.0
                metrics["residual_qty"] += lot.qty
                metrics["residual_cost"] += cost
                metrics["residual_pnl"] += payout - cost
                residual_lots += 1

    actual_pnl = metrics["pair_pnl"] + metrics["residual_pnl"]
    net_pnl = actual_pnl - metrics["official_taker_fee"]
    gross_buy_cost = metrics["gross_buy_cost"]
    gross_buy_qty = metrics["gross_buy_qty"]
    pair_qty = metrics["pair_qty"]
    return {
        "model_id": model_id,
        "fills": fills,
        "skipped": skipped,
        "fill_rate": round(fills / len(rows), 6) if rows else 0.0,
        "active_markets": int(metrics["active_markets"]),
        "gross_buy_qty": round(gross_buy_qty, 6),
        "gross_buy_cost": round(gross_buy_cost, 6),
        "official_taker_fee": round(metrics["official_taker_fee"], 6),
        "pair_qty": round(pair_qty, 6),
        "pair_actions": int(metrics["pair_actions"]),
        "pair_share_rate": round((2.0 * pair_qty / gross_buy_qty) if gross_buy_qty else 0.0, 6),
        "weighted_pair_cost": round((metrics["pair_cost_sum"] / pair_qty) if pair_qty else 0.0, 6),
        "pair_pnl": round(metrics["pair_pnl"], 6),
        "residual_qty": round(metrics["residual_qty"], 6),
        "residual_cost": round(metrics["residual_cost"], 6),
        "residual_qty_rate": round((metrics["residual_qty"] / gross_buy_qty) if gross_buy_qty else 0.0, 6),
        "residual_cost_rate": round((metrics["residual_cost"] / gross_buy_cost) if gross_buy_cost else 0.0, 6),
        "residual_pnl": round(metrics["residual_pnl"], 6),
        "residual_lots": residual_lots,
        "actual_settle_pnl": round(actual_pnl, 6),
        "net_pnl": round(net_pnl, 6),
        "net_roi": round((net_pnl / gross_buy_cost) if gross_buy_cost else 0.0, 6),
        "fee_model": "official_taker",
        "official_fee_rate": FEE_RATE,
        "official_fee_formula": OFFICIAL_FEE_FORMULA,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_preview(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo 'NOT_AUTHORIZED: CE25 BTC5M broad cd5 price/fill comparison packet is review-only' >&2\n"
        "exit 66\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def render_report(packet: dict[str, Any]) -> str:
    lines = [
        "# CE25 BTC5M Broad cd5 Price/Fill Model Comparison",
        "",
        f"Status: `{packet['status']}`",
        "",
        "## Result",
        "",
        "The lower-intensity broad cd5 baseline also fails the executable L2 price substitution check. The seed-price replay is positive, but ask/top5 executable prices push weighted pair cost above 1 and make fee-inclusive net PnL negative.",
        "",
        "| model | fills | fill_rate | gross_buy_cost | fee | weighted_pair_cost | residual_cost_rate | net_pnl | net_roi |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in packet["model_results"]:
        lines.append(
            f"| {row['model_id']} | {row['fills']} | {row['fill_rate']:.4f} | {row['gross_buy_cost']:.2f} | "
            f"{row['official_taker_fee']:.2f} | {row['weighted_pair_cost']:.6f} | "
            f"{row['residual_cost_rate']:.4f} | {row['net_pnl']:.2f} | {row['net_roi']:.4f} |"
        )
    lines.extend(
        [
            "",
            "## Decision",
            "",
            "- Do not treat broad cd5 as a safe downgrade candidate until the state machine is re-searched with executable ask/top5 prices in both selection and PnL.",
            "- The current blocker is price alignment, not L2 size coverage or simulated capital reuse.",
            "- This packet is local Backtest V1 research only; it is not OOS-ready and authorizes no live or private action.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_sha256sums(root: Path, files: list[Path]) -> None:
    lines = [f"{sha256_file(path)}  {path.relative_to(root)}" for path in files]
    (root / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = load_rows()
    model_ids = [
        "baseline_replay_seed_px",
        "l2_top5_vwap_all_available",
        "l2_top5_vwap_within_seed_plus_10c_only",
        "l2_ask1_px_when_ask1_size_ge_seed",
    ]
    results = [run_model(model_id, rows) for model_id in model_ids]
    by_id = {row["model_id"]: row for row in results}
    packet = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS,
        "scope": {
            "strategy_family": "CE25_BTC5M_BROAD_PARTICIPATION_BACKBONE",
            "run_id": "broad_seed300_qty5_pc102_cd5_imb250",
            "source_window": "2026-05-02..2026-05-18",
            "action_rows": len(rows),
            "local_backtest_v1_research_only": True,
        },
        "source_bindings": {
            "action_db": binding(ACTION_DB),
            "l2_top_aligned_mart": binding(L2_MART),
            "cd0_price_fill_model_revision_packet": binding(CD0_PRICE_PACKET),
            "builder": binding(BUILDER),
            "validator": binding(VALIDATOR),
        },
        "method": {
            "official_fee_rate": FEE_RATE,
            "official_fee_formula": OFFICIAL_FEE_FORMULA,
            "settlement": "same fixed action sequence; FIFO YES/NO pair settlement; residual pays only if side equals winner_side",
            "fill_models": model_ids,
        },
        "model_results": results,
        "decision": {
            "broad_cd5_as_safe_downgrade_blocked": True,
            "seed_px_replay_positive": by_id["baseline_replay_seed_px"]["net_pnl"] > 0,
            "l2_executable_models_negative": all(
                by_id[model_id]["net_pnl"] < 0
                for model_id in (
                    "l2_top5_vwap_all_available",
                    "l2_top5_vwap_within_seed_plus_10c_only",
                    "l2_ask1_px_when_ask1_size_ge_seed",
                )
            ),
            "primary_blocker": "state_machine_selection_and_pnl_are_not_aligned_to_executable_l2_prices",
            "next_packet": "ce25_btc5m_state_machine_executable_price_research_packet",
            "oos_discussion_allowed": False,
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
    packet_path = OUT / "CE25_BTC5M_BROAD_CD5_PRICE_FILL_MODEL_COMPARISON_PACKET.json"
    report_path = OUT / "CE25_BTC5M_BROAD_CD5_PRICE_FILL_MODEL_COMPARISON_REPORT.md"
    summary_path = OUT / "ce25_btc5m_broad_cd5_price_fill_model_comparison_summary.csv"
    preview_path = OUT / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    write_json(packet_path, packet)
    report_path.write_text(render_report(packet), encoding="utf-8")
    write_csv(summary_path, results)
    write_preview(preview_path)
    write_sha256sums(OUT, [packet_path, report_path, summary_path, preview_path])
    print(json.dumps({"packet": str(packet_path), "status": STATUS, "model_results": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
