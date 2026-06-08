#!/usr/bin/env python3
"""Combine exact early/late fill-triggered maker runs into one state machine.

Inputs are row CSVs produced by backtest_btc5m_maker_fill_triggered.py.  This
script does not read raw capture data and only opens replay SQLite in read-only
mode when evaluating residual forced-sell alternatives.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


DEFAULT_DAYS = ["2026-04-27", "2026-04-28", "2026-04-29", "2026-04-30", "2026-05-01"]
DAY_SHORT = {
    "2026-04-27": "0427",
    "2026-04-28": "0428",
    "2026-04-29": "0429",
    "2026-04-30": "0430",
    "2026-05-01": "0501",
}


def other(side: str) -> str:
    return "NO" if side == "YES" else "YES"


def ro_connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def as_float(row: dict[str, str], key: str) -> float | None:
    value = row.get(key, "")
    return None if value == "" else float(value)


def as_int(row: dict[str, str], key: str) -> int | None:
    value = row.get(key, "")
    return None if value == "" else int(float(value))


def load_rows_from_specs(output_root: Path, days: list[str], specs: list[tuple[str, str]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for kind_idx, (kind, suffix) in enumerate(specs):
        daily_paths = []
        for day in days:
            short = DAY_SHORT[day]
            path = output_root / f"backtest_btc5m_maker_fill_triggered_{short}_{suffix}" / "btc5m_maker_fill_triggered_rows.csv"
            if path.exists():
                daily_paths.append(path)
        if not daily_paths and days:
            first = DAY_SHORT[days[0]]
            last = DAY_SHORT[days[-1]]
            path = output_root / f"backtest_btc5m_maker_fill_triggered_{first}_{last}_{suffix}" / "btc5m_maker_fill_triggered_rows.csv"
            if path.exists():
                daily_paths.append(path)
        for path in daily_paths:
            with path.open(newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    item: dict[str, Any] = dict(row)
                    item["kind"] = kind
                    item["_kind_rank"] = kind_idx
                    item["first_fill"] = row.get("first_fill") == "True"
                    for key in ["candidate_ts_ms", "fill_ts_ms", "completion_ts_ms"]:
                        item[key] = as_int(row, key)
                    for key in ["pnl", "clip", "first_price", "second_price", "order_price"]:
                        item[key] = as_float(row, key)
                    rows.append(item)
    return rows


def load_rows(output_root: Path, days: list[str], early_suffix: str, late_suffix: str) -> list[dict[str, Any]]:
    return load_rows_from_specs(output_root, days, [("early", early_suffix), ("late", late_suffix)])


def parse_window_suffix_specs(values: list[str] | None) -> list[tuple[str, str]]:
    specs: list[tuple[str, str]] = []
    for value in values or []:
        if "=" not in value:
            raise ValueError("--window-suffix must use KIND=SUFFIX")
        kind, suffix = value.split("=", 1)
        kind = kind.strip()
        suffix = suffix.strip()
        if not kind or not suffix:
            raise ValueError("--window-suffix must use non-empty KIND=SUFFIX")
        specs.append((kind, suffix))
    return specs


def row_key(row: dict[str, Any]) -> tuple[str, str, int, str]:
    return (
        str(row["kind"]),
        str(row["condition_id"]),
        int(row["candidate_ts_ms"]),
        str(row["first_side"]),
    )


def dynamic_rule_applies(row: dict[str, Any], rule: str) -> bool:
    if rule == "none":
        return False
    try:
        field, op, value = rule.split(":", 2)
    except ValueError as exc:
        raise ValueError("dynamic upclip rule must be field:op:value, e.g. prev_bid_delta_1s:ge:0.14") from exc
    if field not in {
        "prev_bid_delta_1s",
        "side_bid",
        "top_bid_sz",
        "queue_same",
        "opp_ask_sz",
        "immediate_pair_cost",
    }:
        raise ValueError(f"unsupported dynamic upclip field: {field}")
    raw = row.get(field)
    if raw in (None, ""):
        return False
    left = float(raw)
    right = float(value)
    if op == "ge":
        return left >= right
    if op == "gt":
        return left > right
    if op == "le":
        return left <= right
    if op == "lt":
        return left < right
    raise ValueError(f"unsupported dynamic upclip op: {op}")


def apply_dynamic_upclip(base_rows: list[dict[str, Any]], upclip_rows: list[dict[str, Any]], rule: str) -> list[dict[str, Any]]:
    if rule == "none":
        return base_rows
    up_by_key = {row_key(row): row for row in upclip_rows}
    out: list[dict[str, Any]] = []
    for row in base_rows:
        replacement = up_by_key.get(row_key(row))
        if replacement is not None and dynamic_rule_applies(row, rule):
            item = dict(replacement)
            item["dynamic_upclip_rule"] = rule
            item["dynamic_upclip_from_clip"] = row.get("clip")
        else:
            item = dict(row)
            item["dynamic_upclip_rule"] = ""
            item["dynamic_upclip_from_clip"] = ""
        out.append(item)
    return out


def write_rows_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key.startswith("_"):
                continue
            if key not in seen:
                seen.add(key)
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: value for key, value in row.items() if key in seen})


def select_state_machine(rows: list[dict[str, Any]], cooldown_ms: int) -> list[dict[str, Any]]:
    by_market: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_market[str(row["condition_id"])].append(row)

    selected: list[dict[str, Any]] = []
    for market_rows in by_market.values():
        blocked_until = 0
        active_until = 0
        for row in sorted(market_rows, key=lambda r: (int(r["candidate_ts_ms"]), int(r.get("_kind_rank", 0)))):
            ts = int(row["candidate_ts_ms"])
            if ts < blocked_until or ts < active_until:
                continue
            selected.append(row)
            if row["first_fill"]:
                if row["completion_ts_ms"] is None:
                    active_until = 10**18
                    blocked_until = 10**18
                else:
                    active_until = int(row["completion_ts_ms"])
                    blocked_until = max(blocked_until, active_until + cooldown_ms)
            else:
                blocked_until = ts + 15_000
    return selected


def first_side_bid_at(conn: sqlite3.Connection, row: dict[str, Any], ts_ms: int) -> float | None:
    book = conn.execute(
        """
        SELECT yes_bid_px, no_bid_px
        FROM md_book_l1
        WHERE condition_id = ? AND recv_ms >= ?
        ORDER BY recv_ms
        LIMIT 1
        """,
        (row["condition_id"], ts_ms),
    ).fetchone()
    if book is None:
        return None
    key = "yes_bid_px" if row["first_side"] == "YES" else "no_bid_px"
    return None if book[key] is None else float(book[key])


def first_side_l2_bid_vwap_at(conn: sqlite3.Connection, row: dict[str, Any], ts_ms: int) -> dict[str, Any] | None:
    book = conn.execute(
        """
        SELECT bid1_px, bid1_sz, bid2_px, bid2_sz, bid3_px, bid3_sz,
               bid4_px, bid4_sz, bid5_px, bid5_sz
        FROM md_book_l2
        WHERE condition_id = ? AND market_side = ? AND recv_ms >= ?
        ORDER BY recv_ms
        LIMIT 1
        """,
        (row["condition_id"], row["first_side"], ts_ms),
    ).fetchone()
    if book is None:
        return None
    clip = float(row["clip"])
    remaining = clip
    proceeds = 0.0
    depth = 0.0
    levels = []
    for idx in range(1, 6):
        px = book[f"bid{idx}_px"]
        sz = book[f"bid{idx}_sz"]
        if px is None or sz is None or float(sz) <= 0:
            continue
        take = min(remaining, float(sz))
        proceeds += take * float(px)
        remaining -= take
        depth += float(sz)
        levels.append({"px": float(px), "sz": float(sz), "take": take})
        if remaining <= 1e-9:
            break
    # Conservative: any unfilled remainder is valued at zero.
    vwap = proceeds / clip if clip else None
    return {
        "vwap": vwap,
        "filled_qty": clip - max(0.0, remaining),
        "unfilled_qty": max(0.0, remaining),
        "visible_bid_depth": depth,
        "levels": levels,
    }


def opposite_l2_ask_vwap_at_completion(conn: sqlite3.Connection, row: dict[str, Any]) -> dict[str, Any] | None:
    ts_ms = int(row["completion_ts_ms"])
    start_ms = (ts_ms // 1000) * 1000
    end_ms = start_ms + 999
    book = conn.execute(
        """
        SELECT recv_ms, ask1_px, ask1_sz, ask2_px, ask2_sz, ask3_px, ask3_sz,
               ask4_px, ask4_sz, ask5_px, ask5_sz
        FROM md_book_l2
        WHERE condition_id = ? AND market_side = ? AND recv_ms >= ? AND recv_ms <= ?
        ORDER BY recv_ms DESC
        LIMIT 1
        """,
        (row["condition_id"], other(str(row["first_side"])), start_ms, end_ms),
    ).fetchone()
    if book is None:
        book = conn.execute(
            """
            SELECT recv_ms, ask1_px, ask1_sz, ask2_px, ask2_sz, ask3_px, ask3_sz,
                   ask4_px, ask4_sz, ask5_px, ask5_sz
            FROM md_book_l2
            WHERE condition_id = ? AND market_side = ? AND recv_ms <= ?
            ORDER BY recv_ms DESC
            LIMIT 1
            """,
            (row["condition_id"], other(str(row["first_side"])), end_ms),
        ).fetchone()
    if book is None:
        return None
    clip = float(row["clip"])
    remaining = clip
    cost = 0.0
    depth = 0.0
    levels = []
    for idx in range(1, 6):
        px = book[f"ask{idx}_px"]
        sz = book[f"ask{idx}_sz"]
        if px is None or sz is None or float(sz) <= 0:
            continue
        take = min(remaining, float(sz))
        cost += take * float(px)
        remaining -= take
        depth += float(sz)
        levels.append({"px": float(px), "sz": float(sz), "take": take})
        if remaining <= 1e-9:
            break
    # Conservative: any unfilled remainder is valued as a $1 buy.
    cost += max(0.0, remaining) * 1.0
    return {
        "vwap": cost / clip if clip else None,
        "filled_qty": clip - max(0.0, remaining),
        "unfilled_qty": max(0.0, remaining),
        "visible_ask_depth": depth,
        "levels": levels,
        "recv_ms": int(book["recv_ms"]),
    }


def l2_completion_reprice_summary(
    replay_root: Path,
    selected: list[dict[str, Any]],
    exit_delay_s: int,
    slippage_ticks: list[float],
    residual_exit_policy: str,
) -> dict[str, Any]:
    out: dict[str, Any] = {"exit_delay_s": exit_delay_s, "residual_exit_policy": residual_exit_policy, "slippage": {}}
    conns: dict[str, sqlite3.Connection] = {}
    try:
        repriced_rows: list[tuple[dict[str, Any], float]] = []
        deltas = []
        unfilled_second = 0.0
        unfilled_exit = 0.0
        for row in selected:
            day = str(row["day"])
            if day not in conns:
                conns[day] = ro_connect(replay_root / day / "crypto_5m.sqlite")
            pnl = float(row["pnl"])
            if row["path"] in ("completion", "slow_completion", "repair"):
                result = opposite_l2_ask_vwap_at_completion(conns[day], row)
                if result is not None and result["vwap"] is not None:
                    pnl = (1.0 - float(row["first_price"]) - float(result["vwap"])) * float(row["clip"])
                    if row.get("second_price") is not None:
                        deltas.append(float(result["vwap"]) - float(row["second_price"]))
                    unfilled_second += float(result["unfilled_qty"])
            elif row["path"] == "residual_settle":
                row_exit_delay_s = residual_exit_delay_s(row, exit_delay_s, residual_exit_policy)
                result = first_side_l2_bid_vwap_at(conns[day], row, int(row["fill_ts_ms"]) + row_exit_delay_s * 1000)
                if result is not None and result["vwap"] is not None:
                    pnl = (float(result["vwap"]) - float(row["first_price"])) * float(row["clip"])
                    unfilled_exit += float(result["unfilled_qty"])
            repriced_rows.append((row, pnl))

        out["base"] = summarize_daily_by_rows(repriced_rows)
        out["second_leg_vwap_delta"] = {
            "count": len(deltas),
            "avg": round(sum(deltas) / len(deltas), 6) if deltas else None,
            "max": round(max(deltas), 6) if deltas else None,
            "positive_count": sum(1 for value in deltas if value > 1e-9),
        }
        out["unfilled_second_qty"] = round(unfilled_second, 6)
        out["unfilled_exit_qty"] = round(unfilled_exit, 6)
        for slip in slippage_ticks:
            slipped: list[tuple[dict[str, Any], float]] = []
            for row, pnl in repriced_rows:
                adjusted = pnl
                if row["path"] in ("completion", "slow_completion", "repair", "residual_settle"):
                    adjusted -= float(slip) * float(row["clip"])
                slipped.append((row, adjusted))
            out["slippage"][str(slip)] = summarize_daily_by_rows(slipped)
    finally:
        for conn in conns.values():
            conn.close()
    return out


def residual_exit_delay_s(row: dict[str, Any], default_delay_s: int, policy: str) -> int:
    if policy == "fixed":
        return default_delay_s
    first_price = float(row["first_price"]) if row.get("first_price") is not None else None
    min_pair_cost_seen_30s = row.get("min_pair_cost_seen_30s")
    min_pair = float(min_pair_cost_seen_30s) if min_pair_cost_seen_30s not in (None, "") else None
    if policy == "price_lt_050_180_else_default":
        return 180 if first_price is not None and first_price < 0.50 else default_delay_s
    if policy == "min_pair_lte_101_180_else_default":
        return 180 if min_pair is not None and min_pair <= 1.01 else default_delay_s
    raise ValueError(f"unknown residual exit policy: {policy}")


def summarize_daily_by_rows(rows: list[tuple[dict[str, Any], float]]) -> dict[str, Any]:
    daily: dict[str, float] = defaultdict(float)
    for row, pnl in rows:
        daily[str(row["day"])] += pnl
    return summarize_daily(daily)


def residual_exit_adjusted_pnl(
    replay_root: Path,
    selected: list[dict[str, Any]],
    exit_delays_s: list[int],
) -> dict[str, Any]:
    base_daily: dict[str, float] = defaultdict(float)
    for row in selected:
        base_daily[str(row["day"])] += float(row["pnl"])

    residuals = [row for row in selected if row["path"] == "residual_settle"]
    out: dict[str, Any] = {
        "official_settlement": summarize_daily(base_daily),
        "policies": {},
        "residual_rows": len(residuals),
    }

    conns: dict[str, sqlite3.Connection] = {}
    try:
        for delay_s in exit_delays_s:
            daily = dict(base_daily)
            sell_pnls = []
            for row in residuals:
                day = str(row["day"])
                if day not in conns:
                    conns[day] = ro_connect(replay_root / day / "crypto_5m.sqlite")
                bid = first_side_bid_at(conns[day], row, int(row["fill_ts_ms"]) + delay_s * 1000)
                if bid is None:
                    continue
                sell_pnl = (bid - float(row["first_price"])) * float(row["clip"])
                daily[day] += sell_pnl - float(row["pnl"])
                sell_pnls.append(round(sell_pnl, 6))
            summary = summarize_daily(daily)
            summary["residual_sell_pnls"] = sell_pnls
            out["policies"][f"force_sell_l1_bid_after_{delay_s}s"] = summary

            daily_l2 = dict(base_daily)
            l2_sell_pnls = []
            l2_unfilled_qty = []
            for row in residuals:
                day = str(row["day"])
                if day not in conns:
                    conns[day] = ro_connect(replay_root / day / "crypto_5m.sqlite")
                vwap_result = first_side_l2_bid_vwap_at(conns[day], row, int(row["fill_ts_ms"]) + delay_s * 1000)
                if vwap_result is None or vwap_result["vwap"] is None:
                    continue
                sell_pnl = (float(vwap_result["vwap"]) - float(row["first_price"])) * float(row["clip"])
                daily_l2[day] += sell_pnl - float(row["pnl"])
                l2_sell_pnls.append(round(sell_pnl, 6))
                l2_unfilled_qty.append(round(float(vwap_result["unfilled_qty"]), 6))
            l2_summary = summarize_daily(daily_l2)
            l2_summary["residual_sell_pnls"] = l2_sell_pnls
            l2_summary["unfilled_qty_after_l2_sweep"] = l2_unfilled_qty
            out["policies"][f"force_sell_l2_bid_vwap_after_{delay_s}s"] = l2_summary
    finally:
        for conn in conns.values():
            conn.close()
    return out


def non_clean_exit_adjusted_pnl(
    replay_root: Path,
    selected: list[dict[str, Any]],
    exit_delays_s: list[int],
) -> dict[str, Any]:
    base_daily: dict[str, float] = defaultdict(float)
    for row in selected:
        base_daily[str(row["day"])] += float(row["pnl"])

    targets = [row for row in selected if row["path"] in ("repair", "residual_settle")]
    out: dict[str, Any] = {"policies": {}, "target_rows": len(targets)}
    conns: dict[str, sqlite3.Connection] = {}
    try:
        for delay_s in exit_delays_s:
            daily = dict(base_daily)
            path_pnl: dict[str, float] = defaultdict(float)
            l2_sell_pnls = []
            l2_unfilled_qty = []
            for row in targets:
                day = str(row["day"])
                if day not in conns:
                    conns[day] = ro_connect(replay_root / day / "crypto_5m.sqlite")
                vwap_result = first_side_l2_bid_vwap_at(conns[day], row, int(row["fill_ts_ms"]) + delay_s * 1000)
                if vwap_result is None or vwap_result["vwap"] is None:
                    continue
                sell_pnl = (float(vwap_result["vwap"]) - float(row["first_price"])) * float(row["clip"])
                daily[day] += sell_pnl - float(row["pnl"])
                path_pnl[str(row["path"])] += sell_pnl
                l2_sell_pnls.append(round(sell_pnl, 6))
                l2_unfilled_qty.append(round(float(vwap_result["unfilled_qty"]), 6))
            summary = summarize_daily(daily)
            summary["replacement_path_pnl"] = {key: round(value, 6) for key, value in sorted(path_pnl.items())}
            summary["replacement_sell_pnls"] = l2_sell_pnls
            summary["unfilled_qty_after_l2_sweep"] = l2_unfilled_qty
            out["policies"][f"replace_repair_and_residual_with_l2_sell_after_{delay_s}s"] = summary
    finally:
        for conn in conns.values():
            conn.close()
    return out


def summarize_daily(daily: dict[str, float]) -> dict[str, Any]:
    return {
        "pnl": round(sum(daily.values()), 6),
        "positive_days": f"{sum(1 for value in daily.values() if value > 0)}/{len(daily)}",
        "daily_pnl": {day: round(value, 6) for day, value in sorted(daily.items())},
    }


def daily_robustness(daily_pnl: dict[str, float]) -> dict[str, Any]:
    values = {day: float(value) for day, value in sorted(daily_pnl.items())}
    total = sum(values.values())
    leave_one_out = {day: round(total - value, 6) for day, value in values.items()}
    min_day = min(values.items(), key=lambda item: item[1]) if values else (None, None)
    max_day = max(values.items(), key=lambda item: item[1]) if values else (None, None)
    return {
        "days": len(values),
        "pnl": round(total, 6),
        "positive_days": f"{sum(1 for value in values.values() if value > 0)}/{len(values)}" if values else "0/0",
        "min_day": {"day": min_day[0], "pnl": None if min_day[1] is None else round(min_day[1], 6)},
        "max_day": {"day": max_day[0], "pnl": None if max_day[1] is None else round(max_day[1], 6)},
        "leave_one_out_pnl": leave_one_out,
        "leave_one_out_min_pnl": round(min(leave_one_out.values()), 6) if leave_one_out else None,
    }


def summarize_path_pnl(selected: list[dict[str, Any]]) -> dict[str, Any]:
    by_path: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "pnl": 0.0})
    for row in selected:
        path = str(row["path"])
        by_path[path]["count"] += 1
        by_path[path]["pnl"] += float(row["pnl"])
    return {
        path: {"count": int(item["count"]), "pnl": round(float(item["pnl"]), 6)}
        for path, item in sorted(by_path.items())
    }


def slippage_robustness(l2_completion_reprice: dict[str, Any]) -> dict[str, Any]:
    cases: dict[str, Any] = {"0.0": l2_completion_reprice["base"], **l2_completion_reprice.get("slippage", {})}
    out: dict[str, Any] = {}
    all_positive_slips = []
    for slip, item in sorted(cases.items(), key=lambda kv: float(kv[0])):
        daily = {day: float(value) for day, value in item["daily_pnl"].items()}
        robust = daily_robustness(daily)
        out[slip] = robust
        if robust["positive_days"] == f"{robust['days']}/{robust['days']}":
            all_positive_slips.append(float(slip))
    return {
        "cases": out,
        "max_tested_slippage_all_days_positive": round(max(all_positive_slips), 6) if all_positive_slips else None,
    }


def build_robustness(report: dict[str, Any], selected: list[dict[str, Any]]) -> dict[str, Any]:
    raw_daily = {day: float(item["pnl"]) for day, item in report["summary"]["daily"].items()}
    l2_daily = {day: float(value) for day, value in report["l2_completion_reprice"]["base"]["daily_pnl"].items()}
    fills_by_day: dict[str, int] = defaultdict(int)
    attempts_by_day: dict[str, int] = defaultdict(int)
    for row in selected:
        attempts_by_day[str(row["day"])] += 1
        fills_by_day[str(row["day"])] += int(bool(row["first_fill"]))
    return {
        "raw_official_settlement": daily_robustness(raw_daily),
        "l2_completion_plus_nonclean_exit": daily_robustness(l2_daily),
        "l2_slippage": slippage_robustness(report["l2_completion_reprice"]),
        "path_pnl_raw": summarize_path_pnl(selected),
        "attempts_by_day": dict(sorted(attempts_by_day.items())),
        "fills_by_day": dict(sorted(fills_by_day.items())),
        "fill_rate_by_day": {
            day: round(fills_by_day[day] / attempts_by_day[day], 6) if attempts_by_day[day] else None
            for day in sorted(attempts_by_day)
        },
    }


def summarize_selected(selected: list[dict[str, Any]]) -> dict[str, Any]:
    daily: dict[str, dict[str, Any]] = defaultdict(lambda: defaultdict(float))
    paths = Counter(str(row["path"]) for row in selected)
    kinds = Counter(str(row["kind"]) for row in selected)
    for row in selected:
        day = str(row["day"])
        daily[day]["attempts"] += 1
        daily[day]["fills"] += int(bool(row["first_fill"]))
        daily[day]["pnl"] += float(row["pnl"])
        daily[day][f"{row['kind']}_attempts"] += 1
        daily[day][f"{row['kind']}_fills"] += int(bool(row["first_fill"]))

    spend_est = 0.0
    for row in selected:
        if not row["first_fill"]:
            continue
        clip = float(row["clip"] or 0)
        first_price = float(row["first_price"] if row["first_price"] is not None else row["order_price"])
        if row["second_price"] is not None:
            spend_est += (first_price + float(row["second_price"])) * clip
        else:
            spend_est += first_price * clip

    pnl = sum(float(row["pnl"]) for row in selected)
    return {
        "attempts": len(selected),
        "fills": sum(1 for row in selected if row["first_fill"]),
        "paths": dict(paths),
        "kinds": dict(kinds),
        "pnl": round(pnl, 6),
        "spend_est": round(spend_est, 6),
        "roi_est": round(pnl / spend_est, 6) if spend_est else None,
        "daily": {day: dict(values) for day, values in sorted(daily.items())},
    }


def render_markdown(report: dict[str, Any]) -> str:
    kinds = sorted(report["summary"].get("kinds", {}))
    lines = [
        "# Fast-Cancel Combo",
        "",
        "## Summary",
        "",
        f"- attempts: `{report['summary']['attempts']}`",
        f"- fills: `{report['summary']['fills']}`",
        f"- pnl: `${report['summary']['pnl']}`",
        f"- roi_est: `{report['summary']['roi_est']}`",
        f"- paths: `{report['summary']['paths']}`",
        "",
        "## Daily",
        "",
        "| day | attempts | fills | pnl | kind attempts/fills |",
        "|---|---:|---:|---:|---|",
    ]
    for day, item in report["summary"]["daily"].items():
        kind_parts = [
            f"{kind}={int(item.get(f'{kind}_attempts', 0))}/{int(item.get(f'{kind}_fills', 0))}"
            for kind in kinds
        ]
        lines.append(
            f"| {day} | {int(item['attempts'])} | {int(item['fills'])} | {round(item['pnl'], 6)} | "
            f"{', '.join(kind_parts)} |"
        )
    lines.extend(["", "## Residual Exit Sensitivity", "", "| policy | pnl | positive days |", "|---|---:|---:|"])
    residual = report["residual_exit"]
    official = residual["official_settlement"]
    lines.append(f"| official settlement | {official['pnl']} | {official['positive_days']} |")
    for name, item in residual["policies"].items():
        lines.append(f"| {name} | {item['pnl']} | {item['positive_days']} |")
    lines.extend(["", "## Non-Clean Exit Sensitivity", "", "| policy | pnl | positive days |", "|---|---:|---:|"])
    for name, item in report["non_clean_exit"]["policies"].items():
        lines.append(f"| {name} | {item['pnl']} | {item['positive_days']} |")
    lines.extend(["", "## L2 Completion Reprice", "", "| case | pnl | positive days |", "|---|---:|---:|"])
    base = report["l2_completion_reprice"]["base"]
    lines.append(f"| l2 completion + residual {report['l2_completion_reprice']['exit_delay_s']}s exit | {base['pnl']} | {base['positive_days']} |")
    for slip, item in report["l2_completion_reprice"]["slippage"].items():
        lines.append(f"| plus `{slip}` slippage | {item['pnl']} | {item['positive_days']} |")
    robust = report.get("robustness", {})
    if robust:
        l2 = robust["l2_completion_plus_nonclean_exit"]
        raw = robust["raw_official_settlement"]
        slip = robust["l2_slippage"]
        lines.extend(
            [
                "",
                "## Robustness",
                "",
                "| lens | pnl | positive days | weakest day | leave-one-out min |",
                "|---|---:|---:|---:|---:|",
                f"| raw official settlement | {raw['pnl']} | {raw['positive_days']} | {raw['min_day']['day']} {raw['min_day']['pnl']} | {raw['leave_one_out_min_pnl']} |",
                f"| l2 completion + non-clean exit | {l2['pnl']} | {l2['positive_days']} | {l2['min_day']['day']} {l2['min_day']['pnl']} | {l2['leave_one_out_min_pnl']} |",
                "",
                f"- max tested slippage with all days positive: `{slip['max_tested_slippage_all_days_positive']}`",
                f"- path pnl raw: `{robust['path_pnl_raw']}`",
                f"- fill rate by day: `{robust['fill_rate_by_day']}`",
            ]
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-root", default="data/replay")
    parser.add_argument("--output-root", default="data/exports")
    parser.add_argument("--output-dir", default="data/exports/dual_window_fastcancel_combo")
    parser.add_argument("--days", default=",".join(DEFAULT_DAYS))
    parser.add_argument("--early-suffix", default="candidate_early10_20_delta4c_bid50")
    parser.add_argument("--late-suffix", default="candidate_late40_60_bid50")
    parser.add_argument("--early-upclip-suffix", default="")
    parser.add_argument("--late-upclip-suffix", default="")
    parser.add_argument("--dynamic-upclip-rule", default="none")
    parser.add_argument(
        "--window-suffix",
        action="append",
        help="Repeatable KIND=SUFFIX input. Overrides --early-suffix/--late-suffix when provided.",
    )
    parser.add_argument(
        "--upclip-window-suffix",
        action="append",
        help="Repeatable KIND=SUFFIX upclip input. Required with --dynamic-upclip-rule when --window-suffix is used.",
    )
    parser.add_argument("--cooldown-ms", type=int, default=10_000)
    parser.add_argument("--exit-delays-s", default="30,60,90,120,180")
    parser.add_argument("--l2-completion-exit-delay-s", type=int, default=120)
    parser.add_argument("--l2-residual-exit-policy", default="fixed", choices=["fixed", "price_lt_050_180_else_default", "min_pair_lte_101_180_else_default"])
    parser.add_argument("--l2-completion-slippage", default="0,0.005,0.01,0.02")
    args = parser.parse_args()

    days = [day.strip() for day in args.days.split(",") if day.strip()]
    specs = parse_window_suffix_specs(args.window_suffix)
    if not specs:
        specs = [("early", args.early_suffix), ("late", args.late_suffix)]
    rows = load_rows_from_specs(Path(args.output_root), days, specs)
    if args.dynamic_upclip_rule != "none":
        upclip_specs = parse_window_suffix_specs(args.upclip_window_suffix)
        if not upclip_specs:
            if not args.early_upclip_suffix or not args.late_upclip_suffix:
                raise ValueError(
                    "--early-upclip-suffix and --late-upclip-suffix are required with --dynamic-upclip-rule"
                )
            upclip_specs = [("early", args.early_upclip_suffix), ("late", args.late_upclip_suffix)]
        if [kind for kind, _ in upclip_specs] != [kind for kind, _ in specs]:
            raise ValueError("--upclip-window-suffix kinds must match --window-suffix kinds and order")
        upclip_rows = load_rows_from_specs(Path(args.output_root), days, upclip_specs)
        rows = apply_dynamic_upclip(rows, upclip_rows, args.dynamic_upclip_rule)
    selected = select_state_machine(rows, args.cooldown_ms)
    report = {
        "parameters": vars(args),
        "summary": summarize_selected(selected),
        "residual_exit": residual_exit_adjusted_pnl(
            Path(args.replay_root),
            selected,
            [int(value) for value in args.exit_delays_s.split(",") if value.strip()],
        ),
        "non_clean_exit": non_clean_exit_adjusted_pnl(
            Path(args.replay_root),
            selected,
            [int(value) for value in args.exit_delays_s.split(",") if value.strip()],
        ),
        "l2_completion_reprice": l2_completion_reprice_summary(
            Path(args.replay_root),
            selected,
            args.l2_completion_exit_delay_s,
            [float(value) for value in args.l2_completion_slippage.split(",") if value.strip()],
            args.l2_residual_exit_policy,
        ),
    }
    report["robustness"] = build_robustness(report, selected)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "dual_window_fastcancel_combo_summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "dual_window_fastcancel_combo_report.md").write_text(render_markdown(report), encoding="utf-8")
    write_rows_csv(output_dir / "dual_window_fastcancel_combo_selected_rows.csv", selected)
    print(json.dumps({"output_dir": str(output_dir), "attempts": report["summary"]["attempts"], "fills": report["summary"]["fills"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
