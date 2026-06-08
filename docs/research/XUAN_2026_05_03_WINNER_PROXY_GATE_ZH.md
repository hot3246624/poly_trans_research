# xuanxuan008 Winner-Path 与可实盘 Proxy Gate 研究

## 摘要

本轮使用 `2026-04-27T07:25:00Z` 到 `2026-05-01` 的可信 replay，只读 SQLite，不读取 raw，不使用 own execution truth。方向字段直接使用 replay 归一化后的 `xuan_trades.outcome_side`、`md_trades.market_side/taker_side`、`settlement_records.winner_side`，不重新做 Up/Down 映射。

关键结论：

- `xuan` 的 trade-level winner count/size 约等于 50%，不是简单“买更多赢家”。
- `xuan` 的 first leg 明显偏向最终赢家侧：`first_winner_rate = 65.69%`。
- 慢利润路径更强：`slow_profit_lt95` 的 first-winner rate 达 `80.20%`，贡献 `$11618.14` surplus。
- 但 `winner_side` 是事后真值，不能实盘使用；可复制部分必须转成开仓时可见的 proxy。
- 目前最强的可复制 proxy 不是“高价就是赢家”，而是：`intended_first_price` 相比同刻 L2 sweep VWAP 有明显折价，以及 first 30s 内是否出现 cheap completion window。

## 数据与产物

- replay root: `/Users/hot/web3Scientist/poly_trans_research/data/replay`
- 日期: `2026-04-27` 至 `2026-05-01`
- xuan BUY trades: `12156`
- xuan paired tranches: `4587`
- winner-path 输出: `/Users/hot/web3Scientist/poly_trans_research/data/exports/xuan_research_runs/replay_20260503_full/xuan_winner_path_5d`
- proxy-gate 输出: `/Users/hot/web3Scientist/poly_trans_research/data/exports/xuan_research_runs/replay_20260503_full/xuan_winner_proxy_gate_5d`
- shadow config: `/Users/hot/web3Scientist/poly_trans_research/configs/xuan/winner_proxy_gate_shadow_v1.json`

## Winner-Path 事实

| 层级 | 指标 | 数值 |
|---|---:|---:|
| trade | winner trade rate | `49.96%` |
| trade | winner size rate | `50.08%` |
| trade | winner USDC rate | `60.05%` |
| tranche | first-leg winner rate | `65.69%` |
| market | residual-is-winner rate | `71.64%` |

解释：

- 从 count/size 看，`xuan` 不是机械地多买赢家。
- 从 USDC 看，他花在赢家侧的钱更多，因为赢家侧价格更高。
- 从 tranche 看，真正重要的是“第一腿更常落在最终赢家侧”，这会让后续补腿和残差管理更有利。

## 按路径拆解

| path | n | first_winner | pair p50 | delay p50 | surplus |
|---|---:|---:|---:|---:|---:|
| `fast_control` | `3765` | `64.04%` | `1.003527` | `8s` | `$2012.44` |
| `slow_bad_ge95` | `327` | `62.69%` | `1.023455` | `44s` | `-$2998.41` |
| `slow_profit_lt95` | `495` | `80.20%` | `0.841998` | `50s` | `$11618.14` |

结论：

- 30s 内快速配对主要是风险控制，不是主要利润来源。
- 真正利润来自少数 slow-profit tranche。
- slow-profit tranche 同时具备两个特征：更强 first-winner 偏置，以及后续出现更低 opposite completion price。

## 开仓时可见 Proxy

proxy-gate baseline：

| 指标 | 数值 |
|---|---:|
| rows | `4587` |
| first_winner_rate | `65.69%` |
| fast_control_rate | `82.08%` |
| slow_profit_rate | `10.79%` |
| slow_bad_rate | `7.13%` |
| surplus_per_size | `0.020216` |

### 正向开仓 proxy

| proxy | n | first_winner | slow_profit | surplus/size | 解释 |
|---|---:|---:|---:|---:|---|
| `first_l2_vwap - intended_first_price > 3c` | `534` | `65.17%` | `14.04%` | `0.045587` | 最强开仓可见 edge，说明成交价明显优于同刻 L2 sweep |
| `0.50 <= first_price < 0.55 AND size > 160` | `206` | `54.37%` | `14.08%` | `0.039042` | 不是 winner proxy，更像中位大单价差/执行优势 |
| `round_offset < 30s AND L2 edge > 3c` | `168` | `64.29%` | `15.48%` | `0.055937` | 早段若能拿到明显折价，质量很好 |
| `0.55 <= first_price < 0.70 AND L2 edge > 3c` | `233` | `68.24%` | `13.30%` | `0.042956` | 同时具备较好 winner proxy 和执行折价 |

注意：`first_price >= 0.70` 的 first-winner rate 很高，达到 `83.81%`，但 surplus/size 只有 `0.011844` 左右，不能简单作为 upclip alpha。高价侧更像“更可能是赢家、残差更安全”，不是“更赚钱”。

### 负向开仓 proxy

| proxy | n | first_winner | surplus/tranche | 解释 |
|---|---:|---:|---:|---|
| `first_l2_vwap - intended_first_price <= -1c` | `219` | `53.88%` | `-$1.61` | 当前成交/报价劣于 L2，可直接 block 或 clip-down |
| `round_offset < 30s AND L2 edge <= -1c` | `81` | `43.21%` | `-$3.54` | 早段负 edge 特别差，应该 hard block |
| `0.55 <= first_price < 0.70 AND L2 edge <= -1c` | `93` | `58.06%` | `-$3.32` | 中高价但执行劣势，不能被 winner proxy 掩盖 |
| `first_price < 0.40` | `174` | `25.86%` | `$1.67` | winner proxy 很差；除非有强 L2 折价，否则应 clip-down |

## `L2 edge > 3c` 可信度审计

`first_l2_vwap - intended_first_price > 3c` 是目前最强的 open-time proxy，但它必须被单独审计，因为这类样本的 public exact match 率低于普通桶，存在 timestamp、深度缺失或 public trade 匹配不完整的风险。

按 `first_tx` 连接 public trade match 后：

| bucket | n | price+size exact | no_match | match_time_diff p50 | surplus/size | pair p50 |
|---|---:|---:|---:|---:|---:|---:|
| `L2 edge > 3c` all | `534` | `314` | `148` | `-3297.5ms` | `0.045587` | `0.970000` |
| `L2 edge > 3c` exact only | `314` | `314` | `0` | 约 `-3.3s` | `0.036209` | `0.984679` |
| `L2 edge <= -1c` all | `219` | `102` | `69` | `-3050.5ms` | `-0.015662` | `1.026723` |
| `L2 edge <= -1c` exact only | `102` | `102` | `0` | 约 `-3.0s` | `0.004862` | `1.024619` |

裁决：

- `L2 edge > 3c` 不是纯伪影；即使只看 price+size exact 子集，surplus/size 仍明显高于 baseline `0.020216`。
- 但 all 样本里 `no_match` 比例较高，所以它暂时只能作为 shadow positive evidence，不能直接 enforce。
- `L2 edge <= -1c` 的负向结论更稳：即使 exact 子集不再明显亏损，pair p50 仍高于 `1.02`，说明这是不适合主动开仓的状态。

## 候选 Gate 反事实

这些不是 live PnL 回测，只是在 xuan 自己的 `4587` 个 tranche 上做“如果只选择这些状态，会留下什么样的样本”的研究裁决。

| policy | selected | selected_rate | first_winner | slow_profit | slow_bad | surplus/size | surplus/tranche |
|---|---:|---:|---:|---:|---:|---:|---:|
| all xuan tranches | `4587` | `100.00%` | `65.69%` | `10.79%` | `7.13%` | `0.020216` | `$2.32` |
| block `L2 edge <= -1c` | `4368` | `95.23%` | `66.28%` | `11.08%` | `7.17%` | `0.021823` | `$2.51` |
| only `L2 edge > 3c` | `534` | `11.64%` | `65.17%` | `14.04%` | `6.74%` | `0.045587` | `$6.29` |
| `L2 edge > 3c` OR mid-large | `697` | `15.20%` | `62.12%` | `13.77%` | `6.60%` | `0.041130` | `$6.49` |
| block low price without edge | `4428` | `96.53%` | `67.10%` | `10.91%` | `7.25%` | `0.020309` | `$2.34` |
| after 30s `min_pair<=0.90` | `1314` | `28.65%` | `72.60%` | `19.41%` | `2.44%` | `0.095495` | `$10.08` |
| after 30s `min_pair<=0.95` | `2231` | `48.64%` | `70.01%` | `16.54%` | `4.30%` | `0.069317` | `$7.37` |
| after 30s no cheap window | `1319` | `28.76%` | `56.71%` | `4.25%` | `8.64%` | `-0.030027` | `-$3.93` |

可执行含义：

- 开仓时强筛 `L2 edge > 3c` 会大幅降低机会频率，只能作为 upclip/优先级信号，不能单独承载主策略。
- 单纯 block `L2 edge <= -1c` 成本很低，只移除 `4.8%` 样本，却提升 surplus/size；这是最像 P0 gate 的候选。
- 30s 后 `min_pair<=0.90/0.95` 的裁决力远强于开仓 proxy，适合做 slow continuation gate。
- `no cheap window` 的样本整体负期望，应进入 near-parity repair，而不是继续慢等。

## 30s 后续 Slow-Path Gate

最强后续信号仍是前 30 秒内是否出现过 cheap completion window。

| first 30s min pair cost | n | first_winner | slow_profit | slow_bad | surplus/size | 裁决 |
|---|---:|---:|---:|---:|---:|---|
| `<=0.90` | `1314` | `72.60%` | `19.41%` | `2.44%` | `0.095495` | 强 allow slow-path |
| `0.90-0.95` | `917` | `66.30%` | `12.43%` | `6.98%` | `0.032382` | 中等 allow，需看库存/预算 |
| `0.95-0.99` | `1037` | `67.79%` | `6.75%` | `11.28%` | 负向 | 倾向 repair |
| `0.99-1.01` | `666` | `57.81%` | `4.05%` | `9.46%` | 负向 | force repair |
| `>1.01` | `417` | `51.32%` | `2.16%` | `9.11%` | 负向 | force repair / no slow wait |

这解释了一个之前难点：他并不是“神奇保证 30s 配对”，而是在 30s 前后分流：

- 大多数 tranche 用 near-parity completion 快速灭风险。
- 少数 tranche 如果已经出现 cheap-window evidence，则允许继续慢等。
- 没有 cheap-window evidence 的未覆盖 tranche，继续等的期望很差，应转 repair。

## 对策略实现的含义

第一，`PGT`/`completion-first` 不能只做 neutral pair target。xuan 的 edge 更像：

```text
open-time execution edge + winner-proxy state selection
-> fast near-parity risk repair
-> cheap-window evidence controls slow-profit continuation
-> residual/merge/redeem handles tail
```

第二，开仓 gate 应先做 shadow，不要直接 enforce。建议 shadow explain 至少记录：

- `intended_first_price`
- `first_l2_vwap`
- `first_l2_edge = first_l2_vwap - intended_first_price`
- `round_offset_s`
- `clip_size`
- `first_l1_spread_ticks`
- `recent_same_minus_opp_buy_size_15s`
- `winner_proxy_bucket`
- `open_gate_decision`
- `open_gate_reason`

第三，slow continuation gate 可以比 open gate 更硬，因为 `min_pair_cost_seen_in_first_30s` 是开仓后已经观察到的事实：

- `<=0.90`: 允许 slow-profit path。
- `0.90-0.95`: 允许但 clip/预算保守。
- `>0.99`: 不再慢等，转 near-parity repair。
- `>1.01`: hard force repair。

## 当前不能下的结论

- 不能说已经 100% 还原 xuan。我们仍拿不到他的挂单队列位置、真实下单时间和取消时间。
- 不能把 `winner_side` 写进 live 策略；它只能用于评估 proxy。
- 不能把 `first_price >= 0.70` 当作自动盈利信号；它是强 winner proxy，但利润不强。
- 不能只靠 `pair_cost <= 0.95` 开仓；那是 slow continuation 证据，不是所有 tranche 的开仓条件。

## 下一步研究

1. 对 `first_l2_edge > 3c` 做逐笔复盘，确认它来自真实可成交折价、timestamp 误差、L2 深度缺失，还是 xuan 的 maker/排队优势。
2. 把 open-time proxy gate 应用到我方 market-side 回测候选，比较 open_allowed vs blocked 的 30s completion 与 surplus。
3. 做 exact public trade match 的 pre/post book delta，确认 xuan 的 taker-like public prints 是否可能是 maker resting order 被打。
4. 将 `/Users/hot/web3Scientist/poly_trans_research/configs/xuan/winner_proxy_gate_shadow_v1.json` 接入 shadow report，只作为 explain/gating default，不进入 live enforce。
