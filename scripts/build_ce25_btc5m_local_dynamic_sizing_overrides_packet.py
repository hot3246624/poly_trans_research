#!/usr/bin/env python3
"""Build local-bound CE25 BTC5m dynamic sizing overrides.

This translates the public-profile broad+overlay sizing schedule into a
no-leak local replay input for the 2026-05-02..2026-05-18 candidate base.

Important boundary: the public-profile `last60` boost is intentionally not
used because it depends on an account's last trade in a market, which is not an
ex-ante feature at candidate-selection time.
"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
BT_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
EXPORTS = ROOT / "data" / "exports"
OUTPUT_DIR = EXPORTS / "ce25_btc5m_local_dynamic_sizing_overrides_packet_20260607"

CANDIDATE_BASE_DIR = (
    BT_ROOT / "derived/completion_candidate_pipeline_v1/local_20260502_20260518_paircap102"
)
CANDIDATE_BASE_MANIFEST = CANDIDATE_BASE_DIR / "CANDIDATE_BASE_MANIFEST.json"
SIZING_GRID_PACKET = (
    EXPORTS
    / "ce25_btc5m_broad_overlay_sizing_grid_packet_20260606"
    / "CE25_BTC5M_BROAD_OVERLAY_SIZING_GRID_PACKET.json"
)
LOCAL_DYNAMIC_ADAPTER_PACKET = (
    EXPORTS
    / "ce25_btc5m_local_dynamic_sizing_adapter_packet_20260607"
    / "CE25_BTC5M_LOCAL_DYNAMIC_SIZING_ADAPTER_PACKET.json"
)
LOCAL_RESIDUAL_SMOKE_PACKET = (
    EXPORTS
    / "ce25_btc5m_local_residual_replay_smoke_packet_20260607"
    / "CE25_BTC5M_LOCAL_RESIDUAL_REPLAY_SMOKE_PACKET.json"
)
STATE_MACHINE = ROOT / "scripts/run_completion_candidate_state_machine.py"
BUILD_SCRIPT = ROOT / "scripts/build_ce25_btc5m_local_dynamic_sizing_overrides_packet.py"

STATUS = (
    "KEEP_CE25_BTC5M_LOCAL_DYNAMIC_SIZING_OVERRIDES_REVIEWED_"
    "NO_LAST60_LEAK_REPLAY_APPROVAL_REQUIRED_NOT_OOS_READY"
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


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def binding(path: Path, required: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if path.exists():
        out.update({"sha256": sha256_file(path), "size": path.stat().st_size})
    elif required:
        out["missing_required"] = True
    return out


def price_bucket(price: float) -> str:
    if price < 0.05:
        return "00-05"
    if price < 0.10:
        return "05-10"
    if price < 0.20:
        return "10-20"
    if price < 0.35:
        return "20-35"
    if price < 0.50:
        return "35-50"
    if price < 0.65:
        return "50-65"
    if price < 0.80:
        return "65-80"
    if price < 0.90:
        return "80-90"
    if price < 0.97:
        return "90-97"
    return "97-100"


def cap_for(bucket: str, first_side: str, schedule: dict[str, Any]) -> float:
    value = float(schedule["base_cap"])
    if bucket == "20-35":
        value += float(schedule["low_20_35_boost"])
    if bucket == "65-80":
        value += float(schedule["high_65_80_boost"])
    if first_side == "NO":
        value += float(schedule["down_side_boost"])
    if bucket in {"35-50", "50-65"} and first_side == "YES":
        value += float(schedule["mid_up_boost"])
    return min(float(schedule["per_market_cap"]), max(0.0, value))


def load_local_first_rows() -> list[dict[str, Any]]:
    try:
        import duckdb  # type: ignore
    except Exception as exc:  # pragma: no cover - operational guard
        raise SystemExit(f"duckdb is required. Run with `uv run --with duckdb python ...`: {exc!r}") from exc
    manifest = read_json(CANDIDATE_BASE_MANIFEST)
    db_path = CANDIDATE_BASE_DIR / str(manifest.get("outputs", {}).get("duckdb", "candidate_base.duckdb"))
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = con.execute(
            """
            with ranked as (
              select
                candidate_row_id,
                day,
                condition_id,
                slug,
                ts_ms,
                offset_s,
                side,
                public_trade_price,
                public_trade_size,
                side_alignment,
                l1_pair_ask,
                row_number() over (
                  partition by condition_id
                  order by ts_ms, candidate_row_id
                ) as rn
              from candidate_base
              where event_kind = 'public_trade'
                and side in ('YES', 'NO')
                and public_trade_price is not null
                and public_trade_size is not null
                and public_trade_size > 0
            )
            select *
            from ranked
            where rn = 1
            order by day, slug, condition_id
            """
        ).fetchall()
        cols = [desc[0] for desc in con.description]
    finally:
        con.close()
    return [dict(zip(cols, row, strict=True)) for row in rows]


def summarize_overrides(rows: list[dict[str, Any]]) -> dict[str, Any]:
    bucket_counts: dict[str, int] = {}
    side_counts: dict[str, int] = {}
    cap_counts: dict[str, int] = {}
    for row in rows:
        bucket_counts[str(row["source_first_price_bucket"])] = bucket_counts.get(str(row["source_first_price_bucket"]), 0) + 1
        side_counts[str(row["source_first_side"])] = side_counts.get(str(row["source_first_side"]), 0) + 1
        cap = str(row["max_open_cost"])
        cap_counts[cap] = cap_counts.get(cap, 0) + 1
    return {
        "row_count": len(rows),
        "unique_condition_count": len({row["condition_id"] for row in rows}),
        "unique_slug_count": len({row["slug"] for row in rows}),
        "enabled_false_count": sum(1 for row in rows if row["enabled"] == "false"),
        "bucket_counts": dict(sorted(bucket_counts.items())),
        "side_counts": dict(sorted(side_counts.items())),
        "cap_counts": dict(sorted(cap_counts.items(), key=lambda item: float(item[0]))),
    }


def write_preview(path: Path, overrides_csv: Path) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo 'NOT_AUTHORIZED: CE25 BTC5M local dynamic sizing overrides are review-only' >&2\n"
        "echo 'Future replay must be separately reviewed and approved.' >&2\n"
        "exit 66\n\n"
        "# Future reviewed state-machine argument only:\n"
        f"#   --sizing-overrides-csv {overrides_csv}\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def render_report(packet: dict[str, Any]) -> str:
    schedule = packet["source_public_profile_schedule"]
    summary = packet["override_summary"]
    lines = [
        "# CE25 BTC5M Local Dynamic Sizing Overrides",
        "",
        f"Status: `{packet['status']}`",
        "",
        "## Decision",
        "",
        "This packet materializes a local-source dynamic sizing override CSV for review. It does not run replay, OOS, WS, live, canary, or orders.",
        "",
        "## No-Leak Translation",
        "",
        "- Uses local 2026-05-02..05-18 first candidate price bucket and first side.",
        "- Keeps public-profile base/20-35/65-80/down/mid-up cap logic.",
        "- Excludes the public-profile `last60_boost` because source last trade timing is not an ex-ante selection field.",
        "- Keys overrides by local `condition_id`, so the state machine can match the local replay source.",
        "",
        "## Bound Public-Profile Schedule",
        "",
        f"- schedule: `{schedule['schedule_id']}`",
        f"- base_cap: `{schedule['base_cap']}`",
        f"- low_20_35_boost: `{schedule['low_20_35_boost']}`",
        f"- high_65_80_boost: `{schedule['high_65_80_boost']}`",
        f"- down_side_boost: `{schedule['down_side_boost']}`",
        f"- mid_up_boost: `{schedule['mid_up_boost']}`",
        f"- excluded_last60_boost: `{packet['translation_policy']['excluded_last60_boost']}`",
        "",
        "## Output Summary",
        "",
        f"- override rows: `{summary['row_count']}`",
        f"- unique conditions: `{summary['unique_condition_count']}`",
        f"- enabled_false_count: `{summary['enabled_false_count']}`",
        f"- side counts: `{summary['side_counts']}`",
        f"- cap counts: `{summary['cap_counts']}`",
        "",
        "## Non-Claims",
        "",
        "- replay_execution_authorized=false",
        "- oos_authorized=false",
        "- ws_authorized=false",
        "- orders_authorized=false",
        "- private_truth_ready=false",
        "- strategy_promotion_ready=false",
        "- live_ready=false",
        "- deployable=false",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    sizing_packet = read_json(SIZING_GRID_PACKET)
    schedule = sizing_packet["decision"]["best_high_coverage_review_candidate"]
    first_rows = load_local_first_rows()
    override_rows: list[dict[str, Any]] = []
    for row in first_rows:
        first_price = float(row["public_trade_price"])
        bucket = price_bucket(first_price)
        first_side = str(row["side"])
        cap = cap_for(bucket, first_side, schedule)
        override_rows.append(
            {
                "sizing_override_id": f"local_no_last60_{schedule['schedule_id']}:{row['condition_id']}",
                "candidate_row_id": "",
                "condition_id": row["condition_id"],
                "slug": row["slug"],
                "target_qty": "",
                "max_open_cost": round(cap, 6),
                "enabled": str(cap > 0).lower(),
                "source_day": row["day"],
                "source_first_candidate_row_id": row["candidate_row_id"],
                "source_first_ts_ms": row["ts_ms"],
                "source_first_offset_s": round(float(row["offset_s"]), 6),
                "source_first_price": round(first_price, 6),
                "source_first_price_bucket": bucket,
                "source_first_side": first_side,
                "source_first_side_alignment": row["side_alignment"],
                "source_first_l1_pair_ask": round(float(row["l1_pair_ask"]), 6) if row["l1_pair_ask"] is not None else "",
                "translation_policy": "FIRST_CANDIDATE_PRICE_SIDE_NO_LAST60",
            }
        )

    overrides_csv = OUTPUT_DIR / "ce25_btc5m_local_dynamic_sizing_overrides.csv"
    fieldnames = [
        "sizing_override_id",
        "candidate_row_id",
        "condition_id",
        "slug",
        "target_qty",
        "max_open_cost",
        "enabled",
        "source_day",
        "source_first_candidate_row_id",
        "source_first_ts_ms",
        "source_first_offset_s",
        "source_first_price",
        "source_first_price_bucket",
        "source_first_side",
        "source_first_side_alignment",
        "source_first_l1_pair_ask",
        "translation_policy",
    ]
    write_csv(overrides_csv, override_rows, fieldnames)
    preview = OUTPUT_DIR / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    write_preview(preview, overrides_csv)

    summary = summarize_overrides(override_rows)
    packet = {
        "schema_version": 1,
        "status": STATUS,
        "created_at": utc_now(),
        "scope": "review-only local-bound dynamic sizing override generation",
        "strategy_id": "CE25_BTC5M_BROAD_PARTICIPATION_CONTROLLER_V1",
        "source_public_profile_schedule": schedule,
        "translation_policy": {
            "policy_id": "FIRST_CANDIDATE_PRICE_SIDE_NO_LAST60",
            "local_source_window": "2026-05-02..2026-05-18",
            "key_field": "condition_id",
            "uses_ex_ante_local_fields_only": True,
            "allowed_fields": [
                "condition_id",
                "slug",
                "first candidate public_trade_price",
                "first candidate side",
                "first candidate offset_s",
                "first candidate side_alignment",
                "first candidate l1_pair_ask",
            ],
            "excluded_fields": [
                "winner_side",
                "settlement payout",
                "source_cash_pnl",
                "source_pair_cost",
                "source_resid_rate",
                "public-profile source_last_trade_s",
                "public-profile source_last_delta_bucket",
            ],
            "excluded_last60_boost": schedule.get("last60_boost"),
            "excluded_last60_reason": "account last trade timing is outcome/control profile evidence, not an ex-ante local replay selection field",
        },
        "override_summary": summary,
        "source_bindings": {
            "candidate_base_manifest": binding(CANDIDATE_BASE_MANIFEST),
            "sizing_grid_packet": binding(SIZING_GRID_PACKET),
            "local_dynamic_adapter_packet": binding(LOCAL_DYNAMIC_ADAPTER_PACKET),
            "local_residual_smoke_packet": binding(LOCAL_RESIDUAL_SMOKE_PACKET),
            "state_machine_script": binding(STATE_MACHINE),
            "build_script": binding(BUILD_SCRIPT),
        },
        "outputs": {
            "packet": "CE25_BTC5M_LOCAL_DYNAMIC_SIZING_OVERRIDES_PACKET.json",
            "report": "CE25_BTC5M_LOCAL_DYNAMIC_SIZING_OVERRIDES_REPORT.md",
            "overrides_csv": "ce25_btc5m_local_dynamic_sizing_overrides.csv",
            "command_preview_not_authorized": "COMMAND_PREVIEW_NOT_AUTHORIZED.sh",
            "sha256sums": "SHA256SUMS.txt",
        },
        "next_step": {
            "allowed": "separate local replay using --sizing-overrides-csv with this CSV",
            "replay_authorized_by_this_packet": False,
            "suggested_probe": "rerun broad_seed300_qty5_pc102_imb250 with local no-last60 overrides",
        },
        "highest_allowed_status": STATUS,
        "non_claims": {
            "replay_execution_authorized": False,
            "ws_authorized": False,
            "oos_authorized": False,
            "orders_authorized": False,
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "deployable": False,
        },
    }
    packet_path = OUTPUT_DIR / "CE25_BTC5M_LOCAL_DYNAMIC_SIZING_OVERRIDES_PACKET.json"
    report_path = OUTPUT_DIR / "CE25_BTC5M_LOCAL_DYNAMIC_SIZING_OVERRIDES_REPORT.md"
    write_json(packet_path, packet)
    report_path.write_text(render_report(packet), encoding="utf-8")

    manifest_files = [packet_path, report_path, overrides_csv, preview]
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
                "overrides_csv": str(overrides_csv),
                "override_summary": summary,
                "sha256sums": str(sums_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
