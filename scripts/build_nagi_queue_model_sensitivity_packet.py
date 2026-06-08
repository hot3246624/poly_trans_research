#!/usr/bin/env python3
"""Stress NAGI maker queue public-proxy frontier under haircut assumptions."""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
EXPORTS = ROOT / "data" / "exports"
OUT = EXPORTS / "nagi_queue_model_sensitivity_packet_20260608"
FRONTIER_ROOT = EXPORTS / "nagi_maker_queue_exhaustive_frontier_packet_20260608"
FRONTIER_CSV = FRONTIER_ROOT / "nagi_maker_queue_exhaustive_frontier.csv"
FRONTIER_PACKET = FRONTIER_ROOT / "NAGI_MAKER_QUEUE_EXHAUSTIVE_FRONTIER_PACKET.json"
SYNTHESIS_PACKET = (
    EXPORTS / "nagi_strategy_synthesis_packet_20260608" / "NAGI_STRATEGY_SYNTHESIS_PACKET.json"
)
BUILDER = ROOT / "scripts/build_nagi_queue_model_sensitivity_packet.py"

STATUS = (
    "KEEP_NAGI_QUEUE_MODEL_SENSITIVITY_REVIEWED_PUBLIC_PROXY_HAIRCUTS_"
    "PRIVATE_QUEUE_TRUTH_REQUIRED_NOT_OOS_READY"
)

OWN_FILL_CONVERSIONS = [1.0, 0.5, 0.25, 0.1, 0.05]
VISIBLE_QUEUE_AHEAD_MULTIPLIERS = [0.0, 0.5, 1.0, 2.0, 5.0]

# Review-only scale gates. These are not OOS/readiness gates.
SCALE_EDGE_MIN = 100.0
SCALE_MARKET_MIN = 100
HIGH_COVERAGE_EDGE_MIN = 250.0
HIGH_COVERAGE_MARKET_MIN = 500
HIGH_COVERAGE_MARKET_SHARE_MIN = 0.50


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
    return out


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def as_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    out: list[dict[str, Any]] = []
    for row in rows:
        if row.get("variant_id") == "variant_id":
            continue
        converted: dict[str, Any] = dict(row)
        for key in (
            "start_s",
            "end_s",
            "px_lo",
            "px_hi",
            "pair_cap",
            "queue_min_qty",
            "queue_qty_sum",
            "queue_edge_qty_sum_fee0",
            "queue_edge_qty_sum_taker_fee07",
            "queue_pair_cost_avg",
            "queue_pair_cost_p50",
            "queue_net_edge_fee0_avg",
            "queue_net_edge_fee0_p50",
            "queue_qty_p50",
            "queue_qty_p90",
            "touch_after_quote_ms_p99",
            "align_lag_ms_p99",
            "queue_row_share",
            "queue_market_share",
        ):
            converted[key] = as_float(row.get(key))
        for key in (
            "eligible_touch_rows",
            "eligible_touch_markets",
            "queue_rows",
            "queue_markets",
            "queue_pos_fee0_rows",
            "queue_pos_fee0_markets",
            "queue_pos_taker_fee07_rows",
            "queue_pos_taker_fee07_markets",
        ):
            converted[key] = as_int(row.get(key))
        for key in (
            "passes_fee0_scale_gate",
            "passes_fee0_high_coverage_gate",
            "survives_taker_fee07_scale_gate",
            "nagi_anchor_like",
            "private_truth_ready",
            "oos_ready",
        ):
            converted[key] = as_bool(row.get(key))
        out.append(converted)
    return out


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


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_preview(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo 'NOT_AUTHORIZED: NAGI queue sensitivity is public-proxy review only.' >&2\n"
        "exit 66\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def write_sha256sums(root: Path, files: list[Path]) -> None:
    lines = [f"{sha256_file(path)}  {path.relative_to(root)}" for path in files]
    (root / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def scenario_id(conversion: float, queue_ahead: float) -> str:
    return f"conv{conversion:g}__ahead{queue_ahead:g}".replace(".", "p")


def stress_rows(frontier: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    summary_rows: list[dict[str, Any]] = []
    top_rows: list[dict[str, Any]] = []
    for conversion in OWN_FILL_CONVERSIONS:
        for queue_ahead in VISIBLE_QUEUE_AHEAD_MULTIPLIERS:
            effective_conversion = conversion / (1.0 + queue_ahead)
            stressed: list[dict[str, Any]] = []
            for row in frontier:
                edge = row["queue_edge_qty_sum_fee0"] * effective_conversion
                qty = row["queue_qty_sum"] * effective_conversion
                review_scale = edge >= SCALE_EDGE_MIN and row["queue_markets"] >= SCALE_MARKET_MIN
                high_coverage = (
                    edge >= HIGH_COVERAGE_EDGE_MIN
                    and row["queue_markets"] >= HIGH_COVERAGE_MARKET_MIN
                    and row["queue_market_share"] >= HIGH_COVERAGE_MARKET_SHARE_MIN
                )
                stressed.append(
                    {
                        "scenario_id": scenario_id(conversion, queue_ahead),
                        "own_fill_conversion": conversion,
                        "visible_queue_ahead_multiplier": queue_ahead,
                        "effective_conversion": round(effective_conversion, 6),
                        "variant_id": row["variant_id"],
                        "time_id": row["time_id"],
                        "band_id": row["band_id"],
                        "side": row["side"],
                        "pair_cap": row["pair_cap"],
                        "queue_min_qty": row["queue_min_qty"],
                        "queue_markets": row["queue_markets"],
                        "queue_market_share": row["queue_market_share"],
                        "queue_rows": row["queue_rows"],
                        "queue_qty_sum": round(qty, 6),
                        "stressed_fee0_edge_qty_sum": round(edge, 6),
                        "raw_fee0_edge_qty_sum": row["queue_edge_qty_sum_fee0"],
                        "raw_taker_fee07_edge_qty_sum": row["queue_edge_qty_sum_taker_fee07"],
                        "queue_pair_cost_p50": row["queue_pair_cost_p50"],
                        "review_scale_gate": review_scale,
                        "review_high_coverage_gate": high_coverage,
                        "nagi_anchor_like": row["nagi_anchor_like"],
                    }
                )
            scale = [row for row in stressed if row["review_scale_gate"]]
            high = [row for row in stressed if row["review_high_coverage_gate"]]
            positive = [row for row in stressed if row["stressed_fee0_edge_qty_sum"] > 0]
            anchor = [row for row in stressed if row["nagi_anchor_like"]]
            top = sorted(stressed, key=lambda item: item["stressed_fee0_edge_qty_sum"], reverse=True)[:25]
            top_rows.extend(top)
            best = top[0] if top else {}
            best_anchor = sorted(anchor, key=lambda item: item["stressed_fee0_edge_qty_sum"], reverse=True)[:1]
            summary_rows.append(
                {
                    "scenario_id": scenario_id(conversion, queue_ahead),
                    "own_fill_conversion": conversion,
                    "visible_queue_ahead_multiplier": queue_ahead,
                    "effective_conversion": round(effective_conversion, 6),
                    "positive_variant_count": len(positive),
                    "review_scale_variant_count": len(scale),
                    "review_high_coverage_variant_count": len(high),
                    "best_variant_id": best.get("variant_id"),
                    "best_stressed_fee0_edge_qty_sum": best.get("stressed_fee0_edge_qty_sum"),
                    "best_queue_markets": best.get("queue_markets"),
                    "best_queue_market_share": best.get("queue_market_share"),
                    "best_review_scale_gate": best.get("review_scale_gate"),
                    "best_review_high_coverage_gate": best.get("review_high_coverage_gate"),
                    "best_anchor_variant_id": best_anchor[0]["variant_id"] if best_anchor else None,
                    "best_anchor_stressed_fee0_edge_qty_sum": best_anchor[0]["stressed_fee0_edge_qty_sum"]
                    if best_anchor
                    else None,
                    "best_anchor_review_scale_gate": best_anchor[0]["review_scale_gate"] if best_anchor else None,
                    "best_anchor_review_high_coverage_gate": best_anchor[0]["review_high_coverage_gate"]
                    if best_anchor
                    else None,
                }
            )
    return summary_rows, top_rows


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    frontier = read_csv(FRONTIER_CSV)
    summary_rows, top_rows = stress_rows(frontier)

    worst = min(summary_rows, key=lambda item: item["effective_conversion"])
    robust_high = [
        row for row in summary_rows if row["review_high_coverage_variant_count"] > 0 and row["effective_conversion"] <= 0.05
    ]
    anchor_scale = [
        row for row in summary_rows if row["best_anchor_review_scale_gate"] is True
    ]
    anchor_high = [
        row for row in summary_rows if row["best_anchor_review_high_coverage_gate"] is True
    ]
    packet = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS,
        "method": {
            "source": "aggregate haircut of local public-proxy maker frontier",
            "effective_conversion_formula": "own_fill_conversion / (1 + visible_queue_ahead_multiplier)",
            "stressed_fee0_edge_qty_sum": "queue_edge_qty_sum_fee0 * effective_conversion",
            "review_scale_gate": {
                "stressed_fee0_edge_qty_sum_min": SCALE_EDGE_MIN,
                "queue_markets_min": SCALE_MARKET_MIN,
            },
            "review_high_coverage_gate": {
                "stressed_fee0_edge_qty_sum_min": HIGH_COVERAGE_EDGE_MIN,
                "queue_markets_min": HIGH_COVERAGE_MARKET_MIN,
                "queue_market_share_min": HIGH_COVERAGE_MARKET_SHARE_MIN,
            },
            "private_truth_limit": "This is not a queue simulator and does not prove own maker fill.",
        },
        "summary": {
            "frontier_variant_count": len(frontier),
            "scenario_count": len(summary_rows),
            "own_fill_conversions": OWN_FILL_CONVERSIONS,
            "visible_queue_ahead_multipliers": VISIBLE_QUEUE_AHEAD_MULTIPLIERS,
            "worst_scenario": worst,
            "high_coverage_survives_at_effective_conversion_lte_0p05": len(robust_high) > 0,
            "anchor_scale_scenario_count": len(anchor_scale),
            "anchor_high_coverage_scenario_count": len(anchor_high),
            "taker_fee07_scale_lane_exists": False,
        },
        "source_bindings": {
            "frontier_csv": binding(FRONTIER_CSV),
            "frontier_packet": binding(FRONTIER_PACKET),
            "synthesis_packet": binding(SYNTHESIS_PACKET),
            "builder": binding(BUILDER),
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
            "private_key_authorized": False,
            "ws_authorized": False,
        },
    }

    summary_csv = OUT / "nagi_queue_model_sensitivity_summary.csv"
    top_csv = OUT / "nagi_queue_model_sensitivity_top.csv"
    packet_path = OUT / "NAGI_QUEUE_MODEL_SENSITIVITY_PACKET.json"
    report_path = OUT / "NAGI_QUEUE_MODEL_SENSITIVITY_REPORT.md"
    preview_path = OUT / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"

    write_csv(summary_csv, summary_rows)
    write_csv(top_csv, top_rows)
    write_json(packet_path, packet)
    report_path.write_text(
        "\n".join(
            [
                "# NAGI Queue Model Sensitivity",
                "",
                f"Status: `{STATUS}`",
                "",
                "This is an aggregate public-proxy haircut review. It does not prove own maker fill, queue priority, OOS, or readiness.",
                "",
                "## Result",
                "",
                f"- Frontier variants: {len(frontier)}",
                f"- Scenarios: {len(summary_rows)}",
                f"- Worst effective conversion: {worst['effective_conversion']}",
                f"- Worst scenario high-coverage variant count: {worst['review_high_coverage_variant_count']}",
                f"- Anchor scale scenario count: {len(anchor_scale)}",
                f"- Anchor high-coverage scenario count: {len(anchor_high)}",
                f"- Any high-coverage lane survives effective conversion <= 0.05: {len(robust_high) > 0}",
                "",
                "## Next",
                "",
                "Use this packet to size the private maker-shadow sample plan; do not tune public proxy further by default.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    write_preview(preview_path)
    write_sha256sums(OUT, [packet_path, report_path, summary_csv, top_csv, preview_path])
    print(json.dumps({"packet": str(packet_path), "status": STATUS, "summary": packet["summary"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
