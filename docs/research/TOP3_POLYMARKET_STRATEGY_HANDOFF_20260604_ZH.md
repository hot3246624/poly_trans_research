# Polymarket Crypto Top3 策略交接文档

生成时间：2026-06-04 BJT

> 后续复核说明：本 handoff 已被 `TOP3_POLYMARKET_STRATEGY_AUTORESEARCH_20260604_ZH.md` 覆盖。后续本地 book-shadow autoresearch 显示，9F5F 只能保留为低覆盖 micro-alpha watch，Username123123 因扩大窗口为负而停车；不要再按本文原始排序直接启动 9F5F/Username profile refresh 或 shadow/OOS 主线。

## 结论

这份文档只整理当前最值得交给实现同事推进的前 3 个研究方向。排序是“可学习策略优先级”，不是账户盈利排行榜。

| 排名 | 策略/对象 | 角色 | 当前结论 | 实现优先级 |
| ---: | --- | --- | --- | --- |
| 1 | `9F5F_BTC_LAST60_MIDPRICE_V1` | 高收益候选 | 当前 24h 样本收益最高，适合马上转 shadow policy | P0 |
| 2 | `CE25_STABLE_BUCKET_PACKAGE_V1` | 稳态基准 + 风控模板 | 多窗口证据更扎实，是校准和稳健化核心 | P0/P1 |
| 3 | `USERNAME123123_SHORT_BURST_LOW_RESID_V1` | 低残仓短 burst 候选 | 历史短窗口极强，但需要补最近窗口 | P1 |

不进入 Top3：

- b27bc：低 residual 很强，但收益质量不稳定，已降级为库存闭合/执行机制研究对象。
- nagi：有可学的 fastpair 执行模板，但扩展到 4 个窗口后全账户 ROI 只有 0.68%，且 bad pair-cost share 高。
- b55：历史 burst 很强，但当前 pair edge 退化，暂停主线。

## 统一数据口径

本交接只使用公开 activity/profile 和本地聚合输出。

可以使用：

- fee-inclusive `cash_pnl`
- `buy_actual`
- fee-like cost
- `pair_cost`
- `resid_rate`
- `bad_pc_ge_100_share`
- 公开序列特征，如资产、周期、距收盘时间、首腿价格桶、首腿方向

不能声称：

- 第三方真实 maker/taker
- 私有挂撤单、排队优先级、authenticated `trader_side`
- 可直接实盘部署
- 前端 UI PnL 为真

核心原则：

- 用 `usdcSize` 做 BUY 真实成本。
- `pair_cost` 是事后评价，不是天然可观察入场信号。
- `pair_delay` 是历史结果变量，实现时只能转成自己的执行 SLA。
- 所有候选必须先 shadow/replay，不做真实下单。

## Rank 1: 9F5F_BTC_LAST60_MIDPRICE_V1

### 来源

账户：

`0x9f5ffe76a818dce37c70f947998b52b70671a008`

窗口：

- BJT: 2026-06-03 15:10:23 -> 2026-06-04 15:10:23
- UTC: 2026-06-03 07:10:23 -> 2026-06-04 07:10:23

输出：

- `/Users/hot/web3Scientist/poly_trans_research/data/exports/profile_9f5f_ce25_seed_24h_20260603_1510_to_20260604_1510_bjt/summary.json`
- `/Users/hot/web3Scientist/poly_trans_research/data/exports/profile_9f5f_ce25_seed_24h_20260603_1510_to_20260604_1510_bjt/group_summary.json`
- `/Users/hot/web3Scientist/poly_trans_research/data/exports/profile_9f5f_ce25_seed_24h_20260603_1510_to_20260604_1510_bjt/ce25_market_sequence.csv`

账户级画像：

| 指标 | 数值 |
| --- | ---: |
| activity_rows | 24,008 |
| markets | 1,518 |
| buy_actual | 202,577.64 |
| cash_pnl | 17,273.02 |
| ROI | 8.53% |
| avg_pair_cost_weighted | 0.9475 |
| resid_rate | 25.68% |
| fee-like cost | 27.10 |

全账户 residual 太高，不能复制全账户。真正值得实现的是 BTC last_60s 中价桶。

### 核心证据桶

| 桶 | markets | buy_actual | cash_pnl | ROI | pair_cost | resid_rate | 决策 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| BTC / last_60s / 35-50 | 80 | 41,476.79 | 4,290.83 | 10.35% | 0.9190 | 13.28% | 主分支 |
| BTC / last_60s / 50-65 | 48 | 26,119.19 | 2,873.43 | 11.00% | 0.9530 | 13.43% | 主分支 |
| last_60s / 35-50 / UP | 82 | 20,633.71 | 3,189.85 | 15.46% | 0.9124 | 15.83% | 激进分支 |
| last_60s / 50-65 / DOWN | 62 | 13,664.66 | 1,550.27 | 11.35% | 0.9049 | 18.89% | 高 residual，需 cap |
| last_60s / 50-65 / UP | 76 | 19,870.99 | 2,176.10 | 10.95% | 0.9419 | 17.34% | 高 residual，需 cap |

### 可实现策略草案

```text
policy_id = 9F5F_BTC_LAST60_MIDPRICE_V1

market_filter:
  asset = BTC
  timeframe = 5m
  time_to_close_s <= 60

entry_branches:
  branch_a:
    first_leg_price in [0.35, 0.50)
    side_priority = UP first, DOWN as control
  branch_b:
    first_leg_price in [0.50, 0.65)
    side_priority = DOWN and UP both tested

entry_controls:
  projected_pair_cost_ceiling = 0.95 initially
  watch_layer_pair_cost_ceiling = 0.97
  no entry if estimated completion leg would push pair_cost >= 1.00

inventory_controls:
  target_resid_rate <= 12%
  hard_resid_rate_cap <= 15%
  no averaging down after residual cap breach
  no new first leg if time_to_close_s <= 10

evaluation:
  fee_inclusive_cash_pnl
  pair_pnl
  realized_pair_cost
  resid_rate
  bad_pc_ge_100_share
  per_market_tail_loss
```

### 为什么排第一

- 当前样本最大，买入额和市场数都足够做第一轮 shadow。
- ROI 明显高于 ce25 优选桶。
- BTC last_60s 两个中价桶的 pair_cost 和 resid_rate 同时可接受。

### 主要风险

- 目前只有一个 24h 窗口，存在窗口选择偏差。
- 9f5f 全账户 residual_rate 25.68%，说明它的一部分收益可能来自残仓方向性或后处理。
- 不能用事后 `pair_cost` 当入场条件，必须用当时盘口估算 projected pair cost。

### 给同事的下一步

1. 用 `ce25_market_sequence.csv` 生成 branch-level replay/shadow candidates。
2. 在本地 replay 里模拟 last_60s 的可成交价格和 completion leg。
3. 做 fee stress：0%、0.8%、2.5%、2.83%、3.0%。
4. 增补至少 2 个非连续 24h 公共 profile 窗口，确认不是单日行情。

## Rank 2: CE25_STABLE_BUCKET_PACKAGE_V1

### 来源

账户：

`0xce25e214d5cfe4f459cf67f08df581885aae7fdc`

核心输出：

- `/Users/hot/web3Scientist/poly_trans_research/data/exports/account_autoresearch_iter_ce25_7win_nagi_4win_20260604_hb2_bjt/iteration_report.md`
- `/Users/hot/web3Scientist/poly_trans_research/data/exports/account_autoresearch_iter_ce25_7win_nagi_4win_20260604_hb2_bjt/account_rollup.tsv`
- `/Users/hot/web3Scientist/poly_trans_research/data/exports/account_autoresearch_iter_ce25_7win_nagi_4win_20260604_hb2_bjt/pre_registered_proxy_summary.tsv`
- `/Users/hot/web3Scientist/poly_trans_research/data/exports/account_autoresearch_iter_ce25_7win_nagi_4win_20260604_hb2_bjt/proxy_scoreboard.tsv`

覆盖：

- 7 个 rolling 24h 窗口
- 2026-05-28 11:45 BJT -> 2026-06-04 11:45 BJT

账户级画像：

| 指标 | 数值 |
| --- | ---: |
| windows | 7 |
| markets | 6,082 |
| buy_actual | 1,228,238.67 |
| cash_pnl | 16,923.26 |
| ROI | 1.38% |
| pair_cost | 0.9695 |
| resid_rate | 12.50% |
| fee_rate | 2.52% |
| bad_pc_ge_100_share | 47.41% |

ce25 全账户不能复制，因为 bad pair-cost share 太高。它的价值在于多窗口稳定桶。

### 子策略 A：CE25_BTC5M_LOW_PRICE_TAIL_DOWN_V1

核心桶：

| 条件 | markets | buy_actual | cash_pnl | ROI | pair_cost | resid_rate | bad_pc_ge_100_share |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| last_60s / 20-35 / DOWN | 125 | 29,615.34 | 2,977.76 | 10.05% | 0.8885 | 11.26% | 24.53% |
| BTC / last_60s / 20-35 | 74 | 31,406.73 | 2,833.16 | 9.02% | 0.8893 | 9.79% | 20.23% |
| BTC / 5m / 20-35 | 129 | 44,586.92 | 2,152.47 | 4.83% | 0.9076 | 13.16% | 30.21% |

可实现解释：

- `last_60s` 转成实时条件：距收盘 <= 60 秒。
- `20-35` 转成盘口条件：第一腿可成交价格在 0.20-0.35。
- `DOWN` 是历史更强方向，但实现必须保留 UP 对照。

策略草案：

```text
policy_id = CE25_BTC5M_LOW_PRICE_TAIL_DOWN_V1

market_filter:
  asset = BTC
  timeframe = 5m
  time_to_close_s <= 60

entry_filter:
  preferred_first_side = DOWN
  first_leg_ask_price in [0.20, 0.35]
  projected_pair_cost_ceiling <= 0.97

execution_controls:
  completion_leg_sla_s <= 15 aggressive, <= 30 conservative
  stop_new_entry_if_time_to_close_s <= 10
  target_resid_rate <= 10%
  hard_resid_rate_cap <= 15%

kill_switch:
  rolling_bad_pc_ge_100_share >= 25%
  rolling_resid_rate >= 15%
  single_market_loss exceeds cap
```

### 子策略 B：CE25_BTC5M_HIGH_PRICE_STOP_1_5M_V1

这个更像风控过滤器，不是最高收益 alpha。

| 条件 | 窗口盈利 | markets | buy_actual | cash_pnl | ROI | pair_cost | resid_rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 65-80 且 last_delta=1-5m | 7/7 | 380 | 37,544.00 | 1,891.57 | 5.04% | 0.9738 | 15.15% |
| 65-80 且 last_delta=last_60s | 5/7 | 206 | 40,269.95 | 335.94 | 0.83% | 1.0018 | 10.29% |

结论：

- 65c-80c 高价腿在收盘前 1-5 分钟更稳。
- 到最后 60 秒后 pair_cost 明显变差。
- 这条规则可以作为全局风控：高价腿最后 60 秒停止新开。

策略草案：

```text
policy_id = CE25_BTC5M_HIGH_PRICE_STOP_1_5M_V1

market_filter:
  asset = BTC initially
  timeframe = 5m
  60 < time_to_close_s <= 300

entry_filter:
  first_leg_ask_price in [0.65, 0.80]
  projected_pair_cost_ceiling <= 0.98

execution_controls:
  smaller size than low-price-tail policy
  do not open new first leg when time_to_close_s <= 60
  residual cap <= 10%
```

### 为什么排第二

- 多窗口证据比 9f5f 更稳。
- 可以同时提供 alpha 桶和风控模板。
- 适合作为实现框架的校准对象：如果 replay/shadow 连 ce25 优选桶都复现不了，就不该直接推进更激进的 9f5f。

### 主要风险

- ce25 全账户 ROI 不高，不能复制全量。
- `bad_pc_ge_100_share` 在账户级接近 47%，说明大量成交是低质量或风控/库存行为。
- 低价尾段桶里的 DOWN 优势可能随行情结构变化。

### 给同事的下一步

1. 先实现 `CE25_BTC5M_LOW_PRICE_TAIL_DOWN_V1` 作为基准 alpha。
2. 同时实现 `CE25_BTC5M_HIGH_PRICE_STOP_1_5M_V1` 作为过滤器。
3. 用 7 个历史窗口做 pre-registered replay，不允许只挑盈利窗口。
4. 与 9f5f 策略共用同一套 residual cap、pair-cost ceiling、fee stress。

## Rank 3: USERNAME123123_SHORT_BURST_LOW_RESID_V1

### 来源

账户：

`0xd950a1a89f3e61a7a9efc85a46e440ce58c15e86`

已有输出：

- `/Users/hot/web3Scientist/poly_trans_research/data/exports/deep_username123123_20260527_1530_1550_bjt`
- `/Users/hot/web3Scientist/poly_trans_research/data/exports/deep_username123123_20260527_1225_1245_bjt`

### 核心证据

| 窗口 | markets | buy_actual | PnL 口径 | PnL | ROI | pair_cost | resid_rate | 决策 |
| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | --- |
| 2026-05-27 15:30-15:50 BJT | 14 | 19,056.81 | cash_plus_current_plus_rebate | 5,360.54 | 28.13% | 0.9327 | 2.64% | 补样本 |
| 2026-05-27 12:25-12:45 BJT | 13 | 13,708.15 | cash_pnl | 1,133.01 | 8.27% | 0.9734 | 1.92% | 补样本 |

### 当前判断

这是最干净的低残仓短 burst 候选之一，但证据还太短。

它排在第三，不是因为收益低，而是因为样本不足：

- 目前主要是 2026-05-27 的短窗口。
- 第一个窗口使用 `cash_plus_current_plus_rebate`，需要与纯 cash PnL、结算后 PnL 分开核对。
- 还没有足够的当前窗口证明它仍然有效。

### 待提炼策略方向

当前不能直接给最终 policy，只能先做 profile refresh。实现同事应重点寻找它是否也落在以下模式：

```text
candidate_id = USERNAME123123_SHORT_BURST_LOW_RESID_V1

profile_features_to_extract:
  asset
  timeframe
  time_to_close_bucket
  first_leg_price_bucket
  first_side
  pair_delay_bucket for profile only
  realized_pair_cost
  resid_rate
  old_no_buy_cash / current_value split if available

hypothesis:
  account may use short burst low-residual pair capture
  possible overlap with 9f5f BTC last_60s mid-price
  possible overlap with ce25 low-price tail

promotion_gate:
  at least 3 non-contiguous windows
  buy_actual >= 10,000 per accepted window
  resid_rate <= 5% preferred, <= 8% hard
  pair_cost <= 0.97 preferred
  fee-inclusive cash_pnl > 0 after old-position cashflow split
```

### 为什么排第三

- 低 residual 比 9f5f 更漂亮。
- ROI 在短窗口非常高。
- 如果补样本后仍成立，可能比 ce25 更适合小资金高周转。

### 主要风险

- 短窗口可能只是行情特例。
- 样本量只有十几个市场，不足以确认稳定性。
- 部分 PnL 口径混入 current/rebate，需要重新拆账。

### 给同事的下一步

1. 拉最近 24h、72h，以及至少两个历史非连续窗口。
2. 使用与 9f5f 同样的 bucket 维度拆解：asset、tf、last_delta、first_price_bucket、first_side。
3. 严格剥离旧仓现金流和 current_value。
4. 若连续窗口仍满足低 residual + 正 pair PnL，再转成 shadow policy。

## 不进入 Top3 的说明

### b27bc

修正报告：

`/Users/hot/web3Scientist/poly_trans_research/docs/research/B27BC_REASSESSMENT_20260604_ZH.md`

结论：

- residual control 很强，1.5%-2.5% 区间非常优秀。
- 但收益质量不稳定，多个窗口 actual_pair_cost 接近或高于 1。
- 当前 6h 表面 `cash_pnl_total` 为正，但剥离旧仓 cash-in 后为负。
- 研究价值在执行/库存闭合，不是高收益 alpha。

### nagi

结论：

- 有 fastpair 执行模板价值。
- 但扩展到 4 个 24h 窗口后，全账户 ROI 只有 0.68%。
- `nagi_last60_first35_50_fastpair` bad pair-cost share 43.62%，未通过默认 safe filter。
- 暂时不列入前三。

### b55

结论：

- 历史 burst 很强。
- 当前 pair edge 退化，2026-06-04 附近 quick screen 已显示负面。
- 暂停主线。

## 统一实现验收标准

所有 Top3 候选都必须通过以下 gate：

1. Public profile gate
   - 至少 3 个非连续窗口。
   - 每个窗口报告 `buy_actual`、fee、`cash_pnl`、`pair_cost`、`resid_rate`、`bad_pc_ge_100_share`。
   - 不使用 frontend UI PnL。

2. Ex-ante gate
   - 入场只允许用当时可观察信息。
   - `pair_cost` 只能作为评价，不能直接作为入场信号。
   - `pair_delay` 只能转成自己的 completion SLA。

3. Replay/shadow gate
   - 使用本地 strict V2/cache/replay。
   - 输出 per-market decision log。
   - 输出 per-market tail loss。
   - 输出 missed-fill / adverse-fill 情况。

4. Fee stress gate
   - 0%
   - 0.8%
   - 2.5%
   - 2.83%
   - 3.0%

5. Capital recycling gate
   - 报告 deployed buy ROI。
   - 单独估算 merge 循环后的初始本金 ROI。
   - 不能把高 turnover deployed ROI 直接当本金日化。

## 推荐分工

| 任务 | Owner 类型 | 输入 | 输出 |
| --- | --- | --- | --- |
| 9f5f shadow policy | 策略实现 | 9f5f `ce25_market_sequence.csv` | branch-level shadow report |
| ce25 benchmark replay | 回测/验证 | ce25 7-window autoresearch 输出 | baseline replay report |
| username123123 refresh | 数据抓取/分析 | username123123 地址和旧 deep 输出 | 24h/72h profile + bucket table |
| common risk gates | 风控/框架 | 三个候选策略 | residual/pair-cost/fee stress shared evaluator |

## 最小推进顺序

1. 先做 ce25 benchmark replay，确认框架能复现已知稳定桶。
2. 再做 9f5f BTC last_60s shadow，追求更高收益。
3. 并行补 username123123 最近窗口，判断是否升级为 P0。
4. b27bc 只做 residual 机制参考，不进入高收益主线。
