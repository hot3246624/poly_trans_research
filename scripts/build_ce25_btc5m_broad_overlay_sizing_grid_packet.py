#!/usr/bin/env python3
"""Grid-search CE25 BTC5m broad+overlay sizing variants.

This is still public-profile proxy research. It searches size schedules for a
high-participation backbone plus overlays, then records which variants should
be tested first once matching replay source is available.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
EXPORTS = ROOT / "data" / "exports"
LEDGER_CSV = (
    EXPORTS
    / "ce25_btc5m_broad_profile_candidate_ledger_20260604"
    / "ce25_btc5m_broad_profile_candidate_ledger.csv"
)
CONTROLLER_V1_PACKET = (
    EXPORTS
    / "ce25_btc5m_broad_overlay_controller_v1_packet_20260606"
    / "CE25_BTC5M_BROAD_OVERLAY_CONTROLLER_V1_PACKET.json"
)
FRONTIER_PACKET = (
    EXPORTS
    / "ce25_btc5m_fee_roi_participation_frontier_20260606"
    / "CE25_BTC5M_FEE_ROI_PARTICIPATION_FRONTIER_SUMMARY.json"
)
MATCHING_SOURCE_PACKET = (
    EXPORTS
    / "ce25_btc5m_matching_source_build_packet_20260605"
    / "CE25_BTC5M_MATCHING_SOURCE_BUILD_PACKET.json"
)
OUTPUT_DIR = EXPORTS / "ce25_btc5m_broad_overlay_sizing_grid_packet_20260606"

STATUS = "KEEP_CE25_BTC5M_BROAD_OVERLAY_SIZING_GRID_REVIEW_ONLY_MATCHING_SOURCE_REQUIRED_NOT_OOS_READY"
EXPECTED_LATEST_ROUNDS = 288
INITIAL_BANKROLL = 300.0
OFFICIAL_FEE_RATE = 0.07


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def fnum(row: dict[str, str], key: str) -> float:
    try:
        value = row.get(key)
        return float(value) if value not in ("", None) else 0.0
    except ValueError:
        return 0.0


def latest_label(rows: list[dict[str, str]]) -> str:
    return sorted({row["source_profile_label"] for row in rows})[-1]


def is_last60(row: dict[str, str]) -> bool:
    return row.get("source_last_delta_bucket") == "last_60s"


def is_20_35(row: dict[str, str]) -> bool:
    return row.get("source_first_price_bucket") == "20-35"


def is_65_80(row: dict[str, str]) -> bool:
    return row.get("source_first_price_bucket") == "65-80"


def is_down(row: dict[str, str]) -> bool:
    return row.get("source_first_side") == "DOWN"


def is_mid_up(row: dict[str, str]) -> bool:
    return row.get("source_first_price_bucket") in {"35-50", "50-65"} and row.get("source_first_side") == "UP"


@dataclass(frozen=True)
class Schedule:
    base: float
    last60_boost: float
    low_boost: float
    high_boost: float
    down_boost: float
    mid_up_boost: float
    cap: float

    @property
    def schedule_id(self) -> str:
        return (
            f"base{self.base:g}_l60{self.last60_boost:g}_low{self.low_boost:g}_"
            f"hi{self.high_boost:g}_down{self.down_boost:g}_midup{self.mid_up_boost:g}_cap{self.cap:g}"
        ).replace(".", "p")

    def cap_for(self, row: dict[str, str]) -> float:
        value = self.base
        if is_last60(row):
            value += self.last60_boost
        if is_20_35(row):
            value += self.low_boost
        if is_65_80(row):
            value += self.high_boost
        if is_down(row):
            value += self.down_boost
        if is_mid_up(row):
            value += self.mid_up_boost
        return min(self.cap, max(0.0, value))


def schedules() -> list[Schedule]:
    out: list[Schedule] = []
    for base in [2.0, 5.0, 8.0, 10.0, 12.0]:
        for last60 in [0.0, 5.0, 10.0, 15.0, 20.0]:
            for low in [0.0, 5.0, 10.0, 15.0]:
                for high in [0.0, 5.0, 10.0]:
                    for down in [0.0, 3.0, 5.0]:
                        for mid_up in [0.0, 3.0, 5.0]:
                            for cap in [15.0, 20.0, 30.0]:
                                if base > cap:
                                    continue
                                out.append(Schedule(base, last60, low, high, down, mid_up, cap))
    return out


def peak_capital(rows: list[dict[str, str]], buy_by_candidate: dict[str, float]) -> float:
    events: list[tuple[int, float]] = []
    for row in rows:
        buy = buy_by_candidate.get(row["candidate_id"], 0.0)
        if buy <= 0:
            continue
        start = int(fnum(row, "source_first_trade_s") or fnum(row, "market_start_s"))
        end = int(fnum(row, "market_end_s"))
        events.append((start, buy))
        events.append((end, -buy))
    current = 0.0
    peak = 0.0
    for _, delta in sorted(events, key=lambda item: (item[0], item[1])):
        current += delta
        peak = max(peak, current)
    return peak


def weighted(rows: list[dict[str, str]], key: str, weights: dict[str, float]) -> float | None:
    total = sum(weights.get(row["candidate_id"], 0.0) for row in rows)
    if total <= 0:
        return None
    return sum(fnum(row, key) * weights.get(row["candidate_id"], 0.0) for row in rows) / total


def summarize(rows: list[dict[str, str]], latest: str, schedule: Schedule) -> dict[str, Any]:
    selected: list[dict[str, str]] = []
    buy_by_id: dict[str, float] = {}
    pnl_by_id: dict[str, float] = {}
    pair_qty_by_id: dict[str, float] = {}
    profile_pnl: dict[str, float] = {}
    for row in rows:
        cap = schedule.cap_for(row)
        if cap <= 0:
            continue
        source_buy = fnum(row, "source_buy_actual")
        if source_buy <= 0:
            continue
        scale = min(1.0, cap / source_buy)
        buy = source_buy * scale
        pnl = fnum(row, "source_cash_pnl") * scale
        if buy <= 0:
            continue
        selected.append(row)
        buy_by_id[row["candidate_id"]] = buy
        pnl_by_id[row["candidate_id"]] = pnl
        pair_qty_by_id[row["candidate_id"]] = fnum(row, "source_paired_qty") * scale
        profile_pnl[row["source_profile_label"]] = profile_pnl.get(row["source_profile_label"], 0.0) + pnl

    buy_total = sum(buy_by_id.values())
    pnl_total = sum(pnl_by_id.values())
    bad_buy = sum(
        buy_by_id[row["candidate_id"]]
        for row in selected
        if fnum(row, "source_pair_cost") >= 1.0
    )
    latest_count = len([row for row in selected if row["source_profile_label"] == latest])
    resid = weighted(selected, "source_resid_rate", buy_by_id)
    pair_cost = weighted(selected, "source_pair_cost", pair_qty_by_id)
    return {
        "schedule_id": schedule.schedule_id,
        "base_cap": schedule.base,
        "last60_boost": schedule.last60_boost,
        "low_20_35_boost": schedule.low_boost,
        "high_65_80_boost": schedule.high_boost,
        "down_side_boost": schedule.down_boost,
        "mid_up_boost": schedule.mid_up_boost,
        "per_market_cap": schedule.cap,
        "selected_market_count": len(selected),
        "latest_window_market_count": latest_count,
        "latest_window_participation_rate": round(latest_count / EXPECTED_LATEST_ROUNDS, 8),
        "active_profile_count": len(profile_pnl),
        "winning_profile_count": sum(1 for value in profile_pnl.values() if value > 0),
        "worst_profile_cash_pnl": round(min(profile_pnl.values()), 6) if profile_pnl else None,
        "scaled_buy_actual": round(buy_total, 6),
        "scaled_cash_pnl": round(pnl_total, 6),
        "scaled_roi_on_buy": round(pnl_total / buy_total, 8) if buy_total else None,
        "roi_on_initial_300": round(pnl_total / INITIAL_BANKROLL, 8),
        "turnover_on_initial_300": round(buy_total / INITIAL_BANKROLL, 8),
        "max_capital_tied_proxy": round(peak_capital(selected, buy_by_id), 6),
        "weighted_pair_cost": round(pair_cost, 8) if pair_cost is not None else None,
        "weighted_resid_rate_by_buy": round(resid, 8) if resid is not None else None,
        "bad_pair_cost_ge_1_buy_share": round(bad_buy / buy_total, 8) if buy_total else None,
        "public_profile_proxy_only": True,
    }


def passes(row: dict[str, Any], min_participation: float, max_resid: float, max_bad_pc: float) -> bool:
    return (
        row["latest_window_participation_rate"] >= min_participation
        and row["winning_profile_count"] >= 6
        and row["weighted_resid_rate_by_buy"] is not None
        and row["weighted_resid_rate_by_buy"] <= max_resid
        and row["bad_pair_cost_ge_1_buy_share"] is not None
        and row["bad_pair_cost_ge_1_buy_share"] <= max_bad_pc
    )


def pareto(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        dominated = False
        for other in rows:
            if other is row:
                continue
            if (
                other["latest_window_participation_rate"] >= row["latest_window_participation_rate"]
                and (other["scaled_roi_on_buy"] or 0.0) >= (row["scaled_roi_on_buy"] or 0.0)
                and (other["weighted_resid_rate_by_buy"] or 9.0) <= (row["weighted_resid_rate_by_buy"] or 9.0)
                and other["bad_pair_cost_ge_1_buy_share"] <= row["bad_pair_cost_ge_1_buy_share"]
                and (
                    other["latest_window_participation_rate"] > row["latest_window_participation_rate"]
                    or (other["scaled_roi_on_buy"] or 0.0) > (row["scaled_roi_on_buy"] or 0.0)
                    or (other["weighted_resid_rate_by_buy"] or 9.0) < (row["weighted_resid_rate_by_buy"] or 9.0)
                    or other["bad_pair_cost_ge_1_buy_share"] < row["bad_pair_cost_ge_1_buy_share"]
                )
            ):
                dominated = True
                break
        if not dominated:
            out.append(row)
    return out


def write_preview(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo 'NOT_AUTHORIZED: CE25 BTC5M broad overlay sizing grid is review-only'\n"
        "exit 66\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.2f}%"


def money(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"${value:,.2f}"


def render_report(packet: dict[str, Any]) -> str:
    decision = packet["decision"]
    best = decision["best_high_coverage_review_candidate"]
    strict = decision["best_strict_resid80_candidate"]
    coverage60 = decision["best_coverage60_resid12_candidate"]

    lines = [
        "# CE25 BTC5M Broad Overlay Sizing Grid",
        "",
        f"Status: `{packet['status']}`",
        "",
        "## Decision",
        "",
        "The best current high-participation public-profile sizing candidate is:",
        "",
        f"- schedule: `{best['schedule_id'] if best else 'NONE'}`",
        f"- latest participation: {pct(best.get('latest_window_participation_rate') if best else None)}",
        f"- scaled ROI on buy: {pct(best.get('scaled_roi_on_buy') if best else None)}",
        f"- weighted residual: {pct(best.get('weighted_resid_rate_by_buy') if best else None)}",
        f"- bad pair-cost >= 1 buy share: {pct(best.get('bad_pair_cost_ge_1_buy_share') if best else None)}",
        f"- 300 USDC proxy PnL: {money(best.get('scaled_cash_pnl') if best else None)}",
        f"- 300 USDC proxy ROI: {pct(best.get('roi_on_initial_300') if best else None)}",
        "",
        "This remains review-only. It is a public-profile sizing proxy, not matching replay, not OOS, and not live/canary-ready.",
        "",
        "## Grid Findings",
        "",
        f"- schedules scanned: {packet['grid']['schedule_count']}",
        f"- feasible with latest participation >=80%, residual <=16%, bad pair-cost share <=32%: {packet['grid']['feasible80_resid16_badpc32_count']}",
        f"- feasible with latest participation >=80%, residual <=14%, bad pair-cost share <=32%: {packet['grid']['strict80_resid14_badpc32_count']}",
        f"- feasible with latest participation >=60%, residual <=12%, bad pair-cost share <=32%: {packet['grid']['coverage60_resid12_badpc32_count']}",
        "",
        "Interpretation: simple size overlays do not solve the residual problem at high participation. The best high-coverage schedules are still residual-watch candidates. Residual reduction now needs replay-backed execution rules, not more public-profile sizing tweaks.",
        "",
        "## Strict Candidates",
        "",
        f"- best >=80% participation and <=14% residual: `{strict['schedule_id'] if strict else 'NONE'}`",
        f"- best >=60% participation and <=12% residual: `{coverage60['schedule_id'] if coverage60 else 'NONE'}`",
        "",
        "## Required Next Step",
        "",
        "Unlock matching replay/source truth for 2026-05-28..2026-06-04, then replay the selected schedules with official fee, fill-level book evidence, merge/reuse capital ledger, residual unwind, and source crosswalk. Do not promote from this public-profile grid.",
        "",
        "## Non-Claims",
        "",
        "- private_truth_ready=false",
        "- strategy_promotion_ready=false",
        "- live_ready=false",
        "- deployable=false",
        "- oos_authorized=false",
        "- orders_authorized=false",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = read_rows(LEDGER_CSV)
    latest = latest_label(rows)
    all_rows = [summarize(rows, latest, schedule) for schedule in schedules()]

    all_rows.sort(
        key=lambda row: (
            row["latest_window_participation_rate"],
            row["weighted_resid_rate_by_buy"] is not None and -row["weighted_resid_rate_by_buy"],
            row["scaled_roi_on_buy"] or 0.0,
        ),
        reverse=True,
    )
    feasible_80 = [
        row for row in all_rows
        if passes(row, min_participation=0.80, max_resid=0.16, max_bad_pc=0.32)
    ]
    feasible_80.sort(
        key=lambda row: (row["roi_on_initial_300"], row["scaled_roi_on_buy"] or 0.0),
        reverse=True,
    )
    strict_resid = [
        row for row in all_rows
        if passes(row, min_participation=0.80, max_resid=0.14, max_bad_pc=0.32)
    ]
    coverage_60_resid12 = [
        row for row in all_rows
        if passes(row, min_participation=0.60, max_resid=0.12, max_bad_pc=0.32)
    ]
    pareto_rows = pareto(all_rows)
    pareto_rows.sort(
        key=lambda row: (
            row["latest_window_participation_rate"],
            row["scaled_roi_on_buy"] or 0.0,
        ),
        reverse=True,
    )

    all_csv = OUTPUT_DIR / "ce25_btc5m_broad_overlay_sizing_grid_all.csv"
    feasible_csv = OUTPUT_DIR / "ce25_btc5m_broad_overlay_sizing_grid_feasible80.csv"
    pareto_csv = OUTPUT_DIR / "ce25_btc5m_broad_overlay_sizing_grid_pareto.csv"
    fields = list(all_rows[0].keys())
    write_csv(all_csv, all_rows, fields)
    write_csv(feasible_csv, feasible_80[:250], fields)
    write_csv(pareto_csv, pareto_rows, fields)
    preview = OUTPUT_DIR / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    write_preview(preview)

    best_feasible = feasible_80[0] if feasible_80 else None
    best_strict = max(strict_resid, key=lambda row: row["roi_on_initial_300"]) if strict_resid else None
    best_coverage60_resid12 = (
        max(coverage_60_resid12, key=lambda row: row["roi_on_initial_300"])
        if coverage_60_resid12
        else None
    )
    packet = {
        "schema_version": 1,
        "status": STATUS,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "strategy_id": "CE25_BTC5M_BROAD_PARTICIPATION_CONTROLLER_V1",
        "strategy_owner_line": "CE25_BROAD_RESEARCH",
        "grid": {
            "schedule_count": len(all_rows),
            "feasible80_resid16_badpc32_count": len(feasible_80),
            "strict80_resid14_badpc32_count": len(strict_resid),
            "coverage60_resid12_badpc32_count": len(coverage_60_resid12),
        },
        "decision": {
            "best_high_coverage_review_candidate": best_feasible,
            "best_strict_resid80_candidate": best_strict,
            "best_coverage60_resid12_candidate": best_coverage60_resid12,
            "interpretation": "high-coverage residual remains a watch item; matching replay is required before claiming real improvement",
        },
        "source_bindings": {
            "ledger_csv": {"path": str(LEDGER_CSV), "sha256": sha256_file(LEDGER_CSV)},
            "controller_v1_packet": {"path": str(CONTROLLER_V1_PACKET), "sha256": sha256_file(CONTROLLER_V1_PACKET)},
            "frontier_packet": {
                "path": str(FRONTIER_PACKET),
                "sha256": sha256_file(FRONTIER_PACKET) if FRONTIER_PACKET.exists() else None,
                "exists": FRONTIER_PACKET.exists(),
            },
            "matching_source_packet": {"path": str(MATCHING_SOURCE_PACKET), "sha256": sha256_file(MATCHING_SOURCE_PACKET)},
            "build_script": {
                "path": str(ROOT / "scripts" / "build_ce25_btc5m_broad_overlay_sizing_grid_packet.py"),
                "sha256": sha256_file(ROOT / "scripts" / "build_ce25_btc5m_broad_overlay_sizing_grid_packet.py"),
            },
        },
        "official_fee_contract": {
            "fee_rate": OFFICIAL_FEE_RATE,
            "formula": "fee = C * feeRate * p * (1 - p)",
            "source_profile_fee_status": "fee-inclusive public activity proxy",
            "exact_replay_status": "BLOCKED_MATCHING_FILL_LEVEL_REPLAY_SOURCE_REQUIRED",
        },
        "matching_source_gate": {
            "status": read_json(MATCHING_SOURCE_PACKET).get("status"),
            "archive_root_available_now": read_json(MATCHING_SOURCE_PACKET).get("environment_preflight", {}).get("archive_root_available_now"),
            "replay_builder_target_days_allowlisted_now": read_json(MATCHING_SOURCE_PACKET).get("environment_preflight", {}).get("replay_builder_target_days_allowlisted_now"),
        },
        "outputs": {
            "all_csv": str(all_csv),
            "feasible80_csv": str(feasible_csv),
            "pareto_csv": str(pareto_csv),
            "command_preview_not_authorized": str(preview),
        },
        "non_claims": {
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "deployable": False,
            "oos_authorized": False,
            "runner_authorized": False,
            "orders_authorized": False,
            "canary_authorized": False,
        },
        "highest_allowed_status": STATUS,
    }
    packet_path = OUTPUT_DIR / "CE25_BTC5M_BROAD_OVERLAY_SIZING_GRID_PACKET.json"
    packet_path.write_text(json.dumps(packet, indent=2, sort_keys=True), encoding="utf-8")
    report_path = OUTPUT_DIR / "CE25_BTC5M_BROAD_OVERLAY_SIZING_GRID_REPORT.md"
    report_path.write_text(render_report(packet), encoding="utf-8")
    packet["outputs"]["report_md"] = str(report_path)
    packet_path.write_text(json.dumps(packet, indent=2, sort_keys=True), encoding="utf-8")

    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": STATUS,
        "files": {},
    }
    for path in sorted(OUTPUT_DIR.iterdir()):
        if path.name == "CE25_BTC5M_BROAD_OVERLAY_SIZING_GRID_HASH_MANIFEST.json":
            continue
        if path.is_file():
            manifest["files"][path.name] = {
                "path": str(path),
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
            }
    manifest_path = OUTPUT_DIR / "CE25_BTC5M_BROAD_OVERLAY_SIZING_GRID_HASH_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    print(
        json.dumps(
            {
                "status": STATUS,
                "output_dir": str(OUTPUT_DIR),
                "schedule_count": len(all_rows),
                "feasible80_count": len(feasible_80),
                "strict80_resid14_count": len(strict_resid),
                "coverage60_resid12_count": len(coverage_60_resid12),
                "best_feasible": best_feasible["schedule_id"] if best_feasible else None,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
