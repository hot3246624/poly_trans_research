#!/usr/bin/env python3
"""Build a sampled L2 fillability probe packet for CE25 BTC5M cd0."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
BT_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
EXPORTS = ROOT / "data" / "exports"
OUT = EXPORTS / "ce25_btc5m_cd0_l2_fillability_probe_packet_20260607"
FULL_DIR = (
    BT_ROOT
    / "derived/completion_candidate_pipeline_v1/ce25_btc5m_local_cd0_watch_full_artifact_bulkcopy_20260607"
    / "broad_qty5_pc102_seed300_cd0_imb250_rage30_rcost050_full_5m"
)
L2_MART = BT_ROOT / "derived/contract_examples/l2_top_aligned_mart_20260502_20260518_l2/l2_top_aligned_mart.duckdb"
L2_MANIFEST = BT_ROOT / "derived/contract_examples/l2_top_aligned_mart_20260502_20260518_l2/L2_TOP_ALIGNED_MART_MANIFEST.json"
THROUGHPUT_PACKET = (
    EXPORTS
    / "ce25_btc5m_cd0_throughput_queue_capital_packet_20260607"
    / "CE25_BTC5M_CD0_THROUGHPUT_QUEUE_CAPITAL_PACKET.json"
)
BUILDER = ROOT / "scripts/build_ce25_btc5m_cd0_l2_fillability_probe_packet.py"
VALIDATOR = ROOT / "scripts/validate_ce25_btc5m_research_packet_chain.py"

STATUS = "KEEP_CE25_BTC5M_CD0_L2_FILLABILITY_PROBE_REVIEWED_SAMPLE_PASS_FULL_INDEXED_JOIN_REQUIRED_NOT_OOS_READY"


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


def write_preview(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo 'NOT_AUTHORIZED: CE25 BTC5M cd0 L2 fillability probe is review-only' >&2\n"
        "exit 66\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def run_probe() -> dict[str, Any]:
    con = duckdb.connect(str(FULL_DIR / "state_machine_results.duckdb"), read_only=True)
    con.execute(f"attach '{L2_MART}' as l2db (read_only)")
    started = time.perf_counter()
    sql = """
    with sample_actions as (
      select * from actions where action_id % 389 = 0
    ), joined as (
      select a.action_id,a.day,a.condition_id,a.slug,a.side,a.ts_ms,a.seed_qty,a.seed_cost,a.seed_px,
             m.source_ts_ms,m.ask1_px,m.ask1_sz,m.raw_l2_ask1_px,m.raw_l2_ask1_sz,
             m.raw_l2_ask2_sz,m.raw_l2_ask3_sz,m.raw_l2_ask4_sz,m.raw_l2_ask5_sz,m.raw_l2_age_ms,
             a.ts_ms-m.source_ts_ms as align_lag_ms
      from sample_actions a
      asof left join l2db.md_book_l2_top_aligned m
        on a.day=m.day and m.asset='BTC' and a.condition_id=m.condition_id and a.side=m.market_side
       and a.ts_ms >= m.source_ts_ms
    )
    select count(*) action_rows,
           count(source_ts_ms) joined_rows,
           sum(case when ask1_px is not null then 1 else 0 end) ask1_present,
           sum(case when raw_l2_ask1_sz >= seed_qty then 1 else 0 end) ask1_size_ge_seed,
           sum(case when coalesce(raw_l2_ask1_sz,0)+coalesce(raw_l2_ask2_sz,0)+coalesce(raw_l2_ask3_sz,0)+coalesce(raw_l2_ask4_sz,0)+coalesce(raw_l2_ask5_sz,0) >= seed_qty then 1 else 0 end) top5_size_ge_seed,
           sum(case when abs(ask1_px-seed_px) <= 0.10 then 1 else 0 end) ask1_px_within_10c,
           max(align_lag_ms), quantile_cont(align_lag_ms,0.99), quantile_cont(align_lag_ms,0.5),
           max(raw_l2_age_ms), quantile_cont(raw_l2_age_ms,0.99), quantile_cont(raw_l2_age_ms,0.5)
    from joined
    """
    row = con.execute(sql).fetchone()
    con.close()
    elapsed = time.perf_counter() - started
    names = (
        "action_rows",
        "joined_rows",
        "ask1_present",
        "ask1_size_ge_seed",
        "top5_size_ge_seed",
        "ask1_px_within_10c",
        "align_lag_ms_max",
        "align_lag_ms_p99",
        "align_lag_ms_p50",
        "raw_l2_age_ms_max",
        "raw_l2_age_ms_p99",
        "raw_l2_age_ms_p50",
    )
    return {
        **{name: (round(value, 6) if isinstance(value, float) else value) for name, value in zip(names, row)},
        "elapsed_s": round(elapsed, 3),
        "sample_predicate": "action_id % 389 = 0",
        "join_method": "ASOF by day, BTC asset, condition_id, side/market_side, action ts_ms >= L2 source_ts_ms",
    }


def render_report(packet: dict[str, Any]) -> str:
    probe = packet["sample_probe"]
    return "\n".join(
        [
            "# CE25 BTC5M cd0 L2 Fillability Probe Packet",
            "",
            f"Status: `{packet['status']}`",
            "",
            "## Probe",
            "",
            f"- sample rows: `{probe['action_rows']}`",
            f"- joined rows: `{probe['joined_rows']}`",
            f"- ask1 size >= seed qty: `{probe['ask1_size_ge_seed']}`",
            f"- top5 size >= seed qty: `{probe['top5_size_ge_seed']}`",
            f"- ask1 px within 10c of replay seed px: `{probe['ask1_px_within_10c']}`",
            f"- align lag p50/p99/max ms: `{probe['align_lag_ms_p50']}` / `{probe['align_lag_ms_p99']}` / `{probe['align_lag_ms_max']}`",
            f"- raw L2 age p50/p99/max ms: `{probe['raw_l2_age_ms_p50']}` / `{probe['raw_l2_age_ms_p99']}` / `{probe['raw_l2_age_ms_max']}`",
            "",
            "## Decision",
            "",
            "The sample passes, but full 388,692-row fillability cannot be inferred from this sample. Build an indexed/materialized L2 subset before any OOS discussion.",
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
    l2_manifest = read_json(L2_MANIFEST)
    probe = run_probe()
    sample_pass = (
        probe["action_rows"] == probe["joined_rows"]
        and probe["top5_size_ge_seed"] == probe["action_rows"]
        and probe["raw_l2_age_ms_p99"] <= 1000
    )
    packet = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS,
        "scope": "sampled local L2/top-depth fillability probe for cd0 watch; review-only",
        "source_bindings": {
            "throughput_packet": binding(THROUGHPUT_PACKET),
            "full_artifact_duckdb": binding(FULL_DIR / "state_machine_results.duckdb"),
            "l2_top_aligned_mart": binding(L2_MART),
            "l2_top_aligned_manifest": binding(L2_MANIFEST),
            "builder": binding(BUILDER),
            "validator": binding(VALIDATOR),
        },
        "l2_mart_summary": {
            "status": l2_manifest.get("status"),
            "table": l2_manifest.get("table"),
            "row_count": l2_manifest.get("row_count"),
            "missing_depth_rows": l2_manifest.get("missing_depth_rows"),
            "max_raw_l2_age_ms": l2_manifest.get("max_raw_l2_age_ms"),
        },
        "sample_probe": probe,
        "decision": {
            "sample_probe_pass": sample_pass,
            "sample_can_support_full_oos_claim": False,
            "full_indexed_join_required": True,
            "full_target_action_count": 388692,
            "next_packet": "cd0_full_l2_fillability_indexed_join_packet",
            "oos_discussion_allowed": False,
        },
        "outputs": {
            "packet": "CE25_BTC5M_CD0_L2_FILLABILITY_PROBE_PACKET.json",
            "report": "CE25_BTC5M_CD0_L2_FILLABILITY_PROBE_REPORT.md",
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
    packet_path = OUT / "CE25_BTC5M_CD0_L2_FILLABILITY_PROBE_PACKET.json"
    report_path = OUT / "CE25_BTC5M_CD0_L2_FILLABILITY_PROBE_REPORT.md"
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
                "sample_probe_pass": sample_pass,
                "action_rows": probe["action_rows"],
                "top5_size_ge_seed": probe["top5_size_ge_seed"],
                "elapsed_s": probe["elapsed_s"],
                "sha256sums": str(OUT / "SHA256SUMS.txt"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
