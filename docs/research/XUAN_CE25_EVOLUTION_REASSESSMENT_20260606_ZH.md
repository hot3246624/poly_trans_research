# xuanxuan008 是否进步、ce25 是否是 xuan 终极形态：2026-06-06 复核

生成时间：2026-06-06 BJT

## 结论

xuanxuan008 最近确实有“风控形态上的进步”，但不是“盈利能力上的进步”。

更准确地说：

- 它的参与规模大幅下降。
- 它的残仓几乎清零。
- 它仍然是 BTC 5m 的成对/merge 型交易。
- 但它的 fee-inclusive `pair_cost` 仍然大于 1，最近 72h 仍亏。
- 它最新问题不是 residual，而是入场过滤不够强，太多市场最终 `pair_cost >= 1.00`。

ce25 不是 xuan 的“终极形态”。ce25 更像是 xuan 想靠近的另一个分支：高吞吐、多桶筛选、有 alpha pocket，但也有大量低质量成交。若只看“pair-arb/补腿闭合”的大框架，ce25 比 xuan 更成熟；但若看低残仓，xuan 当前反而更极端。ce25 的价值不是“全账户复制”，而是它的优选桶和风控模板。

## 数据来源

### 最新 xuan 24h profile

命令：

```bash
python3 scripts/profile_ce25_execution_pattern.py \
  --user 0xcfb103c37c0234f524c632d964ed31f117b5f694 \
  --start-iso 2026-06-05T03:10:00Z \
  --end-iso 2026-06-06T03:10:00Z \
  --window-hours 1 \
  --activity-types TRADE,MERGE,REDEEM,MAKER_REBATE,SPLIT \
  --retries 2 \
  --timeout 20 \
  --pause-ms 120 \
  --output-dir data/exports/profile_xuan_latest_24h_20260605_1110_to_20260606_1110_bjt
```

输出：

`/Users/hot/web3Scientist/poly_trans_research/data/exports/profile_xuan_latest_24h_20260605_1110_to_20260606_1110_bjt`

### 最新 xuan 72h profile

命令：

```bash
python3 scripts/profile_ce25_execution_pattern.py \
  --user 0xcfb103c37c0234f524c632d964ed31f117b5f694 \
  --start-iso 2026-06-03T03:10:00Z \
  --end-iso 2026-06-06T03:10:00Z \
  --window-hours 1 \
  --activity-types TRADE,MERGE,REDEEM,MAKER_REBATE,SPLIT \
  --retries 2 \
  --timeout 20 \
  --pause-ms 120 \
  --output-dir data/exports/profile_xuan_latest_72h_20260603_1110_to_20260606_1110_bjt
```

输出：

`/Users/hot/web3Scientist/poly_trans_research/data/exports/profile_xuan_latest_72h_20260603_1110_to_20260606_1110_bjt`

### 最新 ce25 24h profile

命令：

```bash
python3 scripts/profile_ce25_execution_pattern.py \
  --user 0xce25e214d5cfe4f459cf67f08df581885aae7fdc \
  --start-iso 2026-06-05T03:10:00Z \
  --end-iso 2026-06-06T03:10:00Z \
  --window-hours 1 \
  --activity-types TRADE,MERGE,REDEEM,MAKER_REBATE,SPLIT \
  --retries 2 \
  --timeout 20 \
  --pause-ms 120 \
  --output-dir data/exports/profile_ce25_latest_24h_20260605_1110_to_20260606_1110_bjt
```

输出：

`/Users/hot/web3Scientist/poly_trans_research/data/exports/profile_ce25_latest_24h_20260605_1110_to_20260606_1110_bjt`

历史 ce25 多窗口基准：

`/Users/hot/web3Scientist/poly_trans_research/data/exports/account_autoresearch_iter_ce25_7win_nagi_4win_20260604_hb2_bjt/iteration_report.md`

## xuan 最新表现

### 最新 24h

窗口：

- BJT: 2026-06-05 11:10 -> 2026-06-06 11:10

| 指标 | 数值 |
| --- | ---: |
| markets | 40 |
| activity_rows | 268 |
| buy_actual | 4,517.40 |
| fee | 124.39 |
| fee_rate_est | 2.8316% |
| cash_pnl | -123.23 |
| ROI | -2.73% |
| avg_pair_cost_weighted | 1.0280 |
| resid_rate | 0.00% |
| bad_pc_ge_100_share | 74.92% |
| pair_cost < 0.98 share | 7.61% |

解释：

这不是盈利窗口。xuan 做到了几乎完全配对，但配对成本太高。`avg_pair_cost_weighted = 1.0280` 基本说明费后没有 edge。

### 最新 72h

窗口：

- BJT: 2026-06-03 11:10 -> 2026-06-06 11:10

| 指标 | 数值 |
| --- | ---: |
| markets | 207 |
| activity_rows | 2,048 |
| buy_actual | 42,474.20 |
| fee | 1,163.07 |
| cash_pnl | -669.39 |
| ROI | -1.58% |
| avg_pair_cost_weighted | 1.0160 |
| resid_rate | 0.0001% |
| bad_pc_ge_100_share | 69.27% |
| pair_cost < 0.98 share | 15.03% |

解释：

72h 比 24h 稍好，但仍亏。它已经把 residual 几乎压到 0，但近 70% 的 buy notional 落在 `pair_cost >= 1.00` 的市场，这是核心问题。

## xuan 和 5 月旧窗口对比

| 窗口 | markets | buy_actual | cash_pnl | ROI | actual/pair_cost | resid_rate | fee_rate | 结论 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 2026-05-12 -> 05-19 7d | 1,803 | 1,254,064.98 | -12,422.27 | -0.99% | 1.0100 | 0.0751% | 2.7921% | 高量、低残仓、费后亏 |
| 2026-05-21 -> 05-22 24h | 259 | 172,349.75 | -426.44 | -0.25% | 1.0039 | 0.3504% | 2.7821% | 接近打平，但仍亏 |
| 2026-05-27 15:45 -> 05-28 15:45 | 234 | 81,795.83 | -1,403.28 | -1.72% | 1.0175 | 0.0118% | 2.8201% | 低残仓、pair_cost 变差 |
| 2026-05-28 09:45 -> 15:45 | 58 | 23,570.37 | -549.06 | -2.33% | 1.0238 | 0.0028% | 2.8336% | 小窗口更差 |
| 2026-06-03 -> 06-06 72h | 207 | 42,474.20 | -669.39 | -1.58% | 1.0160 | 0.0001% | 约 2.82% | 规模收缩、残仓极低、仍亏 |
| 2026-06-05 -> 06-06 24h | 40 | 4,517.40 | -123.23 | -2.73% | 1.0280 | 0.00% | 2.8316% | 极小规模、仍亏 |

判断：

xuan 的进步主要是：

1. 参与规模收缩，风险暴露变小。
2. residual 从“很低”进一步变成“几乎没有”。
3. 行为更像纯 BTC 5m 低库存 pair/merge。

但没有看到：

1. 费后 pair_cost 改善到稳定低于 1。
2. 现金 PnL 转正。
3. 对低质量 pair_cost 市场的过滤显著变强。

所以不能说 xuan 已经变强到值得学习。最多说它停止了以前那种高量亏损，变成了低量、低残仓、仍亏的状态。

## xuan 当前亏损结构

最近 72h 的 pair_cost 桶：

| pair_cost bucket | markets | buy_actual | cash_pnl | ROI | 解释 |
| --- | ---: | ---: | ---: | ---: | --- |
| <0.85 | 5 | 115.59 | +46.81 | +40.50% | 样本太小 |
| 0.85-0.90 | 3 | 180.64 | +23.06 | +12.77% | 样本太小 |
| 0.90-0.95 | 15 | 3,061.98 | +219.62 | +7.17% | 有价值，但占比小 |
| 0.95-0.98 | 21 | 3,027.62 | +108.96 | +3.60% | 有价值，但占比小 |
| 0.98-1.00 | 35 | 6,668.40 | +55.95 | +0.84% | 接近打平 |
| 1.00-1.05 | 90 | 19,541.02 | -460.59 | -2.36% | 最大拖累之一 |
| 1.05-1.10 | 32 | 8,471.76 | -508.81 | -6.01% | 明显拖累 |
| >=1.10 | 6 | 1,407.18 | -154.38 | -10.97% | 明显拖累 |

核心问题：

`pair_cost < 0.98` 的买入占比只有 15.03%，`pair_cost >= 1.00` 的买入占比高达 69.27%。xuan 的策略不是没有好市场，而是好市场权重太低，坏市场权重太高。

## ce25 最新表现

### 最新 24h

窗口：

- BJT: 2026-06-05 11:10 -> 2026-06-06 11:10

| 指标 | 数值 |
| --- | ---: |
| markets | 835 |
| activity_rows | 21,526 |
| buy_actual | 279,668.72 |
| fee | 7,262.74 |
| fee_rate_est | 2.6661% |
| cash_pnl | -2,416.50 |
| ROI | -0.86% |
| avg_pair_cost_weighted | 1.0116 |
| resid_rate | 8.33% |
| bad_pc_ge_100_share | 61.76% |
| pair_cost < 0.98 share | 34.26% |

ce25 最新 24h 全账户也不是好窗口。尤其是 `last_60s` 桶当前亏损明显：

| bucket | markets | buy_actual | cash_pnl | ROI | resid_rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1-5m | 384 | 82,443.35 | +2,000.24 | +2.43% | 11.91% |
| 5-15m | 110 | 15,121.83 | +1,703.38 | +11.26% | 19.54% |
| last_60s | 341 | 182,103.54 | -6,120.12 | -3.36% | 5.73% |

解释：

ce25 不是每天都强，当前全账户也在被低质量桶拖累。但它和 xuan 最大不同是：ce25 有更大比例的好 pair-cost 市场，也有历史多窗口正收益的优选桶。

### ce25 多窗口基准

来自 2026-06-04 的 7 个 rolling 24h 窗口：

| 对象 | windows | markets | buy_actual | cash_pnl | ROI | pair_cost | resid_rate | bad_pc_ge_100_share |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| ce25 full account | 7 | 6,082 | 1,228,238.67 | 16,923.26 | 1.38% | 0.9695 | 12.50% | 47.41% |

ce25 最值得学的是优选桶：

| 策略桶 | markets | buy_actual | cash_pnl | ROI | pair_cost | resid_rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| ce25_btc5m_first20_35 | 129 | 44,586.92 | 2,152.47 | 4.83% | 0.9076 | 13.16% |
| ce25_first65_80_stop_1_5m | 380 | 37,544.00 | 1,891.57 | 5.04% | 0.9738 | 15.15% |

## ce25 是 xuan 的终极形态吗？

不是。

更准确的关系是：

### 相似点

- 都围绕 crypto up/down 的 YES/NO 配对闭合。
- 都需要处理 fee-inclusive pair_cost。
- 都不是靠长期裸方向仓作为主模式。
- 都不能用公开数据证明真实 maker/taker。

### 关键差异

| 维度 | xuan 当前 | ce25 |
| --- | --- | --- |
| 资产/周期 | 近 72h 仅 BTC 5m | BTC/ETH/SOL/XRP，5m/15m 等 |
| 参与规模 | 明显收缩，72h buy 42,474 | 高吞吐，最新 24h buy 279,669 |
| residual | 几乎 0 | 8%-12% 常见 |
| pair_cost 质量 | 72h 平均 1.0160 | 历史 7 窗口 0.9695，但最新 24h 1.0116 |
| 好桶占比 | `pc<0.98` 仅 15.03% | 最新 24h `pc<0.98` 为 34.26%，历史优选桶更强 |
| 主要问题 | 入场过滤太弱，费后亏 | 全账户仍有垃圾桶，但可提炼优选桶 |
| 可模仿价值 | 低，最多学低残仓闭合 | 中高，学优选桶和风控模板 |

### 判断

如果把 xuan 理解成“极低 residual 的机械 pair/merge”，ce25 不是它的终极形态，因为 ce25 residual 更高、风险更复杂。

如果把 xuan 理解成“想在 pair-arb 框架里找到正期望”，ce25 的优选桶确实更接近 xuan 应该进化的方向：不是更快配对，而是更强入场过滤、更低 bad pair-cost share、更高好桶权重。

## 给后续研究的方向

### xuan 研究不应再问“能不能配对”

答案已经很清楚：能。最近 72h residual 几乎为 0。

真正要问的是：

1. 哪些开仓前可观察特征能预测 `pair_cost < 0.98`？
2. 为什么它仍然让 69.27% 的 buy notional 落入 `pair_cost >= 1.00`？
3. 它是否只是缩量停损，而不是策略升级？

### ce25 研究不应复制全账户

最新 24h ce25 全账户也亏，说明全账户复制仍然不成立。

后续只应研究：

1. `ce25_btc5m_first20_35`
2. `ce25_first65_80_stop_1_5m`
3. 当前 24h 里正收益的 `1-5m` 和 `5-15m` 桶
4. 为什么 `last_60s` 在最新 24h 变成主要亏损来源

### 交叉结论

最值得实现的不是“复制 xuan”或“复制 ce25”，而是：

```text
XUAN_COMPLETION_DISCIPLINE
  + CE25_ENTRY_FILTERS
  + 9F5F_HIGH_YIELD_LAST60_BUCKETS
```

也就是说：

- 用 xuan 的低 residual 作为执行约束；
- 用 ce25 的优选桶做稳态过滤；
- 用 9f5f 的 BTC last_60s 中价桶追求收益；
- 用统一的 pair_cost ceiling 和 residual cap 做风控。

## 最终定论

xuanxuan008 最近没有真正变成高手。它只是把风险收小、残仓清干净了，但 fee 后仍在亏。

ce25 也不是 xuanxuan008 的终极形态。ce25 是更复杂、更高吞吐、更有可提炼 alpha pocket 的账户；它代表“xuan 应该补上的入场过滤能力”，但不是“完美版 xuan”。

当前研究主线应保持不变：

1. 第一优先级仍是 9f5f BTC last_60s 中价桶。
2. ce25 优选桶作为稳态基准和风控模板。
3. xuan 只作为“低 residual completion discipline”的参考，不再作为核心 alpha 学习对象。
