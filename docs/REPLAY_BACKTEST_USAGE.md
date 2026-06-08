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

## Outcome Truth

- `settlement_records.winner_side` is the normalized winner field for winner-bias/backtests.
- `winner_side` is always `YES` or `NO` for BTC/ETH/SOL/XRP 5m rows in the trusted window.
- `resolution_source='gamma_api'` means official public Gamma API outcome.
- `resolution_source` containing `inferred` must be treated as inferred, not official truth.
- `xuan_trades.outcome_side` and `xuan_activity.outcome_side` normalize raw `Up/Down` into `YES/NO`.
- For market metadata plus outcome, use the view `market_meta_with_outcome`.

## Status

- Market-side replay: trusted.
- Xuan public trades/activity: available for alignment.
- Own execution truth: not available; `own_*` tables are not usable.
- Main replay audit verdict: `market_replay_trusted=true`.
- BTC/ETH/SOL/XRP outcome coverage after local backfill: 100% for `2026-04-27T07:25:00Z` through `2026-05-01`.

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

## Winner Join Example

```python
rows = con.execute("""
    SELECT m.slug, m.symbol, s.winner_side, s.resolution_source
    FROM market_meta m
    JOIN settlement_records s ON s.condition_id = m.condition_id
    WHERE m.symbol = 'BTC'
      AND m.end_ms > 1777274700000
    ORDER BY m.start_ms
    LIMIT 10
""").fetchall()
```

## Rules For Agents

- Open SQLite in read-only mode.
- Filter out data before `2026-04-27T07:25:00Z`.
- Prefer replay tables over raw files.
- Use L2 only when size/depth matters; otherwise prefer `md_book_l1`.
- Do not stage or commit files under `data/`.
