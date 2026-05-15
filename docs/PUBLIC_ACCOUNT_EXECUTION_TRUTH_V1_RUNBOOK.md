# Public Account Execution Truth V1 Runbook

`public_account_execution_truth_v1` is the data-side store for public account
execution audits such as B27 and RWO. It is deliberately a sidecar artifact: it
does not mutate raw captures and does not require replay rebuilds.

## What It Uses

- Data API public account `activity` rows for configured wallets.
- Existing `replay_published` SQLite for market metadata, `md_trades`, strict
  `md_book_l1` / `md_book_l2`, and settlements.
- DuckDB/Parquet output for fast research queries.

It is public execution truth, not private order truth. It cannot reconstruct
private order placement, cancellation, or true queue priority.

## Default Accounts

```text
b27bc: 0xb27bc932bf8110d8f78e55da7d5f0497a18b5b82, BTC 5m only
rwo:   0xd189664c5308903476f9f079820431e4fd7d06f4, updown 5m
```

## Build

Run from the code root on the collector:

```bash
/home/ubuntu/.local/bin/uv run --with duckdb python \
  /home/ubuntu/poly_trans_research/scripts/build_public_account_execution_truth_v1.py \
  --replay-root /home/ubuntu/poly_trans_research/data/replay_published \
  --store-root /home/ubuntu/poly_trans_research/data/verification_store \
  --days 2026-05-02,2026-05-03,2026-05-04,2026-05-05,2026-05-06,2026-05-07,2026-05-08,2026-05-09,2026-05-10,2026-05-11,2026-05-12,2026-05-13 \
  --label 20260502_20260513 \
  --accounts b27bc,rwo \
  --duckdb-threads 2
```

Expected output:

```text
/home/ubuntu/poly_trans_research/data/verification_store/public_account_execution_truth_v1/20260502_20260513
```

DuckDB table:

```text
public_account_execution_events
```

## Minimum Checks

```bash
python - <<'PY'
import duckdb
store = "/home/ubuntu/poly_trans_research/data/verification_store/public_account_execution_truth_v1/20260502_20260513/event_store.duckdb"
con = duckdb.connect(store, read_only=True)
print(con.execute("""
  SELECT account_label, day, event_kind, COUNT(*)
  FROM public_account_execution_events
  GROUP BY 1,2,3
  ORDER BY 1,2,3
""").fetchall())
print(con.execute("""
  SELECT account_label, truth_level, order_type, COUNT(*)
  FROM public_account_execution_events
  WHERE event_kind='fill'
  GROUP BY 1,2,3
  ORDER BY 1,2,3
""").fetchall())
PY
```

## Research Use

Use this store to compare B27/RWO behavior against xuan and against candidate
maker/unwind models:

- public account fill timing;
- public maker/taker role when address evidence exists;
- strict L1/L2 state at public fill time;
- inventory cycle, merge, residual, and settlement outcomes.

Do not use it as deployment proof of private maker fillability. Exact maker
truth requires address/trade evidence or our own private execution telemetry.
