#!/usr/bin/env python3
"""Profile terminal residual risk for the BTC core ex-ante ledger.

The profile uses settlement labels only as retrospective audit labels. It does
not create an executable controller rule by itself.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
BT_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
OUTPUT_DIR = ROOT / "data/exports/btc_core_residual_risk_profile_20260605"
LEDGER = ROOT / "data/exports/btc_core_exante_action_ledger_20260605/btc_core_exante_action_ledger.csv"
STRATEGY_PACKET = (
    ROOT
    / "data/exports/btc_core_completion_strategy_review_packet_20260605/"
    "BTC_CORE_COMPLETION_V1_STRATEGY_REVIEW_PACKET.json"
)
REGISTRY = (
    BT_ROOT
    / "derived/contract_examples/btc_completion_state_machine_from_l1_flow_taker_normalized_v1/"
    "candidate_registry.csv"
)
STATE_MANIFEST = (
    BT_ROOT
    / "derived/contract_examples/btc_completion_state_machine_from_l1_flow_taker_normalized_v1/"
    "RESULT_SUMMARY_MANIFEST.json"
)

STATUS = "KEEP_BTC_CORE_RESIDUAL_RISK_PROFILE_PREPARED_REVIEW_ONLY_NOT_OOS_READY"
DUST = 1e-9


@dataclass
class Lot:
    qty: float
    px: float
    side: str
    ts_ms: int
    action_id: str
    candidate_id: str
    condition_id: str
    slug: str
    side_alignment: str
    offset_s: float
    source_row_hash: str
    exante_row_hash: str


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
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


def f(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def price_bucket(px: float) -> str:
    if px < 0.20:
        return "00_20"
    if px < 0.35:
        return "20_35"
    if px < 0.50:
        return "35_50"
    if px < 0.65:
        return "50_65"
    if px < 0.80:
        return "65_80"
    return "80_100"


def offset_bucket(offset_s: float) -> str:
    if offset_s < 30:
        return "000_030"
    if offset_s < 60:
        return "030_060"
    if offset_s < 90:
        return "060_090"
    if offset_s < 120:
        return "090_120"
    return "120_plus"


def pair_inventory(inv: dict[str, deque[Lot]]) -> None:
    yes = inv["YES"]
    no = inv["NO"]
    while yes and no:
        a = yes[0]
        b = no[0]
        take = min(a.qty, b.qty)
        if take <= DUST:
            break
        a.qty -= take
        b.qty -= take
        if a.qty <= DUST:
            yes.popleft()
        if b.qty <= DUST:
            no.popleft()


def summarize_group(rows: list[dict[str, Any]], group_fields: list[str], total_residual_cost: float) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        key = tuple(row[field] for field in group_fields)
        out = groups.setdefault(
            key,
            {
                **{field: row[field] for field in group_fields},
                "residual_lot_count": 0,
                "residual_qty": 0.0,
                "residual_cost": 0.0,
                "residual_settle_payout": 0.0,
                "residual_settle_pnl": 0.0,
                "winning_residual_qty": 0.0,
                "losing_residual_qty": 0.0,
            },
        )
        qty = float(row["residual_qty"])
        cost = float(row["residual_cost"])
        payout = float(row["residual_settle_payout"])
        pnl = float(row["residual_settle_pnl"])
        out["residual_lot_count"] += 1
        out["residual_qty"] += qty
        out["residual_cost"] += cost
        out["residual_settle_payout"] += payout
        out["residual_settle_pnl"] += pnl
        if payout > 0:
            out["winning_residual_qty"] += qty
        else:
            out["losing_residual_qty"] += qty
    output = []
    for out in groups.values():
        out["residual_qty"] = round(out["residual_qty"], 6)
        out["residual_cost"] = round(out["residual_cost"], 6)
        out["residual_settle_payout"] = round(out["residual_settle_payout"], 6)
        out["residual_settle_pnl"] = round(out["residual_settle_pnl"], 6)
        out["winning_residual_qty"] = round(out["winning_residual_qty"], 6)
        out["losing_residual_qty"] = round(out["losing_residual_qty"], 6)
        out["residual_cost_share"] = round(out["residual_cost"] / total_residual_cost, 6) if total_residual_cost else 0.0
        output.append(out)
    output.sort(key=lambda row: (float(row["residual_settle_pnl"]), -float(row["residual_cost"])))
    return output


def command_preview(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                "echo 'NOT_AUTHORIZED: residual profile is review-only and cannot start OOS/live/runner paths.' >&2",
                "exit 66",
                "",
            ]
        ),
        encoding="utf-8",
    )
    path.chmod(0o755)


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ledger_rows = read_csv(LEDGER)
    registry_rows = read_csv(REGISTRY)
    registry_by_key = {(row["candidate_id"], row["action_id"]): row for row in registry_rows}
    winner_by_condition = {
        row["condition_id"]: row["winner_side"]
        for row in registry_rows
        if row.get("winner_side") in {"YES", "NO"}
    }

    states: dict[str, dict[str, deque[Lot]]] = defaultdict(lambda: {"YES": deque(), "NO": deque()})
    for row in sorted(ledger_rows, key=lambda item: int(float(item["action_id"]))):
        condition_id = row["condition_id"]
        side = row["side"]
        lot = Lot(
            qty=f(row["seed_qty"]),
            px=f(row["seed_px"]),
            side=side,
            ts_ms=int(float(row["ts_ms"])),
            action_id=row["action_id"],
            candidate_id=row["candidate_id"],
            condition_id=condition_id,
            slug=row["slug"],
            side_alignment=row["side_alignment"],
            offset_s=f(row["offset_s"]),
            source_row_hash=row["source_row_hash"],
            exante_row_hash=row["exante_row_hash"],
        )
        states[condition_id][side].append(lot)
        pair_inventory(states[condition_id])

    residual_rows: list[dict[str, Any]] = []
    for condition_id, inv in states.items():
        winner = winner_by_condition.get(condition_id, "")
        for side in ("YES", "NO"):
            for lot in inv[side]:
                if lot.qty <= DUST:
                    continue
                key = (lot.candidate_id, lot.action_id)
                source = registry_by_key.get(key, {})
                cost = lot.qty * lot.px
                payout = lot.qty if side == winner else 0.0
                row = {
                    "candidate_id": lot.candidate_id,
                    "action_id": lot.action_id,
                    "condition_id": condition_id,
                    "slug": lot.slug,
                    "side": side,
                    "winner_side": winner,
                    "residual_won": side == winner,
                    "side_alignment": lot.side_alignment,
                    "seed_px": round(lot.px, 6),
                    "seed_px_bucket": price_bucket(lot.px),
                    "offset_s": round(lot.offset_s, 6),
                    "offset_bucket": offset_bucket(lot.offset_s),
                    "residual_qty": round(lot.qty, 6),
                    "residual_cost": round(cost, 6),
                    "residual_settle_payout": round(payout, 6),
                    "residual_settle_pnl": round(payout - cost, 6),
                    "source_row_hash": lot.source_row_hash,
                    "exante_row_hash": lot.exante_row_hash,
                    "source_registry_pair_qty_after_seed": source.get("pair_qty_after_seed", ""),
                    "source_registry_inventory_yes_qty_after": source.get("inventory_yes_qty_after", ""),
                    "source_registry_inventory_yes_cost_after": source.get("inventory_yes_cost_after", ""),
                }
                residual_rows.append(row)

    total_qty = sum(float(row["residual_qty"]) for row in residual_rows)
    total_cost = sum(float(row["residual_cost"]) for row in residual_rows)
    total_payout = sum(float(row["residual_settle_payout"]) for row in residual_rows)
    total_pnl = sum(float(row["residual_settle_pnl"]) for row in residual_rows)
    state_manifest = read_json(STATE_MANIFEST)
    manifest_core = state_manifest["core_metrics"]
    strategy_packet = read_json(STRATEGY_PACKET)

    bucket_rows: list[dict[str, Any]] = []
    for group_name, fields in {
        "side": ["side"],
        "side_alignment": ["side_alignment"],
        "seed_px_bucket": ["seed_px_bucket"],
        "offset_bucket": ["offset_bucket"],
        "side_x_seed_px_bucket": ["side", "seed_px_bucket"],
        "alignment_x_seed_px_bucket": ["side_alignment", "seed_px_bucket"],
    }.items():
        for row in summarize_group(residual_rows, fields, total_cost):
            bucket_rows.append({"group_name": group_name, **row})

    worst_buckets = bucket_rows[:15]
    audit = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS,
        "strategy_id": "BTC_CORE_COMPLETION_V1",
        "scope": "retrospective_residual_risk_audit_labels_only",
        "summary": {
            "source_rows": len(ledger_rows),
            "residual_lot_count": len(residual_rows),
            "residual_qty": round(total_qty, 6),
            "residual_cost": round(total_cost, 6),
            "residual_settle_payout": round(total_payout, 6),
            "residual_settle_pnl": round(total_pnl, 6),
            "manifest_residual_qty": manifest_core["residual_qty"],
            "manifest_residual_cost": manifest_core["residual_cost"],
            "manifest_residual_settle_payout": manifest_core["residual_settle_payout"],
            "manifest_residual_settle_pnl": manifest_core["residual_settle_pnl"],
            "matches_manifest": (
                round(total_qty, 6) == round(float(manifest_core["residual_qty"]), 6)
                and round(total_cost, 6) == round(float(manifest_core["residual_cost"]), 6)
                and round(total_payout, 6) == round(float(manifest_core["residual_settle_payout"]), 6)
                and round(total_pnl, 6) == round(float(manifest_core["residual_settle_pnl"]), 6)
            ),
            "residual_cost_rate": manifest_core["residual_cost_rate"],
            "residual_qty_rate": manifest_core["residual_qty_rate"],
        },
        "interpretation": {
            "residual_profile": (
                "Residual inventory is small relative to total gross cost and is positive in aggregate over this "
                "15-day local replay, but it still needs OOS/source-of-truth freshness and owner-private execution "
                "truth before any promotion."
            ),
            "use_in_controller": (
                "Bucket results may guide future ex-ante risk controls, but winner_side/residual_won are retrospective "
                "labels and must not be consumed by live decisions."
            ),
        },
        "worst_residual_buckets_by_pnl": worst_buckets,
        "inputs": {
            "ledger": str(LEDGER),
            "ledger_sha256": sha256_file(LEDGER),
            "registry": str(REGISTRY),
            "registry_sha256": sha256_file(REGISTRY),
            "state_manifest": str(STATE_MANIFEST),
            "state_manifest_sha256": sha256_file(STATE_MANIFEST),
            "strategy_packet": str(STRATEGY_PACKET),
            "strategy_packet_sha256": sha256_file(STRATEGY_PACKET),
        },
        "non_claims": {
            "oos_ready": False,
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "deployable": False,
        },
    }

    residual_csv = OUTPUT_DIR / "btc_core_terminal_residual_lots.csv"
    bucket_csv = OUTPUT_DIR / "btc_core_terminal_residual_bucket_summary.csv"
    audit_path = OUTPUT_DIR / "BTC_CORE_RESIDUAL_RISK_PROFILE_AUDIT.json"
    note_path = OUTPUT_DIR / "BTC_CORE_RESIDUAL_RISK_PROFILE_NOTE.md"
    preview_path = OUTPUT_DIR / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    hash_manifest_path = OUTPUT_DIR / "BTC_CORE_RESIDUAL_RISK_PROFILE_HASH_MANIFEST.json"

    write_csv(residual_csv, residual_rows)
    write_csv(bucket_csv, bucket_rows)
    write_json(audit_path, audit)
    note_path.write_text(
        "\n".join(
            [
                "# BTC Core Residual Risk Profile",
                "",
                f"Status: `{STATUS}`",
                "",
                "This artifact profiles terminal residual lots from the sanitized BTC core ex-ante action ledger. Settlement labels are used only for retrospective audit.",
                "",
                "The result is not OOS-ready and does not authorize runner/observer/live/private/order paths.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    command_preview(preview_path)

    files = [audit_path, residual_csv, bucket_csv, note_path, preview_path]
    hash_manifest = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS,
        "files": {
            path.name: {"path": str(path), "sha256": sha256_file(path), "size": path.stat().st_size}
            for path in files
        },
    }
    write_json(hash_manifest_path, hash_manifest)
    audit["outputs"] = {
        "residual_lots_csv": str(residual_csv),
        "bucket_summary_csv": str(bucket_csv),
        "hash_manifest": str(hash_manifest_path),
        "hash_manifest_sha256": sha256_file(hash_manifest_path),
    }
    write_json(audit_path, audit)
    hash_manifest["files"][audit_path.name] = {
        "path": str(audit_path),
        "sha256": sha256_file(audit_path),
        "size": audit_path.stat().st_size,
    }
    write_json(hash_manifest_path, hash_manifest)

    print(f"status={STATUS}")
    print(f"output_dir={OUTPUT_DIR}")
    print(f"residual_lot_count={len(residual_rows)}")
    print(f"residual_cost={round(total_cost, 6)}")
    print(f"residual_settle_pnl={round(total_pnl, 6)}")
    print(f"matches_manifest={audit['summary']['matches_manifest']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
