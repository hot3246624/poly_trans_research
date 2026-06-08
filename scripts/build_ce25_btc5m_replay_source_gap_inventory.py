#!/usr/bin/env python3
"""Inventory the replay/source gap blocking CE25 BTC 5m broad replay.

This script is intentionally review-only. It does not build replay stores,
download archives, scan raw collectors, or authorize OOS/live work. It packages
the current local source availability into a hashable artifact so the next work
item is concrete: build or locate a manifest-backed source covering the CE25
2026-05-28..2026-06-04 public profile window.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
BACKTEST_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
OUTPUT_DIR = ROOT / "data" / "exports" / "ce25_btc5m_replay_source_gap_inventory_20260605"

LEDGER = (
    ROOT
    / "data"
    / "exports"
    / "ce25_btc5m_broad_profile_candidate_ledger_20260604"
    / "ce25_btc5m_broad_profile_candidate_ledger.csv"
)
FEATURE_PROBE_SUMMARY = (
    ROOT
    / "data"
    / "exports"
    / "ce25_btc5m_exante_feature_probe_20260604"
    / "CE25_BTC5M_EXANTE_FEATURE_PROBE_SUMMARY.json"
)
CONTROLLER_PACKET = (
    ROOT
    / "data"
    / "exports"
    / "ce25_btc5m_controller_v0_review_packet_20260604"
    / "CE25_BTC5M_CONTROLLER_V0_REVIEW_PACKET.json"
)
CROSSWALK_SUMMARY = (
    ROOT
    / "data"
    / "exports"
    / "ce25_btc5m_replay_source_crosswalk_20260604"
    / "CE25_BTC5M_REPLAY_SOURCE_CROSSWALK_SUMMARY.json"
)
LOCAL_REPLAY_ASSESSMENT = (
    ROOT
    / "data"
    / "exports"
    / "ce25_btc5m_local_replay_bridge_assessment_20260604"
    / "CE25_BTC5M_LOCAL_REPLAY_BRIDGE_ASSESSMENT.json"
)
LOCAL_DATA_GUIDE = ROOT / "docs" / "AGENT_LOCAL_BACKTEST_DATA_GUIDE_20260519_ZH.md"

STRICT_L1_ROOT = BACKTEST_ROOT / "backtest_cache" / "taker_buy_signal_core_v2_strict_l1"
COMPLETION_V2_ROOT = BACKTEST_ROOT / "verification_store" / "completion_unwind_event_store_v2"
REPLAY_L2_ROOT = BACKTEST_ROOT / "verification_store" / "replay_store_multiasset_l2_v1"
HIGH_PRICE_L2_ROOT = BACKTEST_ROOT / "derived" / "ce25_high_price_l2_top_aligned_validation_v0"

STATUS = "BLOCKED_CE25_BTC5M_BROAD_REPLAY_SOURCE_GAP_MATCHING_20260528_20260604_STORE_REQUIRED_NOT_OOS_READY"
PROFILE_WINDOW_START_UTC = "2026-05-28T03:45:00Z"
PROFILE_WINDOW_END_UTC = "2026-06-04T03:45:00Z"
PROFILE_REQUIRED_DAYS_UTC = [
    "2026-05-28",
    "2026-05-29",
    "2026-05-30",
    "2026-05-31",
    "2026-06-01",
    "2026-06-02",
    "2026-06-03",
    "2026-06-04",
]


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


def iso_from_epoch_s(value: str | int | float) -> str:
    return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def list_manifest_labels(root: Path, manifest_name: str) -> list[dict[str, Any]]:
    labels: list[dict[str, Any]] = []
    if not root.is_dir():
        return labels
    for path in sorted(root.iterdir()):
        manifest = path / manifest_name
        if not manifest.is_file():
            continue
        data = read_json(manifest)
        labels.append(
            {
                "label": path.name,
                "path": str(path),
                "manifest": str(manifest),
                "manifest_sha256": sha256_file(manifest),
                "days": data.get("days") or data.get("labels_days") or [],
                "dataset_type": data.get("dataset_type") or data.get("schema_version") or data.get("cache_kind"),
                "row_count": (data.get("outputs") or {}).get("row_count") or data.get("row_count"),
                "raw_scanned": data.get("raw_scanned"),
                "replay_scanned": data.get("replay_scanned"),
                "collector_scanned": data.get("collector_scanned"),
            }
        )
    return labels


def covered_days(labels: list[dict[str, Any]]) -> list[str]:
    days = sorted({day for label in labels for day in label.get("days", [])})
    return days


def missing_required_days(available_days: list[str]) -> list[str]:
    available = set(available_days)
    return [day for day in PROFILE_REQUIRED_DAYS_UTC if day not in available]


def ledger_scope(rows: list[dict[str, str]]) -> dict[str, Any]:
    starts = [int(float(row["market_start_s"])) for row in rows if row.get("market_start_s")]
    labels = sorted({row["source_profile_label"] for row in rows})
    first_price_buckets = Counter(row.get("source_first_price_bucket", "") for row in rows)
    return {
        "path": str(LEDGER),
        "sha256": sha256_file(LEDGER),
        "candidate_count": len(rows),
        "condition_count": len({row["source_condition_id"] for row in rows}),
        "profile_labels": labels,
        "market_start_min_utc": iso_from_epoch_s(min(starts)) if starts else None,
        "market_start_max_utc": iso_from_epoch_s(max(starts)) if starts else None,
        "first_price_bucket_counts": dict(sorted(first_price_buckets.items())),
        "source_window_start_utc": PROFILE_WINDOW_START_UTC,
        "source_window_end_utc": PROFILE_WINDOW_END_UTC,
        "required_days_utc": PROFILE_REQUIRED_DAYS_UTC,
    }


def summarize_high_price_l2(limit: int = 80) -> dict[str, Any]:
    manifests = sorted(HIGH_PRICE_L2_ROOT.glob("*/CE25_L2_TOP_ALIGNED_VALIDATION_MANIFEST.json"))
    entries: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    max_action_count = 0
    max_market_count = 0
    for manifest_path in manifests[:limit]:
        data = read_json(manifest_path)
        summary = data.get("summary", {})
        status = summary.get("status") or data.get("status")
        status_counts[str(status)] += 1
        action_count = int(summary.get("action_count") or 0)
        market_count = int(summary.get("market_count") or summary.get("l2_top_aligned_vwap_pass_market_count") or 0)
        max_action_count = max(max_action_count, action_count)
        max_market_count = max(max_market_count, market_count)
        result_manifest = summary.get("result_manifest") or (data.get("inputs") or {}).get("result_manifest")
        result_days: list[str] = []
        if result_manifest and Path(result_manifest).is_file():
            try:
                result_data = read_json(Path(result_manifest))
                result_days = result_data.get("days") or result_data.get("core_metrics", {}).get("days") or []
            except json.JSONDecodeError:
                result_days = []
        entries.append(
            {
                "path": str(manifest_path.parent),
                "manifest_sha256": sha256_file(manifest_path),
                "status": status,
                "branch_id": summary.get("branch_id"),
                "variant_id": summary.get("variant_id"),
                "action_count": action_count,
                "market_count": market_count,
                "fee_rate": summary.get("fee_rate"),
                "result_days": result_days,
                "classification": "NARROW_HIGH_PRICE_BRANCH_NOT_CE25_BROAD_PROFILE_REPLAY",
            }
        )
    return {
        "root": str(HIGH_PRICE_L2_ROOT),
        "manifest_count": len(manifests),
        "summarized_count": len(entries),
        "status_counts": dict(status_counts),
        "max_action_count": max_action_count,
        "max_market_count": max_market_count,
        "entries": entries,
        "decision": "useful branch evidence, but not a broad 2026-05-28..2026-06-04 CE25 BTC5M source-of-truth replay",
    }


def source_coverage_section() -> dict[str, Any]:
    strict_l1 = list_manifest_labels(STRICT_L1_ROOT, "CACHE_MANIFEST.json")
    completion_v2 = list_manifest_labels(COMPLETION_V2_ROOT, "EVENT_STORE_MANIFEST.json")
    replay_l2 = list_manifest_labels(REPLAY_L2_ROOT, "REPLAY_STORE_V2_MANIFEST.json")
    return {
        "strict_l1_cache": {
            "root": str(STRICT_L1_ROOT),
            "labels": strict_l1,
            "covered_days": covered_days(strict_l1),
            "missing_required_days_for_ce25_profile": missing_required_days(covered_days(strict_l1)),
        },
        "completion_unwind_event_store_v2": {
            "root": str(COMPLETION_V2_ROOT),
            "labels": completion_v2,
            "covered_days": covered_days(completion_v2),
            "missing_required_days_for_ce25_profile": missing_required_days(covered_days(completion_v2)),
        },
        "replay_store_multiasset_l2_v1": {
            "root": str(REPLAY_L2_ROOT),
            "labels": replay_l2,
            "covered_days": covered_days(replay_l2),
            "missing_required_days_for_ce25_profile": missing_required_days(covered_days(replay_l2)),
        },
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def build_plan() -> list[dict[str, Any]]:
    return [
        {
            "step": 1,
            "name": "locate_or_build_manifest_backed_replay_source",
            "required_output": "replay_store_multiasset_l2_v1 or equivalent for 2026-05-28..2026-06-04 BTC crypto 5m",
            "must_not_use": "raw collector directory existence as readiness; unmanifested sqlite scans",
        },
        {
            "step": 2,
            "name": "build_strict_l1_and_completion_v2_sources",
            "required_output": "strict L1 cache and completion_unwind_event_store_v2 labels covering CE25 profile days",
            "must_report": "data_root, labels, days, assets, row counts, raw/replay/collector scanned flags",
        },
        {
            "step": 3,
            "name": "materialize_btc5m_candidate_base",
            "required_output": "candidate_base.duckdb from the matching completion store",
            "must_validate": "source row count, candidate row count, BTC updown 5m condition coverage",
        },
        {
            "step": 4,
            "name": "rerun_replay_source_crosswalk",
            "required_output": "CE25 broad ledger condition/source row overlap against candidate actions",
            "pass_gate": "condition overlap materially nonzero and all matched rows have no market/window/token drift",
        },
        {
            "step": 5,
            "name": "run_fixed_clock_controller_replay",
            "required_output": "official-fee replay for broad/high-participation candidate controller with residual/capital stress",
            "must_remain": "review-only until source coverage and no-drift gates pass",
        },
    ]


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    ledger_rows = read_csv(LEDGER)
    source_coverage = source_coverage_section()
    crosswalk = read_json(CROSSWALK_SUMMARY)
    replay_assessment = read_json(LOCAL_REPLAY_ASSESSMENT)
    feature_probe = read_json(FEATURE_PROBE_SUMMARY)
    controller_packet = read_json(CONTROLLER_PACKET)

    rows = []
    for source_name, section in source_coverage.items():
        rows.append(
            {
                "source_name": source_name,
                "root": section["root"],
                "label_count": len(section["labels"]),
                "covered_days": "|".join(section["covered_days"]),
                "missing_required_days_for_ce25_profile": "|".join(section["missing_required_days_for_ce25_profile"]),
                "usable_for_ce25_20260528_20260604": "false",
            }
        )
    inventory_csv = OUTPUT_DIR / "ce25_btc5m_replay_source_gap_inventory.csv"
    write_csv(inventory_csv, rows)

    high_price = summarize_high_price_l2()
    high_price_csv = OUTPUT_DIR / "ce25_high_price_l2_branch_inventory.csv"
    write_csv(high_price_csv, high_price["entries"])

    summary = {
        "schema_version": 1,
        "status": STATUS,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": "CE25 BTC 5m broad public-profile controller source-gap inventory; review-only",
        "ce25_profile_window": {
            "start_utc": PROFILE_WINDOW_START_UTC,
            "end_utc": PROFILE_WINDOW_END_UTC,
            "required_days_utc": PROFILE_REQUIRED_DAYS_UTC,
        },
        "ledger_scope": ledger_scope(ledger_rows),
        "feature_probe_anchor": {
            "path": str(FEATURE_PROBE_SUMMARY),
            "sha256": sha256_file(FEATURE_PROBE_SUMMARY),
            "status": feature_probe.get("status"),
            "baseline": feature_probe.get("baseline"),
            "top_review_candidates": feature_probe.get("top_review_candidates", [])[:4],
        },
        "controller_packet_anchor": {
            "path": str(CONTROLLER_PACKET),
            "sha256": sha256_file(CONTROLLER_PACKET),
            "status": controller_packet.get("status"),
            "controller_count": len(controller_packet.get("controller_specs", [])),
        },
        "existing_replay_bridge_anchor": {
            "path": str(LOCAL_REPLAY_ASSESSMENT),
            "sha256": sha256_file(LOCAL_REPLAY_ASSESSMENT),
            "status": replay_assessment.get("status"),
            "core_metrics": replay_assessment.get("core_metrics", {}),
            "capital_cashflow_estimate": replay_assessment.get("capital_cashflow_estimate", {}),
            "classification": "STRUCTURAL_DIRECTIONAL_ONLY_WRONG_WINDOW_FOR_CE25_PROFILE",
        },
        "crosswalk_anchor": {
            "path": str(CROSSWALK_SUMMARY),
            "sha256": sha256_file(CROSSWALK_SUMMARY),
            "status": crosswalk.get("status"),
            "crosswalk": crosswalk.get("crosswalk", {}),
            "decision": crosswalk.get("decision", {}),
        },
        "source_coverage": source_coverage,
        "high_price_l2_branch_inventory": high_price,
        "decision": {
            "can_claim_ce25_broad_controller_replay": False,
            "can_claim_oos_ready": False,
            "can_claim_live_or_deployable": False,
            "blocking_reason": "no manifest-backed local strict L1/completion/L2 source covers CE25 public profile days 2026-05-28..2026-06-04; existing replay days are 2026-05-02..2026-05-18 and crosswalk has zero shared condition IDs",
            "next_required_artifact": "matching replay/completion source build or locator packet for CE25 BTC5M profile window",
            "highest_allowed_status": STATUS,
        },
        "next_build_plan": build_plan(),
        "non_claims": non_claims(),
    }

    summary_path = OUTPUT_DIR / "CE25_BTC5M_REPLAY_SOURCE_GAP_INVENTORY.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    note_path = OUTPUT_DIR / "CE25_BTC5M_REPLAY_SOURCE_GAP_INVENTORY_NOTE.md"
    note_path.write_text(
        "\n".join(
            [
                "# CE25 BTC 5m Replay Source Gap Inventory",
                "",
                f"Status: `{STATUS}`",
                "",
                "## What Is Strong",
                "",
                "- CE25 BTC 5m broad public profile has 1,071 candidate markets in the recent 2026-05-28..2026-06-04 window.",
                "- The public profile feature probe identifies profitable review buckets, including broad baseline and 20-35 / 65-80 first-price buckets.",
                "- Existing local official-fee completion replay is positive and useful as structural evidence.",
                "",
                "## What Is Blocking",
                "",
                "- The existing local replay/completion stores cover 2026-05-02..2026-05-18, not the CE25 profile window.",
                "- The CE25 ledger and existing replay actions have zero shared condition IDs in the source crosswalk.",
                "- High-price L2 top-aligned artifacts validate a narrow old-window branch, not the broad recent CE25 profile.",
                "",
                "## Required Next Artifact",
                "",
                "Build or locate manifest-backed strict L1 / completion V2 / L2 replay sources covering 2026-05-28 through 2026-06-04, then rerun the source crosswalk before any controller replay or OOS packet.",
                "",
                "No OOS, live, private truth, import, order, canary, deployment, or promotion claim is authorized by this inventory.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    command_preview_path = OUTPUT_DIR / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    command_preview_path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                "echo 'NOT_AUTHORIZED: source build/replay is intentionally not executed by this inventory.' >&2",
                "echo 'Next reviewed action must build or locate manifest-backed CE25 2026-05-28..2026-06-04 replay/completion sources.' >&2",
                "exit 66",
                "",
            ]
        ),
        encoding="utf-8",
    )

    artifacts = [
        summary_path,
        note_path,
        inventory_csv,
        high_price_csv,
        command_preview_path,
        LEDGER,
        FEATURE_PROBE_SUMMARY,
        CONTROLLER_PACKET,
        CROSSWALK_SUMMARY,
        LOCAL_REPLAY_ASSESSMENT,
        LOCAL_DATA_GUIDE,
        Path(__file__).resolve(),
    ]
    manifest_path = OUTPUT_DIR / "CE25_BTC5M_REPLAY_SOURCE_GAP_HASH_MANIFEST.json"
    manifest = {
        "schema_version": 1,
        "status": STATUS,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "artifacts": [
            {"path": str(path), "sha256": sha256_file(path), "size_bytes": path.stat().st_size}
            for path in artifacts
            if path.exists()
        ],
        "summary_sha256": sha256_file(summary_path),
        "inventory_csv_sha256": sha256_file(inventory_csv),
        "high_price_branch_csv_sha256": sha256_file(high_price_csv),
        "non_claims": non_claims(),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "ok": True,
                "status": STATUS,
                "output_dir": str(OUTPUT_DIR),
                "summary_sha256": sha256_file(summary_path),
                "manifest_sha256": sha256_file(manifest_path),
                "ledger_condition_count": summary["ledger_scope"]["condition_count"],
                "crosswalk_matched_condition_count": crosswalk.get("crosswalk", {}).get("matched_condition_count"),
                "completion_missing_required_days": source_coverage["completion_unwind_event_store_v2"][
                    "missing_required_days_for_ce25_profile"
                ],
                "strict_l1_missing_required_days": source_coverage["strict_l1_cache"][
                    "missing_required_days_for_ce25_profile"
                ],
                "replay_l2_missing_required_days": source_coverage["replay_store_multiasset_l2_v1"][
                    "missing_required_days_for_ce25_profile"
                ],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
