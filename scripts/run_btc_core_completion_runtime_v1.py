#!/usr/bin/env python3
"""Run the BTC core completion V1 local runtime over a sanitized ex-ante ledger.

This is a deterministic local replay/runtime contract. It consumes only
sanitized ex-ante action rows for decisions/state updates. Optional settlement
labels from the source registry are used only after replay for retrospective
research accounting.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
BT_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
DEFAULT_LEDGER = ROOT / "data/exports/btc_core_exante_action_ledger_20260605/btc_core_exante_action_ledger.csv"
DEFAULT_STRATEGY_PACKET = (
    ROOT
    / "data/exports/btc_core_completion_strategy_review_packet_20260605/"
    "BTC_CORE_COMPLETION_V1_STRATEGY_REVIEW_PACKET.json"
)
DEFAULT_SOURCE_REGISTRY = (
    BT_ROOT
    / "derived/contract_examples/btc_completion_state_machine_from_l1_flow_taker_normalized_v1/"
    "candidate_registry.csv"
)
DEFAULT_RESULT_MANIFEST = (
    BT_ROOT
    / "derived/contract_examples/btc_completion_state_machine_from_l1_flow_taker_normalized_v1/"
    "RESULT_SUMMARY_MANIFEST.json"
)
DEFAULT_OUTPUT_DIR = ROOT / "data/exports/btc_core_completion_runtime_v1_local_replay_20260605"

STATUS_OK = "KEEP_BTC_CORE_COMPLETION_RUNTIME_V1_LOCAL_REPLAY_MATCHED_REVIEW_REQUIRED_NOT_OOS_READY"
STATUS_BLOCKED = "BLOCKED_BTC_CORE_COMPLETION_RUNTIME_V1_LOCAL_REPLAY_CONTRACT_FAILED_NOT_OOS_READY"
OFFICIAL_CLOB_FEE_FORMULA = "fee = shares * fee_rate * price * (1 - price)"
FEE_RATE = 0.07
DUST = 1e-9
FORBIDDEN_DECISION_FIELDS = {
    "winner_side",
    "resolved_outcome",
    "outcome",
    "private_truth_ready",
    "strategy_promotion_ready",
    "live_ready",
    "deployable",
    "inventory_yes_qty_after",
    "inventory_yes_cost_after",
    "inventory_no_qty_after",
    "inventory_no_cost_after",
    "pair_qty_after_seed",
    "pair_actions_after_seed",
    "pair_cost_wavg_after_seed",
}
REQUIRED_LEDGER_FIELDS = {
    "candidate_id",
    "action_id",
    "condition_id",
    "slug",
    "ts_ms",
    "side",
    "opposite_side",
    "seed_px",
    "seed_qty",
    "fee_model",
    "official_taker_fee",
    "fee",
    "source_row_hash",
    "exante_row_hash",
}


@dataclass
class Lot:
    qty: float
    px: float
    side: str
    ts_ms: int
    action_id: str
    candidate_id: str


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader), list(reader.fieldnames or [])


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                fields.append(key)
                seen.add(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def f(value: Any) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return math.nan
    return out if math.isfinite(out) else math.nan


def official_fee(qty: float, px: float) -> float:
    if qty <= DUST or not (0.0 <= px <= 1.0):
        return 0.0
    return qty * FEE_RATE * px * (1.0 - px)


def lot_qty(lots: deque[Lot]) -> float:
    return sum(lot.qty for lot in lots)


def lot_cost(lots: deque[Lot]) -> float:
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
        "paired_qty": paired_qty,
        "pair_actions": float(pair_actions),
        "pair_cost_wavg": pair_cost_sum / paired_qty if paired_qty else None,
        "pair_cost_sum": pair_cost_sum,
    }


def compare_metric(expected: Any, actual: Any) -> dict[str, Any]:
    exp = f(expected)
    act = f(actual)
    if math.isnan(exp) and math.isnan(act):
        ok = True
    elif math.isnan(exp) or math.isnan(act):
        ok = False
    else:
        ok = abs(round(exp, 6) - round(act, 6)) <= 1e-6
    return {
        "expected": None if math.isnan(exp) else round(exp, 6),
        "actual": None if math.isnan(act) else round(act, 6),
        "match": ok,
    }


def command_preview(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                "echo 'NOT_AUTHORIZED: local runtime replay only; no OOS/runner/live path is authorized.' >&2",
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
    parser.add_argument("--strategy-packet", type=Path, default=DEFAULT_STRATEGY_PACKET)
    parser.add_argument("--source-registry", type=Path, default=DEFAULT_SOURCE_REGISTRY)
    parser.add_argument("--result-manifest", type=Path, default=DEFAULT_RESULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    ledger_rows, ledger_fields = read_csv(args.ledger)
    registry_rows, _ = read_csv(args.source_registry)
    strategy_packet = read_json(args.strategy_packet)
    result_manifest = read_json(args.result_manifest)
    forbidden_present = sorted(FORBIDDEN_DECISION_FIELDS.intersection(ledger_fields))
    missing_required = sorted(REQUIRED_LEDGER_FIELDS.difference(ledger_fields))
    source_winner_by_condition = {
        row["condition_id"]: row["winner_side"]
        for row in registry_rows
        if row.get("winner_side") in {"YES", "NO"}
    }

    states: dict[str, dict[str, deque[Lot]]] = defaultdict(lambda: {"YES": deque(), "NO": deque()})
    action_rows: list[dict[str, Any]] = []
    metrics = defaultdict(float)
    fee_mismatch_rows: list[dict[str, Any]] = []
    contract_errors: list[str] = []
    if forbidden_present:
        contract_errors.append(f"forbidden_decision_fields_present:{','.join(forbidden_present)}")
    if missing_required:
        contract_errors.append(f"missing_required_ledger_fields:{','.join(missing_required)}")

    for row in sorted(ledger_rows, key=lambda item: int(float(item["action_id"]))):
        side = row.get("side", "")
        condition_id = row.get("condition_id", "")
        qty = f(row.get("seed_qty"))
        px = f(row.get("seed_px"))
        ts_ms = int(float(row.get("ts_ms") or 0))
        if side not in {"YES", "NO"} or not condition_id or math.isnan(qty) or math.isnan(px):
            contract_errors.append(f"invalid_action_row:{row.get('candidate_id')}:{row.get('action_id')}")
            continue
        fee_recomputed = official_fee(qty, px)
        fee_reported = f(row.get("official_taker_fee"))
        if math.isnan(fee_reported) or abs(round(fee_reported, 6) - round(fee_recomputed, 6)) > 1e-6:
            fee_mismatch_rows.append(
                {
                    "candidate_id": row.get("candidate_id"),
                    "action_id": row.get("action_id"),
                    "seed_qty": round(qty, 6),
                    "seed_px": round(px, 6),
                    "reported_official_taker_fee": None if math.isnan(fee_reported) else round(fee_reported, 6),
                    "recomputed_official_taker_fee": round(fee_recomputed, 6),
                }
            )
        inv = states[condition_id]
        inv[side].append(
            Lot(
                qty=qty,
                px=px,
                side=side,
                ts_ms=ts_ms,
                action_id=row["action_id"],
                candidate_id=row["candidate_id"],
            )
        )
        pair_delta = pair_inventory(inv)
        paired_qty = float(pair_delta["paired_qty"] or 0.0)
        pair_cost_sum = float(pair_delta["pair_cost_sum"] or 0.0)
        metrics["seed_actions"] += 1
        metrics["gross_buy_qty"] += qty
        metrics["gross_buy_cost"] += qty * px
        metrics["official_taker_fee"] += fee_recomputed
        metrics["pair_actions"] += int(float(pair_delta["pair_actions"] or 0.0))
        metrics["pair_qty"] += paired_qty
        metrics["pair_cost_sum"] += pair_cost_sum
        metrics["pair_pnl"] += paired_qty - pair_cost_sum
        action_rows.append(
            {
                "candidate_id": row["candidate_id"],
                "action_id": int(float(row["action_id"])),
                "condition_id": condition_id,
                "slug": row["slug"],
                "ts_ms": ts_ms,
                "side": side,
                "seed_qty": round(qty, 6),
                "seed_px": round(px, 6),
                "official_taker_fee_recomputed": round(fee_recomputed, 6),
                "pair_qty_delta": round(paired_qty, 6),
                "pair_actions_delta": int(float(pair_delta["pair_actions"] or 0.0)),
                "pair_cost_wavg_delta": round(float(pair_delta["pair_cost_wavg"]), 6)
                if pair_delta["pair_cost_wavg"] is not None
                else None,
                "runtime_inventory_yes_qty": round(lot_qty(inv["YES"]), 6),
                "runtime_inventory_no_qty": round(lot_qty(inv["NO"]), 6),
                "runtime_inventory_yes_cost": round(lot_cost(inv["YES"]), 6),
                "runtime_inventory_no_cost": round(lot_cost(inv["NO"]), 6),
                "source_row_hash": row["source_row_hash"],
                "exante_row_hash": row["exante_row_hash"],
            }
        )

    residual_rows: list[dict[str, Any]] = []
    for condition_id, inv in states.items():
        winner = source_winner_by_condition.get(condition_id)
        for side in ("YES", "NO"):
            for lot in inv[side]:
                if lot.qty <= DUST:
                    continue
                cost = lot.qty * lot.px
                payout = lot.qty if side == winner else 0.0
                metrics["residual_qty"] += lot.qty
                metrics["residual_cost"] += cost
                metrics["residual_settle_payout"] += payout
                metrics["residual_settle_pnl"] += payout - cost
                residual_rows.append(
                    {
                        "condition_id": condition_id,
                        "candidate_id": lot.candidate_id,
                        "action_id": int(float(lot.action_id)),
                        "side": side,
                        "winner_side_audit_label": winner,
                        "residual_qty": round(lot.qty, 6),
                        "residual_cost": round(cost, 6),
                        "residual_settle_payout": round(payout, 6),
                        "residual_settle_pnl": round(payout - cost, 6),
                    }
                )

    active_markets = sum(1 for inv in states.values() if lot_qty(inv["YES"]) > DUST or lot_qty(inv["NO"]) > DUST)
    # Preserve manifest semantics: active markets means markets with at least one selected action.
    active_markets = len(states)
    metrics_out = {
        "active_markets": active_markets,
        "seed_actions": int(metrics["seed_actions"]),
        "gross_buy_qty": round(metrics["gross_buy_qty"], 6),
        "gross_buy_cost": round(metrics["gross_buy_cost"], 6),
        "official_taker_fee": round(metrics["official_taker_fee"], 6),
        "pair_actions": int(metrics["pair_actions"]),
        "pair_qty": round(metrics["pair_qty"], 6),
        "weighted_pair_cost": round(metrics["pair_cost_sum"] / metrics["pair_qty"], 6) if metrics["pair_qty"] else None,
        "pair_pnl": round(metrics["pair_pnl"], 6),
        "residual_qty": round(metrics["residual_qty"], 6),
        "residual_cost": round(metrics["residual_cost"], 6),
        "residual_settle_payout": round(metrics["residual_settle_payout"], 6),
        "residual_settle_pnl": round(metrics["residual_settle_pnl"], 6),
    }
    metrics_out["gross_pnl"] = round(metrics_out["pair_pnl"] + metrics_out["residual_settle_pnl"], 6)
    metrics_out["fee_after_pnl"] = round(metrics_out["gross_pnl"] - metrics_out["official_taker_fee"], 6)
    metrics_out["net_roi"] = round(metrics_out["fee_after_pnl"] / metrics_out["gross_buy_cost"], 6)
    metrics_out["pair_share_rate"] = round(metrics_out["pair_qty"] * 2.0 / metrics_out["gross_buy_qty"], 6)
    metrics_out["residual_qty_rate"] = round(metrics_out["residual_qty"] / metrics_out["gross_buy_qty"], 6)
    metrics_out["residual_cost_rate"] = round(metrics_out["residual_cost"] / metrics_out["gross_buy_cost"], 6)

    manifest_core = result_manifest["core_metrics"]
    comparison_fields = [
        "active_markets",
        "seed_actions",
        "gross_buy_qty",
        "gross_buy_cost",
        "official_taker_fee",
        "pair_actions",
        "pair_qty",
        "weighted_pair_cost",
        "pair_pnl",
        "residual_qty",
        "residual_cost",
        "residual_settle_payout",
        "residual_settle_pnl",
        "gross_pnl",
        "fee_after_pnl",
        "net_roi",
        "pair_share_rate",
        "residual_qty_rate",
        "residual_cost_rate",
    ]
    manifest_comparison = {
        field: compare_metric(manifest_core.get(field), metrics_out.get(field)) for field in comparison_fields
    }
    mismatch_count = sum(1 for item in manifest_comparison.values() if not item["match"])
    if fee_mismatch_rows:
        contract_errors.append(f"fee_formula_mismatch_rows:{len(fee_mismatch_rows)}")
    if mismatch_count:
        contract_errors.append(f"manifest_metric_mismatch_count:{mismatch_count}")

    status = STATUS_OK if not contract_errors else STATUS_BLOCKED
    action_path = output_dir / "btc_core_runtime_v1_action_state.csv"
    residual_path = output_dir / "btc_core_runtime_v1_residual_lots.csv"
    fee_mismatch_path = output_dir / "btc_core_runtime_v1_fee_mismatches.csv"
    result_path = output_dir / "BTC_CORE_COMPLETION_RUNTIME_V1_RESULT.json"
    contract_path = output_dir / "BTC_CORE_COMPLETION_RUNTIME_V1_INPUT_CONTRACT.json"
    preview_path = output_dir / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    hash_manifest_path = output_dir / "BTC_CORE_COMPLETION_RUNTIME_V1_HASH_MANIFEST.json"

    write_csv(action_path, action_rows)
    write_csv(residual_path, residual_rows)
    write_csv(fee_mismatch_path, fee_mismatch_rows)
    contract = {
        "schema_version": 1,
        "status": status,
        "strategy_id": "BTC_CORE_COMPLETION_V1",
        "runtime_mode": "local_replay_exante_ledger_only",
        "required_ledger_fields": sorted(REQUIRED_LEDGER_FIELDS),
        "forbidden_decision_fields": sorted(FORBIDDEN_DECISION_FIELDS),
        "ledger_fields": ledger_fields,
        "forbidden_present": forbidden_present,
        "missing_required": missing_required,
        "fee_formula": OFFICIAL_CLOB_FEE_FORMULA,
        "fee_rate": FEE_RATE,
        "settlement_label_policy": "source_registry winner_side is used only after replay for retrospective residual accounting",
        "non_claims": {
            "oos_ready": False,
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "deployable": False,
        },
    }
    result = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": status,
        "strategy_id": "BTC_CORE_COMPLETION_V1",
        "scope": "local_runtime_contract_replay_only_not_oos",
        "metrics": metrics_out,
        "manifest_comparison": manifest_comparison,
        "manifest_metric_mismatch_count": mismatch_count,
        "contract_errors": contract_errors,
        "fee_mismatch_count": len(fee_mismatch_rows),
        "inputs": {
            "ledger": str(args.ledger),
            "ledger_sha256": sha256_file(args.ledger),
            "strategy_packet": str(args.strategy_packet),
            "strategy_packet_sha256": sha256_file(args.strategy_packet),
            "source_registry": str(args.source_registry),
            "source_registry_sha256": sha256_file(args.source_registry),
            "result_manifest": str(args.result_manifest),
            "result_manifest_sha256": sha256_file(args.result_manifest),
        },
        "strategy_packet_status": strategy_packet.get("status"),
        "non_claims": contract["non_claims"],
    }
    write_json(contract_path, contract)
    write_json(result_path, result)
    command_preview(preview_path)

    files = [action_path, residual_path, fee_mismatch_path, result_path, contract_path, preview_path]
    hash_manifest = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": status,
        "files": {
            path.name: {"path": str(path), "sha256": sha256_file(path), "size": path.stat().st_size}
            for path in files
        },
    }
    write_json(hash_manifest_path, hash_manifest)
    result["outputs"] = {
        "action_state_csv": str(action_path),
        "residual_lots_csv": str(residual_path),
        "fee_mismatches_csv": str(fee_mismatch_path),
        "input_contract": str(contract_path),
        "hash_manifest": str(hash_manifest_path),
        "hash_manifest_sha256": sha256_file(hash_manifest_path),
    }
    write_json(result_path, result)
    hash_manifest["files"][result_path.name] = {
        "path": str(result_path),
        "sha256": sha256_file(result_path),
        "size": result_path.stat().st_size,
    }
    write_json(hash_manifest_path, hash_manifest)

    print(f"status={status}")
    print(f"output_dir={output_dir}")
    print(f"seed_actions={metrics_out['seed_actions']}")
    print(f"active_markets={metrics_out['active_markets']}")
    print(f"fee_after_pnl={metrics_out['fee_after_pnl']}")
    print(f"manifest_metric_mismatch_count={mismatch_count}")
    print(f"fee_mismatch_count={len(fee_mismatch_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
