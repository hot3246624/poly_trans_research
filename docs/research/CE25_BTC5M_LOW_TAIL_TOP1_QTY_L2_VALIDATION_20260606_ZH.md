# CE25 BTC5M Low-Tail Top1 Qty L2 Validation

Status: `KEEP_CE25_LOW_TAIL_SIDE_SPLIT_V2_TOP1_QTY_L2_CLEAN_REVIEW_REQUIRED_NOT_OOS_READY`

## 结论

`CE25_BTC5M_LOW_PRICE_TAIL_SIDE_SPLIT_V2` 继续升级：从上一轮的 `WATCH_L2_VALIDATION_NEXT` 升级为 `TOP1_QTY_L2_CLEAN_REVIEW_REQUIRED`。

核心变化：

1. 不加 top1 qty gate 时，UP 两个 strict 分支已经 100% L2 clean；DOWN 两个 strict 分支各有 1 个 action 被挡，pass rate 约 97.5%。
2. DOWN 的 blocker 不是价格错配，也不是 L2 缺失，而是 1 个 market 的 opposite top1 深度不足，top5 VWAP 变成 0.98848，超过 0.965 cap。
3. 加 `entry_requires_opposite_qty=true` 后，DOWN/UP 的 top1_qty 分支全部 100% L2 pass。
4. 因此 V2 的真正可复现形态不是普通 side-split，而是 `side_split + top1_qty + paircap 0.965`。

这仍然不是 OOS/live，也不证明 CE25 私有 maker/taker、真实成交、排队优先级或可部署收益。

## 输入

- strategy input: `/Users/hot/web3Scientist/poly_trans_research/configs/ce25_low_tail/CE25_BTC5M_LAST60_FIRST20_35_V2_INPUT.json`
- runner: `/Users/hot/web3Scientist/poly_trans_research/scripts/run_ce25_nagi_shadow_policy_runner.py`
- action run: `/Users/hot/web3Scientist/poly_backtest_data/derived/ce25_nagi_shadow_policy_autoresearch_v0/ce25_low_tail_side_split_v2_top1_qty_actions_20260606`
- L2 validation output: `/Users/hot/web3Scientist/poly_trans_research/data/exports/ce25_low_tail_side_split_v2_top1_qty_l2_validation_20260606`
- comparison L2 output without top1 qty: `/Users/hot/web3Scientist/poly_trans_research/data/exports/ce25_low_tail_side_split_v2_l2_validation_20260606`
- local L2 mart: `/Users/hot/web3Scientist/poly_backtest_data/derived/contract_examples/l2_top_aligned_mart_20260502_20260518_l2`
- candidate base: `/Users/hot/web3Scientist/poly_backtest_data/derived/completion_candidate_pipeline_v1/local_20260502_20260518_paircap102`
- fee stress: 3.0%
- no private key, no network fetch, no import, no order/cancel/redeem/live/deploy.

## Why Top1 Qty Gate

非 top1_qty DOWN 分支的失败点：

| branch | actions | L2 pass | pass rate | fail reason | failing market | top5 VWAP |
| --- | ---: | ---: | ---: | --- | --- | ---: |
| DOWN entry_paircap cap 0.965 | 39 | 38 | 97.4359% | `TOP1_DEPTH_TOP5_VWAP_GT_CAP` | `btc-updown-5m-1778272800` | 0.98848 |
| DOWN same_row cap 0.965 | 40 | 39 | 97.5000% | `TOP1_DEPTH_TOP5_VWAP_GT_CAP` | `btc-updown-5m-1778272800` | 0.98848 |

该失败 action 的 L1 pair_cost 是 0.96，表面上低于 0.965，但 top1 深度只覆盖一条腿；补到 top5 后 VWAP 超 cap。也就是说，问题不是方向判断，而是盘口深度约束。

因此加 `entry_requires_opposite_qty=true` 是正确修复：入口时要求 opposite top1 size 覆盖当前 qty，把这个 tail risk 前置过滤。

## L2 Clean Results

下表为加 top1 qty gate 后的 3.0% fee 结果。8 个分支全部 `KEEP_L2_TOP_ALIGNED_ACTIONS_VALIDATED_REVIEW_REQUIRED`。

| side | branch | target_qty | actions | markets | L2 pass | buy_actual | pnl | ROI | pair_cost | residual |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| DOWN | entry_paircap_top1_qty_cap_0.965 | 5 | 38 | 38 | 38/38 | 118.378969 | 12.838532 | 10.8453% | 0.889690 | 0.00% |
| DOWN | same_row_top1_qty_cap_0.965 | 5 | 38 | 38 | 38/38 | 118.378969 | 12.838532 | 10.8453% | 0.889690 | 0.00% |
| UP | entry_paircap_top1_qty_cap_0.965 | 5 | 30 | 29 | 30/30 | 92.457885 | 12.219616 | 13.2164% | 0.870625 | 0.00% |
| UP | same_row_top1_qty_cap_0.965 | 5 | 30 | 29 | 30/30 | 92.457885 | 12.219616 | 13.2164% | 0.870625 | 0.00% |
| DOWN | entry_paircap_top1_qty_target_qty_8_cap_0.965 | 8 | 37 | 37 | 37/37 | 165.446045 | 17.941456 | 10.8443% | 0.889734 | 0.00% |
| DOWN | same_row_top1_qty_target_qty_8_cap_0.965 | 8 | 37 | 37 | 37/37 | 165.446045 | 17.941456 | 10.8443% | 0.889734 | 0.00% |
| UP | entry_paircap_top1_qty_target_qty_8_cap_0.965 | 8 | 29 | 28 | 29/29 | 117.899149 | 15.703352 | 13.3193% | 0.869929 | 0.00% |
| UP | same_row_top1_qty_target_qty_8_cap_0.965 | 8 | 29 | 28 | 29/29 | 117.899149 | 15.703352 | 13.3193% | 0.869929 | 0.00% |

Interpretation:

- UP 分支质量更高，但覆盖更低。
- DOWN 分支覆盖略高，top1_qty gate 后深度风险被清掉。
- target_qty=8 没有破坏 L2 cleanliness；但 actions/markets 仍低，不能按线性容量外推。
- same_row 与 entry_paircap 在该设置下结果相同或接近，说明核心是 top1/opposite depth + paircap，而不是等待 completion。

## 当前最优模板

建议把当前研究主线改成：

```text
CE25_BTC5M_LOW_PRICE_TAIL_SIDE_SPLIT_TOP1_QTY_V2
asset = BTC
timeframe = 5m
time_to_close <= 60s
first_leg_price in [0.20, 0.35]
side = UP or DOWN, but side ledgers are separated
paircap <= 0.965
require opposite top1 qty >= intended qty
target_qty baseline = 5
target_qty validation lane = 8
no longer-SLA default
```

## Keep / Reject

Keep:

- `last60_up_20_35_side_split_same_row_top1_qty_cap_0.965`
- `last60_up_20_35_side_split_entry_paircap_top1_qty_cap_0.965`
- `last60_down_20_35_side_split_same_row_top1_qty_cap_0.965`
- `last60_down_20_35_side_split_entry_paircap_top1_qty_cap_0.965`
- target_qty=8 versions as capacity validation lane.

Reject as default:

- non-top1 DOWN strict branch: almost good, but known depth tail.
- longer SLA branches: higher absolute PnL but residual near 50%.
- broad last60 and full ce25 account: recent decay remains unresolved.

## Caveats

- `top_overlay_required_action_count` is nonzero across all branches. Current validator treats overlay as review note, not a hard fail. A stricter source-of-truth pass should either hard-cap overlay usage or produce an overlay freshness attribution report.
- `p95_raw_l2_age_ms` is about 10s in summaries. Since all top1 canonical L1 pass, raw L2 depth is not the deciding evidence for these clean passes, but freshness still needs attribution before OOS.
- This is local historical replay / book-shadow / L2 top-aligned evidence only.

## Next Step

Build a normalized candidate ledger for `CE25_BTC5M_LOW_PRICE_TAIL_SIDE_SPLIT_TOP1_QTY_V2`, analogous to the high-price target_qty8 ledger, with:

- candidate ids and per-leg evidence;
- side-separated UP/DOWN limits;
- target_qty=5 default and target_qty=8 validation lane;
- hard non-claims: `orders_authorized=false`, `live_ready=false`, `deployable=false`;
- overlay freshness attribution before any OOS-style review.
