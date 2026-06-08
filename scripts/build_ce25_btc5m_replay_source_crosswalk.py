#!/usr/bin/env python3
"""Crosswalk CE25 BTC 5m public profile rows against local replay actions."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
BACKTEST_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
LEDGER_DIR = ROOT / "data" / "exports" / "ce25_btc5m_broad_profile_candidate_ledger_20260604"
LEDGER = LEDGER_DIR / "ce25_btc5m_broad_profile_candidate_ledger.csv"
REPLAY_DIR = (
    BACKTEST_ROOT
    / "derived"
    / "completion_candidate_pipeline_v1"
    / "pass_local_completion_residual_cooldown_officialfee_e055_t5_imb125_rc30_050_20260502_20260518_publicfull_v2"
)
REPLAY_ACTIONS = REPLAY_DIR / "actions.csv"
REPLAY_RESULT_MANIFEST = REPLAY_DIR / "RESULT_SUMMARY_MANIFEST.json"
REPLAY_COMPLIANCE_MANIFEST = REPLAY_DIR / "COMPLIANCE_MANIFEST.json"
COMPLETION_STORE_ROOT = BACKTEST_ROOT / "verification_store" / "completion_unwind_event_store_v2"
OUTPUT_DIR = ROOT / "data" / "exports" / "ce25_btc5m_replay_source_crosswalk_20260604"

STATUS = "BLOCKED_CE25_BTC5M_REPLAY_SOURCE_CROSSWALK_WINDOW_MISMATCH_REPLAY_REQUIRED_NOT_OOS_READY"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def non_claims() -> dict[str, bool]:
    return {
        "private_truth_ready": False,
        "strategy_promotion_ready": False,
        "live_ready": False,
        "deployable": False,
        "oos_authorized": False,
        "runner_authorized": False,
        "orders_authorized": False,
    }


def completion_store_labels() -> list[str]:
    if not COMPLETION_STORE_ROOT.is_dir():
        return []
    labels = []
    for path in sorted(COMPLETION_STORE_ROOT.iterdir()):
        if (path / "EVENT_STORE_MANIFEST.json").is_file():
            labels.append(path.name)
    return labels


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", type=Path, default=LEDGER)
    parser.add_argument("--replay-actions", type=Path, default=REPLAY_ACTIONS)
    parser.add_argument("--replay-result-manifest", type=Path, default=REPLAY_RESULT_MANIFEST)
    parser.add_argument("--replay-compliance-manifest", type=Path, default=REPLAY_COMPLIANCE_MANIFEST)
    parser.add_argument("--completion-store-root", type=Path, default=COMPLETION_STORE_ROOT)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    return parser.parse_args()


def completion_store_labels_for(root: Path) -> list[str]:
    if not root.is_dir():
        return []
    labels = []
    for path in sorted(root.iterdir()):
        if (path / "EVENT_STORE_MANIFEST.json").is_file():
            labels.append(path.name)
    return labels


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    ledger_rows = read_csv(args.ledger)
    action_rows = read_csv(args.replay_actions)
    replay_manifest = read_json(args.replay_result_manifest)

    actions_by_condition: dict[str, list[dict[str, str]]] = defaultdict(list)
    replay_days: set[str] = set()
    for row in action_rows:
        actions_by_condition[row["condition_id"]].append(row)
        replay_days.add(row["day"])

    crosswalk_rows: list[dict[str, Any]] = []
    matched_condition_ids: set[str] = set()
    for row in ledger_rows:
        condition_id = row["source_condition_id"]
        matches = actions_by_condition.get(condition_id, [])
        if matches:
            matched_condition_ids.add(condition_id)
        offsets = [float(match["offset_s"]) for match in matches if match.get("offset_s")]
        prices = [float(match["public_trade_price"]) for match in matches if match.get("public_trade_price")]
        crosswalk_rows.append(
            {
                "source_condition_id": condition_id,
                "candidate_id": row["candidate_id"],
                "source_profile_label": row["source_profile_label"],
                "slug": row["slug"],
                "market_start_s": row["market_start_s"],
                "source_first_price_bucket": row["source_first_price_bucket"],
                "source_first_side": row["source_first_side"],
                "replay_action_count": len(matches),
                "replay_day_values": "|".join(sorted({match["day"] for match in matches})),
                "replay_side_values": "|".join(sorted({match["side"] for match in matches})),
                "replay_offset_min_s": min(offsets) if offsets else "",
                "replay_offset_max_s": max(offsets) if offsets else "",
                "replay_price_min": min(prices) if prices else "",
                "replay_price_max": max(prices) if prices else "",
                "crosswalk_status": "MATCHED" if matches else "NO_MATCH_WINDOW_MISMATCH",
            }
        )

    crosswalk_path = args.output_dir / "ce25_btc5m_replay_source_crosswalk.csv"
    with crosswalk_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(crosswalk_rows[0].keys()))
        writer.writeheader()
        writer.writerows(crosswalk_rows)

    ledger_labels = sorted({row["source_profile_label"] for row in ledger_rows})
    summary = {
        "schema_version": 1,
        "status": STATUS,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "ledger_source": {
            "path": str(args.ledger),
            "sha256": sha256_file(args.ledger),
            "candidate_count": len(ledger_rows),
            "condition_count": len({row["source_condition_id"] for row in ledger_rows}),
            "profile_labels": ledger_labels,
        },
        "replay_source": {
            "path": str(args.replay_actions),
            "sha256": sha256_file(args.replay_actions),
            "action_count": len(action_rows),
            "condition_count": len(actions_by_condition),
            "days": sorted(replay_days),
            "result_manifest_sha256": sha256_file(args.replay_result_manifest),
            "result_status": replay_manifest["core_metrics"]["status"],
            "fee_model": replay_manifest["core_metrics"]["fee_model"],
            "official_fee_rate": replay_manifest["core_metrics"]["official_fee_rate"],
        },
        "crosswalk": {
            "matched_condition_count": len(matched_condition_ids),
            "matched_ledger_row_count": sum(1 for row in crosswalk_rows if row["crosswalk_status"] == "MATCHED"),
            "unmatched_ledger_row_count": sum(
                1 for row in crosswalk_rows if row["crosswalk_status"] == "NO_MATCH_WINDOW_MISMATCH"
            ),
            "overlap_action_count": sum(int(row["replay_action_count"]) for row in crosswalk_rows),
            "condition_overlap_rate": len(matched_condition_ids) / len(ledger_rows) if ledger_rows else 0.0,
            "material_drift": "WINDOW_MISMATCH_NO_SHARED_CONDITION_IDS",
        },
        "available_completion_store_labels": completion_store_labels_for(args.completion_store_root),
        "decision": {
            "can_use_existing_replay_as_ce25_profile_reproduction": False,
            "can_use_existing_replay_as_structural_directional_evidence": True,
            "next_required_validation": "build or locate completion/taker replay source covering CE25 BTC5M profile labels 2026-05-28 through 2026-06-04, then rerun source crosswalk before claiming controller replay",
        },
        "highest_allowed_status": STATUS,
        "non_claims": non_claims(),
    }
    summary_path = args.output_dir / "CE25_BTC5M_REPLAY_SOURCE_CROSSWALK_SUMMARY.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    note_path = args.output_dir / "CE25_BTC5M_REPLAY_SOURCE_CROSSWALK_NOTE.md"
    note_path.write_text(
        "\n".join(
            [
                "# CE25 BTC 5m Replay Source Crosswalk",
                "",
                f"Status: `{STATUS}`",
                "",
                "The CE25 BTC 5m public-profile ledger and the existing official-fee local replay result have zero shared condition IDs.",
                "The existing replay remains useful structural evidence, but it is not a reproduction of the 2026-05-28 to 2026-06-04 CE25 profile rows.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    artifacts = [
        crosswalk_path,
        summary_path,
        note_path,
        args.ledger,
        args.replay_actions,
        args.replay_result_manifest,
        args.replay_compliance_manifest,
        Path(__file__).resolve(),
    ]
    manifest = {
        "schema_version": 1,
        "status": STATUS,
        "artifacts": [
            {"path": str(path), "sha256": sha256_file(path), "size_bytes": path.stat().st_size}
            for path in artifacts
        ],
        "summary_sha256": sha256_file(summary_path),
        "crosswalk_csv_sha256": sha256_file(crosswalk_path),
        "non_claims": non_claims(),
    }
    manifest_path = args.output_dir / "CE25_BTC5M_REPLAY_SOURCE_CROSSWALK_HASH_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "ok": True,
                "status": STATUS,
                "output_dir": str(args.output_dir),
                "summary_sha256": sha256_file(summary_path),
                "manifest_sha256": sha256_file(manifest_path),
                "ledger_condition_count": summary["ledger_source"]["condition_count"],
                "replay_condition_count": summary["replay_source"]["condition_count"],
                "matched_condition_count": summary["crosswalk"]["matched_condition_count"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
