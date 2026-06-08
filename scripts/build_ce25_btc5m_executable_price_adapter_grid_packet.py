#!/usr/bin/env python3
"""Run a bounded executable-price adapter grid for CE25 BTC5M local research."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
BT_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
EXPORTS = ROOT / "data" / "exports"
OUT = EXPORTS / "ce25_btc5m_executable_price_adapter_grid_packet_20260607"
CANDIDATE_BASE = (
    BT_ROOT
    / "derived/completion_candidate_pipeline_v1/local_20260502_20260518_paircap102/candidate_base.duckdb"
)
CANDIDATE_MANIFEST = CANDIDATE_BASE.parent / "CANDIDATE_BASE_MANIFEST.json"
EXEC_PRICE_RESEARCH_PACKET = (
    EXPORTS
    / "ce25_btc5m_state_machine_executable_price_research_packet_20260607"
    / "CE25_BTC5M_STATE_MACHINE_EXECUTABLE_PRICE_RESEARCH_PACKET.json"
)
BUILDER = ROOT / "scripts/build_ce25_btc5m_executable_price_adapter_grid_packet.py"
VALIDATOR = ROOT / "scripts/validate_ce25_btc5m_research_packet_chain.py"

FEE_RATE = 0.07
DUST = 1e-9
STATUS_BLOCKED = (
    "BLOCKED_CE25_BTC5M_EXECUTABLE_PRICE_ADAPTER_GRID_NO_POSITIVE_PRIORITY_VARIANT_"
    "RESEARCH_ONLY_NOT_OOS_READY"
)
STATUS_WATCH = (
    "KEEP_CE25_BTC5M_EXECUTABLE_PRICE_ADAPTER_GRID_POSITIVE_WATCH_VARIANT_REVIEW_REQUIRED_"
    "NOT_OOS_READY"
)
STATUS_LOW_QUALITY = (
    "BLOCKED_CE25_BTC5M_EXECUTABLE_PRICE_ADAPTER_GRID_POSITIVE_ONLY_LOW_PAIR_SHARE_HIGH_RESIDUAL_"
    "NOT_OOS_READY"
)


@dataclass
class Lot:
    qty: float
    px: float
    side: str
    ts_ms: int


@dataclass(frozen=True)
class Variant:
    variant_id: str
    cooldown_s: float
    min_trade_to_exec_edge: float | None
    exec_pair_cap: float
    target_qty: float = 5.0
    max_open_cost: float = 250.0
    max_seed_qty: float = 10.0
    fill_haircut: float = 0.25
    imbalance_qty_cap: float = 2.5
    imbalance_cost_cap: float = 1_000_000_000.0
    residual_cooldown_age_s: float = 30.0
    residual_cooldown_cost_cap: float = 0.5
    seed_px_lo: float = 0.05
    seed_px_hi: float = 0.90


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


def official_fee(qty: float, px: float) -> float:
    if qty <= DUST or not math.isfinite(px) or px < 0 or px > 1:
        return 0.0
    return qty * FEE_RATE * px * (1.0 - px)


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


def pair_inventory(inv: dict[str, deque[Lot]], metrics: defaultdict[str, float], ts_ms: int) -> None:
    yes = inv["YES"]
    no = inv["NO"]
    while yes and no:
        a = yes[0]
        b = no[0]
        take = min(a.qty, b.qty)
        if take <= DUST:
            break
        pair_cost = a.px + b.px
        metrics["pair_actions"] += 1
        metrics["pair_qty"] += take
        metrics["pair_cost_sum"] += take * pair_cost
        metrics["pair_pnl"] += take * (1.0 - pair_cost)
        metrics["pair_delay_ms"] += take * max(0, ts_ms - min(a.ts_ms, b.ts_ms))
        a.qty -= take
        b.qty -= take
        if a.qty <= DUST:
            yes.popleft()
        if b.qty <= DUST:
            no.popleft()


def load_rows() -> list[dict[str, Any]]:
    con = duckdb.connect(str(CANDIDATE_BASE), read_only=True)
    cur = con.execute(
        """
        select
          candidate_row_id,
          day,
          condition_id,
          slug,
          ts_ms,
          offset_s,
          side,
          winner_side,
          side_alignment,
          public_trade_price,
          public_trade_size,
          opp_ask,
          buy_full_10,
          buy_vwap_10,
          buy_filled_10,
          strict_l2_age_ms
        from candidate_base
        where asset='BTC'
          and event_kind='public_trade'
          and public_trade_taker_side='SELL'
          and side in ('YES','NO')
          and offset_s >= 0
          and offset_s < 300
        order by condition_id, ts_ms, candidate_row_id
        """
    )
    names = [item[0] for item in cur.description]
    rows = [dict(zip(names, row)) for row in cur.fetchall()]
    con.close()
    return rows


def run_variant(rows: list[dict[str, Any]], variant: Variant) -> dict[str, Any]:
    states: dict[str, dict[str, Any]] = {}
    metrics: defaultdict[str, float] = defaultdict(float)
    for row in rows:
        metrics["candidate_count"] += 1
        condition_id = str(row["condition_id"])
        side = str(row["side"] or "")
        if side not in ("YES", "NO"):
            continue
        ts_ms = int(row["ts_ms"] or 0)
        state = states.get(condition_id)
        if state is None:
            state = {
                "day": row["day"],
                "slug": row["slug"],
                "winner_side": row["winner_side"],
                "inv": {"YES": deque(), "NO": deque()},
                "last_seed_ts_ms": -(10**18),
                "last_ts_ms": ts_ms,
                "active": False,
            }
            states[condition_id] = state
        state["last_ts_ms"] = max(int(state["last_ts_ms"]), ts_ms)

        trade_px = float(row["public_trade_price"]) if row["public_trade_price"] is not None else math.nan
        trade_size = float(row["public_trade_size"] or 0.0)
        if trade_size <= DUST or not math.isfinite(trade_px):
            metrics["block_bad_public_trade"] += 1
            continue
        if not row["buy_full_10"] or row["buy_vwap_10"] is None:
            metrics["block_l2_not_full_10"] += 1
            continue
        exec_px = float(row["buy_vwap_10"])
        if not math.isfinite(exec_px):
            metrics["block_l2_not_full_10"] += 1
            continue
        if not (variant.seed_px_lo <= exec_px <= variant.seed_px_hi):
            metrics["block_exec_price_band"] += 1
            continue
        if variant.min_trade_to_exec_edge is not None and exec_px > trade_px - variant.min_trade_to_exec_edge + 1e-12:
            metrics["block_trade_to_exec_edge"] += 1
            continue
        opp_ask = float(row["opp_ask"]) if row["opp_ask"] is not None else math.nan
        if not math.isfinite(opp_ask) or exec_px + opp_ask > variant.exec_pair_cap + 1e-12:
            metrics["block_exec_pair_cap"] += 1
            continue
        if ts_ms - int(state["last_seed_ts_ms"]) < int(variant.cooldown_s * 1000):
            metrics["block_cooldown"] += 1
            continue

        inv = state["inv"]
        same_qty = lot_qty(inv[side])
        opp_qty = lot_qty(inv[other(side)])
        aged_cost = aged_lot_cost(inv["YES"], ts_ms, variant.residual_cooldown_age_s) + aged_lot_cost(
            inv["NO"], ts_ms, variant.residual_cooldown_age_s
        )
        if aged_cost > variant.residual_cooldown_cost_cap + 1e-12 and same_qty + 1e-9 >= opp_qty:
            metrics["block_residual_cooldown"] += 1
            continue
        if same_qty >= variant.target_qty - 1e-9:
            metrics["block_target"] += 1
            continue
        same_cost = lot_cost(inv[side])
        opp_cost = lot_cost(inv[other(side)])
        if max(0.0, same_cost - opp_cost) > variant.imbalance_cost_cap + 1e-12:
            metrics["block_imbalance_cost"] += 1
            continue
        open_cost = lot_cost(inv["YES"]) + lot_cost(inv["NO"])
        imbalance_room = variant.imbalance_qty_cap - max(0.0, same_qty - opp_qty)
        if imbalance_room <= 1e-9:
            metrics["block_imbalance_qty"] += 1
            continue
        qty = min(
            variant.max_seed_qty,
            float(row["buy_filled_10"] or 0.0),
            trade_size * variant.fill_haircut,
            variant.target_qty - same_qty,
            (variant.max_open_cost - open_cost) / max(exec_px, 1e-9),
            imbalance_room,
        )
        if qty <= 1e-9:
            metrics["block_zero_qty"] += 1
            continue

        inv[side].append(Lot(qty=qty, px=exec_px, side=side, ts_ms=ts_ms))
        state["last_seed_ts_ms"] = ts_ms
        state["active"] = True
        metrics["selected_actions"] += 1
        metrics["gross_buy_qty"] += qty
        metrics["gross_buy_cost"] += qty * exec_px
        metrics["official_taker_fee"] += official_fee(qty, exec_px)
        pair_inventory(inv, metrics, ts_ms)

    residual_lots = 0
    for state in states.values():
        if not state["active"]:
            continue
        inv = state["inv"]
        pair_inventory(inv, metrics, int(state["last_ts_ms"]))
        metrics["active_markets"] += 1
        winner = state["winner_side"]
        for side in ("YES", "NO"):
            for lot in inv[side]:
                if lot.qty <= DUST:
                    continue
                cost = lot.qty * lot.px
                payout = lot.qty if winner == side else 0.0
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
        "variant_id": variant.variant_id,
        "cooldown_s": variant.cooldown_s,
        "min_trade_to_exec_edge": variant.min_trade_to_exec_edge,
        "exec_pair_cap": variant.exec_pair_cap,
        "candidate_count": int(metrics["candidate_count"]),
        "selected_actions": int(metrics["selected_actions"]),
        "active_markets": int(metrics["active_markets"]),
        "gross_buy_qty": round(gross_buy_qty, 6),
        "gross_buy_cost": round(gross_buy_cost, 6),
        "official_taker_fee": round(metrics["official_taker_fee"], 6),
        "pair_qty": round(pair_qty, 6),
        "pair_actions": int(metrics["pair_actions"]),
        "pair_share_rate": round((2.0 * pair_qty / gross_buy_qty) if gross_buy_qty else 0.0, 6),
        "weighted_pair_cost": round((metrics["pair_cost_sum"] / pair_qty) if pair_qty else 0.0, 6),
        "pair_delay_wavg_s": round((metrics["pair_delay_ms"] / pair_qty / 1000.0) if pair_qty else 0.0, 6),
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
        "block_l2_not_full_10": int(metrics["block_l2_not_full_10"]),
        "block_exec_price_band": int(metrics["block_exec_price_band"]),
        "block_trade_to_exec_edge": int(metrics["block_trade_to_exec_edge"]),
        "block_exec_pair_cap": int(metrics["block_exec_pair_cap"]),
        "block_cooldown": int(metrics["block_cooldown"]),
        "block_residual_cooldown": int(metrics["block_residual_cooldown"]),
        "block_target": int(metrics["block_target"]),
        "block_imbalance_qty": int(metrics["block_imbalance_qty"]),
    }


def variants() -> list[Variant]:
    out: list[Variant] = []
    for cooldown_s in (5.0, 0.0):
        cd = f"cd{int(cooldown_s):02d}"
        for edge in (None, 0.0, 0.01, 0.02, 0.05, 0.055):
            edge_label = "noedge" if edge is None else f"edge{int(round(edge * 1000)):03d}"
            out.append(
                Variant(
                    variant_id=f"exec_vwap10_{edge_label}_pc102_{cd}",
                    cooldown_s=cooldown_s,
                    min_trade_to_exec_edge=edge,
                    exec_pair_cap=1.02,
                )
            )
    return out


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_preview(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo 'NOT_AUTHORIZED: CE25 BTC5M executable-price adapter grid packet is review-only' >&2\n"
        "exit 66\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def render_report(packet: dict[str, Any]) -> str:
    best = packet["best_by_net_pnl"]
    lines = [
        "# CE25 BTC5M Executable-Price Adapter Grid",
        "",
        f"Status: `{packet['status']}`",
        "",
        "## Summary",
        "",
        "This bounded grid uses candidate-base prejoined L2 buy_vwap_10 as `execution_px` before selection and uses that same price for fee, inventory lot cost, pair-cost, residual cost, and PnL.",
        "",
        f"- Variants: {packet['grid']['variant_count']}",
        f"- Candidate rows scanned: {packet['grid']['candidate_rows']}",
        f"- Best variant: `{best['variant_id']}`",
        f"- Best net PnL: {best['net_pnl']:.6f}",
        f"- Best ROI: {best['net_roi']:.6f}",
        f"- Best selected actions: {best['selected_actions']}",
        f"- Best active markets: {best['active_markets']}",
        f"- Best weighted pair cost: {best['weighted_pair_cost']:.6f}",
        "",
        "## Interpretation",
        "",
        packet["decision"]["interpretation"],
        "",
        "This is local Backtest V1 research only, not OOS-ready and not live/promotable.",
    ]
    return "\n".join(lines) + "\n"


def write_sha256sums(root: Path, files: list[Path]) -> None:
    lines = [f"{sha256_file(path)}  {path.relative_to(root)}" for path in files]
    (root / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    rows = load_rows()
    grid = [run_variant(rows, variant) for variant in variants()]
    grid.sort(key=lambda row: (row["net_pnl"], row["net_roi"], row["selected_actions"]), reverse=True)
    best = grid[0]
    positive = [row for row in grid if row["net_pnl"] > 0 and row["selected_actions"] > 0]
    quality_positive = [
        row
        for row in positive
        if row["pair_share_rate"] >= 0.60
        and row["residual_cost_rate"] <= 0.20
        and row["active_markets"] >= 1000
    ]
    if quality_positive:
        status = STATUS_WATCH
        interpretation = (
            "At least one bounded executable-price adapter variant is positive and passes preliminary pair-share, "
            "residual, and coverage gates; it requires full artifact generation, wider grid review, and L2 provenance "
            "checks before any OOS discussion."
        )
    elif positive:
        status = STATUS_LOW_QUALITY
        interpretation = (
            "The bounded executable-price adapter grid found positive fee-inclusive PnL only in low-coverage, "
            "high-residual variants. This does not rescue the CE25 BTC5M high-participation backbone; it points to "
            "deeper signal redesign or a new strategy family."
        )
    else:
        status = STATUS_BLOCKED
        interpretation = (
            "No bounded priority variant recovered positive fee-inclusive PnL after aligning selection and PnL to "
            "executable L2 prices; this blocks the current CE25 BTC5M state-machine family pending deeper signal "
            "redesign."
        )
    packet = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": status,
        "source_bindings": {
            "candidate_base": binding(CANDIDATE_BASE),
            "candidate_base_manifest": binding(CANDIDATE_MANIFEST),
            "executable_price_research_packet": binding(EXEC_PRICE_RESEARCH_PACKET),
            "builder": binding(BUILDER),
            "validator": binding(VALIDATOR),
        },
        "method": {
            "mode": "bounded_priority_grid_candidate_base_prejoined_l2_execution_px",
            "execution_px": "buy_vwap_10",
            "execution_px_required_before_selection": True,
            "selection_and_pnl_price_source_match": True,
            "official_fee_formula": "fee = shares * 0.07 * price * (1 - price)",
            "candidate_filter": "BTC public_trade SELL, offset_s in [0,300), side YES/NO",
            "variant_axes": ["cooldown_s in {5,0}", "min_trade_to_exec_edge in {none,0,1c,2c,5c,5.5c}", "exec_pair_cap=1.02"],
            "not_full_matrix": True,
        },
        "grid": {
            "variant_count": len(grid),
            "candidate_rows": len(rows),
            "elapsed_s": round(time.perf_counter() - started, 3),
            "positive_variant_count": len(positive),
            "quality_positive_variant_count": len(quality_positive),
            "quality_gates": {
                "pair_share_rate_min": 0.60,
                "residual_cost_rate_max": 0.20,
                "active_markets_min": 1000,
            },
        },
        "best_by_net_pnl": best,
        "top_variants": grid[:10],
        "decision": {
            "positive_watch_variant_exists": bool(positive),
            "quality_positive_watch_variant_exists": bool(quality_positive),
            "current_seed_px_family_unblocked": False,
            "oos_discussion_allowed": False,
            "primary_blocker": None
            if quality_positive
            else (
                "positive_variants_are_low_pair_share_high_residual"
                if positive
                else "bounded_executable_price_priority_grid_failed_to_recover_positive_fee_inclusive_pnl"
            ),
            "next_step": "full_artifact_for_quality_positive_executable_price_watch_variant"
            if quality_positive
            else "deeper_signal_redesign_or_new_strategy_family_required",
            "interpretation": interpretation,
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
    packet_path = OUT / "CE25_BTC5M_EXECUTABLE_PRICE_ADAPTER_GRID_PACKET.json"
    report_path = OUT / "CE25_BTC5M_EXECUTABLE_PRICE_ADAPTER_GRID_REPORT.md"
    csv_path = OUT / "ce25_btc5m_executable_price_adapter_grid_summary.csv"
    preview_path = OUT / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    write_json(packet_path, packet)
    report_path.write_text(render_report(packet), encoding="utf-8")
    write_csv(csv_path, grid)
    write_preview(preview_path)
    write_sha256sums(OUT, [packet_path, report_path, csv_path, preview_path])
    print(json.dumps({"packet": str(packet_path), "status": status, "best": best}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
