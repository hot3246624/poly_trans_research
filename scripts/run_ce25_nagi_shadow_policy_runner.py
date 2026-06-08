#!/usr/bin/env python3
"""Run CE25/NAGI shadow-policy autoresearch over local completion candidates.

This is local replay/research orchestration only. It never fetches live data,
loads private keys, imports candidates, submits/cancels/redeems orders, or
claims deployability.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

try:
    import duckdb
except ModuleNotFoundError as exc:  # pragma: no cover - operational guard
    raise SystemExit("duckdb is required. Run with python3 in the repo environment.") from exc


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STRATEGY_INPUT = ROOT / "configs" / "ce25_nagi" / "CE25_NAGI_STRATEGY_INPUT_v0.json"
DEFAULT_DATA_ROOT = Path(os.environ.get("POLY_BT_ROOT", "/Users/hot/web3Scientist/poly_backtest_data"))
DEFAULT_MASTER_BASE = (
    DEFAULT_DATA_ROOT
    / "derived"
    / "completion_candidate_pipeline_v1"
    / "local_20260502_20260518_paircap102"
)
OUT_DATASET_TYPE = "ce25_nagi_shadow_policy_autoresearch_v0"
VARIANT_BASE_DATASET_TYPE = "ce25_nagi_policy_variant_candidate_base_v0"
BOOK_SHADOW_SCAN_CACHE_DATASET_TYPE = "ce25_nagi_book_shadow_scan_cache_v0"
BOOK_SHADOW_SCAN_CACHE_VERSION = 1
SIDE_MAP = {
    "UP": "YES",
    "DOWN": "NO",
    "YES": "YES",
    "NO": "NO",
}
BTC_5M_CLOSE_S = 300.0
NON_CLAIMS = {
    "private_truth_ready": False,
    "strategy_promotion_ready": False,
    "live_ready": False,
    "deployable": False,
    "canary_authorized": False,
    "orders_authorized": False,
}
DUST = 1e-9


@dataclass(frozen=True)
class Variant:
    variant_id: str
    policy_id: str
    branch_id: str
    role: str
    first_leg_side: str | None
    yes_no_side: str | None
    offset_min_s: float
    offset_max_s: float
    seed_px_lo: float
    seed_px_hi: float
    seed_l1_pair_cap: float
    completion_sla_s: float
    target_qty: float
    edge: float
    fill_haircut: float
    max_seed_qty: float
    max_open_cost: float
    residual_cooldown_age_s: float
    residual_cooldown_cost_cap: float
    entry_requires_pair_cap: bool = False
    entry_requires_opposite_depth: bool = False
    entry_requires_opposite_qty: bool = False
    same_row_pair_only: bool = False
    max_pair_delay_ms: int | None = None
    mutation_parent: str | None = None
    mutation_note: str | None = None


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def safe_name(value: str) -> str:
    out = []
    for ch in value:
        if ch.isalnum() or ch in "._=-":
            out.append(ch)
        else:
            out.append("_")
    return "".join(out).strip("_") or "run"


def qlit(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fp:
        for block in iter(lambda: fp.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def pct(num: float, den: float) -> float:
    return round(num / den, 6) if abs(den) > DUST else 0.0


def base_variant_defaults(policy_id: str) -> dict[str, Any]:
    return {
        "target_qty": 5.0,
        "edge": 0.055,
        "fill_haircut": 0.25,
        "max_seed_qty": 60.0,
        "max_open_cost": 250.0,
        "residual_cooldown_age_s": 30.0,
        "residual_cooldown_cost_cap": 0.5,
        "entry_requires_pair_cap": False,
        "entry_requires_opposite_depth": False,
        "entry_requires_opposite_qty": False,
        "same_row_pair_only": False,
        "max_pair_delay_ms": None,
        "completion_sla_s": 30.0 if policy_id != "NAGI_LAST60_MIDPRICE_FASTPAIR_V1" else 15.0,
    }


def make_variant(
    *,
    policy_id: str,
    branch_id: str,
    role: str,
    first_leg_side: str | None,
    offset_min_s: float,
    offset_max_s: float,
    seed_px_lo: float,
    seed_px_hi: float,
    pair_cap: float,
    mutation_parent: str | None = None,
    mutation_note: str | None = None,
    **overrides: Any,
) -> Variant:
    defaults = base_variant_defaults(policy_id)
    defaults.update(overrides)
    yes_no = SIDE_MAP[first_leg_side] if first_leg_side else None
    variant_id = safe_name(
        f"{policy_id}__{branch_id}__{first_leg_side or 'ANY'}__o{offset_min_s:g}_{offset_max_s:g}"
        f"__px{seed_px_lo:.2f}_{seed_px_hi:.2f}__pc{pair_cap:.3f}"
    )
    return Variant(
        variant_id=variant_id,
        policy_id=policy_id,
        branch_id=branch_id,
        role=role,
        first_leg_side=first_leg_side,
        yes_no_side=yes_no,
        offset_min_s=float(offset_min_s),
        offset_max_s=float(offset_max_s),
        seed_px_lo=float(seed_px_lo),
        seed_px_hi=float(seed_px_hi),
        seed_l1_pair_cap=float(pair_cap),
        completion_sla_s=float(defaults["completion_sla_s"]),
        target_qty=float(defaults["target_qty"]),
        edge=float(defaults["edge"]),
        fill_haircut=float(defaults["fill_haircut"]),
        max_seed_qty=float(defaults["max_seed_qty"]),
        max_open_cost=float(defaults["max_open_cost"]),
        residual_cooldown_age_s=float(defaults["residual_cooldown_age_s"]),
        residual_cooldown_cost_cap=float(defaults["residual_cooldown_cost_cap"]),
        entry_requires_pair_cap=bool(defaults["entry_requires_pair_cap"]),
        entry_requires_opposite_depth=bool(defaults["entry_requires_opposite_depth"]),
        entry_requires_opposite_qty=bool(defaults["entry_requires_opposite_qty"]),
        same_row_pair_only=bool(defaults["same_row_pair_only"]),
        max_pair_delay_ms=None if defaults["max_pair_delay_ms"] is None else int(defaults["max_pair_delay_ms"]),
        mutation_parent=mutation_parent,
        mutation_note=mutation_note,
    )


def effective_completion_sla_ms(variant: Variant) -> int:
    if variant.max_pair_delay_ms is not None:
        return max(0, int(variant.max_pair_delay_ms))
    return max(0, int(round(variant.completion_sla_s * 1000)))


def seed_variants(strategy: dict[str, Any], include_controls: bool) -> list[Variant]:
    variants: list[Variant] = []
    policy_ids = {str(p["policy_id"]) for p in strategy.get("policies", [])}
    if "9F5F_BTC_LAST60_MIDPRICE_V1" in policy_ids:
        for branch_id, side, px_lo, px_hi in [
            ("last60_up_35_50", "UP", 0.35, 0.50),
            ("last60_down_50_65", "DOWN", 0.50, 0.65),
            ("last60_up_50_65", "UP", 0.50, 0.65),
        ]:
            for pair_cap in (0.95, 0.97):
                variants.append(
                    make_variant(
                        policy_id="9F5F_BTC_LAST60_MIDPRICE_V1",
                        branch_id=branch_id,
                        role="primary",
                        first_leg_side=side,
                        offset_min_s=240,
                        offset_max_s=300,
                        seed_px_lo=px_lo,
                        seed_px_hi=px_hi,
                        pair_cap=pair_cap,
                        completion_sla_s=15 if pair_cap <= 0.95 else 30,
                        residual_cooldown_cost_cap=0.35,
                    )
                )
        if include_controls:
            variants.append(
                make_variant(
                    policy_id="9F5F_BTC_LAST60_MIDPRICE_V1",
                    branch_id="last60_down_35_50_control",
                    role="control",
                    first_leg_side="DOWN",
                    offset_min_s=240,
                    offset_max_s=300,
                    seed_px_lo=0.35,
                    seed_px_hi=0.50,
                    pair_cap=0.97,
                    completion_sla_s=30,
                    residual_cooldown_cost_cap=0.35,
                )
            )
    if "CE25_BTC5M_LOW_PRICE_TAIL_DOWN_V1" in policy_ids:
        for pair_cap in (0.97, 0.98):
            variants.append(
                make_variant(
                    policy_id="CE25_BTC5M_LOW_PRICE_TAIL_DOWN_V1",
                    branch_id="last60_down_20_35",
                    role="primary",
                    first_leg_side="DOWN",
                    offset_min_s=240,
                    offset_max_s=300,
                    seed_px_lo=0.20,
                    seed_px_hi=0.35,
                    pair_cap=pair_cap,
                    completion_sla_s=15 if pair_cap <= 0.97 else 30,
                )
            )
        if include_controls:
            variants.append(
                make_variant(
                    policy_id="CE25_BTC5M_LOW_PRICE_TAIL_DOWN_V1",
                    branch_id="last60_up_20_35_control",
                    role="control",
                    first_leg_side="UP",
                    offset_min_s=240,
                    offset_max_s=300,
                    seed_px_lo=0.20,
                    seed_px_hi=0.35,
                    pair_cap=0.98,
                )
            )
            variants.append(
                make_variant(
                    policy_id="CE25_BTC5M_LOW_PRICE_TAIL_DOWN_V1",
                    branch_id="last60_down_10_20_neighbor",
                    role="neighbor_control",
                    first_leg_side="DOWN",
                    offset_min_s=240,
                    offset_max_s=300,
                    seed_px_lo=0.10,
                    seed_px_hi=0.20,
                    pair_cap=0.98,
                )
            )
    if "CE25_BTC5M_LOW_PRICE_TAIL_SIDE_SPLIT_V2" in policy_ids:
        for side in ("DOWN", "UP"):
            for pair_cap in (0.97, 0.98):
                variants.append(
                    make_variant(
                        policy_id="CE25_BTC5M_LOW_PRICE_TAIL_SIDE_SPLIT_V2",
                        branch_id=f"last60_{side.lower()}_20_35_side_split",
                        role="primary",
                        first_leg_side=side,
                        offset_min_s=240,
                        offset_max_s=300,
                        seed_px_lo=0.20,
                        seed_px_hi=0.35,
                        pair_cap=pair_cap,
                        completion_sla_s=15 if pair_cap <= 0.97 else 30,
                    )
                )
        if include_controls:
            for side in ("DOWN", "UP"):
                variants.append(
                    make_variant(
                        policy_id="CE25_BTC5M_LOW_PRICE_TAIL_SIDE_SPLIT_V2",
                        branch_id=f"last60_{side.lower()}_10_20_neighbor",
                        role="neighbor_control",
                        first_leg_side=side,
                        offset_min_s=240,
                        offset_max_s=300,
                        seed_px_lo=0.10,
                        seed_px_hi=0.20,
                        pair_cap=0.98,
                    )
                )
    if "CE25_BTC5M_HIGH_PRICE_STOP_1_5M_V1" in policy_ids:
        variants.append(
            make_variant(
                policy_id="CE25_BTC5M_HIGH_PRICE_STOP_1_5M_V1",
                branch_id="one_to_five_min_any_65_80",
                role="risk_filter_primary",
                first_leg_side=None,
                offset_min_s=0,
                offset_max_s=240,
                seed_px_lo=0.65,
                seed_px_hi=0.80,
                pair_cap=0.98,
                target_qty=3,
                max_seed_qty=30,
            )
        )
        if include_controls:
            variants.append(
                make_variant(
                    policy_id="CE25_BTC5M_HIGH_PRICE_STOP_1_5M_V1",
                    branch_id="last60_any_65_80_negative_control",
                    role="negative_control",
                    first_leg_side=None,
                    offset_min_s=240,
                    offset_max_s=300,
                    seed_px_lo=0.65,
                    seed_px_hi=0.80,
                    pair_cap=0.98,
                    target_qty=3,
                    max_seed_qty=30,
                )
            )
    if "NAGI_LAST60_MIDPRICE_FASTPAIR_V1" in policy_ids:
        variants.append(
            make_variant(
                policy_id="NAGI_LAST60_MIDPRICE_FASTPAIR_V1",
                branch_id="last60_up_35_50_fastpair",
                role="execution_template",
                first_leg_side="UP",
                offset_min_s=240,
                offset_max_s=300,
                seed_px_lo=0.35,
                seed_px_hi=0.50,
                pair_cap=0.97,
                completion_sla_s=15,
                residual_cooldown_cost_cap=0.25,
            )
        )
        variants.append(
            make_variant(
                policy_id="NAGI_LAST60_MIDPRICE_FASTPAIR_V1",
                branch_id="last60_down_50_65_fastpair",
                role="execution_template",
                first_leg_side="DOWN",
                offset_min_s=240,
                offset_max_s=300,
                seed_px_lo=0.50,
                seed_px_hi=0.65,
                pair_cap=0.97,
                completion_sla_s=15,
                residual_cooldown_cost_cap=0.25,
            )
        )
    return variants


def mutation_variants(seeds: list[Variant], max_mutations: int) -> list[Variant]:
    out: list[Variant] = []
    for v in seeds:
        if len(out) >= max_mutations:
            break
        if v.role not in {"primary", "execution_template", "risk_filter_primary"}:
            continue
        for cap_delta, note in [(-0.015, "much_stricter_pair_cap"), (-0.01, "stricter_pair_cap"), (0.01, "wider_pair_cap"), (0.02, "much_wider_pair_cap")]:
            if len(out) >= max_mutations:
                break
            cap = max(0.80, min(1.02, v.seed_l1_pair_cap + cap_delta))
            out.append(
                make_variant(
                    policy_id=v.policy_id,
                    branch_id=f"{v.branch_id}_{note}",
                    role="mutation",
                    first_leg_side=v.first_leg_side,
                    offset_min_s=v.offset_min_s,
                    offset_max_s=v.offset_max_s,
                    seed_px_lo=v.seed_px_lo,
                    seed_px_hi=v.seed_px_hi,
                    pair_cap=cap,
                    completion_sla_s=v.completion_sla_s,
                    target_qty=v.target_qty,
                    max_seed_qty=v.max_seed_qty,
                    residual_cooldown_cost_cap=v.residual_cooldown_cost_cap,
                    mutation_parent=v.variant_id,
                    mutation_note=note,
                )
            )
        if len(out) >= max_mutations:
            break
        out.append(
            make_variant(
                policy_id=v.policy_id,
                branch_id=f"{v.branch_id}_longer_sla_60s",
                role="mutation",
                first_leg_side=v.first_leg_side,
                offset_min_s=v.offset_min_s,
                offset_max_s=v.offset_max_s,
                seed_px_lo=v.seed_px_lo,
                seed_px_hi=v.seed_px_hi,
                pair_cap=v.seed_l1_pair_cap,
                completion_sla_s=60,
                target_qty=v.target_qty,
                max_seed_qty=v.max_seed_qty,
                residual_cooldown_cost_cap=v.residual_cooldown_cost_cap,
                mutation_parent=v.variant_id,
                mutation_note="longer_completion_sla_60s",
            )
        )
        if len(out) >= max_mutations:
            break
        out.append(
            make_variant(
                policy_id=v.policy_id,
                branch_id=f"{v.branch_id}_same_row_pair_only",
                role="mutation",
                first_leg_side=v.first_leg_side,
                offset_min_s=v.offset_min_s,
                offset_max_s=v.offset_max_s,
                seed_px_lo=v.seed_px_lo,
                seed_px_hi=v.seed_px_hi,
                pair_cap=v.seed_l1_pair_cap,
                completion_sla_s=0,
                target_qty=v.target_qty,
                max_seed_qty=v.max_seed_qty,
                residual_cooldown_cost_cap=v.residual_cooldown_cost_cap,
                entry_requires_pair_cap=True,
                entry_requires_opposite_depth=True,
                same_row_pair_only=True,
                mutation_parent=v.variant_id,
                mutation_note="residual_killer_same_row_pair_only",
            )
        )
        if len(out) >= max_mutations:
            break
        out.append(
            make_variant(
                policy_id=v.policy_id,
                branch_id=f"{v.branch_id}_entry_paircap_required",
                role="mutation",
                first_leg_side=v.first_leg_side,
                offset_min_s=v.offset_min_s,
                offset_max_s=v.offset_max_s,
                seed_px_lo=v.seed_px_lo,
                seed_px_hi=v.seed_px_hi,
                pair_cap=v.seed_l1_pair_cap,
                completion_sla_s=v.completion_sla_s,
                target_qty=v.target_qty,
                max_seed_qty=v.max_seed_qty,
                residual_cooldown_cost_cap=v.residual_cooldown_cost_cap,
                entry_requires_pair_cap=True,
                entry_requires_opposite_depth=True,
                same_row_pair_only=False,
                mutation_parent=v.variant_id,
                mutation_note="residual_killer_entry_requires_paircap_and_opposite_depth",
            )
        )
        for strict_mode, same_row_only in (("same_row", True), ("entry_paircap", False)):
            for cap in (0.965, 0.970, 0.975, v.seed_l1_pair_cap):
                if len(out) >= max_mutations:
                    break
                out.append(
                    make_variant(
                        policy_id=v.policy_id,
                        branch_id=f"{v.branch_id}_{strict_mode}_cap_{cap:.3f}",
                        role="mutation",
                        first_leg_side=v.first_leg_side,
                        offset_min_s=v.offset_min_s,
                        offset_max_s=v.offset_max_s,
                        seed_px_lo=v.seed_px_lo,
                        seed_px_hi=v.seed_px_hi,
                        pair_cap=cap,
                        completion_sla_s=0 if same_row_only else v.completion_sla_s,
                        target_qty=v.target_qty,
                        max_seed_qty=v.max_seed_qty,
                        residual_cooldown_cost_cap=v.residual_cooldown_cost_cap,
                        entry_requires_pair_cap=True,
                        entry_requires_opposite_depth=True,
                        same_row_pair_only=same_row_only,
                        mutation_parent=v.variant_id,
                        mutation_note=f"strict_paircap_sweep_{strict_mode}_cap_{cap:.3f}",
                    )
                )
            if v.policy_id == "CE25_BTC5M_HIGH_PRICE_STOP_1_5M_V1":
                for cap in (0.968, 0.969, 0.970):
                    for px_hi in (0.78, 0.79, v.seed_px_hi):
                        if len(out) >= max_mutations:
                            break
                        if px_hi > v.seed_px_hi + DUST or px_hi <= v.seed_px_lo:
                            continue
                        out.append(
                            make_variant(
                                policy_id=v.policy_id,
                                branch_id=f"{v.branch_id}_{strict_mode}_micro_cap_{cap:.3f}_pxhi_{px_hi:.2f}",
                                role="mutation",
                                first_leg_side=v.first_leg_side,
                                offset_min_s=v.offset_min_s,
                                offset_max_s=v.offset_max_s,
                                seed_px_lo=v.seed_px_lo,
                                seed_px_hi=px_hi,
                                pair_cap=cap,
                                completion_sla_s=0 if same_row_only else v.completion_sla_s,
                                target_qty=v.target_qty,
                                max_seed_qty=v.max_seed_qty,
                                residual_cooldown_cost_cap=v.residual_cooldown_cost_cap,
                                entry_requires_pair_cap=True,
                                entry_requires_opposite_depth=True,
                                same_row_pair_only=same_row_only,
                                mutation_parent=v.variant_id,
                                mutation_note=f"ce25_high_price_micro_tighten_{strict_mode}_cap_{cap:.3f}_pxhi_{px_hi:.2f}",
                            )
                        )
                for cap, px_hi in ((0.965, v.seed_px_hi), (0.970, 0.78), (0.970, 0.79), (0.970, v.seed_px_hi)):
                    if len(out) >= max_mutations:
                        break
                    if px_hi > v.seed_px_hi + DUST or px_hi <= v.seed_px_lo:
                        continue
                    out.append(
                        make_variant(
                            policy_id=v.policy_id,
                            branch_id=f"{v.branch_id}_{strict_mode}_top1_qty_cap_{cap:.3f}_pxhi_{px_hi:.2f}",
                            role="mutation",
                            first_leg_side=v.first_leg_side,
                            offset_min_s=v.offset_min_s,
                            offset_max_s=v.offset_max_s,
                            seed_px_lo=v.seed_px_lo,
                            seed_px_hi=px_hi,
                            pair_cap=cap,
                            completion_sla_s=0 if same_row_only else v.completion_sla_s,
                            target_qty=v.target_qty,
                            max_seed_qty=v.max_seed_qty,
                            residual_cooldown_cost_cap=v.residual_cooldown_cost_cap,
                            entry_requires_pair_cap=True,
                            entry_requires_opposite_depth=True,
                            entry_requires_opposite_qty=True,
                            same_row_pair_only=same_row_only,
                            mutation_parent=v.variant_id,
                            mutation_note=f"ce25_high_price_top1_qty_gate_{strict_mode}_cap_{cap:.3f}_pxhi_{px_hi:.2f}",
                        )
                    )
                for target_qty in (5.0, 8.0):
                    for px_hi in (0.79, v.seed_px_hi):
                        if len(out) >= max_mutations:
                            break
                        if px_hi > v.seed_px_hi + DUST or px_hi <= v.seed_px_lo:
                            continue
                        out.append(
                            make_variant(
                                policy_id=v.policy_id,
                                branch_id=(
                                    f"{v.branch_id}_{strict_mode}_top1_qty_target_qty_{target_qty:g}"
                                    f"_cap_0.970_pxhi_{px_hi:.2f}"
                                ),
                                role="mutation",
                                first_leg_side=v.first_leg_side,
                                offset_min_s=v.offset_min_s,
                                offset_max_s=v.offset_max_s,
                                seed_px_lo=v.seed_px_lo,
                                seed_px_hi=px_hi,
                                pair_cap=0.970,
                                completion_sla_s=0 if same_row_only else v.completion_sla_s,
                                target_qty=target_qty,
                                max_seed_qty=max(v.max_seed_qty, target_qty),
                                residual_cooldown_cost_cap=v.residual_cooldown_cost_cap,
                                entry_requires_pair_cap=True,
                                entry_requires_opposite_depth=True,
                                entry_requires_opposite_qty=True,
                                same_row_pair_only=same_row_only,
                                mutation_parent=v.variant_id,
                                mutation_note=(
                                    f"ce25_high_price_top1_qty_gate_capacity_{strict_mode}"
                                    f"_target_qty_{target_qty:g}_cap_0.970_pxhi_{px_hi:.2f}"
                                ),
                            )
                        )
            if v.policy_id == "CE25_BTC5M_LOW_PRICE_TAIL_SIDE_SPLIT_V2":
                for cap in (0.965, 0.970):
                    if len(out) >= max_mutations:
                        break
                    out.append(
                        make_variant(
                            policy_id=v.policy_id,
                            branch_id=f"{v.branch_id}_{strict_mode}_top1_qty_cap_{cap:.3f}",
                            role="mutation",
                            first_leg_side=v.first_leg_side,
                            offset_min_s=v.offset_min_s,
                            offset_max_s=v.offset_max_s,
                            seed_px_lo=v.seed_px_lo,
                            seed_px_hi=v.seed_px_hi,
                            pair_cap=cap,
                            completion_sla_s=0 if same_row_only else v.completion_sla_s,
                            target_qty=v.target_qty,
                            max_seed_qty=v.max_seed_qty,
                            residual_cooldown_cost_cap=v.residual_cooldown_cost_cap,
                            entry_requires_pair_cap=True,
                            entry_requires_opposite_depth=True,
                            entry_requires_opposite_qty=True,
                            same_row_pair_only=same_row_only,
                            mutation_parent=v.variant_id,
                            mutation_note=f"ce25_low_tail_top1_qty_gate_{strict_mode}_cap_{cap:.3f}",
                        )
                    )
                for target_qty in (5.0, 8.0):
                    if len(out) >= max_mutations:
                        break
                    out.append(
                        make_variant(
                            policy_id=v.policy_id,
                            branch_id=f"{v.branch_id}_{strict_mode}_top1_qty_target_qty_{target_qty:g}_cap_0.965",
                            role="mutation",
                            first_leg_side=v.first_leg_side,
                            offset_min_s=v.offset_min_s,
                            offset_max_s=v.offset_max_s,
                            seed_px_lo=v.seed_px_lo,
                            seed_px_hi=v.seed_px_hi,
                            pair_cap=0.965,
                            completion_sla_s=0 if same_row_only else v.completion_sla_s,
                            target_qty=target_qty,
                            max_seed_qty=max(v.max_seed_qty, target_qty),
                            residual_cooldown_cost_cap=v.residual_cooldown_cost_cap,
                            entry_requires_pair_cap=True,
                            entry_requires_opposite_depth=True,
                            entry_requires_opposite_qty=True,
                            same_row_pair_only=same_row_only,
                            mutation_parent=v.variant_id,
                            mutation_note=f"ce25_low_tail_top1_qty_gate_capacity_{strict_mode}_target_qty_{target_qty:g}_cap_0.965",
                        )
                    )
            for target_qty in (1.0, 2.0, 3.0, 5.0, 8.0, 13.0):
                if len(out) >= max_mutations:
                    break
                out.append(
                    make_variant(
                        policy_id=v.policy_id,
                        branch_id=f"{v.branch_id}_{strict_mode}_target_qty_{target_qty:g}",
                        role="mutation",
                        first_leg_side=v.first_leg_side,
                        offset_min_s=v.offset_min_s,
                        offset_max_s=v.offset_max_s,
                        seed_px_lo=v.seed_px_lo,
                        seed_px_hi=v.seed_px_hi,
                        pair_cap=v.seed_l1_pair_cap,
                        completion_sla_s=0 if same_row_only else v.completion_sla_s,
                        target_qty=target_qty,
                        max_seed_qty=max(v.max_seed_qty, target_qty),
                        residual_cooldown_cost_cap=v.residual_cooldown_cost_cap,
                        entry_requires_pair_cap=True,
                        entry_requires_opposite_depth=True,
                        same_row_pair_only=same_row_only,
                        mutation_parent=v.variant_id,
                        mutation_note=f"capacity_stress_{strict_mode}_target_qty_{target_qty:g}",
                    )
                )
            for fill_haircut in (0.25, 0.5, 0.75, 1.0):
                if len(out) >= max_mutations:
                    break
                out.append(
                    make_variant(
                        policy_id=v.policy_id,
                        branch_id=f"{v.branch_id}_{strict_mode}_haircut_{int(fill_haircut * 100):03d}pct",
                        role="mutation",
                        first_leg_side=v.first_leg_side,
                        offset_min_s=v.offset_min_s,
                        offset_max_s=v.offset_max_s,
                        seed_px_lo=v.seed_px_lo,
                        seed_px_hi=v.seed_px_hi,
                        pair_cap=v.seed_l1_pair_cap,
                        completion_sla_s=0 if same_row_only else v.completion_sla_s,
                        target_qty=v.target_qty,
                        fill_haircut=fill_haircut,
                        max_seed_qty=v.max_seed_qty,
                        residual_cooldown_cost_cap=v.residual_cooldown_cost_cap,
                        entry_requires_pair_cap=True,
                        entry_requires_opposite_depth=True,
                        same_row_pair_only=same_row_only,
                        mutation_parent=v.variant_id,
                        mutation_note=f"depth_stress_{strict_mode}_fill_haircut_{fill_haircut:g}",
                    )
                )
        for delay_ms in (250, 500, 1000, 3000):
            if len(out) >= max_mutations:
                break
            out.append(
                make_variant(
                    policy_id=v.policy_id,
                    branch_id=f"{v.branch_id}_pair_delay_le_{delay_ms}ms",
                    role="mutation",
                    first_leg_side=v.first_leg_side,
                    offset_min_s=v.offset_min_s,
                    offset_max_s=v.offset_max_s,
                    seed_px_lo=v.seed_px_lo,
                    seed_px_hi=v.seed_px_hi,
                    pair_cap=v.seed_l1_pair_cap,
                    completion_sla_s=delay_ms / 1000.0,
                    target_qty=v.target_qty,
                    max_seed_qty=v.max_seed_qty,
                    residual_cooldown_cost_cap=v.residual_cooldown_cost_cap,
                    entry_requires_pair_cap=False,
                    entry_requires_opposite_depth=True,
                    same_row_pair_only=False,
                    max_pair_delay_ms=delay_ms,
                    mutation_parent=v.variant_id,
                    mutation_note=f"residual_killer_completion_delay_le_{delay_ms}ms",
                )
            )
        if len(out) >= max_mutations:
            break
        width = v.seed_px_hi - v.seed_px_lo
        if width >= 0.1:
            mid = (v.seed_px_lo + v.seed_px_hi) / 2
            out.append(
                make_variant(
                    policy_id=v.policy_id,
                    branch_id=f"{v.branch_id}_lower_half",
                    role="mutation",
                    first_leg_side=v.first_leg_side,
                    offset_min_s=v.offset_min_s,
                    offset_max_s=v.offset_max_s,
                    seed_px_lo=v.seed_px_lo,
                    seed_px_hi=mid,
                    pair_cap=v.seed_l1_pair_cap,
                    completion_sla_s=v.completion_sla_s,
                    target_qty=v.target_qty,
                    max_seed_qty=v.max_seed_qty,
                    residual_cooldown_cost_cap=v.residual_cooldown_cost_cap,
                    mutation_parent=v.variant_id,
                    mutation_note="lower_price_half",
                )
            )
            if len(out) >= max_mutations:
                break
            out.append(
                make_variant(
                    policy_id=v.policy_id,
                    branch_id=f"{v.branch_id}_upper_half",
                    role="mutation",
                    first_leg_side=v.first_leg_side,
                    offset_min_s=v.offset_min_s,
                    offset_max_s=v.offset_max_s,
                    seed_px_lo=mid,
                    seed_px_hi=v.seed_px_hi,
                    pair_cap=v.seed_l1_pair_cap,
                    completion_sla_s=v.completion_sla_s,
                    target_qty=v.target_qty,
                    max_seed_qty=v.max_seed_qty,
                    residual_cooldown_cost_cap=v.residual_cooldown_cost_cap,
                    mutation_parent=v.variant_id,
                    mutation_note="upper_price_half",
                )
            )
    return out


def source_master_paths(master_base: Path) -> tuple[Path, Path]:
    manifest_path = master_base / "CANDIDATE_BASE_MANIFEST.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"missing master candidate manifest: {manifest_path}")
    manifest = load_json(manifest_path)
    db_path = master_base / str(manifest.get("outputs", {}).get("duckdb", "candidate_base.duckdb"))
    if not db_path.is_file():
        raise FileNotFoundError(f"missing master candidate db: {db_path}")
    return manifest_path, db_path


def variant_where_sql(variant: Variant) -> str:
    clauses = [
        "event_kind = 'public_trade'",
        "public_trade_price IS NOT NULL",
        f"offset_s >= {variant.offset_min_s}",
        f"offset_s < {variant.offset_max_s}",
        f"public_trade_price >= {variant.seed_px_lo}",
        f"public_trade_price <= {variant.seed_px_hi}",
        f"l1_pair_ask IS NOT NULL AND l1_pair_ask <= {variant.seed_l1_pair_cap}",
    ]
    if variant.yes_no_side:
        clauses.append(f"side = {qlit(variant.yes_no_side)}")
    return " AND ".join(f"({clause})" for clause in clauses)


def build_variant_base(master_base: Path, variant: Variant, out_dir: Path, *, force: bool) -> dict[str, Any]:
    manifest_path, master_db = source_master_paths(master_base)
    master_manifest = load_json(manifest_path)
    base_dir = out_dir / "candidate_bases" / variant.variant_id
    if base_dir.exists():
        if not force:
            raise FileExistsError(f"variant candidate base exists; pass --force: {base_dir}")
        shutil.rmtree(base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    db_path = base_dir / "candidate_base.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute(f"ATTACH {qlit(master_db)} AS src (READ_ONLY)")
    where_sql = variant_where_sql(variant)
    conn.execute(f"CREATE TABLE candidate_base AS SELECT * FROM src.candidate_base WHERE {where_sql}")
    row_count = int(conn.execute("SELECT COUNT(*) FROM candidate_base").fetchone()[0])
    market_count = int(conn.execute("SELECT COUNT(DISTINCT condition_id) FROM candidate_base").fetchone()[0])
    day_counts = dict(conn.execute("SELECT day, COUNT(*) FROM candidate_base GROUP BY 1 ORDER BY 1").fetchall())
    reason_counts = dict(conn.execute("SELECT candidate_reason, COUNT(*) FROM candidate_base GROUP BY 1 ORDER BY 1").fetchall())
    schema = [{"name": r[0], "type": r[1]} for r in conn.execute("DESCRIBE candidate_base").fetchall()]
    parquet_path = base_dir / "candidate_base.parquet"
    conn.execute(f"COPY candidate_base TO {qlit(parquet_path)} (FORMAT PARQUET, COMPRESSION ZSTD)")
    conn.close()
    manifest = {
        "created_at": utc_now(),
        "dataset_type": VARIANT_BASE_DATASET_TYPE,
        "data_root": master_manifest.get("data_root"),
        "source_dataset_type": master_manifest.get("dataset_type"),
        "source_candidate_base_manifest": str(manifest_path),
        "source_candidate_base_manifest_sha256": sha256_file(manifest_path),
        "strategy_owner_line": "CE25_NAGI_RESEARCH",
        "policy_id": variant.policy_id,
        "branch_id": variant.branch_id,
        "variant_id": variant.variant_id,
        "side_mapping_assumption": "UP=YES, DOWN=NO",
        "row_count": row_count,
        "market_count": market_count,
        "labels": master_manifest.get("labels", []),
        "days": master_manifest.get("days", []),
        "market_prefix": master_manifest.get("market_prefix", []),
        "assets": master_manifest.get("assets", []),
        "excluded_labels_or_days": master_manifest.get("excluded_labels_or_days", []),
        "filters": {
            **asdict(variant),
            "where_sql": where_sql,
            "price_proxy": "public_trade_price from completion_unwind_event_store_v2 candidate_base",
            "offset_s_semantics": "seconds_since_market_open_for_btc_5m; time_to_close_s = 300 - offset_s",
            "not_live_book_ask": True,
        },
        "day_counts": day_counts,
        "candidate_reason_counts": reason_counts,
        "schema": schema,
        "outputs": {
            "duckdb": "candidate_base.duckdb",
            "duckdb_table": "candidate_base",
            "parquet": "candidate_base.parquet",
        },
    }
    write_json(base_dir / "CANDIDATE_BASE_MANIFEST.json", manifest)
    return {"candidate_base_dir": str(base_dir), "row_count": row_count, "market_count": market_count}


def state_machine_command(
    variant: Variant,
    candidate_base_dir: Path,
    output_dir: Path,
    fee_rate: float,
) -> list[str]:
    return [
        sys.executable,
        str(ROOT / "scripts" / "run_completion_candidate_state_machine.py"),
        "--candidate-base-dir",
        str(candidate_base_dir),
        "--output-dir",
        str(output_dir),
        "--edge",
        str(variant.edge),
        "--target-qty",
        str(variant.target_qty),
        "--seed-px-lo",
        str(variant.seed_px_lo),
        "--seed-px-hi",
        str(variant.seed_px_hi),
        "--seed-offset-max-s",
        str(variant.offset_max_s),
        "--seed-l1-pair-cap",
        str(variant.seed_l1_pair_cap),
        "--offset-min-s",
        str(variant.offset_min_s),
        "--offset-max-s",
        str(variant.offset_max_s),
        "--cooldown-s",
        str(max(1.0, min(variant.completion_sla_s, 30.0))),
        "--fill-haircut",
        str(variant.fill_haircut),
        "--max-seed-qty",
        str(variant.max_seed_qty),
        "--max-open-cost",
        str(variant.max_open_cost),
        "--residual-cooldown-age-s",
        str(variant.residual_cooldown_age_s),
        "--residual-cooldown-cost-cap",
        str(variant.residual_cooldown_cost_cap),
        "--fee-model",
        "official_taker",
        "--official-fee-rate",
        str(fee_rate),
        "--public-trade-taker-side",
        "ANY",
    ]


def read_action_diagnostics(path: Path) -> dict[str, Any]:
    actions_path = path / "actions.csv"
    if not actions_path.is_file():
        return {
            "bad_pair_cost_action_share": None,
            "max_single_action_seed_cost": None,
            "action_count": 0,
        }
    total_cost = 0.0
    bad_cost = 0.0
    max_cost = 0.0
    action_count = 0
    with actions_path.open(newline="", encoding="utf-8") as fp:
        for row in csv.DictReader(fp):
            action_count += 1
            seed_cost = as_float(row.get("seed_cost"))
            total_cost += seed_cost
            max_cost = max(max_cost, seed_cost)
            pair_cost = as_float(row.get("pair_cost_wavg_after_seed"), math.nan)
            if math.isfinite(pair_cost) and pair_cost >= 1.0:
                bad_cost += seed_cost
    return {
        "bad_pair_cost_action_share": pct(bad_cost, total_cost),
        "max_single_action_seed_cost": round(max_cost, 6),
        "action_count": action_count,
    }


def ask_for(row: dict[str, Any], side: str) -> tuple[float, float]:
    row_side = str(row.get("side") or "")
    if row_side == side:
        return as_float(row.get("side_ask"), math.nan), as_float(row.get("side_ask_sz"), 0.0)
    if str(row.get("opposite_side") or "") == side:
        return as_float(row.get("opp_ask"), math.nan), as_float(row.get("opp_ask_sz"), 0.0)
    return math.nan, 0.0


def side_matches_entry(row: dict[str, Any], variant: Variant, side: str) -> bool:
    px, _ = ask_for(row, side)
    return math.isfinite(px) and variant.seed_px_lo <= px <= variant.seed_px_hi


def book_shadow_sql(variant: Variant) -> str:
    return book_shadow_scan_sql(variant, max_pair_cap=variant.seed_l1_pair_cap)


def book_shadow_scan_sql(variant: Variant, *, max_pair_cap: float) -> str:
    if variant.yes_no_side:
        entry_expr = (
            f"((side = {qlit(variant.yes_no_side)} AND side_ask BETWEEN {variant.seed_px_lo} AND {variant.seed_px_hi}) "
            f"OR (opposite_side = {qlit(variant.yes_no_side)} AND opp_ask BETWEEN {variant.seed_px_lo} AND {variant.seed_px_hi}))"
        )
    else:
        entry_expr = f"((side_ask BETWEEN {variant.seed_px_lo} AND {variant.seed_px_hi}) OR (opp_ask BETWEEN {variant.seed_px_lo} AND {variant.seed_px_hi}))"
    return f"""
      SELECT
        candidate_row_id,
        source_label,
        day,
        condition_id,
        slug,
        ts_ms,
        ts_iso,
        offset_s,
        side,
        opposite_side,
        winner_side,
        side_ask,
        side_ask_sz,
        opp_ask,
        opp_ask_sz,
        l1_pair_ask,
        candidate_reason
      FROM candidate_base
      WHERE offset_s >= {variant.offset_min_s}
        AND offset_s < {variant.offset_max_s}
        AND side IN ('YES', 'NO')
        AND opposite_side IN ('YES', 'NO')
        AND side_ask IS NOT NULL
        AND opp_ask IS NOT NULL
        AND ({entry_expr} OR l1_pair_ask <= {max_pair_cap})
      ORDER BY condition_id, ts_ms, candidate_row_id
    """


def book_shadow_cache_key(variant: Variant, master_manifest_sha256: str) -> tuple[str, dict[str, Any]]:
    """Cache only the exact scan/filter layer; fee stress and execution controls still run live."""
    signature = {
        "cache_version": BOOK_SHADOW_SCAN_CACHE_VERSION,
        "master_candidate_base_manifest_sha256": master_manifest_sha256,
        "offset_min_s": variant.offset_min_s,
        "offset_max_s": variant.offset_max_s,
        "seed_px_lo": variant.seed_px_lo,
        "seed_px_hi": variant.seed_px_hi,
        "seed_l1_pair_cap": variant.seed_l1_pair_cap,
        "yes_no_side": variant.yes_no_side,
        "side_mapping_assumption": "UP=YES, DOWN=NO",
        "sql": book_shadow_sql(variant),
    }
    blob = json.dumps(signature, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest(), signature


def load_book_shadow_rows(
    variant: Variant,
    master_base: Path,
    cache_dir: Path | None,
    *,
    refresh_cache: bool,
) -> tuple[dict[str, list[dict[str, Any]]], int, dict[str, Any]]:
    manifest_path, master_db = source_master_paths(master_base)
    master_manifest_sha = sha256_file(manifest_path)
    cache_key, signature = book_shadow_cache_key(variant, master_manifest_sha)
    cache_info: dict[str, Any] = {
        "enabled": bool(cache_dir),
        "cache_hit": False,
        "cache_key": cache_key,
        "cache_path": None,
        "manifest_path": None,
    }
    parquet_path: Path | None = None
    if cache_dir is not None:
        cache_path = cache_dir / cache_key
        parquet_path = cache_path / "scan_rows.parquet"
        cache_manifest_path = cache_path / "SCAN_CACHE_MANIFEST.json"
        cache_info.update(
            {
                "cache_path": str(cache_path),
                "manifest_path": str(cache_manifest_path),
            }
        )
        if refresh_cache and cache_path.exists():
            shutil.rmtree(cache_path)
        manifest_ok = False
        if parquet_path.is_file() and cache_manifest_path.is_file():
            try:
                cache_manifest = load_json(cache_manifest_path)
                manifest_ok = (
                    cache_manifest.get("cache_key") == cache_key
                    and cache_manifest.get("signature") == signature
                    and cache_manifest.get("outputs", {}).get("parquet") == "scan_rows.parquet"
                )
            except json.JSONDecodeError:
                manifest_ok = False
        if not manifest_ok:
            cache_path.mkdir(parents=True, exist_ok=True)
            conn = duckdb.connect(str(master_db), read_only=True)
            sql = signature["sql"]
            conn.execute(f"COPY ({sql}) TO {qlit(parquet_path)} (FORMAT PARQUET, COMPRESSION ZSTD)")
            row_count = int(conn.execute(f"SELECT COUNT(*) FROM read_parquet({qlit(parquet_path)})").fetchone()[0])
            market_count = int(
                conn.execute(f"SELECT COUNT(DISTINCT condition_id) FROM read_parquet({qlit(parquet_path)})").fetchone()[0]
            )
            conn.close()
            write_json(
                cache_manifest_path,
                {
                    "created_at": utc_now(),
                    "dataset_type": BOOK_SHADOW_SCAN_CACHE_DATASET_TYPE,
                    "cache_key": cache_key,
                    "signature": signature,
                    "row_count": row_count,
                    "market_count": market_count,
                    "outputs": {"parquet": "scan_rows.parquet"},
                    "source_master_db": str(master_db),
                    "source_manifest": str(manifest_path),
                    "source_manifest_sha256": master_manifest_sha,
                },
            )
            cache_info["cache_hit"] = False
        else:
            cache_info["cache_hit"] = True

    sql_source = f"read_parquet({qlit(parquet_path)})" if parquet_path is not None else f"({signature['sql']})"
    conn = duckdb.connect(str(master_db), read_only=True)
    cur = conn.execute(f"SELECT * FROM {sql_source} ORDER BY condition_id, ts_ms, candidate_row_id")
    cols = [desc[0] for desc in cur.description]
    rows_by_market: dict[str, list[dict[str, Any]]] = {}
    source_row_count = 0
    while True:
        batch = cur.fetchmany(100_000)
        if not batch:
            break
        for raw in batch:
            source_row_count += 1
            row = dict(zip(cols, raw))
            rows_by_market.setdefault(str(row["condition_id"]), []).append(row)
    conn.close()
    cache_info["source_row_count"] = source_row_count
    return rows_by_market, source_row_count, cache_info


def fee_for(qty: float, px: float, fee_rate: float) -> float:
    if qty <= DUST or not math.isfinite(px) or px < 0.0 or px > 1.0 or fee_rate <= 0.0:
        return 0.0
    return qty * fee_rate * px * (1.0 - px)


def run_book_shadow_variant(
    variant: Variant,
    master_base: Path,
    out_root: Path,
    fee_rate: float,
    *,
    cache_dir: Path | None,
    refresh_cache: bool,
    force: bool,
    summary_only: bool,
) -> dict[str, Any]:
    run_dir = out_root / "book_shadow_runs" / variant.variant_id / f"fee_{fee_rate:.4f}".replace(".", "p")
    if run_dir.exists():
        if not force:
            raise FileExistsError(f"book-shadow output exists; pass --force: {run_dir}")
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    rows_by_market, source_row_count, cache_info = load_book_shadow_rows(
        variant,
        master_base,
        cache_dir,
        refresh_cache=refresh_cache,
    )

    action_rows: list[dict[str, Any]] = []
    residual_rows: list[dict[str, Any]] = []
    day_metrics: dict[str, dict[str, float]] = {}
    candidate_seq = 0
    completion_sla_ms = effective_completion_sla_ms(variant)

    def dm(day: str) -> dict[str, float]:
        return day_metrics.setdefault(
            day,
            {
                "markets": 0.0,
                "seed_actions": 0.0,
                "paired_actions": 0.0,
                "residual_actions": 0.0,
                "gross_buy_cost": 0.0,
                "fee": 0.0,
                "pair_qty": 0.0,
                "pair_cost_sum": 0.0,
                "pair_pnl": 0.0,
                "residual_qty": 0.0,
                "residual_cost": 0.0,
                "residual_pnl": 0.0,
            },
        )

    for condition_id, market_rows in rows_by_market.items():
        market_rows.sort(key=lambda r: (int(r["ts_ms"] or 0), int(r["candidate_row_id"] or 0)))
        if not market_rows:
            continue
        day = str(market_rows[0].get("day") or "")
        day_metrics_for_market = dm(day)
        day_metrics_for_market["markets"] += 1
        pending: list[dict[str, Any]] = []
        last_seed_ts: dict[str, int] = {"YES": -(10**18), "NO": -(10**18)}
        market_buy_cost = 0.0
        market_pnl = 0.0
        winner_side = str(market_rows[-1].get("winner_side") or "")

        for row in market_rows:
            ts_ms = int(row.get("ts_ms") or 0)
            still_pending: list[dict[str, Any]] = []
            for lot in pending:
                opp = "NO" if lot["side"] == "YES" else "YES"
                opp_px, opp_sz = ask_for(row, opp)
                expired = ts_ms - int(lot["first_ts_ms"]) > completion_sla_ms
                can_complete = (
                    not expired
                    and math.isfinite(opp_px)
                    and opp_sz > DUST
                    and lot["first_px"] + opp_px <= variant.seed_l1_pair_cap + 1e-12
                )
                if can_complete:
                    qty = min(float(lot["qty"]), max(0.0, opp_sz * variant.fill_haircut) if opp_sz else float(lot["qty"]))
                    if qty <= DUST:
                        still_pending.append(lot)
                        continue
                    completion_fee = fee_for(qty, opp_px, fee_rate)
                    pair_cost = lot["first_px"] + opp_px
                    pnl = qty * (1.0 - pair_cost) - float(lot["first_fee"]) - completion_fee
                    candidate_seq += 1
                    market_buy_cost += qty * opp_px + completion_fee
                    market_pnl += pnl
                    metrics = dm(str(row.get("day") or day))
                    metrics["paired_actions"] += 1
                    metrics["gross_buy_cost"] += qty * opp_px + completion_fee
                    metrics["fee"] += completion_fee
                    metrics["pair_qty"] += qty
                    metrics["pair_cost_sum"] += qty * pair_cost
                    metrics["pair_pnl"] += pnl
                    action_rows.append(
                        {
                            "candidate_id": hashlib.sha1(f"{variant.variant_id}|{condition_id}|{lot['first_candidate_row_id']}|{row['candidate_row_id']}".encode()).hexdigest()[:24],
                            "variant_id": variant.variant_id,
                            "policy_id": variant.policy_id,
                            "branch_id": variant.branch_id,
                            "condition_id": condition_id,
                            "slug": row.get("slug"),
                            "day": row.get("day"),
                            "first_leg_side": lot["side"],
                            "completion_leg_side": opp,
                            "first_leg_ts_ms": lot["first_ts_ms"],
                            "completion_leg_ts_ms": ts_ms,
                            "first_source_candidate_row_id": lot["first_candidate_row_id"],
                            "completion_source_candidate_row_id": row.get("candidate_row_id"),
                            "pair_delay_s": round((ts_ms - int(lot["first_ts_ms"])) / 1000.0, 6),
                            "first_leg_price": round(float(lot["first_px"]), 6),
                            "completion_leg_price": round(opp_px, 6),
                            "pair_cost": round(pair_cost, 6),
                            "paired_qty": round(qty, 6),
                            "resid_qty": 0.0,
                            "buy_actual_est": round(qty * pair_cost + float(lot["first_fee"]) + completion_fee, 6),
                            "cash_pnl_est": round(pnl, 6),
                            "fee_model": "official_taker",
                            "fee_rate": fee_rate,
                            "decision_reason": "paired_within_sla",
                            "kill_switch_reason": "",
                        }
                    )
                else:
                    if expired:
                        payout = float(lot["qty"]) if winner_side == lot["side"] else 0.0
                        pnl = payout - float(lot["qty"]) * float(lot["first_px"]) - float(lot["first_fee"])
                        market_pnl += pnl
                        metrics = dm(str(lot["day"]))
                        metrics["residual_actions"] += 1
                        metrics["residual_qty"] += float(lot["qty"])
                        metrics["residual_cost"] += float(lot["qty"]) * float(lot["first_px"]) + float(lot["first_fee"])
                        metrics["residual_pnl"] += pnl
                        residual_rows.append(
                            {
                                "variant_id": variant.variant_id,
                                "policy_id": variant.policy_id,
                                "branch_id": variant.branch_id,
                                "condition_id": condition_id,
                                "slug": lot["slug"],
                                "day": lot["day"],
                                "winner_side": winner_side,
                                "side": lot["side"],
                                "qty": round(float(lot["qty"]), 6),
                                "px": round(float(lot["first_px"]), 6),
                                "cost": round(float(lot["qty"]) * float(lot["first_px"]) + float(lot["first_fee"]), 6),
                                "payout": round(payout, 6),
                                "pnl": round(pnl, 6),
                                "source_candidate_row_id": lot["first_candidate_row_id"],
                                "age_s": round((ts_ms - int(lot["first_ts_ms"])) / 1000.0, 6),
                                "decision_reason": "completion_sla_expired",
                            }
                        )
                    else:
                        still_pending.append(lot)
            pending = still_pending

            allowed_sides = [variant.yes_no_side] if variant.yes_no_side else ["YES", "NO"]
            for side in allowed_sides:
                if side is None or not side_matches_entry(row, variant, side):
                    continue
                seed_cooldown_ms = max(1, completion_sla_ms)
                if ts_ms - last_seed_ts[side] < seed_cooldown_ms:
                    continue
                first_px, first_sz = ask_for(row, side)
                if not math.isfinite(first_px) or first_px <= 0 or first_sz <= DUST:
                    continue
                opp = "NO" if side == "YES" else "YES"
                opp_px, opp_sz = ask_for(row, opp)
                immediate_pair_available = (
                    math.isfinite(opp_px)
                    and opp_sz > DUST
                    and first_px + opp_px <= variant.seed_l1_pair_cap + 1e-12
                )
                if variant.entry_requires_opposite_depth and (not math.isfinite(opp_px) or opp_sz <= DUST):
                    continue
                if variant.entry_requires_pair_cap and not immediate_pair_available:
                    continue
                qty = min(variant.target_qty, max(0.0, first_sz * variant.fill_haircut), variant.max_seed_qty)
                if qty <= DUST:
                    continue
                if variant.entry_requires_opposite_qty and (
                    not math.isfinite(opp_sz) or opp_sz + DUST < qty
                ):
                    continue
                first_fee = fee_for(qty, first_px, fee_rate)
                if market_buy_cost + qty * first_px + first_fee > variant.max_open_cost:
                    continue
                last_seed_ts[side] = ts_ms
                market_buy_cost += qty * first_px + first_fee
                metrics = dm(str(row.get("day") or day))
                metrics["seed_actions"] += 1
                metrics["gross_buy_cost"] += qty * first_px + first_fee
                metrics["fee"] += first_fee
                if immediate_pair_available:
                    completion_fee = fee_for(qty, opp_px, fee_rate)
                    pair_cost = first_px + opp_px
                    pnl = qty * (1.0 - pair_cost) - first_fee - completion_fee
                    candidate_seq += 1
                    market_buy_cost += qty * opp_px + completion_fee
                    market_pnl += pnl
                    metrics["paired_actions"] += 1
                    metrics["gross_buy_cost"] += qty * opp_px + completion_fee
                    metrics["fee"] += completion_fee
                    metrics["pair_qty"] += qty
                    metrics["pair_cost_sum"] += qty * pair_cost
                    metrics["pair_pnl"] += pnl
                    action_rows.append(
                        {
                            "candidate_id": hashlib.sha1(f"{variant.variant_id}|{condition_id}|{row['candidate_row_id']}|same_row".encode()).hexdigest()[:24],
                            "variant_id": variant.variant_id,
                            "policy_id": variant.policy_id,
                            "branch_id": variant.branch_id,
                            "condition_id": condition_id,
                            "slug": row.get("slug"),
                            "day": row.get("day"),
                            "first_leg_side": side,
                            "completion_leg_side": opp,
                            "first_leg_ts_ms": ts_ms,
                            "completion_leg_ts_ms": ts_ms,
                            "first_source_candidate_row_id": row.get("candidate_row_id"),
                            "completion_source_candidate_row_id": row.get("candidate_row_id"),
                            "pair_delay_s": 0.0,
                            "first_leg_price": round(first_px, 6),
                            "completion_leg_price": round(opp_px, 6),
                            "pair_cost": round(pair_cost, 6),
                            "paired_qty": round(qty, 6),
                            "resid_qty": 0.0,
                            "buy_actual_est": round(qty * pair_cost + first_fee + completion_fee, 6),
                            "cash_pnl_est": round(pnl, 6),
                            "fee_model": "official_taker",
                            "fee_rate": fee_rate,
                            "decision_reason": "paired_same_row",
                            "kill_switch_reason": "",
                        }
                    )
                elif variant.same_row_pair_only:
                    payout = qty if winner_side == side else 0.0
                    pnl = payout - qty * first_px - first_fee
                    market_pnl += pnl
                    metrics["residual_actions"] += 1
                    metrics["residual_qty"] += qty
                    metrics["residual_cost"] += qty * first_px + first_fee
                    metrics["residual_pnl"] += pnl
                    residual_rows.append(
                        {
                            "variant_id": variant.variant_id,
                            "policy_id": variant.policy_id,
                            "branch_id": variant.branch_id,
                            "condition_id": condition_id,
                            "slug": row.get("slug"),
                            "day": row.get("day"),
                            "winner_side": winner_side,
                            "side": side,
                            "qty": round(qty, 6),
                            "px": round(first_px, 6),
                            "cost": round(qty * first_px + first_fee, 6),
                            "payout": round(payout, 6),
                            "pnl": round(pnl, 6),
                            "source_candidate_row_id": row.get("candidate_row_id"),
                            "age_s": 0.0,
                            "decision_reason": "same_row_pair_required_but_unavailable",
                        }
                    )
                else:
                    pending.append(
                        {
                            "side": side,
                            "qty": qty,
                            "first_px": first_px,
                            "first_fee": first_fee,
                            "first_ts_ms": ts_ms,
                            "first_candidate_row_id": row.get("candidate_row_id"),
                            "day": row.get("day"),
                            "slug": row.get("slug"),
                        }
                    )

        # Settle remaining pending inventory at final market outcome.
        final_ts_ms = int(market_rows[-1].get("ts_ms") or 0)
        for lot in pending:
            payout = float(lot["qty"]) if winner_side == lot["side"] else 0.0
            pnl = payout - float(lot["qty"]) * float(lot["first_px"]) - float(lot["first_fee"])
            market_pnl += pnl
            metrics = dm(str(lot["day"]))
            metrics["residual_actions"] += 1
            metrics["residual_qty"] += float(lot["qty"])
            metrics["residual_cost"] += float(lot["qty"]) * float(lot["first_px"]) + float(lot["first_fee"])
            metrics["residual_pnl"] += pnl
            residual_rows.append(
                {
                    "variant_id": variant.variant_id,
                    "policy_id": variant.policy_id,
                    "branch_id": variant.branch_id,
                    "condition_id": condition_id,
                    "slug": lot["slug"],
                    "day": lot["day"],
                    "winner_side": winner_side,
                    "side": lot["side"],
                    "qty": round(float(lot["qty"]), 6),
                    "px": round(float(lot["first_px"]), 6),
                    "cost": round(float(lot["qty"]) * float(lot["first_px"]) + float(lot["first_fee"]), 6),
                    "payout": round(payout, 6),
                    "pnl": round(pnl, 6),
                    "source_candidate_row_id": lot["first_candidate_row_id"],
                    "age_s": round(max(0, final_ts_ms - int(lot["first_ts_ms"])) / 1000.0, 6),
                    "decision_reason": "market_end_residual_settle",
                }
            )

    if not summary_only:
        write_csv(run_dir / "book_shadow_actions.csv", action_rows)
        write_csv(run_dir / "book_shadow_residual_lots.csv", residual_rows)
    summary_rows: list[dict[str, Any]] = []
    total = {
        "markets": 0.0,
        "seed_actions": 0.0,
        "paired_actions": 0.0,
        "residual_actions": 0.0,
        "gross_buy_cost": 0.0,
        "fee": 0.0,
        "pair_qty": 0.0,
        "pair_cost_sum": 0.0,
        "pair_pnl": 0.0,
        "residual_qty": 0.0,
        "residual_cost": 0.0,
        "residual_pnl": 0.0,
    }
    for day, m in sorted(day_metrics.items()):
        for key in total:
            total[key] += m.get(key, 0.0)
        pnl = m["pair_pnl"] + m["residual_pnl"]
        summary_rows.append(
            {
                "day": day,
                "markets": int(m["markets"]),
                "seed_actions": int(m["seed_actions"]),
                "paired_actions": int(m["paired_actions"]),
                "residual_actions": int(m["residual_actions"]),
                "gross_buy_cost": round(m["gross_buy_cost"], 6),
                "pair_qty": round(m["pair_qty"], 6),
                "pair_cost_wavg": round(m["pair_cost_sum"] / m["pair_qty"], 6) if m["pair_qty"] else None,
                "pair_pnl": round(m["pair_pnl"], 6),
                "residual_pnl": round(m["residual_pnl"], 6),
                "fee_after_pnl": round(pnl, 6),
                "net_roi": pct(pnl, m["gross_buy_cost"]),
                "residual_qty_rate": pct(m["residual_qty"], m["residual_qty"] + 2 * m["pair_qty"]),
                "residual_cost_rate": pct(m["residual_cost"], m["gross_buy_cost"]),
            }
        )
    write_csv(run_dir / "summary_by_day.csv", summary_rows)
    pair_costs = [as_float(row.get("pair_cost")) for row in action_rows if row.get("pair_cost") not in (None, "")]
    bad_pair_qty = sum(as_float(row.get("paired_qty")) for row in action_rows if as_float(row.get("pair_cost"), 0.0) >= 1.0)
    paired_market_ids = {str(row.get("condition_id")) for row in action_rows if row.get("condition_id") not in (None, "")}
    pair_qty_by_market: dict[str, float] = {}
    pair_actions_by_market: dict[str, int] = {}
    for row in action_rows:
        market_id = str(row.get("condition_id") or "")
        if not market_id:
            continue
        pair_qty_by_market[market_id] = pair_qty_by_market.get(market_id, 0.0) + as_float(row.get("paired_qty"))
        pair_actions_by_market[market_id] = pair_actions_by_market.get(market_id, 0) + 1
    pair_qty = total["pair_qty"]
    max_market_pair_qty = max(pair_qty_by_market.values()) if pair_qty_by_market else 0.0
    max_market_pair_actions = max(pair_actions_by_market.values()) if pair_actions_by_market else 0
    pnl = total["pair_pnl"] + total["residual_pnl"]
    metrics = {
        "engine": "book_shadow",
        "completion_sla_ms": completion_sla_ms,
        "source_row_count": source_row_count,
        "scan_cache_enabled": cache_info.get("enabled"),
        "scan_cache_hit": cache_info.get("cache_hit"),
        "scan_cache_key": cache_info.get("cache_key"),
        "scan_cache_path": cache_info.get("cache_path"),
        "active_markets": int(total["markets"]),
        "candidate_count": source_row_count,
        "seed_actions": int(total["seed_actions"]),
        "pair_actions": int(total["paired_actions"]),
        "paired_market_count": len(paired_market_ids),
        "max_market_pair_qty": round(max_market_pair_qty, 6),
        "max_market_pair_qty_share": pct(max_market_pair_qty, pair_qty),
        "max_market_pair_actions": max_market_pair_actions,
        "max_market_pair_action_share": pct(max_market_pair_actions, total["paired_actions"]),
        "residual_actions": int(total["residual_actions"]),
        "gross_buy_cost": round(total["gross_buy_cost"], 6),
        "pair_qty": round(pair_qty, 6),
        "net_pair_cost_wavg": round(total["pair_cost_sum"] / pair_qty, 6) if pair_qty else None,
        "pair_pnl": round(total["pair_pnl"], 6),
        "residual_pnl": round(total["residual_pnl"], 6),
        "fee_after_pnl": round(pnl, 6),
        "net_roi": pct(pnl, total["gross_buy_cost"]),
        "residual_qty_rate": pct(total["residual_qty"], total["residual_qty"] + 2 * pair_qty),
        "residual_cost_rate": pct(total["residual_cost"], total["gross_buy_cost"]),
        "bad_pair_cost_action_share": pct(bad_pair_qty, pair_qty),
        "pair_cost_min": round(min(pair_costs), 6) if pair_costs else None,
        "pair_cost_max": round(max(pair_costs), 6) if pair_costs else None,
    }
    manifest = {
        "created_at": utc_now(),
        "dataset_type": "ce25_nagi_book_shadow_result_v0",
        "status": "LOCAL_BOOK_SHADOW_REPLAY_EXECUTED_REVIEW_REQUIRED",
        "variant": asdict(variant),
        "fee_rate": fee_rate,
        "metrics": metrics,
        "non_claims": NON_CLAIMS,
        "outputs": {
            "actions_csv": None if summary_only else "book_shadow_actions.csv",
            "residual_lots_csv": None if summary_only else "book_shadow_residual_lots.csv",
            "summary_by_day_csv": "summary_by_day.csv",
        },
        "summary_only": summary_only,
        "elapsed_s": round(time.perf_counter() - started, 3),
        "sql": book_shadow_sql(variant),
        "scan_cache": cache_info,
    }
    write_json(run_dir / "BOOK_SHADOW_RESULT_MANIFEST.json", manifest)
    return {
        **asdict(variant),
        "fee_rate": fee_rate,
        "returncode": 0,
        "status": manifest["status"],
        "result_classification": "LOCAL_BOOK_SHADOW_REPLAY_EXECUTED_REVIEW_REQUIRED",
        "output_dir": str(run_dir),
        "elapsed_s": manifest["elapsed_s"],
        **metrics,
        "max_single_action_seed_cost": None,
        "action_count": len(action_rows),
    }


def classify_row(row: dict[str, Any], gates: dict[str, Any]) -> str:
    if int(row.get("seed_actions") or 0) <= 0:
        return "DISCARD_NO_ACTIONS"
    if int(row.get("pair_actions") or 0) <= 0:
        return "DISCARD_NO_PAIRS_RESIDUAL_ONLY"
    if as_float(row.get("fee_after_pnl")) <= 0:
        return "DISCARD_NEGATIVE_FEE_AFTER_PNL"
    if row.get("net_pair_cost_wavg") not in (None, "") and as_float(row.get("net_pair_cost_wavg")) > as_float(
        gates.get("pair_cost_observable_max"), 0.98
    ):
        return "KEEP_WATCH_PAIR_COST_ABOVE_PREFERRED"
    if as_float(row.get("residual_qty_rate")) > as_float(gates.get("resid_rate_max"), 0.12):
        return "KEEP_WATCH_RESIDUAL_HIGH"
    if row.get("bad_pair_cost_action_share") not in (None, "") and as_float(row.get("bad_pair_cost_action_share")) > as_float(
        gates.get("bad_pc_ge_100_share_p0_max"), 0.25
    ):
        return "KEEP_WATCH_BAD_PAIR_COST_HIGH"
    if int(row.get("pair_actions") or 0) < int(gates.get("paired_action_floor_for_full_keep", 100)):
        return "KEEP_WATCH_LOW_COVERAGE"
    return "KEEP_LOCAL_REPLAY_CANDIDATE"


def score_row(row: dict[str, Any]) -> float:
    if int(row.get("seed_actions") or 0) <= 0 or int(row.get("pair_actions") or 0) <= 0:
        return -999.0
    return round(
        as_float(row.get("net_roi")) * 2.0
        + as_float(row.get("fee_after_pnl")) / 1000.0
        + max(0.0, 1.0 - as_float(row.get("net_pair_cost_wavg"), 1.0)) * 0.5
        - as_float(row.get("residual_qty_rate")) * 0.2
        - as_float(row.get("bad_pair_cost_action_share")) * 0.1,
        9,
    )


def run_state_machine(
    variant: Variant,
    candidate_base_dir: Path,
    out_dir: Path,
    fee_rate: float,
    *,
    force: bool,
) -> dict[str, Any]:
    run_dir = out_dir / "state_machine_runs" / variant.variant_id / f"fee_{fee_rate:.4f}".replace(".", "p")
    if run_dir.exists():
        if not force:
            raise FileExistsError(f"state-machine output exists; pass --force: {run_dir}")
        shutil.rmtree(run_dir)
    cmd = state_machine_command(variant, candidate_base_dir, run_dir, fee_rate)
    started = time.perf_counter()
    proc = subprocess.run(cmd, cwd=str(ROOT), text=True, capture_output=True, check=False)
    elapsed = time.perf_counter() - started
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "command.stdout.log").write_text(proc.stdout, encoding="utf-8")
    (run_dir / "command.stderr.log").write_text(proc.stderr, encoding="utf-8")
    summary_path = run_dir / "RESULT_SUMMARY_MANIFEST.json"
    if proc.returncode != 0 or not summary_path.is_file():
        return {
            **asdict(variant),
            "fee_rate": fee_rate,
            "returncode": proc.returncode,
            "status": "BLOCKED_STATE_MACHINE_FAILED",
            "output_dir": str(run_dir),
            "elapsed_s": round(elapsed, 3),
            "stderr_tail": proc.stderr[-2000:],
        }
    summary = load_json(summary_path)
    metrics = summary.get("core_metrics") if isinstance(summary.get("core_metrics"), dict) else {}
    diag = read_action_diagnostics(run_dir)
    row = {
        **asdict(variant),
        "fee_rate": fee_rate,
        "returncode": proc.returncode,
        "status": metrics.get("status"),
        "result_classification": metrics.get("result_classification"),
        "output_dir": str(run_dir),
        "elapsed_s": round(elapsed, 3),
        "candidate_count": metrics.get("candidate_count"),
        "active_markets": metrics.get("active_markets"),
        "seed_actions": metrics.get("seed_actions"),
        "pair_actions": metrics.get("pair_actions"),
        "gross_buy_cost": metrics.get("gross_buy_cost"),
        "pair_qty": metrics.get("pair_qty"),
        "net_pair_cost_wavg": metrics.get("net_pair_cost_wavg"),
        "pair_delay_wavg_s": metrics.get("pair_delay_wavg_s"),
        "pair_share_rate": metrics.get("pair_share_rate"),
        "pair_pnl": metrics.get("pair_pnl"),
        "net_pnl": metrics.get("net_pnl"),
        "fee_after_pnl": metrics.get("fee_after_pnl"),
        "net_roi": metrics.get("net_roi"),
        "residual_qty_rate": metrics.get("residual_qty_rate"),
        "residual_cost_rate": metrics.get("residual_cost_rate"),
        "worst_residual_net_pnl": metrics.get("worst_residual_net_pnl"),
        "stress100_worst_pnl": metrics.get("stress100_worst_pnl"),
        **diag,
    }
    return row


def next_generation_from_ledger(ledger: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    ranked = [row for row in ledger if str(row.get("classification", "")).startswith("KEEP")]
    ranked.sort(key=lambda row: as_float(row.get("autoresearch_score")), reverse=True)
    next_items: list[dict[str, Any]] = []
    for row in ranked[:limit]:
        px_lo = as_float(row.get("seed_px_lo"))
        px_hi = as_float(row.get("seed_px_hi"))
        pair_cap = as_float(row.get("seed_l1_pair_cap"))
        next_items.append(
            {
                "parent_variant_id": row.get("variant_id"),
                "policy_id": row.get("policy_id"),
                "recommended_mutations": [
                    {
                        "mutation": "tighten_pair_cap",
                        "seed_l1_pair_cap": round(max(0.8, pair_cap - 0.005), 6),
                        "reason": "test whether edge survives stricter fillability gate",
                    },
                    {
                        "mutation": "split_lower_price_half",
                        "seed_px_lo": px_lo,
                        "seed_px_hi": round((px_lo + px_hi) / 2, 6),
                        "reason": "price bucket may be too broad; isolate stronger tail",
                    },
                    {
                        "mutation": "split_upper_price_half",
                        "seed_px_lo": round((px_lo + px_hi) / 2, 6),
                        "seed_px_hi": px_hi,
                        "reason": "price bucket may be too broad; isolate upper half",
                    },
                ],
                "carry_forward_score": row.get("autoresearch_score"),
                "carry_forward_status": row.get("classification"),
            }
        )
    return next_items


def summary_outputs_from_ledger(ledger: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_policy_fee: dict[tuple[str, str], list[dict[str, Any]]] = {}
    by_variant: dict[str, list[dict[str, Any]]] = {}
    for row in ledger:
        by_policy_fee.setdefault((str(row.get("policy_id")), str(row.get("fee_rate"))), []).append(row)
        by_variant.setdefault(str(row.get("variant_id")), []).append(row)

    policy_fee_rows: list[dict[str, Any]] = []
    for (policy_id, fee_rate), rows in sorted(by_policy_fee.items()):
        best = max(rows, key=lambda r: as_float(r.get("autoresearch_score"), -999.0))
        class_counts: dict[str, int] = {}
        for row in rows:
            cls = str(row.get("classification") or "UNKNOWN")
            class_counts[cls] = class_counts.get(cls, 0) + 1
        policy_fee_rows.append(
            {
                "policy_id": policy_id,
                "fee_rate": fee_rate,
                "result_count": len(rows),
                "classification_counts_json": json.dumps(class_counts, sort_keys=True),
                "best_variant_id": best.get("variant_id"),
                "best_branch_id": best.get("branch_id"),
                "best_classification": best.get("classification"),
                "best_fee_after_pnl": best.get("fee_after_pnl"),
                "best_net_roi": best.get("net_roi"),
                "best_pair_actions": best.get("pair_actions"),
                "best_residual_qty_rate": best.get("residual_qty_rate"),
                "best_pair_cost": best.get("net_pair_cost_wavg"),
                "best_autoresearch_score": best.get("autoresearch_score"),
            }
        )

    fee_stress_rows: list[dict[str, Any]] = []
    residual_rows: list[dict[str, Any]] = []
    branch_control_rows: list[dict[str, Any]] = []
    capacity_stress_rows: list[dict[str, Any]] = []
    for variant_id, rows in sorted(by_variant.items()):
        rows_sorted = sorted(rows, key=lambda r: as_float(r.get("fee_rate")))
        max_fee_row = rows_sorted[-1]
        pnls = [as_float(row.get("fee_after_pnl")) for row in rows_sorted]
        all_positive = all(pnl > 0 for pnl in pnls)
        fee_stress_rows.append(
            {
                "variant_id": variant_id,
                "policy_id": max_fee_row.get("policy_id"),
                "branch_id": max_fee_row.get("branch_id"),
                "role": max_fee_row.get("role"),
                "max_fee_rate": max_fee_row.get("fee_rate"),
                "max_fee_classification": max_fee_row.get("classification"),
                "max_fee_pnl": max_fee_row.get("fee_after_pnl"),
                "max_fee_roi": max_fee_row.get("net_roi"),
                "min_fee_pnl": round(min(pnls), 6) if pnls else None,
                "max_fee_pnl_seen": round(max(pnls), 6) if pnls else None,
                "all_fee_rates_positive": all_positive,
                "fee_rates_tested": ",".join(str(row.get("fee_rate")) for row in rows_sorted),
            }
        )
        residual_rows.append(
            {
                "variant_id": variant_id,
                "policy_id": max_fee_row.get("policy_id"),
                "branch_id": max_fee_row.get("branch_id"),
                "role": max_fee_row.get("role"),
                "max_fee_rate": max_fee_row.get("fee_rate"),
                "max_fee_classification": max_fee_row.get("classification"),
                "max_fee_pair_actions": max_fee_row.get("pair_actions"),
                "max_fee_residual_actions": max_fee_row.get("residual_actions"),
                "max_fee_residual_qty_rate": max_fee_row.get("residual_qty_rate"),
                "max_fee_residual_cost_rate": max_fee_row.get("residual_cost_rate"),
                "same_row_pair_only": max_fee_row.get("same_row_pair_only"),
                "entry_requires_pair_cap": max_fee_row.get("entry_requires_pair_cap"),
                "entry_requires_opposite_depth": max_fee_row.get("entry_requires_opposite_depth"),
            }
        )
        branch_control_rows.append(
            {
                "variant_id": variant_id,
                "policy_id": max_fee_row.get("policy_id"),
                "branch_id": max_fee_row.get("branch_id"),
                "role": max_fee_row.get("role"),
                "mutation_note": max_fee_row.get("mutation_note"),
                "max_fee_rate": max_fee_row.get("fee_rate"),
                "classification": max_fee_row.get("classification"),
                "autoresearch_score": max_fee_row.get("autoresearch_score"),
                "active_markets": max_fee_row.get("active_markets"),
                "seed_actions": max_fee_row.get("seed_actions"),
                "pair_actions": max_fee_row.get("pair_actions"),
                "paired_market_count": max_fee_row.get("paired_market_count"),
                "max_market_pair_qty_share": max_fee_row.get("max_market_pair_qty_share"),
                "max_market_pair_action_share": max_fee_row.get("max_market_pair_action_share"),
                "residual_actions": max_fee_row.get("residual_actions"),
                "fee_after_pnl": max_fee_row.get("fee_after_pnl"),
                "net_roi": max_fee_row.get("net_roi"),
                "net_pair_cost_wavg": max_fee_row.get("net_pair_cost_wavg"),
                "residual_qty_rate": max_fee_row.get("residual_qty_rate"),
                "bad_pair_cost_action_share": max_fee_row.get("bad_pair_cost_action_share"),
                "target_qty": max_fee_row.get("target_qty"),
                "fill_haircut": max_fee_row.get("fill_haircut"),
                "max_seed_qty": max_fee_row.get("max_seed_qty"),
                "same_row_pair_only": max_fee_row.get("same_row_pair_only"),
                "entry_requires_pair_cap": max_fee_row.get("entry_requires_pair_cap"),
                "entry_requires_opposite_depth": max_fee_row.get("entry_requires_opposite_depth"),
                "max_pair_delay_ms": max_fee_row.get("max_pair_delay_ms"),
            }
        )
        note = str(max_fee_row.get("mutation_note") or "")
        if note.startswith("capacity_stress_") or note.startswith("depth_stress_") or note.startswith("strict_paircap_sweep_"):
            capacity_stress_rows.append(
                {
                    "variant_id": variant_id,
                    "policy_id": max_fee_row.get("policy_id"),
                    "branch_id": max_fee_row.get("branch_id"),
                    "mutation_note": note,
                    "max_fee_rate": max_fee_row.get("fee_rate"),
                    "classification": max_fee_row.get("classification"),
                    "fee_after_pnl": max_fee_row.get("fee_after_pnl"),
                    "net_roi": max_fee_row.get("net_roi"),
                    "active_markets": max_fee_row.get("active_markets"),
                    "seed_actions": max_fee_row.get("seed_actions"),
                    "pair_actions": max_fee_row.get("pair_actions"),
                    "paired_market_count": max_fee_row.get("paired_market_count"),
                    "max_market_pair_qty_share": max_fee_row.get("max_market_pair_qty_share"),
                    "max_market_pair_action_share": max_fee_row.get("max_market_pair_action_share"),
                    "pair_qty": max_fee_row.get("pair_qty"),
                    "gross_buy_cost": max_fee_row.get("gross_buy_cost"),
                    "net_pair_cost_wavg": max_fee_row.get("net_pair_cost_wavg"),
                    "residual_qty_rate": max_fee_row.get("residual_qty_rate"),
                    "bad_pair_cost_action_share": max_fee_row.get("bad_pair_cost_action_share"),
                    "seed_l1_pair_cap": max_fee_row.get("seed_l1_pair_cap"),
                    "target_qty": max_fee_row.get("target_qty"),
                    "fill_haircut": max_fee_row.get("fill_haircut"),
                    "same_row_pair_only": max_fee_row.get("same_row_pair_only"),
                    "entry_requires_pair_cap": max_fee_row.get("entry_requires_pair_cap"),
                }
            )
    residual_rows.sort(key=lambda r: (as_float(r.get("max_fee_residual_qty_rate")), -int(r.get("max_fee_pair_actions") or 0)))
    branch_control_rows.sort(key=lambda r: as_float(r.get("autoresearch_score"), -999.0), reverse=True)
    capacity_stress_rows.sort(
        key=lambda r: (
            str(r.get("same_row_pair_only")),
            as_float(r.get("target_qty")),
            as_float(r.get("fill_haircut")),
        )
    )
    return {
        "policy_fee_summary.csv": policy_fee_rows,
        "fee_stress_summary.csv": fee_stress_rows,
        "residual_stress_summary.csv": residual_rows,
        "branch_control_summary.csv": branch_control_rows,
        "capacity_stress_summary.csv": capacity_stress_rows,
    }


def report_markdown(
    path: Path,
    *,
    strategy: dict[str, Any],
    master_base: Path,
    engine: str,
    variants: list[Variant],
    ledger: list[dict[str, Any]],
    next_generation: list[dict[str, Any]],
    plan_only: bool,
) -> None:
    title = strategy.get("strategy_family") or "shadow_policy_autoresearch"
    lines = [
        f"# {title} Autoresearch v0",
        "",
        f"Generated UTC: `{utc_now()}`",
        f"Status: `{'PLAN_ONLY_NOT_EXECUTED' if plan_only else 'LOCAL_REPLAY_EXECUTED_REVIEW_REQUIRED'}`",
        "",
        "## Scope",
        "",
        "- Local replay/research only.",
        "- No private key, import, order, cancel, redeem, live, deploy, funding, or latest pointer update.",
        "- Public profile evidence is not private owner truth.",
        "- Side mapping assumption for replay: `UP=YES`, `DOWN=NO`.",
        "- `book_shadow` uses local candidate-base `side_ask` / `opp_ask` / `l1_pair_ask` snapshots, not live books.",
        "- `state_machine` mode remains a public-trade-triggered compatibility engine.",
        "",
        "## Inputs",
        "",
        f"- strategy input: `{strategy.get('source_handoff', {}).get('path', '')}`",
        f"- master candidate base: `{master_base}`",
        f"- engine: `{engine}`",
        f"- variant count: `{len(variants)}`",
        "",
        "## Results",
        "",
    ]
    if not ledger:
        lines.append("No state-machine runs executed. See command ledger and variant plan.")
    else:
        cols = [
            "classification",
            "policy_id",
            "branch_id",
            "fee_rate",
            "seed_actions",
            "active_markets",
            "fee_after_pnl",
            "net_roi",
            "net_pair_cost_wavg",
            "residual_qty_rate",
            "bad_pair_cost_action_share",
            "autoresearch_score",
        ]
        lines.append("| " + " | ".join(cols) + " |")
        lines.append("| " + " | ".join("---" for _ in cols) + " |")
        for row in sorted(ledger, key=lambda r: as_float(r.get("autoresearch_score")), reverse=True):
            vals = [str(row.get(col, "")) for col in cols]
            lines.append("| " + " | ".join(vals) + " |")
    lines.extend(
        [
            "",
            "## Next Generation Queue",
            "",
        ]
    )
    if next_generation:
        for item in next_generation:
            lines.append(f"- parent `{item['parent_variant_id']}` score `{item['carry_forward_score']}`: {item['carry_forward_status']}")
            for mutation in item["recommended_mutations"]:
                lines.append(f"  - `{mutation['mutation']}`: {mutation['reason']}")
    else:
        lines.append("- No keep candidates yet; widen or inspect blocked variants.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy-input", type=Path, default=DEFAULT_STRATEGY_INPUT)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--master-candidate-base", type=Path, default=DEFAULT_MASTER_BASE)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument(
        "--book-shadow-cache-dir",
        type=Path,
        default=None,
        help="Persistent scan cache for book_shadow; default is data_root/derived/ce25_nagi_book_shadow_scan_cache_v0",
    )
    parser.add_argument("--refresh-book-shadow-cache", action="store_true")
    parser.add_argument("--run-name", default=None)
    parser.add_argument("--include-controls", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--include-mutations", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--max-mutations", type=int, default=24)
    parser.add_argument("--max-variants", type=int, default=0, help="0 means all generated variants")
    parser.add_argument(
        "--variant-id-regex",
        action="append",
        default=[],
        help="Optional regex filter applied after variant generation and de-duplication.",
    )
    parser.add_argument("--fee-rate", action="append", type=float, default=None)
    parser.add_argument("--engine", choices=["book_shadow", "state_machine"], default="book_shadow")
    parser.add_argument(
        "--book-shadow-summary-only",
        action="store_true",
        help="In book_shadow mode, skip per-action/per-residual detail CSVs and emit only manifests and summaries.",
    )
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    started = time.perf_counter()
    strategy_path = args.strategy_input.expanduser().resolve()
    strategy = load_json(strategy_path)
    master_base = args.master_candidate_base.expanduser().resolve()
    source_master_paths(master_base)
    run_name = safe_name(args.run_name or f"ce25_nagi_autoresearch_{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}")
    out_root = (
        args.output_dir.expanduser().resolve()
        if args.output_dir
        else args.data_root.expanduser().resolve() / "derived" / "ce25_nagi_shadow_policy_autoresearch_v0" / run_name
    )
    book_shadow_cache_dir = (
        args.book_shadow_cache_dir.expanduser().resolve()
        if args.book_shadow_cache_dir
        else args.data_root.expanduser().resolve() / "derived" / BOOK_SHADOW_SCAN_CACHE_DATASET_TYPE
    )
    if out_root.exists():
        if not args.force:
            raise FileExistsError(f"output exists; pass --force: {out_root}")
        shutil.rmtree(out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    variants = seed_variants(strategy, args.include_controls)
    if args.include_mutations:
        variants.extend(mutation_variants(variants, args.max_mutations))
    deduped: dict[str, Variant] = {}
    for variant in variants:
        deduped.setdefault(variant.variant_id, variant)
    variants = list(deduped.values())
    if args.variant_id_regex:
        patterns = [re.compile(pattern) for pattern in args.variant_id_regex]
        variants = [variant for variant in variants if any(pattern.search(variant.variant_id) for pattern in patterns)]
    if args.max_variants and args.max_variants > 0:
        variants = variants[: args.max_variants]
    fee_rates = args.fee_rate if args.fee_rate else [0.0283]

    variant_rows = [asdict(v) for v in variants]
    write_csv(out_root / "variant_plan.csv", variant_rows)
    write_json(out_root / "variant_plan.json", {"variants": variant_rows})

    command_rows: list[dict[str, Any]] = []
    ledger: list[dict[str, Any]] = []
    if not args.plan_only:
        for variant in variants:
            base_info: dict[str, Any] = {"row_count": None, "market_count": None, "candidate_base_dir": ""}
            candidate_base_dir: Path | None = None
            if args.engine == "state_machine":
                base_info = build_variant_base(master_base, variant, out_root, force=args.force)
                candidate_base_dir = Path(base_info["candidate_base_dir"])
            for fee_rate in fee_rates:
                if args.engine == "state_machine":
                    assert candidate_base_dir is not None
                    run_dir = out_root / "state_machine_runs" / variant.variant_id / f"fee_{fee_rate:.4f}".replace(".", "p")
                    cmd = state_machine_command(variant, candidate_base_dir, run_dir, fee_rate)
                    command = " ".join(cmd)
                else:
                    run_dir = out_root / "book_shadow_runs" / variant.variant_id / f"fee_{fee_rate:.4f}".replace(".", "p")
                    command = (
                        f"internal_book_shadow engine variant={variant.variant_id} fee_rate={fee_rate}"
                        f" summary_only={bool(args.book_shadow_summary_only)}"
                    )
                command_rows.append(
                    {
                        "variant_id": variant.variant_id,
                        "fee_rate": fee_rate,
                        "candidate_base_dir": str(candidate_base_dir or ""),
                        "output_dir": str(run_dir),
                        "command": command,
                    }
                )
                if args.engine == "state_machine":
                    assert candidate_base_dir is not None
                    row = run_state_machine(variant, candidate_base_dir, out_root, fee_rate, force=args.force)
                else:
                    row = run_book_shadow_variant(
                        variant,
                        master_base,
                        out_root,
                        fee_rate,
                        cache_dir=book_shadow_cache_dir,
                        refresh_cache=args.refresh_book_shadow_cache,
                        force=args.force,
                        summary_only=args.book_shadow_summary_only,
                    )
                gates = strategy.get("global_controls", {}).get("initial_acceptance_gates", {})
                row["classification"] = classify_row(row, gates)
                row["autoresearch_score"] = score_row(row)
                row["candidate_base_row_count"] = base_info["row_count"]
                row["candidate_base_market_count"] = base_info["market_count"]
                ledger.append(row)
    else:
        for variant in variants:
            preview_base = out_root / "candidate_bases" / variant.variant_id
            for fee_rate in fee_rates:
                run_parent = "state_machine_runs" if args.engine == "state_machine" else "book_shadow_runs"
                run_dir = out_root / run_parent / variant.variant_id / f"fee_{fee_rate:.4f}".replace(".", "p")
                command = (
                    " ".join(state_machine_command(variant, preview_base, run_dir, fee_rate))
                    if args.engine == "state_machine"
                    else f"internal_book_shadow engine variant={variant.variant_id} fee_rate={fee_rate} summary_only={bool(args.book_shadow_summary_only)}"
                )
                command_rows.append(
                    {
                        "variant_id": variant.variant_id,
                        "fee_rate": fee_rate,
                        "candidate_base_dir": str(preview_base),
                        "output_dir": str(run_dir),
                        "command": command,
                    }
                )

    write_csv(out_root / "command_ledger.csv", command_rows)
    if ledger:
        ledger.sort(key=lambda row: as_float(row.get("autoresearch_score")), reverse=True)
        write_csv(out_root / "autoresearch_ledger.csv", ledger)
        for filename, rows in summary_outputs_from_ledger(ledger).items():
            write_csv(out_root / filename, rows)
    next_generation = next_generation_from_ledger(ledger, limit=6)
    write_json(out_root / "next_generation_candidates.json", {"next_generation": next_generation})

    manifest = {
        "created_at": utc_now(),
        "dataset_type": OUT_DATASET_TYPE,
        "status": "PLAN_ONLY_NOT_EXECUTED" if args.plan_only else "LOCAL_REPLAY_EXECUTED_REVIEW_REQUIRED",
        "strategy_owner_line": strategy.get("strategy_owner_line", "CE25_NAGI_RESEARCH"),
        "strategy_family": strategy.get("strategy_family"),
        "strategy_input": str(strategy_path),
        "strategy_input_sha256": sha256_file(strategy_path),
        "master_candidate_base": str(master_base),
        "master_candidate_base_manifest_sha256": sha256_file(master_base / "CANDIDATE_BASE_MANIFEST.json"),
        "variant_count": len(variants),
        "engine": args.engine,
        "book_shadow_summary_only": bool(args.book_shadow_summary_only) if args.engine == "book_shadow" else None,
        "book_shadow_scan_cache_dir": str(book_shadow_cache_dir) if args.engine == "book_shadow" else None,
        "book_shadow_scan_cache_dataset_type": BOOK_SHADOW_SCAN_CACHE_DATASET_TYPE if args.engine == "book_shadow" else None,
        "book_shadow_scan_cache_refresh_requested": bool(args.refresh_book_shadow_cache),
        "fee_rates": fee_rates,
        "result_count": len(ledger),
        "non_claims": NON_CLAIMS,
        "side_mapping_assumption": "UP=YES, DOWN=NO",
        "price_proxy": (
            "side_ask/opp_ask/l1_pair_ask snapshots from local completion candidate base"
            if args.engine == "book_shadow"
            else "public_trade_price from local completion_unwind_event_store_v2 candidate base"
        ),
        "public_trade_taker_side_filter": "ANY",
        "outputs": {
            "variant_plan_csv": "variant_plan.csv",
            "variant_plan_json": "variant_plan.json",
            "command_ledger_csv": "command_ledger.csv",
            "autoresearch_ledger_csv": "autoresearch_ledger.csv" if ledger else None,
            "policy_fee_summary_csv": "policy_fee_summary.csv" if ledger else None,
            "fee_stress_summary_csv": "fee_stress_summary.csv" if ledger else None,
            "residual_stress_summary_csv": "residual_stress_summary.csv" if ledger else None,
            "next_generation_candidates_json": "next_generation_candidates.json",
            "report_md": "CE25_NAGI_SHADOW_POLICY_AUTORESEARCH_REPORT.md",
        },
        "elapsed_s": round(time.perf_counter() - started, 3),
    }
    write_json(out_root / "AUTORESEARCH_MANIFEST.json", manifest)
    report_markdown(
        out_root / "CE25_NAGI_SHADOW_POLICY_AUTORESEARCH_REPORT.md",
        strategy=strategy,
        master_base=master_base,
        engine=args.engine,
        variants=variants,
        ledger=ledger,
        next_generation=next_generation,
        plan_only=args.plan_only,
    )
    print(
        json.dumps(
            {
                "output_dir": str(out_root),
                "status": manifest["status"],
                "variant_count": len(variants),
                "result_count": len(ledger),
                "top": ledger[0] if ledger else None,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
