# `0x3048d653` 全周期策略调查报告

日期：2026-08-11 BJT  
账户：`0x3048d65321be3497164cdfc2996f94f98a2e7537`  
研究标签：`twitter-CryptoWithGab`  
证据等级：公开 activity / 公开 trades；不包含账户私有 CLOB 真相

## 0. 结论先行

该账户是目前最值得继续研究的 BTC 5m 公开账户之一，但结论必须分层：

```text
策略表型：F1 complete-set accumulation / completion controller，高置信度
辅助表型：F2 有界库存倾斜，中等置信度
最近可完整核验的 240h 费后现金账：正
剔除边界旧仓和 maker rebate 后：仍正
全生命周期精确费后 PnL：当前不能声称已完整回填
P0-Research Candidate：YES
P0-Mechanism Candidate：YES，待 FIFO/L2 验证
P0-Implementation：NO
直接跟随公开成交：NO
```

最重要的判断不是“它只买不卖所以一定是 maker”，而是：在已完整核验的窗口内，它通过大量 BTC 5m 市场的双边积累、赎回和资金循环获得正现金结果；残仓是拖累而不是主要收益来源。它仍然面临较高的 fee burden 和 bad-pair exposure，不能把事后 side-VWAP pair cost 当作可执行 alpha。

## 1. 全周期边界与数据完整性

### 1.1 最早可观察活动

用 public Data API 按 2026-01-01 至 2026-08-12 的 7 天时间段做低成本探测：

- 最早观察到的 TRADE：2026-06-10 04:41:39 UTC，即 2026-06-10 12:41:39 BJT；
- 最早观察到的 REDEEM：2026-06-10 04:45:28 UTC，即 2026-06-10 12:45:28 BJT；
- 2026-06-10 之前的 TRADE 周段未发现该钱包活动。

这可以把账户生命周期起点近似定位在 2026-06-10，但不是链上创建时间，也不证明账户在更早时间没有不可见或未被公开 API 返回的活动。

### 1.2 可核验窗口

| 证据层 | 时间（BJT） | 状态 | 用途 |
|---|---|---|---|
| 240h fee-inclusive activity | 2026-07-26 14:59:29 至 2026-08-05 14:59:29 | 完整生成 | 主现金账和 pair/residual 诊断 |
| 72h fee-inclusive activity | 2026-08-02 14:53:43 至 2026-08-05 14:53:43 | 完整生成 | 近期稳定性子窗 |
| 2026-06-10 至 2026-08-11 activity 全历史 | 约 62 天 | 未完整生成 | API 429/连接中断，不能作为精确全周期净 PnL |
| account-level `/trades` 深翻 | 2026-06-10 至 2026-08-11 | offset 15,000 返回 400 | 需按 market-scoped `/trades` 补齐 gross 行为 |

因此本报告的“全周期”是**生命周期调查报告**，不是虚构的“62 天精确现金 PnL 报告”。所有金额结论均标明实际覆盖窗口。

## 2. 严格费后现金账

### 2.1 240h 主窗口

BUY 成本使用 `activity.usdcSize`；它已经包含 public activity 可见的 fee-like 协议扣款。现金流包括 SELL、MERGE、REDEEM 和 maker rebate。该窗口没有 SELL 和 MERGE，主要路径是 BUY → REDEEM。

| 指标 | 数值 |
|---|---:|
| TRADE rows | 57,309 |
| REDEEM rows | 1,860 |
| MAKER_REBATE rows | 8 |
| BTC 5m trade markets | 1,888 |
| buy gross cost | $859,585.37 |
| buy actual cost | $880,315.27 |
| fee-like cost | $20,729.90 |
| fee rate / gross BUY | 2.4116% |
| REDEEM proceeds | $888,932.30 |
| MAKER_REBATE | $1,124.37 |
| headline cash PnL | +$9,741.40 |

窗口内有一个没有窗口内 BUY 的旧市场 REDEEM，金额 $445.32。把它从本窗口 cohort 中剔除：

```text
strict cash PnL = 9,741.40 - 445.32 = +9,296.08 USDC
strict ROI = +9,296.08 / 880,315.27 = +1.056%
strict cash PnL ex maker rebate = +8,171.71 USDC
```

这说明该窗口的正结果不是由 maker rebate 单独制造的。返佣约占 headline PnL 的 11.54%，剔除后仍为正。

### 2.2 72h 子窗口

| 指标 | 数值 |
|---|---:|
| buy actual | $417,818.54 |
| fee-like cost | $9,705.84 |
| fee rate / gross BUY | 2.3782% |
| REDEEM proceeds | $423,425.10 |
| MAKER_REBATE | $558.17 |
| cash PnL | +$6,164.73 |
| cash ROI | +1.476% |
| actual side-VWAP pair cost | 0.986141 |
| diagnostic paired actual PnL | +$5,295.07 |

72h 仍为正是有价值的稳定性信号，但它与 240h 主窗口有重叠，不能当作独立 OOS 窗口。

### 2.3 日序质量

240h 窗口按 BJT 有 9 个活跃日期桶。原始现金账有 6 个正桶，但 2026-07-28 的 $81.19 完全来自返佣、没有 BUY；按交易现金而非返佣计，正收益活跃日为 5/9。最大日级回撤约 $836，说明窗口内并非逐日无损。

这比“6/9 天赚钱”更严格，也避免把返佣-only 日期误算成交易能力。

## 3. 配对、残仓与收益归因

### 3.1 账户级 pair/residual 诊断

| 指标 | 数值 |
|---|---:|
| paired quantity | 802,922.48 pairs |
| paired share of bought shares | 88.97% |
| residual share rate | 11.03% |
| residual notional estimate | $88,553.64 |
| RER estimate | 10.06% |
| gross side-VWAP pair cost | 0.962997 |
| actual side-VWAP pair cost | 0.986100 |
| pair fee cost | 0.023103 |
| diagnostic paired gross PnL | +$29,710.86 |
| diagnostic paired actual PnL | +$11,160.85 |

用严格边界修正后的现金账归因：

```text
strict cash PnL - diagnostic paired actual PnL
= 9,296.08 - 11,160.85
= -1,864.76 USDC residual/other drag estimate
```

剔除返佣后，残仓及其他非配对部分的估算拖累约为 $2,989.14。这个结果支持“completion 主导、残仓拖累”的表型，不支持“靠残仓方向赌赢赚钱”。

### 3.2 配对质量并不完美

按 per-market actual side-VWAP pair cost：

| 分位数 | pair cost |
|---|---:|
| p10 | 0.89069 |
| p25 | 0.94473 |
| p50 | 0.98809 |
| p75 | 1.02853 |
| p90 | 1.07068 |

`pair_cost >= 1.00` 的市场为 515 / 1,888；这些市场占 BUY notional 约 43.38%，对应诊断损失约 $12,913.16。也就是说，它不是逐市场硬性锁定 `<1`，更像账户级预算：好市场的折价收益覆盖部分坏市场的补腿或库存损失。

### 3.3 重要口径限制

上述 pair cost 是两侧实际成交均价之和，属于 side-VWAP 事后诊断。它没有回答：

- 第一腿和第二腿是否能按时间顺序锁定；
- 第二腿成交时盘口是否仍存在；
- 我们能否以同样价格成交；
- 最后 60 秒补腿是否会穿透 ask1/top5；
- residual settlement 是否属于可重复 edge。

因此 `0.9861` 可以作为行为指纹，不能作为 Design/Implementation KPI。

## 4. 市场、时序和成交结构

### 4.1 市场集中度

240h 主窗口 1,888 个交易市场全部是 `btc-updown-5m`。按 240 小时理论 2,880 个 5m 轮次估算，公开参与率约 65.56%。这是历史窗口覆盖率，不是稳定在线率，也不能直接转成我们的预期成交率。

账户没有在该窗口通过 ETH/SOL 或长周期市场分散风险；它的表现主要是 BTC 5m 专用控制器的表现。

### 4.2 窗口内时序

| 指标 | p10 | p50 | p90 |
|---|---:|---:|---:|
| 首笔距开盘 | 14s | 21s | 69s |
| 末笔距开盘 | 102s | 236s | 296s |

其他特征：

- 87.39% 的市场在开盘后 60 秒内出现首笔；
- 45.97% 的市场最后一笔发生在结束前最后 60 秒；
- 2.44% 的市场存在结束后成交记录；
- 盘前首笔比例为 0%；
- 每市场成交笔数 p10/p50/p90 为 6/25/63，最大 126。

这更像：

```text
开盘后 admission
→ 双腿分批积累
→ inventory imbalance 监控
→ final60 repair 或停止补腿
→ REDEEM / 资金循环
```

而不是纯盘前抢单、纯盘后抢单或单笔方向下注。

## 5. 策略流派判定

### F1：合成/完成式做市，置信度高

支持证据：只 BUY、双边 Up/Down 大量并存、88.97% 买入份额可事后形成互补数量、主要通过 REDEEM 收口、残仓不是主要收益来源、返佣不是必要收益来源。

它的经济模型不是经典 A-S 的完整买卖 round-trip，而是：

```text
first-leg accumulation
→ complementary inventory accumulation
→ bounded completion / settlement
→ redeem and capital recycle
```

### F2：有界库存倾斜，置信度中等

11.03% residual share rate 和 10.06% RER 说明它并不强制每轮零残仓。残仓更像执行误差、库存容忍或有界倾斜；当前样本不支持残仓方向是主要 alpha。

### 不是已证实的经典 A-S

公开 activity 看不到 quote、cancel、未成交订单、queue age、reservation price 或 inventory-skew response。因此最多只能称为：

```text
AS-compatible inventory-control phenotype
```

不能称为已确认的 Avellaneda-Stoikov/GLFT 实现。

### Maker/taker 边界

存在 MAKER_REBATE 只能说明账户有符合返佣规则的公开活动，不能证明所有成交 maker-only。公开 activity 也不能证明 authenticated `trader_side`、post-only、队列位置和撤单优先级。正确标签仍是：

```text
mixed or regime-dependent execution: plausible
maker-only: unproven
taker-only: unproven
```

## 6. 全周期演化：目前能说什么、不能说什么

### 已知

1. 账户公开活动大约从 2026-06-10 开始；
2. 2026-07-26 至 2026-08-05 有完整 240h 费后正账本；
3. 2026-08-02 至 2026-08-05 的重叠 72h 仍为正；
4. 7 月底样本显示高频 BTC 5m completion 表型清晰。

### 尚不能声称

不能根据当前资料声称：

- 6/10 至 8/11 全周期净赚多少；
- 7 月初是否已经盈利；
- 何时发生参数升级或策略 regime 切换；
- 费率、maker/taker 比例在整个生命周期是否稳定；
- 240h 结果是否代表整个账户历史。

全历史 activity 回填曾在高密度 offset 触发 429/连接关闭；account-level `/trades` 在 offset 15,000 返回 400，且 `/trades` 不含 `activity.usdcSize`。因此不能用未完成回填或 gross trades 代替全周期费后现金账。

## 7. 研究级风险评估

| 风险 | 评级 | 依据 |
|---|---|---|
| 费用风险 | 高 | fee-like 约占 gross pair profit 69.8% |
| 补腿/坏 pair 风险 | 高 | 43.38% BUY notional 位于 pair cost >=1 市场 |
| 残仓风险 | 中 | RER 约 10.06%，不是零风险但显著低于方向型账户 |
| 方向结算依赖 | 中低 | 严格窗口残仓为拖累，不是主要利润源 |
| 执行复制风险 | 高 | 无 L2/top5、FIFO、queue truth |
| 返佣依赖 | 低至中 | ex-rebate 仍正，但返佣可改变边际 |
| 历史稳定性 | 未决 | 仅有一个完整 240h 主窗口和一个重叠 72h 子窗 |

## 8. 对同事的研发建议

### P0-A：先补齐历史证据，不先写策略

1. 用 deterministic BTC 5m market-scoped `/trades` 枚举 6/10 至今，建立 gross 行为档案；
2. 用 activity 分段抓取 REDEEM、MAKER_REBATE 和可承受范围内的 TRADE `usdcSize`；
3. 每个时间段输出 coverage、429、offset、old-no-buy 和边界市场清单；
4. 形成至少三个不重叠 10d fee-inclusive 窗口。

### P0-B：重做 FIFO reconciliation

对 240h 主窗口并排报告：

- side-VWAP pair cost；
- FIFO 时间匹配 pair cost；
- FIFO residual notional/RER；
- residual realized PnL 单列为 settlement outcome；
- 两种口径差异及其来源。

### P0-C：做 executable bridge

只有当历史 L2 与账户成交时间/market 有足够 overlap 后，才运行：

- first-leg maker upper bound；
- taker ask1/top5 stress；
- second-leg repair sweep；
- final60 单列；
- fee/rebate 压力测试；
- `powerwinner` 或其他负控。

### P0-D：提取可研究控制器，而不是跟单

候选模块：

```text
early-window admission
inventory-aware side skew
marginal pair economics
time-dependent repair
RER/inventory pause
bad-pair budget and kill
redeem/capital-recycle ledger
```

禁止把 winner、未来成交、最终 RER、REDEEM 结果或事后 pair cost 当作入场特征。

## 9. 证据路径与复现命令

主窗口：

- [240h summary](/Users/hot/web3Scientist/poly_trans_research/outputs/tmp_addr_3048d6_240h/summary.json)
- [240h daily cashflow](/Users/hot/web3Scientist/poly_trans_research/outputs/tmp_addr_3048d6_240h/daily_cashflow.csv)
- [240h market trade metrics](/Users/hot/web3Scientist/poly_trans_research/outputs/tmp_addr_3048d6_240h/market_trade_metrics.csv)
- [240h market cashflow metrics](/Users/hot/web3Scientist/poly_trans_research/outputs/tmp_addr_3048d6_240h/market_cashflow_metrics.csv)
- [72h summary](/Users/hot/web3Scientist/poly_trans_research/outputs/tmp_addr_3048d6_72h/summary.json)
- [前版策略交接报告](/Users/hot/web3Scientist/poly_trans_research/docs/research/TWITTER_CRYPTOWITHGAB_0X3048D653_STRATEGY_RESEARCH_HANDOFF_20260808_ZH.md)

canonical fee-inclusive run：

```bash
python3 scripts/analyze_xuan_public_activity_pnl.py \
  --user 0x3048d65321be3497164cdfc2996f94f98a2e7537 \
  --start-iso 2026-07-26T06:59:29Z \
  --end-iso 2026-08-05T06:59:29Z \
  --window-hours 240 \
  --output-dir outputs/tmp_addr_3048d6_240h_recheck
```

鲁棒回填包装器和 gross `/trades` 采集器已经加入：

- `scripts/analyze_public_account_robust.py`：只改 HTTP 退避，复用 canonical accounting；
- `scripts/collect_account_trades_full_cycle.py`：全周期 gross trades 尝试，明确不含 fee。

## 10. 最终裁决

```text
0x3048d653 = 当前最值得进入“机制拆解”阶段的 BTC5m completion controller 候选。

它已经证明：在一个严格可核验的 240h 窗口内，费后现金结果为正，且不依赖返佣或残仓获胜。
它尚未证明：全周期稳定、FIFO 可锁定、L2 可执行、maker-only 或可被我们复制。
```

推荐标签：`P0-Research / F1-completion / F2-bounded-inventory / implementation-NO`。
