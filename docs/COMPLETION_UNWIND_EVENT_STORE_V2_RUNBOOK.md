# Completion/Unwind Event Store V2 Runbook

`completion_unwind_event_store_v2` is a backward-compatible extension of
`completion_unwind_event_store_v1`. It preserves the V1 event model and adds
adjacent L1 delta fields so research agents do not have to infer book changes
from neighboring rows themselves.

## Added Fields

For each event row and `side`:

```text
prev_side_bid
prev_side_bid_sz
prev_side_ask
prev_side_ask_sz
side_bid_delta_qty
side_bid_level_drop_qty
side_ask_delta_qty
side_ask_level_lift_qty
book_update_reason
```

Interpretation:

- `side_bid_delta_qty`: same-price bid size decrease,
  `max(prev_side_bid_sz - side_bid_sz, 0)`.
- `side_bid_level_drop_qty`: previous best bid size when the best bid price
  drops or disappears.
- `side_ask_delta_qty`: same-price ask size decrease,
  `max(prev_side_ask_sz - side_ask_sz, 0)`.
- `side_ask_level_lift_qty`: previous best ask size when the best ask price
  lifts or disappears.
- `book_update_reason`: one of `initial`, `level_drop`, `level_lift`,
  `price_change`, `size_change`, or `unknown`.

## Expected Path

```text
/home/ubuntu/poly_trans_research/data/verification_store/completion_unwind_event_store_v2/<label>
```

DuckDB table:

```text
completion_unwind_events
```

## Build From Replay

```bash
uv run --with duckdb python scripts/build_completion_unwind_event_store_v2.py \
  --replay-root /home/ubuntu/poly_trans_research/data/replay_published \
  --store-root /home/ubuntu/poly_trans_research/data/verification_store \
  --days 2026-05-02,2026-05-03,2026-05-04,2026-05-05,2026-05-06,2026-05-07,2026-05-08 \
  --label 20260502_20260508 \
  --force
```

## Fast Upgrade From V1

Use this path when V1 is already published. It preserves V1 event/L2 columns and
scans replay only for adjacent L1 deltas referenced by V1 event rows.

```bash
uv run --with duckdb python scripts/build_completion_unwind_event_store_v2_from_v1.py \
  --replay-root /home/ubuntu/poly_trans_research/data/replay_published \
  --store-root /home/ubuntu/poly_trans_research/data/verification_store \
  --v1-store-dirs /home/ubuntu/poly_trans_research/data/verification_store/completion_unwind_event_store_v1/20260502_20260507,/home/ubuntu/poly_trans_research/data/verification_store/completion_unwind_event_store_v1/20260508 \
  --days 2026-05-02,2026-05-03,2026-05-04,2026-05-05,2026-05-06,2026-05-07,2026-05-08 \
  --label 20260502_20260508 \
  --force
```

## Checks

```bash
python - <<'PY'
import duckdb
store = "/home/ubuntu/poly_trans_research/data/verification_store/completion_unwind_event_store_v2/20260502_20260508/event_store.duckdb"
con = duckdb.connect(store, read_only=True)
print(con.execute("select event_kind, count(*) from completion_unwind_events group by 1 order by 1").fetchall())
print(con.execute("select book_update_reason, count(*) from completion_unwind_events group by 1 order by 1").fetchall())
print(con.execute("select count(*) from completion_unwind_events where strict_l1_recv_ms > ts_ms").fetchone())
PY
```

## Reliability

The store still uses only public replay market facts:

- strict L1/L2 context uses rows with `recv_ms <= ts_ms`;
- delta fields are adjacent-L1 evidence, not private order queue truth;
- V1 remains available and unchanged.
