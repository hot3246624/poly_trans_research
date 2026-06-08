#!/usr/bin/env python3
"""Synthesize NAGI BTC5M research into one review-only decision packet."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
EXPORTS = ROOT / "data" / "exports"
OUT = EXPORTS / "nagi_strategy_synthesis_packet_20260608"
BUILDER = ROOT / "scripts/build_nagi_strategy_synthesis_packet.py"

STATUS = (
    "KEEP_NAGI_STRATEGY_SYNTHESIS_REVIEWED_MAKER_QUEUE_PRIVATE_TRUTH_REQUIRED_"
    "NOT_OOS_READY"
)

PACKETS = {
    "fastpair_pivot": EXPORTS
    / "nagi_last60_midprice_fastpair_pivot_packet_20260608"
    / "NAGI_LAST60_MIDPRICE_FASTPAIR_PIVOT_PACKET.json",
    "maker_queue_proxy": EXPORTS
    / "nagi_last60_midprice_maker_queue_proxy_packet_20260608"
    / "NAGI_LAST60_MIDPRICE_MAKER_QUEUE_PROXY_PACKET.json",
    "maker_queue_residual_matrix": EXPORTS
    / "nagi_last60_midprice_maker_queue_residual_matrix_packet_20260608"
    / "NAGI_LAST60_MIDPRICE_MAKER_QUEUE_RESIDUAL_MATRIX_PACKET.json",
    "maker_queue_exhaustive_frontier": EXPORTS
    / "nagi_maker_queue_exhaustive_frontier_packet_20260608"
    / "NAGI_MAKER_QUEUE_EXHAUSTIVE_FRONTIER_PACKET.json",
    "private_maker_requirements": EXPORTS
    / "nagi_private_maker_shadow_requirements_packet_20260608"
    / "NAGI_PRIVATE_MAKER_SHADOW_REQUIREMENTS_PACKET.json",
    "private_maker_approval": EXPORTS
    / "nagi_private_maker_shadow_approval_packet_20260608"
    / "NAGI_PRIVATE_MAKER_SHADOW_APPROVAL_PACKET.json",
    "runner_review": EXPORTS
    / "nagi_private_maker_shadow_runner_review_packet_20260608"
    / "NAGI_PRIVATE_MAKER_SHADOW_RUNNER_REVIEW_PACKET.json",
    "kill_switch_review": EXPORTS
    / "nagi_private_maker_shadow_kill_switch_review_packet_20260608"
    / "NAGI_PRIVATE_MAKER_SHADOW_KILL_SWITCH_REVIEW_PACKET.json",
    "telemetry_contract": EXPORTS
    / "nagi_private_maker_shadow_telemetry_contract_packet_20260608"
    / "NAGI_PRIVATE_MAKER_SHADOW_TELEMETRY_CONTRACT_PACKET.json",
    "ce25_taker_supply": EXPORTS
    / "ce25_btc5m_executable_taker_pair_edge_supply_packet_20260607"
    / "CE25_BTC5M_EXECUTABLE_TAKER_PAIR_EDGE_SUPPLY_PACKET.json",
    "ce25_maker_public_shadow": EXPORTS
    / "ce25_btc5m_maker_queue_public_shadow_staging_packet_20260608"
    / "CE25_BTC5M_MAKER_QUEUE_PUBLIC_SHADOW_STAGING_PACKET.json",
}

ADDITIONAL_PROFILE_SOURCES = {
    "autoresearch_1win_account_rollup": EXPORTS
    / "account_autoresearch_iter_ce25_7win_nagi_1win_20260604_bjt"
    / "account_rollup.tsv",
    "autoresearch_2win_account_rollup": EXPORTS
    / "account_autoresearch_iter_ce25_7win_nagi_2win_20260604_bjt"
    / "account_rollup.tsv",
    "autoresearch_3win_account_rollup": EXPORTS
    / "account_autoresearch_iter_ce25_7win_nagi_3win_20260604_hb1_bjt"
    / "account_rollup.tsv",
    "autoresearch_4win_account_rollup": EXPORTS
    / "account_autoresearch_iter_ce25_7win_nagi_4win_20260604_hb2_bjt"
    / "account_rollup.tsv",
    "autoresearch_4win_proxy_window_rollup": EXPORTS
    / "account_autoresearch_iter_ce25_7win_nagi_4win_20260604_hb2_bjt"
    / "pre_registered_proxy_window_rollup.tsv",
    "autoresearch_4win_proxy_scoreboard": EXPORTS
    / "account_autoresearch_iter_ce25_7win_nagi_4win_20260604_hb2_bjt"
    / "proxy_scoreboard.tsv",
    "autoresearch_4win_proxy_summary": EXPORTS
    / "account_autoresearch_iter_ce25_7win_nagi_4win_20260604_hb2_bjt"
    / "pre_registered_proxy_summary.tsv",
    "rolling24_summary": EXPORTS
    / "nagi_rolling24_20260530_0900_to_20260603_0900_bjt"
    / "rolling24_summary.json",
    "stability_summary": EXPORTS
    / "nagi_stability_20260529_0000_to_20260602_1040_bjt"
    / "stability_summary.json",
    "historical_alpha_handoff": ROOT
    / "docs/research/CE25_NAGI_HISTORICAL_ALPHA_HANDOFF_ZH.md",
}

SCRIPTS = {
    "telemetry_validator": ROOT / "scripts/validate_nagi_private_maker_shadow_telemetry.py",
    "runner_shell": ROOT / "scripts/run_nagi_private_maker_shadow.py",
    "kill_switch_evaluator": ROOT / "scripts/evaluate_nagi_private_maker_shadow_kill_switch.py",
    "exhaustive_frontier_builder": ROOT
    / "scripts/build_nagi_maker_queue_exhaustive_frontier_packet.py",
    "synthesis_builder": BUILDER,
}

NON_CLAIMS = {
    "deployable": False,
    "live_ready": False,
    "maker_fill_proven": False,
    "oos_authorized": False,
    "oos_ready": False,
    "orders_authorized": False,
    "private_key_authorized": False,
    "private_truth_ready": False,
    "queue_priority_proven": False,
    "runner_authorized": False,
    "strategy_promotion_ready": False,
    "ws_authorized": False,
}


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


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_preview(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo 'NOT_AUTHORIZED: NAGI synthesis is review-only; no WS/orders/private keys.' >&2\n"
        "exit 66\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def write_sha256sums(root: Path, files: list[Path]) -> None:
    lines = [f"{sha256_file(path)}  {path.relative_to(root)}" for path in files]
    (root / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def best_by_time(frontier: dict[str, Any], time_id: str) -> dict[str, Any] | None:
    for row in frontier.get("summary", {}).get("frontier_by_time", []):
        if row.get("time_id") == time_id:
            return row
    return None


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    payloads = {name: load_json(path) for name, path in PACKETS.items() if path.exists()}
    missing = [name for name, path in PACKETS.items() if not path.exists()]
    if missing:
        raise FileNotFoundError(f"missing required packets: {missing}")

    pivot = payloads["fastpair_pivot"]
    matrix = payloads["maker_queue_residual_matrix"]
    frontier = payloads["maker_queue_exhaustive_frontier"]
    approval = payloads["private_maker_approval"]
    runner = payloads["runner_review"]
    kill_switch = payloads["kill_switch_review"]
    telemetry_contract = payloads["telemetry_contract"]

    public_fastpair = pivot["public_profile_evidence"]["nagi_fastpair_proxy"]
    account_rollup = pivot["public_profile_evidence"]["nagi_account_rollup_4_window"]
    base_replay = pivot["local_replay_evidence"]["result"]
    exhaustive_summary = frontier["summary"]
    best_anchor = exhaustive_summary["best_nagi_anchor_like_variant"]
    best_fee0 = exhaustive_summary["best_fee0_edge_variant"]
    best_taker = exhaustive_summary["best_taker_fee07_edge_variant"]
    matrix_best = matrix["summary"]["best_fee0_variant"]

    stages = [
        {
            "stage": "public_profile_screen",
            "decision": "KEEP_AS_EXECUTION_TEMPLATE_ONLY",
            "evidence": {
                "wallet": pivot["account"]["wallet"],
                "account_markets_4_window": int(account_rollup["markets"]),
                "account_roi_4_window": float(account_rollup["roi"]),
                "account_bad_pair_cost_share": float(account_rollup["bad_pc_ge_100_share"]),
                "fastpair_proxy_markets": int(public_fastpair["markets"]),
                "fastpair_proxy_roi": float(public_fastpair["roi"]),
                "fastpair_proxy_resid_rate": float(public_fastpair["resid_rate"]),
                "fastpair_proxy_bad_pair_cost_share": float(public_fastpair["bad_pc_ge_100_share"]),
            },
            "blocker": "full account copy rejected; public activity cannot prove private maker execution",
        },
        {
            "stage": "local_book_shadow_translation",
            "decision": "BLOCK_TAKER_OR_SEED_PRICE_COPY",
            "evidence": {
                "base_nagi_template_positive_under_fee0": base_replay[
                    "base_nagi_template_positive_under_fee0"
                ],
                "base_nagi_template_positive_under_official07": base_replay[
                    "base_nagi_template_positive_under_official07"
                ],
                "primary_failure_mode": base_replay["primary_failure_mode"],
            },
            "blocker": "residual and pair completion do not match NAGI public profile under local book-shadow",
        },
        {
            "stage": "maker_queue_proxy",
            "decision": "KEEP_MAKER_FEE0_QUEUE_LANE",
            "evidence": {
                "residual_matrix_variants": matrix["summary"]["variant_count"],
                "fee0_proxy_pass_count": matrix["summary"]["fee0_proxy_pass_count"],
                "best_fee0_variant_id": matrix_best["variant_id"],
                "best_fee0_queue_markets": matrix_best["queue_markets"],
                "best_fee0_queue_market_share": matrix_best["queue_market_share"],
                "best_fee0_edge_qty_sum": matrix_best["queue_edge_qty_sum_fee0"],
                "best_fee0_taker_fee07_edge_qty_sum": matrix_best[
                    "queue_edge_qty_sum_taker_fee07"
                ],
            },
            "blocker": "public SELL touch and visible queue proxy do not prove our order fill",
        },
        {
            "stage": "exhaustive_public_proxy_frontier",
            "decision": "MAP_BROAD_MAKER_FEE0_SUPPLY_AND_BLOCK_TAKER_SCALE",
            "evidence": {
                "variant_count": exhaustive_summary["variant_count"],
                "fee0_scale_pass_count": exhaustive_summary["fee0_scale_pass_count"],
                "fee0_high_coverage_pass_count": exhaustive_summary[
                    "fee0_high_coverage_pass_count"
                ],
                "taker_fee07_scale_pass_count": exhaustive_summary[
                    "taker_fee07_scale_pass_count"
                ],
                "best_fee0_variant_id": best_fee0["variant_id"],
                "best_fee0_queue_markets": best_fee0["queue_markets"],
                "best_fee0_queue_market_share": best_fee0["queue_market_share"],
                "best_fee0_edge_qty_sum": best_fee0["queue_edge_qty_sum_fee0"],
                "nagi_anchor_variant_id": best_anchor["variant_id"],
                "nagi_anchor_queue_markets": best_anchor["queue_markets"],
                "nagi_anchor_edge_qty_sum": best_anchor["queue_edge_qty_sum_fee0"],
                "best_taker_variant_id": best_taker["variant_id"],
                "best_taker_queue_markets": best_taker["queue_markets"],
                "best_taker_edge_qty_sum": best_taker["queue_edge_qty_sum_taker_fee07"],
            },
            "blocker": "maker fee0 supply is broad; taker fee07 has no scalable lane",
        },
        {
            "stage": "private_shadow_readiness",
            "decision": "PREPARE_ONLY_NOT_EXECUTION_READY",
            "evidence": {
                "approval_status": approval["status"],
                "runner_review_status": runner["status"],
                "kill_switch_review_status": kill_switch["status"],
                "telemetry_contract_status": telemetry_contract["status"],
                "telemetry_contract_ok": telemetry_contract["ok"],
                "execution_ready": approval["execution_readiness"]["execution_ready"],
                "blockers": approval["execution_readiness"]["blockers"],
            },
            "blocker": "exact approval, private bindings, actual order client, runtime kill switch, and own maker telemetry are missing",
        },
    ]

    candidate_lanes = [
        {
            "lane_id": "NAGI_ANCHOR_LAST60_YES_35_50",
            "role": "closest public-profile template",
            "policy_hint": {
                "market_scope": "BTC 5m current/live only",
                "decision_window": "last60",
                "side": "YES",
                "bid_price_band": [0.35, 0.50],
                "pair_cost_cap": 0.995,
                "maker_only": True,
                "maker_fee_required": 0,
            },
            "public_proxy_evidence": {
                "variant_id": matrix_best["variant_id"],
                "queue_markets": matrix_best["queue_markets"],
                "queue_market_share": matrix_best["queue_market_share"],
                "fee0_edge_qty_sum": matrix_best["queue_edge_qty_sum_fee0"],
                "taker_fee07_edge_qty_sum": matrix_best["queue_edge_qty_sum_taker_fee07"],
            },
            "status": "PRIMARY_PRIVATE_SHADOW_CANDIDATE_REVIEW_ONLY",
        },
        {
            "lane_id": "BROAD_MAKER_FULL300_YES_35_50",
            "role": "highest local public-proxy fee0 maker supply, not NAGI-specific",
            "policy_hint": {
                "market_scope": "BTC 5m current/live only",
                "decision_window": "full300",
                "side": "YES",
                "bid_price_band": [0.35, 0.50],
                "pair_cost_cap": 1.000,
                "maker_only": True,
                "maker_fee_required": 0,
            },
            "public_proxy_evidence": {
                "variant_id": best_fee0["variant_id"],
                "queue_markets": best_fee0["queue_markets"],
                "queue_market_share": best_fee0["queue_market_share"],
                "fee0_edge_qty_sum": best_fee0["queue_edge_qty_sum_fee0"],
                "taker_fee07_edge_qty_sum": best_fee0["queue_edge_qty_sum_taker_fee07"],
            },
            "status": "SECONDARY_DESIGN_CANDIDATE_REVIEW_ONLY",
        },
        {
            "lane_id": "LAST60_HIGH_BAND_YES_65_80",
            "role": "strong last60 public-proxy edge outside NAGI anchor band",
            "policy_hint": {
                "market_scope": "BTC 5m current/live only",
                "decision_window": "last60",
                "side": "YES",
                "bid_price_band": [0.65, 0.80],
                "pair_cost_cap": 1.000,
                "maker_only": True,
                "maker_fee_required": 0,
            },
            "public_proxy_evidence": best_by_time(frontier, "last60"),
            "status": "EXPLORATORY_ONLY_PRIVATE_TRUTH_REQUIRED",
        },
        {
            "lane_id": "TAKER_OR_TAKER_FEE07_PAIR_COPY",
            "role": "rejected lane",
            "public_proxy_evidence": {
                "taker_fee07_scale_pass_count": exhaustive_summary["taker_fee07_scale_pass_count"],
                "best_taker_variant_id": best_taker["variant_id"],
                "best_taker_queue_markets": best_taker["queue_markets"],
            },
            "status": "BLOCKED_NO_SCALE",
        },
    ]

    packet = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS,
        "highest_allowed_status": "local research/review-only, not OOS-ready",
        "decision": {
            "strategy_stage": "CONVERGED_TO_MAKER_QUEUE_PRIVATE_TELEMETRY_GATE",
            "ce25_taker_replication_blocked": True,
            "nagi_full_account_copy_rejected": True,
            "nagi_taker_copy_rejected": True,
            "maker_fee0_public_proxy_lane_exists": True,
            "taker_fee07_scale_lane_exists": False,
            "private_shadow_discussion_allowed": "review-only requirements/design, not execution",
            "next_step": "prepare exact review-only post-only maker order-client + runtime kill-switch implementation packet, then require explicit user approval and own maker telemetry before any OOS discussion",
        },
        "official_fee_boundary": {
            "crypto_taker_fee_rate": 0.07,
            "maker_fee_rate": 0.0,
            "formula": "C * feeRate * p * (1-p)",
            "interpretation": "NAGI evidence only survives as maker/no-fee public proxy; official taker fee blocks scale.",
        },
        "evidence_stages": stages,
        "additional_public_profile_findings": {
            "stability_broadening": {
                "interpretation": "NAGI account-level ROI weakens as public-profile coverage expands; this supports rejecting full-account copy.",
                "roi_path_by_window_count": {
                    "1_window": 0.0152,
                    "2_window": 0.0132,
                    "3_window": 0.0093,
                    "4_window": 0.0068,
                },
                "four_window_rollup": {
                    "markets": 1123,
                    "buy_actual": 798783.27,
                    "cash_pnl": 5399.32,
                    "pair_cost": 0.9747,
                    "resid_rate": 0.1210,
                    "bad_pc_ge_100_share": 0.4706,
                },
            },
            "fastpair_window_caveat": {
                "interpretation": "The NAGI fastpair bucket is 3/4 profitable but has one pair_cost>1 losing window.",
                "window_cash_pnl": [97.99, -392.04, 2172.76, 1090.41],
                "window_pair_cost": [0.9749, 1.0003, 0.9559, 0.9534],
            },
            "side_split_profile_clues": [
                {
                    "bucket": "last_60s|35-50|UP",
                    "markets": 216,
                    "buy_actual": 152633.74,
                    "cash_pnl": 5143.55,
                    "roi": 0.0337,
                    "pair_cost": 0.9547,
                    "resid_rate": 0.0849,
                    "bad_pc_ge_100_share": 0.4343,
                    "claim_level": "public-profile clue only",
                },
                {
                    "bucket": "last_60s|50-65|DOWN",
                    "markets": 141,
                    "buy_actual": 103067.16,
                    "cash_pnl": 3592.29,
                    "roi": 0.0349,
                    "pair_cost": 0.9613,
                    "resid_rate": 0.0655,
                    "bad_pc_ge_100_share": 0.4287,
                    "claim_level": "public-profile clue only",
                },
            ],
            "slowpair_control": {
                "interpretation": "Slowpair control is materially weaker than fastpair and should not be promoted.",
                "markets": 197,
                "buy_actual": 142205.81,
                "cash_pnl": 795.21,
                "roi": 0.0056,
                "pair_cost": 0.9692,
                "resid_rate": 0.0943,
                "bad_pc_ge_100_share": 0.4980,
            },
            "rebate_and_pair_cost_caveat": {
                "interpretation": "Some contiguous NAGI windows are negative before or despite rebates, reinforcing that public profile is not a stable full-account strategy.",
                "rolling24_bad_window": {
                    "window": "2026-06-02 09:00 -> 2026-06-03 09:00 BJT",
                    "final_pnl": -549.36,
                    "pair_cost": 1.0095,
                    "pair_pnl": -1508.74,
                    "net_incl_rebate": 123.29,
                },
                "stability_bad_slice": {
                    "window": "2026-06-02 BJT slice",
                    "final_pnl": -1460.72,
                    "net_incl_rebate": -535.41,
                    "pair_cost": 1.0102,
                    "max_market_loss": -565.35,
                },
            },
        },
        "candidate_lanes": candidate_lanes,
        "private_truth_gate": {
            "minimum_own_maker_filled_markets": 100,
            "minimum_own_maker_filled_actions": 500,
            "maker_fee_must_equal": 0,
            "counted_taker_fill_share_must_equal": 0,
            "positive_realized_maker_edge_after_fees_required": True,
            "residual_cost_rate_max": 0.20,
            "pair_cost_p50_max": 0.995,
            "public_touch_to_own_fill_conversion_required": True,
        },
        "automation_next_iteration_semantics": {
            "monitor_roots": [
                str(OUT),
                str(EXPORTS / "nagi_private_maker_shadow_approval_packet_20260608"),
                str(EXPORTS / "nagi_private_maker_shadow_runner_review_packet_20260608"),
                str(EXPORTS / "nagi_private_maker_shadow_kill_switch_review_packet_20260608"),
                str(EXPORTS / "nagi_private_maker_shadow_telemetry_contract_packet_20260608"),
                str(EXPORTS / "nagi_post_only_maker_order_client_design_packet_20260608"),
            ],
            "dont_notify_when": [
                "SHA manifests stable",
                "non_claims remain false for orders/OOS/private truth/readiness",
                "no new exact approval or runner implementation request exists",
            ],
            "notify_when": [
                "source or packet hashes drift",
                "any non-claim flips true",
                "actual order-client or runtime kill-switch implementation appears and needs review",
                "user explicitly asks for exact approval packet preparation",
            ],
            "next_build_packet": "nagi_dry_run_only_order_client_adapter_review_packet_20260608",
            "execution_authorized": False,
        },
        "source_bindings": {
            "packets": {name: binding(path) for name, path in PACKETS.items()},
            "additional_public_profile_sources": {
                name: binding(path) for name, path in ADDITIONAL_PROFILE_SOURCES.items()
            },
            "scripts": {name: binding(path) for name, path in SCRIPTS.items()},
        },
        "non_claims": NON_CLAIMS,
    }

    packet_path = OUT / "NAGI_STRATEGY_SYNTHESIS_PACKET.json"
    report_path = OUT / "NAGI_STRATEGY_SYNTHESIS_REPORT.md"
    preview_path = OUT / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"

    write_json(packet_path, packet)
    report_path.write_text(
        "\n".join(
            [
                "# NAGI Strategy Synthesis",
                "",
                f"Status: `{STATUS}`",
                "",
                "## Decision",
                "",
                "- Stage: `CONVERGED_TO_MAKER_QUEUE_PRIVATE_TELEMETRY_GATE`",
                "- Taker / official-fee07 scale lane: blocked",
                "- Maker fee0 public-proxy lane: exists, broad, and not limited to the NAGI last60 anchor",
                "- Execution status: review-only; no OOS, no orders, no private truth claim",
                "",
                "## Key Evidence",
                "",
                f"- NAGI account rollup: {account_rollup['markets']} markets, ROI {float(account_rollup['roi']):.4%}, bad pair-cost share {float(account_rollup['bad_pc_ge_100_share']):.2%}",
                f"- NAGI fastpair public bucket: {public_fastpair['markets']} markets, ROI {float(public_fastpair['roi']):.4%}, residual {float(public_fastpair['resid_rate']):.2%}",
                "- Public-profile stability caveat: account ROI weakens from 1.52% at 1 window to 0.68% at 4 windows; fastpair has one pair-cost>1 losing window.",
                "- Side-split clues: last60 UP 35-50 and DOWN 50-65 are stronger public-profile buckets, but both retain bad pair-cost share above 42%.",
                f"- Base local book-shadow: fee0 positive={base_replay['base_nagi_template_positive_under_fee0']}, official07 positive={base_replay['base_nagi_template_positive_under_official07']}",
                f"- Residual matrix best: `{matrix_best['variant_id']}`, {matrix_best['queue_markets']} queue markets, fee0 edge qty {matrix_best['queue_edge_qty_sum_fee0']}, taker07 edge qty {matrix_best['queue_edge_qty_sum_taker_fee07']}",
                f"- Exhaustive frontier: {exhaustive_summary['variant_count']} variants, fee0 scale passes {exhaustive_summary['fee0_scale_pass_count']}, taker07 scale passes {exhaustive_summary['taker_fee07_scale_pass_count']}",
                f"- Best broad fee0 lane: `{best_fee0['variant_id']}`, {best_fee0['queue_markets']} queue markets, fee0 edge qty {best_fee0['queue_edge_qty_sum_fee0']}",
                f"- Telemetry contract: `{telemetry_contract['status']}`, fixtures ok={telemetry_contract['ok']}",
                "",
                "## Next Review-Only Step",
                "",
                "Implement a dry-run-only order-client adapter review packet. This still does not authorize execution; it only proves that future order intents would be rejected or logged under the bound telemetry contract.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    write_preview(preview_path)
    write_sha256sums(OUT, [packet_path, report_path, preview_path])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
