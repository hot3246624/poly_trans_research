# Completion/Unwind Event Store V1 Runbook

This store is for xuan-style inventory-cycle research. It is separate from the
taker-BUY signal cache.

## Purpose

Use `completion_unwind_event_store_v1` when a strategy needs to answer:

- If I hold YES/NO inventory, what could I buy to complete the pair?
- If I hold YES/NO inventory, what could I sell to unwind?
- What public trade or visible book event triggered this opportunity?
- Was the side high/low at that exact time?

The store does not simulate our private queue position. It exposes public,
strictly time-causal market facts.

## Event Types

`public_trade`

Public `md_trades` rows for BTC 5m markets, with strict latest L1/L2 context at
`trade_ts_ms`. This is the right input for public-flow and maker-fill proxy
research.

`l1_price_change`

Visible top-of-book price-change rows. Each L1 price-change emits one YES row
and one NO row, with latest L2 context at the same `recv_ms`. This is the right
input for completion/unwind opportunity scans.

## Key Columns

Identity/time:

```text
day
event_kind
event_id
ts_ms
ts_iso
condition_id
slug
offset_s
side
opposite_side
winner_side
side_is_winner
```

Strict book context:

```text
strict_l1_recv_ms
strict_l1_age_ms
strict_l2_recv_ms
strict_l2_age_ms
side_alignment
high_side
```

L1 prices:

```text
side_bid
side_ask
side_bid_sz
side_ask_sz
opp_bid
opp_ask
opp_bid_sz
opp_ask_sz
l1_pair_ask
l1_pair_bid
```

L2 executable tiers:

```text
buy_best_px
buy_available_qty
buy_full_10 / buy_vwap_10 / buy_filled_10 / buy_worst_px_10
buy_full_25 / buy_vwap_25 / ...
buy_full_60 / buy_vwap_60 / ...
buy_full_100 / buy_vwap_100 / ...
buy_full_250 / buy_vwap_250 / ...

sell_best_px
sell_available_qty
sell_full_10 / sell_vwap_10 / sell_filled_10 / sell_worst_px_10
sell_full_25 / sell_vwap_25 / ...
sell_full_60 / sell_vwap_60 / ...
sell_full_100 / sell_vwap_100 / ...
sell_full_250 / sell_vwap_250 / ...
```

Interpretation:

- `buy_*` consumes ask-side L2 for `side`.
- `sell_*` consumes bid-side L2 for `side`.
- `l1_pair_ask` is `side_ask + opposite_side_ask`.
- `l1_pair_bid` is `side_bid + opposite_side_bid`.

Public trade fields are populated only for `event_kind='public_trade'`:

```text
public_trade_row_id
public_trade_taker_side
public_trade_price
public_trade_size
public_trade_recv_ms
```

## Expected Paths

Collector:

```text
/home/ubuntu/poly_trans_research/data/verification_store/completion_unwind_event_store_v1/20260502_20260507
/home/ubuntu/poly_trans_research/data/verification_store/completion_unwind_event_store_v1/20260508
```

Backtest server, after copy:

```text
/tmp/poly-verification-store/completion_unwind_event_store_v1/20260502_20260507
/tmp/poly-verification-store/completion_unwind_event_store_v1/20260508
```

## Copy To Backtest Server

```bash
COLLECTOR=ubuntu@ec2-108-129-167-79.eu-west-1.compute.amazonaws.com
KEY=~/.ssh/polymarket-Ireland.pem
mkdir -p /tmp/poly-verification-store/completion_unwind_event_store_v1

rsync -a -e "ssh -i $KEY" \
  "$COLLECTOR:/home/ubuntu/poly_trans_research/data/verification_store/completion_unwind_event_store_v1/20260502_20260507" \
  /tmp/poly-verification-store/completion_unwind_event_store_v1/

rsync -a -e "ssh -i $KEY" \
  "$COLLECTOR:/home/ubuntu/poly_trans_research/data/verification_store/completion_unwind_event_store_v1/20260508" \
  /tmp/poly-verification-store/completion_unwind_event_store_v1/
```

## Query Examples

Completion opportunity for NO after holding YES inventory:

```sql
SELECT *
FROM completion_unwind_events
WHERE side = 'NO'
  AND buy_full_60
  AND buy_vwap_60 <= 0.95
  AND strict_l2_age_ms <= 750
ORDER BY condition_id, ts_ms;
```

Unwind opportunity for existing YES inventory:

```sql
SELECT *
FROM completion_unwind_events
WHERE side = 'YES'
  AND sell_full_60
  AND sell_vwap_60 >= 0.50
  AND strict_l2_age_ms <= 750
ORDER BY condition_id, ts_ms;
```

Public SELL maker-fill proxy events:

```sql
SELECT *
FROM completion_unwind_events
WHERE event_kind = 'public_trade'
  AND public_trade_taker_side = 'SELL'
  AND side = 'YES'
ORDER BY condition_id, ts_ms;
```

## Reliability Rules

- The store uses only L1/L2 rows with `recv_ms <= ts_ms`.
- `strict_l1_age_ms` and `strict_l2_age_ms` must be filtered by the strategy.
- The store does not claim maker queue fill certainty.
- Use it for fast inventory/completion search and xuan-cycle analysis.
- For deployment-critical claims, audit selected samples back to
  `replay_published`.
