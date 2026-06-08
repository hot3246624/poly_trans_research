# b27bc 重新评估：低残仓不等于高质量盈利

生成时间：2026-06-04 BJT

## 修正结论

我上一轮把 b27bc 提到过高，原因是只用了 2026-06-03 15:10 -> 2026-06-04 15:10 BJT 的 leaderboard quick screen，并把“低 residual + 当前正 PnL”看得过重。

重新纳入历史本地输出和刚补跑的 6h deep_compare 后，b27bc 的更准确结论是：

- 它确实是高频、低残仓、强库存闭合账户。
- 但它不是当前最高优先级的可模仿策略对象。
- 它的 pair_cost 质量并不稳定，多个窗口接近或高于 1。
- 它的表面 `cash_pnl_total` 会被窗口前买入、窗口内 redeem 的旧仓现金流放大。
- 更严格地只看本窗口有 buy 的市场，当前 6h 反而是负数。

因此，b27bc 应从“P1 强策略候选”降级为“低残仓基础设施/执行风控研究对象”。它可以用来研究库存闭合和高频撮合，但不应被列为 9f5f/ce25 之后的核心 alpha 复制对象。

## 证据 1：2026-06-03 06:06 -> 12:06 BJT 旧 deep_compare

来源：

`/Users/hot/web3Scientist/poly_trans_research/data/exports/deep_compare_b27bc_current_6h_20260603_0606_to_1206_bjt/b27bc/summary.json`

| 指标 | 数值 |
| --- | ---: |
| row_count | 40,128 |
| with_buy_markets | 178 |
| buy_actual | 297,088.10 |
| fee | 1,821.23 |
| fee_rate_on_gross | 0.6168% |
| cash_pnl_total | -1,387.99 |
| with_buy_plus_current_ex_no_condition_rebate | -4,749.57 |
| with_buy_plus_current_plus_no_condition_rebate | -945.68 |
| actual_pair_cost | 1.0117 |
| gross_pair_cost | 1.0055 |
| paired_actual_profit | -3,353.36 |
| resid_rate_on_buy_qty | 2.25% |

解释：

这一窗口非常关键：b27bc 的 residual 确实极低，但 pair_cost 已经高于 1，paired component 是负的。也就是说它“收得干净”，但并没有“买得便宜”。

## 证据 2：2026-06-04 10:00 -> 16:00 BJT 新补跑 deep_compare

命令：

```bash
python3 scripts/deep_compare_public_activity_candidates.py \
  --account b27bc \
  --start-iso 2026-06-04T02:00:00Z \
  --end-iso 2026-06-04T08:00:00Z \
  --window-hours 1 \
  --retries 2 \
  --timeout 15 \
  --pause-ms 100 \
  --output-dir data/exports/deep_compare_b27bc_current_6h_20260604_1000_to_1600_bjt_recheck
```

来源：

`/Users/hot/web3Scientist/poly_trans_research/data/exports/deep_compare_b27bc_current_6h_20260604_1000_to_1600_bjt_recheck/b27bc/summary.json`

账户级结果：

| 指标 | 数值 |
| --- | ---: |
| row_count | 40,048 |
| with_buy_markets | 172 |
| buy_actual | 306,806.31 |
| buy_gross | 303,686.20 |
| fee | 3,120.11 |
| fee_rate_on_gross | 1.0274% |
| cash_pnl_total | +4,612.79 |
| old_no_buy_cash | +6,478.43 |
| with_buy_cash_ex_no_condition_rebate | -1,865.64 |
| current_value | 625.37 |
| with_buy_plus_current_ex_no_condition_rebate | -1,240.27 |
| actual_pair_cost | 0.9980 |
| gross_pair_cost | 0.9878 |
| paired_actual_profit | +607.51 |
| resid_rate_on_buy_qty | 1.67% |
| per-market actual_pair_cost p50 | 0.9810 |
| per-market actual_pair_cost p75 | 1.0408 |
| per-market actual_pair_cost p90 | 1.0862 |

解释：

表面 `cash_pnl_total` 是 +4,612.79，但其中 `old_no_buy_cash` 是 +6,478.43。也就是说，有大量现金流来自窗口内没有 buy 的旧仓 redeem/cash-in。

更严格的本窗口交易质量应看：

- `with_buy_cash_ex_no_condition_rebate = -1,865.64`
- `with_buy_plus_current_ex_no_condition_rebate = -1,240.27`

所以这 6h 并不能证明 b27bc 当前盈利质量强。它更像是把旧仓兑现后让窗口 cash PnL 看起来好。

## 证据 3：当前 6h 分资产/周期

来源：

`/Users/hot/web3Scientist/poly_trans_research/data/exports/deep_compare_b27bc_current_6h_20260604_1000_to_1600_bjt_recheck/b27bc/asset_tf_groups.csv`

| 资产/周期 | markets | buy_actual | cash_pnl | current_value | cash_plus_current | pair_pnl | resid_rate | ROI cash | 判断 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| BTC 5m | 71 | 223,060.72 | +974.32 | 0.00 | +974.32 | +557.68 | 1.85% | +0.44% | 低收益、低残仓 |
| BTC 15m | 24 | 34,379.50 | -745.05 | 0.00 | -745.05 | +145.91 | 0.40% | -2.17% | 负 |
| BTC 1h/named | 6 | 3,375.16 | -602.53 | 625.37 | +22.84 | +22.59 | 0.66% | -17.85% | 基本打平靠 current |
| ETH 5m | 71 | 45,990.92 | -1,492.38 | 0.00 | -1,492.38 | -118.67 | 1.78% | -3.24% | 负 |

解释：

如果只研究 BTC 5m，b27bc 仍有一点正收益，但 deployed ROI 只有 0.44%，远低于 9f5f 的 BTC last_60s 桶，也低于 ce25 的优选桶。它的优势是残仓控制，不是收益。

## 为什么 leaderboard quick screen 会高估 b27bc

上一轮 quick screen 的 b27bc 指标：

| 窗口 | markets | buy_actual | cash_pnl_observed | ROI | actual_pair_cost | resid_rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2026-06-03 15:10 -> 2026-06-04 15:10 BJT | 14 | 13,833.90 | +2,114.78 | 15.29% | 0.9583 | 2.29% |

这个 screen 仍有价值，但它不是完整策略 profile。问题在于：

- markets 只有 14 个，样本太小；
- 只看 quick screen 容易选中局部好窗口；
- 没有充分剥离窗口前旧仓兑现；
- 无法展示 b27bc 在大量市场里的 pair_cost 分布；
- 与 40k rows 级别的 deep_compare 相比，证据权重应更低。

## 和 9f5f / ce25 的重新排序

| 对象 | 收益质量 | 风控 | 样本 | 可复制价值 | 新排序 |
| --- | --- | --- | --- | --- | ---: |
| 9f5f BTC last_60s 中价桶 | 高 | 中等 residual | 当前 24h 样本大 | 高，适合 shadow policy | 1 |
| ce25 优选桶 | 中 | 中低 residual | 多窗口 | 高，适合基准和稳态验证 | 2 |
| username123123 短 burst | 高 | 低 residual | 样本短 | 中高，需补窗口 | 3 |
| b27bc | 低到中，不稳定 | 极低 residual | 高频样本大 | 中，偏执行/库存闭合研究 | 4 |
| 04b6 | pocket 强但全窗口不稳 | 中 | 有旧样本 | 中低 | 5 |
| b55 | 历史强、当前退化 | 中 | 历史窗口 | 暂停 | 6 |

## 现在对 b27bc 的定论

b27bc 不是 xuan 的终极版本，也不是当前最值得复制的 alpha 账户。

更准确地说：

- 它可能有很强的自动化撮合/库存闭合基础设施；
- 它能把 residual 压到 1.5%-2.5% 区间，这很强；
- 但它的 pair_cost 分布并不够好，p75/p90 经常超过 1；
- fee 后收益容易被吃掉；
- 当前窗口表面盈利很大一部分来自旧仓兑现，不是新交易 alpha。

因此，它仍值得研究，但研究目标应从“寻找可复制赚钱秘籍”改成：

1. 它如何把 residual 做到极低；
2. 它在什么 market/time/price 条件下 pair_cost 能低于 0.98；
3. 它是否存在小范围可复制的 BTC 5m 正桶；
4. 它是否只是高频薄利执行，而不是高盈亏比策略。

## 对上一份报告的修正

需要把 `/Users/hot/web3Scientist/poly_trans_research/docs/research/CE25_SEEDED_HIGH_YIELD_ACCOUNT_STRATEGY_EXPLORATION_ZH.md` 中 b27bc 的优先级下调：

- 删除“最像 ce25 终极形态”的强表述。
- 从 P1 强策略候选改为 P2/Watch。
- 保留 bounded profiler 任务，但目标改为“研究低 residual 机制”，不是“提取高收益策略”。
- 横向探索优先级应回到 9f5f、ce25 优选桶、username123123。
