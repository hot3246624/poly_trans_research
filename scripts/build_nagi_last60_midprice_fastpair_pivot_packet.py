#!/usr/bin/env python3
"""Build a review-only NAGI last60 midprice fastpair pivot packet."""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
BT_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
EXPORTS = ROOT / "data" / "exports"
OUT = EXPORTS / "nagi_last60_midprice_fastpair_pivot_packet_20260608"

ACCOUNT_EXPORT = EXPORTS / "account_autoresearch_iter_ce25_7win_nagi_4win_20260604_hb2_bjt"
FEE0_RUN = (
    BT_ROOT
    / "derived/ce25_nagi_shadow_policy_autoresearch_v0/nagi_last60_midprice_fastpair_pivot_fee0_20260608"
)
OFFICIAL07_RUN = (
    BT_ROOT
    / "derived/ce25_nagi_shadow_policy_autoresearch_v0/nagi_last60_midprice_fastpair_pivot_official07_20260608"
)

HANDOFF_DOC = ROOT / "docs/research/CE25_NAGI_HISTORICAL_ALPHA_HANDOFF_ZH.md"
TRANSITION_DOC = ROOT / "docs/research/CE25_NAGI_STRATEGY_TRANSITION_PLAN_20260604_ZH.md"
STRATEGY_INPUT = ROOT / "configs/ce25_nagi/CE25_NAGI_STRATEGY_INPUT_v0.json"
RUNNER = ROOT / "scripts/run_ce25_nagi_shadow_policy_runner.py"
BUILDER = ROOT / "scripts/build_nagi_last60_midprice_fastpair_pivot_packet.py"
CE25_TAKER_SUPPLY_PACKET = (
    EXPORTS
    / "ce25_btc5m_executable_taker_pair_edge_supply_packet_20260607"
    / "CE25_BTC5M_EXECUTABLE_TAKER_PAIR_EDGE_SUPPLY_PACKET.json"
)
CE25_MAKER_QUEUE_STAGING_PACKET = (
    EXPORTS
    / "ce25_btc5m_maker_queue_public_shadow_staging_packet_20260608"
    / "CE25_BTC5M_MAKER_QUEUE_PUBLIC_SHADOW_STAGING_PACKET.json"
)

STATUS = (
    "BLOCKED_NAGI_LAST60_MIDPRICE_FASTPAIR_PUBLIC_PROFILE_STRONG_"
    "LOCAL_BOOK_SHADOW_NEGATIVE_REDESIGN_REQUIRED_NOT_OOS_READY"
)


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


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


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


def as_float(row: dict[str, Any], key: str) -> float:
    try:
        return float(row.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def pick_pre_registered(rows: list[dict[str, str]], proxy_id: str) -> dict[str, Any]:
    for row in rows:
        if row.get("proxy_id") == proxy_id:
            return dict(row)
    raise KeyError(proxy_id)


def pick_safe_candidates(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if row.get("account") != "nagi":
            continue
        value = row.get("value") or ""
        if "35-50" in value or "50-65" in value or "last_60s" in value:
            out.append(dict(row))
    return out


def load_branch_rows(run_root: Path, fee_label: str) -> list[dict[str, Any]]:
    rows = read_csv(run_root / "branch_control_summary.csv")
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "fee_label": fee_label,
                "variant_id": row.get("variant_id"),
                "branch_id": row.get("branch_id"),
                "classification": row.get("classification"),
                "active_markets": int(float(row.get("active_markets") or 0)),
                "seed_actions": int(float(row.get("seed_actions") or 0)),
                "pair_actions": int(float(row.get("pair_actions") or 0)),
                "paired_market_count": int(float(row.get("paired_market_count") or 0)),
                "residual_actions": int(float(row.get("residual_actions") or 0)),
                "fee_after_pnl": round(float(row.get("fee_after_pnl") or 0.0), 6),
                "net_roi": round(float(row.get("net_roi") or 0.0), 6),
                "net_pair_cost_wavg": round(float(row.get("net_pair_cost_wavg") or 0.0), 6),
                "residual_qty_rate": round(float(row.get("residual_qty_rate") or 0.0), 6),
                "bad_pair_cost_action_share": round(float(row.get("bad_pair_cost_action_share") or 0.0), 6),
                "target_qty": round(float(row.get("target_qty") or 0.0), 6),
                "completion_sla_s": 15.0,
            }
        )
    return out


def summarize_replay(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    best = max(rows, key=lambda r: float(r["fee_after_pnl"]))
    positive = [r for r in rows if float(r["fee_after_pnl"]) > 0]
    return {
        "variant_count": len(rows),
        "positive_variant_count": len(positive),
        "best_variant_id": best["variant_id"],
        "best_branch_id": best["branch_id"],
        "best_fee_after_pnl": best["fee_after_pnl"],
        "best_net_roi": best["net_roi"],
        "best_residual_qty_rate": best["residual_qty_rate"],
        "classifications": sorted({str(r["classification"]) for r in rows}),
    }


def write_preview(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo 'NOT_AUTHORIZED: NAGI pivot packet is review-only; no WS, OOS, or orders are authorized.' >&2\n"
        "exit 66\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def write_sha256sums(root: Path, files: list[Path]) -> None:
    lines = [f"{sha256_file(path)}  {path.relative_to(root)}" for path in files]
    (root / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_packet() -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    pre_registered = read_tsv(ACCOUNT_EXPORT / "pre_registered_proxy_summary.tsv")
    safe_candidates = read_tsv(ACCOUNT_EXPORT / "proxy_safe_candidates.tsv")
    account_rollup = read_tsv(ACCOUNT_EXPORT / "account_rollup.tsv")
    nagi_rollup = next(row for row in account_rollup if row.get("account") == "nagi")
    nagi_fastpair = pick_pre_registered(pre_registered, "nagi_last60_first35_50_fastpair")
    nagi_slowpair = pick_pre_registered(pre_registered, "nagi_last60_first35_50_slowpair_control")
    nagi_safe = pick_safe_candidates(safe_candidates)
    replay_rows = [
        *load_branch_rows(FEE0_RUN, "fee0_public_profile_control"),
        *load_branch_rows(OFFICIAL07_RUN, "official_fee_rate_0p07"),
    ]
    fee0_rows = [r for r in replay_rows if r["fee_label"] == "fee0_public_profile_control"]
    official_rows = [r for r in replay_rows if r["fee_label"] == "official_fee_rate_0p07"]
    ce25_taker = load_json(CE25_TAKER_SUPPLY_PACKET) if CE25_TAKER_SUPPLY_PACKET.exists() else {}
    ce25_maker = load_json(CE25_MAKER_QUEUE_STAGING_PACKET) if CE25_MAKER_QUEUE_STAGING_PACKET.exists() else {}

    packet = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS,
        "strategy_owner_line": "CE25_NAGI_RESEARCH",
        "account": {
            "handle": "nagi777",
            "wallet": "0xbf337426aa856996b8bb79b238345dd1a0276bf7",
            "polymarket_profile_url": "https://polymarket.com/zh/@nagi777?tab=positions",
            "local_docs_primary": True,
        },
        "source_bindings": {
            "handoff_doc": binding(HANDOFF_DOC),
            "transition_doc": binding(TRANSITION_DOC),
            "strategy_input": binding(STRATEGY_INPUT),
            "account_autoresearch_export_dir": binding(ACCOUNT_EXPORT),
            "pre_registered_proxy_summary": binding(ACCOUNT_EXPORT / "pre_registered_proxy_summary.tsv"),
            "proxy_safe_candidates": binding(ACCOUNT_EXPORT / "proxy_safe_candidates.tsv"),
            "account_rollup": binding(ACCOUNT_EXPORT / "account_rollup.tsv"),
            "runner": binding(RUNNER),
            "fee0_replay_manifest": binding(FEE0_RUN / "AUTORESEARCH_MANIFEST.json"),
            "official07_replay_manifest": binding(OFFICIAL07_RUN / "AUTORESEARCH_MANIFEST.json"),
            "ce25_executable_taker_supply_packet": binding(CE25_TAKER_SUPPLY_PACKET),
            "ce25_maker_queue_staging_packet": binding(CE25_MAKER_QUEUE_STAGING_PACKET),
            "builder": binding(BUILDER),
        },
        "public_profile_evidence": {
            "nagi_account_rollup_4_window": nagi_rollup,
            "nagi_fastpair_proxy": nagi_fastpair,
            "nagi_slowpair_control": nagi_slowpair,
            "nagi_safe_candidates": nagi_safe,
            "interpretation": {
                "full_account_copy_rejected": True,
                "execution_template_keep": True,
                "pair_delay_is_outcome_not_entry_signal": True,
                "first_price_requires_own_executable_price_translation": True,
            },
        },
        "local_replay_evidence": {
            "engine": "book_shadow",
            "mode": "summary_only",
            "fee0_summary": summarize_replay(fee0_rows),
            "official07_summary": summarize_replay(official_rows),
            "branch_rows": replay_rows,
            "result": {
                "base_nagi_template_positive_under_fee0": summarize_replay(fee0_rows).get(
                    "positive_variant_count"
                )
                > 0,
                "base_nagi_template_positive_under_official07": summarize_replay(official_rows).get(
                    "positive_variant_count"
                )
                > 0,
                "primary_failure_mode": "high_residual_and_insufficient_pair_completion_under_local_book_shadow",
            },
        },
        "ce25_failure_context": {
            "taker_high_participation_blocked": True,
            "taker_positive_market_share": (ce25_taker.get("supply_ceiling") or {}).get(
                "positive_net_edge_market_share"
            ),
            "maker_queue_public_proxy_exists": True,
            "maker_queue_positive_edge_queue_fill_market_share": (ce25_maker.get("aggregate") or {}).get(
                "positive_edge_queue_fill_market_share"
            ),
        },
        "decision": {
            "pivot_to_nagi_learning_is_justified": True,
            "copy_nagi_full_account_allowed": False,
            "nagi_public_profile_bucket_is_strong": True,
            "nagi_base_book_shadow_template_passes": False,
            "nagi_strategy_ready": False,
            "oos_discussion_allowed": False,
            "next_step": "nagi_replay_redesign_matrix_pair_completion_and_residual_killer_review_only",
        },
        "redesign_queue": [
            {
                "id": "NAGI_MAKER_QUEUE_TRANSLATION",
                "reason": "Public profile fee0/maker-like success does not survive local book-shadow base template.",
                "required_work": "Translate NAGI to maker queue proxy using bid-side placement, queue-ahead, public SELL touch, and own-fill non-claims.",
            },
            {
                "id": "NAGI_RESIDUAL_KILLER_FASTPAIR",
                "reason": "Base branches have residual_qty_rate around 46%-53%, far above NAGI profile 7%-10%.",
                "required_work": "Force same-row or sub-second pair completion, smaller target_qty, stricter entry paircap/opposite depth, and hard residual stop.",
            },
            {
                "id": "NAGI_PROFILE_TO_REPLAY_COVERAGE_BRIDGE",
                "reason": "Public profile evidence covers 4 windows; local replay window is 2026-05-02..05-18 and cannot directly reproduce June account windows.",
                "required_work": "Keep claims local-replay/review-only until matching source truth exists for NAGI public windows.",
            },
        ],
        "highest_allowed_status": "review-only NAGI pivot, not OOS-ready",
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
    return packet, nagi_safe, replay_rows


def render_report(packet: dict[str, Any]) -> str:
    fast = packet["public_profile_evidence"]["nagi_fastpair_proxy"]
    fee0 = packet["local_replay_evidence"]["fee0_summary"]
    official = packet["local_replay_evidence"]["official07_summary"]
    lines = [
        "# NAGI Last60 Midprice Fastpair Pivot",
        "",
        f"Status: `{packet['status']}`",
        "",
        "## Decision",
        "",
        "NAGI is worth studying as a maker/no-fee fast-pair execution template, but the current base local book-shadow translation fails. Do not copy the full account and do not move to OOS.",
        "",
        "## Public Profile Evidence",
        "",
        f"- Proxy: `{fast.get('proxy_id')}`",
        f"- Markets: {fast.get('markets')}",
        f"- Buy actual: {fast.get('buy_actual')}",
        f"- Cash PnL: {fast.get('cash_pnl')}",
        f"- ROI: {fast.get('roi')}",
        f"- Pair cost: {fast.get('pair_cost')}",
        f"- Residual rate: {fast.get('resid_rate')}",
        f"- Bad pair-cost >= 1 share: {fast.get('bad_pc_ge_100_share')}",
        "",
        "## Local Book-Shadow Replay",
        "",
        f"- Fee 0 positive variants: {fee0.get('positive_variant_count')} / {fee0.get('variant_count')}",
        f"- Fee 0 best PnL: {fee0.get('best_fee_after_pnl')}",
        f"- Fee 0 best residual qty rate: {fee0.get('best_residual_qty_rate')}",
        f"- Official 0.07 fee positive variants: {official.get('positive_variant_count')} / {official.get('variant_count')}",
        f"- Official 0.07 best PnL: {official.get('best_fee_after_pnl')}",
        f"- Official 0.07 best residual qty rate: {official.get('best_residual_qty_rate')}",
        "",
        "## Next Step",
        "",
        "`nagi_replay_redesign_matrix_pair_completion_and_residual_killer_review_only`",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    packet, safe_rows, replay_rows = build_packet()
    packet_path = OUT / "NAGI_LAST60_MIDPRICE_FASTPAIR_PIVOT_PACKET.json"
    report_path = OUT / "NAGI_LAST60_MIDPRICE_FASTPAIR_PIVOT_REPORT.md"
    safe_path = OUT / "nagi_public_profile_safe_candidates.csv"
    replay_path = OUT / "nagi_local_book_shadow_replay_comparison.csv"
    preview_path = OUT / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"

    write_json(packet_path, packet)
    report_path.write_text(render_report(packet), encoding="utf-8")
    write_csv(safe_path, safe_rows)
    write_csv(replay_path, replay_rows)
    write_preview(preview_path)
    write_sha256sums(OUT, [packet_path, report_path, safe_path, replay_path, preview_path])
    print(
        json.dumps(
            {
                "packet": str(packet_path),
                "status": packet["status"],
                "decision": packet["decision"],
                "local_replay": packet["local_replay_evidence"],
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
