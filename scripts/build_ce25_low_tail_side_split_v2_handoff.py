#!/usr/bin/env python3
"""Build a CE25 BTC5M low-tail side-split V2 handoff report."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
BT_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
RUN_DIR = (
    BT_ROOT
    / "derived"
    / "ce25_nagi_shadow_policy_autoresearch_v0"
    / "ce25_low_tail_side_split_v2_iter0_20260606"
)
OUTPUT_DIR = ROOT / "data" / "exports" / "ce25_low_tail_side_split_v2_handoff_20260606"
DOC_PATH = ROOT / "docs" / "research" / "CE25_BTC5M_LOW_TAIL_SIDE_SPLIT_V2_HANDOFF_20260606_ZH.md"
STRATEGY_INPUT = ROOT / "configs" / "ce25_low_tail" / "CE25_BTC5M_LAST60_FIRST20_35_V2_INPUT.json"
RUNNER = ROOT / "scripts" / "run_ce25_nagi_shadow_policy_runner.py"

PROFILE_PATHS = [
    *sorted(
        (ROOT / "data" / "exports" / "rolling_profiles_ce25_nagi_20260528_1145_to_20260604_1145_bjt").glob(
            "ce25_*/ce25_market_sequence.csv"
        )
    ),
    ROOT / "data" / "exports" / "profile_ce25_latest_24h_20260603_1145_to_20260604_1145_bjt" / "ce25_market_sequence.csv",
    ROOT / "data" / "exports" / "profile_ce25_latest_mid24h_20260604_1110_to_20260605_1110_bjt" / "ce25_market_sequence.csv",
    ROOT / "data" / "exports" / "profile_ce25_latest_24h_20260605_1110_to_20260606_1110_bjt" / "ce25_market_sequence.csv",
]

STATUS = "KEEP_CE25_LOW_TAIL_SIDE_SPLIT_V2_WATCH_L2_VALIDATION_NEXT_NOT_OOS_READY"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def fnum(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None, delimiter: str = ",") -> None:
    if fieldnames is None:
        seen: set[str] = set()
        fieldnames = []
        for row in rows:
            for key in row:
                if key not in seen:
                    seen.add(key)
                    fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=delimiter)
        writer.writeheader()
        writer.writerows(rows)


def metric_summary(rows: list[dict[str, Any]], prefix: str = "") -> dict[str, Any]:
    buy = sum(fnum(row.get(prefix + "buy_actual", row.get("buy_actual"))) for row in rows)
    pnl = sum(fnum(row.get(prefix + "cash_pnl", row.get("cash_pnl"))) for row in rows)
    fee = sum(fnum(row.get(prefix + "fee", row.get("fee"))) for row in rows)
    paired = sum(fnum(row.get(prefix + "paired_qty", row.get("paired_qty"))) for row in rows)
    buy_qty = sum(fnum(row.get(prefix + "buy_qty", row.get("buy_qty"))) for row in rows)
    resid = sum(fnum(row.get(prefix + "resid_qty", row.get("resid_qty"))) for row in rows)
    pc = (
        sum(fnum(row.get(prefix + "pair_cost", row.get("pair_cost"))) * fnum(row.get(prefix + "paired_qty", row.get("paired_qty"))) for row in rows)
        / paired
        if paired
        else 0.0
    )
    bad = sum(
        fnum(row.get(prefix + "buy_actual", row.get("buy_actual")))
        for row in rows
        if fnum(row.get(prefix + "pair_cost", row.get("pair_cost"))) >= 1.0
    )
    lt98 = sum(
        fnum(row.get(prefix + "buy_actual", row.get("buy_actual")))
        for row in rows
        if fnum(row.get(prefix + "pair_cost", row.get("pair_cost"))) < 0.98
    )
    gross = buy - fee
    return {
        "market_count": len(rows),
        "buy_actual": round(buy, 6),
        "cash_pnl": round(pnl, 6),
        "roi": round(pnl / buy, 8) if buy else 0.0,
        "fee": round(fee, 6),
        "fee_rate_est": round(fee / gross, 8) if gross else 0.0,
        "pair_cost_weighted": round(pc, 8),
        "resid_rate": round(resid / buy_qty, 8) if buy_qty else 0.0,
        "bad_pc_ge_100_share": round(bad / buy, 8) if buy else 0.0,
        "pc_lt_098_share": round(lt98 / buy, 8) if buy else 0.0,
        "profitable_market_count": sum(1 for row in rows if fnum(row.get(prefix + "cash_pnl", row.get("cash_pnl"))) > 0),
    }


def profile_label(path: Path) -> str:
    return path.parent.name


def load_profile_bucket() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    market_rows: list[dict[str, Any]] = []
    window_rows: list[dict[str, Any]] = []
    for path in PROFILE_PATHS:
        rows = [
            row
            for row in read_csv(path)
            if row.get("asset") == "BTC"
            and row.get("tf") == "5m"
            and row.get("last_delta_bucket") == "last_60s"
            and row.get("first_price_bucket") == "20-35"
        ]
        label = profile_label(path)
        for row in rows:
            out = dict(row)
            out["source_profile_label"] = label
            out["source_profile_path"] = str(path)
            market_rows.append(out)
        summary = metric_summary(rows)
        summary["source_profile_label"] = label
        summary["source_profile_path"] = str(path)
        summary["profitable_window"] = summary["cash_pnl"] > 0
        window_rows.append(summary)
    side_rows = []
    for side in ("DOWN", "UP"):
        xs = [row for row in market_rows if row.get("first_side") == side]
        item = metric_summary(xs)
        item["first_side"] = side
        side_rows.append(item)
    total = metric_summary(market_rows)
    total["window_count"] = len(window_rows)
    total["profitable_window_count"] = sum(1 for row in window_rows if row["profitable_window"])
    total["side_split"] = side_rows
    return market_rows, window_rows, total


def load_replay() -> tuple[list[dict[str, str]], list[dict[str, Any]], dict[str, Any]]:
    rows = read_csv(RUN_DIR / "autoresearch_ledger.csv")
    selected_families = [
        "same_row_cap_0.965",
        "entry_paircap_cap_0.965",
        "same_row_pair_only",
        "entry_paircap_required",
        "same_row_target_qty_8",
        "entry_paircap_target_qty_8",
        "same_row_target_qty_13",
        "entry_paircap_target_qty_13",
        "longer_sla_60s",
    ]
    selected: list[dict[str, Any]] = []
    for fee in ("0.0283", "0.03"):
        fee_rows = [row for row in rows if str(row.get("fee_rate")) == fee]
        for side in ("DOWN", "UP"):
            side_rows = [row for row in fee_rows if row.get("first_leg_side") == side]
            for family in selected_families:
                xs = [row for row in side_rows if family in row.get("branch_id", "")]
                if not xs:
                    continue
                row = max(xs, key=lambda r: fnum(r.get("autoresearch_score")))
                selected.append(
                    {
                        "fee_rate": fee,
                        "first_leg_side": side,
                        "family": family,
                        "branch_id": row.get("branch_id"),
                        "fee_after_pnl": fnum(row.get("fee_after_pnl")),
                        "net_roi": fnum(row.get("net_roi")),
                        "pair_actions": int(fnum(row.get("pair_actions"))),
                        "paired_market_count": int(fnum(row.get("paired_market_count"))),
                        "gross_buy_cost": fnum(row.get("gross_buy_cost")),
                        "net_pair_cost_wavg": fnum(row.get("net_pair_cost_wavg")),
                        "residual_qty_rate": fnum(row.get("residual_qty_rate")),
                        "bad_pair_cost_action_share": fnum(row.get("bad_pair_cost_action_share")),
                        "classification": row.get("classification"),
                        "target_qty": fnum(row.get("target_qty")),
                        "same_row_pair_only": row.get("same_row_pair_only"),
                        "entry_requires_pair_cap": row.get("entry_requires_pair_cap"),
                        "entry_requires_opposite_depth": row.get("entry_requires_opposite_depth"),
                        "mutation_note": row.get("mutation_note"),
                    }
                )

    side_summary: list[dict[str, Any]] = []
    for fee in ("0.0283", "0.03"):
        fee_rows = [row for row in rows if str(row.get("fee_rate")) == fee]
        for side in ("DOWN", "UP"):
            side_rows = [row for row in fee_rows if row.get("first_leg_side") == side]
            best = max(side_rows, key=lambda row: fnum(row.get("autoresearch_score")))
            best_pnl = max(side_rows, key=lambda row: fnum(row.get("fee_after_pnl")))
            side_summary.append(
                {
                    "fee_rate": fee,
                    "first_leg_side": side,
                    "variant_count": len(side_rows),
                    "positive_variant_count": sum(1 for row in side_rows if fnum(row.get("fee_after_pnl")) > 0),
                    "keep_variant_count": sum(1 for row in side_rows if "KEEP" in str(row.get("classification"))),
                    "best_score_branch": best.get("branch_id"),
                    "best_score_pnl": fnum(best.get("fee_after_pnl")),
                    "best_score_roi": fnum(best.get("net_roi")),
                    "best_score_pairs": int(fnum(best.get("pair_actions"))),
                    "best_score_pair_cost": fnum(best.get("net_pair_cost_wavg")),
                    "best_score_classification": best.get("classification"),
                    "best_pnl_branch": best_pnl.get("branch_id"),
                    "best_pnl": fnum(best_pnl.get("fee_after_pnl")),
                    "best_pnl_roi": fnum(best_pnl.get("net_roi")),
                    "best_pnl_residual_qty_rate": fnum(best_pnl.get("residual_qty_rate")),
                    "best_pnl_classification": best_pnl.get("classification"),
                }
            )
    summary = {
        "run_dir": str(RUN_DIR),
        "manifest_sha256": sha256_file(RUN_DIR / "AUTORESEARCH_MANIFEST.json"),
        "ledger_sha256": sha256_file(RUN_DIR / "autoresearch_ledger.csv"),
        "variant_count": len({row["variant_id"] for row in rows}),
        "result_count": len(rows),
        "side_summary": side_summary,
    }
    return rows, selected, summary


def pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def money(value: float) -> str:
    return f"{value:,.2f}"


def write_report(profile_total: dict[str, Any], window_rows: list[dict[str, Any]], replay_summary: dict[str, Any], selected: list[dict[str, Any]]) -> None:
    rows_3pct = [row for row in selected if row["fee_rate"] == "0.03"]
    def find(side: str, family: str) -> dict[str, Any]:
        for row in rows_3pct:
            if row["first_leg_side"] == side and row["family"] == family:
                return row
        return {}

    down_cap = find("DOWN", "entry_paircap_cap_0.965")
    up_cap = find("UP", "same_row_cap_0.965")
    down_qty8 = find("DOWN", "same_row_target_qty_8")
    up_qty8 = find("UP", "same_row_target_qty_8")
    down_long = find("DOWN", "longer_sla_60s")
    up_long = find("UP", "longer_sla_60s")

    side_public = {row["first_side"]: row for row in profile_total["side_split"]}
    lines = [
        "# CE25 BTC5M Low-Tail Side-Split V2 Handoff",
        "",
        f"Status: `{STATUS}`",
        "",
        "## 结论",
        "",
        "`CE25_BTC5M_LOW_PRICE_TAIL_DOWN_V1` 应升级为 `CE25_BTC5M_LOW_PRICE_TAIL_SIDE_SPLIT_V2`，但只能升级为 watch / L2 validation candidate，不能升级为 OOS/live。",
        "",
        "原因：最新 9 个 public profile 窗口显示 `BTC 5m / last_60s / first_price 20-35` 在 DOWN 与 UP 两侧都为正；本轮修正 runner 后，UP strict residual-killer 也在本地 2026-05-02 至 2026-05-18 book-shadow 里过了 fee stress。旧工具链把 UP 标为 control，导致之前没有公平扫描 UP strict，这是一个真实工具偏差。",
        "",
        "但它不是 full-keep 主策略：strict 分支残差为 0、ROI 很高，但 paired markets 只有 29 到 48 个，属于高质量低覆盖 micro-alpha。不能用宽松 longer SLA 版本放大，因为它虽然 PnL 更高，但分类是 `KEEP_WATCH_RESIDUAL_HIGH`。",
        "",
        "## Public Profile 证据",
        "",
        f"- source window: 2026-05-28 11:45 BJT 到 2026-06-06 11:10 BJT，9 个 24h-ish profile。",
        f"- bucket: BTC 5m / last_60s / first_price 20-35。",
        f"- markets: {profile_total['market_count']}。",
        f"- buy_actual: ${money(profile_total['buy_actual'])}。",
        f"- cash_pnl: ${money(profile_total['cash_pnl'])}，ROI {pct(profile_total['roi'])}。",
        f"- weighted pair_cost: {profile_total['pair_cost_weighted']:.4f}。",
        f"- resid_rate: {pct(profile_total['resid_rate'])}。",
        f"- bad_pc>=1 share: {pct(profile_total['bad_pc_ge_100_share'])}。",
        f"- profitable windows: {profile_total['profitable_window_count']}/{profile_total['window_count']}。",
        "",
        "| side | markets | buy_actual | cash_pnl | ROI | pair_cost | resid_rate | bad_pc>=1 | pc<0.98 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for side in ("DOWN", "UP"):
        row = side_public[side]
        lines.append(
            f"| {side} | {row['market_count']} | ${money(row['buy_actual'])} | ${money(row['cash_pnl'])} | {pct(row['roi'])} | "
            f"{row['pair_cost_weighted']:.4f} | {pct(row['resid_rate'])} | {pct(row['bad_pc_ge_100_share'])} | {pct(row['pc_lt_098_share'])} |"
        )
    lines.extend(
        [
            "",
            "Interpretation: public profile 不支持继续把它理解成纯 DOWN-only。DOWN 规模更大，UP ROI 与 pair_cost 更好。两边都应进入 side-split 验证。",
            "",
            "## 本地 Book-Shadow 证据",
            "",
            f"- run: `{RUN_DIR}`",
            "- source data: local completion candidate base / book-shadow，2026-05-02 至 2026-05-18。",
            f"- variants: {replay_summary['variant_count']}，results: {replay_summary['result_count']}。",
            "- fee stress: 2.83% 与 3.0%。下表使用 3.0%。",
            "",
            "| side | strict branch | pnl | ROI | pairs | markets | pair_cost | residual | class |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
            f"| DOWN | entry_paircap_cap_0.965 | ${money(down_cap['fee_after_pnl'])} | {pct(down_cap['net_roi'])} | {down_cap['pair_actions']} | {down_cap['paired_market_count']} | {down_cap['net_pair_cost_wavg']:.4f} | {pct(down_cap['residual_qty_rate'])} | {down_cap['classification']} |",
            f"| UP | same_row_cap_0.965 | ${money(up_cap['fee_after_pnl'])} | {pct(up_cap['net_roi'])} | {up_cap['pair_actions']} | {up_cap['paired_market_count']} | {up_cap['net_pair_cost_wavg']:.4f} | {pct(up_cap['residual_qty_rate'])} | {up_cap['classification']} |",
            "",
            "Capacity stress 仍是低覆盖，但 target_qty=8 没有破坏质量：",
            "",
            "| side | target_qty=8 branch | pnl | ROI | pairs | markets | pair_cost | residual |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
            f"| DOWN | same_row_target_qty_8 | ${money(down_qty8['fee_after_pnl'])} | {pct(down_qty8['net_roi'])} | {down_qty8['pair_actions']} | {down_qty8['paired_market_count']} | {down_qty8['net_pair_cost_wavg']:.4f} | {pct(down_qty8['residual_qty_rate'])} |",
            f"| UP | same_row_target_qty_8 | ${money(up_qty8['fee_after_pnl'])} | {pct(up_qty8['net_roi'])} | {up_qty8['pair_actions']} | {up_qty8['paired_market_count']} | {up_qty8['net_pair_cost_wavg']:.4f} | {pct(up_qty8['residual_qty_rate'])} |",
            "",
            "不要被 longer SLA 的绝对 PnL 误导：",
            "",
            "| side | longer SLA pnl | ROI | pairs | residual | class |",
            "| --- | ---: | ---: | ---: | ---: | --- |",
            f"| DOWN | ${money(down_long['fee_after_pnl'])} | {pct(down_long['net_roi'])} | {down_long['pair_actions']} | {pct(down_long['residual_qty_rate'])} | {down_long['classification']} |",
            f"| UP | ${money(up_long['fee_after_pnl'])} | {pct(up_long['net_roi'])} | {up_long['pair_actions']} | {pct(up_long['residual_qty_rate'])} | {up_long['classification']} |",
            "",
            "Interpretation: 核心不是“低价就买”，而是“低价尾部 + 对手腿已经能在同一行或短 SLA 内以 paircap 完成”。宽松追 completion 会把 residual 风险重新打开。",
            "",
            "## 可复现秘籍",
            "",
            "1. 时间：只看 BTC 5m 最后 60 秒，对应 fixed clock / public book，不允许直接使用 CE25 的 `source_last_delta_bucket`。",
            "2. 价格：第一腿 executable price 在 0.20 到 0.35；0.10 到 0.20 邻居是负控，不是主线。",
            "3. 配对：入口必须要求 projected pair_cost <= 0.965/0.970，并且 opposite depth 可覆盖；same-row 优先。",
            "4. 方向：V2 应同时允许 DOWN 与 UP，但分别记账、分别限额；不要把两侧合并成无差别仓位。",
            "5. 风控：strict residual-killer 才是主线；longer SLA 只能研究，不能作为默认。",
            "6. 容量：target_qty=5 是基线，target_qty=8 可验证；target_qty=13 虽仍正，但必须等 L2 depth/capacity 通过后再讨论。",
            "",
            "## 下一步",
            "",
            "P0：对 `last60_up/down_20_35_side_split_same_row_cap_0.965` 与 `entry_paircap_cap_0.965` 做 L2 top-aligned validation。",
            "",
            "P1：对 target_qty=8 做同样 L2 验证，要求 top1/opposite depth 覆盖，不能只看 book-shadow。",
            "",
            "P2：把 V2 policy 写入正式 strategy input，但保持 `orders_authorized=false`、`live_ready=false`。",
            "",
            "## 边界",
            "",
            "本报告只使用 public profile 与本地 public/replay book-shadow。它不证明 CE25 私有 maker/taker、真实成交、排队优先级、可部署性或 live 预期收益。",
            "",
        ]
    )
    DOC_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    profile_rows, window_rows, profile_total = load_profile_bucket()
    replay_rows, selected, replay_summary = load_replay()

    write_csv(OUTPUT_DIR / "ce25_low_tail_public_profile_market_ledger.csv", profile_rows)
    write_csv(OUTPUT_DIR / "ce25_low_tail_public_profile_window_summary.tsv", window_rows, delimiter="\t")
    write_csv(OUTPUT_DIR / "ce25_low_tail_replay_selected_branches.tsv", selected, delimiter="\t")

    summary = {
        "schema_version": 1,
        "status": STATUS,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "strategy_id": "CE25_BTC5M_LOW_PRICE_TAIL_SIDE_SPLIT_V2",
        "public_profile": profile_total,
        "replay": replay_summary,
        "decision": {
            "upgrade_down_only_to_side_split_v2": True,
            "highest_allowed_status": STATUS,
            "keep_as_watch_not_full_keep": True,
            "reason": "both DOWN and UP strict residual-killer variants are fee-positive with zero residual, but low paired-market coverage requires L2 validation before promotion",
        },
        "non_claims": {
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "deployable": False,
            "orders_authorized": False,
        },
        "sources": {
            "strategy_input": str(STRATEGY_INPUT),
            "runner": str(RUNNER),
            "run_dir": str(RUN_DIR),
            "profile_paths": [str(path) for path in PROFILE_PATHS],
        },
    }
    summary_path = OUTPUT_DIR / "CE25_LOW_TAIL_SIDE_SPLIT_V2_HANDOFF_SUMMARY.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    write_report(profile_total, window_rows, replay_summary, selected)

    manifest_paths = [
        summary_path,
        OUTPUT_DIR / "ce25_low_tail_public_profile_market_ledger.csv",
        OUTPUT_DIR / "ce25_low_tail_public_profile_window_summary.tsv",
        OUTPUT_DIR / "ce25_low_tail_replay_selected_branches.tsv",
        DOC_PATH,
        STRATEGY_INPUT,
        RUNNER,
        RUN_DIR / "AUTORESEARCH_MANIFEST.json",
        RUN_DIR / "autoresearch_ledger.csv",
    ]
    manifest = {
        "schema_version": 1,
        "status": STATUS,
        "artifacts": [
            {"path": str(path), "sha256": sha256_file(path), "size_bytes": path.stat().st_size}
            for path in manifest_paths
        ],
        "summary_sha256": sha256_file(summary_path),
        "report_sha256": sha256_file(DOC_PATH),
    }
    manifest_path = OUTPUT_DIR / "CE25_LOW_TAIL_SIDE_SPLIT_V2_HANDOFF_HASH_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "status": STATUS,
                "output_dir": str(OUTPUT_DIR),
                "report": str(DOC_PATH),
                "summary_sha256": sha256_file(summary_path),
                "report_sha256": sha256_file(DOC_PATH),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
