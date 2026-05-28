#!/usr/bin/env python3
"""Reconstruct per-pair FIFO lots from the old BTC completion baseline actions."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_DATA_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
DEFAULT_OLD_BASELINE = (
    DEFAULT_DATA_ROOT
    / "derived/completion_candidate_pipeline_v1/"
    / "pass_local_completion_residual_cooldown_officialfee_e055_t5_imb125_rc30_050_20260502_20260518_publicfull_v2"
)
DUST = 1e-12


@dataclass
class Lot:
    action_id: int
    candidate_row_id: str
    day: str
    condition_id: str
    slug: str
    side: str
    ts_ms: int
    px: float
    qty: float


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return [dict(row) for row in csv.DictReader(f)]


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def to_float(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    return float(value)


def to_int(value: Any) -> int:
    if value in (None, ""):
        return 0
    return int(float(value))


def rounded(value: float) -> float:
    return round(value, 6)


def lot_from_action(row: dict[str, str]) -> Lot:
    return Lot(
        action_id=to_int(row["action_id"]),
        candidate_row_id=str(row.get("candidate_row_id") or ""),
        day=str(row["day"]),
        condition_id=str(row["condition_id"]),
        slug=str(row.get("slug") or ""),
        side=str(row["side"]),
        ts_ms=to_int(row["ts_ms"]),
        px=to_float(row["seed_px"]),
        qty=to_float(row["seed_qty"]),
    )


def reconstruct_pair_lots(action_rows: list[dict[str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    states: dict[str, dict[str, deque[Lot]]] = defaultdict(lambda: {"YES": deque(), "NO": deque()})
    pair_rows: list[dict[str, Any]] = []
    action_checks: list[dict[str, Any]] = []
    pair_id = 0
    rows = sorted(action_rows, key=lambda r: (str(r["condition_id"]), to_int(r["ts_ms"]), to_int(r["action_id"])))
    for row in rows:
        lot = lot_from_action(row)
        state = states[lot.condition_id]
        state[lot.side].append(lot)
        paired_qty = 0.0
        pair_cost_sum = 0.0
        pair_actions = 0
        while state["YES"] and state["NO"]:
            yes = state["YES"][0]
            no = state["NO"][0]
            take = min(yes.qty, no.qty)
            if take <= DUST:
                break
            pair_id += 1
            pair_actions += 1
            paired_qty += take
            pair_cost = yes.px + no.px
            pair_cost_sum += take * pair_cost
            older_ts = min(yes.ts_ms, no.ts_ms)
            pair_rows.append(
                {
                    "pair_id": pair_id,
                    "day": lot.day,
                    "condition_id": lot.condition_id,
                    "slug": lot.slug,
                    "trigger_action_id": lot.action_id,
                    "yes_action_id": yes.action_id,
                    "no_action_id": no.action_id,
                    "yes_candidate_row_id": yes.candidate_row_id,
                    "no_candidate_row_id": no.candidate_row_id,
                    "qty": rounded(take),
                    "yes_px": rounded(yes.px),
                    "no_px": rounded(no.px),
                    "pair_cost": rounded(pair_cost),
                    "pair_pnl": rounded(take * (1.0 - pair_cost)),
                    "trigger_ts_ms": lot.ts_ms,
                    "older_ts_ms": older_ts,
                    "pair_delay_s": rounded(max(0, lot.ts_ms - older_ts) / 1000.0),
                    "trigger_side": lot.side,
                }
            )
            yes.qty -= take
            no.qty -= take
            if yes.qty <= DUST:
                state["YES"].popleft()
            if no.qty <= DUST:
                state["NO"].popleft()
        expected_qty = to_float(row.get("pair_qty_after_seed"))
        expected_actions = to_int(row.get("pair_actions_after_seed"))
        expected_wavg = to_float(row.get("pair_cost_wavg_after_seed"))
        actual_wavg = pair_cost_sum / paired_qty if paired_qty else 0.0
        action_checks.append(
            {
                "action_id": lot.action_id,
                "condition_id": lot.condition_id,
                "day": lot.day,
                "expected_pair_qty_after_seed": rounded(expected_qty),
                "actual_pair_qty_after_seed": rounded(paired_qty),
                "pair_qty_delta": rounded(paired_qty - expected_qty),
                "expected_pair_actions_after_seed": expected_actions,
                "actual_pair_actions_after_seed": pair_actions,
                "pair_actions_delta": pair_actions - expected_actions,
                "expected_pair_cost_wavg_after_seed": rounded(expected_wavg) if expected_qty else "",
                "actual_pair_cost_wavg_after_seed": rounded(actual_wavg) if paired_qty else "",
                "pair_cost_wavg_delta": rounded(actual_wavg - expected_wavg) if paired_qty or expected_qty else "",
            }
        )
    return pair_rows, action_checks


def summarize_by_day(pair_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in pair_rows:
        grouped[str(row["day"])].append(row)
    out: list[dict[str, Any]] = []
    for day, rows in sorted(grouped.items()):
        qty = sum(to_float(row["qty"]) for row in rows)
        pnl = sum(to_float(row["pair_pnl"]) for row in rows)
        cost_sum = sum(to_float(row["qty"]) * to_float(row["pair_cost"]) for row in rows)
        out.append(
            {
                "day": day,
                "pair_actions": len(rows),
                "pair_qty": rounded(qty),
                "pair_cost_wavg": rounded(cost_sum / qty) if qty else "",
                "pair_pnl": rounded(pnl),
                "p50_pair_delay_s": "",
                "max_pair_delay_s": rounded(max(to_float(row["pair_delay_s"]) for row in rows)) if rows else "",
            }
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-baseline-dir", type=Path, default=DEFAULT_OLD_BASELINE)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_DATA_ROOT / "derived/contract_examples/xuan_old_baseline_pair_lots_latest",
    )
    args = parser.parse_args()
    old_dir = args.old_baseline_dir.expanduser()
    output_dir = args.output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = old_dir / "RESULT_SUMMARY_MANIFEST.json"
    manifest = read_json(manifest_path)
    old_metrics = manifest.get("core_metrics") or {}
    pair_rows, action_checks = reconstruct_pair_lots(read_csv(old_dir / "actions.csv"))
    by_day = summarize_by_day(pair_rows)
    pair_csv = output_dir / "xuan_old_baseline_pair_lots.csv"
    check_csv = output_dir / "xuan_old_baseline_pair_lot_action_checks.csv"
    by_day_csv = output_dir / "xuan_old_baseline_pair_lots_by_day.csv"
    write_csv(pair_csv, pair_rows, list(pair_rows[0].keys()) if pair_rows else ["pair_id"])
    write_csv(check_csv, action_checks, list(action_checks[0].keys()) if action_checks else ["action_id"])
    write_csv(by_day_csv, by_day, list(by_day[0].keys()) if by_day else ["day"])
    pair_qty = sum(to_float(row["qty"]) for row in pair_rows)
    pair_pnl = sum(to_float(row["pair_pnl"]) for row in pair_rows)
    max_qty_delta = max(abs(to_float(row["pair_qty_delta"])) for row in action_checks) if action_checks else 0.0
    max_action_delta = max(abs(to_int(row["pair_actions_delta"])) for row in action_checks) if action_checks else 0
    metrics = {
        "pair_action_count": len(pair_rows),
        "pair_qty": rounded(pair_qty),
        "pair_pnl": rounded(pair_pnl),
        "old_manifest_pair_actions": old_metrics.get("pair_actions"),
        "old_manifest_pair_qty": old_metrics.get("pair_qty"),
        "old_manifest_pair_pnl": old_metrics.get("pair_pnl"),
        "pair_actions_delta_vs_manifest": len(pair_rows) - int(old_metrics.get("pair_actions") or 0),
        "pair_qty_delta_vs_manifest": rounded(pair_qty - float(old_metrics.get("pair_qty") or 0.0)),
        "pair_pnl_delta_vs_manifest": rounded(pair_pnl - float(old_metrics.get("pair_pnl") or 0.0)),
        "max_action_pair_qty_delta": rounded(max_qty_delta),
        "max_action_pair_actions_delta": max_action_delta,
    }
    ok = (
        metrics["pair_actions_delta_vs_manifest"] == 0
        and abs(metrics["pair_qty_delta_vs_manifest"]) <= 1e-5
        and abs(metrics["pair_pnl_delta_vs_manifest"]) <= 1e-3
        and metrics["max_action_pair_qty_delta"] <= 1e-6
        and metrics["max_action_pair_actions_delta"] == 0
    )
    out_manifest = {
        "schema_version": "xuan_old_baseline_pair_lots_v1",
        "created_utc": utc_now(),
        "status": "OK_OLD_BASELINE_PAIR_LOTS_RECONSTRUCTED" if ok else "FAIL_OLD_BASELINE_PAIR_LOTS_MISMATCH",
        "old_baseline_manifest": str(manifest_path),
        "output_dir": str(output_dir),
        "outputs": {
            "pair_lots_csv": str(pair_csv),
            "action_checks_csv": str(check_csv),
            "summary_by_day_csv": str(by_day_csv),
        },
        "metrics": metrics,
        "semantics": {
            "pairing_rule": "FIFO YES/NO lots per condition_id, matching the old state-machine pair_inventory implementation.",
            "not_private_truth": True,
        },
        "sha256": {
            "pair_lots_csv": sha256_file(pair_csv),
            "action_checks_csv": sha256_file(check_csv),
            "summary_by_day_csv": sha256_file(by_day_csv),
        },
    }
    out_path = output_dir / "XUAN_OLD_BASELINE_PAIR_LOTS_MANIFEST.json"
    out_path.write_text(json.dumps(out_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": out_manifest["status"], "metrics": metrics, "outputs": out_manifest["outputs"]}, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
