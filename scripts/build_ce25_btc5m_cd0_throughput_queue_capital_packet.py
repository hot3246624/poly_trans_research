#!/usr/bin/env python3
"""Build a throughput/queue/capital review packet for the CE25 BTC5M cd0 watch."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
BT_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
EXPORTS = ROOT / "data" / "exports"
OUT = EXPORTS / "ce25_btc5m_cd0_throughput_queue_capital_packet_20260607"
FULL_DIR = (
    BT_ROOT
    / "derived/completion_candidate_pipeline_v1/ce25_btc5m_local_cd0_watch_full_artifact_bulkcopy_20260607"
    / "broad_qty5_pc102_seed300_cd0_imb250_rage30_rcost050_full_5m"
)
FULL_PACKET = (
    EXPORTS
    / "ce25_btc5m_cd0_watch_full_artifact_packet_20260607"
    / "CE25_BTC5M_CD0_WATCH_FULL_ARTIFACT_PACKET.json"
)
BUILDER = ROOT / "scripts/build_ce25_btc5m_cd0_throughput_queue_capital_packet.py"
VALIDATOR = ROOT / "scripts/validate_ce25_btc5m_research_packet_chain.py"

STATUS = "KEEP_CE25_BTC5M_CD0_THROUGHPUT_QUEUE_CAPITAL_REVIEWED_FILLABILITY_REQUIRED_NOT_OOS_READY"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def binding(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if path.exists():
        out.update({"sha256": sha256_file(path), "size": path.stat().st_size})
    return out


def scalar_row(con: duckdb.DuckDBPyConnection, sql: str) -> list[Any]:
    return list(con.execute(sql).fetchone())


def global_open_cost_estimate(actions_csv: Path) -> dict[str, Any]:
    open_cost_by_condition: dict[str, float] = {}
    end_by_condition: dict[str, int] = {}
    values: list[float] = []
    max_value = 0.0
    max_ts = None
    with actions_csv.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ts_ms = int(row["ts_ms"])
            expired = [condition_id for condition_id, end_ms in end_by_condition.items() if end_ms <= ts_ms]
            for condition_id in expired:
                open_cost_by_condition.pop(condition_id, None)
                end_by_condition.pop(condition_id, None)
            condition_id = row["condition_id"]
            match = re.search(r"-(\d{10})$", row["slug"])
            if match:
                end_by_condition[condition_id] = (int(match.group(1)) + 300) * 1000
            open_cost_by_condition[condition_id] = float(row["inventory_yes_cost_after"] or 0.0) + float(
                row["inventory_no_cost_after"] or 0.0
            )
            total = sum(open_cost_by_condition.values())
            values.append(total)
            if total > max_value:
                max_value = total
                max_ts = row["ts_iso"]

    sorted_values = sorted(values)

    def quantile(q: float) -> float | None:
        if not sorted_values:
            return None
        pos = (len(sorted_values) - 1) * q
        lo = math.floor(pos)
        hi = math.ceil(pos)
        if lo == hi:
            return sorted_values[lo]
        weight = pos - lo
        return sorted_values[lo] * (1 - weight) + sorted_values[hi] * weight

    return {
        "sample_count": len(values),
        "p50": round(float(quantile(0.50) or 0.0), 6),
        "p90": round(float(quantile(0.90) or 0.0), 6),
        "p99": round(float(quantile(0.99) or 0.0), 6),
        "p999": round(float(quantile(0.999) or 0.0), 6),
        "max": round(max_value, 6),
        "max_ts": max_ts,
        "method": "sum latest per-market inventory cost and clear markets at slug_start_sec+300s",
    }


def write_preview(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo 'NOT_AUTHORIZED: CE25 BTC5M cd0 throughput packet is review-only' >&2\n"
        "exit 66\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def render_report(packet: dict[str, Any]) -> str:
    throughput = packet["throughput"]
    capital = packet["capital_path"]
    return "\n".join(
        [
            "# CE25 BTC5M cd0 Throughput / Queue / Capital Packet",
            "",
            f"Status: `{packet['status']}`",
            "",
            "## Finding",
            "",
            "The cd0 watch path is not blocked by simulated open capital under immediate pair/merge reuse assumptions; it is blocked by execution throughput and fillability evidence.",
            "",
            f"- actions: `{throughput['actions']}`",
            f"- active markets: `{throughput['active_markets']}`",
            f"- actions per market p50/p90/p99/max: `{throughput['actions_per_market']['p50']}` / `{throughput['actions_per_market']['p90']}` / `{throughput['actions_per_market']['p99']}` / `{throughput['actions_per_market']['max']}`",
            f"- actions per minute p99/max: `{throughput['actions_per_minute']['p99']}` / `{throughput['actions_per_minute']['max']}`",
            f"- actions per second p99/max: `{throughput['actions_per_second']['p99']}` / `{throughput['actions_per_second']['max']}`",
            f"- per-market open-cost max: `{capital['per_market_open_cost_after']['max']}`",
            f"- estimated global open-cost p99/max: `{capital['global_open_cost_estimate']['p99']}` / `{capital['global_open_cost_estimate']['max']}`",
            f"- gross buy turnover vs 300 USDC: `{capital['gross_buy_turnover_vs_300_usdc']}`",
            "",
            "## Decision",
            "",
            "Next step is a fillability/queue feasibility packet using L2/top-depth evidence and explicit action-rate guards. Do not advance to OOS from this packet.",
            "",
            "## Non-Claims",
            "",
            "- oos_authorized=false",
            "- runner_authorized=false",
            "- private_truth_ready=false",
            "- strategy_promotion_ready=false",
            "- live_ready=false",
            "- deployable=false",
        ]
    ) + "\n"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    full_packet = read_json(FULL_PACKET)
    full_metrics = full_packet["full_artifact"]["core_metrics"]
    con = duckdb.connect(str(FULL_DIR / "state_machine_results.duckdb"), read_only=True)
    actions_per_market = scalar_row(
        con,
        """
        select min(c), quantile_cont(c,0.5), quantile_cont(c,0.9), quantile_cont(c,0.99), max(c), avg(c)
        from (select condition_id, count(*) c from actions group by 1)
        """,
    )
    seed_qty = scalar_row(
        con,
        "select min(seed_qty), quantile_cont(seed_qty,0.5), quantile_cont(seed_qty,0.9), quantile_cont(seed_qty,0.99), max(seed_qty), avg(seed_qty) from actions",
    )
    seed_cost = scalar_row(
        con,
        "select min(seed_cost), quantile_cont(seed_cost,0.5), quantile_cont(seed_cost,0.9), quantile_cont(seed_cost,0.99), max(seed_cost), avg(seed_cost) from actions",
    )
    open_cost = scalar_row(
        con,
        "select min(inventory_yes_cost_after+inventory_no_cost_after), quantile_cont(inventory_yes_cost_after+inventory_no_cost_after,0.5), quantile_cont(inventory_yes_cost_after+inventory_no_cost_after,0.9), quantile_cont(inventory_yes_cost_after+inventory_no_cost_after,0.99), max(inventory_yes_cost_after+inventory_no_cost_after), avg(inventory_yes_cost_after+inventory_no_cost_after) from actions",
    )
    per_second = scalar_row(
        con,
        """
        with per_second as (
          select cast(floor(ts_ms/1000) as bigint) sec, count(*) actions, sum(seed_cost) seed_cost
          from actions group by 1
        )
        select min(actions), quantile_cont(actions,0.5), quantile_cont(actions,0.9), quantile_cont(actions,0.99), max(actions), avg(actions), max(seed_cost)
        from per_second
        """,
    )
    per_minute = scalar_row(
        con,
        """
        with per_minute as (
          select cast(floor(ts_ms/60000) as bigint) minb, count(*) actions, sum(seed_cost) seed_cost
          from actions group by 1
        )
        select min(actions), quantile_cont(actions,0.5), quantile_cont(actions,0.9), quantile_cont(actions,0.99), max(actions), avg(actions), max(seed_cost)
        from per_minute
        """,
    )
    inter_action = scalar_row(
        con,
        """
        with x as (
          select condition_id, ts_ms, (ts_ms-lag(ts_ms) over(partition by condition_id order by ts_ms))/1000.0 as dt_s
          from actions
        ), y as (select dt_s from x where dt_s is not null)
        select count(*), min(dt_s), quantile_cont(dt_s,0.1), quantile_cont(dt_s,0.5), quantile_cont(dt_s,0.9), quantile_cont(dt_s,0.99), max(dt_s), avg(dt_s)
        from y
        """,
    )
    con.close()

    def dist(row: list[Any], names: tuple[str, ...]) -> dict[str, Any]:
        return {name: round(value, 6) if isinstance(value, float) else value for name, value in zip(names, row)}

    packet = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS,
        "scope": "review-only throughput/queue/capital feasibility over cd0 full artifact; no OOS/live authority",
        "source_bindings": {
            "cd0_full_artifact_packet": binding(FULL_PACKET),
            "full_result_summary_manifest": binding(FULL_DIR / "RESULT_SUMMARY_MANIFEST.json"),
            "full_duckdb": binding(FULL_DIR / "state_machine_results.duckdb"),
            "builder": binding(BUILDER),
            "validator": binding(VALIDATOR),
        },
        "throughput": {
            "actions": int(full_metrics["selected_candidate_count"]),
            "active_markets": int(full_metrics["active_markets"]),
            "actions_per_market": dist(actions_per_market, ("min", "p50", "p90", "p99", "max", "avg")),
            "actions_per_second": dist(per_second, ("min", "p50", "p90", "p99", "max", "avg", "max_seed_cost")),
            "actions_per_minute": dist(per_minute, ("min", "p50", "p90", "p99", "max", "avg", "max_seed_cost")),
            "inter_action_seconds_by_market": dist(
                inter_action, ("count", "min", "p10", "p50", "p90", "p99", "max", "avg")
            ),
        },
        "capital_path": {
            "seed_qty": dist(seed_qty, ("min", "p50", "p90", "p99", "max", "avg")),
            "seed_cost": dist(seed_cost, ("min", "p50", "p90", "p99", "max", "avg")),
            "per_market_open_cost_after": dist(open_cost, ("min", "p50", "p90", "p99", "max", "avg")),
            "global_open_cost_estimate": global_open_cost_estimate(FULL_DIR / "actions.csv"),
            "assumed_initial_capital_usdc": 300.0,
            "gross_buy_cost": full_metrics["gross_buy_cost"],
            "net_pnl": full_metrics["net_pnl"],
            "gross_buy_turnover_vs_300_usdc": round(float(full_metrics["gross_buy_cost"]) / 300.0, 6),
            "net_pnl_vs_300_usdc_if_full_reuse_assumption_held": round(float(full_metrics["net_pnl"]) / 300.0, 6),
            "capital_reuse_warning": (
                "The high return versus 300 USDC is only a replay-local reuse proxy. It assumes rapid pair/merge capital "
                "reuse and does not prove private execution, queue priority, or live fillability."
            ),
        },
        "decision": {
            "capital_open_cost_not_primary_blocker_under_replay_reuse_assumption": True,
            "primary_blocker": "throughput_queue_fillability",
            "requires_l2_top_depth_or_fillability_replay": True,
            "requires_action_rate_guards_before_oos": True,
            "oos_discussion_allowed": False,
            "next_packet": "cd0_l2_fillability_queue_feasibility_review_packet",
        },
        "outputs": {
            "packet": "CE25_BTC5M_CD0_THROUGHPUT_QUEUE_CAPITAL_PACKET.json",
            "report": "CE25_BTC5M_CD0_THROUGHPUT_QUEUE_CAPITAL_REPORT.md",
            "command_preview": "COMMAND_PREVIEW_NOT_AUTHORIZED.sh",
            "sha256sums": "SHA256SUMS.txt",
        },
        "non_claims": {
            "oos_authorized": False,
            "runner_authorized": False,
            "orders_authorized": False,
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "deployable": False,
        },
        "highest_allowed_status": STATUS,
    }
    packet_path = OUT / "CE25_BTC5M_CD0_THROUGHPUT_QUEUE_CAPITAL_PACKET.json"
    report_path = OUT / "CE25_BTC5M_CD0_THROUGHPUT_QUEUE_CAPITAL_REPORT.md"
    preview_path = OUT / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    write_json(packet_path, packet)
    report_path.write_text(render_report(packet), encoding="utf-8")
    write_preview(preview_path)
    files = [packet_path, report_path, preview_path]
    (OUT / "SHA256SUMS.txt").write_text(
        "".join(f"{sha256_file(path)}  {path.relative_to(OUT)}\n" for path in files),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": STATUS,
                "output_dir": str(OUT),
                "packet": str(packet_path),
                "report": str(report_path),
                "primary_blocker": packet["decision"]["primary_blocker"],
                "actions_per_minute_max": packet["throughput"]["actions_per_minute"]["max"],
                "global_open_cost_max": packet["capital_path"]["global_open_cost_estimate"]["max"],
                "sha256sums": str(OUT / "SHA256SUMS.txt"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
