# CE25 种子扩展：高盈亏比账号与策略探索报告

生成时间：2026-06-04 BJT

## 2026-06-04 复核勘误：b27bc 已降级

后续重新纳入 b27bc 的旧窗口和新补跑 6h deep_compare 后，确认本报告对 b27bc 的优先级提得过高。b27bc 的 residual control 很强，但 fee-inclusive 收益质量不稳定，且当前 6h 的表面正 PnL 受到窗口前旧仓 redeem/cash-in 放大。

修正文档：

`/Users/hot/web3Scientist/poly_trans_research/docs/research/B27BC_REASSESSMENT_20260604_ZH.md`

修正后排序：

- 9f5f BTC last_60s 中价桶仍是最高优先级 shadow policy 候选。
- ce25 优选桶仍是稳态基准。
- username123123 需要补窗口，优先级高于 b27bc。
- b27bc 从“强策略候选”降为“低 residual/库存闭合机制研究对象”。

## 结论先行

这轮不再把 ce25 或 nagi 当成唯一跟踪对象，而是把 ce25 的研究成果当成筛选模板，去找更高收益、更高盈亏比、同时仍有可复现可能的账号/策略桶。

当前最值得推进的方向不是完整复制某个账户，而是：

1. `9F5F_BTC_LAST60_MIDPRICE_V1`：收益最高、样本最大，适合马上转成 shadow policy 验证；但残仓风险明显高于 ce25，不能照抄全账户。
2. `USERNAME123123_SHORT_BURST_LOW_RESID_V1`：历史短窗口非常干净，低残仓、高 ROI，是值得补齐多窗口样本的高优先级候选。
3. `B27BC_LOW_RESIDUAL_EXECUTION_STUDY_V1`：残仓极低、频率极高，但收益质量不稳定；现在只作为低 residual/库存闭合机制研究对象。
4. ce25 仍然保留为基准模板，不是因为收益最高，而是因为窗口更多、桶更稳定、便于转成可验证规则。

明确拒绝：b55 不能作为当前主学习对象。它有过很强的历史 burst，但 2026-06-04 当前榜单窗口 pair edge 已转负，像是环境依赖或策略退化。

## 数据边界

本报告只使用公开数据和本地既有研究输出。

可以支持的结论：

- fee-inclusive `cash_pnl`
- `buy_actual`
- `fee-like cost`
- `pair_cost`
- `resid_rate`
- 公开活动序列特征，如资产、周期、首腿价格桶、临近收盘时间桶
- 可转成 shadow policy 的 proxy 规则

不能支持的结论：

- 第三方真实 authenticated `trader_side`
- 私有 maker-only / taker-only 真相
- 排队优先级、挂撤单细节
- 可直接上线交易
- 私钥、签名、下单、撤单、redeem 相关操作

Frontend UI PnL 不作为 canonical source。

## 本轮新增证据

### 1. 当前榜单 24h 快筛

脚本：

```bash
python3 scripts/screen_leaderboard_crypto_agents.py \
  --top 80 \
  --category crypto \
  --period week \
  --days 1 \
  --max-offset 1500 \
  --retries 3 \
  --timeout 20 \
  --pause-ms 120 \
  --output-dir data/exports/leaderboard_crypto_week_top80_24h_screen_20260604_ce25_seed_bjt
```

窗口：

- BJT: 2026-06-03 15:10:23 -> 2026-06-04 15:10:23
- UTC: 2026-06-03 07:10:23 -> 2026-06-04 07:10:23

输出：

- `/Users/hot/web3Scientist/poly_trans_research/data/exports/leaderboard_crypto_week_top80_24h_screen_20260604_ce25_seed_bjt/summary.json`
- `/Users/hot/web3Scientist/poly_trans_research/data/exports/leaderboard_crypto_week_top80_24h_screen_20260604_ce25_seed_bjt/summary.csv`
- `/Users/hot/web3Scientist/poly_trans_research/data/exports/leaderboard_crypto_week_top80_24h_screen_20260604_ce25_seed_bjt/market_rows.csv`
- `/Users/hot/web3Scientist/poly_trans_research/data/exports/leaderboard_crypto_week_top80_24h_screen_20260604_ce25_seed_bjt/asset_tf_groups.csv`

严格筛选条件：

- `buy_actual >= 5000`
- `updown_markets >= 10`
- `cash_pnl_observed > 0`
- `actual_pair_cost < 0.98`
- `resid_rate_on_buy_qty < 20%`

命中者只有一个：

| 账户 | 周榜排名 | 市场数 | buy_actual | cash_pnl | ROI | actual_pair_cost | resid_rate | fee_rate | 决策 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `0xb27bc932bf8110d8f78e55da7d5f0497a18b5b82` | 69 | 14 | 13,833.90 | 2,114.78 | 15.29% | 0.9583 | 2.29% | 0.82% | 降级，Watch |

解释：b27bc 是当前榜单里唯一同时满足低残仓、低 pair cost、正收益、足够交易量的候选。但后续 deep_compare 复核显示，这个 quick screen 高估了它的收益质量；它的主要确定性是 residual control，而不是稳定 alpha。

限制：对 b27bc 跑 24h 深 profile 时公开 API 卡住，进程已终止，没有写出 profile 文件。后续 6h deep_compare 已能说明它应降级为执行/库存闭合研究对象。

### 2. 9f5f 深 profile

脚本：

```bash
python3 scripts/profile_ce25_execution_pattern.py \
  --user 0x9f5ffe76a818dce37c70f947998b52b70671a008 \
  --start-iso 2026-06-03T07:10:23Z \
  --end-iso 2026-06-04T07:10:23Z \
  --activity-types TRADE,MERGE,REDEEM,MAKER_REBATE,SPLIT \
  --retries 3 \
  --timeout 20 \
  --pause-ms 120 \
  --output-dir data/exports/profile_9f5f_ce25_seed_24h_20260603_1510_to_20260604_1510_bjt
```

输出：

- `/Users/hot/web3Scientist/poly_trans_research/data/exports/profile_9f5f_ce25_seed_24h_20260603_1510_to_20260604_1510_bjt/summary.json`
- `/Users/hot/web3Scientist/poly_trans_research/data/exports/profile_9f5f_ce25_seed_24h_20260603_1510_to_20260604_1510_bjt/group_summary.json`
- `/Users/hot/web3Scientist/poly_trans_research/data/exports/profile_9f5f_ce25_seed_24h_20260603_1510_to_20260604_1510_bjt/ce25_market_sequence.csv`
- `/Users/hot/web3Scientist/poly_trans_research/data/exports/profile_9f5f_ce25_seed_24h_20260603_1510_to_20260604_1510_bjt/raw_activity.json`

账户级结果：

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

解释：9f5f 的全账户收益非常强，但 25.68% residual 太高，不能作为完整账户复制。真正有价值的是里面的 BTC last_60s 中价区间。

## 9f5f 可提炼策略桶

### A. 按资产、临近收盘、首腿价格桶

| 策略桶 | 市场数 | buy_actual | cash_pnl | ROI | pair_cost | resid_rate | 决策 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| BTC / last_60s / 35-50 | 80 | 41,476.79 | 4,290.83 | 10.35% | 0.9190 | 13.28% | 保留，P0 |
| BTC / last_60s / 50-65 | 48 | 26,119.19 | 2,873.43 | 11.00% | 0.9530 | 13.43% | 保留，P0 |
| ETH / last_60s / 35-50 | 53 | 6,323.40 | 798.33 | 12.63% | 0.8904 | 25.66% | 暂缓，高 residual |
| ETH / last_60s / 50-65 | 48 | 6,340.34 | 785.82 | 12.39% | 0.8167 | 30.30% | 暂缓，高 residual |

判断：BTC 两个桶的收益和风控更平衡，是本轮最适合转成 shadow policy 的新候选。

### B. 按临近收盘、首腿价格桶、首腿方向

| 策略桶 | 市场数 | buy_actual | cash_pnl | ROI | pair_cost | resid_rate | 决策 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| last_60s / 35-50 / UP | 82 | 20,633.71 | 3,189.85 | 15.46% | 0.9124 | 15.83% | 保留，激进 |
| last_60s / 50-65 / DOWN | 62 | 13,664.66 | 1,550.27 | 11.35% | 0.9049 | 18.89% | 保留，需 residual cap |
| last_60s / 50-65 / UP | 76 | 19,870.99 | 2,176.10 | 10.95% | 0.9419 | 17.34% | 保留，需 residual cap |
| last_60s / 35-50 / DOWN | 104 | 28,335.70 | 1,804.33 | 6.37% | 0.9164 | 16.58% | 次优 |

判断：`last_60s / 35-50 / UP` 是 9f5f 样本里最强的单桶，但它不是低风险桶。它适合做 shadow 验证，不适合直接当实盘规则。

## 与 ce25 基准对比

ce25 最新多窗口基准来自：

`/Users/hot/web3Scientist/poly_trans_research/data/exports/account_autoresearch_iter_ce25_7win_nagi_4win_20260604_hb2_bjt/iteration_report.md`

| 对象 | 窗口 | markets | buy_actual | cash_pnl | ROI | pair_cost | resid_rate | bad_pc_ge_100_share | 角色 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| ce25 full account | 7 个 24h rolling | 6,082 | 1,228,238.67 | 16,923.26 | 1.38% | 0.9695 | 12.50% | 47.41% | 稳定基准，不全账户复制 |
| ce25_btc5m_first20_35 | 7 active windows | 129 | 44,586.92 | 2,152.47 | 4.83% | 0.9076 | 13.16% | 待验证 | 基准 P0 |
| ce25_first65_80_stop_1_5m | 7/7 profitable | 380 | 37,544.00 | 1,891.57 | 5.04% | 待补 | 低风险 | 待验证 | 风控模板 |
| 9f5f BTC last_60s 35-50 | 1 个 24h | 80 | 41,476.79 | 4,290.83 | 10.35% | 0.9190 | 13.28% | 待补 | 新 P0 |
| 9f5f BTC last_60s 50-65 | 1 个 24h | 48 | 26,119.19 | 2,873.43 | 11.00% | 0.9530 | 13.43% | 待补 | 新 P0 |
| b27bc current strict screen | 1 个 24h screen | 14 | 13,833.90 | 2,114.78 | 15.29% | 0.9583 | 2.29% | 待补 | 已降级，低 residual 研究 |

结论：如果只看收益，9f5f 明显强于 ce25；如果看证据稳定性，ce25 仍更强；b27bc 的低残仓值得研究，但不能再按高收益候选推进。

## 历史候选横向复盘

### username123123

地址：

`0xd950a1a89f3e61a7a9efc85a46e440ce58c15e86`

已有输出：

- `/Users/hot/web3Scientist/poly_trans_research/data/exports/deep_username123123_20260527_1530_1550_bjt`
- `/Users/hot/web3Scientist/poly_trans_research/data/exports/deep_username123123_20260527_1225_1245_bjt`

关键结果：

| 窗口 | 市场数 | buy_actual | PnL 口径 | PnL | ROI | pair_cost | resid_rate | 决策 |
| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | --- |
| 2026-05-27 15:30-15:50 BJT | 14 | 19,056.81 | cash_plus_current_plus_rebate | 5,360.54 | 28.13% | 0.9327 | 2.64% | 保留，P1 |
| 2026-05-27 12:25-12:45 BJT | 13 | 13,708.15 | cash_pnl | 1,133.01 | 8.27% | 0.9734 | 1.92% | 保留，P1 |

解释：这是非常漂亮的低残仓 burst，但窗口短，且部分指标使用 `cash_plus_current_plus_rebate`，必须补当前窗口和结算后窗口。

### 04b6

地址：

`0x04b6d7e930cf9e493c5e6ef24b496294f95594c8`

已有输出：

- `/Users/hot/web3Scientist/poly_trans_research/data/exports/deep_04b6_20260527_0030_0845_bjt`
- `/Users/hot/web3Scientist/poly_trans_research/data/exports/leaderboard_04b6_execution_profile_20260526_0730_1930_bjt`

关键结果：

| 来源 | 市场数 | buy_actual | cash_pnl | ROI | pair_cost | resid_rate | 决策 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| deep_04b6_20260527_0030_0845_bjt | 22 | 53,517.25 | 6,208.33 | 11.60% | 0.9676 | 10.34% | 保留观察 |
| execution_profile_20260526_0730_1930_bjt | 多市场 | 172,457.05 | -3,771.43 | -2.19% | 0.9529 | 11.83% | 拒绝全账户复制 |

解释：04b6 有强 pocket，但全窗口并不稳定。它不是目前质量最优对象，优先级低于 9f5f、ce25 优选桶、username123123。

### b55

地址：

`0xb55fa1296e6ec55d0ce53d93b9237389f11764d4`

已有输出：

- `/Users/hot/web3Scientist/poly_trans_research/data/exports/deep_b55_20260527_1100_1245_bjt`
- `/Users/hot/web3Scientist/poly_trans_research/data/exports/deep_b55_20260527_0830_1245_bjt`

历史 burst：

| 窗口 | 市场数 | buy_actual | cash_pnl | ROI | pair_cost | resid_rate | 决策 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 2026-05-27 11:00-12:45 BJT | 53 | 19,472.76 | 13,307.97 | 68.34% | 0.8620 | 15.79% | 历史保留，当前拒绝 |

当前问题：2026-06-04 当前榜单窗口显示 b55 pair edge 已明显退化，`actual_pair_cost` 约 1.024，quick screen PnL 为负。它更像历史行情/机制窗口里的异常强策略，不应继续当主线。

## 可交给实现同事的候选策略

### P0: 9F5F_BTC_LAST60_MIDPRICE_V1

目标：把 9f5f 里最强、同时 residual 可控的 BTC last_60s 中价桶转成 ex-ante shadow policy。

候选规则：

- 市场：BTC up/down 5m 优先，15m 作为二级扩展。
- 时间：距离市场结束 `<= 60s`。
- 首腿价格：
  - branch A: `35 <= price < 50`
  - branch B: `50 <= price < 65`
- 方向：
  - branch A 优先验证 UP。
  - branch B 同时验证 UP 和 DOWN，DOWN 的 pair_cost 更好，但 residual 更高。
- 入场硬约束：
  - 初始 `expected_pair_cost <= 0.95`
  - 放宽观察层 `expected_pair_cost <= 0.97`
  - 不能因为第一腿亏损而无上限补仓。
- 风控：
  - 单市场 residual cap 初始 `<= 12%`
  - 激进层可放到 `<= 15%`
  - 超过 cap 后只允许降风险或退出，不允许扩大库存。
- 评价指标：
  - fee-inclusive ROI
  - pair_pnl
  - pair_cost
  - resid_rate
  - bad_pc_ge_100_share
  - market-level tail loss

为什么不是照抄 9f5f：

9f5f 全账户 residual_rate 为 25.68%，说明它有一部分收益来自库存方向性或残仓处理。我们当前没有私有执行真相，不能把这部分当成可复制 alpha。

### P2/Watch: B27BC_LOW_RESIDUAL_EXECUTION_STUDY_V1

目标：研究 b27bc 如何把 residual 压得很低，而不是直接把它当成高收益 pair 策略复制。

当前证据：

- 当前 24h screen 命中严格筛选。
- ROI 15.29%。
- actual_pair_cost 0.9583。
- resid_rate 2.29%。

当前缺口：

- 深 profile 被公开 API 卡住，没有拿到完整 sequence。
- 缺少首腿时间、首腿价格、首腿方向、pair delay 等桶。

下一步不是直接写策略，而是先做数据工具：

- 支持高频账号的 bounded profiler。
- 支持按更小窗口，例如 15m / 30m / 60m，断点续抓。
- 支持 `max_offset`、分页缓存、失败重试后保留部分结果。
- 从 `market_rows.csv` 和 activity cache 合成 per-market sequence。

只有拿到 sequence 后，才能判断 b27bc 是否存在小范围正期望 BTC 5m 桶；目前不再把它视为 ce25 的终极版本。

### P1: USERNAME123123_SHORT_BURST_LOW_RESID_V1

目标：验证历史短窗口低残仓 burst 是否可重复。

当前证据：

- 20 分钟窗口 ROI 可到 28.13%。
- residual_rate 只有 2.64%。
- 另一个短窗口 residual_rate 1.92%。

风险：

- 样本太短。
- 可能是特定行情窗口。
- `cash_plus_current_plus_rebate` 需要和纯 `cash_pnl`、结算后 PnL 分开复核。

下一步：

- 拉最近 24h 和最近 72h。
- 按 BTC/ETH/5m/15m 拆桶。
- 检查是否存在和 9f5f / b27bc 相同的 last_60s 中价模式。

### P2: CE25_LOW_PRICE_TAIL_DOWN_V1

目标：保留 ce25 作为稳态基准，而不是收益最高候选。

当前状态：

- 多窗口证据最完整。
- `ce25_btc5m_first20_35` 是当前最稳的 ce25 alpha 桶。
- `ce25_first65_80_stop_1_5m` 更像风控模板。

使用方式：

- 用它校准 replay/shadow policy 框架。
- 用它衡量新候选是否真的更强。
- 不要拿 ce25 full account 直接复制。

## 统一验证路线

所有候选进入实现前，需要走同一套验证：

1. Public profile 验证：
   - 至少 3 个非连续窗口。
   - 每个窗口报告 fee-inclusive `cash_pnl`、`buy_actual`、`pair_cost`、`resid_rate`。
   - 不用 frontend UI PnL。

2. Ex-ante shadow policy：
   - 只使用入场前可观察特征。
   - 禁止把 `pair_delay <= 15s` 这种结果变量直接当信号。
   - `pair_cost` 只能作为事后评价，不能直接作为入场条件，除非用当时 orderbook 可估算 expected pair cost。

3. Replay / strict V2 验证：
   - 使用本地 replay/cache/source-of-truth。
   - 排除本地指南中标记不可用的日期。
   - 输出 market-level tail loss，而不是只看总 ROI。

4. Fee stress：
   - 0%
   - 0.8%
   - 2.5%
   - 2.83%
   - 3.0%

5. 资金循环口径：
   - 报告 deployed buy ROI。
   - 另行估算 merge 循环后的初始本金 ROI。
   - 不能把高 turnover 的 deployed ROI 直接等同于本金日化。

## 下一步优先级

1. 先把 `9F5F_BTC_LAST60_MIDPRICE_V1` 转成 shadow policy。
   - 这是最快能产出可验证策略的方向。
   - 它的收益比 ce25 高，样本比 username123123 大。

2. 同时修 b27bc 高体量 profiler。
   - b27bc 值得学习的是低 residual 和库存闭合机制。
   - 但现有 deep_compare 不支持把它列为高收益复制对象。

3. 给 username123123 补最近窗口。
   - 如果低 residual burst 仍存在，它可能比 9f5f 更适合小资金。

4. 暂停 b55 主线。
   - 除非后续再出现当前窗口正 pair edge，否则只保留为历史案例。

5. 04b6 放在第二梯队。
   - 它有可学 pocket，但不是目前最高质量主线。

## 当前排序

| 排名 | 对象 | 学习价值 | 复制难度 | 风险 | 当前动作 |
| ---: | --- | --- | --- | --- | --- |
| 1 | 9f5f BTC last_60s 中价桶 | 高 | 中 | 中高 residual | 立即转 shadow policy |
| 2 | username123123 | 高 | 未知 | 样本短 | 补最近窗口 |
| 3 | ce25 | 中高 | 中 | 较稳 | 作为基准和风控模板 |
| 4 | b27bc | 中，偏执行机制 | 未知 | 收益质量不稳 | 做低 residual 机制研究 |
| 5 | 04b6 | 中 | 中 | 不稳定 | 第二梯队观察 |
| 6 | b55 | 历史高 | 高 | 当前退化 | 暂停主线 |

## 给实现同事的最小任务包

### Task A: 9f5f shadow policy

输入：

- `/Users/hot/web3Scientist/poly_trans_research/data/exports/profile_9f5f_ce25_seed_24h_20260603_1510_to_20260604_1510_bjt/ce25_market_sequence.csv`

实现：

- `9F5F_BTC_LAST60_MIDPRICE_V1`
- branch:
  - BTC 5m, last_60s, first_price 35-50
  - BTC 5m, last_60s, first_price 50-65
- 输出：
  - per-market decision log
  - expected pair cost at decision time
  - realized pair_cost
  - residual_rate
  - fee-inclusive PnL
  - tail loss

验收：

- 不使用 outcome-only 字段入场。
- fee stress 后仍保留正期望。
- residual cap 下收益没有被完全吃掉。

### Task B: b27bc bounded profiler

输入：

- `/Users/hot/web3Scientist/poly_trans_research/data/exports/leaderboard_crypto_week_top80_24h_screen_20260604_ce25_seed_bjt/summary.json`
- `/Users/hot/web3Scientist/poly_trans_research/data/exports/leaderboard_crypto_week_top80_24h_screen_20260604_ce25_seed_bjt/market_rows.csv`

实现：

- 高频账号分页抓取。
- 支持 15m/30m/60m 小窗口。
- 支持 resume cache。
- 抓不到完整 24h 时也输出 partial profile 和缺口。

验收：

- 至少拿到 b27bc 最近 3 个 30m 窗口。
- 每个窗口输出 sequence buckets。
- 判断它是否存在可复现的低 residual entry pattern。
- 严格剥离窗口前旧仓 redeem/cash-in，报告 `with_buy_cash_ex_no_condition_rebate` 和 `with_buy_plus_current_ex_no_condition_rebate`。

### Task C: username123123 refresh

输入：

- `/Users/hot/web3Scientist/poly_trans_research/data/exports/deep_username123123_20260527_1530_1550_bjt`
- `/Users/hot/web3Scientist/poly_trans_research/data/exports/deep_username123123_20260527_1225_1245_bjt`

实现：

- 最近 24h profile。
- 最近 72h profile。
- 按 BTC/ETH、5m/15m、last_60s/1-5m、first_price_bucket 拆桶。

验收：

- 若 residual_rate 仍 < 5%，进入 P0/P1。
- 若只在历史短窗口成立，保留为行情特例。

## 最终判断

现阶段最值得继续投入的不是 ce25 本身，而是“ce25 框架下筛出来的 9f5f/username123123 类型策略”，同时把 b27bc 用作低 residual 执行机制参照。

如果目标是尽快让同事实现可验证版本，先做 9f5f 的 BTC last_60s 中价桶；若要研究库存闭合和残仓控制，再攻 b27bc 的 sequence 提取。ce25 继续作为基准线，b55 暂停，04b6 降级观察。
