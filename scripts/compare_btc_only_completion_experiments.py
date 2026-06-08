#!/usr/bin/env python3
"""Compare BTC-only completion state-machine experiment manifests."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
from pathlib import Path
from typing import Any


DEFAULT_EXPERIMENTS = {
    "baseline": Path(
        "/Users/hot/web3Scientist/poly_backtest_data/derived/completion_candidate_pipeline_v1/"
        "pass_local_completion_residual_cooldown_officialfee_e055_t5_imb125_rc30_050_20260502_20260518_publicfull_v2"
    ),
    "seed_px_hi_065": Path(
        "/Users/hot/web3Scientist/poly_backtest_data/derived/completion_candidate_pipeline_v1/"
        "experiment_seedpxhi065_officialfee_e055_t5_imb125_rc30_050_btc_only"
    ),
    "residual_cap_025": Path(
        "/Users/hot/web3Scientist/poly_backtest_data/derived/completion_candidate_pipeline_v1/"
        "experiment_residualcap025_officialfee_e055_t5_imb125_rc30_btc_only"
    ),
    "offset_000_060": Path(
        "/Users/hot/web3Scientist/poly_backtest_data/derived/completion_candidate_pipeline_v1/"
        "experiment_offset000_060_officialfee_e055_t5_imb125_rc30_050_btc_only"
    ),
}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def load_experiment(label: str, path: Path) -> dict[str, Any]:
    manifest = read_json(path / "RESULT_SUMMARY_MANIFEST.json")
    compliance = read_json(path / "COMPLIANCE_MANIFEST.json")
    core = manifest.get("core_metrics") or {}
    return {
        "label": label,
        "path": str(path),
        "status": manifest.get("status"),
        "selected_candidate_count": core.get("selected_candidate_count"),
        "pair_actions": core.get("pair_actions"),
        "pair_qty": core.get("pair_qty"),
        "net_pair_cost_wavg": core.get("net_pair_cost_wavg"),
        "fee_after_pnl": core.get("fee_after_pnl"),
        "stress100_worst_pnl": core.get("stress100_worst_pnl"),
        "worst_day_fee_after_pnl": core.get("worst_day_fee_after_pnl"),
        "qty_residual_rate": core.get("qty_residual_rate"),
        "residual_cost_rate": core.get("residual_cost_rate"),
        "official_taker_fee": core.get("official_taker_fee"),
        "promotion_gate_pass": compliance.get("promotion_gate_pass"),
        "deployable": core.get("deployable"),
        "can_support_strategy_promotion": core.get("can_support_strategy_promotion"),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def render_report(report: dict[str, Any]) -> str:
    rows = report["experiments"]
    lines = [
        "# BTC-only Completion Experiment Comparison",
        "",
        f"- decision: `{report['decision']}`",
        f"- generated_at_utc: `{report['generated_at_utc']}`",
        "- scope: local BTC-only completion-store state-machine experiments; research-only.",
        "",
        "| label | status | pair_actions | fee_after_pnl | stress100_worst_pnl | worst_day_fee_after_pnl | residual_rate | pnl_delta_vs_baseline | stress_delta_vs_baseline |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['label']} | {row['status']} | {row['pair_actions']} | {row['fee_after_pnl']} | "
            f"{row['stress100_worst_pnl']} | {row['worst_day_fee_after_pnl']} | {row['qty_residual_rate']} | "
            f"{row['fee_after_pnl_delta_vs_baseline']} | {row['stress100_worst_pnl_delta_vs_baseline']} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `seed_px_hi_065` cuts too much useful inventory: PnL and stress both degrade.",
            "- `residual_cap_025` is effectively neutral; it does not solve residual concentration.",
            "- `offset_000_060` removes late-window risk but destroys stress robustness, so the 60-120s window is also a major edge source.",
            "- Current baseline remains the best of this tested set.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path(".tmp/btc_only_completion_experiment_comparison_latest"))
    parser.add_argument(
        "--experiment",
        action="append",
        default=[],
        help="Optional label=path. Defaults to the current baseline and three local experiments.",
    )
    args = parser.parse_args()

    experiments = dict(DEFAULT_EXPERIMENTS)
    for item in args.experiment:
        label, sep, path = item.partition("=")
        if not sep:
            raise SystemExit("--experiment must be label=path")
        experiments[label] = Path(path)

    rows = [load_experiment(label, path) for label, path in experiments.items()]
    baseline = next(row for row in rows if row["label"] == "baseline")
    for row in rows:
        row["fee_after_pnl_delta_vs_baseline"] = round(float(row["fee_after_pnl"]) - float(baseline["fee_after_pnl"]), 6)
        row["stress100_worst_pnl_delta_vs_baseline"] = round(
            float(row["stress100_worst_pnl"]) - float(baseline["stress100_worst_pnl"]),
            6,
        )

    report = {
        "generated_at_utc": utc_now(),
        "decision": "KEEP_BTC_ONLY_BASELINE_REMAINS_BEST_OF_TESTED_EXPERIMENTS",
        "private_truth_ready": False,
        "deployable": False,
        "promotion_gate_pass": False,
        "experiments": rows,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "btc_only_completion_experiment_comparison.csv", rows)
    (args.output_dir / "BTC_ONLY_COMPLETION_EXPERIMENT_COMPARISON.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "BTC_ONLY_COMPLETION_EXPERIMENT_COMPARISON.md").write_text(render_report(report), encoding="utf-8")
    print(json.dumps({"decision": report["decision"], "output_dir": str(args.output_dir)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
