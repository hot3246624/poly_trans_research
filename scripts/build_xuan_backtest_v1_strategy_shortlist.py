#!/usr/bin/env python3
"""Build a review-only strategy shortlist from local Backtest V1 artifacts.

The shortlist separates executable/ex-ante research lanes from outcome-filtered
diagnostics. It does not run OOS, start any runner, or authorize live/canary
work.
"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import duckdb
except ModuleNotFoundError as exc:  # pragma: no cover - operational guard
    raise SystemExit("duckdb is required. Run with `uv run --with duckdb python ...`.") from exc


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
BT_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
OUT = ROOT / "data" / "exports" / "xuan_backtest_v1_strategy_shortlist_20260605"

RESCORE_DIR = BT_ROOT / "derived" / "contract_examples" / "xuan_completion_candidate_rescore_latest"
RESCORE_MANIFEST = RESCORE_DIR / "XUAN_COMPLETION_CANDIDATE_RESCORE_MANIFEST.json"
RESCORE_ALL = RESCORE_DIR / "xuan_completion_candidate_rescore_all.parquet"
RESCORE_TOP = RESCORE_DIR / "xuan_completion_candidate_rescore_top.csv"
CAPITAL_LEDGER = BT_ROOT / "derived" / "contract_examples" / "xuan_capital_ledger_latest" / "XUAN_CAPITAL_LEDGER_REPORT.json"
READINESS_GATE = (
    BT_ROOT
    / "derived"
    / "contract_examples"
    / "xuan_backtest_v1_strategy_readiness_latest"
    / "XUAN_BACKTEST_V1_STRATEGY_READINESS_GATE.json"
)
AUDIT_MANIFEST = (
    BT_ROOT
    / "derived"
    / "contract_examples"
    / "backtest_candidate_audit_pack_with_l2_evidence_latest"
    / "BACKTEST_CANDIDATE_AUDIT_PACK_MANIFEST.json"
)
AUDIT_CSV = (
    BT_ROOT
    / "derived"
    / "contract_examples"
    / "backtest_candidate_audit_pack_with_l2_evidence_latest"
    / "backtest_candidate_audit_pack.csv"
)
MULTI_SM = (
    BT_ROOT
    / "derived"
    / "contract_examples"
    / "multiasset_completion_state_machine_from_l1_flow_v1"
    / "RESULT_SUMMARY_MANIFEST.json"
)
MULTI_DAY = MULTI_SM.parent / "summary_by_day.csv"
BTC_SM = (
    BT_ROOT
    / "derived"
    / "contract_examples"
    / "btc_completion_state_machine_from_l1_flow_taker_normalized_v1"
    / "RESULT_SUMMARY_MANIFEST.json"
)
BTC_DAY = BTC_SM.parent / "summary_by_day.csv"
HEALTH_SCRIPT = ROOT / "scripts" / "validate_multiasset_backtest_v1_local_install.py"

STATUS = "KEEP_XUAN_BACKTEST_V1_STRATEGY_SHORTLIST_REVIEW_READY_OWNER_TRUTH_PROMOTION_BLOCKED"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def fnum(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    return float(value)


def non_claims() -> dict[str, bool]:
    return {
        "private_truth_ready": False,
        "strategy_promotion_ready": False,
        "live_ready": False,
        "deployable": False,
        "oos_authorized": False,
        "runner_authorized": False,
        "orders_authorized": False,
        "canary_authorized": False,
    }


def query_rows(query: str) -> list[dict[str, Any]]:
    con = duckdb.connect()
    try:
        cur = con.execute(query)
        cols = [desc[0] for desc in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        con.close()


def rescore_query(where: str, filter_id: str) -> dict[str, Any]:
    path = str(RESCORE_ALL).replace("'", "''")
    rows = query_rows(
        f"""
        SELECT
          '{filter_id}' AS filter_id,
          count(*) AS market_count,
          count(distinct day) AS day_count,
          count(distinct asset) AS asset_count,
          sum(selected_seed_actions) AS selected_seed_actions,
          sum(gross_buy_cost) AS gross_buy_cost,
          sum(official_taker_fee) AS official_taker_fee,
          sum(xuan_after_fee_pnl) AS after_fee_pnl,
          sum(market_end_residual_cost) AS residual_cost,
          sum(xuan_after_fee_pnl) / nullif(sum(gross_buy_cost), 0) AS gross_cost_roi,
          sum(market_end_residual_cost) / nullif(sum(gross_buy_cost), 0) AS residual_cost_share,
          min(xuan_after_fee_pnl) AS worst_market_after_fee_pnl,
          sum(CASE WHEN positive_xuan_completion_candidate THEN 1 ELSE 0 END) AS positive_market_count
        FROM read_parquet('{path}')
        WHERE {where}
        """
    )
    return rows[0]


def asset_scoreboard() -> list[dict[str, Any]]:
    path = str(RESCORE_ALL).replace("'", "''")
    return query_rows(
        f"""
        SELECT
          asset,
          count(*) AS market_count,
          sum(selected_seed_actions) AS selected_seed_actions,
          sum(gross_buy_cost) AS gross_buy_cost,
          sum(official_taker_fee) AS official_taker_fee,
          sum(xuan_after_fee_pnl) AS after_fee_pnl,
          sum(market_end_residual_cost) AS residual_cost,
          sum(xuan_after_fee_pnl) / nullif(sum(gross_buy_cost), 0) AS gross_cost_roi,
          sum(market_end_residual_cost) / nullif(sum(gross_buy_cost), 0) AS residual_cost_share,
          sum(CASE WHEN positive_xuan_completion_candidate THEN 1 ELSE 0 END) AS positive_market_count
        FROM read_parquet('{path}')
        GROUP BY asset
        ORDER BY after_fee_pnl DESC
        """
    )


def filter_scoreboard() -> list[dict[str, Any]]:
    return [
        {
            **rescore_query("positive_xuan_completion_candidate", "OUTCOME_DIAGNOSTIC_ALL_POSITIVE"),
            "filter_semantics": "OUTCOME_LABEL_DIAGNOSTIC_NOT_EXECUTABLE",
        },
        {
            **rescore_query(
                "positive_xuan_completion_candidate AND residual_cost_share <= 0.03",
                "OUTCOME_DIAGNOSTIC_POSITIVE_RESIDUAL_LE_3PCT",
            ),
            "filter_semantics": "OUTCOME_LABEL_DIAGNOSTIC_NOT_EXECUTABLE",
        },
        {
            **rescore_query(
                "positive_xuan_completion_candidate AND gross_cost_roi >= 0.05 AND residual_cost_share <= 0.05",
                "OUTCOME_DIAGNOSTIC_POSITIVE_ROI_GE_5PCT_RESIDUAL_LE_5PCT",
            ),
            "filter_semantics": "OUTCOME_LABEL_DIAGNOSTIC_NOT_EXECUTABLE",
        },
        {
            **rescore_query("asset = 'BTC'", "EX_ANTE_RESEARCH_BTC_CORE_ALL"),
            "filter_semantics": "ASSET_SCOPE_EX_ANTE_RESEARCH_COMPATIBLE",
        },
        {
            **rescore_query(
                "asset = 'BTC' AND positive_xuan_completion_candidate AND residual_cost_share <= 0.03",
                "OUTCOME_DIAGNOSTIC_BTC_POSITIVE_RESIDUAL_LE_3PCT",
            ),
            "filter_semantics": "OUTCOME_LABEL_DIAGNOSTIC_NOT_EXECUTABLE",
        },
        {
            **rescore_query(
                "asset = 'ETH' AND positive_xuan_completion_candidate AND residual_cost_share <= 0.05",
                "OUTCOME_DIAGNOSTIC_ETH_POSITIVE_RESIDUAL_LE_5PCT",
            ),
            "filter_semantics": "OUTCOME_LABEL_DIAGNOSTIC_NOT_EXECUTABLE",
        },
    ]


def day_stats(path: Path) -> dict[str, Any]:
    rows = read_csv(path)
    pnls = [fnum(row["fee_after_pnl"]) for row in rows]
    return {
        "day_count": len(rows),
        "positive_day_count": sum(1 for value in pnls if value > 0),
        "worst_day_fee_after_pnl": min(pnls) if pnls else None,
        "best_day_fee_after_pnl": max(pnls) if pnls else None,
        "total_fee_after_pnl": sum(pnls),
        "total_gross_buy_cost": sum(fnum(row["gross_buy_cost"]) for row in rows),
        "total_official_taker_fee": sum(fnum(row["official_taker_fee"]) for row in rows),
        "total_residual_cost": sum(fnum(row["residual_cost"]) for row in rows),
    }


def state_machine_lane(lane_id: str, manifest_path: Path, day_path: Path, priority: int, role: str) -> dict[str, Any]:
    manifest = read_json(manifest_path)
    core = manifest["core_metrics"]
    days = day_stats(day_path)
    return {
        "lane_id": lane_id,
        "priority": priority,
        "role": role,
        "evidence_type": "LOCAL_COMPLETION_STORE_STATE_MACHINE_RESEARCH_ONLY",
        "asset_scope": "BTC" if "btc_" in lane_id.lower() else "MULTIASSET",
        "day_count": len(manifest["days"]),
        "active_markets": core["active_markets"],
        "selected_seed_actions": core["selected_candidate_count"],
        "gross_buy_cost": core["gross_buy_cost"],
        "official_taker_fee": core["official_taker_fee"],
        "fee_after_pnl": core["fee_after_pnl"],
        "gross_cost_roi": core["net_roi"],
        "weighted_pair_cost": core["weighted_pair_cost"],
        "pair_share_rate": core["pair_share_rate"],
        "residual_cost_rate": core["residual_cost_rate"],
        "worst_day_fee_after_pnl": core["worst_day_fee_after_pnl"],
        "positive_day_count": days["positive_day_count"],
        "deployable": False,
        "private_truth_ready": False,
        "promotion_ready": False,
        "recommendation": (
            "PRIMARY_NEXT_REPLAY_REFINEMENT"
            if lane_id == "BTC_CORE_COMPLETION_V1"
            else "SECONDARY_CAPACITY_EXPANSION_AFTER_BTC_CONTRACT"
        ),
        "next_required_action": (
            "derive an ex-ante BTC controller packet from state-machine fields and validate no outcome-label leakage"
            if lane_id == "BTC_CORE_COMPLETION_V1"
            else "split by asset and residual-risk diagnostics before any owner-line executor work"
        ),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                fieldnames.append(key)
                seen.add(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    rescore = read_json(RESCORE_MANIFEST)
    capital = read_json(CAPITAL_LEDGER)
    readiness = read_json(READINESS_GATE)
    audit_pack = read_json(AUDIT_MANIFEST)

    assets = asset_scoreboard()
    filters = filter_scoreboard()
    lanes = [
        state_machine_lane(
            "BTC_CORE_COMPLETION_V1",
            BTC_SM,
            BTC_DAY,
            1,
            "primary replay-backed research strategy candidate",
        ),
        state_machine_lane(
            "MULTIASSET_COMPLETION_V1",
            MULTI_SM,
            MULTI_DAY,
            2,
            "capacity expansion and asset-split research candidate",
        ),
        {
            "lane_id": "BTC_LOW_RESIDUAL_ORACLE_DIAGNOSTIC",
            "priority": 3,
            "role": "diagnostic upper-bound for ex-ante residual predictor research",
            "evidence_type": "OUTCOME_FILTERED_RESCORE_DIAGNOSTIC_NOT_EXECUTABLE",
            "asset_scope": "BTC",
            **next(row for row in filters if row["filter_id"] == "OUTCOME_DIAGNOSTIC_BTC_POSITIVE_RESIDUAL_LE_3PCT"),
            "deployable": False,
            "private_truth_ready": False,
            "promotion_ready": False,
            "recommendation": "USE_ONLY_TO_TRAIN_OR_VALIDATE_EX_ANTE_RESIDUAL_RISK_FEATURES",
            "next_required_action": "rewrite residual/leakage filters into pre-action L1/L2/search-safe features",
        },
        {
            "lane_id": "ETH_LOW_RESIDUAL_ORACLE_DIAGNOSTIC",
            "priority": 4,
            "role": "secondary diagnostic if BTC controller contract stabilizes",
            "evidence_type": "OUTCOME_FILTERED_RESCORE_DIAGNOSTIC_NOT_EXECUTABLE",
            "asset_scope": "ETH",
            **next(row for row in filters if row["filter_id"] == "OUTCOME_DIAGNOSTIC_ETH_POSITIVE_RESIDUAL_LE_5PCT"),
            "deployable": False,
            "private_truth_ready": False,
            "promotion_ready": False,
            "recommendation": "HOLD_UNTIL_BTC_EX_ANTE_CONTROLLER_AND_OWNER_TRUTH_PLAN_EXIST",
            "next_required_action": "derive ETH-specific ex-ante risk controls; do not copy outcome filters",
        },
    ]

    strategy_lanes_csv = OUT / "strategy_lanes.csv"
    asset_csv = OUT / "asset_scoreboard.csv"
    filter_csv = OUT / "filter_scoreboard.csv"
    write_csv(strategy_lanes_csv, lanes)
    write_csv(asset_csv, assets)
    write_csv(filter_csv, filters)

    packet = {
        "schema_version": 1,
        "status": STATUS,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Backtest V1 local strategy shortlist; research-only; no OOS/live authorization",
        "data_window": {
            "days": read_json(BTC_SM)["days"],
            "blocked_days": ["2026-05-14", "2026-05-15", "2026-05-19"],
            "source": "local Backtest V1 search-safe/completion-store artifacts",
        },
        "anchors": {
            "rescore_manifest": {"path": str(RESCORE_MANIFEST), "sha256": sha256_file(RESCORE_MANIFEST), "status": rescore["status"]},
            "capital_ledger": {"path": str(CAPITAL_LEDGER), "sha256": sha256_file(CAPITAL_LEDGER), "status": capital["status"]},
            "readiness_gate": {"path": str(READINESS_GATE), "sha256": sha256_file(READINESS_GATE), "status": readiness["status"]},
            "audit_manifest": {"path": str(AUDIT_MANIFEST), "sha256": sha256_file(AUDIT_MANIFEST), "status": audit_pack.get("status", "OK")},
            "btc_state_machine": {"path": str(BTC_SM), "sha256": sha256_file(BTC_SM), "status": read_json(BTC_SM)["status"]},
            "multiasset_state_machine": {
                "path": str(MULTI_SM),
                "sha256": sha256_file(MULTI_SM),
                "status": read_json(MULTI_SM)["status"],
            },
        },
        "strategy_lanes": lanes,
        "asset_scoreboard_rows": len(assets),
        "filter_scoreboard_rows": len(filters),
        "capital_summary": capital["summary"],
        "readiness_summary": {
            "strategy_research_ready": readiness.get("strategy_research_ready"),
            "shadow_design_ready": readiness.get("shadow_design_ready"),
            "shadow_start_ready": readiness.get("shadow_start_ready"),
            "strategy_promotion_ready": readiness.get("strategy_promotion_ready"),
            "private_truth_ready": readiness.get("private_truth_ready"),
            "deployable": readiness.get("deployable"),
            "live_orders_allowed": readiness.get("live_orders_allowed"),
        },
        "decision": {
            "primary_lane": "BTC_CORE_COMPLETION_V1",
            "why": "BTC has broad 15-day coverage, 4307 active markets, 4147.98 official-fee-after PnL, 8.89% gross-cost ROI, and 2.52% residual cost rate without outcome filtering.",
            "do_not_promote_because": [
                "owner/private truth is absent",
                "BTC parity remains blocked",
                "existing no-order/shadow/public evidence cannot set private_truth_ready",
                "outcome-filtered positive/residual diagnostics are not executable rules",
            ],
            "next_packet": "BTC_CORE_COMPLETION_V1 ex-ante controller contract and leakage audit packet",
        },
        "non_claims": non_claims(),
        "highest_allowed_status": STATUS,
    }
    packet_path = OUT / "XUAN_BACKTEST_V1_STRATEGY_SHORTLIST_PACKET.json"
    packet_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    note_path = OUT / "XUAN_BACKTEST_V1_STRATEGY_SHORTLIST_NOTE.md"
    note_path.write_text(
        "\n".join(
            [
                "# Xuan Backtest V1 Strategy Shortlist",
                "",
                f"Status: `{STATUS}`",
                "",
                "Primary lane: `BTC_CORE_COMPLETION_V1`.",
                "",
                "BTC has the cleanest research profile: broad coverage, positive official-fee-after PnL, and low residual cost rate. Multiasset expands capacity but carries larger residual tails from non-BTC assets. Outcome-filtered low-residual rows are useful diagnostics only and must be rewritten into ex-ante features before any OOS or executor packet.",
                "",
                "No OOS, runner, private key, order, canary, deploy, funding, latest-pointer, private-truth, promotion, live-ready, or deployable claim is authorized.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    preview_path = OUT / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    preview_path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                "echo 'NOT_AUTHORIZED: this shortlist does not start OOS, runner, observer, or live paths.' >&2",
                "echo 'Next reviewed step is BTC_CORE_COMPLETION_V1 ex-ante controller/leakage audit packet.' >&2",
                "exit 66",
                "",
            ]
        ),
        encoding="utf-8",
    )

    artifacts = [
        packet_path,
        note_path,
        strategy_lanes_csv,
        asset_csv,
        filter_csv,
        preview_path,
        RESCORE_MANIFEST,
        RESCORE_ALL,
        RESCORE_TOP,
        CAPITAL_LEDGER,
        READINESS_GATE,
        AUDIT_MANIFEST,
        AUDIT_CSV,
        BTC_SM,
        BTC_DAY,
        MULTI_SM,
        MULTI_DAY,
        HEALTH_SCRIPT,
        Path(__file__).resolve(),
    ]
    manifest = {
        "schema_version": 1,
        "status": STATUS,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "artifacts": [
            {"path": str(path), "sha256": sha256_file(path), "size_bytes": path.stat().st_size}
            for path in artifacts
            if path.exists()
        ],
        "packet_sha256": sha256_file(packet_path),
        "strategy_lanes_csv_sha256": sha256_file(strategy_lanes_csv),
        "asset_scoreboard_csv_sha256": sha256_file(asset_csv),
        "filter_scoreboard_csv_sha256": sha256_file(filter_csv),
        "non_claims": non_claims(),
    }
    manifest_path = OUT / "XUAN_BACKTEST_V1_STRATEGY_SHORTLIST_HASH_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "ok": True,
                "status": STATUS,
                "output_dir": str(OUT),
                "packet_sha256": sha256_file(packet_path),
                "manifest_sha256": sha256_file(manifest_path),
                "primary_lane": packet["decision"]["primary_lane"],
                "lane_count": len(lanes),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
