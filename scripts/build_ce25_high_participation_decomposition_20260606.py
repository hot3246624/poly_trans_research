#!/usr/bin/env python3
"""Build CE25 high-participation decomposition report.

The goal is to separate CE25's broad public-account participation controller
from narrow high-ROI overlays. This is public-profile research only.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
EXPORTS = ROOT / "data" / "exports"
ROLLING_ROOT = EXPORTS / "rolling_profiles_ce25_nagi_20260528_1145_to_20260604_1145_bjt"
OUTPUT_DIR = EXPORTS / "ce25_high_participation_decomposition_20260606"
REPORT_PATH = ROOT / "docs" / "research" / "CE25_HIGH_PARTICIPATION_DECOMPOSITION_20260606_ZH.md"

ASSETS = ("BTC", "ETH", "SOL", "XRP")
EXPECTED_PER_24H = {"5m": 288, "15m": 96}
STATUS = "KEEP_CE25_HIGH_PARTICIPATION_MAIN_ENGINE_RESEARCH_REVIEW_ONLY_NOT_OOS_READY"


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


def summarize(rows: list[dict[str, str]]) -> dict[str, float]:
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
    return {
        "markets": float(len(rows)),
        "buy_actual": buy_actual,
        "cash_pnl": cash_pnl,
        "roi": cash_pnl / buy_actual if buy_actual > 0 else 0.0,
        "fee": fee,
        "fee_rate": fee / max(buy_actual - fee, 1e-12),
        "resid_rate": resid_qty / buy_qty if buy_qty > 0 else 0.0,
        "pair_cost": pair_cost,
        "bad_pc_ge_100_share": bad_buy / buy_actual if buy_actual > 0 else 0.0,
        "good_pc_lt_098_share": good_buy / buy_actual if buy_actual > 0 else 0.0,
        "wins": float(sum(1 for row in rows if fnum(row.get("cash_pnl")) > 0)),
        "losses": float(sum(1 for row in rows if fnum(row.get("cash_pnl")) <= 0)),
    }


def row_out(key: Any, rows: list[dict[str, str]], expected: float | None = None) -> dict[str, Any]:
    stats = summarize(rows)
    out = {"key": key, **stats}
    if expected is not None:
        out["expected_markets"] = expected
        out["participation_rate"] = stats["markets"] / expected if expected > 0 else 0.0
    return out


def group_rows(
    rows: list[dict[str, str]],
    key_func: Callable[[dict[str, str]], Any],
    expected_func: Callable[[Any], float | None] | None = None,
    min_markets: int = 1,
) -> list[dict[str, Any]]:
    grouped: dict[Any, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        key = key_func(row)
        if key is not None:
            grouped[key].append(row)
    out = []
    for key, group in grouped.items():
        if len(group) < min_markets:
            continue
        expected = expected_func(key) if expected_func else None
        out.append(row_out(key, group, expected))
    return out


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
    if not profiles:
        raise SystemExit("no CE25 profile paths found")

    profile_rows: list[dict[str, Any]] = []
    all_rows: list[dict[str, str]] = []
    for path in profiles:
        rows = read_csv(path)
        label = label_for(path)
        for row in rows:
            row["_profile_label"] = label
            row["_profile_path"] = str(path)
        all_rows.extend(rows)
        stats = summarize(rows)
        counts = defaultdict(int)
        for row in rows:
            counts[(row.get("asset"), row.get("tf"))] += 1
        profile_rows.append(
            {
                "window_label": label,
                "profile_path": str(path),
                "markets": int(stats["markets"]),
                "buy_actual": round(stats["buy_actual"], 6),
                "cash_pnl": round(stats["cash_pnl"], 6),
                "roi": round(stats["roi"], 8),
                "pair_cost": round(stats["pair_cost"], 8),
                "resid_rate": round(stats["resid_rate"], 8),
                "bad_pc_ge_100_share": round(stats["bad_pc_ge_100_share"], 8),
                "btc_5m_participation_rate": round(counts[("BTC", "5m")] / EXPECTED_PER_24H["5m"], 8),
                "all_crypto_5m_participation_rate": round(
                    sum(counts[(asset, "5m")] for asset in ASSETS) / (EXPECTED_PER_24H["5m"] * len(ASSETS)), 8
                ),
                "all_crypto_15m_participation_rate": round(
                    sum(counts[(asset, "15m")] for asset in ASSETS) / (EXPECTED_PER_24H["15m"] * len(ASSETS)), 8
                ),
            }
        )

    window_count = len(profiles)

    def asset_tf_expected(key: Any) -> float | None:
        asset, tf = key
        if asset in ASSETS and tf in EXPECTED_PER_24H:
            return EXPECTED_PER_24H[tf] * window_count
        return None

    asset_tf = group_rows(
        all_rows,
        lambda row: (row.get("asset"), row.get("tf")),
        expected_func=asset_tf_expected,
        min_markets=10,
    )
    asset_tf = [row for row in asset_tf if row["key"][0] in ASSETS and row["key"][1] in EXPECTED_PER_24H]
    asset_tf.sort(key=lambda row: row["buy_actual"], reverse=True)

    btc5_rows = [row for row in all_rows if row.get("asset") == "BTC" and row.get("tf") == "5m"]
    btc5_first_price = group_rows(
        btc5_rows,
        lambda row: row.get("first_price_bucket"),
        expected_func=lambda _key: EXPECTED_PER_24H["5m"] * window_count,
        min_markets=5,
    )
    btc5_first_price.sort(key=lambda row: row["markets"], reverse=True)

    btc5_first_last = group_rows(
        btc5_rows,
        lambda row: (row.get("first_price_bucket"), row.get("last_delta_bucket")),
        expected_func=lambda _key: EXPECTED_PER_24H["5m"] * window_count,
        min_markets=20,
    )
    btc5_first_last.sort(key=lambda row: row["markets"], reverse=True)

    full_stats = summarize(all_rows)
    latest = profile_rows[-1]
    previous_high = profile_rows[-3]
    low_tail_last60 = next(
        row for row in btc5_first_last if row["key"] == ("20-35", "last_60s")
    )
    btc5_35_50 = next(row for row in btc5_first_price if row["key"] == "35-50")
    btc5_50_65 = next(row for row in btc5_first_price if row["key"] == "50-65")

    summary = {
        "schema_version": 1,
        "status": STATUS,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "profile_count": window_count,
        "profile_paths": [str(path) for path in profiles],
        "full_public_profile": {key: round(value, 8) if isinstance(value, float) else value for key, value in full_stats.items()},
        "latest_window": latest,
        "previous_high_coverage_window": previous_high,
        "main_findings": {
            "latest_btc_5m_participation_rate": latest["btc_5m_participation_rate"],
            "previous_high_btc_5m_participation_rate": previous_high["btc_5m_participation_rate"],
            "low_tail_last60_participation_rate": low_tail_last60["participation_rate"],
            "low_tail_last60_roi": low_tail_last60["roi"],
            "btc5_35_50_participation_rate": btc5_35_50["participation_rate"],
            "btc5_35_50_roi": btc5_35_50["roi"],
            "btc5_50_65_participation_rate": btc5_50_65["participation_rate"],
            "btc5_50_65_roi": btc5_50_65["roi"],
        },
        "non_claims": non_claims(),
    }

    summary_path = OUTPUT_DIR / "CE25_HIGH_PARTICIPATION_DECOMPOSITION_SUMMARY.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    write_tsv(
        OUTPUT_DIR / "ce25_high_participation_by_window.tsv",
        profile_rows,
        [
            "window_label",
            "markets",
            "buy_actual",
            "cash_pnl",
            "roi",
            "pair_cost",
            "resid_rate",
            "bad_pc_ge_100_share",
            "btc_5m_participation_rate",
            "all_crypto_5m_participation_rate",
            "all_crypto_15m_participation_rate",
            "profile_path",
        ],
    )
    common_fields = [
        "key",
        "markets",
        "expected_markets",
        "participation_rate",
        "buy_actual",
        "cash_pnl",
        "roi",
        "pair_cost",
        "resid_rate",
        "bad_pc_ge_100_share",
        "good_pc_lt_098_share",
        "wins",
        "losses",
    ]
    write_tsv(OUTPUT_DIR / "ce25_asset_tf_summary.tsv", asset_tf, common_fields)
    write_tsv(OUTPUT_DIR / "ce25_btc5m_first_price_summary.tsv", btc5_first_price, common_fields)
    write_tsv(OUTPUT_DIR / "ce25_btc5m_first_price_last_delta_summary.tsv", btc5_first_last, common_fields)

    def table_window(rows: list[dict[str, Any]]) -> str:
        lines = [
            "| window | markets | PnL | ROI | pair_cost | resid | BTC5m participation | all 5m participation | all 15m participation |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for row in rows:
            lines.append(
                "| {window_label} | {markets} | {pnl} | {roi} | {pc} | {resid} | {btc5} | {all5} | {all15} |".format(
                    window_label=row["window_label"],
                    markets=row["markets"],
                    pnl=money(row["cash_pnl"]),
                    roi=pct(row["roi"]),
                    pc=fmt(row["pair_cost"]),
                    resid=pct(row["resid_rate"]),
                    btc5=pct(row["btc_5m_participation_rate"]),
                    all5=pct(row["all_crypto_5m_participation_rate"]),
                    all15=pct(row["all_crypto_15m_participation_rate"]),
                )
            )
        return "\n".join(lines)

    def table_key(rows: list[dict[str, Any]], limit: int | None = None) -> str:
        subset = rows if limit is None else rows[:limit]
        lines = [
            "| key | markets | participation | buy_actual | PnL | ROI | pair_cost | resid | bad pc>=1 | wins/losses |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for row in subset:
            lines.append(
                "| {key} | {markets:.0f} | {part} | {buy} | {pnl} | {roi} | {pc} | {resid} | {bad} | {wins:.0f}/{losses:.0f} |".format(
                    key=row["key"],
                    markets=row["markets"],
                    part=pct(row.get("participation_rate", 0.0)),
                    buy=money(row["buy_actual"]),
                    pnl=money(row["cash_pnl"]),
                    roi=pct(row["roi"]),
                    pc=fmt(row["pair_cost"]),
                    resid=pct(row["resid_rate"]),
                    bad=pct(row["bad_pc_ge_100_share"]),
                    wins=row["wins"],
                    losses=row["losses"],
                )
            )
        return "\n".join(lines)

    report = f"""# CE25 高参与率主引擎分解

Status: `{STATUS}`

## 结论修正

用户的质疑成立：极低参与率的高 ROI overlay，不能和 CE25 高参与率主引擎直接比较。

上一轮 `last60 low-tail top1_qty` 的 10%-13% ROI 只有约 1.5%-2.0% replay 参与率；它只能作为 alpha overlay。CE25 真正值得研究的是 **BTC 5m 高覆盖主控层**：它在 2026-06-03 11:45 -> 2026-06-04 11:45 BJT 的 BTC 5m 参与率达到 {pct(previous_high["btc_5m_participation_rate"])}，但最近 2026-06-05 11:10 -> 2026-06-06 11:10 BJT 已降到 {pct(latest["btc_5m_participation_rate"])}，且账户级 PnL 为 {money(latest["cash_pnl"])}。

所以现在的研究方向必须从“最高 ROI 小桶”转为“高覆盖主引擎 + 小型 overlay”的组合拆解。

## 数据口径

- 账户：CE25 `0xce25e214d5cfe4f459cf67f08df581885aae7fdc`
- 窗口：9 个公开 activity profile；前 7 个为 2026-05-28 11:45 -> 2026-06-04 11:45 BJT 滚动 24h，后 2 个为 2026-06-04/05 最近 24h profile。
- 参与率近似：假设每个资产每天 5m 有 288 轮、15m 有 96 轮。该口径用于解释覆盖，不代表私有订单或真实 maker 行为。
- PnL：public activity fee-inclusive `cash_pnl`，不是前端 UI PnL。

## 账户窗口覆盖

{table_window(profile_rows)}

Interpretation:

- CE25 的高覆盖不是常数。BTC 5m 从峰值 {pct(previous_high["btc_5m_participation_rate"])} 下滑到最近 {pct(latest["btc_5m_participation_rate"])}。
- 最近两窗现金 PnL 连续为负，且 pair_cost 从 0.9813 恶化到 1.0022/1.0116。
- 这说明 CE25 主引擎有市场环境/参数状态切换，不应只复制它的高覆盖行为。

## 主引擎资产/周期分解

{table_key(asset_tf)}

Interpretation:

- `BTC 5m` 是最值得继续研究的主线：覆盖 {pct(next(row for row in asset_tf if row["key"] == ("BTC", "5m"))["participation_rate"])}，9 窗口 PnL {money(next(row for row in asset_tf if row["key"] == ("BTC", "5m"))["cash_pnl"])}，ROI {pct(next(row for row in asset_tf if row["key"] == ("BTC", "5m"))["roi"])}。
- `BTC 15m` 覆盖更高，但 ROI 只有 {pct(next(row for row in asset_tf if row["key"] == ("BTC", "15m"))["roi"])}，不是优先 alpha。
- `SOL 5m` 是明显拖累项：PnL {money(next(row for row in asset_tf if row["key"] == ("SOL", "5m"))["cash_pnl"])}，pair_cost {fmt(next(row for row in asset_tf if row["key"] == ("SOL", "5m"))["pair_cost"])}。

## BTC 5m 价格桶

{table_key(btc5_first_price)}

Interpretation:

- CE25 的 BTC 5m 主覆盖来自 `35-50` 和 `50-65` 两个中价桶：合计覆盖约 {pct(btc5_35_50["participation_rate"] + btc5_50_65["participation_rate"])}。
- `20-35` 的 ROI 更高，为 {pct(next(row for row in btc5_first_price if row["key"] == "20-35")["roi"])}，但覆盖只有 {pct(next(row for row in btc5_first_price if row["key"] == "20-35")["participation_rate"])}。
- 因此 `20-35` 是 alpha overlay，不是高覆盖主引擎。

## BTC 5m 价格桶 x 时间桶

{table_key(btc5_first_last, limit=12)}

Interpretation:

- `35-50 last_60s` 与 `50-65 last_60s` 是高覆盖主引擎里更可研究的子层，覆盖分别为 {pct(next(row for row in btc5_first_last if row["key"] == ("35-50", "last_60s"))["participation_rate"])} / {pct(next(row for row in btc5_first_last if row["key"] == ("50-65", "last_60s"))["participation_rate"])}，ROI 分别为 {pct(next(row for row in btc5_first_last if row["key"] == ("35-50", "last_60s"))["roi"])} / {pct(next(row for row in btc5_first_last if row["key"] == ("50-65", "last_60s"))["roi"])}。
- `20-35 last_60s` 是上一轮 low-tail 研究对应的公开 profile 形态：覆盖 {pct(low_tail_last60["participation_rate"])}，ROI {pct(low_tail_last60["roi"])}。它很强，但太窄。
- `35-50 1-5m` 是高覆盖负贡献层，PnL {money(next(row for row in btc5_first_last if row["key"] == ("35-50", "1-5m"))["cash_pnl"])}，应作为过滤/降权对象。

## 新研究排序

1. P0: `BTC 5m broad controller`。目标不是 10%+ ROI，而是恢复/解释 50%-88% 覆盖下仍正收益的状态切换。
2. P1: `BTC 5m 50-65 last_60s` 和 `35-50 last_60s`。这是高覆盖主引擎的可学习入口。
3. P2: `BTC 5m 20-35 last_60s/top1_qty`。保留为高 ROI overlay，但不能当主策略。
4. Reject as mainline: 只追 `20-35 last60/top1_qty`。覆盖太低。
5. Reject/diagnose: `35-50 1-5m`、`SOL 5m`。它们解释 CE25 最近亏损和质量恶化。

## 下一步

下一轮应该把 BTC 5m broad controller 做成候选 ledger：用 public profile 先标注每个市场属于 `core_midprice_last60`、`negative_midprice_early`、`low_tail_overlay`、`high_price_risk_control`，再检查哪些特征是 ex-ante 可观测，哪些只是 outcome label。不能再把参与率 2% 的 overlay 当成 CE25 主体。
"""
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report)

    artifacts = [
        summary_path,
        OUTPUT_DIR / "ce25_high_participation_by_window.tsv",
        OUTPUT_DIR / "ce25_asset_tf_summary.tsv",
        OUTPUT_DIR / "ce25_btc5m_first_price_summary.tsv",
        OUTPUT_DIR / "ce25_btc5m_first_price_last_delta_summary.tsv",
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
    manifest_path = OUTPUT_DIR / "CE25_HIGH_PARTICIPATION_DECOMPOSITION_HASH_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "ok": True,
                "status": STATUS,
                "output_dir": str(OUTPUT_DIR),
                "report_path": str(REPORT_PATH),
                "profile_count": window_count,
                "latest_btc_5m_participation_rate": latest["btc_5m_participation_rate"],
                "previous_high_btc_5m_participation_rate": previous_high["btc_5m_participation_rate"],
                "low_tail_last60_participation_rate": low_tail_last60["participation_rate"],
                "btc5_35_50_participation_rate": btc5_35_50["participation_rate"],
                "btc5_50_65_participation_rate": btc5_50_65["participation_rate"],
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
