#!/usr/bin/env python3
"""Build a review-only NAGI777 reverse-engineering handoff packet.

The packet combines public activity profiling with local maker-queue proxy
evidence. It is intentionally research-only: no orders, keys, signing,
redeems, cancels, or private maker claims.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
EXPORTS = ROOT / "data" / "exports"
PROFILE = EXPORTS / "profile_nagi_20260604_1110_to_20260608_1110_bjt"
OUT = EXPORTS / "nagi777_reverse_engineering_packet_20260608"
DOC = ROOT / "docs" / "research" / "NAGI777_REVERSE_ENGINEERING_20260608_ZH.md"

PROFILE_SUMMARY = PROFILE / "summary.json"
PROFILE_MARKETS = PROFILE / "ce25_market_sequence.csv"
PIVOT_PACKET = (
    EXPORTS
    / "nagi_last60_midprice_fastpair_pivot_packet_20260608"
    / "NAGI_LAST60_MIDPRICE_FASTPAIR_PIVOT_PACKET.json"
)
FRONTIER_PACKET = (
    EXPORTS
    / "nagi_maker_queue_exhaustive_frontier_packet_20260608"
    / "NAGI_MAKER_QUEUE_EXHAUSTIVE_FRONTIER_PACKET.json"
)
FRONTIER_TOP = (
    EXPORTS
    / "nagi_maker_queue_exhaustive_frontier_packet_20260608"
    / "nagi_maker_queue_exhaustive_frontier_top.csv"
)
RESIDUAL_MATRIX_PACKET = (
    EXPORTS
    / "nagi_last60_midprice_maker_queue_residual_matrix_packet_20260608"
    / "NAGI_LAST60_MIDPRICE_MAKER_QUEUE_RESIDUAL_MATRIX_PACKET.json"
)
BRANCH_SUMMARY = (
    EXPORTS
    / "nagi_last60_midprice_maker_queue_proxy_packet_20260608"
    / "nagi_last60_midprice_maker_queue_branch_summary.csv"
)

BUILDER = ROOT / "scripts" / "build_nagi777_reverse_engineering_packet.py"

STATUS = (
    "KEEP_NAGI777_REVERSE_ENGINEERING_REVIEWED_PUBLIC_ONLY_"
    "MAKER_QUEUE_TELEMETRY_REQUIRED_NOT_DEPLOYABLE"
)
ACCOUNT = "0xbf337426aa856996b8bb79b238345dd1a0276bf7"


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


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def as_str(value: Any) -> str:
    return "" if value is None else str(value)


def round6(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 6)


def pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.2f}%"


def money(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"${value:,.2f}"


def num(value: float | None, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def load_market_rows(path: Path) -> list[dict[str, Any]]:
    numeric = {
        "trade_count",
        "first_trade_s",
        "last_trade_s",
        "first_delta_s",
        "last_delta_s",
        "pair_delay_s",
        "first_price",
        "first_side_qty",
        "opp_qty",
        "first_side_avg",
        "opp_avg",
        "pair_cost",
        "buy_qty",
        "buy_actual",
        "fee",
        "fee_rate",
        "cash_in",
        "cash_pnl",
        "cohort_pnl",
        "paired_qty",
        "resid_qty",
        "resid_rate",
        "pair_pnl",
        "residual_pnl_est",
        "yes_qty",
        "no_qty",
        "yes_avg",
        "no_avg",
    }
    rows: list[dict[str, Any]] = []
    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            row: dict[str, Any] = {}
            for key, value in raw.items():
                row[key] = as_float(value) if key in numeric else value
            rows.append(row)
    return rows


def weighted_pair_cost(rows: Iterable[dict[str, Any]]) -> float | None:
    den = 0.0
    nume = 0.0
    for row in rows:
        pc = row.get("pair_cost")
        paired_qty = row.get("paired_qty") or 0.0
        if pc is None or paired_qty <= 0:
            continue
        den += paired_qty
        nume += paired_qty * pc
    return nume / den if den else None


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    buy_actual = sum((row.get("buy_actual") or 0.0) for row in rows)
    buy_qty = sum((row.get("buy_qty") or 0.0) for row in rows)
    paired_qty = sum((row.get("paired_qty") or 0.0) for row in rows)
    resid_qty = sum((row.get("resid_qty") or 0.0) for row in rows)
    cash_pnl = sum((row.get("cash_pnl") or 0.0) for row in rows)
    pair_pnl = sum((row.get("pair_pnl") or 0.0) for row in rows)
    residual_pnl = sum((row.get("residual_pnl_est") or 0.0) for row in rows)
    fee = sum((row.get("fee") or 0.0) for row in rows)
    bad_buy = sum(
        (row.get("buy_actual") or 0.0)
        for row in rows
        if row.get("pair_cost") is not None and row["pair_cost"] >= 1.0
    )
    good_buy = sum(
        (row.get("buy_actual") or 0.0)
        for row in rows
        if row.get("pair_cost") is not None and row["pair_cost"] < 0.98
    )
    wins = sum(1 for row in rows if (row.get("cash_pnl") or 0.0) > 0)
    losses = sum(1 for row in rows if (row.get("cash_pnl") or 0.0) <= 0)
    return {
        "markets": len(rows),
        "buy_actual": round6(buy_actual),
        "cash_pnl": round6(cash_pnl),
        "roi": round6(cash_pnl / buy_actual) if buy_actual else None,
        "pair_pnl": round6(pair_pnl),
        "residual_pnl": round6(residual_pnl),
        "fee": round6(fee),
        "fee_rate": round6(fee / (buy_actual - fee)) if buy_actual and buy_actual != fee else None,
        "paired_qty": round6(paired_qty),
        "resid_qty": round6(resid_qty),
        "resid_rate": round6(resid_qty / buy_qty) if buy_qty else None,
        "pair_cost": round6(weighted_pair_cost(rows)),
        "bad_pc_ge_100_share": round6(bad_buy / buy_actual) if buy_actual else None,
        "good_pc_lt_098_share": round6(good_buy / buy_actual) if buy_actual else None,
        "win_loss": f"{wins}/{losses}",
    }


def group_by(rows: list[dict[str, Any]], keys: list[str]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[tuple(as_str(row.get(key)) for key in keys)].append(row)
    out: list[dict[str, Any]] = []
    total_buy = sum((row.get("buy_actual") or 0.0) for row in rows)
    total_markets = len(rows)
    for values, bucket_rows in buckets.items():
        item = {key: value for key, value in zip(keys, values)}
        item.update(summarize_rows(bucket_rows))
        item["buy_coverage"] = round6((item["buy_actual"] or 0.0) / total_buy) if total_buy else None
        item["market_coverage"] = round6((item["markets"] or 0) / total_markets) if total_markets else None
        out.append(item)
    out.sort(key=lambda x: (x.get("cash_pnl") or 0.0), reverse=True)
    return out


def filter_rows(rows: list[dict[str, Any]], predicate: Callable[[dict[str, Any]], bool]) -> list[dict[str, Any]]:
    return [row for row in rows if predicate(row)]


def profile_candidate_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    definitions: list[tuple[str, str, str, Callable[[dict[str, Any]], bool], str, str]] = [
        (
            "NAGI_FULL_RECENT_ACCOUNT",
            "最近 96h 全账户",
            "public_result_only",
            lambda r: True,
            "整体表现，不是可直接复制的策略",
            "KEEP_AS_BASELINE",
        ),
        (
            "NAGI_DOWN_FIRST_CORE",
            "首腿 DOWN",
            "post_fill_observable_public_proxy",
            lambda r: r.get("first_side") == "DOWN",
            "最近最强主线；收益主要来自 residual",
            "KEEP_FOR_SIDE_GATE",
        ),
        (
            "NAGI_UP_FIRST_WEAK",
            "首腿 UP",
            "post_fill_observable_public_proxy",
            lambda r: r.get("first_side") == "UP",
            "仍盈利但明显弱于 DOWN first",
            "KEEP_AS_SECONDARY_ONLY",
        ),
        (
            "NAGI_RESID_UP_FAVORABLE",
            "残仓为 UP",
            "outcome_inventory_label",
            lambda r: r.get("resid_side") == "UP",
            "公开结果显示 residual UP 更优；需转成事前方向模型",
            "TRANSLATE_TO_DIRECTIONAL_RESIDUAL_MODEL",
        ),
        (
            "NAGI_DOWN_FIRST_RESID_UP",
            "首腿 DOWN / 残仓 UP",
            "mixed_post_fill_and_outcome_label",
            lambda r: r.get("first_side") == "DOWN" and r.get("resid_side") == "UP",
            "近期最高质量组合之一，但 resid_side 是结果标签",
            "KEEP_AS_MODEL_TARGET",
        ),
        (
            "NAGI_UP_FIRST_RESID_DOWN_AVOID",
            "首腿 UP / 残仓 DOWN",
            "mixed_post_fill_and_outcome_label",
            lambda r: r.get("first_side") == "UP" and r.get("resid_side") == "DOWN",
            "近期明确亏损桶，优先做 kill switch",
            "REJECT_OR_HARD_KILL",
        ),
        (
            "NAGI_LAST60_35_50_PAIR_CONTROL",
            "最后 60s，首价 35-50",
            "post_fill_observable_template",
            lambda r: r.get("last_delta_bucket") == "last_60s"
            and r.get("first_price_bucket") == "35-50",
            "覆盖大、残仓低，适合作为 maker queue 主模板",
            "KEEP_FOR_SHADOW_POLICY",
        ),
        (
            "NAGI_1_5M_50_65_RESIDUAL_ENGINE",
            "1-5m，首价 50-65",
            "post_fill_observable_high_residual",
            lambda r: r.get("last_delta_bucket") == "1-5m"
            and r.get("first_price_bucket") == "50-65",
            "ROI 高但 residual 高，是方向暴露而非纯配对",
            "KEEP_FOR_RESIDUAL_RESEARCH_ONLY",
        ),
        (
            "NAGI_LAST60_FASTPAIR_LE5",
            "最后 60s，配对 <=5s",
            "outcome_or_control_label",
            lambda r: r.get("last_delta_bucket") == "last_60s"
            and r.get("pair_delay_bucket") == "<=5s",
            "表现好但 pair_delay 不能直接当入场信号",
            "TRANSLATE_TO_TIMEOUT_AND_REPAIR_POLICY",
        ),
        (
            "NAGI_LAST60_PAIR_5_15",
            "最后 60s，配对 5-15s",
            "outcome_or_control_label",
            lambda r: r.get("last_delta_bucket") == "last_60s"
            and r.get("pair_delay_bucket") == "5-15s",
            "稳健正收益，适合转成 repair window",
            "KEEP_FOR_REPAIR_POLICY",
        ),
        (
            "NAGI_LAST60_PAIR_15_30_WEAK",
            "最后 60s，配对 15-30s",
            "outcome_or_control_label",
            lambda r: r.get("last_delta_bucket") == "last_60s"
            and r.get("pair_delay_bucket") == "15-30s",
            "近期亏损，说明最后 60s 不能拖太久",
            "REJECT_OR_TIMEOUT",
        ),
        (
            "NAGI_LAST60_PAIR_1_3M_BAD",
            "最后 60s，配对 1-3m",
            "outcome_or_control_label",
            lambda r: r.get("last_delta_bucket") == "last_60s"
            and r.get("pair_delay_bucket") == "1-3m",
            "近期亏损最明确的时间拖延桶",
            "HARD_TIMEOUT",
        ),
        (
            "NAGI_1_5M_PAIR_5_15",
            "1-5m，配对 5-15s",
            "outcome_or_control_label",
            lambda r: r.get("last_delta_bucket") == "1-5m"
            and r.get("pair_delay_bucket") == "5-15s",
            "高 ROI 但 residual 高，需更严格风控",
            "KEEP_SMALL_SIZE_RESEARCH",
        ),
        (
            "NAGI_FIRST65_80_SMALL_SAMPLE",
            "首价 65-80",
            "post_fill_observable_small_sample",
            lambda r: r.get("first_price_bucket") == "65-80",
            "ROI 高但样本和覆盖小，不作为主策略",
            "WATCH_ONLY",
        ),
    ]
    out: list[dict[str, Any]] = []
    total_buy = sum((row.get("buy_actual") or 0.0) for row in rows)
    total_markets = len(rows)
    for cid, label, observability, pred, interpretation, decision in definitions:
        bucket = filter_rows(rows, pred)
        metrics = summarize_rows(bucket)
        metrics.update(
            {
                "candidate_id": cid,
                "label": label,
                "observability": observability,
                "interpretation": interpretation,
                "decision": decision,
                "buy_coverage": round6((metrics["buy_actual"] or 0.0) / total_buy) if total_buy else None,
                "market_coverage": round6((metrics["markets"] or 0) / total_markets) if total_markets else None,
            }
        )
        out.append(metrics)
    return out


def read_csv_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


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


def sort_for_report(rows: list[dict[str, Any]], limit: int = 12) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda x: (x.get("cash_pnl") or 0.0), reverse=True)[:limit]


def md_table(rows: list[dict[str, Any]], cols: list[tuple[str, str]], max_rows: int | None = None) -> str:
    selected = rows if max_rows is None else rows[:max_rows]
    lines = [
        "| " + " | ".join(label for _, label in cols) + " |",
        "| " + " | ".join("---" for _ in cols) + " |",
    ]
    for row in selected:
        values = []
        for key, _ in cols:
            value = row.get(key)
            if key in {"cash_pnl", "pair_pnl", "residual_pnl", "buy_actual", "fee"}:
                values.append(money(value))
            elif key in {
                "roi",
                "resid_rate",
                "bad_pc_ge_100_share",
                "good_pc_lt_098_share",
                "buy_coverage",
                "market_coverage",
                "fee_rate",
            }:
                values.append(pct(value))
            elif key in {"pair_cost"}:
                values.append(num(value, 4))
            else:
                values.append(as_str(value))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def top_market_examples(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = [
        "condition_id",
        "slug",
        "title",
        "first_trade_bjt",
        "first_side",
        "resid_side",
        "first_price_bucket",
        "last_delta_bucket",
        "pair_delay_bucket",
        "buy_actual",
        "cash_pnl",
        "pair_pnl",
        "residual_pnl_est",
        "pair_cost",
        "resid_rate",
    ]
    best = sorted(rows, key=lambda x: x.get("cash_pnl") or 0.0, reverse=True)[:20]
    worst = sorted(rows, key=lambda x: x.get("cash_pnl") or 0.0)[:20]
    out: list[dict[str, Any]] = []
    for tag, items in [("top", best), ("worst", worst)]:
        for rank, row in enumerate(items, 1):
            out.append({"rank_type": tag, "rank": rank, **{field: row.get(field) for field in fields}})
    return out


def frontier_rows() -> list[dict[str, Any]]:
    frontier = load_json(FRONTIER_PACKET)
    residual_matrix = load_json(RESIDUAL_MATRIX_PACKET)
    top_rows = read_csv_rows(FRONTIER_TOP)[:20]
    branch_rows = read_csv_rows(BRANCH_SUMMARY) if BRANCH_SUMMARY.exists() else []
    top_items = [
        {
            "candidate_id": f"FRONTIER_TOP_{rank:02d}",
            "source": "exhaustive_frontier_top_csv",
            "frontier_top_rank": rank,
            **row,
        }
        for rank, row in enumerate(top_rows, 1)
    ]
    items = [
        {
            "candidate_id": "MAKER_QUEUE_FULL300_YES_35_50",
            "source": "exhaustive_frontier_best_fee0",
            **frontier["summary"]["best_fee0_edge_variant"],
        },
        {
            "candidate_id": "MAKER_QUEUE_LAST60_YES_35_50",
            "source": "exhaustive_frontier_best_nagi_anchor",
            **frontier["summary"]["best_nagi_anchor_like_variant"],
        },
        {
            "candidate_id": "MAKER_QUEUE_LAST60_UP_35_50_PC995",
            "source": "residual_matrix_best_fee0",
            **residual_matrix["summary"]["best_fee0_variant"],
        },
    ]
    for row in branch_rows:
        items.append({"candidate_id": row.get("branch_id"), "source": "branch_summary", **row})
    return top_items + items


def market_extreme_notes(rows: list[dict[str, Any]]) -> dict[str, Any]:
    bad_pair = filter_rows(rows, lambda r: r.get("pair_cost") is not None and r["pair_cost"] >= 1.10)
    good_pair = filter_rows(rows, lambda r: r.get("pair_cost") is not None and r["pair_cost"] < 0.95)
    high_resid = filter_rows(rows, lambda r: (r.get("resid_rate") or 0.0) >= 0.35)
    low_resid = filter_rows(rows, lambda r: (r.get("resid_rate") or 0.0) < 0.05)
    return {
        "pair_cost_ge_1_10": summarize_rows(bad_pair),
        "pair_cost_lt_0_95": summarize_rows(good_pair),
        "resid_rate_ge_35pct": summarize_rows(high_resid),
        "resid_rate_lt_5pct": summarize_rows(low_resid),
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_sha256sums(root: Path, files: list[Path]) -> None:
    lines = [f"{sha256_file(path)}  {path.relative_to(root)}" for path in files]
    (root / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def render_report(
    packet: dict[str, Any],
    daily: list[dict[str, Any]],
    profile_candidates: list[dict[str, Any]],
    matrix_rows: list[dict[str, Any]],
    frontier: dict[str, Any],
) -> str:
    latest = packet["latest_public_profile"]["summary"]
    prior = packet["prior_public_profile_snapshot"]["nagi_account_rollup_4_window"]
    queue = packet["maker_queue_proxy"]["best_fee0_edge_variant"]
    anchor = packet["maker_queue_proxy"]["best_nagi_anchor_like_variant"]
    matrix_best = packet["maker_queue_proxy"]["residual_matrix_best_fee0_variant"]
    extreme = packet["market_extreme_notes"]

    def candidate(cid: str) -> dict[str, Any]:
        for row in profile_candidates:
            if row["candidate_id"] == cid:
                return row
        raise KeyError(cid)

    keep_rows = [
        candidate("NAGI_DOWN_FIRST_CORE"),
        candidate("NAGI_DOWN_FIRST_RESID_UP"),
        candidate("NAGI_LAST60_35_50_PAIR_CONTROL"),
        candidate("NAGI_1_5M_50_65_RESIDUAL_ENGINE"),
        candidate("NAGI_LAST60_FASTPAIR_LE5"),
        candidate("NAGI_LAST60_PAIR_5_15"),
        candidate("NAGI_1_5M_PAIR_5_15"),
        candidate("NAGI_FIRST65_80_SMALL_SAMPLE"),
    ]
    reject_rows = [
        candidate("NAGI_UP_FIRST_RESID_DOWN_AVOID"),
        candidate("NAGI_LAST60_PAIR_15_30_WEAK"),
        candidate("NAGI_LAST60_PAIR_1_3M_BAD"),
    ]
    cols = [
        ("candidate_id", "candidate"),
        ("markets", "markets"),
        ("buy_coverage", "buy cov"),
        ("buy_actual", "buy actual"),
        ("cash_pnl", "cash pnl"),
        ("roi", "roi"),
        ("pair_cost", "pair cost"),
        ("pair_pnl", "pair pnl"),
        ("residual_pnl", "residual pnl"),
        ("resid_rate", "resid"),
        ("bad_pc_ge_100_share", "bad pc>=1"),
        ("decision", "decision"),
    ]
    group_cols = [
        ("group", "group"),
        ("bucket", "bucket"),
        ("markets", "markets"),
        ("buy_coverage", "buy cov"),
        ("cash_pnl", "cash pnl"),
        ("roi", "roi"),
        ("pair_cost", "pair cost"),
        ("pair_pnl", "pair pnl"),
        ("residual_pnl", "residual pnl"),
        ("resid_rate", "resid"),
        ("bad_pc_ge_100_share", "bad pc>=1"),
    ]
    matrix_for_md = []
    for row in matrix_rows:
        label_keys = [k for k in row if k in {"first_side", "resid_side", "first_price_bucket", "last_delta_bucket", "pair_delay_bucket"}]
        bucket = " / ".join(as_str(row.get(k)) for k in label_keys)
        matrix_for_md.append({"group": row.get("group"), "bucket": bucket, **row})

    lines = [
        "# NAGI777 逆向研究与超越路线",
        "",
        f"Status: `{STATUS}`",
        "",
        "## 0. 一句话结论",
        "",
        (
            "把 ce25 放下后，nagi777 是当前更值得继续研究的对象，但不能把它理解为"
            "简单 pair-arb。最近 96h 的公开 activity 显示：它是盈利的，"
            f"fee-inclusive cash PnL 为 {money(latest['cash_pnl'])}，ROI {pct(latest['roi'])}；"
            f"但 pair PnL 只有 {money(latest['pair_pnl'])}，"
            f"residual PnL 是 {money(latest['residual_pnl'])}。"
            "所以它最近真正赚钱的位置在 residual/inventory，而不是无脑买 YES+NO merge。"
        ),
        "",
        (
            "可复刻的核心不是“追 taker 成交”，而是："
            "post-only / maker / 零费队列捕获 + 残仓方向模型 + 超时 repair/kill switch。"
            "本地 maker-queue frontier 里 fee0 有大面积正边，但官方 taker fee07 规模化全灭；"
            "这也是为什么直接复制公开成交会失败。"
        ),
        "",
        "## 1. 来源与边界",
        "",
        f"- 账户：`{ACCOUNT}`",
        f"- 最新公开 activity profile：`{PROFILE}`",
        f"- 最新窗口：{latest['window']['start_bjt']} -> {latest['window']['end_bjt']} BJT",
        f"- activity rows：{latest['activity_rows']:,}；markets：{latest['market_count']:,}",
        "- 指标口径：BUY 成本使用 `usdcSize`，PnL 是 fee-inclusive public cash PnL。",
        "- 不能从公开 activity 证明第三方真实 maker/taker、私有排队位置、撤单逻辑或 authenticated trader_side。",
        "- 本报告不授权、不准备、不建议任何下单；它只是给实现同事的研究规格。",
        "",
        "## 2. 最近 96h：nagi 真的在赚钱，但赚钱结构变了",
        "",
        "| 窗口 | markets | buy actual | cash pnl | ROI | pair cost | pair pnl | residual pnl | resid | fee | bad pc>=1 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        (
            f"| 2026-06-04 11:10 -> 2026-06-08 11:10 BJT | {latest['market_count']} | "
            f"{money(latest['buy_actual'])} | {money(latest['cash_pnl'])} | {pct(latest['roi'])} | "
            f"{num(latest['avg_pair_cost_weighted'], 4)} | {money(latest['pair_pnl'])} | "
            f"{money(latest['residual_pnl'])} | {pct(latest['resid_rate'])} | {money(latest['fee'])} | "
            f"{pct(latest['bad_pc_ge_100_share'])} |"
        ),
        (
            f"| 旧 4-window 快照 | {prior['markets']} | {money(float(prior['buy_actual']))} | "
            f"{money(float(prior['cash_pnl']))} | {pct(float(prior['roi']))} | "
            f"{num(float(prior['pair_cost']), 4)} | {money(float(prior['pair_pnl']))} | "
            f"{money(float(prior['residual_pnl']))} | {pct(float(prior['resid_rate']))} | "
            f"{pct(float(prior['fee_rate']))} | {pct(float(prior['bad_pc_ge_100_share']))} |"
        ),
        "",
        "解释：旧快照是 pair PnL 大正、residual 大负；最新 96h 变成 pair 接近不赚钱、residual 大正。这说明 nagi 的近期强势不是稳定无风险套利，而是残仓方向暴露打对了。要超越它，必须比它更少吃坏 residual，而不是只模仿配对。",
        "",
        "### 日级拆分",
        "",
        md_table(
            daily,
            [
                ("day_bjt", "BJT day"),
                ("markets", "markets"),
                ("buy_actual", "buy actual"),
                ("cash_pnl", "cash pnl"),
                ("roi", "roi"),
                ("pair_cost", "pair cost"),
                ("pair_pnl", "pair pnl"),
                ("residual_pnl", "residual pnl"),
                ("resid_rate", "resid"),
                ("bad_pc_ge_100_share", "bad pc>=1"),
            ],
        ),
        "",
        "## 3. nagi 的强桶与弱桶",
        "",
        "### 保留研究桶",
        "",
        md_table(keep_rows, cols),
        "",
        "### 明确要砍掉或加硬超时的桶",
        "",
        md_table(reject_rows, cols),
        "",
        "几个关键读法：",
        "",
        "- `NAGI_DOWN_FIRST_CORE` 是最新主线：首腿 DOWN 覆盖 48.9% markets，PnL +$9.29k，ROI 2.64%。",
        "- `NAGI_UP_FIRST_RESID_DOWN_AVOID` 是最明确的失败桶：PnL -$1.24k，pair cost 1.0183；这应该成为第一条 kill switch。",
        "- `NAGI_LAST60_35_50_PAIR_CONTROL` 覆盖大、残仓低，是最像可执行主模板的 public bucket。",
        "- `NAGI_1_5M_50_65_RESIDUAL_ENGINE` ROI 高，但 residual 高达 26%+，更像方向模型，不应当用大仓直接复制。",
        "- pair_delay 是结果/控制变量，不能直接当入场信号；只能转译为 own-fill 后的 repair timeout。",
        "",
        "### 诊断矩阵 top rows",
        "",
        md_table(matrix_for_md[:18], group_cols),
        "",
        "## 4. 本地 maker queue 代理：为什么 taker 复刻会死",
        "",
        "| proxy | time | side | band | queue markets | queue qty | fee0 edge qty | taker07 edge qty | pair cost p50 | queue market share | p99 touch lag | p99 align lag |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        (
            f"| best broad | {queue['time_id']} | {queue['side']} | {queue['px_lo']}-{queue['px_hi']} | "
            f"{queue['queue_markets']} | {num(queue['queue_qty_sum'], 2)} | {num(queue['queue_edge_qty_sum_fee0'], 2)} | "
            f"{num(queue['queue_edge_qty_sum_taker_fee07'], 2)} | {num(queue['queue_pair_cost_p50'], 4)} | "
            f"{pct(queue['queue_market_share'])} | {num(queue['touch_after_quote_ms_p99'], 2)}ms | {num(queue['align_lag_ms_p99'], 2)}ms |"
        ),
        (
            f"| nagi anchor | {anchor['time_id']} | {anchor['side']} | {anchor['px_lo']}-{anchor['px_hi']} | "
            f"{anchor['queue_markets']} | {num(anchor['queue_qty_sum'], 2)} | {num(anchor['queue_edge_qty_sum_fee0'], 2)} | "
            f"{num(anchor['queue_edge_qty_sum_taker_fee07'], 2)} | {num(anchor['queue_pair_cost_p50'], 4)} | "
            f"{pct(anchor['queue_market_share'])} | {num(anchor['touch_after_quote_ms_p99'], 2)}ms | {num(anchor['align_lag_ms_p99'], 2)}ms |"
        ),
        (
            f"| residual matrix best | last60 | {matrix_best['side']} | {matrix_best['px_lo']}-{matrix_best['px_hi']} | "
            f"{matrix_best['queue_markets']} | {num(matrix_best['queue_qty_sum'], 2)} | {num(matrix_best['queue_edge_qty_sum_fee0'], 2)} | "
            f"{num(matrix_best['queue_edge_qty_sum_taker_fee07'], 2)} | {num(matrix_best['queue_pair_cost_p50'], 4)} | "
            f"{pct(matrix_best['queue_market_share'])} | {num(matrix_best['touch_after_quote_ms_p99'], 2)}ms | {num(matrix_best['align_lag_ms_p99'], 2)}ms |"
        ),
        "",
        (
            f"frontier 扫描 {frontier['summary']['variant_count']} 个 variants："
            f"fee0 scale pass {frontier['summary']['fee0_scale_pass_count']}，"
            f"fee0 high coverage pass {frontier['summary']['fee0_high_coverage_pass_count']}，"
            f"taker fee07 scale pass {frontier['summary']['taker_fee07_scale_pass_count']}。"
            "这基本把路径分清了：若我们付 taker fee，nagi 这类策略不应作为可复刻路线；若我们能证明 post-only maker 零费成交，才有研究价值。"
        ),
        "",
        "## 5. 超越 nagi 的最小可执行研究规格",
        "",
        "### P0: 不碰 taker，先证明自己的 maker fill",
        "",
        "- 所有候选都必须 post-only maker-only；任何 taker/ambiguous fill 直接失败。",
        "- 必须记录 own authenticated telemetry：intent、submit、ack、fill、maker/taker flag、fee、cancel ack、book age、queue proxy、pair_cost_at_decision。",
        "- 没有 own truth 之前，只能叫 shadow policy，不能叫复刻成功。",
        "",
        "### P1: 主队列模板",
        "",
        "- 起点模板：BTC 5m，YES bid 0.35-0.50，pair_cap <= 0.995 或 1.000，final 60s 优先，qmin 先从 0/1/5 分层。",
        "- 更广覆盖模板：full300 YES 0.35-0.50 仅作候选池，不直接大仓；它覆盖高但更依赖 residual 风控。",
        "- 目标不是吃完所有信号，而是只吃能证明 maker/no-fee 且 pair_cost 可修复的触点。",
        "",
        "### P2: residual killer，比 nagi 更强的关键",
        "",
        "- 第一条 hard kill：避免或极小仓参与 `UP first -> DOWN residual` 结构。",
        "- 最后 60s 后，repair 超时必须硬：15-30s 开始降权，1-3m 直接硬止损或不再扩大残仓。",
        "- 如果 first leg 后 5-15s 内无法以 pair_cost <= 0.995/1.000 修复，仓位进入 residual-risk 模式，后续只允许降风险，不允许追单摊大。",
        "- residual 方向模型至少要解释为什么最近 UP residual 明显优于 DOWN residual；否则这部分利润不可复刻。",
        "",
        "### P3: 目标函数",
        "",
        "- 第一目标：fee0 maker fill truth rate，而不是前端 PnL。",
        "- 第二目标：bad_pc>=1 share 从 nagi 当前约 49% 降到 <35%。",
        "- 第三目标：resid_rate 控在 <10%-12%，同时保留 `DOWN first` 的收益优势。",
        "- 第四目标：以 7 个滚动 24h 窗口验证，而不是只看一天 winner。",
        "",
        "## 6. 失败模式清单",
        "",
        f"- pair_cost >= 1.10 的市场：{extreme['pair_cost_ge_1_10']['markets']} 个，"
        f"PnL {money(extreme['pair_cost_ge_1_10']['cash_pnl'])}，"
        f"pair_cost {num(extreme['pair_cost_ge_1_10']['pair_cost'], 4)}。",
        f"- high residual >=35% 的市场：{extreme['resid_rate_ge_35pct']['markets']} 个，"
        f"PnL {money(extreme['resid_rate_ge_35pct']['cash_pnl'])}，"
        f"resid {pct(extreme['resid_rate_ge_35pct']['resid_rate'])}。",
        f"- pair_cost <0.95 的市场：{extreme['pair_cost_lt_0_95']['markets']} 个，"
        f"PnL {money(extreme['pair_cost_lt_0_95']['cash_pnl'])}，"
        f"这是可复刻收益池的主要训练目标。",
        "",
        "## 7. 交给实现同事的下一步",
        "",
        "1. 先实现 dry-run private maker shadow，不发单也不撤单，只记录如果发 post-only 会在哪里、以什么价、是否会被 taker 化。",
        "2. 用同一套 telemetry 重放 `MAKER_QUEUE_LAST60_YES_35_50`，验证 own queue touch/fill 率是否接近本地代理。",
        "3. 把 `NAGI_UP_FIRST_RESID_DOWN_AVOID`、`LAST60_PAIR_15_30_WEAK`、`LAST60_PAIR_1_3M_BAD` 转成 kill switch。",
        "4. 再做 7 个滚动 24h OOS shadow，不达标不进入真实交易讨论。",
        "",
        "## 8. 机器可读产物",
        "",
        f"- Packet JSON: `{OUT / 'NAGI777_REVERSE_ENGINEERING_PACKET.json'}`",
        f"- Candidate buckets: `{OUT / 'nagi777_profile_candidate_buckets.csv'}`",
        f"- Group matrix: `{OUT / 'nagi777_group_matrix.csv'}`",
        f"- Daily summary: `{OUT / 'nagi777_daily_summary.csv'}`",
        f"- Top/worst examples: `{OUT / 'nagi777_top_market_examples.csv'}`",
        f"- Frontier candidates: `{OUT / 'nagi777_maker_queue_candidates.csv'}`",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    DOC.parent.mkdir(parents=True, exist_ok=True)

    profile_summary = load_json(PROFILE_SUMMARY)
    rows = load_market_rows(PROFILE_MARKETS)
    pivot = load_json(PIVOT_PACKET)
    frontier = load_json(FRONTIER_PACKET)
    residual_matrix = load_json(RESIDUAL_MATRIX_PACKET)

    profile_totals = summarize_rows(rows)
    profile_summary_for_packet = {
        **profile_summary,
        "roi": profile_totals["roi"],
        "pair_pnl": profile_totals["pair_pnl"],
        "residual_pnl": profile_totals["residual_pnl"],
        "bad_pc_ge_100_share": profile_totals["bad_pc_ge_100_share"],
        "good_pc_lt_098_share": profile_totals["good_pc_lt_098_share"],
    }

    daily_rows = []
    day_buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        day_buckets[as_str(row.get("first_trade_bjt"))[:10]].append(row)
    for day, day_rows in sorted(day_buckets.items()):
        daily_rows.append({"day_bjt": day, **summarize_rows(day_rows)})

    group_matrix: list[dict[str, Any]] = []
    for group_name, keys in [
        ("first_side", ["first_side"]),
        ("resid_side", ["resid_side"]),
        ("first_side_x_resid_side", ["first_side", "resid_side"]),
        ("first_price_x_last_delta", ["first_price_bucket", "last_delta_bucket"]),
        ("first_price_x_pair_delay", ["first_price_bucket", "pair_delay_bucket"]),
        ("last_delta_x_pair_delay", ["last_delta_bucket", "pair_delay_bucket"]),
    ]:
        for item in group_by(rows, keys):
            group_matrix.append({"group": group_name, **item})

    profile_candidates = profile_candidate_rows(rows)
    frontier_candidate_rows = frontier_rows()
    examples = top_market_examples(rows)
    extreme_notes = market_extreme_notes(rows)

    packet = {
        "status": STATUS,
        "generated_at": utc_now(),
        "account": ACCOUNT,
        "scope": {
            "research_only": True,
            "orders_authorized": False,
            "private_key_authorized": False,
            "private_maker_truth_claimed": False,
            "frontend_ui_pnl_used": False,
        },
        "sources": {
            "builder": binding(BUILDER),
            "profile_summary": binding(PROFILE_SUMMARY),
            "profile_markets": binding(PROFILE_MARKETS),
            "pivot_packet": binding(PIVOT_PACKET),
            "frontier_packet": binding(FRONTIER_PACKET),
            "residual_matrix_packet": binding(RESIDUAL_MATRIX_PACKET),
            "branch_summary": binding(BRANCH_SUMMARY),
        },
        "latest_public_profile": {"summary": profile_summary_for_packet},
        "prior_public_profile_snapshot": {
            "source": str(PIVOT_PACKET),
            "nagi_account_rollup_4_window": pivot["public_profile_evidence"]["nagi_account_rollup_4_window"],
        },
        "maker_queue_proxy": {
            "summary": {
                "variant_count": frontier["summary"]["variant_count"],
                "fee0_scale_pass_count": frontier["summary"]["fee0_scale_pass_count"],
                "fee0_high_coverage_pass_count": frontier["summary"]["fee0_high_coverage_pass_count"],
                "taker_fee07_scale_pass_count": frontier["summary"]["taker_fee07_scale_pass_count"],
            },
            "best_fee0_edge_variant": frontier["summary"]["best_fee0_edge_variant"],
            "best_nagi_anchor_like_variant": frontier["summary"]["best_nagi_anchor_like_variant"],
            "residual_matrix_best_fee0_variant": residual_matrix["summary"]["best_fee0_variant"],
        },
        "profile_candidates": profile_candidates,
        "market_extreme_notes": extreme_notes,
        "non_claims": {
            "deployable": False,
            "live_ready": False,
            "maker_fill_proven": False,
            "oos_ready": False,
            "private_truth_ready": False,
            "queue_priority_proven": False,
        },
        "recommended_next_step": (
            "Build dry-run-only private maker shadow telemetry for the last60 YES 35-50 queue policy, "
            "then validate maker fill truth and residual kill switches across seven rolling 24h windows."
        ),
    }

    files: list[Path] = []
    packet_path = OUT / "NAGI777_REVERSE_ENGINEERING_PACKET.json"
    candidate_path = OUT / "nagi777_profile_candidate_buckets.csv"
    matrix_path = OUT / "nagi777_group_matrix.csv"
    daily_path = OUT / "nagi777_daily_summary.csv"
    examples_path = OUT / "nagi777_top_market_examples.csv"
    frontier_path = OUT / "nagi777_maker_queue_candidates.csv"
    report_path = OUT / "NAGI777_REVERSE_ENGINEERING_REPORT.md"

    write_json(packet_path, packet)
    write_csv(candidate_path, profile_candidates)
    write_csv(matrix_path, group_matrix)
    write_csv(daily_path, daily_rows)
    write_csv(examples_path, examples)
    write_csv(frontier_path, frontier_candidate_rows)

    top_matrix = sorted(
        [row for row in group_matrix if row.get("markets", 0) >= 10],
        key=lambda x: x.get("cash_pnl") or 0.0,
        reverse=True,
    )
    report = render_report(packet, daily_rows, profile_candidates, top_matrix, frontier)
    report_path.write_text(report + "\n", encoding="utf-8")
    DOC.write_text(report + "\n", encoding="utf-8")

    files.extend([packet_path, candidate_path, matrix_path, daily_path, examples_path, frontier_path, report_path])
    write_sha256sums(OUT, files)

    print(json.dumps({"status": STATUS, "out": str(OUT), "doc": str(DOC)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
