#!/usr/bin/env python3
"""Build a review-only CE25 BTC5M local dynamic sizing adapter packet.

This script does not execute replay, WS, OOS, live, canary, or order paths. It
only packages a local-source audit and a proposed adapter contract for review.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
BT_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
EXPORTS = ROOT / "data" / "exports"
OUTPUT_DIR = EXPORTS / "ce25_btc5m_local_dynamic_sizing_adapter_packet_20260607"

CANDIDATE_BASE_DIR = (
    BT_ROOT / "derived/completion_candidate_pipeline_v1/local_20260502_20260518_paircap102"
)
CANDIDATE_BASE_MANIFEST = CANDIDATE_BASE_DIR / "CANDIDATE_BASE_MANIFEST.json"
STATE_MACHINE = ROOT / "scripts" / "run_completion_candidate_state_machine.py"
PUBLIC_DYNAMIC_OVERRIDES_DIR = EXPORTS / "ce25_btc5m_dynamic_sizing_overrides_packet_20260606"
PUBLIC_DYNAMIC_OVERRIDES_CSV = PUBLIC_DYNAMIC_OVERRIDES_DIR / "ce25_btc5m_dynamic_sizing_overrides.csv"
PUBLIC_DYNAMIC_OVERRIDES_PACKET = (
    PUBLIC_DYNAMIC_OVERRIDES_DIR / "CE25_BTC5M_DYNAMIC_SIZING_OVERRIDES_PACKET.json"
)
LOCAL_RESIDUAL_SMOKE_PACKET = (
    EXPORTS
    / "ce25_btc5m_local_residual_replay_smoke_packet_20260607"
    / "CE25_BTC5M_LOCAL_RESIDUAL_REPLAY_SMOKE_PACKET.json"
)
BUILD_SCRIPT = ROOT / "scripts" / "build_ce25_btc5m_local_dynamic_sizing_adapter_packet.py"

STATUS = (
    "KEEP_CE25_BTC5M_LOCAL_DYNAMIC_SIZING_ADAPTER_REVIEW_ONLY_"
    "PUBLIC_PROFILE_DIRECT_OVERRIDE_DISALLOWED_REPLAY_APPROVAL_REQUIRED"
)


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


def binding(path: Path, required: bool = True) -> dict[str, Any]:
    item: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if path.exists():
        item.update({"sha256": sha256_file(path), "size": path.stat().st_size})
    elif required:
        item["missing_required"] = True
    return item


def output_from_manifest(manifest: dict[str, Any], key: str, default_name: str) -> Path:
    outputs = manifest.get("outputs", {})
    value = outputs.get(key, default_name) if isinstance(outputs, dict) else default_name
    return CANDIDATE_BASE_DIR / str(value)


def duckdb_overlap_audit() -> dict[str, Any]:
    manifest = read_json(CANDIDATE_BASE_MANIFEST)
    db_path = output_from_manifest(manifest, "duckdb", "candidate_base.duckdb")
    parquet_path = output_from_manifest(manifest, "parquet", "candidate_base.parquet")
    base_relation = "candidate_base"
    audit: dict[str, Any] = {
        "engine": "duckdb",
        "status": "AUDIT_NOT_RUN",
        "candidate_base_manifest": str(CANDIDATE_BASE_MANIFEST),
        "candidate_base_duckdb": str(db_path),
        "candidate_base_parquet": str(parquet_path),
        "public_profile_overrides_csv": str(PUBLIC_DYNAMIC_OVERRIDES_CSV),
        "source_window": "local_2026-05-02..2026-05-18_paircap102",
        "direct_sizing_overrides_csv_allowed": False,
    }

    try:
        import duckdb  # type: ignore
    except Exception as exc:  # pragma: no cover - operational fail-closed path
        audit.update(
            {
                "status": "AUDIT_FAILED_FAIL_CLOSED",
                "audit_error": f"duckdb_import_failed: {exc!r}",
                "direct_replay_interpretation": "DISALLOW_DIRECT_PUBLIC_PROFILE_OVERRIDES_UNTIL_DUCKDB_AUDIT_PASSES",
            }
        )
        return audit

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        table_exists = con.execute(
            "select count(*) from information_schema.tables where table_name = 'candidate_base'"
        ).fetchone()[0]
        if not table_exists:
            if not parquet_path.exists():
                raise FileNotFoundError(f"candidate_base table missing and parquet missing: {parquet_path}")
            con.execute(
                f"create temp view candidate_base as select * from read_parquet({str(parquet_path)!r})"
            )
            base_relation = "candidate_base"

        con.execute(
            f"""
            create temp view public_overrides as
            select
              nullif(trim(cast(condition_id as varchar)), '') as condition_id,
              nullif(trim(cast(slug as varchar)), '') as slug
            from read_csv_auto({str(PUBLIC_DYNAMIC_OVERRIDES_CSV)!r}, header=true)
            """
        )
        sql = f"""
        with
        candidate_counts as (
          select
            count(*) as row_count,
            count(distinct candidate_row_id) as candidate_row_id_count,
            count(distinct condition_id) as condition_count,
            count(distinct slug) as slug_count,
            min(day) as min_day,
            max(day) as max_day
          from {base_relation}
        ),
        candidate_reason_counts as (
          select candidate_reason, count(*) as row_count
          from {base_relation}
          group by 1
        ),
        override_counts as (
          select
            count(*) as row_count,
            count(distinct condition_id) filter (where condition_id is not null) as condition_count,
            count(distinct slug) filter (where slug is not null) as slug_count
          from public_overrides
        ),
        condition_overlap as (
          select count(distinct o.condition_id) as overlap_count
          from public_overrides o
          join (select distinct condition_id from {base_relation}) c using (condition_id)
          where o.condition_id is not null
        ),
        slug_overlap as (
          select count(distinct o.slug) as overlap_count
          from public_overrides o
          join (select distinct slug from {base_relation}) c using (slug)
          where o.slug is not null
        )
        select
          (select row_count from candidate_counts) as candidate_row_count,
          (select candidate_row_id_count from candidate_counts) as candidate_row_id_count,
          (select condition_count from candidate_counts) as candidate_condition_count,
          (select slug_count from candidate_counts) as candidate_slug_count,
          (select min_day from candidate_counts) as candidate_min_day,
          (select max_day from candidate_counts) as candidate_max_day,
          (select row_count from override_counts) as override_row_count,
          (select condition_count from override_counts) as override_condition_count,
          (select slug_count from override_counts) as override_slug_count,
          (select overlap_count from condition_overlap) as condition_overlap_count,
          (select overlap_count from slug_overlap) as slug_overlap_count
        """
        columns = [col[0] for col in con.execute(sql).description]
        values = con.fetchone()
        counts = dict(zip(columns, values, strict=True))
        reason_counts = [
            {"candidate_reason": row[0], "row_count": row[1]}
            for row in con.execute(
                f"""
                select candidate_reason, count(*) as row_count
                from {base_relation}
                group by 1
                order by row_count desc
                """
            ).fetchall()
        ]
        direct_allowed = bool(counts["condition_overlap_count"] or counts["slug_overlap_count"])
        audit.update(
            {
                "status": "PASS_ZERO_OVERLAP_DIRECT_PUBLIC_PROFILE_OVERRIDE_DISALLOWED",
                "candidate_counts": {
                    "row_count": counts["candidate_row_count"],
                    "candidate_row_id_count": counts["candidate_row_id_count"],
                    "condition_count": counts["candidate_condition_count"],
                    "slug_count": counts["candidate_slug_count"],
                    "min_day": counts["candidate_min_day"],
                    "max_day": counts["candidate_max_day"],
                },
                "candidate_reason_counts": reason_counts,
                "public_profile_override_counts": {
                    "row_count": counts["override_row_count"],
                    "condition_count": counts["override_condition_count"],
                    "slug_count": counts["override_slug_count"],
                },
                "overlap_counts": {
                    "condition_id": counts["condition_overlap_count"],
                    "slug": counts["slug_overlap_count"],
                },
                "direct_sizing_overrides_csv_allowed": direct_allowed,
                "direct_replay_interpretation": (
                    "DIRECT_PUBLIC_PROFILE_OVERRIDE_CAN_MATCH_LOCAL_SOURCE_REVIEW_REQUIRED"
                    if direct_allowed
                    else "DIRECT_PUBLIC_PROFILE_OVERRIDE_IS_NO_OP_ON_LOCAL_20260502_20260518_SOURCE"
                ),
                "disallowed_argument": "--sizing-overrides-csv "
                + str(PUBLIC_DYNAMIC_OVERRIDES_CSV),
            }
        )
    except Exception as exc:
        audit.update(
            {
                "status": "AUDIT_FAILED_FAIL_CLOSED",
                "audit_error": repr(exc),
                "direct_sizing_overrides_csv_allowed": False,
                "direct_replay_interpretation": "DISALLOW_DIRECT_PUBLIC_PROFILE_OVERRIDES_UNTIL_DUCKDB_AUDIT_PASSES",
            }
        )
    finally:
        con.close()
    return audit


def proposed_adapter_contract() -> dict[str, Any]:
    return {
        "contract_id": "CE25_BTC5M_LOCAL_DYNAMIC_SIZING_ADAPTER_CONTRACT_V0_REVIEW",
        "artifact_type": "proposal_only_no_runtime_generation",
        "override_key": "candidate_row_id",
        "allowed_input_source": "local_2026-05-02..2026-05-18 candidate_base rows only",
        "override_generation_rule": (
            "derive candidate_row_id-keyed max_open_cost/target_qty/enabled rows from ex-ante "
            "local candidate fields available before candidate selection"
        ),
        "allowed_ex_ante_fields": [
            "candidate_row_id",
            "day",
            "condition_id",
            "slug",
            "ts_ms",
            "offset_s",
            "side",
            "opposite_side",
            "high_side",
            "candidate_reason",
            "side_bid",
            "side_ask",
            "side_bid_sz",
            "side_ask_sz",
            "opp_bid",
            "opp_ask",
            "opp_bid_sz",
            "opp_ask_sz",
            "l1_pair_ask",
            "l1_pair_bid",
            "buy_full_10",
            "buy_vwap_10",
            "buy_filled_10",
            "buy_full_25",
            "buy_vwap_25",
            "buy_filled_25",
            "buy_full_60",
            "buy_vwap_60",
            "buy_filled_60",
            "buy_best_px",
            "buy_best_sz",
            "buy_available_qty",
            "sell_best_px",
            "sell_best_sz",
            "sell_available_qty",
            "side_bid_level_drop_qty",
            "side_ask_level_lift_qty",
            "side_bid_delta_qty",
            "side_ask_delta_qty",
            "book_update_reason",
            "public_trade_taker_side",
            "public_trade_price",
            "public_trade_size",
            "l1_pair_available_qty",
        ],
        "forbidden_leakage_fields": [
            "winner_side",
            "side_is_winner",
            "side_alignment",
            "source_last_trade",
            "source_last_trade_price",
            "source_last_trade_side",
            "outcome_label",
            "settlement",
            "payout",
            "net_pnl",
        ],
        "preflight_required": {
            "nonzero_candidate_row_id_matches": True,
            "duplicate_candidate_row_id_policy": "FAIL_CLOSED",
            "unknown_candidate_row_id_policy": "FAIL_CLOSED",
            "missing_candidate_row_id_policy": "FAIL_CLOSED",
            "coverage_policy": "DEFAULT_DISABLED_UNLESS_EXPLICIT_FULL_ROW_COVERAGE_APPROVED",
            "full_row_coverage_if_enabled": "require override rows == candidate_row_id_count before replay",
        },
        "runtime_boundary": {
            "direct_public_profile_condition_or_slug_overrides": "DISALLOWED_FOR_LOCAL_2026_05_02_2026_05_18",
            "generation_script_status": "NOT_IMPLEMENTED_BY_THIS_PACKET",
            "replay_execution": "REQUIRES_SEPARATE_APPROVAL_PACKET",
            "ws_oos_live_orders": "NOT_AUTHORIZED",
        },
    }


def write_preview(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo 'NOT_AUTHORIZED: CE25 BTC5M local dynamic sizing adapter packet is review-only' >&2\n"
        "echo 'Direct public-profile --sizing-overrides-csv is disallowed for local 2026-05-02..05-18.' >&2\n"
        "exit 66\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def render_report(packet: dict[str, Any]) -> str:
    audit = packet["duckdb_audit"]
    candidate = audit.get("candidate_counts", {})
    overrides = audit.get("public_profile_override_counts", {})
    overlap = audit.get("overlap_counts", {})
    contract = packet["proposed_local_bound_adapter_contract"]
    lines = [
        "# CE25 BTC5M Local Dynamic Sizing Adapter Packet",
        "",
        f"Status: `{packet['status']}`",
        "",
        "## Decision",
        "",
        "This packet is review-only. It does not build local sizing overrides and does not authorize replay, WS, OOS, live, canary, or orders.",
        "",
        "## DuckDB Audit",
        "",
        f"- audit status: `{audit.get('status')}`",
        f"- local candidate rows: `{candidate.get('row_count')}`",
        f"- local candidate conditions: `{candidate.get('condition_count')}`",
        f"- local candidate slugs: `{candidate.get('slug_count')}`",
        f"- public-profile override rows: `{overrides.get('row_count')}`",
        f"- public-profile override conditions: `{overrides.get('condition_count')}`",
        f"- public-profile override slugs: `{overrides.get('slug_count')}`",
        f"- condition overlap: `{overlap.get('condition_id')}`",
        f"- slug overlap: `{overlap.get('slug')}`",
        "",
        "Direct use of the public-profile `--sizing-overrides-csv` is disallowed for local `2026-05-02..05-18` because the local condition/slug overlap is zero.",
        "",
        "## Legitimate Local-Bound Adapter Contract",
        "",
        f"- key: `{contract['override_key']}`",
        "- generate overrides from ex-ante local candidate row fields only",
        "- require nonzero `candidate_row_id` match preflight",
        "- fail closed on duplicate, missing, or unknown candidate row ids",
        "- exclude outcome labels, `winner_side`, settlement/PnL fields, and source-last-trade leakage",
        "- default disabled unless a separate approval explicitly requires full-row coverage",
        "- require separate replay approval before any state-machine run",
        "",
        "## Bound Sources",
        "",
    ]
    for name, item in packet["source_bindings"].items():
        lines.append(f"- {name}: `{item.get('path')}` exists=`{item.get('exists')}`")
    lines.extend(
        [
            "",
            "## Non-Claims",
            "",
            "- replay_execution_authorized=false",
            "- ws_authorized=false",
            "- oos_authorized=false",
            "- live_ready=false",
            "- orders_authorized=false",
            "- private_truth_ready=false",
            "- strategy_promotion_ready=false",
            "- deployable=false",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    preview = OUTPUT_DIR / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    write_preview(preview)

    audit = duckdb_overlap_audit()
    packet = {
        "schema_version": 1,
        "status": STATUS,
        "created_at": utc_now(),
        "scope": "review-only local dynamic sizing adapter proposal for CE25 BTC5M local 2026-05-02..05-18",
        "source_window": {
            "candidate_base_dir": str(CANDIDATE_BASE_DIR),
            "candidate_base_manifest": binding(CANDIDATE_BASE_MANIFEST),
            "valid_days_utc": read_json(CANDIDATE_BASE_MANIFEST).get("days"),
        },
        "source_bindings": {
            "local_candidate_base_manifest": binding(CANDIDATE_BASE_MANIFEST),
            "state_machine_script": binding(STATE_MACHINE),
            "public_profile_dynamic_overrides_csv": binding(PUBLIC_DYNAMIC_OVERRIDES_CSV),
            "public_profile_dynamic_overrides_packet": binding(PUBLIC_DYNAMIC_OVERRIDES_PACKET),
            "local_residual_smoke_packet": binding(LOCAL_RESIDUAL_SMOKE_PACKET, required=False),
            "build_script": binding(BUILD_SCRIPT),
        },
        "duckdb_audit": audit,
        "direct_public_profile_override_decision": {
            "direct_sizing_overrides_csv_allowed": False,
            "disallowed_for_source_window": "local_2026-05-02..2026-05-18",
            "reason": audit.get("direct_replay_interpretation"),
            "disallowed_argument": "--sizing-overrides-csv " + str(PUBLIC_DYNAMIC_OVERRIDES_CSV),
        },
        "proposed_local_bound_adapter_contract": proposed_adapter_contract(),
        "outputs": {
            "packet": "CE25_BTC5M_LOCAL_DYNAMIC_SIZING_ADAPTER_PACKET.json",
            "report": "CE25_BTC5M_LOCAL_DYNAMIC_SIZING_ADAPTER_REPORT.md",
            "duckdb_audit": "CE25_BTC5M_LOCAL_DYNAMIC_SIZING_ADAPTER_DUCKDB_AUDIT.json",
            "command_preview_not_authorized": "COMMAND_PREVIEW_NOT_AUTHORIZED.sh",
            "sha256sums": "SHA256SUMS.txt",
        },
        "non_claims": {
            "replay_execution_authorized": False,
            "runner_authorized": False,
            "ws_authorized": False,
            "oos_authorized": False,
            "canary_authorized": False,
            "live_ready": False,
            "orders_authorized": False,
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "deployable": False,
        },
    }

    packet_path = OUTPUT_DIR / "CE25_BTC5M_LOCAL_DYNAMIC_SIZING_ADAPTER_PACKET.json"
    report_path = OUTPUT_DIR / "CE25_BTC5M_LOCAL_DYNAMIC_SIZING_ADAPTER_REPORT.md"
    audit_path = OUTPUT_DIR / "CE25_BTC5M_LOCAL_DYNAMIC_SIZING_ADAPTER_DUCKDB_AUDIT.json"
    write_json(packet_path, packet)
    write_json(audit_path, audit)
    report_path.write_text(render_report(packet), encoding="utf-8")

    manifest_files = [packet_path, report_path, audit_path, preview]
    sums_path = OUTPUT_DIR / "SHA256SUMS.txt"
    sums_path.write_text(
        "".join(f"{sha256_file(path)}  {path.relative_to(OUTPUT_DIR)}\n" for path in manifest_files),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": STATUS,
                "output_dir": str(OUTPUT_DIR),
                "packet": str(packet_path),
                "report": str(report_path),
                "duckdb_audit": str(audit_path),
                "condition_overlap_count": audit.get("overlap_counts", {}).get("condition_id"),
                "slug_overlap_count": audit.get("overlap_counts", {}).get("slug"),
                "direct_sizing_overrides_csv_allowed": False,
                "sha256sums": str(sums_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
