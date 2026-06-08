# CE25 BTC5m Fee-Inclusive ROI / Participation Frontier

Status: `KEEP_CE25_BTC5M_FEE_ROI_PARTICIPATION_FRONTIER_REVIEW_ONLY_NOT_OOS_READY`

## 结论

你的方向是对的：参与率越高越有价值，但必须同时提高 fee-inclusive ROI。新的前沿搜索显示，CE25 BTC5m 最值得学的不是低覆盖 low-tail，而是 **最后 60 秒主控层**。

最重要的改进：

- 全量 BTC5m：参与率 53.94%，fee-inclusive ROI 1.78%。
- 只保留最后 60 秒：参与率仍有 29.32%，fee-inclusive ROI 提高到 2.17%。
- 最后 60 秒 + DOWN 首腿：参与率 14.93%，ROI 进一步到 2.63%，最近两窗几乎打平。
- `20-35 last60` 的 ROI 9.44%，但参与率只有 2.89%，仍只能做 overlay。

## 推荐车道

| lane | role | participation | fee-inclusive ROI | PnL | pair_cost | residual | bad pc>=1 | recent2 PnL / ROI |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| ALL_BTC5M | full_broad_baseline | 53.94% | 1.78% | $9,930.37 | 0.9508 | 11.70% | 40.32% | $-347.73 / -0.23% |
| last_delta_bucket=last_60s | balanced_high_participation_controller | 29.32% | 2.17% | $8,356.14 | 0.9514 | 10.05% | 39.77% | $-637.73 / -0.58% |
| last_delta_bucket+first_side=last_60s|DOWN | side_filtered_drawdown_controller | 14.93% | 2.63% | $5,258.70 | 0.9431 | 9.72% | 36.39% | $-3.46 / -0.01% |
| first_price_bucket+first_side=50-65|UP | midprice_up_roi_booster | 9.95% | 2.98% | $3,372.56 | 0.9762 | 10.50% | 48.75% | $-55.31 / -0.17% |
| first_price_bucket+last_delta_bucket=20-35|last_60s | low_tail_overlay | 2.89% | 9.44% | $2,941.86 | 0.8667 | 11.24% | 18.47% | $610.82 / 12.39% |

Interpretation:

- P0 应该是 `last_delta_bucket=last_60s`，不是 low-tail。它用约一半 BTC5m 覆盖，拿到全 BTC5m 大部分 PnL，并把 ROI 从 1.78% 提到 2.17%。
- P1 是 `last_60s|DOWN`。它参与率降到 14.93%，但 ROI 到 2.63%，并且最近两窗 PnL 只有 $-3.46，比全 BTC5m 和全 last60 更抗衰减。
- `50-65|UP` 是 ROI booster，但参与率刚低于 10%，最近两窗没有明显优势，暂不当主线。
- `20-35|last60` 仍然很强，但参与率太低，只能叠加。

## Pareto Frontier

| filter | participation | fee-inclusive ROI | PnL | pair_cost | residual | bad pc>=1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| ALL_BTC5M | 53.94% | 1.78% | $9,930.37 | 0.9508 | 11.70% | 40.32% |
| first_delta_bucket=1-5m | 53.51% | 1.78% | $9,927.82 | 0.9508 | 11.67% | 40.34% |
| last_delta_bucket=last_60s | 29.32% | 2.17% | $8,356.14 | 0.9514 | 10.05% | 39.77% |
| last_delta_bucket+first_delta_bucket=last_60s|1-5m | 28.90% | 2.17% | $8,353.59 | 0.9514 | 10.00% | 39.80% |
| last_delta_bucket+first_side=last_60s|DOWN | 14.93% | 2.63% | $5,258.70 | 0.9431 | 9.72% | 36.39% |
| last_delta_bucket+first_delta_bucket+first_side=last_60s|1-5m|DOWN | 14.70% | 2.63% | $5,260.38 | 0.9431 | 9.68% | 36.39% |
| first_price_bucket+first_side=50-65|UP | 9.95% | 2.98% | $3,372.56 | 0.9762 | 10.50% | 48.75% |
| first_price_bucket+first_delta_bucket+first_side=50-65|1-5m|UP | 9.95% | 2.98% | $3,372.56 | 0.9762 | 10.50% | 48.75% |
| first_price_bucket=20-35 | 5.94% | 5.41% | $2,930.28 | 0.8944 | 13.62% | 26.50% |
| first_price_bucket+first_delta_bucket=20-35|1-5m | 5.94% | 5.41% | $2,930.28 | 0.8944 | 13.62% | 26.50% |
| first_price_bucket+first_side=20-35|DOWN | 3.43% | 6.56% | $2,291.50 | 0.8936 | 11.93% | 27.47% |
| first_price_bucket+first_delta_bucket+first_side=20-35|1-5m|DOWN | 3.43% | 6.56% | $2,291.50 | 0.8936 | 11.93% | 27.47% |
| first_price_bucket+last_delta_bucket=20-35|last_60s | 2.89% | 9.44% | $2,941.86 | 0.8667 | 11.24% | 18.47% |
| first_price_bucket+last_delta_bucket+first_delta_bucket=20-35|last_60s|1-5m | 2.89% | 9.44% | $2,941.86 | 0.8667 | 11.24% | 18.47% |

## 新策略组合假设

```text
CE25_BTC5M_BROAD_LAST60_CONTROLLER_V1
asset = BTC
tf = 5m
primary clock = last_60s
base lane = all first_side
risk lane = first_side DOWN
overlay = first_price 20-35 + last_60s
do not use pair_delay as live entry condition
do not use cash_pnl/pair_cost/residual as ex-ante entry condition
```

## 需要继续验证

1. 把 `BTC5M_LAST60` 转成 ex-ante candidate ledger，检查每个市场入口是否可以用公开盘口在事前识别。
2. 单独验证 `last60 DOWN` 为什么最近两窗比 all-last60 抗跌。
3. 检查 `last60 UP` 是不是当前市场环境下的拖累项。
4. 再做 L1/L2 book-shadow：参与率 29% 的策略比 2.9% overlay 更值得消耗验证预算。

This is public-only/review-only. It does not prove CE25 private trader_side, queue priority, maker-only behavior, or deployable live performance.
