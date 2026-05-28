#!/usr/bin/env python3
"""Validate the MacBook-local multiasset backtest V1 compact install.

This gate validates published compact artifacts under POLY_BT_ROOT. It does not
scan raw archives, replay cold archives, collectors, or remote hosts.
"""

from __future__ import annotations

import argparse
import datetime as dt
import csv
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any


DEFAULT_DATA_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
EXPECTED_ASSETS = ("BNB", "BTC", "DOGE", "ETH", "HYPE", "SOL", "XRP")
EXPECTED_DAYS = tuple(
    [f"2026-05-{day:02d}" for day in range(2, 14)]
    + ["2026-05-16", "2026-05-17", "2026-05-18"]
)
BLOCKLISTED_DAYS = ("2026-05-14", "2026-05-15", "2026-05-19")
FORBIDDEN_SEARCH_SAFE_TOKENS = ("winner", "settlement", "outcome", "private", "residual")
FORBIDDEN_EXTERNAL_POLYDATA_PREFIXES = (
    "/Volumes/PolyData/poly_backtest_data",
    "/Volumes/PolyData/poly_backtest_tmp",
)
ALLOWED_EXTERNAL_POLYDATA_PREFIXES = (
    "/Volumes/PolyData/poly_replay_archive",
)
EXPECTED_AUDIT_TABLES = ("audit_candidate_evidence", "audit_candidates")
EXPECTED_AUDIT_VIEWS = ("candidate_evidence_by_experiment", "search_safe_private_blocked")
EXPECTED_CONFIGS = (
    "configs/backtest/search_multiasset_l1_flow_7asset_smoke_contract.json",
    "configs/backtest/search_multiasset_l1_flow_matrix_formal_v1.json",
    "configs/backtest/search_multiasset_l1_flow_matrix_deep_v1.json",
    "configs/backtest/search_multiasset_l1_flow_batch_formal_v1.json",
    "configs/backtest/search_multiasset_l1_flow_batch_deep_v1.json",
)
EXPECTED_RUNNERS = (
    "scripts/backtest_v1_pipeline_lib.py",
    "scripts/validate_multiasset_backtest_v1_local_install.py",
    "scripts/run_backtest_search_matrix.py",
    "scripts/run_backtest_search_shards.py",
    "scripts/run_backtest_matrix_batch.py",
    "scripts/run_backtest_batch_pipeline.py",
    "scripts/build_backtest_result_catalog.py",
    "scripts/compare_backtest_result_catalog.py",
    "scripts/select_backtest_candidate_shortlist.py",
    "scripts/build_backtest_validation_queue.py",
    "scripts/run_backtest_validation_queue.py",
    "scripts/run_backtest_l2_validation_queue.py",
    "scripts/run_backtest_l2_top_aligned_validation_queue.py",
    "scripts/build_backtest_validation_result_catalog.py",
    "scripts/build_backtest_candidate_audit_pack.py",
    "scripts/build_backtest_v1_crosswalk_report.py",
    "scripts/build_backtest_v1_btc_parity_gate.py",
    "scripts/build_xuan_bridge_scorecard.py",
    "scripts/build_xuan_old_baseline_l2_bridge.py",
    "scripts/build_xuan_old_baseline_pair_lots.py",
    "scripts/build_xuan_old_baseline_residual_l2_recovery.py",
    "scripts/build_multiasset_completion_candidate_base_from_l1_flow.py",
    "scripts/run_completion_candidate_state_machine.py",
    "scripts/build_btc_completion_adapter_delta_report.py",
    "scripts/build_btc_strict_rescue_opportunity_report.py",
    "scripts/build_btc_rescue_adjusted_capital_ledger.py",
    "scripts/build_btc_merge_turnover_report.py",
    "scripts/build_btc_source_semantics_delta_report.py",
    "scripts/build_multiasset_strict_rescue_opportunity_report.py",
    "scripts/build_multiasset_merge_turnover_report.py",
    "scripts/build_xuan_backtest_v1_strategy_readiness_gate.py",
    "scripts/repair_replay_store_duckdb_view_paths.py",
    "scripts/validate_l1_from_l2_parity.py",
    "scripts/build_l2_top_aligned_mart_partitioned.py",
    "scripts/build_l2_top_aligned_mart.py",
    "scripts/build_replay_store_v2.py",
    "scripts/build_multiasset_l2_validation_plan.py",
)
EXPECTED_SUPPORT_FILES = (
    "docs/BACKTEST_ARCHITECTURE_V1_RUNBOOK_ZH.md",
    "src/completion_first_data/owner_truth.py",
    "tests/test_completion_candidate_state_machine_schema.py",
    "tests/test_owner_truth_v1.py",
)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def issue(issues: list[dict[str, str]], severity: str, path: Path | str, message: str) -> None:
    issues.append({"severity": severity, "path": str(path), "message": message})


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def check_hash(path_value: str | None, expected: str | None, issues: list[dict[str, str]]) -> dict[str, Any]:
    if not path_value:
        return {"path": None, "present": False, "hash_ok": False}
    path = Path(path_value).expanduser()
    if not path.exists():
        issue(issues, "fail", path, "hashed artifact is missing")
        return {"path": str(path), "present": False, "hash_ok": False}
    actual = sha256_file(path)
    ok = bool(expected) and actual == expected
    if expected and not ok:
        issue(issues, "fail", path, "sha256 mismatch")
    return {"path": str(path), "present": True, "hash_ok": ok, "sha256": actual}


def check_no_external_backtest_root(paths: list[str], issues: list[dict[str, str]]) -> dict[str, Any]:
    forbidden: list[str] = []
    allowed_archive: list[str] = []
    for value in paths:
        if "/Volumes/PolyData" not in value:
            continue
        if any(prefix in value for prefix in FORBIDDEN_EXTERNAL_POLYDATA_PREFIXES):
            forbidden.append(value)
        elif any(prefix in value for prefix in ALLOWED_EXTERNAL_POLYDATA_PREFIXES):
            allowed_archive.append(value)
        else:
            forbidden.append(value)
    for path in forbidden:
        issue(issues, "fail", path, "published compact artifact still references external PolyData runtime/backtest path")
    return {
        "ok": not forbidden,
        "forbidden_count": len(forbidden),
        "allowed_archive_count": len(allowed_archive),
        "forbidden_refs": forbidden[:50],
        "allowed_archive_refs": allowed_archive[:50],
    }


def collect_strings(data: Any) -> list[str]:
    out: list[str] = []
    if isinstance(data, str):
        out.append(data)
    elif isinstance(data, dict):
        for value in data.values():
            out.extend(collect_strings(value))
    elif isinstance(data, list):
        for value in data:
            out.extend(collect_strings(value))
    return out


def collect_json_strings(paths: list[Path]) -> list[str]:
    values: list[str] = []
    for path in paths:
        if path.exists():
            values.extend(collect_strings(read_json(path)))
    return values


def query_search_safe_duckdb(db_path: Path, table: str, issues: list[dict[str, str]], strict_duckdb: bool) -> dict[str, Any]:
    if not db_path.exists():
        issue(issues, "fail", db_path, "search-safe DuckDB is missing")
        return {"present": False, "query_ok": False}
    try:
        import duckdb  # type: ignore
    except Exception as exc:  # pragma: no cover - environment dependent
        if strict_duckdb:
            issue(issues, "fail", db_path, f"duckdb import failed: {exc!r}")
        else:
            issue(issues, "warn", db_path, f"duckdb import failed; skipped row validation: {exc!r}")
        return {"present": True, "query_ok": False, "duckdb_error": repr(exc)}

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        row_count = int(con.execute(f"select count(*) from {table}").fetchone()[0])
        by_asset = {row[0]: int(row[1]) for row in con.execute(f"select market_symbol, count(*) from {table} group by 1").fetchall()}
        days = [row[0] for row in con.execute(f"select distinct day from {table} order by 1").fetchall()]
        blocklisted_rows = int(
            con.execute(
                f"select count(*) from {table} where day in ({','.join(['?'] * len(BLOCKLISTED_DAYS))})",
                list(BLOCKLISTED_DAYS),
            ).fetchone()[0]
        )
        columns = [row[1] for row in con.execute(f"pragma table_info('{table}')").fetchall()]
    finally:
        con.close()

    missing_assets = sorted(set(EXPECTED_ASSETS) - set(by_asset))
    missing_days = sorted(set(EXPECTED_DAYS) - set(days))
    forbidden_columns = [
        col for col in columns if any(token in col.lower() for token in FORBIDDEN_SEARCH_SAFE_TOKENS)
    ]
    if missing_assets:
        issue(issues, "fail", db_path, f"missing expected assets: {missing_assets}")
    if missing_days:
        issue(issues, "fail", db_path, f"missing expected days: {missing_days}")
    if blocklisted_rows:
        issue(issues, "fail", db_path, f"blocklisted day rows present: {blocklisted_rows}")
    if forbidden_columns:
        issue(issues, "fail", db_path, f"forbidden search-safe columns present: {forbidden_columns}")
    return {
        "present": True,
        "query_ok": not (missing_assets or missing_days or blocklisted_rows or forbidden_columns),
        "row_count": row_count,
        "by_asset": dict(sorted(by_asset.items())),
        "days": days,
        "blocklisted_rows": blocklisted_rows,
        "forbidden_columns": forbidden_columns,
    }


def check_audit_csv_paths(path_value: str | None, issues: list[dict[str, str]]) -> dict[str, Any]:
    if not path_value:
        return {"present": False, "external_validation_manifest_count": None}
    path = Path(path_value).expanduser()
    if not path.exists():
        return {"present": False, "external_validation_manifest_count": None}
    external = 0
    row_count = 0
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            row_count += 1
            if str(row.get("validation_manifest") or "").startswith("/Volumes/PolyData/poly_backtest_data/"):
                external += 1
    if external:
        issue(issues, "fail", path, f"external validation_manifest paths remain in audit evidence CSV: {external}")
    return {"present": True, "row_count": row_count, "external_validation_manifest_count": external}


def check_audit_duckdb(path_value: str | None, issues: list[dict[str, str]], strict_duckdb: bool) -> dict[str, Any]:
    if not path_value:
        return {"present": False, "query_ok": False}
    path = Path(path_value).expanduser()
    if not path.exists():
        return {"present": False, "query_ok": False}
    try:
        import duckdb  # type: ignore
    except Exception as exc:  # pragma: no cover - environment dependent
        if strict_duckdb:
            issue(issues, "fail", path, f"duckdb import failed: {exc!r}")
        else:
            issue(issues, "warn", path, f"duckdb import failed; skipped audit DuckDB validation: {exc!r}")
        return {"present": True, "query_ok": False, "duckdb_error": repr(exc)}

    con = duckdb.connect(str(path), read_only=True)
    try:
        relations = con.execute(
            "select table_name, table_type from information_schema.tables where table_schema='main'"
        ).fetchall()
        relation_map = {row[0]: row[1] for row in relations}
        external_count = int(
            con.execute(
                """
                select count(*)
                from audit_candidate_evidence
                where validation_manifest like '/Volumes/PolyData/poly_backtest_data/%'
                """
            ).fetchone()[0]
        )
        evidence_count = int(con.execute("select count(*) from audit_candidate_evidence").fetchone()[0])
        candidate_count = int(con.execute("select count(*) from audit_candidates").fetchone()[0])
    finally:
        con.close()

    missing_tables = [name for name in EXPECTED_AUDIT_TABLES if relation_map.get(name) != "BASE TABLE"]
    missing_views = [name for name in EXPECTED_AUDIT_VIEWS if relation_map.get(name) != "VIEW"]
    if missing_tables:
        issue(issues, "fail", path, f"missing expected audit base tables: {missing_tables}")
    if missing_views:
        issue(issues, "fail", path, f"missing expected audit views: {missing_views}")
    if external_count:
        issue(issues, "fail", path, f"external validation_manifest paths remain in audit DuckDB: {external_count}")
    return {
        "present": True,
        "query_ok": not (missing_tables or missing_views or external_count),
        "tables": sorted(name for name, kind in relation_map.items() if kind == "BASE TABLE"),
        "views": sorted(name for name, kind in relation_map.items() if kind == "VIEW"),
        "candidate_count": candidate_count,
        "evidence_count": evidence_count,
        "external_validation_manifest_count": external_count,
    }


def check_core_replay_duckdb(manifest_path: Path, issues: list[dict[str, str]], strict_duckdb: bool) -> dict[str, Any]:
    if not manifest_path.exists():
        return {"present": False, "query_ok": False}
    manifest = read_json(manifest_path)
    outputs = manifest.get("outputs") or {}
    db_value = outputs.get("duckdb") or manifest.get("duckdb") or "store.duckdb"
    db_path = Path(str(db_value)).expanduser()
    if not db_path.is_absolute():
        db_path = manifest_path.parent / db_path
    if not db_path.exists():
        issue(issues, "fail", db_path, "core replay DuckDB is missing")
        return {"present": False, "query_ok": False}
    try:
        import duckdb  # type: ignore
    except Exception as exc:  # pragma: no cover - environment dependent
        if strict_duckdb:
            issue(issues, "fail", db_path, f"duckdb import failed: {exc!r}")
        else:
            issue(issues, "warn", db_path, f"duckdb import failed; skipped core replay DuckDB validation: {exc!r}")
        return {"present": True, "query_ok": False, "duckdb_error": repr(exc)}

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        external_view_count = int(
            con.execute(
                """
                select count(*)
                from duckdb_views()
                where schema_name='main'
                  and not internal
                  and not temporary
                  and contains(sql, '/Volumes/PolyData/poly_backtest_data')
                """
            ).fetchone()[0]
        )
        counts = {
            table: int(con.execute(f"select count(*) from {table}").fetchone()[0])
            for table in ("market_meta", "md_trades", "md_book_l1", "settlement_records")
        }
    except Exception as exc:
        issue(issues, "fail" if strict_duckdb else "warn", db_path, f"core replay query failed: {exc!r}")
        return {"present": True, "query_ok": False, "duckdb_error": repr(exc)}
    finally:
        con.close()
    if external_view_count:
        issue(issues, "fail", db_path, f"core replay DuckDB views still reference external PolyData root: {external_view_count}")
    return {
        "present": True,
        "query_ok": external_view_count == 0 and all(value > 0 for value in counts.values()),
        "duckdb": str(db_path),
        "external_view_count": external_view_count,
        "row_counts": counts,
    }


def check_optional_report(path: Path, issues: list[dict[str, str]], expected_schema: str) -> dict[str, Any]:
    if not path.exists():
        issue(issues, "warn", path, f"optional report missing: {expected_schema}")
        return {"present": False, "schema_version": None, "status": None}
    data = read_json(path)
    schema = data.get("schema_version")
    if schema != expected_schema:
        issue(issues, "warn", path, f"unexpected report schema_version: {schema}")
    status = data.get("status")
    if status is None and "ok" in data:
        status = "OK" if data.get("ok") else "FAIL"
    return {
        "present": True,
        "schema_version": schema,
        "status": status,
        "sha256": sha256_file(path),
    }


def is_git_tracked(repo_root: Path, rel_path: str) -> bool | None:
    if not (repo_root / ".git").exists():
        return None
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", rel_path],
        cwd=repo_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def check_repo_files(repo_root: Path, rel_paths: tuple[str, ...], issues: list[dict[str, str]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rel in rel_paths:
        path = repo_root / rel
        present = path.exists()
        if not present:
            issue(issues, "fail", path, "required reproducibility file is missing")
        tracked = is_git_tracked(repo_root, rel) if present else False
        if present and tracked is False:
            issue(issues, "fail", path, "required reproducibility file is not tracked in git index")
        rows.append(
            {
                "path": str(path),
                "present": present,
                "git_tracked": tracked,
                "sha256": sha256_file(path) if present else None,
            }
        )
    return rows


def build_report(data_root: Path, strict_duckdb: bool) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    repo_root = Path(__file__).resolve().parents[1]
    derived = data_root / "derived"
    verification = data_root / "verification_store"
    paths = {
        "search_manifest": derived
        / "multiasset_l1_flow_event_store_v1/20260502_20260518_minsz10/L1_FLOW_SEARCH_SAFE_VIEW_MANIFEST.json",
        "readiness_report": derived
        / "contract_examples/backtest_readiness_deep_with_experiment_v1/BACKTEST_READINESS_REPORT.json",
        "suite_manifest": derived
        / "contract_examples/backtest_experiment_suite_deep_v1/BACKTEST_EXPERIMENT_SUITE_MANIFEST.json",
        "audit_manifest": derived
        / "contract_examples/backtest_candidate_audit_pack_latest/BACKTEST_CANDIDATE_AUDIT_PACK_MANIFEST.json",
        "audit_with_l2_manifest": derived
        / "contract_examples/backtest_candidate_audit_pack_with_l2_evidence_latest/BACKTEST_CANDIDATE_AUDIT_PACK_MANIFEST.json",
        "core_replay_manifest": verification
        / "replay_store_multiasset_core_v1/20260502_20260518_core/REPLAY_STORE_V2_MANIFEST.json",
        "runbook": repo_root / "docs/BACKTEST_ARCHITECTURE_V1_RUNBOOK_ZH.md",
        "crosswalk_report": derived
        / "contract_examples/backtest_v1_crosswalk_latest/BACKTEST_V1_CROSSWALK_REPORT.json",
        "btc_parity_gate": derived
        / "contract_examples/backtest_v1_btc_parity_latest/BACKTEST_V1_BTC_PARITY_GATE.json",
        "xuan_bridge_scorecard": derived
        / "contract_examples/xuan_bridge_scorecard_latest/XUAN_BRIDGE_SCORECARD_MANIFEST.json",
        "xuan_old_baseline_l2_bridge": derived
        / "contract_examples/xuan_old_baseline_l2_bridge_latest/XUAN_OLD_BASELINE_L2_BRIDGE_MANIFEST.json",
        "xuan_old_baseline_pair_lots": derived
        / "contract_examples/xuan_old_baseline_pair_lots_latest/XUAN_OLD_BASELINE_PAIR_LOTS_MANIFEST.json",
        "xuan_old_baseline_residual_l2_recovery": derived
        / "contract_examples/xuan_old_baseline_residual_l2_recovery_latest/XUAN_OLD_BASELINE_RESIDUAL_L2_RECOVERY_MANIFEST.json",
        "multiasset_completion_candidate_base": derived
        / "contract_examples/multiasset_completion_candidate_base_from_l1_flow_v1/CANDIDATE_BASE_MANIFEST.json",
        "multiasset_completion_state_machine": derived
        / "contract_examples/multiasset_completion_state_machine_from_l1_flow_v1/RESULT_SUMMARY_MANIFEST.json",
        "btc_completion_candidate_base": derived
        / "contract_examples/btc_completion_candidate_base_from_l1_flow_taker_normalized_v1/CANDIDATE_BASE_MANIFEST.json",
        "btc_completion_state_machine": derived
        / "contract_examples/btc_completion_state_machine_from_l1_flow_taker_normalized_v1/RESULT_SUMMARY_MANIFEST.json",
        "btc_completion_adapter_delta": derived
        / "contract_examples/btc_completion_adapter_delta_latest/BTC_COMPLETION_ADAPTER_DELTA_REPORT.json",
        "btc_strict_rescue_opportunity": derived
        / "contract_examples/btc_strict_rescue_opportunity_latest/BTC_STRICT_RESCUE_OPPORTUNITY_REPORT.json",
        "btc_rescue_adjusted_capital_ledger": derived
        / "contract_examples/btc_rescue_adjusted_capital_ledger_latest/BTC_RESCUE_ADJUSTED_CAPITAL_LEDGER.json",
        "btc_merge_turnover": derived
        / "contract_examples/btc_merge_turnover_latest/BTC_MERGE_TURNOVER_REPORT.json",
        "btc_source_semantics_delta": derived
        / "contract_examples/btc_source_semantics_delta_latest/BTC_SOURCE_SEMANTICS_DELTA_REPORT.json",
        "multiasset_strict_rescue_opportunity": derived
        / "contract_examples/multiasset_strict_rescue_opportunity_latest/MULTIASSET_STRICT_RESCUE_OPPORTUNITY_REPORT.json",
        "multiasset_merge_turnover": derived
        / "contract_examples/multiasset_merge_turnover_latest/MULTIASSET_MERGE_TURNOVER_REPORT.json",
        "xuan_strategy_readiness_gate": derived
        / "contract_examples/xuan_backtest_v1_strategy_readiness_latest/XUAN_BACKTEST_V1_STRATEGY_READINESS_GATE.json",
        "l2_validation_plan": derived
        / "contract_examples/backtest_l2_validation_plan_latest/BACKTEST_L2_VALIDATION_PLAN_MANIFEST.json",
        "l1_from_l2_parity": derived
        / "contract_examples/l1_from_l2_parity_latest/L1_FROM_L2_PARITY_REPORT.json",
        "l2_top_aligned_mart": derived
        / "contract_examples/l2_top_aligned_mart_20260502_20260518_l2/L2_TOP_ALIGNED_MART_MANIFEST.json",
        "l2_validation_results": derived
        / "contract_examples/backtest_l2_validation_results_latest/BACKTEST_L2_VALIDATION_RESULTS_MANIFEST.json",
    }

    loaded: dict[str, dict[str, Any]] = {}
    required_json_names = (
        "search_manifest",
        "readiness_report",
        "suite_manifest",
        "audit_manifest",
        "core_replay_manifest",
    )
    for name in required_json_names:
        path = paths[name]
        if not path.exists():
            issue(issues, "fail", path, f"{name} is missing")
            continue
        loaded[name] = read_json(path)

    initial_external_scan = check_no_external_backtest_root(
        [value for item in loaded.values() for value in collect_strings(item)], issues
    )

    search = loaded.get("search_manifest") or {}
    search_outputs = search.get("outputs") or {}
    search_duckdb_rel = search_outputs.get("duckdb") or "event_store.duckdb"
    search_table = search_outputs.get("table") or "l1_taker_buy_events_search_safe"
    search_db = paths["search_manifest"].parent / str(search_duckdb_rel)
    search_query = query_search_safe_duckdb(search_db, str(search_table), issues, strict_duckdb)
    manifest_assets = sorted(search.get("assets") or [])
    manifest_days = sorted(search.get("days") or [])
    if manifest_assets != sorted(EXPECTED_ASSETS):
        issue(issues, "fail", paths["search_manifest"], f"manifest assets mismatch: {manifest_assets}")
    if manifest_days != sorted(EXPECTED_DAYS):
        issue(issues, "fail", paths["search_manifest"], f"manifest days mismatch: {manifest_days}")
    if set(manifest_days) & set(BLOCKLISTED_DAYS):
        issue(issues, "fail", paths["search_manifest"], "manifest includes blocklisted days")

    readiness = loaded.get("readiness_report") or {}
    checks = readiness.get("checks") or []
    failed_checks = [item.get("name") for item in checks if not item.get("ok")]
    if readiness and not readiness.get("ok"):
        issue(issues, "fail", paths["readiness_report"], "readiness report ok is false")
    if failed_checks:
        issue(issues, "fail", paths["readiness_report"], f"readiness failed checks: {failed_checks}")

    suite = loaded.get("suite_manifest") or {}
    if suite and not suite.get("ok"):
        issue(issues, "fail", paths["suite_manifest"], "experiment suite ok is false")

    audit = loaded.get("audit_manifest") or {}
    if audit and not audit.get("ok"):
        issue(issues, "fail", paths["audit_manifest"], "candidate audit pack ok is false")
    if audit and not audit.get("readiness_manifest"):
        issue(issues, "fail", paths["audit_manifest"], "readiness_manifest is empty")
    if audit and not audit.get("readiness_fingerprint"):
        issue(issues, "fail", paths["audit_manifest"], "readiness_fingerprint is empty")
    if audit and not audit.get("readiness_manifest_sha256"):
        issue(issues, "fail", paths["audit_manifest"], "readiness_manifest_sha256 is empty")
    audit_hashes = {
        "csv": check_hash(audit.get("candidate_audit_pack_csv"), audit.get("candidate_audit_pack_csv_sha256"), issues),
        "evidence_csv": check_hash(
            audit.get("candidate_audit_pack_evidence_csv"), audit.get("candidate_audit_pack_evidence_csv_sha256"), issues
        ),
        "duckdb": check_hash(
            audit.get("candidate_audit_pack_duckdb"), audit.get("candidate_audit_pack_duckdb_sha256"), issues
        ),
    }
    audit_csv_paths = check_audit_csv_paths(audit.get("candidate_audit_pack_evidence_csv"), issues)
    audit_duckdb = check_audit_duckdb(audit.get("candidate_audit_pack_duckdb"), issues, strict_duckdb)
    core_replay_duckdb = check_core_replay_duckdb(paths["core_replay_manifest"], issues, strict_duckdb)
    required_configs = check_repo_files(repo_root, EXPECTED_CONFIGS, issues)
    required_runners = check_repo_files(repo_root, EXPECTED_RUNNERS, issues)
    required_support_files = check_repo_files(repo_root, EXPECTED_SUPPORT_FILES, issues)
    if not paths["runbook"].exists():
        issue(issues, "fail", paths["runbook"], "runbook is missing")
    reports = {
        "crosswalk": check_optional_report(
            paths["crosswalk_report"], issues, "backtest_v1_crosswalk_report_v1"
        ),
        "btc_parity_gate": check_optional_report(
            paths["btc_parity_gate"], issues, "backtest_v1_btc_parity_gate_v1"
        ),
        "xuan_bridge_scorecard": check_optional_report(
            paths["xuan_bridge_scorecard"], issues, "xuan_bridge_scorecard_v1"
        ),
        "xuan_old_baseline_l2_bridge": check_optional_report(
            paths["xuan_old_baseline_l2_bridge"], issues, "xuan_old_baseline_l2_bridge_v1"
        ),
        "xuan_old_baseline_pair_lots": check_optional_report(
            paths["xuan_old_baseline_pair_lots"], issues, "xuan_old_baseline_pair_lots_v1"
        ),
        "xuan_old_baseline_residual_l2_recovery": check_optional_report(
            paths["xuan_old_baseline_residual_l2_recovery"],
            issues,
            "xuan_old_baseline_residual_l2_recovery_v1",
        ),
        "multiasset_completion_candidate_base": check_optional_report(
            paths["multiasset_completion_candidate_base"], issues, "completion_candidate_base_v1"
        ),
        "multiasset_completion_state_machine": check_optional_report(
            paths["multiasset_completion_state_machine"], issues, "result_summary_v2"
        ),
        "btc_completion_candidate_base": check_optional_report(
            paths["btc_completion_candidate_base"], issues, "completion_candidate_base_v1"
        ),
        "btc_completion_state_machine": check_optional_report(
            paths["btc_completion_state_machine"], issues, "result_summary_v2"
        ),
        "btc_completion_adapter_delta": check_optional_report(
            paths["btc_completion_adapter_delta"], issues, "btc_completion_adapter_delta_report_v1"
        ),
        "btc_strict_rescue_opportunity": check_optional_report(
            paths["btc_strict_rescue_opportunity"], issues, "btc_strict_rescue_opportunity_report_v1"
        ),
        "btc_rescue_adjusted_capital_ledger": check_optional_report(
            paths["btc_rescue_adjusted_capital_ledger"], issues, "btc_rescue_adjusted_capital_ledger_v1"
        ),
        "btc_merge_turnover": check_optional_report(
            paths["btc_merge_turnover"], issues, "btc_merge_turnover_report_v1"
        ),
        "btc_source_semantics_delta": check_optional_report(
            paths["btc_source_semantics_delta"], issues, "btc_source_semantics_delta_report_v1"
        ),
        "multiasset_strict_rescue_opportunity": check_optional_report(
            paths["multiasset_strict_rescue_opportunity"], issues, "multiasset_strict_rescue_opportunity_report_v1"
        ),
        "multiasset_merge_turnover": check_optional_report(
            paths["multiasset_merge_turnover"], issues, "multiasset_merge_turnover_report_v1"
        ),
        "xuan_strategy_readiness_gate": check_optional_report(
            paths["xuan_strategy_readiness_gate"], issues, "xuan_backtest_v1_strategy_readiness_gate_v1"
        ),
        "l2_validation_plan": check_optional_report(
            paths["l2_validation_plan"], issues, "backtest_l2_validation_plan_v1"
        ),
        "l1_from_l2_parity": check_optional_report(
            paths["l1_from_l2_parity"], issues, "l1_from_l2_parity_report_v1"
        ),
        "l2_top_aligned_mart": check_optional_report(
            paths["l2_top_aligned_mart"], issues, "l2_top_aligned_mart_v1"
        ),
        "l2_validation_results": check_optional_report(
            paths["l2_validation_results"], issues, "backtest_l2_top_aligned_validation_results_v1"
        ),
        "audit_with_l2": check_optional_report(
            paths["audit_with_l2_manifest"], issues, "backtest_candidate_audit_pack_v1"
        ),
    }
    audit_with_l2 = read_json(paths["audit_with_l2_manifest"]) if paths["audit_with_l2_manifest"].exists() else {}
    published_json_paths = [
        paths[name]
        for name in (
            "search_manifest",
            "readiness_report",
            "suite_manifest",
            "audit_manifest",
            "audit_with_l2_manifest",
            "core_replay_manifest",
            "crosswalk_report",
            "btc_parity_gate",
            "xuan_bridge_scorecard",
            "xuan_old_baseline_l2_bridge",
            "xuan_old_baseline_pair_lots",
            "xuan_old_baseline_residual_l2_recovery",
            "multiasset_completion_candidate_base",
            "multiasset_completion_state_machine",
            "btc_completion_candidate_base",
            "btc_completion_state_machine",
            "btc_completion_adapter_delta",
            "btc_strict_rescue_opportunity",
            "btc_rescue_adjusted_capital_ledger",
            "btc_merge_turnover",
            "btc_source_semantics_delta",
            "multiasset_strict_rescue_opportunity",
            "multiasset_merge_turnover",
            "xuan_strategy_readiness_gate",
            "l2_validation_plan",
            "l1_from_l2_parity",
            "l2_top_aligned_mart",
            "l2_validation_results",
        )
    ]
    published_external_scan = check_no_external_backtest_root(collect_json_strings(published_json_paths), issues)
    external_polydata_runtime_refs_absent = bool(initial_external_scan["ok"] and published_external_scan["ok"])

    fail_count = sum(1 for item in issues if item["severity"] == "fail")
    warn_count = sum(1 for item in issues if item["severity"] == "warn")
    return {
        "artifact": "multiasset_backtest_v1_local_install_validation",
        "created_utc": utc_now(),
        "data_root": str(data_root),
        "status": "OK" if fail_count == 0 else "FAIL",
        "summary": {
            "fail_count": fail_count,
            "warn_count": warn_count,
            "external_polydata_backtest_refs_absent": external_polydata_runtime_refs_absent,
            "external_polydata_runtime_ref_count": published_external_scan["forbidden_count"],
            "allowed_external_polydata_archive_ref_count": published_external_scan["allowed_archive_count"],
            "expected_assets": list(EXPECTED_ASSETS),
            "expected_days": list(EXPECTED_DAYS),
            "blocklisted_days": list(BLOCKLISTED_DAYS),
            "search_safe_row_count": search_query.get("row_count"),
            "search_safe_assets": search_query.get("by_asset"),
            "readiness_ok": readiness.get("ok") if readiness else None,
            "suite_ok": suite.get("ok") if suite else None,
            "audit_ok": audit.get("ok") if audit else None,
            "audit_with_l2_status": reports["audit_with_l2"].get("status"),
            "candidate_selected_count": audit.get("selected_candidate_count") if audit else None,
            "candidate_selected_count_current": (
                audit_with_l2.get("selected_candidate_count")
                if audit_with_l2
                else audit.get("selected_candidate_count") if audit else None
            ),
            "audit_with_l2_selected_candidate_count": audit_with_l2.get("selected_candidate_count") if audit_with_l2 else None,
            "audit_with_l2_evidence_row_count": audit_with_l2.get("evidence_row_count") if audit_with_l2 else None,
            "audit_with_l2_ready_count": audit_with_l2.get("l2_top_aligned_evidence_ready_count") if audit_with_l2 else None,
            "private_promotion_ready_count": audit.get("private_promotion_ready_count") if audit else None,
            "audit_duckdb_query_ok": audit_duckdb.get("query_ok"),
            "core_replay_duckdb_query_ok": core_replay_duckdb.get("query_ok"),
            "core_replay_duckdb_external_view_count": core_replay_duckdb.get("external_view_count"),
            "audit_csv_external_validation_manifest_count": audit_csv_paths.get("external_validation_manifest_count"),
            "audit_duckdb_external_validation_manifest_count": audit_duckdb.get("external_validation_manifest_count"),
            "runbook_present": paths["runbook"].exists(),
            "required_config_count": len(required_configs),
            "required_config_present_count": sum(1 for row in required_configs if row["present"]),
            "required_runner_count": len(required_runners),
            "required_runner_present_count": sum(1 for row in required_runners if row["present"]),
            "required_support_file_count": len(required_support_files),
            "required_support_file_present_count": sum(1 for row in required_support_files if row["present"]),
            "required_config_git_tracked_count": sum(1 for row in required_configs if row["git_tracked"] is not False),
            "required_runner_git_tracked_count": sum(1 for row in required_runners if row["git_tracked"] is not False),
            "required_support_file_git_tracked_count": sum(
                1 for row in required_support_files if row["git_tracked"] is not False
            ),
            "crosswalk_status": reports["crosswalk"].get("status"),
            "btc_parity_gate_status": reports["btc_parity_gate"].get("status"),
            "xuan_bridge_scorecard_status": reports["xuan_bridge_scorecard"].get("status"),
            "xuan_old_baseline_l2_bridge_status": reports["xuan_old_baseline_l2_bridge"].get("status"),
            "xuan_old_baseline_pair_lots_status": reports["xuan_old_baseline_pair_lots"].get("status"),
            "xuan_old_baseline_residual_l2_recovery_status": reports[
                "xuan_old_baseline_residual_l2_recovery"
            ].get("status"),
            "multiasset_completion_candidate_base_status": reports["multiasset_completion_candidate_base"].get("status"),
            "multiasset_completion_state_machine_status": reports["multiasset_completion_state_machine"].get("status"),
            "btc_completion_candidate_base_status": reports["btc_completion_candidate_base"].get("status"),
            "btc_completion_state_machine_status": reports["btc_completion_state_machine"].get("status"),
            "btc_completion_adapter_delta_status": reports["btc_completion_adapter_delta"].get("status"),
            "btc_strict_rescue_opportunity_status": reports["btc_strict_rescue_opportunity"].get("status"),
            "btc_rescue_adjusted_capital_ledger_status": reports["btc_rescue_adjusted_capital_ledger"].get("status"),
            "btc_merge_turnover_status": reports["btc_merge_turnover"].get("status"),
            "btc_source_semantics_delta_status": reports["btc_source_semantics_delta"].get("status"),
            "multiasset_strict_rescue_opportunity_status": reports["multiasset_strict_rescue_opportunity"].get("status"),
            "multiasset_merge_turnover_status": reports["multiasset_merge_turnover"].get("status"),
            "xuan_strategy_readiness_gate_status": reports["xuan_strategy_readiness_gate"].get("status"),
            "l2_validation_plan_status": reports["l2_validation_plan"].get("status"),
            "l1_from_l2_parity_status": reports["l1_from_l2_parity"].get("status"),
            "l2_top_aligned_mart_status": reports["l2_top_aligned_mart"].get("status"),
            "l2_validation_results_status": reports["l2_validation_results"].get("status"),
        },
        "paths": {name: str(path) for name, path in paths.items()},
        "search_safe_query": search_query,
        "audit_hashes": audit_hashes,
        "audit_csv_paths": audit_csv_paths,
        "audit_duckdb": audit_duckdb,
        "core_replay_duckdb": core_replay_duckdb,
        "required_configs": required_configs,
        "required_runners": required_runners,
        "required_support_files": required_support_files,
        "reports": reports,
        "issues": issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    parser.add_argument("--output-json")
    parser.add_argument("--strict-duckdb", action="store_true")
    args = parser.parse_args()

    report = build_report(Path(args.data_root).expanduser(), strict_duckdb=args.strict_duckdb)
    text = json.dumps(report, indent=2, sort_keys=True)
    print(text)
    if args.output_json:
        out = Path(args.output_json).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    return 0 if report["status"] == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
