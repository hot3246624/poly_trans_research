#!/usr/bin/env python3
"""Package CE25 BTC5m local residual-rule replay smoke evidence.

This packet intentionally uses the local 2026-05-02..2026-05-18 candidate base.
It does not wait for PolyData, does not execute replay, and does not authorize
WS/OOS/live paths. It compares existing local state-machine runs and records why
the public-profile dynamic sizing overrides are not directly reusable on the
older local source window.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
BT_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
EXPORTS = ROOT / "data" / "exports"
OUTPUT_DIR = EXPORTS / "ce25_btc5m_local_residual_replay_smoke_packet_20260607"

CANDIDATE_BASE_DIR = (
    BT_ROOT / "derived/completion_candidate_pipeline_v1/local_20260502_20260518_paircap102"
)
CANDIDATE_BASE_MANIFEST = CANDIDATE_BASE_DIR / "CANDIDATE_BASE_MANIFEST.json"
LOCAL_SOURCE_ALIGNMENT_PACKET = (
    EXPORTS
    / "ce25_btc5m_local_replay_source_alignment_packet_20260607"
    / "CE25_BTC5M_LOCAL_REPLAY_SOURCE_ALIGNMENT_PACKET.json"
)
DYNAMIC_OVERRIDES_CSV = (
    EXPORTS
    / "ce25_btc5m_dynamic_sizing_overrides_packet_20260606"
    / "ce25_btc5m_dynamic_sizing_overrides.csv"
)
DYNAMIC_OVERRIDES_PACKET = (
    EXPORTS
    / "ce25_btc5m_dynamic_sizing_overrides_packet_20260606"
    / "CE25_BTC5M_DYNAMIC_SIZING_OVERRIDES_PACKET.json"
)
LOCAL_DYNAMIC_OVERRIDES_CSV = (
    EXPORTS
    / "ce25_btc5m_local_dynamic_sizing_overrides_packet_20260607"
    / "ce25_btc5m_local_dynamic_sizing_overrides.csv"
)
LOCAL_DYNAMIC_OVERRIDES_PACKET = (
    EXPORTS
    / "ce25_btc5m_local_dynamic_sizing_overrides_packet_20260607"
    / "CE25_BTC5M_LOCAL_DYNAMIC_SIZING_OVERRIDES_PACKET.json"
)
STATE_MACHINE = ROOT / "scripts/run_completion_candidate_state_machine.py"
BUILD_SCRIPT = ROOT / "scripts/build_ce25_btc5m_local_residual_replay_smoke_packet.py"

RESULT_ROOT = BT_ROOT / "derived/completion_candidate_pipeline_v1"
RUNS = [
    {
        "run_id": "baseline_seed120_qty5_pc102",
        "role": "legacy_pre_cleanup_reference_baseline",
        "dir": RESULT_ROOT
        / "pass_local_completion_residual_cooldown_officialfee_e055_t5_imb125_rc30_050_20260502_20260518_publicfull_v2",
    },
    {
        "run_id": "strict_seed300_qty2_pc096",
        "role": "strict_paircap_residual_rule_probe",
        "dir": RESULT_ROOT
        / "ce25_btc5m_local_residual_rule_replay_20260607"
        / "strict_qty2_pc096_seed300_cd5_imb125_rage30_rcost005_full_5m",
    },
    {
        "run_id": "broad_seed180_qty5_pc102",
        "role": "local_mid_window_residual_rule_probe",
        "dir": RESULT_ROOT
        / "ce25_btc5m_local_residual_rule_replay_20260607"
        / "broad_qty5_pc102_seed180_cd5_imb125_rage30_rcost050_first3m",
    },
    {
        "run_id": "broad_seed300_qty5_pc102",
        "role": "local_full_5m_broad_residual_rule_probe",
        "dir": RESULT_ROOT
        / "ce25_btc5m_local_residual_rule_replay_20260607"
        / "broad_qty5_pc102_seed300_cd5_imb125_rage30_rcost050_full_5m",
    },
    {
        "run_id": "broad_seed300_qty5_pc102_imb250",
        "role": "local_full_5m_relaxed_imbalance_probe",
        "dir": RESULT_ROOT
        / "ce25_btc5m_local_residual_rule_replay_20260607"
        / "broad_qty5_pc102_seed300_cd5_imb250_rage30_rcost050_full_5m",
    },
    {
        "run_id": "dynamic_no_last60_seed300_qty5_pc102_imb250",
        "role": "local_no_last60_dynamic_sizing_replay_probe",
        "dir": RESULT_ROOT
        / "ce25_btc5m_local_dynamic_sizing_replay_20260607"
        / "no_last60_base8_l6020_low5_hi10_down3_midup5_cap30_seed300_pc102_imb250",
    },
    {
        "run_id": "dynamic_no_last60_seed300_qty30_pc102_imb250",
        "role": "local_no_last60_dynamic_sizing_cap_active_probe",
        "dir": RESULT_ROOT
        / "ce25_btc5m_local_dynamic_sizing_replay_20260607"
        / "no_last60_base8_l6020_low5_hi10_down3_midup5_cap30_seed300_pc102_imb250_tqty30",
    },
]

STATUS = (
    "KEEP_CE25_BTC5M_LOCAL_RESIDUAL_REPLAY_SMOKE_REVIEWED_"
    "BACKTEST_V1_COMPACT_ARTIFACTS_OK_LEGACY_STRICT_CACHE_OPTIONAL_NOT_OOS_READY"
)
LEGACY_STRICT_CACHE_STATUS = (
    "LEGACY_STRICT_L1_CACHE_ABSENT_BACKTEST_V1_MAINLINE_NOT_BLOCKED_"
    "REBUILD_ONLY_FOR_OLD_TAKER_BUY_PIPELINE_REPRO"
)
OFFICIAL_FEE_FORMULA = "fee = shares * fee_rate * price * (1 - price)"
OFFICIAL_FEE_RATE = 0.07


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


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def binding(path: Path, required: bool = True) -> dict[str, Any]:
    item: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if path.exists():
        item.update({"sha256": sha256_file(path), "size": path.stat().st_size})
    elif required:
        item["missing_required"] = True
    return item


def fnum(value: Any) -> float:
    try:
        if value in (None, ""):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def market_start_s(slug: str) -> int | None:
    match = re.search(r"-(\d{10})$", slug or "")
    if not match:
        return None
    return int(match.group(1))


def estimate_market_end_cashflow(actions_path: Path) -> dict[str, Any]:
    if not actions_path.exists():
        return {"available": False, "reason": "missing_actions_csv"}
    by_market: dict[str, dict[str, Any]] = defaultdict(lambda: {"YES": 0.0, "NO": 0.0, "cost": 0.0, "fee": 0.0})
    events: list[tuple[int, str, float, str]] = []
    with actions_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            condition_id = row["condition_id"]
            side = row["side"]
            if side not in {"YES", "NO"}:
                continue
            qty = fnum(row.get("seed_qty"))
            seed_cost = fnum(row.get("seed_cost"))
            fee = fnum(row.get("fee"))
            ts_ms = int(fnum(row.get("ts_ms")))
            start_s = market_start_s(row.get("slug", ""))
            market = by_market[condition_id]
            market[side] += qty
            market["cost"] += seed_cost
            market["fee"] += fee
            market["winner_side"] = row.get("winner_side")
            market["market_end_ms"] = (start_s + 300) * 1000 if start_s is not None else ts_ms
            events.append((ts_ms, "buy", -(seed_cost + fee), condition_id))

    payout_total = 0.0
    pair_qty_total = 0.0
    residual_payout_total = 0.0
    for condition_id, market in by_market.items():
        yes_qty = float(market["YES"])
        no_qty = float(market["NO"])
        pair_qty = min(yes_qty, no_qty)
        winner = market.get("winner_side")
        residual_payout = 0.0
        if winner == "YES":
            residual_payout = max(0.0, yes_qty - no_qty)
        elif winner == "NO":
            residual_payout = max(0.0, no_qty - yes_qty)
        payout = pair_qty + residual_payout
        pair_qty_total += pair_qty
        residual_payout_total += residual_payout
        payout_total += payout
        events.append((int(market["market_end_ms"]), "market_end_payout", payout, condition_id))

    events.sort(key=lambda item: (item[0], 0 if item[1] == "buy" else 1))
    cash = 0.0
    min_cash = 0.0
    min_event: dict[str, Any] | None = None
    max_cash = 0.0
    for ts_ms, event_type, amount, condition_id in events:
        cash += amount
        if cash < min_cash:
            min_cash = cash
            min_event = {
                "ts_ms": ts_ms,
                "event_type": event_type,
                "condition_id": condition_id,
                "cash_after": round(cash, 6),
            }
        max_cash = max(max_cash, cash)
    total_buy_cost_with_fee = -sum(amount for _, event_type, amount, _ in events if event_type == "buy")
    peak = -min_cash
    return {
        "available": True,
        "cashflow_model": "buy outflow at action ts; pair/residual payout at 5m market end; approximate capital stress, not exchange/private settlement truth",
        "market_count": len(by_market),
        "cashflow_event_count": len(events),
        "total_buy_cost_with_fee": round(total_buy_cost_with_fee, 6),
        "payout_total": round(payout_total, 6),
        "final_cash_pnl": round(cash, 6),
        "peak_capital_required_usdc": round(peak, 6),
        "capital_300_sufficient": peak <= 300.0 + 1e-9,
        "roi_on_300_if_unscaled_and_sufficient": round(cash / 300.0, 6) if peak <= 300.0 + 1e-9 else None,
        "linear_scaled_pnl_for_300_capital": round(cash * 300.0 / peak, 6) if peak > 0 else None,
        "linear_scaled_roi_for_300_capital": round((cash * 300.0 / peak) / 300.0, 6) if peak > 0 else None,
        "linear_scaled_capacity_validated": False,
        "linear_scaled_capacity_caveat": "linear scaling uses peak cash stress only; it does not prove live depth, queue fill, slippage, settlement latency, or order capacity",
        "pair_qty_total": round(pair_qty_total, 6),
        "residual_payout_total": round(residual_payout_total, 6),
        "min_cash_event": min_event,
        "max_cash_after_all_events": round(max_cash, 6),
    }


def summarize_run(item: dict[str, Any]) -> dict[str, Any]:
    run_dir = item["dir"]
    result_path = run_dir / "RESULT_SUMMARY_MANIFEST.json"
    compliance_path = run_dir / "COMPLIANCE_MANIFEST.json"
    actions_path = run_dir / "actions.csv"
    if not result_path.exists():
        return {
            "run_id": item["run_id"],
            "role": item["role"],
            "dir": str(run_dir),
            "exists": False,
            "status": "MISSING",
        }
    result = read_json(result_path)
    core = result["core_metrics"]
    compliance = result.get("compliance_summary", {})
    config = result.get("config", {})
    cashflow = estimate_market_end_cashflow(actions_path)
    return {
        "run_id": item["run_id"],
        "role": item["role"],
        "dir": str(run_dir),
        "exists": True,
        "result_summary_manifest_sha256": sha256_file(result_path),
        "compliance_manifest_sha256": sha256_file(compliance_path) if compliance_path.exists() else None,
        "actions_csv_sha256": sha256_file(actions_path) if actions_path.exists() else None,
        "status": result.get("status"),
        "row_count": result.get("row_count"),
        "target_qty": config.get("target_qty"),
        "seed_offset_max_s": config.get("seed_offset_max_s"),
        "seed_l1_pair_cap": config.get("seed_l1_pair_cap"),
        "residual_cooldown_age_s": config.get("residual_cooldown_age_s"),
        "residual_cooldown_cost_cap": config.get("residual_cooldown_cost_cap"),
        "fee_model": config.get("fee_model"),
        "official_fee_rate": config.get("official_fee_rate"),
        "active_markets": core.get("active_markets"),
        "selected_candidate_count": core.get("selected_candidate_count"),
        "pair_actions": core.get("pair_actions"),
        "gross_buy_cost": core.get("gross_buy_cost"),
        "official_taker_fee": core.get("official_taker_fee"),
        "net_pnl": core.get("net_pnl"),
        "net_roi": core.get("net_roi"),
        "actual_settle_roi": core.get("actual_settle_roi"),
        "weighted_pair_cost": core.get("weighted_pair_cost"),
        "pair_share_rate": core.get("pair_share_rate"),
        "residual_qty_rate": core.get("residual_qty_rate"),
        "residual_cost_rate": core.get("residual_cost_rate"),
        "stress100_worst_pnl": core.get("stress100_worst_pnl"),
        "worst_day_fee_after_pnl": core.get("worst_day_fee_after_pnl"),
        "sizing_override_match_rows": core.get("sizing_override_match_rows"),
        "legacy_strict_cache_pass": compliance.get("strict_cache_pass"),
        "backtest_v1_mainline_cache_policy": "COMPACT_ARTIFACTS_DO_NOT_REQUIRE_LEGACY_STRICT_L1_CACHE",
        "public_account_audit_coverage_pass": compliance.get("public_account_audit_coverage_pass"),
        "capital_300_sufficient": cashflow.get("capital_300_sufficient"),
        "roi_on_300_if_unscaled_and_sufficient": cashflow.get("roi_on_300_if_unscaled_and_sufficient"),
        "peak_capital_required_usdc": cashflow.get("peak_capital_required_usdc"),
        "cashflow": cashflow,
    }


def dynamic_override_overlap_audit() -> dict[str, Any]:
    overrides = read_csv(DYNAMIC_OVERRIDES_CSV)
    override_condition_ids = {row.get("condition_id", "") for row in overrides if row.get("condition_id")}
    override_slugs = {row.get("slug", "") for row in overrides if row.get("slug")}
    audit: dict[str, Any] = {
        "override_csv": str(DYNAMIC_OVERRIDES_CSV),
        "override_rows": len(overrides),
        "override_condition_count": len(override_condition_ids),
        "override_slug_count": len(override_slugs),
        "candidate_base_dir": str(CANDIDATE_BASE_DIR),
        "candidate_condition_count": None,
        "candidate_slug_count": None,
        "condition_overlap_count": None,
        "slug_overlap_count": None,
        "direct_condition_or_slug_override_legitimate": False,
        "direct_replay_interpretation": "UNKNOWN_UNTIL_AUDIT",
    }
    try:
        import duckdb  # type: ignore

        manifest = read_json(CANDIDATE_BASE_MANIFEST)
        db_path = CANDIDATE_BASE_DIR / str(manifest.get("outputs", {}).get("duckdb", "candidate_base.duckdb"))
        con = duckdb.connect(str(db_path), read_only=True)
        candidate_conditions = {str(row[0]) for row in con.execute("SELECT DISTINCT condition_id FROM candidate_base").fetchall()}
        candidate_slugs = {str(row[0]) for row in con.execute("SELECT DISTINCT slug FROM candidate_base").fetchall()}
        con.close()
        condition_overlap = override_condition_ids & candidate_conditions
        slug_overlap = override_slugs & candidate_slugs
        audit.update(
            {
                "candidate_condition_count": len(candidate_conditions),
                "candidate_slug_count": len(candidate_slugs),
                "condition_overlap_count": len(condition_overlap),
                "slug_overlap_count": len(slug_overlap),
                "direct_condition_or_slug_override_legitimate": bool(condition_overlap or slug_overlap),
                "direct_replay_interpretation": (
                    "DIRECT_OVERRIDE_CAN_MATCH_LOCAL_SOURCE"
                    if condition_overlap or slug_overlap
                    else "DIRECT_PUBLIC_PROFILE_OVERRIDE_IS_NO_OP_ON_LOCAL_20260502_20260518_SOURCE"
                ),
            }
        )
    except Exception as exc:  # pragma: no cover - operational report fallback
        audit.update({"audit_error": repr(exc), "direct_replay_interpretation": "AUDIT_FAILED_FAIL_CLOSED"})
    return audit


def write_preview(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo 'NOT_AUTHORIZED: CE25 BTC5M local residual replay smoke packet is review-only' >&2\n"
        "exit 66\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def render_report(packet: dict[str, Any], run_rows: list[dict[str, Any]]) -> str:
    existing = [row for row in run_rows if row.get("exists")]
    best = max(existing, key=lambda row: fnum(row.get("net_pnl"))) if existing else None
    by_id = {str(row.get("run_id")): row for row in existing}
    imb250 = by_id.get("broad_seed300_qty5_pc102_imb250")
    dynamic_t5 = by_id.get("dynamic_no_last60_seed300_qty5_pc102_imb250")
    dynamic_t30 = by_id.get("dynamic_no_last60_seed300_qty30_pc102_imb250")

    def same_metrics(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
        if not a or not b:
            return False
        keys = [
            "selected_candidate_count",
            "active_markets",
            "net_pnl",
            "net_roi",
            "pair_share_rate",
            "residual_qty_rate",
            "weighted_pair_cost",
        ]
        return all(a.get(key) == b.get(key) for key in keys)

    lines = [
        "# CE25 BTC5M Local Residual Replay Smoke Packet",
        "",
        f"Status: `{packet['status']}`",
        "",
        "## Decision",
        "",
        "The current mainline should proceed on the local `2026-05-02..2026-05-18` replay/source window. Waiting for `/Volumes/PolyData` is no longer the critical path for this branch.",
        "",
        "## Replay Result Summary",
        "",
        "| run | status | offset max | paircap | actions | markets | net PnL | net ROI | residual qty | pair share | legacy strict cache |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in run_rows:
        lines.append(
            "| {run_id} | {status} | {offset} | {paircap} | {actions} | {markets} | {pnl} | {roi} | {resid} | {pair_share} | {cache} |".format(
                run_id=row["run_id"],
                status=row.get("status"),
                offset=row.get("seed_offset_max_s", ""),
                paircap=row.get("seed_l1_pair_cap", ""),
                actions=row.get("selected_candidate_count", row.get("row_count", "")),
                markets=row.get("active_markets", ""),
                pnl=round(fnum(row.get("net_pnl")), 6) if row.get("net_pnl") is not None else "",
                roi=f"{100 * fnum(row.get('net_roi')):.2f}%" if row.get("net_roi") is not None else "",
                resid=f"{100 * fnum(row.get('residual_qty_rate')):.2f}%"
                if row.get("residual_qty_rate") is not None
                else "",
                pair_share=f"{100 * fnum(row.get('pair_share_rate')):.2f}%"
                if row.get("pair_share_rate") is not None
                else "",
                cache=row.get("legacy_strict_cache_pass"),
            )
        )
    lines.extend(
        [
            "",
            "## Current Best Local Smoke",
            "",
        ]
    )
    if best:
        lines.extend(
            [
                f"- best by fee-after PnL: `{best['run_id']}`",
                f"- selected actions: `{best.get('selected_candidate_count')}`",
                f"- active markets: `{best.get('active_markets')}`",
                f"- fee-after net PnL: `{best.get('net_pnl')}`",
                f"- official-fee net ROI: `{best.get('net_roi')}`",
                f"- residual qty rate: `{best.get('residual_qty_rate')}`",
                f"- legacy strict cache pass: `{best.get('legacy_strict_cache_pass')}`",
                "- Backtest V1 mainline cache policy: compact artifacts are the active source; old strict L1 cache is not required for current research/shadow-design metrics.",
            ]
        )
    lines.extend(
        [
            "",
            "## Dynamic Sizing Boundary",
            "",
            f"- direct override overlap: condition `{packet['dynamic_override_overlap_audit'].get('condition_overlap_count')}`, slug `{packet['dynamic_override_overlap_audit'].get('slug_overlap_count')}`",
            "- The 2026-05-28+ public-profile condition-level override CSV must not be presented as a valid direct replay input for 2026-05-02..05-18 when overlap is zero.",
            "- A legitimate local dynamic-sizing replay needs a bucket-rule adapter that derives `candidate_row_id` or local condition-level caps from the same 2026-05-02..05-18 candidate rows.",
            "",
            "## Local Dynamic Sizing Replay Result",
            "",
            f"- no-last60 dynamic target_qty=5 matches imb250 baseline exactly: `{same_metrics(imb250, dynamic_t5)}`",
            f"- no-last60 dynamic target_qty=30 matches imb250 baseline exactly: `{same_metrics(imb250, dynamic_t30)}`",
            "- Interpretation: the local max-open-cost caps are not binding under the current state-machine controls. The active constraints are currently imbalance/cooldown/residual-cooldown rather than public-profile-style per-market cap sizing.",
            "- Next useful dynamic-sizing work should target `target_qty`, `imbalance_qty_cap`, or candidate-row enable/disable policies, not just `max_open_cost` caps.",
            "",
            "## Compliance Caveats",
            "",
            "- New local replay runs after cache cleanup may show legacy `strict_cache_pass=false` because the old strict L1 cache root is missing.",
            "- Backtest V1 mainline no longer depends on `/Users/hot/web3Scientist/poly_backtest_data/backtest_cache/taker_buy_signal_core_v2_strict_l1`; it uses compact DuckDB/artifact layers such as multiasset L1 flow, replay_store core/L2, L2 top-aligned mart, audit/catalog/readiness manifests.",
            "- Rebuilding the old strict cache is only needed for old taker-buy strict cache wide-search reproducibility or old report reproduction, not for current Backtest V1 research or shadow/no-order design.",
            "- Public-account audit coverage remains present for the included local days.",
            "",
            "## Non-Claims",
            "",
            "- replay_execution_authorized=false",
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
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    run_rows = [summarize_run(item) for item in RUNS]
    overlap = dynamic_override_overlap_audit()
    preview = OUTPUT_DIR / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    write_preview(preview)

    csv_path = OUTPUT_DIR / "ce25_btc5m_local_residual_replay_smoke_summary.csv"
    csv_fields = [
        "run_id",
        "role",
        "dir",
        "exists",
        "status",
        "row_count",
        "target_qty",
        "seed_offset_max_s",
        "seed_l1_pair_cap",
        "residual_cooldown_age_s",
        "residual_cooldown_cost_cap",
        "selected_candidate_count",
        "active_markets",
        "pair_actions",
        "gross_buy_cost",
        "official_taker_fee",
        "net_pnl",
        "net_roi",
        "weighted_pair_cost",
        "pair_share_rate",
        "residual_qty_rate",
        "residual_cost_rate",
        "stress100_worst_pnl",
        "worst_day_fee_after_pnl",
        "sizing_override_match_rows",
        "legacy_strict_cache_pass",
        "public_account_audit_coverage_pass",
        "capital_300_sufficient",
        "roi_on_300_if_unscaled_and_sufficient",
        "peak_capital_required_usdc",
    ]
    write_csv(csv_path, run_rows, csv_fields)

    existing = [row for row in run_rows if row.get("exists")]
    best = max(existing, key=lambda row: fnum(row.get("net_pnl"))) if existing else None
    legacy_strict_cache_fail_runs = [row["run_id"] for row in existing if row.get("legacy_strict_cache_pass") is False]
    packet = {
        "schema_version": 1,
        "status": STATUS,
        "created_at": utc_now(),
        "scope": "review-only local residual-rule replay smoke over 2026-05-02..2026-05-18 BTC5M candidate base",
        "source_window": {
            "valid_days_utc": read_json(CANDIDATE_BASE_MANIFEST).get("days"),
            "candidate_base_dir": str(CANDIDATE_BASE_DIR),
            "candidate_base_manifest": binding(CANDIDATE_BASE_MANIFEST),
            "polydata_required_for_current_mainline": False,
        },
        "official_fee": {
            "fee_model": "official_taker",
            "official_fee_rate": OFFICIAL_FEE_RATE,
            "official_fee_formula": OFFICIAL_FEE_FORMULA,
            "source": "https://docs.polymarket.com/trading/fees",
        },
        "source_bindings": {
            "local_source_alignment_packet": binding(LOCAL_SOURCE_ALIGNMENT_PACKET),
            "dynamic_sizing_overrides_packet": binding(DYNAMIC_OVERRIDES_PACKET),
            "dynamic_sizing_overrides_csv": binding(DYNAMIC_OVERRIDES_CSV),
            "local_dynamic_sizing_overrides_packet": binding(LOCAL_DYNAMIC_OVERRIDES_PACKET, required=False),
            "local_dynamic_sizing_overrides_csv": binding(LOCAL_DYNAMIC_OVERRIDES_CSV, required=False),
            "state_machine_script": binding(STATE_MACHINE),
            "build_script": binding(BUILD_SCRIPT),
        },
        "runs": run_rows,
        "best_local_smoke_by_net_pnl": best["run_id"] if best else None,
        "legacy_strict_cache_policy": {
            "status": LEGACY_STRICT_CACHE_STATUS,
            "old_cache_root": str(BT_ROOT / "backtest_cache/taker_buy_signal_core_v2_strict_l1"),
            "backtest_v1_mainline_blocked_by_missing_old_cache": False,
            "current_research_metrics_blocked_by_missing_old_cache": False,
            "rebuild_required_for_current_mainline": False,
            "rebuild_required_only_for": [
                "old taker-buy strict cache pipeline reproducibility",
                "old strict_cache_pass=true report reproduction",
                "backward-compatible legacy script execution",
            ],
            "rebuild_source_requirement": "must use replay md_book_l1/md_trades/replay source or equivalent DuckDB bridge; never infer from state-machine result outputs",
            "legacy_strict_cache_fail_runs": legacy_strict_cache_fail_runs,
        },
        "dynamic_override_overlap_audit": overlap,
        "dynamic_sizing_decision": {
            "direct_public_profile_override_csv_allowed_for_local_replay": bool(
                overlap.get("direct_condition_or_slug_override_legitimate")
            ),
            "direct_replay_reason": overlap.get("direct_replay_interpretation"),
            "next_legitimate_path": (
                "build a local bucket-rule adapter that derives candidate_row_id/local condition caps "
                "from 2026-05-02..05-18 candidate rows, then run a separate local dynamic-sizing replay"
            ),
        },
        "outputs": {
            "packet": "CE25_BTC5M_LOCAL_RESIDUAL_REPLAY_SMOKE_PACKET.json",
            "report": "CE25_BTC5M_LOCAL_RESIDUAL_REPLAY_SMOKE_REPORT.md",
            "summary_csv": "ce25_btc5m_local_residual_replay_smoke_summary.csv",
            "command_preview_not_authorized": "COMMAND_PREVIEW_NOT_AUTHORIZED.sh",
            "sha256sums": "SHA256SUMS.txt",
        },
        "highest_allowed_status": STATUS,
        "non_claims": {
            "replay_execution_authorized": False,
            "oos_authorized": False,
            "runner_authorized": False,
            "orders_authorized": False,
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "deployable": False,
        },
    }
    packet_path = OUTPUT_DIR / "CE25_BTC5M_LOCAL_RESIDUAL_REPLAY_SMOKE_PACKET.json"
    report_path = OUTPUT_DIR / "CE25_BTC5M_LOCAL_RESIDUAL_REPLAY_SMOKE_REPORT.md"
    overlap_path = OUTPUT_DIR / "CE25_BTC5M_DYNAMIC_OVERRIDE_LOCAL_OVERLAP_AUDIT.json"
    write_json(packet_path, packet)
    write_json(overlap_path, overlap)
    report_path.write_text(render_report(packet, run_rows), encoding="utf-8")

    manifest_files = [packet_path, report_path, csv_path, overlap_path, preview]
    sums_path = OUTPUT_DIR / "SHA256SUMS.txt"
    sums_path.write_text(
        "".join(f"{sha256_file(path)}  {path.relative_to(OUTPUT_DIR)}\n" for path in manifest_files),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": STATUS,
                "output_dir": str(OUTPUT_DIR),
                "packet": str(packet_path),
                "report": str(report_path),
                "run_count": len(run_rows),
                "existing_run_count": len(existing),
                "best_local_smoke_by_net_pnl": packet["best_local_smoke_by_net_pnl"],
                "dynamic_direct_override_reason": overlap.get("direct_replay_interpretation"),
                "legacy_strict_cache_status": packet["legacy_strict_cache_policy"]["status"],
                "backtest_v1_mainline_blocked_by_missing_old_cache": packet["legacy_strict_cache_policy"][
                    "backtest_v1_mainline_blocked_by_missing_old_cache"
                ],
                "sha256sums": str(sums_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
