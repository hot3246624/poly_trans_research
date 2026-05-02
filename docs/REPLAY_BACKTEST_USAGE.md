# Replay Backtest Usage

Use this local replay dataset as the main backtest/research source.

## Paths

- Replay root: `/Users/hot/web3Scientist/poly_trans_research/data/replay`
- Trusted start: `2026-04-27T07:25:00Z`
- Valid days: `2026-04-27` through `2026-05-01`
- Example DB: `/Users/hot/web3Scientist/poly_trans_research/data/replay/2026-05-01/crypto_5m.sqlite`

Do not use `data/raw`, `data/exports`, or `data/replay.before_*` for normal backtests.

## Tables

- Market metadata: `market_meta`
- Trades: `md_trades`
- L1 book: `md_book_l1`
- L2 book: `md_book_l2`
- Settlements: `settlement_records`
- Xuan public truth: `xuan_trades`, `xuan_activity`, `xuan_poll_log`

## Status

- Market-side replay: trusted.
- Xuan public trades/activity: available for alignment.
- Own execution truth: not available; `own_*` tables are not usable.
- Main replay audit verdict: `market_replay_trusted=true`.

## Minimal Read Example

```python
import sqlite3

db = "/Users/hot/web3Scientist/poly_trans_research/data/replay/2026-05-01/crypto_5m.sqlite"
con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)

rows = con.execute("""
    SELECT condition_id, trade_ts_ms, market_side, taker_side, price, size
    FROM md_trades
    WHERE trade_ts_ms >= ?
    LIMIT 10
""", (1777591500000,)).fetchall()

con.close()
```

## Rules For Agents

- Open SQLite in read-only mode.
- Filter out data before `2026-04-27T07:25:00Z`.
- Prefer replay tables over raw files.
- Use L2 only when size/depth matters; otherwise prefer `md_book_l1`.
- Do not stage or commit files under `data/`.
