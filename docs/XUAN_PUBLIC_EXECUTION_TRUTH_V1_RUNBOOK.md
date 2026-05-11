# Xuan Public Execution Truth V1 Runbook

`xuan_public_execution_truth_v1` is the data-side store for xuan public execution
and inventory-cycle analysis. It is deliberately named "public execution truth",
not "private maker truth".

## What It Is

The store combines:

- `xuan_activity` public `TRADE / MERGE / REDEEM` rows.
- `xuan_trades` public trade rows.
- `md_trades` public market trades, when a trade-id, tx, address, or
  time/price/size match is available.
- `md_book_l1` and `md_book_l2` strict latest context with `recv_ms <= event_ts_ms`.
- `settlement_records` for winner/residual accounting.

DuckDB table:

```text
xuan_public_execution_events
```

Expected collector path:

```text
/home/ubuntu/poly_trans_research/data/verification_store/xuan_public_execution_truth_v1/<label>
```

## Reliability Boundary

This store does not claim to know xuan private order placement, cancellation, or
true queue priority. Those require authenticated/private order streams that are
not public.

Role fields are labeled by confidence:

- `truth_level='exact_address_match'`: xuan/proxy wallet matched public maker or
  taker address.
- `truth_level='exact_trade_id_match'`: public trade identifier matched.
- `truth_level='public_match_inferred_role'`: matched by public time/price/size;
  role is inferred from public taker side.
- `truth_level='public_xuan_activity_only'`: xuan activity exists, but no public
  trade match was found.
- `is_private_truth=false`: always false unless xuan private credentials are ever
  available.

`is_exact_maker_fill=true` is only allowed for high-confidence maker address or
exact identifier matches.

## Build

```bash
uv run --with duckdb python scripts/build_xuan_public_execution_truth_v1.py \
  --replay-root /home/ubuntu/poly_trans_research/data/replay_published \
  --store-root /home/ubuntu/poly_trans_research/data/verification_store \
  --days 2026-05-02,2026-05-03,2026-05-04,2026-05-05,2026-05-06,2026-05-07,2026-05-08 \
  --label 20260502_20260508 \
  --force
```

## Minimum Checks

```bash
python - <<'PY'
import duckdb
store = "/home/ubuntu/poly_trans_research/data/verification_store/xuan_public_execution_truth_v1/20260502_20260508/event_store.duckdb"
con = duckdb.connect(store, read_only=True)
print(con.execute("""
  SELECT day, event_kind, COUNT(*)
  FROM xuan_public_execution_events
  GROUP BY 1,2
  ORDER BY 1,2
""").fetchall())
print(con.execute("""
  SELECT truth_level, order_type, COUNT(*)
  FROM xuan_public_execution_events
  WHERE event_kind='fill'
  GROUP BY 1,2
  ORDER BY 1,2
""").fetchall())
print(con.execute("""
  SELECT COUNT(*)
  FROM xuan_public_execution_events
  WHERE event_kind='fill'
    AND order_type='maker'
    AND is_exact_maker_fill
""").fetchone())
PY
```

## How Research Agents Should Use It

Use this store to compare existing public-trade-only and L1-depletion models
against xuan public execution evidence:

- measure where public trade matching explains xuan fills;
- quantify where maker/taker role is exact, inferred, or unknown;
- recover FIFO public inventory cycles, pair cost, merge timing, and residual;
- build conservative fill models from high-confidence rows first.

Do not treat inferred maker rows as deployment proof. Final strategy claims still
need replay/source-of-truth validation and live/shadow execution truth.
