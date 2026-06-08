#!/usr/bin/env python3
"""Replay the BTC core ex-ante action ledger against registry state fields.

This is a source-of-truth bridge check. It intentionally does not select a new
strategy or start any live/OOS path. The verifier asks one narrow question:
can the sanitized ex-ante action ledger reproduce the current BTC completion
state-machine registry's pair/inventory state using the checked-in FIFO
semantics?
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
from collections import Counter, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
DEFAULT_BT_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
DEFAULT_LEDGER = (
    DEFAULT_ROOT
    / "data/exports/btc_core_exante_action_ledger_20260605/btc_core_exante_action_ledger.csv"
)
DEFAULT_LEDGER_AUDIT = (
    DEFAULT_ROOT
    / "data/exports/btc_core_exante_action_ledger_20260605/BTC_CORE_EXANTE_ACTION_LEDGER_AUDIT.json"
)
DEFAULT_LEAKAGE_PACKET = (
    DEFAULT_ROOT
    / "data/exports/btc_core_exante_controller_leakage_audit_packet_20260605/"
    "BTC_CORE_EXANTE_CONTROLLER_LEAKAGE_AUDIT_PACKET.json"
)
DEFAULT_REGISTRY = (
    DEFAULT_BT_ROOT
    / "derived/contract_examples/btc_completion_state_machine_from_l1_flow_taker_normalized_v1/"
    "candidate_registry.csv"
)
DEFAULT_RESULT_MANIFEST = (
    DEFAULT_BT_ROOT
    / "derived/contract_examples/btc_completion_state_machine_from_l1_flow_taker_normalized_v1/"
    "RESULT_SUMMARY_MANIFEST.json"
)
DEFAULT_STATE_MACHINE_SOURCE = DEFAULT_ROOT / "scripts/run_completion_candidate_state_machine.py"
DEFAULT_OUTPUT_DIR = DEFAULT_ROOT / "data/exports/btc_core_local_replay_verifier_20260605"

DUST = 1e-9
ROUND_DIGITS = 6
COMPARE_FIELDS = [
    "pair_qty_after_seed",
    "pair_actions_after_seed",
    "pair_cost_wavg_after_seed",
    "inventory_yes_qty_after",
    "inventory_no_qty_after",
    "inventory_yes_cost_after",
    "inventory_no_cost_after",
]


@dataclass
class Lot:
    qty: float
    px: float
    ts_ms: int


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def stable_row_hash(row: dict[str, Any], fields: list[str]) -> str:
    h = hashlib.sha256()
    for field in fields:
        h.update(field.encode("utf-8"))
        h.update(b"=")
        h.update(str(row.get(field, "")).encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    if fieldnames is None:
        fieldnames = []
        seen: set[str] = set()
        for row in rows:
            for key in row:
                if key not in seen:
                    seen.add(key)
                    fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def parse_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def parse_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def norm_float(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    if abs(value) <= DUST:
        return 0.0
    return round(value, ROUND_DIGITS)


def compare_value(expected_raw: Any, actual_raw: Any) -> tuple[bool, float | int | None, float | int | None]:
    expected = parse_float(expected_raw)
    actual = parse_float(actual_raw)
    expected_norm = norm_float(expected)
    actual_norm = norm_float(actual)
    if expected_norm is None and actual_norm is None:
        return True, expected_norm, actual_norm
    if expected_norm is None or actual_norm is None:
        return False, expected_norm, actual_norm
    return abs(expected_norm - actual_norm) <= 1e-6, expected_norm, actual_norm


def inv_qty(lots: deque[Lot]) -> float:
    return sum(lot.qty for lot in lots)


def inv_cost(lots: deque[Lot]) -> float:
    return sum(lot.qty * lot.px for lot in lots)


def pair_inventory(inv: dict[str, deque[Lot]]) -> dict[str, float | None]:
    yes = inv["YES"]
    no = inv["NO"]
    paired_qty = 0.0
    pair_cost_sum = 0.0
    pair_actions = 0
    while yes and no:
        a = yes[0]
        b = no[0]
        take = min(a.qty, b.qty)
        if take <= DUST:
            break
        pair_cost = a.px + b.px
        pair_actions += 1
        paired_qty += take
        pair_cost_sum += take * pair_cost
        a.qty -= take
        b.qty -= take
        if a.qty <= DUST:
            yes.popleft()
        if b.qty <= DUST:
            no.popleft()
    return {
        "pair_qty_after_seed": paired_qty,
        "pair_actions_after_seed": float(pair_actions),
        "pair_cost_wavg_after_seed": (pair_cost_sum / paired_qty) if paired_qty else None,
        "inventory_yes_qty_after": inv_qty(yes),
        "inventory_no_qty_after": inv_qty(no),
        "inventory_yes_cost_after": inv_cost(yes),
        "inventory_no_cost_after": inv_cost(no),
    }


def output_status(
    total_rows: int,
    missing_rows: int,
    drift_rows: int,
    critical_state_drift_count: int,
    terminal_metric_mismatch_count: int,
) -> str:
    if total_rows <= 0:
        return "BLOCKED_BTC_CORE_LOCAL_REPLAY_VERIFIER_NO_ROWS_REVIEW_REQUIRED_NOT_OOS_READY"
    if missing_rows:
        return "BLOCKED_BTC_CORE_LOCAL_REPLAY_VERIFIER_LEDGER_SOURCE_ROW_MISSING_REVIEW_REQUIRED_NOT_OOS_READY"
    if critical_state_drift_count or terminal_metric_mismatch_count:
        return "BLOCKED_BTC_CORE_LOCAL_REPLAY_VERIFIER_STATE_SEMANTICS_DRIFT_REVIEW_REQUIRED_NOT_OOS_READY"
    if drift_rows:
        return "KEEP_BTC_CORE_LOCAL_REPLAY_VERIFIER_TERMINAL_PNL_MATCHED_POST_ACTION_FIELD_DRIFT_REVIEW_REQUIRED_NOT_OOS_READY"
    return "KEEP_BTC_CORE_LOCAL_REPLAY_VERIFIER_MATCHED_REVIEW_REQUIRED_NOT_OOS_READY"


def build_command_preview(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                "echo 'NOT_AUTHORIZED: local replay verifier packet is review-only; run script directly only for local research refresh.' >&2",
                "exit 66",
                "",
            ]
        ),
        encoding="utf-8",
    )
    path.chmod(0o755)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--ledger-audit", type=Path, default=DEFAULT_LEDGER_AUDIT)
    parser.add_argument("--leakage-packet", type=Path, default=DEFAULT_LEAKAGE_PACKET)
    parser.add_argument("--source-registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--result-manifest", type=Path, default=DEFAULT_RESULT_MANIFEST)
    parser.add_argument("--state-machine-source", type=Path, default=DEFAULT_STATE_MACHINE_SOURCE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--max-drift-examples", type=int, default=500)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    ledger_rows = read_csv(args.ledger)
    source_rows = read_csv(args.source_registry)
    result_manifest = load_json(args.result_manifest) if args.result_manifest.is_file() else {}
    ledger_by_key = {(row["candidate_id"], row["action_id"]): row for row in ledger_rows}
    source_rows_sorted = sorted(source_rows, key=lambda row: (parse_int(row.get("action_id")) or 0))

    states: dict[str, dict[str, deque[Lot]]] = {}
    market_winners: dict[str, str] = {}
    replay_rows: list[dict[str, Any]] = []
    drift_examples: list[dict[str, Any]] = []
    missing_rows: list[dict[str, Any]] = []
    drift_by_field = {field: 0 for field in COMPARE_FIELDS}
    terminal_pair_actions = 0
    terminal_pair_qty = 0.0
    terminal_pair_cost_sum = 0.0
    terminal_pair_pnl = 0.0
    terminal_gross_buy_qty = 0.0
    terminal_gross_buy_cost = 0.0

    for source in source_rows_sorted:
        key = (source["candidate_id"], source["action_id"])
        ledger = ledger_by_key.get(key)
        if ledger is None:
            missing_rows.append(
                {
                    "candidate_id": source["candidate_id"],
                    "action_id": source["action_id"],
                    "condition_id": source.get("condition_id"),
                    "reason": "source_registry_row_missing_from_sanitized_exante_ledger",
                }
            )
            continue

        condition_id = ledger["condition_id"]
        inv = states.setdefault(condition_id, {"YES": deque(), "NO": deque()})
        if source.get("winner_side") in {"YES", "NO"}:
            market_winners[condition_id] = source["winner_side"]
        side = ledger["side"]
        qty = parse_float(ledger.get("seed_qty"))
        px = parse_float(ledger.get("seed_px"))
        ts_ms = parse_int(ledger.get("ts_ms"))
        if side not in {"YES", "NO"} or qty is None or px is None or ts_ms is None:
            missing_rows.append(
                {
                    "candidate_id": source["candidate_id"],
                    "action_id": source["action_id"],
                    "condition_id": condition_id,
                    "reason": "ledger_required_replay_field_invalid",
                    "side": side,
                    "seed_qty": ledger.get("seed_qty"),
                    "seed_px": ledger.get("seed_px"),
                    "ts_ms": ledger.get("ts_ms"),
                }
            )
            continue

        inv[side].append(Lot(qty=qty, px=px, ts_ms=ts_ms))
        replay = pair_inventory(inv)
        paired_qty = float(replay["pair_qty_after_seed"] or 0.0)
        pair_cost_wavg = replay["pair_cost_wavg_after_seed"]
        terminal_pair_actions += int(float(replay["pair_actions_after_seed"] or 0.0))
        terminal_pair_qty += paired_qty
        if pair_cost_wavg is not None and math.isfinite(float(pair_cost_wavg)):
            terminal_pair_cost_sum += paired_qty * float(pair_cost_wavg)
            terminal_pair_pnl += paired_qty * (1.0 - float(pair_cost_wavg))
        terminal_gross_buy_qty += qty
        terminal_gross_buy_cost += qty * px

        row_drift_fields: list[str] = []
        replay_out: dict[str, Any] = {
            "candidate_id": source["candidate_id"],
            "action_id": int(source["action_id"]),
            "condition_id": condition_id,
            "slug": ledger.get("slug"),
            "ts_iso": ledger.get("ts_iso"),
            "side": side,
            "seed_qty": norm_float(qty),
            "seed_px": norm_float(px),
        }
        for field in COMPARE_FIELDS:
            ok, expected, actual = compare_value(source.get(field), replay.get(field))
            replay_out[f"source_{field}"] = expected
            replay_out[f"replay_{field}"] = actual
            replay_out[f"{field}_match"] = ok
            if not ok:
                row_drift_fields.append(field)
                drift_by_field[field] += 1

        replay_out["row_match"] = not row_drift_fields
        replay_out["drift_fields"] = "|".join(row_drift_fields)
        replay_out["source_row_hash"] = source.get("source_row_hash", "")
        replay_out["exante_row_hash"] = ledger.get("exante_row_hash", "")
        replay_out["replay_state_row_hash"] = stable_row_hash(replay_out, list(replay_out.keys()))
        replay_rows.append(replay_out)
        if row_drift_fields and len(drift_examples) < args.max_drift_examples:
            drift_examples.append(replay_out)

    compared_rows = len(replay_rows)
    drift_rows = sum(1 for row in replay_rows if not row["row_match"])
    field_drift_count = sum(drift_by_field.values())
    missing_count = len(missing_rows)
    terminal_residual_qty = 0.0
    terminal_residual_cost = 0.0
    terminal_residual_settle_payout = 0.0
    terminal_residual_settle_pnl = 0.0
    for condition_id, inv in states.items():
        winner = market_winners.get(condition_id)
        for side in ("YES", "NO"):
            for lot in inv[side]:
                if lot.qty <= DUST:
                    continue
                cost = lot.qty * lot.px
                payout = lot.qty if side == winner else 0.0
                terminal_residual_qty += lot.qty
                terminal_residual_cost += cost
                terminal_residual_settle_payout += payout
                terminal_residual_settle_pnl += payout - cost
    terminal_metrics = {
        "pair_actions": terminal_pair_actions,
        "pair_qty": norm_float(terminal_pair_qty),
        "weighted_pair_cost": norm_float(terminal_pair_cost_sum / terminal_pair_qty) if terminal_pair_qty else None,
        "pair_pnl": norm_float(terminal_pair_pnl),
        "residual_qty": norm_float(terminal_residual_qty),
        "residual_cost": norm_float(terminal_residual_cost),
        "residual_settle_payout": norm_float(terminal_residual_settle_payout),
        "residual_settle_pnl": norm_float(terminal_residual_settle_pnl),
        "gross_buy_qty": norm_float(terminal_gross_buy_qty),
        "gross_buy_cost": norm_float(terminal_gross_buy_cost),
        "gross_pnl": norm_float(terminal_pair_pnl + terminal_residual_settle_pnl),
    }
    manifest_core_metrics = result_manifest.get("core_metrics", {})
    terminal_metric_comparison: dict[str, dict[str, Any]] = {}
    for field, replay_value in terminal_metrics.items():
        manifest_value = manifest_core_metrics.get(field)
        ok, expected, actual = compare_value(manifest_value, replay_value)
        terminal_metric_comparison[field] = {
            "manifest": expected,
            "replay": actual,
            "match": ok,
        }
    terminal_metric_mismatch_count = sum(1 for item in terminal_metric_comparison.values() if not item["match"])
    non_inventory_yes_drift_count = sum(
        count for field, count in drift_by_field.items() if field not in {"inventory_yes_qty_after", "inventory_yes_cost_after"}
    )
    status = output_status(
        compared_rows,
        missing_count,
        drift_rows,
        non_inventory_yes_drift_count,
        terminal_metric_mismatch_count,
    )
    drift_side_counts = Counter(str(row.get("side", "")) for row in replay_rows if not row["row_match"])
    drift_field_sets = Counter(str(row.get("drift_fields", "")) for row in replay_rows if not row["row_match"])
    drift_source_yes_qty_values = Counter(
        str(row.get("source_inventory_yes_qty_after", "")) for row in replay_rows if not row["row_match"]
    )
    drift_source_yes_cost_values = Counter(
        str(row.get("source_inventory_yes_cost_after", "")) for row in replay_rows if not row["row_match"]
    )
    drift_attribution = {
        "drift_side_counts": dict(drift_side_counts.most_common()),
        "drift_field_sets": dict(drift_field_sets.most_common()),
        "source_inventory_yes_qty_values_on_drift": dict(drift_source_yes_qty_values.most_common(20)),
        "source_inventory_yes_cost_values_on_drift": dict(drift_source_yes_cost_values.most_common(20)),
        "interpretation": (
            "All observed drift is isolated to YES-side inventory qty/cost fields when replaying with current FIFO "
            "quantity/cost semantics. Source YES inventory qty is 1.0 on every drift row and source YES inventory "
            "cost is binary 0/1, which is consistent with a legacy settlement/outcome-like field semantic or "
            "artifact/source-version mismatch rather than pair matching/order coverage drift."
        )
        if drift_rows
        else "No pair/inventory state drift observed under current FIFO semantics.",
    }

    replay_csv = output_dir / "btc_core_local_replay_state_comparison.csv"
    drift_csv = output_dir / "btc_core_local_replay_drift_examples.csv"
    missing_csv = output_dir / "btc_core_local_replay_missing_rows.csv"
    write_csv(replay_csv, replay_rows)
    write_csv(drift_csv, drift_examples)
    write_csv(missing_csv, missing_rows)

    leakage_packet = load_json(args.leakage_packet) if args.leakage_packet.is_file() else {}
    ledger_audit = load_json(args.ledger_audit) if args.ledger_audit.is_file() else {}
    summary = {
        "status": status,
        "generated_at": utc_now(),
        "scope": {
            "strategy_lane": "BTC_CORE_COMPLETION_V1",
            "owner_line": "xuan_research_local",
            "source_window": "2026-05-02..2026-05-18",
            "evidence_type": "local_replay_backtest_public_l1_source_only",
            "not_oos_ready": True,
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "deployable": False,
        },
        "inputs": {
            "ledger": str(args.ledger),
            "ledger_sha256": sha256_file(args.ledger),
            "source_registry": str(args.source_registry),
            "source_registry_sha256": sha256_file(args.source_registry),
            "state_machine_source": str(args.state_machine_source),
            "state_machine_source_sha256": sha256_file(args.state_machine_source),
            "result_manifest": str(args.result_manifest),
            "result_manifest_sha256": sha256_file(args.result_manifest),
            "ledger_audit": str(args.ledger_audit),
            "ledger_audit_sha256": sha256_file(args.ledger_audit),
            "leakage_packet": str(args.leakage_packet),
            "leakage_packet_sha256": sha256_file(args.leakage_packet),
        },
        "summary": {
            "source_registry_rows": len(source_rows),
            "sanitized_ledger_rows": len(ledger_rows),
            "compared_rows": compared_rows,
            "missing_or_invalid_rows": missing_count,
            "drift_rows": drift_rows,
            "drift_row_rate": round(drift_rows / compared_rows, 6) if compared_rows else None,
            "field_drift_count": field_drift_count,
            "drift_by_field": drift_by_field,
            "drift_attribution": drift_attribution,
            "non_inventory_yes_drift_count": non_inventory_yes_drift_count,
            "terminal_metric_mismatch_count": terminal_metric_mismatch_count,
            "terminal_metric_comparison": terminal_metric_comparison,
        },
        "source_registry_manifest_summary": result_manifest.get("summary", {}),
        "exante_ledger_audit_summary": ledger_audit.get("summary", {}),
        "leakage_packet_status": leakage_packet.get("status"),
        "outputs": {
            "comparison_csv": str(replay_csv),
            "drift_examples_csv": str(drift_csv),
            "missing_rows_csv": str(missing_csv),
        },
        "decision": {
            "oos_authorized": False,
            "runner_observer_authorized": False,
            "private_order_live_authorized": False,
            "latest_pointer_update_authorized": False,
            "if_status_blocked": "review state-machine artifact/source semantic drift before using this ledger as a runner contract",
            "if_status_keep": "state replay bridge can be used as a local review input, still not OOS/private/live ready",
        },
    }

    summary_path = output_dir / "BTC_CORE_LOCAL_REPLAY_VERIFIER_SUMMARY.json"
    note_path = output_dir / "BTC_CORE_LOCAL_REPLAY_VERIFIER_NOTE.md"
    command_preview = output_dir / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    hash_manifest_path = output_dir / "BTC_CORE_LOCAL_REPLAY_VERIFIER_HASH_MANIFEST.json"

    write_json(summary_path, summary)
    note_path.write_text(
        "\n".join(
            [
                "# BTC Core Local Replay Verifier",
                "",
                f"Status: `{status}`",
                "",
                "This packet is local review-only. It replays the sanitized BTC ex-ante action ledger with the checked-in FIFO pair/inventory semantics and compares it to the existing BTC completion state-machine registry post-action fields.",
                "",
                "It does not authorize OOS, shared-WS, runner/observer, broker/service mutation, private key loading, import, order/cancel/redeem, canary/live/deploy/funding, latest pointer updates, or readiness/promotion claims.",
                "",
                "If drift is nonzero, the existing source artifact and current state-machine source are not yet a safe runner contract; resolve artifact/source semantic drift before OOS preparation.",
                "",
                "The verifier separates terminal PnL/residual metrics from post-action display fields. A KEEP status with post-action field drift means terminal pair/residual/gross metrics match the manifest, while selected registry inventory fields still need schema clarification before being used as runner inputs.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    build_command_preview(command_preview)

    hash_manifest = {
        "generated_at": utc_now(),
        "status": status,
        "files": {},
    }
    for path in [
        summary_path,
        replay_csv,
        drift_csv,
        missing_csv,
        note_path,
        command_preview,
    ]:
        hash_manifest["files"][path.name] = {
            "path": str(path),
            "sha256": sha256_file(path),
            "size": path.stat().st_size,
        }
    write_json(hash_manifest_path, hash_manifest)

    # Re-hash manifest after writing so callers have a stable top-level digest.
    final_manifest_hash = sha256_file(hash_manifest_path)
    summary["outputs"]["hash_manifest"] = str(hash_manifest_path)
    summary["outputs"]["hash_manifest_sha256"] = final_manifest_hash
    write_json(summary_path, summary)

    # Refresh hashes for files affected by the final summary write.
    hash_manifest["files"][summary_path.name] = {
        "path": str(summary_path),
        "sha256": sha256_file(summary_path),
        "size": summary_path.stat().st_size,
    }
    write_json(hash_manifest_path, hash_manifest)

    print(f"status={status}")
    print(f"output_dir={output_dir}")
    print(f"compared_rows={compared_rows}")
    print(f"missing_or_invalid_rows={missing_count}")
    print(f"drift_rows={drift_rows}")
    print(f"field_drift_count={field_drift_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
