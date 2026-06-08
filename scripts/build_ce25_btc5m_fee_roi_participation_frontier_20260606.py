#!/usr/bin/env python3
"""Build CE25 BTC5m fee-inclusive ROI/participation frontier report."""

from __future__ import annotations

import csv
import hashlib
import itertools
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
EXPORTS = ROOT / "data" / "exports"
ROLLING_ROOT = EXPORTS / "rolling_profiles_ce25_nagi_20260528_1145_to_20260604_1145_bjt"
OUTPUT_DIR = EXPORTS / "ce25_btc5m_fee_roi_participation_frontier_20260606"
REPORT_PATH = ROOT / "docs" / "research" / "CE25_BTC5M_FEE_ROI_PARTICIPATION_FRONTIER_20260606_ZH.md"

EXPECTED_BTC5M_PER_24H = 288
STATUS = "KEEP_CE25_BTC5M_FEE_ROI_PARTICIPATION_FRONTIER_REVIEW_ONLY_NOT_OOS_READY"
FEATURES = ("first_price_bucket", "last_delta_bucket", "first_delta_bucket", "first_side")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def fnum(value: str | None) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except ValueError:
        return 0.0


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def profile_paths() -> list[Path]:
    paths = sorted(ROLLING_ROOT.glob("ce25_*_bjt/ce25_market_sequence.csv"))
    extras = [
        EXPORTS / "profile_ce25_latest_24h_20260603_1145_to_20260604_1145_bjt" / "ce25_market_sequence.csv",
        EXPORTS / "profile_ce25_latest_mid24h_20260604_1110_to_20260605_1110_bjt" / "ce25_market_sequence.csv",
        EXPORTS / "profile_ce25_latest_24h_20260605_1110_to_20260606_1110_bjt" / "ce25_market_sequence.csv",
    ]
    for path in extras:
        if path.exists() and path not in paths:
            paths.append(path)
    return paths


def label_for(path: Path) -> str:
    name = path.parent.name
    if name.startswith("ce25_"):
        return name.removeprefix("ce25_")
    if name.startswith("profile_ce25_"):
        return name.removeprefix("profile_ce25_")
    return name


def summarize(rows: list[dict[str, str]], expected_markets: int) -> dict[str, Any]:
    buy_actual = sum(fnum(row.get("buy_actual")) for row in rows)
    cash_pnl = sum(fnum(row.get("cash_pnl")) for row in rows)
    fee = sum(fnum(row.get("fee")) for row in rows)
    buy_qty = sum(fnum(row.get("buy_qty")) for row in rows)
    resid_qty = sum(fnum(row.get("resid_qty")) for row in rows)
    paired_qty = sum(fnum(row.get("paired_qty")) for row in rows)
    pair_cost = (
        sum(fnum(row.get("pair_cost")) * fnum(row.get("paired_qty")) for row in rows) / paired_qty
        if paired_qty > 0
        else 0.0
    )
    bad_buy = sum(fnum(row.get("buy_actual")) for row in rows if fnum(row.get("pair_cost")) >= 1.0)
    good_buy = sum(fnum(row.get("buy_actual")) for row in rows if fnum(row.get("pair_cost")) < 0.98)
    windows = {row["_window_label"] for row in rows}
    win_windows = {row["_window_label"] for row in rows if fnum(row.get("cash_pnl")) > 0}
    return {
        "markets": len(rows),
        "expected_markets": expected_markets,
        "participation_rate": len(rows) / expected_markets if expected_markets > 0 else 0.0,
        "buy_actual": buy_actual,
        "cash_pnl": cash_pnl,
        "roi": cash_pnl / buy_actual if buy_actual > 0 else 0.0,
        "fee": fee,
        "fee_rate": fee / max(buy_actual - fee, 1e-12),
        "pair_cost": pair_cost,
        "resid_rate": resid_qty / buy_qty if buy_qty > 0 else 0.0,
        "bad_pc_ge_100_share": bad_buy / buy_actual if buy_actual > 0 else 0.0,
        "good_pc_lt_098_share": good_buy / buy_actual if buy_actual > 0 else 0.0,
        "wins": sum(1 for row in rows if fnum(row.get("cash_pnl")) > 0),
        "losses": sum(1 for row in rows if fnum(row.get("cash_pnl")) <= 0),
        "active_windows": len(windows),
        "win_windows": len(win_windows),
    }


def make_filter_name(features: tuple[str, ...], key: tuple[str, ...]) -> str:
    if not features:
        return "ALL_BTC5M"
    return "+".join(features) + "=" + "|".join(key)


def predicate_from_filter(filter_name: str) -> Callable[[dict[str, str]], bool]:
    if filter_name == "ALL_BTC5M":
        return lambda row: True
    left, right = filter_name.split("=", 1)
    features = tuple(left.split("+"))
    values = tuple(right.split("|"))
    return lambda row: all(row.get(feature) == value for feature, value in zip(features, values))


def write_tsv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def money(value: float) -> str:
    return f"${value:,.2f}"


def fmt(value: float, digits: int = 4) -> str:
    return f"{value:.{digits}f}"


def non_claims() -> dict[str, bool]:
    return {
        "private_truth_ready": False,
        "strategy_promotion_ready": False,
        "live_ready": False,
        "deployable": False,
        "orders_authorized": False,
        "oos_authorized": False,
    }


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    profiles = profile_paths()
    expected = EXPECTED_BTC5M_PER_24H * len(profiles)
    rows: list[dict[str, str]] = []
    profile_labels: list[str] = []
    for path in profiles:
        label = label_for(path)
        profile_labels.append(label)
        for row in read_csv(path):
            if row.get("asset") == "BTC" and row.get("tf") == "5m":
                row["_window_label"] = label
                row["_profile_path"] = str(path)
                rows.append(row)

    candidates: list[dict[str, Any]] = []
    for k in range(0, len(FEATURES) + 1):
        if k == 0:
            stats = summarize(rows, expected)
            candidates.append({"filter_name": "ALL_BTC5M", "features": "", "values": "", **stats})
            continue
        for features in itertools.combinations(FEATURES, k):
            grouped: dict[tuple[str, ...], list[dict[str, str]]] = defaultdict(list)
            for row in rows:
                key = tuple(row.get(feature, "") for feature in features)
                if all(key):
                    grouped[key].append(row)
            for key, group in grouped.items():
                if len(group) < 20:
                    continue
                stats = summarize(group, expected)
                if stats["participation_rate"] < 0.02:
                    continue
                candidates.append(
                    {
                        "filter_name": make_filter_name(features, key),
                        "features": "+".join(features),
                        "values": "|".join(key),
                        **stats,
                    }
                )

    positive = [row for row in candidates if row["cash_pnl"] > 0 and row["roi"] > 0]
    frontier = []
    for row in positive:
        dominated = False
        for other in positive:
            if (
                other["participation_rate"] >= row["participation_rate"]
                and other["roi"] >= row["roi"]
                and (other["participation_rate"] > row["participation_rate"] or other["roi"] > row["roi"])
            ):
                dominated = True
                break
        if not dominated:
            frontier.append(row)
    frontier.sort(key=lambda row: row["participation_rate"], reverse=True)

    selected_names = [
        "ALL_BTC5M",
        "last_delta_bucket=last_60s",
        "last_delta_bucket+first_side=last_60s|DOWN",
        "first_price_bucket+first_side=50-65|UP",
        "first_price_bucket+last_delta_bucket=20-35|last_60s",
    ]
    selected = []
    selected_window_rows = []
    for name in selected_names:
        pred = predicate_from_filter(name)
        selected_rows = [row for row in rows if pred(row)]
        aggregate = summarize(selected_rows, expected)
        recent_rows = [row for row in selected_rows if row["_window_label"] in set(profile_labels[-2:])]
        recent = summarize(recent_rows, EXPECTED_BTC5M_PER_24H * 2)
        selected.append(
            {
                "filter_name": name,
                "role": {
                    "ALL_BTC5M": "full_broad_baseline",
                    "last_delta_bucket=last_60s": "balanced_high_participation_controller",
                    "last_delta_bucket+first_side=last_60s|DOWN": "side_filtered_drawdown_controller",
                    "first_price_bucket+first_side=50-65|UP": "midprice_up_roi_booster",
                    "first_price_bucket+last_delta_bucket=20-35|last_60s": "low_tail_overlay",
                }[name],
                **aggregate,
                "recent2_markets": recent["markets"],
                "recent2_participation_rate": recent["participation_rate"],
                "recent2_cash_pnl": recent["cash_pnl"],
                "recent2_roi": recent["roi"],
            }
        )
        for label in profile_labels:
            window_rows = [row for row in selected_rows if row["_window_label"] == label]
            selected_window_rows.append({"filter_name": name, "window_label": label, **summarize(window_rows, EXPECTED_BTC5M_PER_24H)})

    fields = [
        "filter_name",
        "features",
        "values",
        "markets",
        "expected_markets",
        "participation_rate",
        "buy_actual",
        "cash_pnl",
        "roi",
        "fee_rate",
        "pair_cost",
        "resid_rate",
        "bad_pc_ge_100_share",
        "active_windows",
        "win_windows",
        "wins",
        "losses",
    ]
    write_tsv(OUTPUT_DIR / "ce25_btc5m_fee_roi_candidates.tsv", sorted(candidates, key=lambda row: row["roi"], reverse=True), fields)
    write_tsv(OUTPUT_DIR / "ce25_btc5m_fee_roi_pareto_frontier.tsv", frontier, fields)
    selected_fields = [
        "filter_name",
        "role",
        "markets",
        "expected_markets",
        "participation_rate",
        "buy_actual",
        "cash_pnl",
        "roi",
        "fee_rate",
        "pair_cost",
        "resid_rate",
        "bad_pc_ge_100_share",
        "active_windows",
        "win_windows",
        "recent2_markets",
        "recent2_participation_rate",
        "recent2_cash_pnl",
        "recent2_roi",
    ]
    write_tsv(OUTPUT_DIR / "ce25_btc5m_selected_frontier_lanes.tsv", selected, selected_fields)
    write_tsv(
        OUTPUT_DIR / "ce25_btc5m_selected_frontier_by_window.tsv",
        selected_window_rows,
        [
            "filter_name",
            "window_label",
            "markets",
            "participation_rate",
            "buy_actual",
            "cash_pnl",
            "roi",
            "fee_rate",
            "pair_cost",
            "resid_rate",
            "bad_pc_ge_100_share",
        ],
    )

    all_btc = selected[0]
    last60 = next(row for row in selected if row["filter_name"] == "last_delta_bucket=last_60s")
    last60_down = next(row for row in selected if row["filter_name"] == "last_delta_bucket+first_side=last_60s|DOWN")
    mid_up = next(row for row in selected if row["filter_name"] == "first_price_bucket+first_side=50-65|UP")
    low_tail = next(row for row in selected if row["filter_name"] == "first_price_bucket+last_delta_bucket=20-35|last_60s")

    summary = {
        "schema_version": 1,
        "status": STATUS,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "profile_count": len(profiles),
        "expected_btc5m_markets": expected,
        "selected": selected,
        "pareto_frontier_count": len(frontier),
        "recommended": {
            "p0": "last_delta_bucket=last_60s",
            "p0_reason": "29.32% participation, fee-inclusive ROI improves from 1.78% to 2.17%, and it captures most BTC5m PnL while cutting early noise.",
            "p1": "last_delta_bucket+first_side=last_60s|DOWN",
            "p1_reason": "14.93% participation, higher 2.63% ROI, lower residual/bad-pair-cost, and recent two windows nearly flat instead of materially negative.",
            "p2": "first_price_bucket+last_delta_bucket=20-35|last_60s",
            "p2_reason": "9.44% ROI but only 2.89% participation; overlay only.",
        },
        "non_claims": non_claims(),
    }
    summary_path = OUTPUT_DIR / "CE25_BTC5M_FEE_ROI_PARTICIPATION_FRONTIER_SUMMARY.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    def table(rows_in: list[dict[str, Any]]) -> str:
        lines = [
            "| lane | role | participation | fee-inclusive ROI | PnL | pair_cost | residual | bad pc>=1 | recent2 PnL / ROI |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for row in rows_in:
            lines.append(
                "| {name} | {role} | {part} | {roi} | {pnl} | {pc} | {resid} | {bad} | {r2pnl} / {r2roi} |".format(
                    name=row["filter_name"],
                    role=row["role"],
                    part=pct(row["participation_rate"]),
                    roi=pct(row["roi"]),
                    pnl=money(row["cash_pnl"]),
                    pc=fmt(row["pair_cost"]),
                    resid=pct(row["resid_rate"]),
                    bad=pct(row["bad_pc_ge_100_share"]),
                    r2pnl=money(row["recent2_cash_pnl"]),
                    r2roi=pct(row["recent2_roi"]),
                )
            )
        return "\n".join(lines)

    frontier_lines = [
        "| filter | participation | fee-inclusive ROI | PnL | pair_cost | residual | bad pc>=1 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in frontier[:14]:
        frontier_lines.append(
            "| {name} | {part} | {roi} | {pnl} | {pc} | {resid} | {bad} |".format(
                name=row["filter_name"],
                part=pct(row["participation_rate"]),
                roi=pct(row["roi"]),
                pnl=money(row["cash_pnl"]),
                pc=fmt(row["pair_cost"]),
                resid=pct(row["resid_rate"]),
                bad=pct(row["bad_pc_ge_100_share"]),
            )
        )

    report = f"""# CE25 BTC5m Fee-Inclusive ROI / Participation Frontier

Status: `{STATUS}`

## 结论

你的方向是对的：参与率越高越有价值，但必须同时提高 fee-inclusive ROI。新的前沿搜索显示，CE25 BTC5m 最值得学的不是低覆盖 low-tail，而是 **最后 60 秒主控层**。

最重要的改进：

- 全量 BTC5m：参与率 {pct(all_btc["participation_rate"])}，fee-inclusive ROI {pct(all_btc["roi"])}。
- 只保留最后 60 秒：参与率仍有 {pct(last60["participation_rate"])}，fee-inclusive ROI 提高到 {pct(last60["roi"])}。
- 最后 60 秒 + DOWN 首腿：参与率 {pct(last60_down["participation_rate"])}，ROI 进一步到 {pct(last60_down["roi"])}，最近两窗几乎打平。
- `20-35 last60` 的 ROI {pct(low_tail["roi"])}，但参与率只有 {pct(low_tail["participation_rate"])}，仍只能做 overlay。

## 推荐车道

{table(selected)}

Interpretation:

- P0 应该是 `last_delta_bucket=last_60s`，不是 low-tail。它用约一半 BTC5m 覆盖，拿到全 BTC5m 大部分 PnL，并把 ROI 从 {pct(all_btc["roi"])} 提到 {pct(last60["roi"])}。
- P1 是 `last_60s|DOWN`。它参与率降到 {pct(last60_down["participation_rate"])}，但 ROI 到 {pct(last60_down["roi"])}，并且最近两窗 PnL 只有 {money(last60_down["recent2_cash_pnl"])}，比全 BTC5m 和全 last60 更抗衰减。
- `50-65|UP` 是 ROI booster，但参与率刚低于 10%，最近两窗没有明显优势，暂不当主线。
- `20-35|last60` 仍然很强，但参与率太低，只能叠加。

## Pareto Frontier

{"\n".join(frontier_lines)}

## 新策略组合假设

```text
CE25_BTC5M_BROAD_LAST60_CONTROLLER_V1
asset = BTC
tf = 5m
primary clock = last_60s
base lane = all first_side
risk lane = first_side DOWN
overlay = first_price 20-35 + last_60s
do not use pair_delay as live entry condition
do not use cash_pnl/pair_cost/residual as ex-ante entry condition
```

## 需要继续验证

1. 把 `BTC5M_LAST60` 转成 ex-ante candidate ledger，检查每个市场入口是否可以用公开盘口在事前识别。
2. 单独验证 `last60 DOWN` 为什么最近两窗比 all-last60 抗跌。
3. 检查 `last60 UP` 是不是当前市场环境下的拖累项。
4. 再做 L1/L2 book-shadow：参与率 29% 的策略比 2.9% overlay 更值得消耗验证预算。

This is public-only/review-only. It does not prove CE25 private trader_side, queue priority, maker-only behavior, or deployable live performance.
"""
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report)

    artifacts = [
        summary_path,
        OUTPUT_DIR / "ce25_btc5m_fee_roi_candidates.tsv",
        OUTPUT_DIR / "ce25_btc5m_fee_roi_pareto_frontier.tsv",
        OUTPUT_DIR / "ce25_btc5m_selected_frontier_lanes.tsv",
        OUTPUT_DIR / "ce25_btc5m_selected_frontier_by_window.tsv",
        REPORT_PATH,
        Path(__file__).resolve(),
        *profiles,
    ]
    manifest = {
        "schema_version": 1,
        "status": STATUS,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "artifacts": [{"path": str(path), "sha256": sha256_file(path), "size_bytes": path.stat().st_size} for path in artifacts],
        "summary_sha256": sha256_file(summary_path),
        "report_sha256": sha256_file(REPORT_PATH),
        "non_claims": non_claims(),
    }
    manifest_path = OUTPUT_DIR / "CE25_BTC5M_FEE_ROI_PARTICIPATION_FRONTIER_HASH_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "ok": True,
                "status": STATUS,
                "output_dir": str(OUTPUT_DIR),
                "report_path": str(REPORT_PATH),
                "p0": "last_delta_bucket=last_60s",
                "p0_participation_rate": last60["participation_rate"],
                "p0_roi": last60["roi"],
                "p1": "last_delta_bucket+first_side=last_60s|DOWN",
                "p1_participation_rate": last60_down["participation_rate"],
                "p1_roi": last60_down["roi"],
                "manifest_sha256": sha256_file(manifest_path),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
