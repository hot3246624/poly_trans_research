#!/usr/bin/env python3
"""Validate the CE25 BTC5M local Backtest V1 research packet chain.

This validator is designed for heartbeat automation. It performs local
read-only checks only. It does not build replay, start WS/OOS, or touch live
paths.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
BT_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")

EXPECTED_DAYS = [
    *[f"2026-05-{day:02d}" for day in range(2, 14)],
    "2026-05-16",
    "2026-05-17",
    "2026-05-18",
]
EXPECTED_ASSETS = ["BNB", "BTC", "DOGE", "ETH", "HYPE", "SOL", "XRP"]
EXPECTED_SEARCH_SAFE_ROWS = 4_655_442
EXPECTED_CANONICAL_AUDIT_SELECTED = 80

STATUS_OK = (
    "KEEP_CE25_BTC5M_LOCAL_BACKTEST_V1_COMPACT_ARTIFACTS_OK_"
    "LEGACY_STRICT_CACHE_OPTIONAL_RESEARCH_ONLY_NOT_OOS_READY"
)
STATUS_BLOCKED = "BLOCKED_CE25_BTC5M_LOCAL_RESEARCH_PACKET_CHAIN_DRIFT_REVIEW_REQUIRED"

BACKTEST_V1_ARTIFACTS = {
    "search_safe_manifest": BT_ROOT
    / "derived/multiasset_l1_flow_event_store_v1/20260502_20260518_minsz10/L1_FLOW_SEARCH_SAFE_VIEW_MANIFEST.json",
    "l1_flow_manifest": BT_ROOT
    / "derived/multiasset_l1_flow_event_store_v1/20260502_20260518_minsz10/L1_FLOW_EVENT_STORE_MANIFEST.json",
    "core_replay_manifest": BT_ROOT
    / "verification_store/replay_store_multiasset_core_v1/20260502_20260518_core/REPLAY_STORE_V2_MANIFEST.json",
    "l2_replay_manifest": BT_ROOT
    / "verification_store/replay_store_multiasset_l2_v1/20260502_20260518_l2/REPLAY_STORE_V2_MANIFEST.json",
    "l2_top_aligned_manifest": BT_ROOT
    / "derived/contract_examples/l2_top_aligned_mart_20260502_20260518_l2/L2_TOP_ALIGNED_MART_MANIFEST.json",
    "canonical_audit_manifest": BT_ROOT
    / "derived/contract_examples/backtest_candidate_audit_pack_with_l2_evidence_latest/BACKTEST_CANDIDATE_AUDIT_PACK_MANIFEST.json",
    "readiness_gate": BT_ROOT
    / "derived/contract_examples/xuan_backtest_v1_strategy_readiness_latest/XUAN_BACKTEST_V1_STRATEGY_READINESS_GATE.json",
}

CE25_PACKET_ROOTS = {
    "local_source_alignment": ROOT / "data/exports/ce25_btc5m_local_replay_source_alignment_packet_20260607",
    "local_residual_smoke": ROOT / "data/exports/ce25_btc5m_local_residual_replay_smoke_packet_20260607",
    "local_dynamic_adapter": ROOT / "data/exports/ce25_btc5m_local_dynamic_sizing_adapter_packet_20260607",
    "local_dynamic_overrides": ROOT / "data/exports/ce25_btc5m_local_dynamic_sizing_overrides_packet_20260607",
    "local_policy_frontier": ROOT / "data/exports/ce25_btc5m_local_policy_frontier_packet_20260607",
    "local_fast_policy_grid": ROOT / "data/exports/ce25_btc5m_local_fast_policy_grid_packet_20260607",
    "cd0_watch_full_artifact": ROOT / "data/exports/ce25_btc5m_cd0_watch_full_artifact_packet_20260607",
    "cd0_throughput_queue_capital": ROOT / "data/exports/ce25_btc5m_cd0_throughput_queue_capital_packet_20260607",
    "cd0_l2_fillability_probe": ROOT / "data/exports/ce25_btc5m_cd0_l2_fillability_probe_packet_20260607",
    "cd0_full_l2_fillability_indexed": ROOT
    / "data/exports/ce25_btc5m_cd0_full_l2_fillability_indexed_packet_20260607",
    "cd0_price_fill_model_revision": ROOT
    / "data/exports/ce25_btc5m_cd0_price_fill_model_revision_packet_20260607",
    "broad_cd5_price_fill_model_comparison": ROOT
    / "data/exports/ce25_btc5m_broad_cd5_price_fill_model_comparison_packet_20260607",
    "state_machine_executable_price_research": ROOT
    / "data/exports/ce25_btc5m_state_machine_executable_price_research_packet_20260607",
    "executable_price_adapter_grid": ROOT
    / "data/exports/ce25_btc5m_executable_price_adapter_grid_packet_20260607",
    "executable_taker_pair_edge_supply": ROOT
    / "data/exports/ce25_btc5m_executable_taker_pair_edge_supply_packet_20260607",
    "maker_bid_edge_supply": ROOT
    / "data/exports/ce25_btc5m_maker_bid_edge_supply_packet_20260607",
    "maker_queue_shadow_design": ROOT
    / "data/exports/ce25_btc5m_maker_queue_shadow_design_packet_20260608",
    "maker_queue_public_shadow_staging": ROOT
    / "data/exports/ce25_btc5m_maker_queue_public_shadow_staging_packet_20260608",
}

CE25_PACKET_FILES = {
    "local_source_alignment_packet": CE25_PACKET_ROOTS["local_source_alignment"]
    / "CE25_BTC5M_LOCAL_REPLAY_SOURCE_ALIGNMENT_PACKET.json",
    "local_source_alignment_manifest": CE25_PACKET_ROOTS["local_source_alignment"]
    / "CE25_BTC5M_LOCAL_REPLAY_SOURCE_ALIGNMENT_HASH_MANIFEST.json",
    "local_residual_packet": CE25_PACKET_ROOTS["local_residual_smoke"]
    / "CE25_BTC5M_LOCAL_RESIDUAL_REPLAY_SMOKE_PACKET.json",
    "local_residual_sha256sums": CE25_PACKET_ROOTS["local_residual_smoke"] / "SHA256SUMS.txt",
    "local_dynamic_adapter_packet": CE25_PACKET_ROOTS["local_dynamic_adapter"]
    / "CE25_BTC5M_LOCAL_DYNAMIC_SIZING_ADAPTER_PACKET.json",
    "local_dynamic_adapter_sha256sums": CE25_PACKET_ROOTS["local_dynamic_adapter"] / "SHA256SUMS.txt",
    "local_dynamic_overrides_packet": CE25_PACKET_ROOTS["local_dynamic_overrides"]
    / "CE25_BTC5M_LOCAL_DYNAMIC_SIZING_OVERRIDES_PACKET.json",
    "local_dynamic_overrides_sha256sums": CE25_PACKET_ROOTS["local_dynamic_overrides"] / "SHA256SUMS.txt",
    "local_policy_frontier_packet": CE25_PACKET_ROOTS["local_policy_frontier"]
    / "CE25_BTC5M_LOCAL_POLICY_FRONTIER_PACKET.json",
    "local_policy_frontier_sha256sums": CE25_PACKET_ROOTS["local_policy_frontier"] / "SHA256SUMS.txt",
    "local_fast_policy_grid_packet": CE25_PACKET_ROOTS["local_fast_policy_grid"]
    / "CE25_BTC5M_LOCAL_FAST_POLICY_GRID_PACKET.json",
    "local_fast_policy_grid_sha256sums": CE25_PACKET_ROOTS["local_fast_policy_grid"] / "SHA256SUMS.txt",
    "cd0_watch_full_artifact_packet": CE25_PACKET_ROOTS["cd0_watch_full_artifact"]
    / "CE25_BTC5M_CD0_WATCH_FULL_ARTIFACT_PACKET.json",
    "cd0_watch_full_artifact_sha256sums": CE25_PACKET_ROOTS["cd0_watch_full_artifact"] / "SHA256SUMS.txt",
    "cd0_throughput_queue_capital_packet": CE25_PACKET_ROOTS["cd0_throughput_queue_capital"]
    / "CE25_BTC5M_CD0_THROUGHPUT_QUEUE_CAPITAL_PACKET.json",
    "cd0_throughput_queue_capital_sha256sums": CE25_PACKET_ROOTS["cd0_throughput_queue_capital"]
    / "SHA256SUMS.txt",
    "cd0_l2_fillability_probe_packet": CE25_PACKET_ROOTS["cd0_l2_fillability_probe"]
    / "CE25_BTC5M_CD0_L2_FILLABILITY_PROBE_PACKET.json",
    "cd0_l2_fillability_probe_sha256sums": CE25_PACKET_ROOTS["cd0_l2_fillability_probe"]
    / "SHA256SUMS.txt",
    "cd0_full_l2_fillability_indexed_packet": CE25_PACKET_ROOTS["cd0_full_l2_fillability_indexed"]
    / "CE25_BTC5M_CD0_FULL_L2_FILLABILITY_INDEXED_PACKET.json",
    "cd0_full_l2_fillability_indexed_sha256sums": CE25_PACKET_ROOTS["cd0_full_l2_fillability_indexed"]
    / "SHA256SUMS.txt",
    "cd0_price_fill_model_revision_packet": CE25_PACKET_ROOTS["cd0_price_fill_model_revision"]
    / "CE25_BTC5M_CD0_PRICE_FILL_MODEL_REVISION_PACKET.json",
    "cd0_price_fill_model_revision_sha256sums": CE25_PACKET_ROOTS["cd0_price_fill_model_revision"]
    / "SHA256SUMS.txt",
    "broad_cd5_price_fill_model_comparison_packet": CE25_PACKET_ROOTS[
        "broad_cd5_price_fill_model_comparison"
    ]
    / "CE25_BTC5M_BROAD_CD5_PRICE_FILL_MODEL_COMPARISON_PACKET.json",
    "broad_cd5_price_fill_model_comparison_sha256sums": CE25_PACKET_ROOTS[
        "broad_cd5_price_fill_model_comparison"
    ]
    / "SHA256SUMS.txt",
    "state_machine_executable_price_research_packet": CE25_PACKET_ROOTS[
        "state_machine_executable_price_research"
    ]
    / "CE25_BTC5M_STATE_MACHINE_EXECUTABLE_PRICE_RESEARCH_PACKET.json",
    "state_machine_executable_price_research_sha256sums": CE25_PACKET_ROOTS[
        "state_machine_executable_price_research"
    ]
    / "SHA256SUMS.txt",
    "executable_price_adapter_grid_packet": CE25_PACKET_ROOTS["executable_price_adapter_grid"]
    / "CE25_BTC5M_EXECUTABLE_PRICE_ADAPTER_GRID_PACKET.json",
    "executable_price_adapter_grid_sha256sums": CE25_PACKET_ROOTS["executable_price_adapter_grid"]
    / "SHA256SUMS.txt",
    "executable_taker_pair_edge_supply_packet": CE25_PACKET_ROOTS["executable_taker_pair_edge_supply"]
    / "CE25_BTC5M_EXECUTABLE_TAKER_PAIR_EDGE_SUPPLY_PACKET.json",
    "executable_taker_pair_edge_supply_sha256sums": CE25_PACKET_ROOTS["executable_taker_pair_edge_supply"]
    / "SHA256SUMS.txt",
    "maker_bid_edge_supply_packet": CE25_PACKET_ROOTS["maker_bid_edge_supply"]
    / "CE25_BTC5M_MAKER_BID_EDGE_SUPPLY_PACKET.json",
    "maker_bid_edge_supply_sha256sums": CE25_PACKET_ROOTS["maker_bid_edge_supply"] / "SHA256SUMS.txt",
    "maker_queue_shadow_design_packet": CE25_PACKET_ROOTS["maker_queue_shadow_design"]
    / "CE25_BTC5M_MAKER_QUEUE_SHADOW_DESIGN_PACKET.json",
    "maker_queue_shadow_design_sha256sums": CE25_PACKET_ROOTS["maker_queue_shadow_design"]
    / "SHA256SUMS.txt",
    "maker_queue_public_shadow_staging_packet": CE25_PACKET_ROOTS["maker_queue_public_shadow_staging"]
    / "CE25_BTC5M_MAKER_QUEUE_PUBLIC_SHADOW_STAGING_PACKET.json",
    "maker_queue_public_shadow_staging_sha256sums": CE25_PACKET_ROOTS[
        "maker_queue_public_shadow_staging"
    ]
    / "SHA256SUMS.txt",
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def issue(issues: list[str], text: str) -> None:
    issues.append(text)


def resolve_manifest_path(manifest_path: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else manifest_path.parent / path


def check_sha256sums(root: Path, sums_path: Path) -> dict[str, Any]:
    if not sums_path.is_file():
        return {"ok": False, "checked": 0, "issues": [f"missing sha256 sums {sums_path}"]}
    checked = 0
    issues: list[str] = []
    for raw in sums_path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        checked += 1
        try:
            expected, rel = raw.split("  ", 1)
        except ValueError:
            issues.append(f"malformed sha256 line: {raw}")
            continue
        path = root / rel
        if not path.exists():
            issues.append(f"missing artifact {path}")
            continue
        actual = sha256_file(path)
        if actual != expected:
            issues.append(f"sha256 drift {path}")
    return {"ok": not issues, "checked": checked, "issues": issues}


def check_json_hash_manifest(manifest_path: Path, entries_key: str) -> dict[str, Any]:
    if not manifest_path.is_file():
        return {"ok": False, "checked": 0, "issues": [f"missing hash manifest {manifest_path}"]}
    manifest = load_json(manifest_path)
    entries = manifest.get(entries_key)
    if isinstance(entries, dict):
        iterable = entries.values()
    else:
        iterable = entries or []
    checked = 0
    issues: list[str] = []
    for meta in iterable:
        checked += 1
        path_value = meta.get("path")
        expected = meta.get("sha256")
        if not path_value or not expected:
            issues.append(f"incomplete hash metadata in {manifest_path}")
            continue
        path = resolve_manifest_path(manifest_path, str(path_value))
        if not path.exists():
            issues.append(f"missing artifact {path}")
            continue
        if sha256_file(path) != expected:
            issues.append(f"sha256 drift {path}")
    return {"ok": not issues, "checked": checked, "issues": issues}


def query_duckdb_count(db_path: Path, table: str) -> dict[str, Any]:
    if not db_path.exists():
        return {"ok": False, "present": False, "issues": [f"missing duckdb {db_path}"]}
    try:
        import duckdb  # type: ignore
    except Exception as exc:  # pragma: no cover - environment dependent
        return {"ok": False, "present": True, "issues": [f"duckdb import failed: {exc!r}"]}
    try:
        con = duckdb.connect(str(db_path), read_only=True)
        row_count = int(con.execute(f"select count(*) from {table}").fetchone()[0])
    except Exception as exc:
        return {"ok": False, "present": True, "issues": [f"duckdb query failed: {exc!r}"]}
    finally:
        try:
            con.close()
        except Exception:
            pass
    return {"ok": row_count > 0, "present": True, "row_count": row_count, "issues": [] if row_count > 0 else ["empty table"]}


def check_backtest_v1_artifacts() -> dict[str, Any]:
    issues: list[str] = []
    checks: dict[str, Any] = {}
    for name, path in BACKTEST_V1_ARTIFACTS.items():
        if not path.exists():
            issue(issues, f"missing Backtest V1 artifact {name}: {path}")
            checks[name] = {"present": False}
        else:
            checks[name] = {"present": True, "sha256": sha256_file(path)}

    if issues:
        return {"ok": False, "issues": issues, "checks": checks}

    search = load_json(BACKTEST_V1_ARTIFACTS["search_safe_manifest"])
    search_days = sorted(search.get("days") or [])
    search_assets = sorted(search.get("assets") or [])
    search_rows = int((search.get("outputs") or {}).get("row_count") or 0)
    if search_days != sorted(EXPECTED_DAYS):
        issue(issues, f"search-safe days mismatch: {search_days}")
    if search_assets != sorted(EXPECTED_ASSETS):
        issue(issues, f"search-safe assets mismatch: {search_assets}")
    if search_rows != EXPECTED_SEARCH_SAFE_ROWS:
        issue(issues, f"search-safe row_count mismatch: {search_rows}")
    search_db = BACKTEST_V1_ARTIFACTS["search_safe_manifest"].parent / str(
        (search.get("outputs") or {}).get("duckdb") or "event_store.duckdb"
    )
    table = str((search.get("outputs") or {}).get("table") or "l1_taker_buy_events_search_safe")
    checks["search_safe_duckdb_query"] = query_duckdb_count(search_db, table)
    if not checks["search_safe_duckdb_query"]["ok"]:
        issues.extend(checks["search_safe_duckdb_query"]["issues"])

    l2_top = load_json(BACKTEST_V1_ARTIFACTS["l2_top_aligned_manifest"])
    if l2_top.get("status") != "OK":
        issue(issues, f"l2 top-aligned status not OK: {l2_top.get('status')}")
    if int(l2_top.get("row_count") or 0) <= 0:
        issue(issues, "l2 top-aligned row_count is empty")

    audit = load_json(BACKTEST_V1_ARTIFACTS["canonical_audit_manifest"])
    if audit.get("ok") is not True:
        issue(issues, "canonical audit manifest ok is not true")
    if int(audit.get("selected_candidate_count") or 0) != EXPECTED_CANONICAL_AUDIT_SELECTED:
        issue(issues, f"canonical audit selected_candidate_count mismatch: {audit.get('selected_candidate_count')}")
    if int(audit.get("private_promotion_ready_count") or 0) != 0:
        issue(issues, "canonical audit private_promotion_ready_count is nonzero")

    readiness = load_json(BACKTEST_V1_ARTIFACTS["readiness_gate"])
    for flag in ("private_truth_ready", "strategy_promotion_ready", "live_ready", "deployable", "live_orders_allowed"):
        if flag in readiness and readiness.get(flag) is not False:
            issue(issues, f"readiness flag not false: {flag}={readiness.get(flag)!r}")

    core = load_json(BACKTEST_V1_ARTIFACTS["core_replay_manifest"])
    core_db = Path(str(core.get("output_duckdb") or core.get("duckdb") or "store.duckdb"))
    if not core_db.is_absolute():
        core_db = BACKTEST_V1_ARTIFACTS["core_replay_manifest"].parent / core_db
    checks["core_replay_md_book_l1"] = query_duckdb_count(core_db, "md_book_l1")
    checks["core_replay_md_trades"] = query_duckdb_count(core_db, "md_trades")
    for key in ("core_replay_md_book_l1", "core_replay_md_trades"):
        if not checks[key]["ok"]:
            issues.extend(checks[key]["issues"])

    checks["summary"] = {
        "search_safe_row_count": search_rows,
        "canonical_audit_selected_candidate_count": int(audit.get("selected_candidate_count") or 0),
        "private_promotion_ready_count": int(audit.get("private_promotion_ready_count") or 0),
        "readiness_status": readiness.get("status"),
        "l2_top_aligned_status": l2_top.get("status"),
        "legacy_strict_cache_required_for_backtest_v1_mainline": False,
    }
    return {"ok": not issues, "issues": issues, "checks": checks}


def check_ce25_packets() -> dict[str, Any]:
    issues: list[str] = []
    checks: dict[str, Any] = {}
    for name, path in CE25_PACKET_FILES.items():
        if not path.exists():
            issue(issues, f"missing CE25 packet file {name}: {path}")
            checks[name] = {"present": False}
        else:
            checks[name] = {"present": True, "sha256": sha256_file(path)}

    if issues:
        return {"ok": False, "issues": issues, "checks": checks}

    checks["source_alignment_hash_manifest"] = check_json_hash_manifest(
        CE25_PACKET_FILES["local_source_alignment_manifest"], "files"
    )
    checks["local_residual_sha256sums"] = check_sha256sums(
        CE25_PACKET_ROOTS["local_residual_smoke"], CE25_PACKET_FILES["local_residual_sha256sums"]
    )
    checks["local_dynamic_adapter_sha256sums"] = check_sha256sums(
        CE25_PACKET_ROOTS["local_dynamic_adapter"], CE25_PACKET_FILES["local_dynamic_adapter_sha256sums"]
    )
    checks["local_dynamic_overrides_sha256sums"] = check_sha256sums(
        CE25_PACKET_ROOTS["local_dynamic_overrides"], CE25_PACKET_FILES["local_dynamic_overrides_sha256sums"]
    )
    checks["local_policy_frontier_sha256sums"] = check_sha256sums(
        CE25_PACKET_ROOTS["local_policy_frontier"], CE25_PACKET_FILES["local_policy_frontier_sha256sums"]
    )
    checks["local_fast_policy_grid_sha256sums"] = check_sha256sums(
        CE25_PACKET_ROOTS["local_fast_policy_grid"], CE25_PACKET_FILES["local_fast_policy_grid_sha256sums"]
    )
    checks["cd0_watch_full_artifact_sha256sums"] = check_sha256sums(
        CE25_PACKET_ROOTS["cd0_watch_full_artifact"], CE25_PACKET_FILES["cd0_watch_full_artifact_sha256sums"]
    )
    checks["cd0_throughput_queue_capital_sha256sums"] = check_sha256sums(
        CE25_PACKET_ROOTS["cd0_throughput_queue_capital"],
        CE25_PACKET_FILES["cd0_throughput_queue_capital_sha256sums"],
    )
    checks["cd0_l2_fillability_probe_sha256sums"] = check_sha256sums(
        CE25_PACKET_ROOTS["cd0_l2_fillability_probe"],
        CE25_PACKET_FILES["cd0_l2_fillability_probe_sha256sums"],
    )
    checks["cd0_full_l2_fillability_indexed_sha256sums"] = check_sha256sums(
        CE25_PACKET_ROOTS["cd0_full_l2_fillability_indexed"],
        CE25_PACKET_FILES["cd0_full_l2_fillability_indexed_sha256sums"],
    )
    checks["cd0_price_fill_model_revision_sha256sums"] = check_sha256sums(
        CE25_PACKET_ROOTS["cd0_price_fill_model_revision"],
        CE25_PACKET_FILES["cd0_price_fill_model_revision_sha256sums"],
    )
    checks["broad_cd5_price_fill_model_comparison_sha256sums"] = check_sha256sums(
        CE25_PACKET_ROOTS["broad_cd5_price_fill_model_comparison"],
        CE25_PACKET_FILES["broad_cd5_price_fill_model_comparison_sha256sums"],
    )
    checks["state_machine_executable_price_research_sha256sums"] = check_sha256sums(
        CE25_PACKET_ROOTS["state_machine_executable_price_research"],
        CE25_PACKET_FILES["state_machine_executable_price_research_sha256sums"],
    )
    checks["executable_price_adapter_grid_sha256sums"] = check_sha256sums(
        CE25_PACKET_ROOTS["executable_price_adapter_grid"],
        CE25_PACKET_FILES["executable_price_adapter_grid_sha256sums"],
    )
    checks["executable_taker_pair_edge_supply_sha256sums"] = check_sha256sums(
        CE25_PACKET_ROOTS["executable_taker_pair_edge_supply"],
        CE25_PACKET_FILES["executable_taker_pair_edge_supply_sha256sums"],
    )
    checks["maker_bid_edge_supply_sha256sums"] = check_sha256sums(
        CE25_PACKET_ROOTS["maker_bid_edge_supply"],
        CE25_PACKET_FILES["maker_bid_edge_supply_sha256sums"],
    )
    checks["maker_queue_shadow_design_sha256sums"] = check_sha256sums(
        CE25_PACKET_ROOTS["maker_queue_shadow_design"],
        CE25_PACKET_FILES["maker_queue_shadow_design_sha256sums"],
    )
    checks["maker_queue_public_shadow_staging_sha256sums"] = check_sha256sums(
        CE25_PACKET_ROOTS["maker_queue_public_shadow_staging"],
        CE25_PACKET_FILES["maker_queue_public_shadow_staging_sha256sums"],
    )
    for key, result in checks.items():
        if isinstance(result, dict) and "ok" in result and not result["ok"]:
            issues.extend(result.get("issues") or [f"{key} failed"])

    residual = load_json(CE25_PACKET_FILES["local_residual_packet"])
    status = str(residual.get("status") or "")
    if "STRICT_CACHE_REBUILD_REQUIRED" in status:
        issue(issues, f"residual packet still carries obsolete strict-cache blocker status: {status}")
    if "BACKTEST_V1_COMPACT_ARTIFACTS_OK" not in status:
        issue(issues, f"residual packet missing Backtest V1 compact-artifact status: {status}")
    legacy_policy = residual.get("legacy_strict_cache_policy") or {}
    if legacy_policy.get("backtest_v1_mainline_blocked_by_missing_old_cache") is not False:
        issue(issues, "legacy strict cache policy does not explicitly unblock Backtest V1 mainline")
    if legacy_policy.get("rebuild_required_for_current_mainline") is not False:
        issue(issues, "legacy strict cache policy still requires rebuild for current mainline")
    non_claims = residual.get("non_claims") or {}
    bad_claims = [key for key, value in non_claims.items() if value is not False]
    if bad_claims:
        issue(issues, f"residual packet non-claims are not false: {bad_claims}")
    if residual.get("dynamic_sizing_decision", {}).get("direct_public_profile_override_csv_allowed_for_local_replay") is not False:
        issue(issues, "direct public-profile override CSV is not fail-closed for local replay")

    frontier = load_json(CE25_PACKET_FILES["local_policy_frontier_packet"])
    frontier_status = str(frontier.get("status") or "")
    if "IMB250_PRIMARY_FAST_EVALUATOR_REQUIRED" not in frontier_status:
        issue(issues, f"policy frontier packet does not point to fast evaluator next step: {frontier_status}")
    if frontier.get("decision", {}).get("primary_complete_run") != "broad_offset300_imb250":
        issue(issues, "policy frontier primary complete run drifted")
    if frontier.get("decision", {}).get("abandoned_wrong_imb75_interpretation", "").find("must not be used") < 0:
        issue(issues, "policy frontier does not fail-close abandoned wrong imb75 run")
    if frontier.get("next_grid_after_fast_reproducer", {}).get("require_reproduce_primary_imb250_before_sweep") is not True:
        issue(issues, "policy frontier does not require fast reproducer before larger sweep")
    frontier_bad_claims = [key for key, value in (frontier.get("non_claims") or {}).items() if value is not False]
    if frontier_bad_claims:
        issue(issues, f"policy frontier non-claims are not false: {frontier_bad_claims}")

    fast_grid = load_json(CE25_PACKET_FILES["local_fast_policy_grid_packet"])
    fast_status = str(fast_grid.get("status") or "")
    if "LOCAL_FAST_POLICY_GRID_REVIEWED_CD0_WATCH" not in fast_status:
        issue(issues, f"fast policy grid packet status drifted: {fast_status}")
    if fast_grid.get("reproducer_check", {}).get("ok") is not True:
        issue(issues, "fast policy grid did not reproduce primary complete run")
    if fast_grid.get("decision", {}).get("current_primary_complete_run") != "broad_offset300_imb250":
        issue(issues, "fast policy grid primary complete run drifted")
    if fast_grid.get("decision", {}).get("watch_variant") != "imb250_cd00_rc050_age30":
        issue(issues, "fast policy grid watch variant drifted")
    if fast_grid.get("decision", {}).get("watch_variant_requires_full_artifact_rerun") is not True:
        issue(issues, "fast policy grid does not require full artifact rerun")
    if fast_grid.get("decision", {}).get("watch_variant_requires_throughput_queue_feasibility_audit") is not True:
        issue(issues, "fast policy grid does not require throughput/queue feasibility audit")
    fast_bad_claims = [key for key, value in (fast_grid.get("non_claims") or {}).items() if value is not False]
    if fast_bad_claims:
        issue(issues, f"fast policy grid non-claims are not false: {fast_bad_claims}")

    cd0 = load_json(CE25_PACKET_FILES["cd0_watch_full_artifact_packet"])
    cd0_status = str(cd0.get("status") or "")
    if "CD0_WATCH_FULL_ARTIFACT_REVIEWED_BULKCOPY_COMPLETE" not in cd0_status:
        issue(issues, f"cd0 full artifact packet status drifted: {cd0_status}")
    if cd0.get("decision", {}).get("cd0_watch_full_artifact_validated_for_local_research") is not True:
        issue(issues, "cd0 full artifact is not validated for local research")
    if cd0.get("decision", {}).get("oos_discussion_allowed") is not False:
        issue(issues, "cd0 packet allows OOS discussion before throughput/queue review")
    if cd0.get("fast_grid_reproduction", {}).get("ok") is not True:
        issue(issues, "cd0 full artifact does not reproduce fast-grid watch metrics")
    if cd0.get("full_artifact", {}).get("duckdb_counts", {}).get("ok") is not True:
        issue(issues, "cd0 full artifact DuckDB counts are not ok")
    full_tables = (cd0.get("full_artifact", {}).get("duckdb_counts", {}).get("tables") or {})
    expected_counts = {"actions": 388692, "candidate_registry": 388692, "summary_by_day": 15, "residual_lots": 6268}
    for table, expected in expected_counts.items():
        actual = int((full_tables.get(table) or {}).get("row_count") or -1)
        if actual != expected:
            issue(issues, f"cd0 full artifact table count mismatch for {table}: {actual} != {expected}")
    if cd0.get("partial_attempt", {}).get("complete_evidence_allowed") is not False:
        issue(issues, "cd0 partial attempt is not fail-closed")
    if cd0.get("partial_attempt", {}).get("duckdb_counts", {}).get("ok") is not False:
        issue(issues, "cd0 partial attempt DuckDB should remain non-ok")
    if cd0.get("runner_optimization", {}).get("bulkcopy_full_artifact_elapsed_s") in (None, ""):
        issue(issues, "cd0 packet does not bind bulkcopy full artifact elapsed time")
    cd0_bad_claims = [key for key, value in (cd0.get("non_claims") or {}).items() if value is not False]
    if cd0_bad_claims:
        issue(issues, f"cd0 full artifact non-claims are not false: {cd0_bad_claims}")

    throughput = load_json(CE25_PACKET_FILES["cd0_throughput_queue_capital_packet"])
    throughput_status = str(throughput.get("status") or "")
    if "CD0_THROUGHPUT_QUEUE_CAPITAL_REVIEWED_FILLABILITY_REQUIRED" not in throughput_status:
        issue(issues, f"cd0 throughput packet status drifted: {throughput_status}")
    if throughput.get("decision", {}).get("primary_blocker") != "throughput_queue_fillability":
        issue(issues, "cd0 throughput packet primary blocker drifted")
    if throughput.get("decision", {}).get("requires_l2_top_depth_or_fillability_replay") is not True:
        issue(issues, "cd0 throughput packet does not require L2/top-depth or fillability replay")
    if throughput.get("decision", {}).get("oos_discussion_allowed") is not False:
        issue(issues, "cd0 throughput packet allows OOS discussion")
    if int((throughput.get("throughput") or {}).get("actions") or 0) != 388692:
        issue(issues, "cd0 throughput action count drifted")
    if float((throughput.get("throughput") or {}).get("actions_per_minute", {}).get("max") or 0.0) != 160.0:
        issue(issues, "cd0 throughput max actions/minute drifted")
    if float((throughput.get("capital_path") or {}).get("global_open_cost_estimate", {}).get("max") or 0.0) <= 0.0:
        issue(issues, "cd0 throughput global open cost estimate missing")
    throughput_bad_claims = [
        key for key, value in (throughput.get("non_claims") or {}).items() if value is not False
    ]
    if throughput_bad_claims:
        issue(issues, f"cd0 throughput non-claims are not false: {throughput_bad_claims}")

    l2_probe = load_json(CE25_PACKET_FILES["cd0_l2_fillability_probe_packet"])
    l2_status = str(l2_probe.get("status") or "")
    if "CD0_L2_FILLABILITY_PROBE_REVIEWED_SAMPLE_PASS" not in l2_status:
        issue(issues, f"cd0 L2 fillability probe status drifted: {l2_status}")
    if l2_probe.get("decision", {}).get("sample_probe_pass") is not True:
        issue(issues, "cd0 L2 fillability sample probe is not pass")
    if l2_probe.get("decision", {}).get("full_indexed_join_required") is not True:
        issue(issues, "cd0 L2 fillability probe does not require full indexed join")
    if l2_probe.get("decision", {}).get("sample_can_support_full_oos_claim") is not False:
        issue(issues, "cd0 L2 sample is allowed to support full OOS claim")
    if int(l2_probe.get("sample_probe", {}).get("action_rows") or 0) != 999:
        issue(issues, "cd0 L2 sample action row count drifted")
    if int(l2_probe.get("sample_probe", {}).get("joined_rows") or 0) != 999:
        issue(issues, "cd0 L2 sample joined row count drifted")
    if int(l2_probe.get("sample_probe", {}).get("top5_size_ge_seed") or 0) != 999:
        issue(issues, "cd0 L2 sample top5 coverage drifted")
    if l2_probe.get("decision", {}).get("oos_discussion_allowed") is not False:
        issue(issues, "cd0 L2 probe allows OOS discussion")
    l2_bad_claims = [key for key, value in (l2_probe.get("non_claims") or {}).items() if value is not False]
    if l2_bad_claims:
        issue(issues, f"cd0 L2 fillability non-claims are not false: {l2_bad_claims}")

    full_l2 = load_json(CE25_PACKET_FILES["cd0_full_l2_fillability_indexed_packet"])
    full_l2_status = str(full_l2.get("status") or "")
    if "CD0_FULL_L2_FILLABILITY_INDEXED_JOIN_REVIEWED_PRICE_FILLABILITY_BLOCKED" not in full_l2_status:
        issue(issues, f"cd0 full L2 fillability status drifted: {full_l2_status}")
    if full_l2.get("decision", {}).get("full_indexed_join_complete") is not True:
        issue(issues, "cd0 full L2 indexed join is not complete")
    if full_l2.get("decision", {}).get("depth_size_coverage_pass") is not True:
        issue(issues, "cd0 full L2 depth size coverage did not pass")
    if full_l2.get("decision", {}).get("price_fillability_at_seed_price_pass") is not False:
        issue(issues, "cd0 full L2 price fillability at seed should remain blocked")
    if full_l2.get("decision", {}).get("primary_blocker") != "price_fillability_at_replay_seed_price":
        issue(issues, "cd0 full L2 primary blocker drifted")
    if full_l2.get("decision", {}).get("oos_discussion_allowed") is not False:
        issue(issues, "cd0 full L2 packet allows OOS discussion")
    if int(full_l2.get("full_indexed_join", {}).get("aggregate", {}).get("joined_rows") or 0) != 388692:
        issue(issues, "cd0 full L2 joined row count drifted")
    if float(full_l2.get("fillability_rates", {}).get("top5_size_ge_seed_rate") or 0.0) < 0.999:
        issue(issues, "cd0 full L2 top5 size coverage drifted below threshold")
    if float(full_l2.get("fillability_rates", {}).get("top5_full_at_or_better_rate") or 1.0) > 0.01:
        issue(issues, "cd0 full L2 seed-price fillability unexpectedly high; review semantics")
    full_l2_bad_claims = [
        key for key, value in (full_l2.get("non_claims") or {}).items() if value is not False
    ]
    if full_l2_bad_claims:
        issue(issues, f"cd0 full L2 non-claims are not false: {full_l2_bad_claims}")

    price_fill = load_json(CE25_PACKET_FILES["cd0_price_fill_model_revision_packet"])
    price_fill_status = str(price_fill.get("status") or "")
    price_fill_decision = price_fill.get("decision") or {}
    price_fill_models = {
        str(row.get("model_id")): row for row in (price_fill.get("model_results") or []) if row.get("model_id")
    }
    if "CD0_PRICE_FILL_MODEL_L2_EXECUTABLE_PRICE_NEGATIVE_PNL" not in price_fill_status:
        issue(issues, f"cd0 price/fill model revision status drifted: {price_fill_status}")
    if (price_fill.get("baseline_reproduction") or {}).get("matches_prior_cd0_full_artifact") is not True:
        issue(issues, "cd0 price/fill model revision does not reproduce prior baseline")
    if price_fill_decision.get("cd0_as_written_blocked") is not True:
        issue(issues, "cd0 price/fill model revision does not block cd0 as written")
    if price_fill_decision.get("replay_seed_px_optimism_detected") is not True:
        issue(issues, "cd0 price/fill model revision does not flag replay seed-price optimism")
    if price_fill_decision.get("primary_blocker") != "l2_executable_price_turns_pair_cost_above_one_and_net_pnl_negative":
        issue(issues, "cd0 price/fill model revision primary blocker drifted")
    if price_fill_decision.get("oos_discussion_allowed") is not False:
        issue(issues, "cd0 price/fill model revision allows OOS discussion")
    baseline_model = price_fill_models.get("baseline_replay_seed_px") or {}
    if float(baseline_model.get("net_pnl") or 0.0) <= 0.0:
        issue(issues, "cd0 price/fill baseline replay seed model is not positive")
    for model_id in (
        "l2_top5_vwap_all_available",
        "l2_top5_vwap_within_seed_plus_10c_only",
        "l2_ask1_px_when_ask1_size_ge_seed",
    ):
        model = price_fill_models.get(model_id)
        if not model:
            issue(issues, f"cd0 price/fill model missing {model_id}")
            continue
        if float(model.get("net_pnl") or 0.0) >= 0.0:
            issue(issues, f"cd0 price/fill executable model unexpectedly non-negative: {model_id}")
        if model_id != "l2_top5_vwap_within_seed_plus_5c_only" and float(model.get("weighted_pair_cost") or 0.0) <= 1.0:
            issue(issues, f"cd0 price/fill executable model pair cost not above one: {model_id}")
    price_fill_bad_claims = [
        key for key, value in (price_fill.get("non_claims") or {}).items() if value is not False
    ]
    if price_fill_bad_claims:
        issue(issues, f"cd0 price/fill non-claims are not false: {price_fill_bad_claims}")

    cd5_price = load_json(CE25_PACKET_FILES["broad_cd5_price_fill_model_comparison_packet"])
    cd5_price_status = str(cd5_price.get("status") or "")
    cd5_price_decision = cd5_price.get("decision") or {}
    cd5_models = {
        str(row.get("model_id")): row for row in (cd5_price.get("model_results") or []) if row.get("model_id")
    }
    if "BROAD_CD5_PRICE_FILL_MODEL_L2_EXECUTABLE_PRICE_NEGATIVE_PNL" not in cd5_price_status:
        issue(issues, f"broad cd5 price/fill status drifted: {cd5_price_status}")
    if cd5_price_decision.get("broad_cd5_as_safe_downgrade_blocked") is not True:
        issue(issues, "broad cd5 price/fill packet does not block cd5 as safe downgrade")
    if cd5_price_decision.get("seed_px_replay_positive") is not True:
        issue(issues, "broad cd5 price/fill packet does not preserve positive seed replay fact")
    if cd5_price_decision.get("l2_executable_models_negative") is not True:
        issue(issues, "broad cd5 price/fill packet does not require negative L2 executable models")
    if (
        cd5_price_decision.get("primary_blocker")
        != "state_machine_selection_and_pnl_are_not_aligned_to_executable_l2_prices"
    ):
        issue(issues, "broad cd5 price/fill primary blocker drifted")
    if cd5_price_decision.get("oos_discussion_allowed") is not False:
        issue(issues, "broad cd5 price/fill packet allows OOS discussion")
    cd5_seed = cd5_models.get("baseline_replay_seed_px") or {}
    if float(cd5_seed.get("net_pnl") or 0.0) <= 0.0:
        issue(issues, "broad cd5 seed-price replay is not positive")
    for model_id in (
        "l2_top5_vwap_all_available",
        "l2_top5_vwap_within_seed_plus_10c_only",
        "l2_ask1_px_when_ask1_size_ge_seed",
    ):
        model = cd5_models.get(model_id)
        if not model:
            issue(issues, f"broad cd5 price/fill model missing {model_id}")
            continue
        if float(model.get("net_pnl") or 0.0) >= 0.0:
            issue(issues, f"broad cd5 executable model unexpectedly non-negative: {model_id}")
        if float(model.get("weighted_pair_cost") or 0.0) <= 1.0:
            issue(issues, f"broad cd5 executable model pair cost not above one: {model_id}")
    cd5_price_bad_claims = [
        key for key, value in (cd5_price.get("non_claims") or {}).items() if value is not False
    ]
    if cd5_price_bad_claims:
        issue(issues, f"broad cd5 price/fill non-claims are not false: {cd5_price_bad_claims}")

    executable_price = load_json(CE25_PACKET_FILES["state_machine_executable_price_research_packet"])
    executable_price_status = str(executable_price.get("status") or "")
    executable_price_decision = executable_price.get("decision") or {}
    executable_price_contract = executable_price.get("implementation_contract") or {}
    executable_price_evidence = executable_price.get("blocking_evidence") or {}
    if "STATE_MACHINE_EXECUTABLE_PRICE_RESEARCH_PACKET_PREPARED" not in executable_price_status:
        issue(issues, f"state-machine executable-price packet status drifted: {executable_price_status}")
    if executable_price_decision.get("current_seed_px_family_blocked") is not True:
        issue(issues, "state-machine executable-price packet does not block current seed_px family")
    if executable_price_decision.get("cd0_blocked") is not True:
        issue(issues, "state-machine executable-price packet does not carry cd0 blocked fact")
    if executable_price_decision.get("broad_cd5_fallback_blocked") is not True:
        issue(issues, "state-machine executable-price packet does not block broad cd5 fallback")
    if (
        executable_price_decision.get("primary_blocker")
        != "selection_and_pnl_must_be_researched_under_executable_l2_prices"
    ):
        issue(issues, "state-machine executable-price primary blocker drifted")
    if executable_price_decision.get("oos_discussion_allowed") is not False:
        issue(issues, "state-machine executable-price packet allows OOS discussion")
    for required_flag in (
        "candidate_l2_join_required_before_selection",
        "execution_px_required_before_selection",
        "selection_and_pnl_price_source_must_match",
    ):
        if executable_price_contract.get(required_flag) is not True:
            issue(issues, f"state-machine executable-price contract flag not true: {required_flag}")
    fail_closed_if = executable_price_contract.get("fail_closed_if") or []
    for required_fail_closed in (
        "l2_missing_or_stale",
        "top5_size_less_than_target_qty",
        "execution_px_not_used_for_lot_price",
        "fee_not_recomputed_from_execution_px",
        "selection_price_and_pnl_price_diverge",
        "readiness_or_live_claim_true",
    ):
        if required_fail_closed not in fail_closed_if:
            issue(issues, f"state-machine executable-price missing fail-closed condition: {required_fail_closed}")
    if float((executable_price_evidence.get("cd0") or {}).get("seed_net_pnl") or 0.0) <= 0.0:
        issue(issues, "state-machine executable-price packet lost positive cd0 seed replay fact")
    if float((executable_price_evidence.get("cd0") or {}).get("top5_all_net_pnl") or 0.0) >= 0.0:
        issue(issues, "state-machine executable-price packet lost negative cd0 top5 executable fact")
    if float((executable_price_evidence.get("cd5") or {}).get("seed_net_pnl") or 0.0) <= 0.0:
        issue(issues, "state-machine executable-price packet lost positive cd5 seed replay fact")
    if float((executable_price_evidence.get("cd5") or {}).get("top5_all_net_pnl") or 0.0) >= 0.0:
        issue(issues, "state-machine executable-price packet lost negative cd5 top5 executable fact")
    executable_price_bad_claims = [
        key for key, value in (executable_price.get("non_claims") or {}).items() if value is not False
    ]
    if executable_price_bad_claims:
        issue(issues, f"state-machine executable-price non-claims are not false: {executable_price_bad_claims}")

    adapter_grid = load_json(CE25_PACKET_FILES["executable_price_adapter_grid_packet"])
    adapter_grid_status = str(adapter_grid.get("status") or "")
    adapter_grid_decision = adapter_grid.get("decision") or {}
    adapter_grid_meta = adapter_grid.get("grid") or {}
    adapter_grid_best = adapter_grid.get("best_by_net_pnl") or {}
    if "EXECUTABLE_PRICE_ADAPTER_GRID_POSITIVE_ONLY_LOW_PAIR_SHARE_HIGH_RESIDUAL" not in adapter_grid_status:
        issue(issues, f"executable-price adapter grid status drifted: {adapter_grid_status}")
    if int(adapter_grid_meta.get("variant_count") or 0) != 12:
        issue(issues, f"executable-price adapter grid variant count drifted: {adapter_grid_meta.get('variant_count')}")
    if int(adapter_grid_meta.get("positive_variant_count") or 0) <= 0:
        issue(issues, "executable-price adapter grid lost positive low-quality variant fact")
    if int(adapter_grid_meta.get("quality_positive_variant_count", -1)) != 0:
        issue(issues, "executable-price adapter grid quality-positive count should remain zero")
    if adapter_grid_decision.get("positive_watch_variant_exists") is not True:
        issue(issues, "executable-price adapter grid did not preserve positive watch variant fact")
    if adapter_grid_decision.get("quality_positive_watch_variant_exists") is not False:
        issue(issues, "executable-price adapter grid quality watch gate is not fail-closed")
    if adapter_grid_decision.get("current_seed_px_family_unblocked") is not False:
        issue(issues, "executable-price adapter grid unblocked seed_px family")
    if adapter_grid_decision.get("oos_discussion_allowed") is not False:
        issue(issues, "executable-price adapter grid allows OOS discussion")
    if adapter_grid_decision.get("primary_blocker") != "positive_variants_are_low_pair_share_high_residual":
        issue(issues, "executable-price adapter grid primary blocker drifted")
    if float(adapter_grid_best.get("net_pnl") or 0.0) <= 0.0:
        issue(issues, "executable-price adapter grid best variant no longer positive; review status")
    if float(adapter_grid_best.get("pair_share_rate") or 1.0) >= 0.60:
        issue(issues, "executable-price adapter grid best pair_share unexpectedly passes quality gate")
    if float(adapter_grid_best.get("residual_cost_rate") or 0.0) <= 0.20:
        issue(issues, "executable-price adapter grid best residual unexpectedly passes quality gate")
    adapter_grid_bad_claims = [
        key for key, value in (adapter_grid.get("non_claims") or {}).items() if value is not False
    ]
    if adapter_grid_bad_claims:
        issue(issues, f"executable-price adapter grid non-claims are not false: {adapter_grid_bad_claims}")

    taker_supply = load_json(CE25_PACKET_FILES["executable_taker_pair_edge_supply_packet"])
    taker_supply_status = str(taker_supply.get("status") or "")
    taker_supply_ceiling = taker_supply.get("supply_ceiling") or {}
    taker_supply_decision = taker_supply.get("decision") or {}
    if "EXECUTABLE_TAKER_PAIR_EDGE_SUPPLY_CEILING_HIGH_PARTICIPATION_IMPOSSIBLE" not in taker_supply_status:
        issue(issues, f"executable taker pair-edge supply status drifted: {taker_supply_status}")
    if int(taker_supply_ceiling.get("scanned_market_count") or 0) != 4308:
        issue(issues, "executable taker pair-edge scanned market count drifted")
    if int(taker_supply_ceiling.get("eligible_market_count") or 0) != 4308:
        issue(issues, "executable taker pair-edge eligible market count drifted")
    if int(taker_supply_ceiling.get("positive_net_edge_markets") or 0) > 100:
        issue(issues, "executable taker pair-edge positive market count unexpectedly high; review status")
    if float(taker_supply_ceiling.get("positive_net_edge_market_share") or 1.0) >= 0.80:
        issue(issues, "executable taker pair-edge market share unexpectedly passes high-participation floor")
    if taker_supply_ceiling.get("high_participation_possible_under_taker_pair_edge") is not False:
        issue(issues, "executable taker pair-edge supply packet does not block high participation")
    if taker_supply_decision.get("high_participation_taker_backbone_blocked") is not True:
        issue(issues, "executable taker pair-edge decision does not block high-participation taker backbone")
    if taker_supply_decision.get("cooldown_size_merge_tuning_can_fix") is not False:
        issue(issues, "executable taker pair-edge decision allows cooldown/size/merge tuning as fix")
    if taker_supply_decision.get("requires_maker_or_queue_edge_or_new_signal_family") is not True:
        issue(issues, "executable taker pair-edge decision does not require maker/queue edge or new family")
    if taker_supply_decision.get("oos_discussion_allowed") is not False:
        issue(issues, "executable taker pair-edge packet allows OOS discussion")
    taker_supply_bad_claims = [
        key for key, value in (taker_supply.get("non_claims") or {}).items() if value is not False
    ]
    if taker_supply_bad_claims:
        issue(issues, f"executable taker pair-edge non-claims are not false: {taker_supply_bad_claims}")

    maker_supply = load_json(CE25_PACKET_FILES["maker_bid_edge_supply_packet"])
    maker_supply_status = str(maker_supply.get("status") or "")
    maker_supply_ceiling = maker_supply.get("supply_ceiling") or {}
    maker_supply_decision = maker_supply.get("decision") or {}
    maker_supply_method = maker_supply.get("method") or {}
    if "MAKER_BID_EDGE_SUPPLY_REVIEWED_QUEUE_TRUTH_REQUIRED" not in maker_supply_status:
        issue(issues, f"maker bid edge supply status drifted: {maker_supply_status}")
    if int(maker_supply_ceiling.get("scanned_market_count") or 0) != 4308:
        issue(issues, "maker bid edge supply scanned market count drifted")
    if int(maker_supply_ceiling.get("eligible_market_count") or 0) != 4308:
        issue(issues, "maker bid edge supply eligible market count drifted")
    if float(maker_supply_ceiling.get("positive_edge_market_share") or 0.0) < 0.90:
        issue(issues, "maker bid edge positive market share unexpectedly low")
    if float(maker_supply_ceiling.get("positive_edge_touch_market_share") or 0.0) < 0.90:
        issue(issues, "maker bid edge public-touch market share unexpectedly low")
    if maker_supply_decision.get("maker_bid_edge_supply_exists") is not True:
        issue(issues, "maker bid edge supply packet does not preserve supply existence")
    if maker_supply_decision.get("maker_bid_edge_supply_can_support_research") is not True:
        issue(issues, "maker bid edge supply packet blocks research unexpectedly")
    if maker_supply_decision.get("taker_backbone_replacement_without_queue_truth_allowed") is not False:
        issue(issues, "maker bid edge supply packet allows replacing taker backbone without queue truth")
    if maker_supply_decision.get("requires_private_or_shadow_maker_fill_evidence") is not True:
        issue(issues, "maker bid edge supply packet does not require private/shadow maker fill evidence")
    if maker_supply_decision.get("primary_blocker") != "queue_priority_and_private_fill_truth_missing":
        issue(issues, "maker bid edge supply primary blocker drifted")
    if maker_supply_decision.get("oos_discussion_allowed") is not False:
        issue(issues, "maker bid edge supply packet allows OOS discussion")
    if "not proof" not in str(maker_supply_method.get("private_truth_boundary") or ""):
        issue(issues, "maker bid edge private-truth boundary missing")
    maker_supply_bad_claims = [
        key for key, value in (maker_supply.get("non_claims") or {}).items() if value is not False
    ]
    if maker_supply_bad_claims:
        issue(issues, f"maker bid edge non-claims are not false: {maker_supply_bad_claims}")

    maker_queue = load_json(CE25_PACKET_FILES["maker_queue_shadow_design_packet"])
    maker_queue_status = str(maker_queue.get("status") or "")
    maker_queue_decision = maker_queue.get("decision") or {}
    maker_queue_objective = maker_queue.get("design_objective") or {}
    maker_queue_lanes = maker_queue.get("evidence_lanes") or {}
    maker_queue_public_lane = maker_queue_lanes.get("public_no_order_shadow") or {}
    maker_queue_private_lane = maker_queue_lanes.get("private_order_telemetry") or {}
    maker_queue_sources = maker_queue.get("source_bindings") or {}
    maker_queue_current = maker_queue.get("current_research_state") or {}
    maker_queue_taker = maker_queue_current.get("taker_backbone_state") or {}
    maker_queue_maker = maker_queue_current.get("maker_bid_supply_state") or {}
    maker_queue_fields = {str(item.get("field") or "") for item in maker_queue.get("required_evidence_fields") or []}
    maker_queue_models = {str(item.get("model_id") or "") for item in maker_queue.get("queue_models") or []}
    maker_queue_gates = {str(item.get("gate_id") or "") for item in maker_queue.get("fail_closed_gates") or []}
    if "MAKER_QUEUE_SHADOW_DESIGN_PACKET_PREPARED_REVIEW_ONLY_QUEUE_TRUTH_REQUIRED" not in maker_queue_status:
        issue(issues, f"maker queue shadow design status drifted: {maker_queue_status}")
    bound_maker_path = str((maker_queue_sources.get("maker_bid_edge_supply_packet") or {}).get("path") or "")
    if bound_maker_path != str(CE25_PACKET_FILES["maker_bid_edge_supply_packet"]):
        issue(issues, "maker queue shadow design does not bind maker bid edge supply packet path")
    bound_maker_sha = str((maker_queue_sources.get("maker_bid_edge_supply_packet") or {}).get("sha256") or "")
    if bound_maker_sha != sha256_file(CE25_PACKET_FILES["maker_bid_edge_supply_packet"]):
        issue(issues, "maker queue shadow design maker bid edge supply checksum drifted")
    bound_taker_path = str((maker_queue_sources.get("executable_taker_pair_edge_supply_packet") or {}).get("path") or "")
    if bound_taker_path != str(CE25_PACKET_FILES["executable_taker_pair_edge_supply_packet"]):
        issue(issues, "maker queue shadow design does not bind taker supply packet path")
    if maker_queue_objective.get("public_shadow_can_prove_private_fill") is not False:
        issue(issues, "maker queue design allows public shadow to prove private fill")
    if maker_queue_objective.get("private_order_lane_authorized") is not False:
        issue(issues, "maker queue design authorizes private order lane")
    if maker_queue_objective.get("orders_authorized") is not False:
        issue(issues, "maker queue design authorizes orders")
    if maker_queue_objective.get("oos_authorized") is not False:
        issue(issues, "maker queue design authorizes OOS")
    if maker_queue_objective.get("ws_authorized") is not False:
        issue(issues, "maker queue design authorizes WS")
    if maker_queue_public_lane.get("allowed_in_this_packet") is not False:
        issue(issues, "maker queue design allows public shadow execution inside design packet")
    if maker_queue_public_lane.get("may_be_prepared_by_next_review_packet") is not True:
        issue(issues, "maker queue design does not identify next public-shadow review packet")
    if maker_queue_private_lane.get("allowed_in_this_packet") is not False:
        issue(issues, "maker queue design allows private order telemetry inside design packet")
    if maker_queue_private_lane.get("requires_separate_exact_approval") is not True:
        issue(issues, "maker queue design does not require separate exact approval for private telemetry")
    required_queue_fields = {
        "candidate_row_id",
        "condition_id",
        "side",
        "quote_ts_ms",
        "side_bid_px",
        "opposite_bid_px",
        "bid_visible_depth_at_or_ahead",
        "public_sell_touch_ts_ms",
        "public_sell_trade_price",
        "public_sell_trade_size",
        "touch_after_quote_ms",
        "l1_age_ms",
        "l2_age_ms",
        "align_lag_ms",
        "hypothetical_fill_qty_after_visible_depth",
        "own_order_id",
        "own_order_ack_ts_ms",
        "own_order_fill_qty",
        "own_order_cancel_ts_ms",
    }
    missing_queue_fields = sorted(required_queue_fields - maker_queue_fields)
    if missing_queue_fields:
        issue(issues, f"maker queue design missing required evidence fields: {missing_queue_fields}")
    required_queue_models = {
        "TOUCH_ONLY_NOT_FILL_PROOF",
        "SIZE_AFTER_VISIBLE_DEPTH_CONSERVATIVE",
        "TRADE_SIZE_MINUS_VISIBLE_BID_DEPTH",
        "OWN_ORDER_TELEMETRY_REQUIRED",
    }
    missing_queue_models = sorted(required_queue_models - maker_queue_models)
    if missing_queue_models:
        issue(issues, f"maker queue design missing queue models: {missing_queue_models}")
    required_queue_gates = {
        "NO_ORDER_AUTHORIZATION",
        "PUBLIC_TOUCH_NOT_PRIVATE_FILL",
        "QUOTE_STALENESS",
        "QUEUE_AHEAD_MISSING",
        "TRADE_CAUSALITY",
        "PRIVATE_TELEMETRY_ABSENT",
        "READINESS_FLAGS",
    }
    missing_queue_gates = sorted(required_queue_gates - maker_queue_gates)
    if missing_queue_gates:
        issue(issues, f"maker queue design missing fail-closed gates: {missing_queue_gates}")
    if maker_queue_decision.get("maker_queue_shadow_design_prepared") is not True:
        issue(issues, "maker queue design packet does not mark design prepared")
    if maker_queue_decision.get("taker_high_participation_backbone_remains_blocked") is not True:
        issue(issues, "maker queue design does not preserve taker backbone block")
    if maker_queue_decision.get("public_shadow_can_prove_private_fill") is not False:
        issue(issues, "maker queue decision allows public shadow private-fill proof")
    if maker_queue_decision.get("private_order_lane_authorized") is not False:
        issue(issues, "maker queue decision authorizes private order lane")
    if maker_queue_decision.get("orders_authorized") is not False:
        issue(issues, "maker queue decision authorizes orders")
    if maker_queue_decision.get("oos_discussion_allowed") is not False:
        issue(issues, "maker queue decision allows OOS discussion")
    if maker_queue_decision.get("primary_blocker") != "queue_priority_and_private_fill_truth_missing":
        issue(issues, "maker queue primary blocker drifted")
    if maker_queue_decision.get("next_step") != "prepare_public_no_order_maker_queue_shadow_staging_packet_or_new_strategy_family_packet":
        issue(issues, "maker queue next step drifted")
    if float(maker_queue_taker.get("positive_net_edge_market_share") or 1.0) >= 0.80:
        issue(issues, "maker queue design lost taker market-share block")
    if float(maker_queue_maker.get("positive_edge_market_share") or 0.0) < 0.90:
        issue(issues, "maker queue design lost maker market-share supply fact")
    maker_queue_bad_claims = [
        key for key, value in (maker_queue.get("non_claims") or {}).items() if value is not False
    ]
    if maker_queue_bad_claims:
        issue(issues, f"maker queue design non-claims are not false: {maker_queue_bad_claims}")

    maker_queue_staging = load_json(CE25_PACKET_FILES["maker_queue_public_shadow_staging_packet"])
    maker_queue_staging_status = str(maker_queue_staging.get("status") or "")
    maker_queue_staging_method = maker_queue_staging.get("method") or {}
    maker_queue_staging_aggregate = maker_queue_staging.get("aggregate") or {}
    maker_queue_staging_decision = maker_queue_staging.get("decision") or {}
    maker_queue_staging_sources = maker_queue_staging.get("source_bindings") or {}
    if "MAKER_QUEUE_PUBLIC_SHADOW_STAGING_REVIEWED_PUBLIC_PROXY_ONLY" not in maker_queue_staging_status:
        issue(issues, f"maker queue public shadow staging status drifted: {maker_queue_staging_status}")
    bound_design_path = str(
        (maker_queue_staging_sources.get("maker_queue_shadow_design_packet") or {}).get("path") or ""
    )
    if bound_design_path != str(CE25_PACKET_FILES["maker_queue_shadow_design_packet"]):
        issue(issues, "maker queue public shadow staging does not bind design packet path")
    bound_design_sha = str(
        (maker_queue_staging_sources.get("maker_queue_shadow_design_packet") or {}).get("sha256") or ""
    )
    if bound_design_sha != sha256_file(CE25_PACKET_FILES["maker_queue_shadow_design_packet"]):
        issue(issues, "maker queue public shadow staging design checksum drifted")
    if maker_queue_staging_method.get("public_proxy_only") is not True:
        issue(issues, "maker queue public shadow staging is not marked public-proxy-only")
    if maker_queue_staging_method.get("private_truth_ready") is not False:
        issue(issues, "maker queue public shadow staging marks private truth ready")
    if "not proof" not in str(maker_queue_staging_method.get("private_truth_boundary") or ""):
        issue(issues, "maker queue public shadow staging private-truth boundary missing")
    if int(maker_queue_staging_aggregate.get("scanned_markets") or 0) != 4308:
        issue(issues, "maker queue public shadow staging scanned market count drifted")
    if int(maker_queue_staging_aggregate.get("eligible_touch_markets") or 0) != 4308:
        issue(issues, "maker queue public shadow staging eligible touch market count drifted")
    if float(maker_queue_staging_aggregate.get("queue_fill_market_share") or 0.0) < 0.80:
        issue(issues, "maker queue public shadow staging queue-fill market share unexpectedly low")
    if float(maker_queue_staging_aggregate.get("positive_edge_queue_fill_market_share") or 0.0) < 0.80:
        issue(issues, "maker queue public shadow staging positive-edge queue market share unexpectedly low")
    if int(maker_queue_staging_aggregate.get("stale_reject_count") or 0) <= 0:
        issue(issues, "maker queue public shadow staging stale reject count missing")
    if maker_queue_staging_decision.get("public_no_order_queue_shadow_staging_prepared") is not True:
        issue(issues, "maker queue public shadow staging not marked prepared")
    if maker_queue_staging_decision.get("queue_proxy_supports_next_review") is not True:
        issue(issues, "maker queue public shadow staging does not support next review")
    if maker_queue_staging_decision.get("private_truth_unblocked") is not False:
        issue(issues, "maker queue public shadow staging unblocks private truth")
    if maker_queue_staging_decision.get("maker_fill_proven") is not False:
        issue(issues, "maker queue public shadow staging proves maker fill")
    if maker_queue_staging_decision.get("queue_priority_proven") is not False:
        issue(issues, "maker queue public shadow staging proves queue priority")
    if maker_queue_staging_decision.get("orders_authorized") is not False:
        issue(issues, "maker queue public shadow staging authorizes orders")
    if maker_queue_staging_decision.get("oos_discussion_allowed") is not False:
        issue(issues, "maker queue public shadow staging allows OOS discussion")
    if maker_queue_staging_decision.get("primary_blocker") != "own_order_queue_priority_and_fill_telemetry_missing":
        issue(issues, "maker queue public shadow staging primary blocker drifted")
    maker_queue_staging_bad_claims = [
        key for key, value in (maker_queue_staging.get("non_claims") or {}).items() if value is not False
    ]
    if maker_queue_staging_bad_claims:
        issue(issues, f"maker queue public shadow staging non-claims are not false: {maker_queue_staging_bad_claims}")

    checks["local_residual_semantics"] = {
        "status": status,
        "legacy_strict_cache_status": legacy_policy.get("status"),
        "best_local_smoke_by_net_pnl": residual.get("best_local_smoke_by_net_pnl"),
        "dynamic_direct_override_allowed": residual.get("dynamic_sizing_decision", {}).get(
            "direct_public_profile_override_csv_allowed_for_local_replay"
        ),
    }
    checks["local_policy_frontier_semantics"] = {
        "status": frontier_status,
        "primary_complete_run": frontier.get("decision", {}).get("primary_complete_run"),
        "next_engineering_step": frontier.get("decision", {}).get("next_engineering_step"),
    }
    checks["local_fast_policy_grid_semantics"] = {
        "status": fast_status,
        "variant_count": fast_grid.get("evaluator", {}).get("variant_count"),
        "reproducer_ok": fast_grid.get("reproducer_check", {}).get("ok"),
        "current_primary_complete_run": fast_grid.get("decision", {}).get("current_primary_complete_run"),
        "watch_variant": fast_grid.get("decision", {}).get("watch_variant"),
        "watch_variant_family": fast_grid.get("decision", {}).get("watch_variant_family"),
        "watch_variant_requires_throughput_queue_feasibility_audit": fast_grid.get("decision", {}).get(
            "watch_variant_requires_throughput_queue_feasibility_audit"
        ),
    }
    checks["cd0_watch_full_artifact_semantics"] = {
        "status": cd0_status,
        "validated_for_local_research": cd0.get("decision", {}).get(
            "cd0_watch_full_artifact_validated_for_local_research"
        ),
        "new_watch_variant": cd0.get("decision", {}).get("new_watch_variant"),
        "next_required_step": cd0.get("decision", {}).get("next_required_step"),
        "net_pnl": cd0.get("full_artifact", {}).get("core_metrics", {}).get("net_pnl"),
        "net_roi": cd0.get("full_artifact", {}).get("core_metrics", {}).get("net_roi"),
        "residual_cost_rate": cd0.get("full_artifact", {}).get("core_metrics", {}).get("residual_cost_rate"),
        "bulkcopy_full_artifact_elapsed_s": cd0.get("runner_optimization", {}).get(
            "bulkcopy_full_artifact_elapsed_s"
        ),
    }
    checks["cd0_throughput_queue_capital_semantics"] = {
        "status": throughput_status,
        "primary_blocker": throughput.get("decision", {}).get("primary_blocker"),
        "next_packet": throughput.get("decision", {}).get("next_packet"),
        "actions_per_minute_max": throughput.get("throughput", {}).get("actions_per_minute", {}).get("max"),
        "actions_per_second_max": throughput.get("throughput", {}).get("actions_per_second", {}).get("max"),
        "global_open_cost_max": throughput.get("capital_path", {})
        .get("global_open_cost_estimate", {})
        .get("max"),
        "gross_buy_turnover_vs_300_usdc": throughput.get("capital_path", {}).get(
            "gross_buy_turnover_vs_300_usdc"
        ),
    }
    checks["cd0_l2_fillability_probe_semantics"] = {
        "status": l2_status,
        "sample_probe_pass": l2_probe.get("decision", {}).get("sample_probe_pass"),
        "action_rows": l2_probe.get("sample_probe", {}).get("action_rows"),
        "joined_rows": l2_probe.get("sample_probe", {}).get("joined_rows"),
        "ask1_size_ge_seed": l2_probe.get("sample_probe", {}).get("ask1_size_ge_seed"),
        "top5_size_ge_seed": l2_probe.get("sample_probe", {}).get("top5_size_ge_seed"),
        "raw_l2_age_ms_p99": l2_probe.get("sample_probe", {}).get("raw_l2_age_ms_p99"),
        "align_lag_ms_p99": l2_probe.get("sample_probe", {}).get("align_lag_ms_p99"),
        "next_packet": l2_probe.get("decision", {}).get("next_packet"),
    }
    checks["cd0_full_l2_fillability_indexed_semantics"] = {
        "status": full_l2_status,
        "joined_rows": full_l2.get("full_indexed_join", {}).get("aggregate", {}).get("joined_rows"),
        "top5_size_ge_seed_rate": full_l2.get("fillability_rates", {}).get("top5_size_ge_seed_rate"),
        "top5_full_at_or_better_rate": full_l2.get("fillability_rates", {}).get(
            "top5_full_at_or_better_rate"
        ),
        "top5_full_within_10c_rate": full_l2.get("fillability_rates", {}).get("top5_full_within_10c_rate"),
        "primary_blocker": full_l2.get("decision", {}).get("primary_blocker"),
        "next_packet": full_l2.get("decision", {}).get("next_packet"),
    }
    checks["cd0_price_fill_model_revision_semantics"] = {
        "status": price_fill_status,
        "cd0_as_written_blocked": price_fill_decision.get("cd0_as_written_blocked"),
        "primary_blocker": price_fill_decision.get("primary_blocker"),
        "baseline_net_pnl": baseline_model.get("net_pnl"),
        "l2_top5_all_net_pnl": (price_fill_models.get("l2_top5_vwap_all_available") or {}).get("net_pnl"),
        "l2_top5_within_10c_net_pnl": (
            price_fill_models.get("l2_top5_vwap_within_seed_plus_10c_only") or {}
        ).get("net_pnl"),
        "l2_ask1_net_pnl": (price_fill_models.get("l2_ask1_px_when_ask1_size_ge_seed") or {}).get(
            "net_pnl"
        ),
        "next_packet": price_fill_decision.get("next_packet"),
    }
    checks["broad_cd5_price_fill_model_comparison_semantics"] = {
        "status": cd5_price_status,
        "broad_cd5_as_safe_downgrade_blocked": cd5_price_decision.get(
            "broad_cd5_as_safe_downgrade_blocked"
        ),
        "primary_blocker": cd5_price_decision.get("primary_blocker"),
        "seed_net_pnl": cd5_seed.get("net_pnl"),
        "l2_top5_all_net_pnl": (cd5_models.get("l2_top5_vwap_all_available") or {}).get("net_pnl"),
        "l2_top5_within_10c_net_pnl": (
            cd5_models.get("l2_top5_vwap_within_seed_plus_10c_only") or {}
        ).get("net_pnl"),
        "l2_ask1_net_pnl": (cd5_models.get("l2_ask1_px_when_ask1_size_ge_seed") or {}).get(
            "net_pnl"
        ),
        "next_packet": cd5_price_decision.get("next_packet"),
    }
    checks["state_machine_executable_price_research_semantics"] = {
        "status": executable_price_status,
        "current_seed_px_family_blocked": executable_price_decision.get("current_seed_px_family_blocked"),
        "primary_blocker": executable_price_decision.get("primary_blocker"),
        "candidate_l2_join_required_before_selection": executable_price_contract.get(
            "candidate_l2_join_required_before_selection"
        ),
        "selection_and_pnl_price_source_must_match": executable_price_contract.get(
            "selection_and_pnl_price_source_must_match"
        ),
        "cd0_top5_all_net_pnl": (executable_price_evidence.get("cd0") or {}).get("top5_all_net_pnl"),
        "cd5_top5_all_net_pnl": (executable_price_evidence.get("cd5") or {}).get("top5_all_net_pnl"),
        "next_implementation": executable_price_decision.get("next_implementation"),
    }
    checks["executable_price_adapter_grid_semantics"] = {
        "status": adapter_grid_status,
        "variant_count": adapter_grid_meta.get("variant_count"),
        "positive_variant_count": adapter_grid_meta.get("positive_variant_count"),
        "quality_positive_variant_count": adapter_grid_meta.get("quality_positive_variant_count"),
        "best_variant_id": adapter_grid_best.get("variant_id"),
        "best_net_pnl": adapter_grid_best.get("net_pnl"),
        "best_pair_share_rate": adapter_grid_best.get("pair_share_rate"),
        "best_residual_cost_rate": adapter_grid_best.get("residual_cost_rate"),
        "primary_blocker": adapter_grid_decision.get("primary_blocker"),
        "next_step": adapter_grid_decision.get("next_step"),
    }
    checks["executable_taker_pair_edge_supply_semantics"] = {
        "status": taker_supply_status,
        "scanned_market_count": taker_supply_ceiling.get("scanned_market_count"),
        "eligible_market_count": taker_supply_ceiling.get("eligible_market_count"),
        "positive_net_edge_rows": taker_supply_ceiling.get("positive_net_edge_rows"),
        "positive_net_edge_markets": taker_supply_ceiling.get("positive_net_edge_markets"),
        "positive_net_edge_market_share": taker_supply_ceiling.get("positive_net_edge_market_share"),
        "raw_pair_cost_p50": taker_supply_ceiling.get("raw_pair_cost_p50"),
        "net_pair_edge_p50": taker_supply_ceiling.get("net_pair_edge_p50"),
        "primary_blocker": taker_supply_decision.get("primary_blocker"),
        "next_step": taker_supply_decision.get("next_step"),
    }
    checks["maker_bid_edge_supply_semantics"] = {
        "status": maker_supply_status,
        "scanned_market_count": maker_supply_ceiling.get("scanned_market_count"),
        "eligible_market_count": maker_supply_ceiling.get("eligible_market_count"),
        "positive_edge_rows": maker_supply_ceiling.get("positive_edge_rows"),
        "positive_edge_markets": maker_supply_ceiling.get("positive_edge_markets"),
        "positive_edge_market_share": maker_supply_ceiling.get("positive_edge_market_share"),
        "positive_edge_touch_markets": maker_supply_ceiling.get("positive_edge_touch_markets"),
        "positive_edge_touch_market_share": maker_supply_ceiling.get("positive_edge_touch_market_share"),
        "maker_bid_pair_cost_p50": maker_supply_ceiling.get("maker_bid_pair_cost_p50"),
        "maker_net_pair_edge_p50": maker_supply_ceiling.get("maker_net_pair_edge_p50"),
        "primary_blocker": maker_supply_decision.get("primary_blocker"),
        "next_step": maker_supply_decision.get("next_step"),
    }
    checks["maker_queue_shadow_design_semantics"] = {
        "status": maker_queue_status,
        "public_shadow_can_prove_private_fill": maker_queue_objective.get(
            "public_shadow_can_prove_private_fill"
        ),
        "private_order_lane_authorized": maker_queue_objective.get("private_order_lane_authorized"),
        "orders_authorized": maker_queue_objective.get("orders_authorized"),
        "oos_authorized": maker_queue_objective.get("oos_authorized"),
        "ws_authorized": maker_queue_objective.get("ws_authorized"),
        "public_shadow_allowed_in_this_packet": maker_queue_public_lane.get("allowed_in_this_packet"),
        "private_order_lane_allowed_in_this_packet": maker_queue_private_lane.get("allowed_in_this_packet"),
        "required_evidence_field_count": len(maker_queue_fields),
        "queue_model_count": len(maker_queue_models),
        "fail_closed_gate_count": len(maker_queue_gates),
        "taker_positive_net_edge_market_share": maker_queue_taker.get("positive_net_edge_market_share"),
        "maker_positive_edge_market_share": maker_queue_maker.get("positive_edge_market_share"),
        "primary_blocker": maker_queue_decision.get("primary_blocker"),
        "next_step": maker_queue_decision.get("next_step"),
    }
    checks["maker_queue_public_shadow_staging_semantics"] = {
        "status": maker_queue_staging_status,
        "public_proxy_only": maker_queue_staging_method.get("public_proxy_only"),
        "private_truth_ready": maker_queue_staging_method.get("private_truth_ready"),
        "scanned_markets": maker_queue_staging_aggregate.get("scanned_markets"),
        "quote_fresh_rows": maker_queue_staging_aggregate.get("quote_fresh_rows"),
        "eligible_touch_markets": maker_queue_staging_aggregate.get("eligible_touch_markets"),
        "queue_fill_rows": maker_queue_staging_aggregate.get("queue_fill_rows"),
        "queue_fill_markets": maker_queue_staging_aggregate.get("queue_fill_markets"),
        "queue_fill_market_share": maker_queue_staging_aggregate.get("queue_fill_market_share"),
        "positive_edge_queue_fill_rows": maker_queue_staging_aggregate.get(
            "positive_edge_queue_fill_rows"
        ),
        "positive_edge_queue_fill_markets": maker_queue_staging_aggregate.get(
            "positive_edge_queue_fill_markets"
        ),
        "positive_edge_queue_fill_market_share": maker_queue_staging_aggregate.get(
            "positive_edge_queue_fill_market_share"
        ),
        "touch_after_quote_ms_p99": maker_queue_staging_aggregate.get("touch_after_quote_ms_p99"),
        "align_lag_ms_p99": maker_queue_staging_aggregate.get("align_lag_ms_p99"),
        "stale_reject_count": maker_queue_staging_aggregate.get("stale_reject_count"),
        "primary_blocker": maker_queue_staging_decision.get("primary_blocker"),
        "next_step": maker_queue_staging_decision.get("next_step"),
    }
    return {"ok": not issues, "issues": issues, "checks": checks}


def main() -> int:
    backtest = check_backtest_v1_artifacts()
    ce25 = check_ce25_packets()
    issues = [*(backtest.get("issues") or []), *(ce25.get("issues") or [])]
    ok = not issues
    payload = {
        "ok": ok,
        "decision": "DONT_NOTIFY" if ok else "NOTIFY",
        "status": STATUS_OK if ok else STATUS_BLOCKED,
        "backtest_v1": backtest,
        "ce25_packets": ce25,
        "issue_count": len(issues),
        "issues": issues,
        "legacy_strict_cache_policy": {
            "old_cache_root": str(BT_ROOT / "backtest_cache/taker_buy_signal_core_v2_strict_l1"),
            "backtest_v1_mainline_requires_old_strict_cache": False,
            "current_research_requires_old_strict_cache": False,
            "rebuild_old_strict_cache_only_for_legacy_repro": True,
            "state_machine_outputs_can_rebuild_strict_cache": False,
        },
        "highest_allowed_status": "local research/review-only, not OOS-ready",
        "non_claims": {
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "deployable": False,
            "oos_authorized": False,
            "orders_authorized": False,
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
