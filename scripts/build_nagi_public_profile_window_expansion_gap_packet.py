#!/usr/bin/env python3
"""Inventory local NAGI public-profile windows and public-only expansion gaps."""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
EXPORTS = ROOT / "data" / "exports"
OUT = EXPORTS / "nagi_public_profile_window_expansion_gap_packet_20260608"

WALLET = "0xbf337426aa856996b8bb79b238345dd1a0276bf7"
STATUS = (
    "KEEP_NAGI_PUBLIC_PROFILE_WINDOW_EXPANSION_GAP_REVIEWED_"
    "PUBLIC_ONLY_CANNOT_REPLACE_PRIVATE_QUEUE_TRUTH_NOT_OOS_READY"
)

BUILDER = ROOT / "scripts/build_nagi_public_profile_window_expansion_gap_packet.py"
AUTORESEARCH_ROOT = EXPORTS / "account_autoresearch_iter_ce25_7win_nagi_4win_20260604_hb2_bjt"
AUTORESEARCH_DATA_SOURCES = AUTORESEARCH_ROOT / "data_sources.json"
AUTORESEARCH_ACCOUNT_ROLLUP = AUTORESEARCH_ROOT / "account_rollup.tsv"
AUTORESEARCH_PROXY_SUMMARY = AUTORESEARCH_ROOT / "pre_registered_proxy_summary.tsv"
LATEST_PROFILE_ROOT = EXPORTS / "profile_nagi_20260604_1110_to_20260608_1110_bjt"
LATEST_PROFILE_SUMMARY = LATEST_PROFILE_ROOT / "summary.json"
LATEST_PROFILE_SEQUENCE = LATEST_PROFILE_ROOT / "ce25_market_sequence.csv"
REVERSE_PACKET_ROOT = EXPORTS / "nagi777_reverse_engineering_packet_20260608"
REVERSE_PACKET = REVERSE_PACKET_ROOT / "NAGI777_REVERSE_ENGINEERING_PACKET.json"
REVERSE_DAILY = REVERSE_PACKET_ROOT / "nagi777_daily_summary.csv"
REVERSE_CANDIDATES = REVERSE_PACKET_ROOT / "nagi777_profile_candidate_buckets.csv"
SYNTHESIS_PACKET = (
    EXPORTS / "nagi_strategy_synthesis_packet_20260608" / "NAGI_STRATEGY_SYNTHESIS_PACKET.json"
)
QUEUE_SENSITIVITY_PACKET = (
    EXPORTS / "nagi_queue_model_sensitivity_packet_20260608" / "NAGI_QUEUE_MODEL_SENSITIVITY_PACKET.json"
)
SAMPLE_SIZE_PACKET = (
    EXPORTS
    / "nagi_private_shadow_sample_size_plan_packet_20260608"
    / "NAGI_PRIVATE_SHADOW_SAMPLE_SIZE_PLAN_PACKET.json"
)
REVERSE_REPORT = ROOT / "docs/research/NAGI777_REVERSE_ENGINEERING_20260608_ZH.md"


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
        out.update({"file_count": sum(1 for item in path.rglob("*") if item.is_file())})
    return out


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


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
        "echo 'NOT_AUTHORIZED: NAGI public-profile gap packet is review-only.' >&2\n"
        "exit 66\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def write_sha256sums(root: Path, files: list[Path]) -> None:
    lines = [f"{sha256_file(path)}  {path.relative_to(root)}" for path in files]
    (root / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_bjt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def seconds_between(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def row_count(path: Path) -> int | None:
    if not path.exists():
        return None
    with path.open(newline="", encoding="utf-8") as f:
        return max(sum(1 for _ in f) - 1, 0)


def summary_record(path: Path) -> dict[str, Any] | None:
    try:
        payload = read_json(path)
    except Exception:
        return None
    if isinstance(payload, list):
        if not payload:
            return None
        payload = payload[0]
    if not isinstance(payload, dict):
        return None
    window = payload.get("window") or {}
    cash_pnl = payload.get("cash_pnl", payload.get("cash_pnl_total", payload.get("with_buy_cash_ex_no_condition_rebate")))
    market_count = payload.get("market_count", payload.get("with_buy_markets", payload.get("paired_market_count")))
    pair_cost = payload.get("avg_pair_cost_weighted", payload.get("actual_pair_cost"))
    resid_rate = payload.get("resid_rate", payload.get("resid_rate_on_buy_qty"))
    activity_rows = payload.get("activity_rows", payload.get("row_count"))
    return {
        "summary_path": str(path),
        "root": str(path.parent),
        "user": payload.get("user"),
        "window_start_bjt": window.get("start_bjt"),
        "window_end_bjt": window.get("end_bjt"),
        "window_start_iso": window.get("start_iso"),
        "window_end_iso": window.get("end_iso"),
        "activity_rows": activity_rows,
        "market_count": market_count,
        "buy_actual": payload.get("buy_actual"),
        "cash_pnl": cash_pnl,
        "roi": (cash_pnl / payload["buy_actual"]) if cash_pnl is not None and payload.get("buy_actual") else None,
        "pair_cost": pair_cost,
        "resid_rate": resid_rate,
        "fee": payload.get("fee"),
        "has_sequence_csv": (path.parent / "ce25_market_sequence.csv").exists(),
        "sequence_row_count": row_count(path.parent / "ce25_market_sequence.csv"),
        "summary_sha256": sha256_file(path),
    }


def classify_profile(record: dict[str, Any]) -> str:
    root = record["root"]
    if record.get("has_sequence_csv") and record.get("activity_rows") is not None:
        return "profile_with_market_sequence"
    if "deep_compare_nagi" in root:
        return "deep_compare_summary_only"
    if "deep_nagi777" in root:
        return "deep_slice_summary_only"
    return "summary_only"


def inventory_profiles() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(EXPORTS.rglob("summary.json")):
        if "nagi" not in str(path).lower():
            continue
        rec = summary_record(path)
        if not rec:
            continue
        user = rec.get("user")
        # Some deep summaries omit user, but their path is explicitly nagi.
        if user and str(user).lower() != WALLET:
            continue
        rec["profile_class"] = classify_profile(rec)
        rows.append(rec)
    return rows


def data_source_nagi_windows() -> list[dict[str, Any]]:
    if not AUTORESEARCH_DATA_SOURCES.exists():
        return []
    data = read_json(AUTORESEARCH_DATA_SOURCES)
    out: list[dict[str, Any]] = []
    for source in data.get("sources", []):
        if source.get("account") != "nagi":
            continue
        window = source.get("window") or {}
        out.append(
            {
                "source_kind": "autoresearch_4win_source",
                "profile_path": source.get("profile_path"),
                "summary_path": source.get("summary_path"),
                "window_start_bjt": window.get("start_bjt"),
                "window_end_bjt": window.get("end_bjt"),
                "window_start_iso": window.get("start_iso"),
                "window_end_iso": window.get("end_iso"),
            }
        )
    return out


def cache_only_dirs(profile_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    profile_roots = {Path(row["root"]).resolve() for row in profile_rows}
    out: list[dict[str, Any]] = []
    candidate_roots = {path for path in EXPORTS.rglob("*") if path.is_dir() and "nagi" in str(path).lower()}
    for root in sorted(candidate_roots):
        if not root.is_dir():
            continue
        if root.resolve() in profile_roots:
            continue
        if "cache" in root.parts[-1:]:
            continue
        cache = root / "cache"
        if cache.is_dir():
            out.append(
                {
                    "root": str(root),
                    "profile_summary_present": (root / "summary.json").exists(),
                    "market_sequence_present": (root / "ce25_market_sequence.csv").exists(),
                    "cache_file_count": sum(1 for item in cache.rglob("*.json")),
                    "classification": "cache_or_raw_only_no_profile_summary",
                }
            )
    return out


def profile_gaps(profile_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    usable = []
    for row in profile_rows:
        if row["profile_class"] != "profile_with_market_sequence":
            continue
        start = parse_bjt(row.get("window_start_bjt"))
        end = parse_bjt(row.get("window_end_bjt"))
        if not start or not end:
            continue
        usable.append((start, end, row))
    usable.sort(key=lambda item: item[0])
    gaps: list[dict[str, Any]] = []
    for (_, prev_end, prev_row), (next_start, _, next_row) in zip(usable, usable[1:]):
        gap_s = seconds_between(prev_end, next_start)
        if gap_s > 0:
            gaps.append(
                {
                    "gap_start_bjt": prev_end.isoformat(),
                    "gap_end_bjt": next_start.isoformat(),
                    "gap_hours": round(gap_s / 3600.0, 6),
                    "prev_profile": prev_row["root"],
                    "next_profile": next_row["root"],
                    "gap_type": "missing_profile_window",
                }
            )
        elif gap_s < 0:
            gaps.append(
                {
                    "overlap_start_bjt": next_start.isoformat(),
                    "overlap_end_bjt": prev_end.isoformat(),
                    "overlap_hours": round(abs(gap_s) / 3600.0, 6),
                    "prev_profile": prev_row["root"],
                    "next_profile": next_row["root"],
                    "gap_type": "overlap",
                }
            )
    return gaps


def read_tsv_row(path: Path, key_field: str, key_value: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            if row.get(key_field) == key_value:
                return row
    return None


def read_csv_rows(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return rows[:limit] if limit is not None else rows


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    profiles = inventory_profiles()
    data_source_windows = data_source_nagi_windows()
    raw_only = cache_only_dirs(profiles)
    gaps = profile_gaps(profiles)
    old_rollup = read_tsv_row(AUTORESEARCH_ACCOUNT_ROLLUP, "account", "nagi")

    reverse_packet = read_json(REVERSE_PACKET) if REVERSE_PACKET.exists() else {}
    latest_public_summary = (
        reverse_packet.get("latest_public_profile", {}).get("summary", {})
        if isinstance(reverse_packet, dict)
        else {}
    )
    candidate_rows = read_csv_rows(REVERSE_CANDIDATES)
    daily_rows = read_csv_rows(REVERSE_DAILY)
    kept_candidate_rows = [
        row for row in candidate_rows if str(row.get("decision", "")).startswith("KEEP")
        or str(row.get("decision", "")).startswith("TRANSLATE")
    ]
    rejected_candidate_rows = [
        row for row in candidate_rows if "REJECT" in str(row.get("decision", ""))
        or "HARD" in str(row.get("decision", ""))
    ]

    full_profiles = [row for row in profiles if row["profile_class"] == "profile_with_market_sequence"]
    latest_96h = next((row for row in profiles if row["root"].endswith("profile_nagi_20260604_1110_to_20260608_1110_bjt")), None)
    old_4win = {
        "markets": int(float(old_rollup["markets"])) if old_rollup else None,
        "buy_actual": float(old_rollup["buy_actual"]) if old_rollup else None,
        "cash_pnl": float(old_rollup["cash_pnl"]) if old_rollup else None,
        "roi": float(old_rollup["roi"]) if old_rollup else None,
        "pair_cost": float(old_rollup["pair_cost"]) if old_rollup else None,
        "resid_rate": float(old_rollup["resid_rate"]) if old_rollup else None,
        "bad_pc_ge_100_share": float(old_rollup["bad_pc_ge_100_share"]) if old_rollup else None,
    }

    decision = {
        "public_window_expansion_value": "LOW_FOR_PRIVATE_TRUTH_HIGH_FOR_RESIDUAL_MODEL_REFINEMENT",
        "public_only_can_change_private_maker_queue_conclusion": False,
        "public_only_can_refine_candidate_bucket_ranking": True,
        "public_only_can_refine_residual_direction_model": True,
        "no_web_fetch_performed": True,
        "next_step": "nagi_strategy_decision_register_packet_20260608",
        "reason": (
            "Local evidence already shows NAGI full-account/public taker copy is rejected and maker fee0 lane "
            "requires own authenticated fill telemetry. Additional public windows may change residual/side "
            "bucket ranking but cannot prove queue priority, maker fills, or private execution truth."
        ),
    }

    packet = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS,
        "wallet": WALLET,
        "method": {
            "scope": "local public-profile inventory only",
            "web_fetch_performed": False,
            "execution_authorized": False,
            "profile_classes": {
                "profile_with_market_sequence": "profile_ce25_execution_pattern-style output with summary and ce25_market_sequence.csv",
                "deep_compare_summary_only": "public PnL summary without market sequence profile",
                "deep_slice_summary_only": "short-window/deep summary, not comparable to 24h profile rollups",
                "cache_or_raw_only_no_profile_summary": "local cache/raw files exist but profile summary is absent",
            },
        },
        "summary": {
            "profile_with_market_sequence_count": len(full_profiles),
            "autoresearch_nagi_4win_source_count": len(data_source_windows),
            "summary_profile_count": len(profiles),
            "cache_or_raw_only_dir_count": len(raw_only),
            "missing_or_overlap_segment_count": len(gaps),
            "old_4win_public_rollup": old_4win,
            "latest_96h_public_profile": {
                "root": latest_96h["root"] if latest_96h else None,
                "market_count": latest_96h.get("market_count") if latest_96h else None,
                "buy_actual": latest_96h.get("buy_actual") if latest_96h else None,
                "cash_pnl": latest_96h.get("cash_pnl") if latest_96h else None,
                "roi": latest_96h.get("roi") if latest_96h else None,
                "pair_cost": latest_96h.get("pair_cost") if latest_96h else None,
                "resid_rate": latest_96h.get("resid_rate") if latest_96h else None,
                "bad_pc_ge_100_share": latest_public_summary.get("bad_pc_ge_100_share"),
                "pair_pnl": latest_public_summary.get("pair_pnl"),
                "residual_pnl": latest_public_summary.get("residual_pnl"),
            },
            "reverse_engineering_candidate_count": len(candidate_rows),
            "reverse_engineering_keep_or_translate_count": len(kept_candidate_rows),
            "reverse_engineering_reject_or_hard_count": len(rejected_candidate_rows),
            "daily_summary_days": [row.get("day_bjt") for row in daily_rows],
        },
        "decision": decision,
        "profile_inventory": profiles,
        "autoresearch_4win_sources": data_source_windows,
        "cache_or_raw_only_inventory": raw_only,
        "profile_window_gaps_and_overlaps": gaps,
        "public_only_materiality": [
            {
                "question": "Can more public-only NAGI windows make taker copy viable?",
                "answer": "No. Taker fee07 scale lane remains absent in local maker-queue frontier, and public activity cannot prove maker/taker truth.",
            },
            {
                "question": "Can more public-only NAGI windows make full-account copy viable?",
                "answer": "No. Full-account bad pair-cost remains high and account-level profits shift between pair and residual components.",
            },
            {
                "question": "Can more public-only NAGI windows help?",
                "answer": "Yes, for residual direction/timeout/kill-switch model refinement and for selecting candidate buckets before private shadow.",
            },
        ],
        "source_bindings": {
            "builder": binding(BUILDER),
            "autoresearch_data_sources": binding(AUTORESEARCH_DATA_SOURCES),
            "autoresearch_account_rollup": binding(AUTORESEARCH_ACCOUNT_ROLLUP),
            "autoresearch_proxy_summary": binding(AUTORESEARCH_PROXY_SUMMARY),
            "latest_profile_summary": binding(LATEST_PROFILE_SUMMARY),
            "latest_profile_sequence": binding(LATEST_PROFILE_SEQUENCE),
            "reverse_engineering_packet": binding(REVERSE_PACKET),
            "reverse_engineering_daily_summary": binding(REVERSE_DAILY),
            "reverse_engineering_candidates": binding(REVERSE_CANDIDATES),
            "reverse_engineering_report": binding(REVERSE_REPORT),
            "synthesis_packet": binding(SYNTHESIS_PACKET),
            "queue_sensitivity_packet": binding(QUEUE_SENSITIVITY_PACKET),
            "sample_size_packet": binding(SAMPLE_SIZE_PACKET),
        },
        "non_claims": {
            "private_truth_ready": False,
            "queue_priority_proven": False,
            "maker_fill_proven": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "deployable": False,
            "oos_ready": False,
            "orders_authorized": False,
            "cancels_authorized": False,
            "private_key_authorized": False,
            "api_creds_authorized": False,
            "ws_authorized": False,
            "web_fetch_performed": False,
        },
    }

    inventory_csv = OUT / "nagi_public_profile_window_inventory.csv"
    gap_csv = OUT / "nagi_public_profile_window_gaps.csv"
    raw_csv = OUT / "nagi_public_profile_cache_or_raw_only_inventory.csv"
    packet_path = OUT / "NAGI_PUBLIC_PROFILE_WINDOW_EXPANSION_GAP_PACKET.json"
    report_path = OUT / "NAGI_PUBLIC_PROFILE_WINDOW_EXPANSION_GAP_REPORT.md"
    preview_path = OUT / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"

    write_csv(inventory_csv, profiles)
    write_csv(gap_csv, gaps)
    write_csv(raw_csv, raw_only)
    write_json(packet_path, packet)
    report_path.write_text(
        "\n".join(
            [
                "# NAGI Public Profile Window Expansion Gap",
                "",
                f"Status: `{STATUS}`",
                "",
                "This is a local public-profile inventory. It did not fetch the web and does not authorize private keys, API credentials, WS, orders, cancels, OOS, canary, live, or deployment.",
                "",
                "## Inventory",
                "",
                f"- Profile-with-market-sequence outputs: {len(full_profiles)}",
                f"- Autoresearch NAGI 4-window source count: {len(data_source_windows)}",
                f"- Summary/profile files inventoried: {len(profiles)}",
                f"- Cache/raw-only NAGI dirs without comparable profile summary: {len(raw_only)}",
                "",
                "## Decision",
                "",
                "- More public-only windows can refine residual direction and kill-switch buckets.",
                "- More public-only windows cannot prove maker fill, queue priority, or authenticated maker/taker truth.",
                "- The next canonical artifact should be the strategy decision register.",
                "",
                "## Latest 96h Public Profile",
                "",
                f"- Markets: {latest_96h.get('market_count') if latest_96h else None}",
                f"- Buy actual: {latest_96h.get('buy_actual') if latest_96h else None}",
                f"- Cash PnL: {latest_96h.get('cash_pnl') if latest_96h else None}",
                f"- ROI: {latest_96h.get('roi') if latest_96h else None}",
                f"- Pair cost: {latest_96h.get('pair_cost') if latest_96h else None}",
                f"- Residual rate: {latest_96h.get('resid_rate') if latest_96h else None}",
                f"- Bad pair-cost >=1 share: {latest_public_summary.get('bad_pc_ge_100_share')}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    write_preview(preview_path)
    write_sha256sums(OUT, [packet_path, report_path, inventory_csv, gap_csv, raw_csv, preview_path])

    print(
        json.dumps(
            {
                "packet": str(packet_path),
                "status": STATUS,
                "summary": packet["summary"],
                "decision": decision,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
