#!/usr/bin/env python3
"""Build the CE25 BTC5M executable-price state-machine research packet."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
BT_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
EXPORTS = ROOT / "data" / "exports"
OUT = EXPORTS / "ce25_btc5m_state_machine_executable_price_research_packet_20260607"

CD0_PRICE_PACKET = (
    EXPORTS
    / "ce25_btc5m_cd0_price_fill_model_revision_packet_20260607"
    / "CE25_BTC5M_CD0_PRICE_FILL_MODEL_REVISION_PACKET.json"
)
CD5_PRICE_PACKET = (
    EXPORTS
    / "ce25_btc5m_broad_cd5_price_fill_model_comparison_packet_20260607"
    / "CE25_BTC5M_BROAD_CD5_PRICE_FILL_MODEL_COMPARISON_PACKET.json"
)
FULL_L2_PACKET = (
    EXPORTS
    / "ce25_btc5m_cd0_full_l2_fillability_indexed_packet_20260607"
    / "CE25_BTC5M_CD0_FULL_L2_FILLABILITY_INDEXED_PACKET.json"
)
STATE_MACHINE = ROOT / "scripts/run_completion_candidate_state_machine.py"
VALIDATOR = ROOT / "scripts/validate_ce25_btc5m_research_packet_chain.py"
L2_MART = BT_ROOT / "derived/contract_examples/l2_top_aligned_mart_20260502_20260518_l2/l2_top_aligned_mart.duckdb"
L2_MANIFEST = BT_ROOT / "derived/contract_examples/l2_top_aligned_mart_20260502_20260518_l2/L2_TOP_ALIGNED_MART_MANIFEST.json"

STATUS = (
    "KEEP_CE25_BTC5M_STATE_MACHINE_EXECUTABLE_PRICE_RESEARCH_PACKET_PREPARED_"
    "IMPLEMENTATION_REQUIRED_NOT_OOS_READY"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def binding(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if path.exists():
        out.update({"sha256": sha256_file(path), "size": path.stat().st_size})
    return out


def model_by_id(packet: dict[str, Any], model_id: str) -> dict[str, Any]:
    for row in packet.get("model_results") or []:
        if row.get("model_id") == model_id:
            return row
    raise KeyError(model_id)


def write_preview(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo 'NOT_AUTHORIZED: CE25 BTC5M executable-price state-machine research packet is review-only' >&2\n"
        "exit 66\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def render_report(packet: dict[str, Any]) -> str:
    facts = packet["blocking_evidence"]
    lines = [
        "# CE25 BTC5M Executable-Price State-Machine Research Packet",
        "",
        f"Status: `{packet['status']}`",
        "",
        "## Decision",
        "",
        "The current CE25 BTC5M broad/cd0 state-machine family is not allowed to advance on seed-price replay evidence. Both the high-intensity cd0 watch variant and the lower-intensity broad cd5 fallback become negative when the same action sequence is repriced with executable L2 ask/top5 prices.",
        "",
        "## Blocking Evidence",
        "",
        f"- cd0 seed replay net PnL: {facts['cd0']['seed_net_pnl']:.2f}",
        f"- cd0 top5-all executable net PnL: {facts['cd0']['top5_all_net_pnl']:.2f}",
        f"- cd0 ask1 executable net PnL: {facts['cd0']['ask1_net_pnl']:.2f}",
        f"- cd5 seed replay net PnL: {facts['cd5']['seed_net_pnl']:.2f}",
        f"- cd5 top5-all executable net PnL: {facts['cd5']['top5_all_net_pnl']:.2f}",
        f"- cd5 ask1 executable net PnL: {facts['cd5']['ask1_net_pnl']:.2f}",
        "",
        "## Required Implementation Contract",
        "",
        "1. Join candidate rows to L2 top-aligned books before selection using condition_id, side, and ts_ms ASOF semantics.",
        "2. Compute one explicit `execution_px` per candidate from ask1/top5, with source_ts, raw_l2_age_ms, align_lag_ms, size coverage, and slippage fields.",
        "3. Use `execution_px` consistently for price-band gates, max-open-cost, fees, inventory lot price, pair-cost, residual cost, and PnL.",
        "4. Fail closed when L2 is missing/stale, top5 cannot fill target qty, or executable pair-cost/slippage exceeds the registered policy.",
        "5. Re-search participation/residual/official-fee results under this aligned price model before any shadow/OOS discussion.",
        "",
        "This packet is research-only and does not authorize WS, OOS, runner/observer, private keys, orders, canary/live, or promotion claims.",
    ]
    return "\n".join(lines) + "\n"


def write_sha256sums(root: Path, files: list[Path]) -> None:
    lines = [f"{sha256_file(path)}  {path.relative_to(root)}" for path in files]
    (root / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    cd0 = load_json(CD0_PRICE_PACKET)
    cd5 = load_json(CD5_PRICE_PACKET)
    packet = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS,
        "source_bindings": {
            "cd0_price_fill_model_revision_packet": binding(CD0_PRICE_PACKET),
            "broad_cd5_price_fill_model_comparison_packet": binding(CD5_PRICE_PACKET),
            "cd0_full_l2_fillability_indexed_packet": binding(FULL_L2_PACKET),
            "state_machine": binding(STATE_MACHINE),
            "validator": binding(VALIDATOR),
            "l2_top_aligned_mart": binding(L2_MART),
            "l2_top_aligned_manifest": binding(L2_MANIFEST),
            "builder": binding(Path(__file__).resolve()),
        },
        "blocking_evidence": {
            "cd0": {
                "status": cd0.get("status"),
                "seed_net_pnl": model_by_id(cd0, "baseline_replay_seed_px")["net_pnl"],
                "top5_all_net_pnl": model_by_id(cd0, "l2_top5_vwap_all_available")["net_pnl"],
                "top5_within_10c_net_pnl": model_by_id(cd0, "l2_top5_vwap_within_seed_plus_10c_only")[
                    "net_pnl"
                ],
                "ask1_net_pnl": model_by_id(cd0, "l2_ask1_px_when_ask1_size_ge_seed")["net_pnl"],
            },
            "cd5": {
                "status": cd5.get("status"),
                "seed_net_pnl": model_by_id(cd5, "baseline_replay_seed_px")["net_pnl"],
                "top5_all_net_pnl": model_by_id(cd5, "l2_top5_vwap_all_available")["net_pnl"],
                "top5_within_10c_net_pnl": model_by_id(cd5, "l2_top5_vwap_within_seed_plus_10c_only")[
                    "net_pnl"
                ],
                "ask1_net_pnl": model_by_id(cd5, "l2_ask1_px_when_ask1_size_ge_seed")["net_pnl"],
            },
        },
        "implementation_contract": {
            "candidate_l2_join_required_before_selection": True,
            "execution_px_required_before_selection": True,
            "selection_and_pnl_price_source_must_match": True,
            "official_fee_formula": "fee = shares * fee_rate * price * (1 - price)",
            "required_execution_px_models": [
                "ask1_px_when_ask1_size_ge_seed",
                "top5_vwap_when_top5_size_ge_seed",
                "top5_vwap_with_slippage_cap",
            ],
            "minimum_required_outputs": [
                "variant_grid_summary",
                "participation_by_day",
                "pair_cost_distribution",
                "residual_cost_distribution",
                "l2_age_align_lag_distribution",
                "official_fee_net_pnl",
                "non_claims",
            ],
            "fail_closed_if": [
                "l2_missing_or_stale",
                "top5_size_less_than_target_qty",
                "execution_px_not_used_for_lot_price",
                "fee_not_recomputed_from_execution_px",
                "selection_price_and_pnl_price_diverge",
                "readiness_or_live_claim_true",
            ],
        },
        "decision": {
            "current_seed_px_family_blocked": True,
            "cd0_blocked": True,
            "broad_cd5_fallback_blocked": True,
            "primary_blocker": "selection_and_pnl_must_be_researched_under_executable_l2_prices",
            "next_implementation": "add executable-price input/join mode to run_completion_candidate_state_machine.py or create a thin executable-price adapter with identical registry semantics",
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
    packet_path = OUT / "CE25_BTC5M_STATE_MACHINE_EXECUTABLE_PRICE_RESEARCH_PACKET.json"
    report_path = OUT / "CE25_BTC5M_STATE_MACHINE_EXECUTABLE_PRICE_RESEARCH_REPORT.md"
    preview_path = OUT / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    write_json(packet_path, packet)
    report_path.write_text(render_report(packet), encoding="utf-8")
    write_preview(preview_path)
    write_sha256sums(OUT, [packet_path, report_path, preview_path])
    print(json.dumps({"packet": str(packet_path), "status": STATUS}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
