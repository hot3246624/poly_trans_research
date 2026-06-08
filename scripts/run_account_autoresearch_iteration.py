#!/usr/bin/env python3
"""Generate an autoresearch-style iteration report from account profile outputs.

This script does not fetch Polymarket data. It reads previously generated
`ce25_market_sequence.csv` profile files, scores executable proxy buckets, and
writes a repeatable research ledger/report.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import itertools
import json
import math
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FEATURES = [
    "asset",
    "tf",
    "first_delta_bucket",
    "last_delta_bucket",
    "pair_delay_bucket",
    "first_price_bucket",
    "first_side",
]
PRE_REGISTERED_PROXIES: list[dict[str, Any]] = [
    {
        "id": "ce25_btc5m_first20_35",
        "label": "ce25 BTC 5m first_price 20-35",
        "kind": "primary_alpha_candidate",
        "criteria": {"account": ["ce25"], "asset": ["BTC"], "tf": ["5m"], "first_price_bucket": ["20-35"]},
    },
    {
        "id": "ce25_btc5m_first10_20_neighbor",
        "label": "ce25 BTC 5m first_price 10-20 neighbor",
        "kind": "neighbor_control",
        "criteria": {"account": ["ce25"], "asset": ["BTC"], "tf": ["5m"], "first_price_bucket": ["10-20"]},
    },
    {
        "id": "ce25_btc5m_first35_50_neighbor",
        "label": "ce25 BTC 5m first_price 35-50 neighbor",
        "kind": "neighbor_control",
        "criteria": {"account": ["ce25"], "asset": ["BTC"], "tf": ["5m"], "first_price_bucket": ["35-50"]},
    },
    {
        "id": "nagi_last60_first35_50_fastpair",
        "label": "nagi last_60s first_price 35-50 fast pair <=15s",
        "kind": "execution_template_candidate",
        "criteria": {
            "account": ["nagi"],
            "last_delta_bucket": ["last_60s"],
            "first_price_bucket": ["35-50"],
            "pair_delay_bucket": ["<=5s", "5-15s"],
        },
    },
    {
        "id": "nagi_last60_first35_50_slowpair_control",
        "label": "nagi last_60s first_price 35-50 slow pair 15-60s",
        "kind": "execution_control",
        "criteria": {
            "account": ["nagi"],
            "last_delta_bucket": ["last_60s"],
            "first_price_bucket": ["35-50"],
            "pair_delay_bucket": ["15-30s", "30-60s"],
        },
    },
    {
        "id": "ce25_first65_80_stop_1_5m",
        "label": "ce25 first_price 65-80 stop before last minute",
        "kind": "risk_control_candidate",
        "criteria": {"account": ["ce25"], "last_delta_bucket": ["1-5m"], "first_price_bucket": ["65-80"]},
    },
    {
        "id": "ce25_first65_80_last60_control",
        "label": "ce25 first_price 65-80 last_60s control",
        "kind": "timing_control",
        "criteria": {"account": ["ce25"], "last_delta_bucket": ["last_60s"], "first_price_bucket": ["65-80"]},
    },
    {
        "id": "ce25_15m_first50_65_delay30_60_fragile",
        "label": "ce25 15m first_price 50-65 pair delay 30-60s",
        "kind": "fragile_survivorship_check",
        "criteria": {"account": ["ce25"], "tf": ["15m"], "first_price_bucket": ["50-65"], "pair_delay_bucket": ["30-60s"]},
    },
]
EPS = 1e-9


def fnum(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def money(value: float) -> str:
    sign = "+" if value >= 0 else "-"
    return f"{sign}${abs(value):,.2f}"


def now_bjt() -> dt.datetime:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def read_profile(path: Path, account: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    summary = read_json(path.parent / "summary.json")
    window = summary.get("window") if isinstance(summary.get("window"), dict) else {}
    window_label = f"{window.get('start_bjt', path.parent.name)} -> {window.get('end_bjt', path.parent.name)}"
    with path.open(newline="", encoding="utf-8") as fp:
        for row in csv.DictReader(fp):
            if fnum(row.get("buy_actual")) <= EPS:
                continue
            rows.append(
                {
                    "account": account,
                    "source_file": str(path),
                    "window_label": window_label,
                    "window_start_bjt": window.get("start_bjt", ""),
                    "window_end_bjt": window.get("end_bjt", ""),
                    **row,
                }
            )
    return rows, summary


def write_csv(path: Path, rows: list[dict[str, Any]], *, delimiter: str = ",") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields, delimiter=delimiter)
        writer.writeheader()
        writer.writerows(rows)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    buy = sum(fnum(r.get("buy_actual")) for r in rows)
    pnl = sum(fnum(r.get("cash_pnl")) for r in rows)
    pair_pnl = sum(fnum(r.get("pair_pnl")) for r in rows)
    residual_pnl = sum(fnum(r.get("residual_pnl_est")) for r in rows)
    paired = sum(fnum(r.get("paired_qty")) for r in rows)
    buy_qty = sum(fnum(r.get("buy_qty")) for r in rows)
    resid = sum(fnum(r.get("resid_qty")) for r in rows)
    fee = sum(fnum(r.get("fee")) for r in rows)
    gross = sum(max(fnum(r.get("buy_actual")) - fnum(r.get("fee")), 0.0) for r in rows)
    pair_cost = (
        sum(fnum(r.get("pair_cost")) * fnum(r.get("paired_qty")) for r in rows) / paired
        if paired > EPS
        else 0.0
    )
    good_buy = sum(fnum(r.get("buy_actual")) for r in rows if fnum(r.get("pair_cost")) < 0.98)
    bad_buy = sum(fnum(r.get("buy_actual")) for r in rows if fnum(r.get("pair_cost")) >= 1.0)
    wins = sum(1 for r in rows if fnum(r.get("cash_pnl")) > 0)
    losses = sum(1 for r in rows if fnum(r.get("cash_pnl")) < 0)
    return {
        "markets": len(rows),
        "buy_actual": round(buy, 6),
        "cash_pnl": round(pnl, 6),
        "roi": round(pnl / buy, 6) if buy > EPS else 0.0,
        "pair_pnl": round(pair_pnl, 6),
        "pair_roi": round(pair_pnl / buy, 6) if buy > EPS else 0.0,
        "residual_pnl": round(residual_pnl, 6),
        "resid_rate": round(resid / buy_qty, 6) if buy_qty > EPS else 0.0,
        "pair_cost": round(pair_cost, 6),
        "fee_rate": round(fee / gross, 6) if gross > EPS else 0.0,
        "good_pc_lt_098_share": round(good_buy / buy, 6) if buy > EPS else 0.0,
        "bad_pc_ge_100_share": round(bad_buy / buy, 6) if buy > EPS else 0.0,
        "wins": wins,
        "losses": losses,
    }


def proxy_score(rows: list[dict[str, Any]], min_buy: float, min_markets: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for account in sorted({str(r["account"]) for r in rows}):
        account_rows = [r for r in rows if r["account"] == account]
        for size in (1, 2, 3):
            for combo in itertools.combinations(FEATURES, size):
                buckets: dict[tuple[str, ...], list[dict[str, Any]]] = {}
                for row in account_rows:
                    key = tuple(str(row.get(feature) or "") for feature in combo)
                    buckets.setdefault(key, []).append(row)
                for key, bucket_rows in buckets.items():
                    summary = summarize(bucket_rows)
                    if summary["buy_actual"] < min_buy or summary["markets"] < min_markets:
                        continue
                    quality = (
                        summary["pair_roi"] * 1.5
                        + summary["roi"]
                        - summary["resid_rate"] * 0.15
                        - summary["bad_pc_ge_100_share"] * 0.10
                    )
                    out.append(
                        {
                            "account": account,
                            "features": "+".join(combo),
                            "value": "|".join(key),
                            **summary,
                            "quality_score": round(quality, 6),
                        }
                    )
    out.sort(key=lambda r: (fnum(r.get("quality_score")), fnum(r.get("buy_actual"))), reverse=True)
    return out


def safe_candidates(
    scored: list[dict[str, Any]],
    *,
    min_buy: float,
    max_resid_rate: float,
    max_bad_pc_share: float,
) -> list[dict[str, Any]]:
    safe: list[dict[str, Any]] = []
    for row in scored:
        if fnum(row.get("buy_actual")) < min_buy:
            continue
        if fnum(row.get("cash_pnl")) <= 0 or fnum(row.get("pair_pnl")) <= 0:
            continue
        if fnum(row.get("resid_rate")) >= max_resid_rate:
            continue
        if fnum(row.get("bad_pc_ge_100_share")) >= max_bad_pc_share:
            continue
        safe.append(row)
    return safe


def row_matches(row: dict[str, Any], criteria: dict[str, list[str]]) -> bool:
    for key, allowed in criteria.items():
        if str(row.get(key) or "") not in set(allowed):
            return False
    return True


def concentration(rows: list[dict[str, Any]], total_pnl: float, n: int) -> float | None:
    if total_pnl <= EPS:
        return None
    positives = sorted((fnum(row.get("cash_pnl")) for row in rows), reverse=True)
    return round(sum(max(value, 0.0) for value in positives[:n]) / total_pnl, 6)


def abs_concentration(rows: list[dict[str, Any]], n: int) -> float | None:
    abs_total = sum(abs(fnum(row.get("cash_pnl"))) for row in rows)
    if abs_total <= EPS:
        return None
    values = sorted((abs(fnum(row.get("cash_pnl"))) for row in rows), reverse=True)
    return round(sum(values[:n]) / abs_total, 6)


def add_proxy_diagnostics(summary: dict[str, Any], rows: list[dict[str, Any]], denominator: dict[str, Any]) -> dict[str, Any]:
    buy = fnum(summary.get("buy_actual"))
    total_buy = fnum(denominator.get("buy_actual"))
    one_sided_buy = sum(fnum(row.get("buy_actual")) for row in rows if fnum(row.get("paired_qty")) <= EPS)
    cashes = [fnum(row.get("cash_pnl")) for row in rows]
    out = dict(summary)
    out.update(
        {
            "buy_coverage": round(buy / total_buy, 6) if total_buy > EPS else 0.0,
            "one_sided_buy_share": round(one_sided_buy / buy, 6) if buy > EPS else 0.0,
            "max_market_loss": round(min(cashes), 6) if cashes else 0.0,
            "top1_net_share": concentration(rows, fnum(summary.get("cash_pnl")), 1),
            "top3_net_share": concentration(rows, fnum(summary.get("cash_pnl")), 3),
            "top1_abs_share": abs_concentration(rows, 1),
            "top3_abs_share": abs_concentration(rows, 3),
        }
    )
    return out


def pre_registered_proxy_rollups(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    denominators: dict[tuple[str, str], dict[str, Any]] = {}
    for account in sorted({str(row.get("account")) for row in rows}):
        for window in sorted({str(row.get("window_label")) for row in rows if row.get("account") == account}):
            key = (account, window)
            denominators[key] = summarize([row for row in rows if row.get("account") == account and row.get("window_label") == window])

    window_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []
    for proxy in PRE_REGISTERED_PROXIES:
        criteria = proxy["criteria"]
        account_values = criteria.get("account") or sorted({str(row.get("account")) for row in rows})
        proxy_all_rows: list[dict[str, Any]] = []
        profitable_windows = 0
        negative_windows = 0
        active_windows = 0
        for account in account_values:
            windows = sorted({str(row.get("window_label")) for row in rows if row.get("account") == account})
            for window in windows:
                bucket_rows = [row for row in rows if row.get("account") == account and row.get("window_label") == window and row_matches(row, criteria)]
                if not bucket_rows:
                    continue
                active_windows += 1
                proxy_all_rows.extend(bucket_rows)
                bucket_summary = add_proxy_diagnostics(summarize(bucket_rows), bucket_rows, denominators[(account, window)])
                pnl = fnum(bucket_summary.get("cash_pnl"))
                profitable_windows += 1 if pnl > 0 else 0
                negative_windows += 1 if pnl < 0 else 0
                window_rows.append(
                    {
                        "proxy_id": proxy["id"],
                        "proxy_label": proxy["label"],
                        "kind": proxy["kind"],
                        "criteria": json.dumps(criteria, ensure_ascii=False, sort_keys=True),
                        "account": account,
                        "window_label": window,
                        **bucket_summary,
                    }
                )
        if proxy_all_rows:
            # Use total buy for the proxy account(s) across all included windows as coverage denominator.
            total_denominator = summarize(
                [row for row in rows if row.get("account") in set(account_values) and row.get("window_label") in {r.get("window_label") for r in proxy_all_rows}]
            )
            proxy_summary = add_proxy_diagnostics(summarize(proxy_all_rows), proxy_all_rows, total_denominator)
        else:
            proxy_summary = summarize([])
        summary_rows.append(
            {
                "proxy_id": proxy["id"],
                "proxy_label": proxy["label"],
                "kind": proxy["kind"],
                "criteria": json.dumps(criteria, ensure_ascii=False, sort_keys=True),
                "active_windows": active_windows,
                "profitable_windows": profitable_windows,
                "negative_windows": negative_windows,
                **proxy_summary,
            }
        )
    summary_rows.sort(key=lambda row: (fnum(row.get("profitable_windows")), fnum(row.get("cash_pnl"))), reverse=True)
    return window_rows, summary_rows


def account_rollups(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for account in sorted({str(r["account"]) for r in rows}):
        account_rows = [r for r in rows if r["account"] == account]
        out.append({"account": account, **summarize(account_rows)})
    return out


def parse_profile_spec(spec: str) -> tuple[str, Path]:
    if "=" not in spec:
        raise SystemExit(f"invalid --profile spec {spec!r}; expected account=/path/to/ce25_market_sequence.csv")
    account, raw_path = spec.split("=", 1)
    account = account.strip()
    path = Path(raw_path).expanduser()
    if path.is_dir():
        path = path / "ce25_market_sequence.csv"
    if not account or not path.exists():
        raise SystemExit(f"profile not found for {spec!r}: {path}")
    return account, path


def markdown_table(rows: list[dict[str, Any]], cols: list[tuple[str, str]], *, limit: int | None = None) -> str:
    subset = rows[:limit] if limit is not None else rows
    lines = ["| " + " | ".join(label for _, label in cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
    for row in subset:
        vals: list[str] = []
        for key, _ in cols:
            value = row.get(key)
            if key in {"buy_actual", "cash_pnl", "pair_pnl", "residual_pnl"}:
                vals.append(money(fnum(value)))
            elif key in {"roi", "pair_roi", "resid_rate", "fee_rate", "good_pc_lt_098_share", "bad_pc_ge_100_share"}:
                vals.append(pct(fnum(value)))
            elif key in {"pair_cost", "quality_score"}:
                vals.append(f"{fnum(value):.4f}")
            elif value is None:
                vals.append("")
            else:
                vals.append(str(value))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def write_report(
    path: Path,
    *,
    profiles: list[tuple[str, Path, dict[str, Any]]],
    rollups: list[dict[str, Any]],
    scored: list[dict[str, Any]],
    safe: list[dict[str, Any]],
    proxy_summary: list[dict[str, Any]],
    min_buy: float,
    max_resid_rate: float,
    max_bad_pc_share: float,
) -> None:
    bjt = now_bjt()
    lines: list[str] = []
    lines.append("# Account Autoresearch Iteration")
    lines.append("")
    lines.append(f"Generated: {bjt.isoformat(timespec='seconds')}")
    lines.append("")
    lines.append("## Method")
    lines.append("")
    lines.append(
        "This is an autoresearch-style pass: fixed eval metrics, explicit experiment ledger, proxy scoring, and keep/reject decisions. "
        "It reads already generated public-activity profile files and does not fetch or mutate raw Polymarket data."
    )
    lines.append("")
    lines.append("## Inputs")
    lines.append("")
    for account, profile_path, summary in profiles:
        window = summary.get("window") if isinstance(summary.get("window"), dict) else {}
        start_bjt = window.get("start_bjt", "unknown")
        end_bjt = window.get("end_bjt", "unknown")
        lines.append(f"- {account}: `{profile_path}` ({start_bjt} -> {end_bjt})")
    lines.append("")
    lines.append("## Account Rollup")
    lines.append("")
    lines.append(
        markdown_table(
            rollups,
            [
                ("account", "account"),
                ("markets", "markets"),
                ("buy_actual", "buy"),
                ("cash_pnl", "cash PnL"),
                ("roi", "ROI"),
                ("pair_pnl", "pair PnL"),
                ("pair_cost", "pair cost"),
                ("resid_rate", "resid"),
                ("fee_rate", "fee"),
                ("bad_pc_ge_100_share", "bad pc>=1"),
            ],
        )
    )
    lines.append("")
    lines.append("## Safe Proxy Candidates")
    lines.append("")
    lines.append(
        f"Filter: buy >= ${min_buy:,.0f}, cash PnL > 0, pair PnL > 0, residual < {pct(max_resid_rate)}, bad pc>=1 share < {pct(max_bad_pc_share)}."
    )
    lines.append("")
    if safe:
        lines.append(
            markdown_table(
                safe,
                [
                    ("account", "account"),
                    ("features", "features"),
                    ("value", "value"),
                    ("markets", "markets"),
                    ("buy_actual", "buy"),
                    ("cash_pnl", "cash PnL"),
                    ("roi", "ROI"),
                    ("pair_roi", "pair ROI"),
                    ("pair_cost", "pair cost"),
                    ("resid_rate", "resid"),
                    ("bad_pc_ge_100_share", "bad pc>=1"),
                    ("quality_score", "score"),
                ],
                limit=20,
            )
        )
    else:
        lines.append("No safe proxy candidates passed the filter.")
    lines.append("")
    lines.append("## Keep / Reject")
    lines.append("")
    if safe:
        top = safe[0]
        lines.append(
            f"- Keep for validation: `{top['account']}` `{top['features']}={top['value']}`. "
            f"It has {top['markets']} markets, {money(fnum(top['cash_pnl']))} cash PnL, "
            f"{pct(fnum(top['roi']))} ROI, and {fnum(top['pair_cost']):.4f} pair cost in this pass."
        )
    lines.append(
        "- Reject as a standalone alpha signal: low residual rate. It is a risk-control target; pair cost remains the stronger profit-quality signal."
    )
    lines.append(
        "- Reject blind account copying. The proxy-safe set is much narrower than the full account universe."
    )
    lines.append("")
    lines.append("## Pre-Registered Proxy Checks")
    lines.append("")
    lines.append(
        "These proxy checks are encoded before rolling validation, so they are not re-selected from the latest-window winners. "
        "`pair_cost` is evaluated only as an outcome metric."
    )
    lines.append("")
    lines.append(
        markdown_table(
            proxy_summary,
            [
                ("proxy_id", "proxy"),
                ("kind", "kind"),
                ("active_windows", "windows"),
                ("profitable_windows", "win windows"),
                ("markets", "markets"),
                ("buy_actual", "buy"),
                ("cash_pnl", "cash PnL"),
                ("roi", "ROI"),
                ("pair_pnl", "pair PnL"),
                ("pair_cost", "pair cost"),
                ("resid_rate", "resid"),
                ("bad_pc_ge_100_share", "bad pc>=1"),
                ("top3_net_share", "top3 net"),
            ],
            limit=20,
        )
    )
    lines.append("")
    lines.append("## Next Iteration Queue")
    lines.append("")
    lines.append("1. Run the same proxy filter across 7 rolling 24h windows.")
    lines.append("2. Promote candidates only if they keep positive pair PnL and bad pc>=1 share stays below the configured threshold.")
    lines.append("3. Convert winning proxy buckets into a shadow policy and measure participation, capital recycling, and drawdown.")
    lines.append("4. Compare ce25-style fee-covered execution against nagi-style no-fee maker execution on the same BTC windows.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_data_sources(
    path: Path,
    *,
    profiles: list[tuple[str, Path, dict[str, Any]]],
    rows: list[dict[str, Any]],
    min_buy: float,
    min_markets: int,
    max_resid_rate: float,
    max_bad_pc_share: float,
) -> None:
    sources: list[dict[str, Any]] = []
    for account, profile_path, summary in profiles:
        window = summary.get("window") if isinstance(summary.get("window"), dict) else {}
        sources.append(
            {
                "account": account,
                "profile_path": str(profile_path),
                "summary_path": str(profile_path.parent / "summary.json"),
                "window": window,
                "source_kind": "public_activity_profile",
                "fetching_performed_by_this_script": False,
                "note": "profile_ce25_execution_pattern.py writes ce25_market_sequence.csv even for non-ce25 users",
            }
        )
    payload = {
        "generated_at_bjt": now_bjt().isoformat(timespec="seconds"),
        "script": str(Path(__file__).resolve()),
        "row_count": len(rows),
        "scoring": {
            "features": FEATURES,
            "min_buy": min_buy,
            "min_markets": min_markets,
            "max_resid_rate": max_resid_rate,
            "max_bad_pc_share": max_bad_pc_share,
            "quality_score": "pair_roi*1.5 + roi - resid_rate*0.15 - bad_pc_ge_100_share*0.10",
        },
        "sources": sources,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_results(rows: list[dict[str, Any]], safe: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rollups = account_rollups(rows)
    result_rows: list[dict[str, Any]] = []
    for rollup in rollups:
        result_rows.append(
            {
                "id": f"ROLLUP_{rollup['account']}",
                "hypothesis": f"{rollup['account']} has positive fee-inclusive proxy-window PnL",
                "metric": "cash_pnl_roi_pair_cost_resid",
                "result": (
                    f"cash={rollup['cash_pnl']:.2f}; roi={rollup['roi']:.6f}; "
                    f"pair_cost={rollup['pair_cost']:.6f}; resid={rollup['resid_rate']:.6f}"
                ),
                "status": "keep" if fnum(rollup["cash_pnl"]) > 0 else "reject",
                "next_action": "validate over rolling windows",
            }
        )
    if safe:
        top = safe[0]
        result_rows.append(
            {
                "id": "TOP_PROXY",
                "hypothesis": "The top proxy is a viable shadow-policy seed",
                "metric": "quality_score_cash_pnl_pair_pnl_bad_pc_share",
                "result": (
                    f"{top['account']} {top['features']}={top['value']}; "
                    f"cash={top['cash_pnl']:.2f}; pair={top['pair_pnl']:.2f}; "
                    f"bad_pc_share={top['bad_pc_ge_100_share']:.6f}"
                ),
                "status": "keep_but_validate",
                "next_action": "run 7-day rolling and out-of-sample check",
            }
        )
    result_rows.append(
        {
            "id": "COPY_FULL_ACCOUNT",
            "hypothesis": "Full-account copying is a good strategy template",
            "metric": "safe_proxy_coverage",
            "result": f"{len(safe)} safe proxy buckets found from scored buckets",
            "status": "reject",
            "next_action": "learn only proxy-stable sub-strategies",
        }
    )
    return result_rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", action="append", default=[], help="account=/path/to/profile_dir_or_ce25_market_sequence.csv")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--min-buy", type=float, default=10_000.0)
    parser.add_argument("--min-markets", type=int, default=8)
    parser.add_argument("--max-resid-rate", type=float, default=0.18)
    parser.add_argument("--max-bad-pc-share", type=float, default=0.25)
    args = parser.parse_args()

    if not args.profile:
        raise SystemExit("provide at least one --profile account=/path")

    output_dir = Path(args.output_dir) if args.output_dir else ROOT / "data" / "exports" / f"account_autoresearch_iter_{now_bjt().strftime('%Y%m%d_%H%M%S')}_bjt"
    output_dir.mkdir(parents=True, exist_ok=True)

    profiles: list[tuple[str, Path, dict[str, Any]]] = []
    rows: list[dict[str, Any]] = []
    for spec in args.profile:
        account, path = parse_profile_spec(spec)
        profile_rows, summary = read_profile(path, account)
        profiles.append((account, path, summary))
        rows.extend(profile_rows)

    if not rows:
        raise SystemExit("no eligible profile rows found")

    rollups = account_rollups(rows)
    scored = proxy_score(rows, args.min_buy, args.min_markets)
    safe = safe_candidates(
        scored,
        min_buy=args.min_buy,
        max_resid_rate=args.max_resid_rate,
        max_bad_pc_share=args.max_bad_pc_share,
    )
    proxy_window_rows, proxy_summary = pre_registered_proxy_rollups(rows)

    write_csv(output_dir / "account_rollup.tsv", rollups, delimiter="\t")
    write_csv(output_dir / "proxy_scoreboard.tsv", scored, delimiter="\t")
    write_csv(output_dir / "proxy_safe_candidates.tsv", safe, delimiter="\t")
    write_csv(output_dir / "pre_registered_proxy_window_rollup.tsv", proxy_window_rows, delimiter="\t")
    write_csv(output_dir / "pre_registered_proxy_summary.tsv", proxy_summary, delimiter="\t")
    write_csv(output_dir / "results.tsv", build_results(rows, safe), delimiter="\t")
    write_data_sources(
        output_dir / "data_sources.json",
        profiles=profiles,
        rows=rows,
        min_buy=args.min_buy,
        min_markets=args.min_markets,
        max_resid_rate=args.max_resid_rate,
        max_bad_pc_share=args.max_bad_pc_share,
    )
    write_report(
        output_dir / "iteration_report.md",
        profiles=profiles,
        rollups=rollups,
        scored=scored,
        safe=safe,
        proxy_summary=proxy_summary,
        min_buy=args.min_buy,
        max_resid_rate=args.max_resid_rate,
        max_bad_pc_share=args.max_bad_pc_share,
    )
    print(json.dumps({"output_dir": str(output_dir), "profiles": len(profiles), "rows": len(rows), "safe_candidates": len(safe)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
