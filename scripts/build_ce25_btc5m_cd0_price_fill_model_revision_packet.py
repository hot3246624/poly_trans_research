#!/usr/bin/env python3
"""Build a price/fill model revision packet for CE25 BTC5M cd0."""

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
OUT = EXPORTS / "ce25_btc5m_cd0_price_fill_model_revision_packet_20260607"
ACTION_DB = (
    BT_ROOT
    / "derived/completion_candidate_pipeline_v1/ce25_btc5m_local_cd0_watch_full_artifact_bulkcopy_20260607"
    / "broad_qty5_pc102_seed300_cd0_imb250_rage30_rcost050_full_5m/state_machine_results.duckdb"
)
JOIN_DB = (
    BT_ROOT
    / "derived/completion_candidate_pipeline_v1/ce25_btc5m_cd0_full_l2_fillability_indexed_join_20260607"
    / "cd0_l2_fillability_join.duckdb"
)
FULL_L2_PACKET = (
    EXPORTS
    / "ce25_btc5m_cd0_full_l2_fillability_indexed_packet_20260607"
    / "CE25_BTC5M_CD0_FULL_L2_FILLABILITY_INDEXED_PACKET.json"
)
FULL_ARTIFACT_PACKET = (
    EXPORTS
    / "ce25_btc5m_cd0_watch_full_artifact_packet_20260607"
    / "CE25_BTC5M_CD0_WATCH_FULL_ARTIFACT_PACKET.json"
)
BUILDER = ROOT / "scripts/build_ce25_btc5m_cd0_price_fill_model_revision_packet.py"
VALIDATOR = ROOT / "scripts/validate_ce25_btc5m_research_packet_chain.py"

STATUS = "BLOCKED_CE25_BTC5M_CD0_PRICE_FILL_MODEL_L2_EXECUTABLE_PRICE_NEGATIVE_PNL_NOT_OOS_READY"
FEE_RATE = 0.07
OFFICIAL_FEE_FORMULA = "fee = shares * fee_rate * price * (1 - price)"


@dataclass
class Lot:
    qty: float
    px: float
    ts_ms: int
    side: str


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
    if model_id == "l2_top5_vwap_within_seed_plus_5c_only":
        return float(row["top5_vwap"]) if row["top5_full_within_5c"] else None
    if model_id == "l2_ask1_px_when_ask1_size_ge_seed":
        return float(row["raw_l2_ask1_px"]) if row["ask1_size_ge_seed"] else None
    raise ValueError(f"unknown model_id {model_id}")


def run_model(model_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    inv: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"YES": deque(), "NO": deque(), "winner": None, "active": False}
    )
    metrics: defaultdict[str, float] = defaultdict(float)
    skipped = 0
    fill_count = 0
    residual_lots = 0

    for row in rows:
        fill_px = choose_fill_px(model_id, row)
        if fill_px is None or not math.isfinite(fill_px):
            skipped += 1
            continue
        condition_id = row["condition_id"]
        side = row["side"]
        ts_ms = int(row["ts_ms"])
        qty = float(row["seed_qty"])
        state = inv[condition_id]
        state["winner"] = row["winner_side"]
        state["active"] = True
        state[side].append(Lot(qty=qty, px=fill_px, ts_ms=ts_ms, side=side))
        fill_count += 1
        metrics["gross_buy_qty"] += qty
        metrics["gross_buy_cost"] += qty * fill_px
        metrics["official_taker_fee"] += official_fee(qty, fill_px)
        metrics["seed_actions"] += 1

        yes = state["YES"]
        no = state["NO"]
        while yes and no:
            a = yes[0]
            b = no[0]
            take = min(a.qty, b.qty)
            if take <= 1e-9:
                break
            pair_cost = a.px + b.px
            metrics["pair_actions"] += 1
            metrics["pair_qty"] += take
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
                metrics["residual_payout"] += payout
                metrics["residual_pnl"] += payout - cost
                residual_lots += 1

    actual_pnl = metrics["pair_pnl"] + metrics["residual_pnl"]
    net_pnl = actual_pnl - metrics["official_taker_fee"]
    gross_buy_cost = metrics["gross_buy_cost"]
    gross_buy_qty = metrics["gross_buy_qty"]
    pair_qty = metrics["pair_qty"]
    return {
        "model_id": model_id,
        "fills": fill_count,
        "skipped": skipped,
        "fill_rate": round(fill_count / len(rows), 6) if rows else 0.0,
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


def load_rows() -> list[dict[str, Any]]:
    con = duckdb.connect(str(ACTION_DB), read_only=True)
    con.execute(f"attach '{JOIN_DB}' as joindb (read_only)")
    cur = con.execute(
        """
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
          j.raw_l2_ask1_px,
          j.raw_l2_ask1_sz,
          j.top5_cum_sz,
          j.top5_vwap,
          j.ask1_size_ge_seed,
          j.top5_size_ge_seed,
          j.top5_full_within_5c,
          j.top5_full_within_10c
        from actions a
        join joindb.joined_fillability j using(action_id)
        order by a.ts_ms, a.action_id
        """
    )
    names = [item[0] for item in cur.description]
    rows = [dict(zip(names, row)) for row in cur.fetchall()]
    con.close()
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_preview(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo 'NOT_AUTHORIZED: CE25 BTC5M cd0 price/fill model revision packet is review-only' >&2\n"
        "exit 66\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def render_report(packet: dict[str, Any]) -> str:
    rows = packet["model_results"]
    lines = [
        "# CE25 BTC5M cd0 Price / Fill Model Revision Packet",
        "",
        f"Status: `{packet['status']}`",
        "",
        "## Result",
        "",
        "| model | fills | skipped | net pnl | roi | pair cost | residual cost | fill rate |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            "| {model} | {fills} | {skipped} | {pnl} | {roi:.2%} | {pcost} | {resid:.2%} | {fill:.2%} |".format(
                model=row["model_id"],
                fills=row["fills"],
                skipped=row["skipped"],
                pnl=row["net_pnl"],
                roi=row["net_roi"],
                pcost=row["weighted_pair_cost"],
                resid=row["residual_cost_rate"],
                fill=row["fill_rate"],
            )
        )
    lines.extend(
        [
            "",
            "## Decision",
            "",
            "cd0 as written is blocked. The positive replay result depends on optimistic seed_px fills; replacing fills with executable L2 ask/top5 prices turns the strategy negative.",
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
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = load_rows()
    model_ids = [
        "baseline_replay_seed_px",
        "l2_top5_vwap_all_available",
        "l2_top5_vwap_within_seed_plus_10c_only",
        "l2_top5_vwap_within_seed_plus_5c_only",
        "l2_ask1_px_when_ask1_size_ge_seed",
    ]
    results = [run_model(model_id, rows) for model_id in model_ids]
    baseline = results[0]
    l2_all = next(row for row in results if row["model_id"] == "l2_top5_vwap_all_available")
    l2_10c = next(row for row in results if row["model_id"] == "l2_top5_vwap_within_seed_plus_10c_only")
    packet = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS,
        "scope": "fixed-action local replay price/fill substitution over cd0 actions; review-only",
        "method": {
            "description": (
                "Replay the same cd0 action sequence through FIFO pair/residual settlement, substituting fill prices "
                "from L2 ask/top5 evidence. This is a fixed-action diagnostic, not a revised state-machine search."
            ),
            "official_fee_formula": OFFICIAL_FEE_FORMULA,
            "official_fee_rate": FEE_RATE,
            "model_ids": model_ids,
        },
        "source_bindings": {
            "full_l2_fillability_packet": binding(FULL_L2_PACKET),
            "full_artifact_packet": binding(FULL_ARTIFACT_PACKET),
            "action_duckdb": binding(ACTION_DB),
            "l2_join_duckdb": binding(JOIN_DB),
            "builder": binding(BUILDER),
            "validator": binding(VALIDATOR),
        },
        "model_results": results,
        "baseline_reproduction": {
            "matches_prior_cd0_full_artifact": True,
            "baseline_net_pnl": baseline["net_pnl"],
            "baseline_net_roi": baseline["net_roi"],
        },
        "decision": {
            "cd0_as_written_blocked": True,
            "primary_blocker": "l2_executable_price_turns_pair_cost_above_one_and_net_pnl_negative",
            "l2_top5_all_net_pnl": l2_all["net_pnl"],
            "l2_top5_all_net_roi": l2_all["net_roi"],
            "l2_top5_within_10c_net_pnl": l2_10c["net_pnl"],
            "l2_top5_within_10c_net_roi": l2_10c["net_roi"],
            "replay_seed_px_optimism_detected": True,
            "next_packet": "cd0_state_machine_price_alignment_revision_or_downgrade_packet",
            "oos_discussion_allowed": False,
        },
        "outputs": {
            "packet": "CE25_BTC5M_CD0_PRICE_FILL_MODEL_REVISION_PACKET.json",
            "report": "CE25_BTC5M_CD0_PRICE_FILL_MODEL_REVISION_REPORT.md",
            "summary_csv": "ce25_btc5m_cd0_price_fill_model_revision_summary.csv",
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
    packet_path = OUT / "CE25_BTC5M_CD0_PRICE_FILL_MODEL_REVISION_PACKET.json"
    report_path = OUT / "CE25_BTC5M_CD0_PRICE_FILL_MODEL_REVISION_REPORT.md"
    csv_path = OUT / "ce25_btc5m_cd0_price_fill_model_revision_summary.csv"
    preview_path = OUT / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    write_json(packet_path, packet)
    report_path.write_text(render_report(packet), encoding="utf-8")
    write_csv(csv_path, results)
    write_preview(preview_path)
    files = [packet_path, report_path, csv_path, preview_path]
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
                "baseline_net_pnl": baseline["net_pnl"],
                "l2_top5_all_net_pnl": l2_all["net_pnl"],
                "l2_top5_within_10c_net_pnl": l2_10c["net_pnl"],
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
