# Xuan 30s 配对信号指纹

数据源：`data/exports/xuan_research_runs/replay_20260503_full/xuan_winner_proxy_gate_5d/xuan_winner_proxy_gate_rows.csv`

样本：`4587` 个 xuan BTC 5m tranche。

## 结论

这轮研究把“为什么 xuan 经常 30s 左右配对成功”拆成了一个更可执行的结论：

- `30s 配对成功`不是 edge 本身。坏样本同样可以很快配对，甚至更快。
- 真正可观测的信号更像两段式：
  - 开仓时：`first_l2_edge = first_l2_vwap - first_price`，即首腿成交价相对 L2 可成交均价是否足够便宜。
  - 首腿后 30s：是否出现 `min_pair_cost_30s <= 0.90/0.95` 的便宜 opposite completion path。
- 这意味着 xuan 不是靠固定 pair target，也不是靠“进场后神奇配对”。更可能是：先用 L2 execution edge 过滤首腿，再把首腿后 30s 当作证据窗口；出现便宜 completion path 就继续/扩大，没出现就修复/退出/不再开。

## 核心证据

| 规则 | 样本 | fast30 | first winner | surplus/tranche | surplus/size | pair p50 | delay p50 |
|---|---:|---:|---:|---:|---:|---:|---:|
| ALL | 4587 | 82.08% | 65.69% | 2.32 | 2.02% | 0.9946 | 10.0s |
| open L2 edge > +3c | 534 | 79.21% | 65.17% | 6.29 | 4.56% | 0.9700 | 10.0s |
| open L2 edge <= -1c | 219 | 88.58% | 53.88% | -1.61 | -1.57% | 1.0267 | 4.0s |
| min pair cost 30s <= 0.90 | 1314 | 78.16% | 72.60% | 10.08 | 9.55% | 0.9154 | 14.0s |
| min pair cost 30s <= 0.95 | 2231 | 79.16% | 70.01% | 7.37 | 6.93% | 0.9500 | 14.0s |
| min pair cost 30s > 0.99 或缺失 | 1319 | 87.11% | 56.71% | -3.93 | -3.00% | 1.0281 | 6.0s |
| L2 edge > +3c 且 min30 <= 0.90 | 149 | 73.15% | 75.84% | 16.57 | 12.63% | 0.8811 | 14.0s |
| L2 edge <= -1c 或 min30 > 0.99 | 1474 | 87.25% | 56.51% | -3.55 | -2.78% | 1.0277 | 6.0s |

关键反直觉点：`min_pair_cost_30s > 0.99` 的 cohort 配对更快，median delay 只有 `6s`，但 `surplus/size=-3.00%`。所以“快配对”不是盈利来源；“是否在 30s 证据窗口内出现便宜 pair path”才是收益分叉。

## Edge X 30s Evidence

| 开仓 L2 edge | 30s min pair cost | 样本 | first winner | surplus/size | pair p50 |
|---|---|---:|---:|---:|---:|
| > +3c | <= 0.90 | 149 | 75.84% | 12.63% | 0.8811 |
| +1c..+3c | <= 0.90 | 394 | 75.13% | 10.03% | 0.9000 |
| 0..+1c | <= 0.90 | 350 | 75.43% | 9.37% | 0.9190 |
| -1c..0 | <= 0.90 | 334 | 69.46% | 9.09% | 0.9300 |
| > +3c | 0.90..0.95 | 95 | 66.32% | 5.87% | 0.9410 |
| +1c..+3c | > 0.99 | 346 | 53.76% | -4.80% | 1.0375 |
| 0..+1c | > 0.99 | 291 | 56.36% | -5.07% | 1.0380 |
| -1c..0 | > 0.99 | 199 | 57.79% | -5.37% | 1.0354 |

解释：

- `min30 <= 0.90` 是最强分叉。即使开仓 L2 edge 不是最强，只要 30s 内出现便宜 completion path，cohort 也明显赚钱。
- `min30 > 0.99` 是危险分叉。即使开仓 L2 edge 是正的，只要 30s 内没有便宜 path，也会快速转亏。
- 这说明第一腿开仓不是最终裁决，只是进入观察状态。真正决定是否继续承担库存的是首腿后的 30s evidence。

## 对策略的直接含义

第一版“超越 xuan”的研究策略不应该再盲目追求参与率，而应该测试这套状态机：

1. `CandidateOpen`：只有当首腿有非负 L2 edge 才允许进入；`edge > +3c` 才允许放大 clip。
2. `EvidenceWindow`：首腿成交后 30s 内持续计算当前最小可完成 pair cost。
3. `Continue`：若 `min_pair_cost_seen <= 0.90/0.95`，允许 completion-first 继续挂/吃 opposite。
4. `RepairAbort`：若 30s 后仍 `current_pair_cost > 0.99` 且没有 cheap path，停止同侧风险，进入修复或退出。
5. `Cooldown`：坏证据窗口之后不立即重开，避免同一 market regime 下连续接坏库存。

## 新增回测验证

我把 `first_l2_edge` 直接补进了 `scripts/backtest_btc5m_maker_fill_triggered.py`，用于验证“深 maker bid 被冲击流扫中”这个解释。

第一轮结果：

| 策略片段 | attempts | fills | completed | residual | first winner | PnL | pair p50 | 结论 |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| top bid，`edge > +3c` | 0 | 0 | 0 | 0 | n/a | 0 | n/a | 不可能触发，说明 `+3c edge` 不是顶价追单 |
| `bid - 3c`，`edge > +3c`，top bid <=100 | 21 | 5 | 4 | 1 | 80.0% | +46.8 | 0.94 | 极低频但很干净 |
| `bid - 1/2/3c`，`edge > +1c`，top bid <=250 | 554 | 111 | 78 | 33 | 44.1% | -183.0 | 0.95 | 放宽后质量坍塌 |
| `bid - 3/4/5c`，`edge > +3c`，top bid <=250 | 507 | 117 | 81 | 36 | 45.3% | +83.4 | 0.95 | 深折扣有效，但 side selection 仍差 |

这个验证很重要：

- `first_l2_edge > +3c` 的真实含义不是“挂在 top bid 附近”。如果我们在 top bid 买，数学上几乎不可能得到 `L2 vwap - first_price > +3c`。
- 更接近 xuan 的行为是：挂在更深价格，等冲击 SELL flow 扫下来，用成交折扣换安全边际。
- 但是仅学“深 bid 折扣”还不够。我们的 momentum-side proxy first-winner 只有约 `45%`，而 xuan 是 `65.69%`。
- 因此 xuan 的 edge 至少有两层：`execution discount` 和 `winner-side selection`。当前最主要缺口已经从“30s 怎么配对”转为“他为什么首腿更常买到 winner side”。

## Winner-Side Selection 线索

用 xuan 的开仓前特征做两两 bucket 后，出现两类不同 alpha：

| 类型 | 代表 bucket | 样本 | first winner | surplus/size | 解释 |
|---|---|---:|---:|---:|---|
| 方向性 winner-bias | `first_price >= 0.70` | 约 1400+ | 约 83%-86% | 约 0.7%-1.6% | 高侧更常是 winner，但 pair 利润较薄 |
| 折扣型 pair alpha | `first_l2_edge > +3c` | 534 | 65.2% | 4.56% | 成交折扣厚，pair cost 明显更低 |
| 最强折扣子集 | `first_l2_edge > +3c AND min30 <= 0.90` | 149 | 75.8% | 12.63% | 折扣和 30s cheap path 同时成立 |
| 我方错误 proxy | momentum-side deeper bid | 117 fills | 约 45% | 不稳定 | 方向选择错误，无法复刻 xuan winner-bias |

这改变了下一步优先级：

1. 不再用 `best_prev_bid_momentum` 当主 side selector。
2. 回测主线切到 `high_side` / high-side-with-discount。
3. `first_l2_edge` 只解决成交折扣，不负责方向判断。
4. `first_price >= 0.70` 一类高侧信号解决 winner-bias，但利润薄，必须配合 cheap completion/repair 控制。
5. 下一轮策略应是双层 gate：`high_side winner bias` 决定方向，`deeper maker L2 edge` 决定价格和 clip。

## 关键修正：xuan 首腿不是 maker-first 主导

继续核对 `xuan_public_trade_match_5000ms.csv` 后，发现一个更强的约束：

| 首腿价格桶 | tranche | exact price+size match | taker-like BUY | maker-like bid | first winner | surplus/size |
|---|---:|---:|---:|---:|---:|---:|
| `<0.50` | 585 | 468 | 468 | 0 | 40.9% | 1.83% |
| `0.50-0.55` | 812 | 655 | 655 | 0 | 54.6% | 3.21% |
| `0.55-0.70` | 1788 | 1567 | 1567 | 0 | 64.7% | 2.13% |
| `>=0.70` | 1402 | 1343 | 1343 | 0 | 83.8% | 1.18% |
| ALL | 4587 | 4033 | 4033 | 0 | 65.7% | 2.02% |

解释：

- 在 exact price+size match 子集里，xuan 首腿全部对应 public `taker_side=BUY`，不是 `taker_side=SELL`。
- 这不代表每一笔都是 taker，但足够推翻“主要 maker bid 被扫中”的实现主叙事。
- 我们用 maker SELL-flow 回测 high-side 会系统性模拟错误路径：它买到的是“高侧被卖下来”的逆向流，所以 first-winner 只有约 `60%`，而 xuan 高价首腿是 aggressive BUY path，first-winner 可到 `83.8%`。

## Taker Probe 结果

用 `scripts/backtest_btc5m_bounded_taker_l2_schedule.py` 测 2026-05-01 high-side taker-like 入场：

| mode | schedule | candidates | closed/fill | first winner | residual | residual winner | pair p50 | net PnL | ROI |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `0.50-0.55 clip60` | `30s@0.95 -> 90s@1.03` | 257 | 78.2% | 49.0% | 56 | 44.6% | 0.95 | +24.12 | +0.18% |
| `0.50-0.55 clip60` | `30s@0.95 -> 120s@1.04` | 257 | 83.3% | 49.0% | 43 | 39.5% | 0.95 | -79.38 | -0.57% |
| `0.55-0.70 clip60` | `30s@0.95 -> 90s@1.03` | 276 | 81.5% | 54.3% | 51 | 29.4% | 0.95 | -610.64 | -4.07% |
| `0.70-0.90 clip60` | `30s@0.95 -> 90s@1.03` | 268 | 81.0% | 68.3% | 51 | 45.1% | 0.95 | -551.65 | -3.69% |
| `0.70-0.90 clip120` | `30s@0.95 -> 90s@1.03` | 268 | 80.6% | 68.3% | 52 | 44.2% | 0.95 | -1245.78 | -4.16% |

结论：

- `taker-like high-side` 能恢复部分 winner-bias，但随机采样仍然不够赚钱。
- `0.50-0.55` 中价区间更接近 xuan 的 pair alpha；高价区间方向性强，但 residual/repair 成本吞掉收益。
- xuan 的信号不是单纯 “high-side taker buy”，而是 `taker-like execution + 特定时点/盘口状态 + 30s cheap completion evidence`。
- 下一轮应该停止用“每 N 秒随机采样”代表 xuan 入场，改成以 public `taker_side=BUY` 事件作为候选触发器：只有当市场本身出现 aggressive BUY flow 时，才评估是否跟随/补腿。

## Xuan 在 Public BUY 事件里的选择偏好

进一步把 xuan first-leg exact match 与全市场 `md_trades.taker_side=BUY` 事件做对照：

```text
all_buy_events = 3,250,791
xuan_selected_exact_first = 4,032
base_select_rate = 0.124%
```

按价格：

| price bucket | all BUY | xuan selected | select rate | lift | all winner | selected winner |
|---|---:|---:|---:|---:|---:|---:|
| `0.55-0.70` | 649,227 | 1,608 | 0.248% | 2.00x | 61.8% | 64.9% |
| `>=0.70` | 1,122,009 | 1,364 | 0.122% | 0.98x | 87.0% | 83.9% |
| `0.50-0.55` | 283,240 | 616 | 0.217% | 1.75x | 55.2% | 54.1% |
| `0.40-0.50` | 422,126 | 296 | 0.070% | 0.57x | 45.4% | 42.9% |
| `<0.40` | 774,189 | 148 | 0.019% | 0.15x | 24.0% | 22.3% |

按 size：

| size bucket | all BUY | xuan selected | select rate | lift | selected winner |
|---|---:|---:|---:|---:|---:|
| `100-200` | 104,784 | 1,445 | 1.379% | 11.12x | 65.0% |
| `50-100` | 153,956 | 1,293 | 0.840% | 6.77x | 66.4% |
| `>=200` | 81,703 | 553 | 0.677% | 5.46x | 65.6% |
| `20-50` | 475,954 | 686 | 0.144% | 1.16x | 70.6% |
| `<20` | 2,434,394 | 55 | 0.002% | 0.02x | 67.3% |

最强二维选择偏好：

| price x size | all BUY | xuan selected | select rate | lift | selected winner |
|---|---:|---:|---:|---:|---:|
| `0.55-0.70 | 100-200` | 14,475 | 576 | 3.979% | 32.08x | 65.8% |
| `0.55-0.70 | >=200` | 7,880 | 245 | 3.109% | 25.07x | 62.9% |
| `0.50-0.55 | 100-200` | 7,691 | 224 | 2.912% | 23.48x | 53.1% |
| `0.55-0.70 | 50-100` | 22,777 | 506 | 2.222% | 17.91x | 61.9% |
| `0.50-0.55 | >=200` | 5,016 | 101 | 2.014% | 16.23x | 52.5% |
| `0.50-0.55 | 50-100` | 11,889 | 200 | 1.682% | 13.56x | 56.0% |
| `>=0.70 | 100-200` | 33,768 | 481 | 1.424% | 11.48x | 80.2% |
| `>=0.70 | 50-100` | 45,835 | 430 | 0.938% | 7.56x | 86.3% |

这个结果比前面的粗 gate 更有价值：

- xuan 不是跟所有 aggressive BUY。
- xuan 极少碰 `<20` 的噪音小单，强烈偏好 `50-200` 这一档。
- 最像主战场的是 `0.55-0.70 & size 50-200/200+`，不是最高价 `>=0.70`。
- `>=0.70` 的 winner 率高，但选择 lift 不高，说明这更像方向性确认区，不是主要 pair alpha 来源。
- 真正适合我们下一步建模的是：`taker BUY event` 作为触发器，`price 0.55-0.70` + `size 50-200` 作为第一层 gate，再叠加 `30s cheap completion evidence`。

## L1/Flow 特征全量审计

新增脚本：

```bash
python3 scripts/analyze_xuan_buy_selection_features.py
```

输出：

- `data/exports/xuan_buy_selection_features_20260505/xuan_buy_selection_features_summary.json`
- `data/exports/xuan_buy_selection_features_20260505/xuan_buy_selection_feature_groups.csv`
- `data/exports/xuan_buy_selection_features_20260505/xuan_buy_selection_features_report.md`

全量覆盖：

```text
all_buy_events_with_l1 = 3,235,273
selected_matched_events = 4,032 / 4,033
base_select_rate = 0.124626%
```

最强可执行 lift 来自 `price x size x immediate L1 pair`：

| bucket | all | selected | select rate | lift | all winner | selected winner |
|---|---:|---:|---:|---:|---:|---:|
| `0.55-0.70 | 100-200 | <=1.00` | 5,345 | 292 | 5.463% | 43.84x | 65.7% | 67.8% |
| `0.55-0.70 | >=200 | <=1.00` | 2,261 | 122 | 5.396% | 43.30x | 63.6% | 62.3% |
| `0.50-0.55 | 100-200 | <=1.00` | 2,671 | 134 | 5.017% | 40.26x | 58.6% | 56.7% |
| `0.55-0.70 | 100-200 | 1.00-1.03` | 7,570 | 227 | 2.999% | 24.06x | 60.5% | 62.6% |
| `0.55-0.70 | 50-100 | <=1.00` | 8,663 | 251 | 2.897% | 23.25x | 65.6% | 67.7% |
| `>=0.70 | 100-200 | <=1.00` | 9,281 | 251 | 2.704% | 21.70x | 88.4% | 76.9% |

解释：

- `immediate L1 pair <=1.00` 不是直接套利保证，因为真实 completion 需要 L2 和队列，但它是 xuan 选择 public BUY 的强特征。
- `0.55-0.70 & size 100-200 & immediate<=1.00` 是当前最像 xuan 首腿触发器的主战场。
- `>=0.70` 的全市场 winner 率很高，但 xuan selected winner 反而低于全市场；它不是最强选择偏好。
- recent flow 反而不强，`recent_buy_count_5s >5` 覆盖了绝大多数样本，不能解释 xuan 的选择。

## Oracle First-Fill Probe

为了区分“信号本身是否赚钱”和“我们是否能执行到同样价格”，新增 `first_price_source=trade` 的事件回测口径。

05-01 单日，规则：

```text
trigger = public taker_side=BUY
first_price_source = trade
price = 0.55-0.70
size = 100-200
L1 immediate pair <= 1.00
require_high_side = true
clip = 60
schedule = 30s@0.95
```

结果：

| schedule | rows | closed | first winner | residual | residual winner | pair p50 | delay p50 | pnl | ROI |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `30s@0.95` | 351 | 59.54% | 62.11% | 142 | 53.52% | 0.949997 | 8.41s | +73.87 | +0.57% |
| `30s@0.95 -> 90s@1.03` | 351 | 88.60% | 62.11% | 40 | 22.50% | 0.95 | 15.62s | -300.54 | -2.34% |
| `30s@0.95 -> 120s@1.04` | 351 | 91.17% | 62.11% | 31 | 16.13% | 0.95 | 16.36s | -237.24 | -1.84% |

裁决：

- 信号本身在 05-01 已经可以转正，但只在严格 `30s@0.95` 下转正。
- 宽 repair 会把收益吞掉，说明失败样本不是“多等会儿就好”，而是应当直接放弃/不做。
- 这也解释 xuan 的 30s 节奏：`30s` 不是随意等待，而是 cheap-completion evidence 的硬截止。
- 但这个 probe 使用 `trade price` 作为首腿，不是我们一定能执行到的价格；下一步必须解决执行可达性。

## 下一步：Candidate Cache

当前 `backtest_btc5m_taker_flow_follow.py` 可以验证单日，但 5 天全量太慢，瓶颈是每次都重复扫 L2 completion 窗口。

下一步应该新增 cache pipeline：

1. 预生成 `taker_buy_candidate_rows`：
   - one row per public BUY event
   - 包含 price/size/offset/high_side/L1 immediate pair/L2 first sweep/30s min completion path
2. 参数搜索只读 candidate CSV/Parquet，不再重复扫 SQLite。
3. 首先搜索：
   - price buckets
   - size buckets
   - immediate L1 pair buckets
   - 30s completion ceiling
   - skip/repair policy
4. 目标不是复刻 xuan 的每笔成交，而是找到 “positive before execution slippage” 的最小规则集，再进入 shadow 验证执行可达性。

## 工程注意

`backtest_xuan_proxy_completion_first_v1.py` 的 5 天全量 high-side L2 sweep 当前太慢，5s/20s 采样都不适合快速搜索。下一步需要先做回测加速：

- 按 market 预载 L2 后建立秒级索引。
- 对 completion sweep 做窗口内候选缓存。
- 搜索阶段先输出 candidate rows，再离线聚合参数，而不是每个参数重复扫 SQLite。
- 对 taker-like 研究，候选生成应从 `md_trades.taker_side=BUY` 事件出发，而不是固定时间采样。
- 事件触发器不能跟随所有 BUY flow；必须先实现 xuan-selection lift gate，尤其是 `price x size`。

否则我们会在参数搜索上浪费大量时间，无法高频迭代。

## 重大更新：事件触发 Taker BUY 策略核

新增 `scripts/backtest_btc5m_taker_buy_signal_fast.py` 后，已把研究从“xuan 做了什么”推进到“我们能否用 replay 复现一个正收益策略核”。

回测口径：

```text
数据 = replay SQLite, 2026-04-27..2026-05-01
触发器 = public md_trades.taker_side=BUY
方向 = market_side 必须等于当前 L1 high-side
首腿成本 = L2 ask sweep VWAP，不使用 xuan 成交价
补腿 = opposite L2 ask sweep, 30s 内 pair_cost <= ceiling
未补腿 = 按 settlement winner_side 结算残仓
```

最强核心规则：

```text
trade price = 0.55..0.70
trade size = 100..150
L1 immediate pair <= 0.99
completion ceiling = 0.96 within 30s
clip = 60
cooldown = 10s
```

5 天结果：

| rule | rows | closed | first winner | residual | residual winner | PnL | ROI | negative days |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `price55_70_size100_150_l1imm099_30s96_l2first_v1` | 215 | 55.81% | 73.49% | 95 | 81.05% | +1440.57 | 18.21% | none |
| `price55_70_size100_150_l1imm099_30s96_l2first_clip120_v1` | 161 | 57.14% | 69.57% | 69 | 78.26% | +1897.70 | 16.03% | none |
| `price55_70_size100_150_l1imm099_30s96_l2first_clip160_v1` | 139 | 53.96% | 70.50% | 64 | 75.00% | +1833.52 | 13.35% | none |
| `price55_70_size100_150_l1imm0995_30s95_l2first_v1` | 239 | 52.72% | 71.55% | 113 | 76.99% | +1448.57 | 16.43% | none |

边界测试：

| adjacent rule | rows | closed | first winner | residual | residual winner | PnL | ROI | negative days |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `price55_70_size50_100_l1imm099_30s96_l2first_v1` | 589 | 60.78% | 65.70% | 231 | 58.87% | +639.21 | 2.99% | 2026-04-29 |
| `price55_70_size150_200_l1imm099_30s96_l2first_v1` | 150 | 61.33% | 60.00% | 58 | 55.17% | +19.79 | 0.36% | 2026-04-27, 2026-04-29 |
| `price70_90_size100_150_l1imm100_30s98_l2first_v1` | 375 | 73.87% | 78.67% | 98 | 75.51% | +294.74 | 1.70% | 2026-05-01 |

裁决：

- 当前最像 xuan 精髓的不是“所有 30s 配对”，而是 `public BUY 触发器 + high-side + 100-150 中等冲击流 + immediate pair <= 0.99 + 30s cheap completion`。
- `100-150` 是关键容量桶。`50-100` 频率高但质量差，`150-200` edge 基本消失，高价 `0.70-0.90` 有方向性但 pair 利润太薄。
- clip 从 `60` 放大到 `120/160` 后仍 5 天全正，但 ROI 下滑，说明容量存在，不能无限放大。
- 这个策略核已经在 L2 sweep 成本口径下转正，不再只是 xuan public fill 的 hindsight 解释。
- 下一步不是继续扩大 pair target，而是把该规则做成 shadow gate，验证我们自己的成交可达性和线上延迟。

## 反事实与风险修正

### Immediate Pair Gate 是硬门槛

保持 `price55_70 + size100_150 + high-side + 30s@0.96 + clip60` 不变，只放宽 `L1 immediate pair`：

| max immediate pair | rows | closed | first winner | residual | residual winner | PnL | ROI | negative days |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `0.99` | 215 | 55.81% | 73.49% | 95 | 81.05% | +1440.57 | 18.21% | none |
| `1.00` | 418 | 54.07% | 66.99% | 192 | 68.23% | +1369.05 | 8.89% | 2026-04-29 |
| `1.01` | 1220 | 50.25% | 60.49% | 607 | 54.70% | -943.70 | -2.09% | 2026-04-27, 2026-04-28, 2026-04-29, 2026-05-01 |
| `1.02` | 1523 | 50.62% | 60.21% | 752 | 53.99% | -1497.31 | -2.66% | 2026-04-28, 2026-04-29, 2026-04-30, 2026-05-01 |
| `1.03` | 1621 | 50.28% | 60.64% | 806 | 54.96% | -1157.65 | -1.93% | 2026-04-28, 2026-04-29, 2026-04-30, 2026-05-01 |

裁决：

- `<=0.99` 是默认 hard gate。
- `<=1.00` 可以作为 shadow 扩展观察，但不能作为默认 enforce。
- `>=1.01` 直接破坏 edge，说明这不是“跟随所有 BUY flow”的策略。

### `block_after_residual` 后仍成立

原始事件回测在 `30s` 未补腿后会继续扫描同一 market 的后续机会。为了贴近 `active_tranche_limit=1`，新增 `--block-after-residual`：一旦留下 residual，本 market 不再开新 first leg。

| rule | rows | closed | first winner | residual | residual winner | PnL | ROI | negative days |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| clip60 原始 | 215 | 55.81% | 73.49% | 95 | 81.05% | +1440.57 | 18.21% | none |
| clip60 block residual | 212 | 56.60% | 73.11% | 92 | 80.43% | +1376.39 | 17.66% | none |
| clip120 原始 | 161 | 57.14% | 69.57% | 69 | 78.26% | +1897.70 | 16.03% | none |
| clip120 block residual | 158 | 58.23% | 68.99% | 66 | 77.27% | +1771.92 | 15.27% | none |
| clip160 原始 | 139 | 53.96% | 70.50% | 64 | 75.00% | +1833.52 | 13.35% | none |
| clip160 block residual | 138 | 54.35% | 70.29% | 63 | 74.60% | +1786.80 | 13.11% | none |

裁决：

- 策略核不是靠 residual 后继续在同一 market 加仓堆出来的。
- `block_after_residual=true` 应作为 shadow 默认口径。
- residual winner rate 仍高，说明未在 30s clean close 的 tranche 不是纯坏库存；但 live 不能默认持有到结算，需要单独做 residual exit/hold 策略。

### 与 xuan public truth 的重叠

新增：

```bash
python3 scripts/analyze_taker_buy_signal_xuan_overlap.py \
  --rows-csv data/exports/btc5m_taker_buy_signal_fast_0427_0501_price55_70_size100_150_l1imm099_30s96_l2first_blockres_clip60_v1/btc5m_taker_buy_signal_fast_rows.csv \
  --replay-root data/replay \
  --output-dir data/exports/taker_buy_signal_xuan_overlap_0427_0501_core_blockres_clip60_v1
```

输出：

- `data/exports/taker_buy_signal_xuan_overlap_0427_0501_core_blockres_clip60_v1/taker_buy_signal_xuan_overlap_summary.json`
- `data/exports/taker_buy_signal_xuan_overlap_0427_0501_core_blockres_clip60_v1/taker_buy_signal_xuan_overlap_report.md`

结果：

| overlap metric | count | rate |
|---|---:|---:|
| xuan any trade in market | 156 / 212 | 73.58% |
| xuan same-side trade in market | 156 / 212 | 73.58% |
| xuan any trade within 5s | 85 / 212 | 40.09% |
| xuan same-side trade within 5s | 80 / 212 | 37.74% |
| xuan any trade within 30s | 134 / 212 | 63.21% |
| xuan same-side trade within 30s | 131 / 212 | 61.79% |

按是否与 xuan 30s 同方向重叠拆分：

| cohort | rows | PnL | ROI | first winner | closed |
|---|---:|---:|---:|---:|---:|
| xuan same-side near30 | 131 | +972.54 | 20.34% | 74.81% | 53.44% |
| not xuan same-side near30 | 81 | +403.85 | 13.41% | 70.37% | 61.73% |

裁决：

- 这个规则不是纯 replay artifact，约 `62%` 的候选在 `30s` 内与 xuan 同方向 public BUY 重叠。
- xuan 重叠 cohort 更强，说明该 gate 确实抓到了 xuan 偏好的状态空间。
- 非 xuan 重叠 cohort 也为正，说明它不是简单复制 xuan 成交，而是有机会作为“超越 xuan”的可扩展子策略。

## Public WS Shadow Observer

新增：

```bash
uv run python scripts/run_taker_buy_signal_public_ws_shadow.py \
  --config configs/xuan/taker_buy_signal_core_v1.json \
  --round-offsets 0,1,2 \
  --duration-sec 900 \
  --output-dir data/exports/taker_buy_signal_public_ws_shadow_$(date -u +%Y%m%d_%H%M%S) \
  --trigger-source last_trade_price \
  --probe-immediate-pairs 0.99,1.00,1.01
```

固定语义：

- public WS only，不用密钥，不发 REST，不下单。
- 默认 `--trigger-source last_trade_price`，与 replay `md_trades` 主口径一致。
- `--trigger-source price_change/hybrid` 只用于 parser/debug，不作为研究默认口径。
- base observer 启动时只解析一次 `round-offsets`，因此默认运行 `<=900s`。
- `--probe-immediate-pairs` 只做 no-order sensitivity probe，不改变主策略 hard gate。
- 每个候选输出 `allowed/reason`；放行后输出 `would_take_first`、`completion` 或 `residual_open`。
- `block_after_residual=true` 时，一个 market 残仓后不再继续开新 tranche。

长跑使用 rolling wrapper：

```bash
uv run python scripts/run_taker_buy_signal_public_ws_shadow_rolling.py \
  --config configs/xuan/taker_buy_signal_core_v1.json \
  --round-offsets 0,1,2 \
  --duration-sec 3600 \
  --chunk-sec 840 \
  --output-dir data/exports/taker_buy_signal_public_ws_shadow_rolling_$(date -u +%Y%m%d_%H%M%S) \
  --trigger-source last_trade_price \
  --probe-immediate-pairs 0.99,1.00,1.01
```

rolling wrapper 每个 chunk 重新解析最新市场，并追加到同一个输出目录；基础 observer 仍保持单次固定订阅，便于调试。

smoke test：

```bash
uv run python scripts/run_taker_buy_signal_public_ws_shadow.py \
  --config configs/xuan/taker_buy_signal_core_v1.json \
  --round-offsets 0,1 \
  --duration-sec 75 \
  --output-dir data/exports/taker_buy_signal_public_ws_shadow_smoke_20260505_last_trade \
  --trigger-source last_trade_price
```

结果：

| metric | value |
|---|---:|
| subscribed markets | 2 |
| candidates | 6 |
| allowed | 0 |
| reason | `immediate_pair_too_high` |
| emitted events | 10 |
| place real orders | false |

解释：

- 程序链路已跑通：Gamma resolve、public WS subscribe、book/trade normalize、gate explain、report 写入。
- 75 秒内没有放行是符合预期的：候选触发时 `l1_immediate_pair` 在 `1.01-1.076`，高于默认 hard gate `0.99`。
- 这也在线验证了一个重要风险控制：策略不会因为有 BUY flow 就追，而是会在 immediate pair 不够便宜时直接 block。

15 分钟 rolling shadow：

```bash
uv run python scripts/run_taker_buy_signal_public_ws_shadow_rolling.py \
  --config configs/xuan/taker_buy_signal_core_v1.json \
  --round-offsets 0,1,2 \
  --duration-sec 900 \
  --chunk-sec 280 \
  --output-dir data/exports/taker_buy_signal_public_ws_shadow_rolling_20260505_073955 \
  --trigger-source last_trade_price
```

结果：

| metric | value |
|---|---:|
| chunks | 4 |
| emitted events | 47 |
| strict candidates | 11 |
| allowed | 0 |
| candidate `l1_immediate_pair` p50 | 1.030 |
| candidate `l1_immediate_pair` min | 1.010 |
| candidate offset p50 | 29.655s |
| dominant block reason | `immediate_pair_too_high` |

裁决：

- 在线候选密度存在：约 15 分钟内出现 11 个 `price/size/high-side` 候选。
- 但这 11 个候选的 `l1_immediate_pair` 全部大于 `0.99`，最小也只有 `1.01`，不满足可锁利状态。
- 这和 replay 反事实一致：`max_l1_immediate_pair=1.01` 之后回测转负，所以当前不应为了参与率放宽 gate。
- 下一步 shadow 应继续累计更长时间，目标不是追求每 15 分钟必有放行，而是等待真正满足 `<=0.99` 的稀缺窗口。

5 分钟 multi-threshold probe：

```bash
uv run python scripts/run_taker_buy_signal_public_ws_shadow_rolling.py \
  --config configs/xuan/taker_buy_signal_core_v1.json \
  --round-offsets 0,1,2 \
  --duration-sec 300 \
  --chunk-sec 140 \
  --output-dir data/exports/taker_buy_signal_public_ws_shadow_probe_20260505_082514 \
  --trigger-source last_trade_price \
  --probe-immediate-pairs 0.99,1.00,1.01
```

结果：

| metric | value |
|---|---:|
| strict candidates | 10 |
| main allowed | 0 |
| candidate `l1_immediate_pair` p50 | 1.010 |
| candidate `l1_immediate_pair` min | 1.010 |
| `1.01` probe starts | 1 |
| `1.01` probe completed | 0 |
| `1.01` probe min pair cost seen 30s | 0.980 |
| `1.01` probe status | `residual_open` |

裁决：

- 即使把 sensitivity probe 放宽到 `1.01`，这条 near-miss 在 `30s` 内也只改善到 `0.98`，没有达到默认 completion ceiling `0.96`。
- 这条在线证据继续支持 `0.99` hard gate；不能因为 `1.01` 看起来“只差 2c”就放宽。
- 如果后续要研究 `1.00/1.01`，必须作为独立 residual/repair 策略，而不是直接并入当前 clean-completion core。

## Near-Miss Replay 审计

新增固定审计脚本：

```bash
uv run python scripts/analyze_taker_buy_nearmiss_replay.py \
  --inputs \
    data/exports/btc5m_taker_buy_signal_fast_0427_0501_price55_70_size100_150_l1imm100_30s96_l2first_highside_v1 \
    data/exports/btc5m_taker_buy_signal_fast_0427_0501_price55_70_size100_150_l1imm101_30s96_l2first_highside_v1 \
  --output-dir data/exports/taker_buy_nearmiss_replay_audit_20260505
```

输出：

- `data/exports/taker_buy_nearmiss_replay_audit_20260505/taker_buy_nearmiss_replay_audit.json`
- `data/exports/taker_buy_nearmiss_replay_audit_20260505/taker_buy_nearmiss_replay_audit.md`

核心结果：

| `l1_immediate_pair` 桶 | rows | closed | first winner | residual winner | PnL | ROI | negative days |
|---|---:|---:|---:|---:|---:|---:|---|
| `<=0.99` | 198 | 59.60% | 73.23% | 81.25% | +1276.86 | +17.57% | - |
| `0.99-1.00` | 221 | 48.87% | 61.54% | 59.29% | +114.99 | +1.41% | 2026-04-27, 2026-04-29 |
| `1.00-1.01` | 924 | 47.40% | 58.77% | 53.29% | -1315.01 | -3.85% | 2026-04-27..2026-05-01 |

`0.99-1.00` 内部没有稳定可直接升级的子桶：

| 子切分 | bucket | rows | PnL | ROI | 说明 |
|---|---|---:|---:|---:|---|
| offset | `<30s` | 79 | +81.90 | +2.85% | 仍有负日 |
| offset | `30-60s` | 59 | -2.90 | -0.13% | 无 edge |
| offset | `60-120s` | 55 | +77.90 | +3.77% | 三个负日 |
| offset | `120s+` | 28 | -41.91 | -3.99% | 明确拒绝 |
| first L2 VWAP | `<0.60` | 87 | -49.19 | -1.65% | 明确拒绝 |
| first L2 VWAP | `0.60-0.65` | 68 | -43.74 | -1.73% | 明确拒绝 |
| first L2 VWAP | `>=0.65` | 66 | +207.91 | +7.81% | 仍有负日，样本不足，不能并入 core |
| public size | `<=100` | 34 | +98.64 | +7.77% | 样本小且多负日 |
| public size | `100-110` | 66 | +55.30 | +2.30% | 边际太弱 |
| public size | `>=110` | 121 | -38.95 | -0.86% | 明确拒绝 |

裁决：

- `0.99` hard gate 继续冻结为 V1 clean-completion core 边界。
- `0.99-1.00` 不能为了提高参与率直接放行；它只能作为后续独立 residual/repair 策略研究。
- `1.00-1.01` 在五天窗口全部为负，V1 core 明确拒绝。
- 这解释了 5 分钟在线 probe：`1.01` near-miss 虽然 30s 内看到 `0.98`，但没有达到 `0.96` completion ceiling，属于“看似接近、实际吞收益”的状态。

## Dynamic Clip 审计

新增固定审计脚本：

```bash
uv run python scripts/analyze_taker_buy_dynamic_clip_from_runs.py \
  --clip60-rows data/exports/btc5m_taker_buy_signal_fast_0427_0501_price55_70_size100_150_l1imm099_30s96_l2first_blockres_clip60_v1/btc5m_taker_buy_signal_fast_rows.csv \
  --clip120-rows data/exports/btc5m_taker_buy_signal_fast_0427_0501_price55_70_size100_150_l1imm099_30s96_l2first_blockres_clip120_v1/btc5m_taker_buy_signal_fast_rows.csv \
  --clip160-rows data/exports/btc5m_taker_buy_signal_fast_0427_0501_price55_70_size100_150_l1imm099_30s96_l2first_blockres_clip160_v1/btc5m_taker_buy_signal_fast_rows.csv \
  --output-dir data/exports/taker_buy_dynamic_clip_audit_20260505
```

输出：

- `data/exports/taker_buy_dynamic_clip_audit_20260505/taker_buy_dynamic_clip_audit.json`
- `data/exports/taker_buy_dynamic_clip_audit_20260505/taker_buy_dynamic_clip_audit.md`

核心结果：

| policy | rows | fallback | PnL | ROI | min day pnl | closed | negative days |
|---|---:|---:|---:|---:|---:|---:|---|
| `base60` | 212 | 0 | +1376.39 | 17.66% | +147.98 | 56.60% | - |
| `l1<=0.98 -> 120 else 60` | 212 | 9 | +2354.52 | 19.65% | +186.09 | 55.66% | - |
| `l1<=0.98 -> 160 else 60` | 212 | 15 | +2621.47 | 18.14% | +263.54 | 52.36% | - |
| `offset>=60 -> 160 else 60` | 212 | 44 | +2487.44 | 21.98% | +265.91 | 55.66% | - |
| `offset>=60 && l1<=0.98 -> 160 else 60` | 212 | 10 | +2531.52 | 23.61% | +220.11 | 55.66% | - |
| `offset>=60 OR l1<=0.98 -> 160 else 60` | 212 | 49 | +2577.39 | 17.13% | +309.35 | 52.36% | - |

解释：

- 当前不应该回答“每次是不是直接买 120/160”。更正确的设计是状态化 sizing。
- `l1<=0.98` 是更强 pair edge，可上调；`l1>0.98` 在 clip160 下会转负，不能放大。
- `offset>=60s` 的样本 ROI 显著高于开盘前 60s，说明 xuan 的“开盘 1 分钟后更稳定”不是偶然。
- 第一版 native dynamic backtest 应优先验证：`base clip=60`，仅当 `offset>=60s && l1_immediate_pair<=0.98` 时升到 `160`；如果 fillability 或容量不稳，则降级为 `l1<=0.98 -> 120`。
- 该审计由多组固定 clip replay 拼接而来，不等同于原生动态回测；进入实盘前必须用 candidate cache/native dynamic backtest 复核。

## Candidate Cache 当前口径复核

新增 candidate cache 主路径：

```bash
uv run python scripts/build_taker_buy_signal_candidate_cache.py \
  --replay-root data/replay \
  --days 2026-04-27,2026-04-28,2026-04-29,2026-04-30,2026-05-01 \
  --min-trade-price 0.50 \
  --max-trade-price 0.75 \
  --min-trade-size 50 \
  --max-trade-size 250 \
  --clip 60 \
  --output-dir data/exports/taker_buy_signal_candidate_cache_0427_0501_clip60_v1
```

输出：

- `data/exports/taker_buy_signal_candidate_cache_0427_0501_clip60_v1/taker_buy_signal_candidate_cache.csv`
- `data/exports/taker_buy_signal_candidate_cache_0427_0501_clip60_v1/taker_buy_signal_candidate_cache_summary.json`

缓存覆盖：

| metric | value |
|---|---:|
| BTC 5m markets | 1343 |
| candidate rows | 59569 |
| trigger price range | 0.50-0.75 |
| trigger size range | 50-250 |
| clip | 60 |
| completion windows | 30s |

聚焦搜索：

```bash
uv run python scripts/search_taker_buy_signal_candidate_cache.py \
  --cache-csv data/exports/taker_buy_signal_candidate_cache_0427_0501_clip60_v1/taker_buy_signal_candidate_cache.csv \
  --output-dir data/exports/taker_buy_signal_candidate_search_focused_0427_0501_clip60_v1 \
  --price-ranges 0.55-0.70,0.55-0.60,0.60-0.65,0.65-0.70 \
  --size-ranges 100-150,100-130,110-150,120-150 \
  --first-ranges 0.55-0.75,0.55-0.70,0.60-0.75,0.60-0.70 \
  --offset-ranges 0-240,0-60,60-240,60-180,30-180,120-240 \
  --max-l1-pairs 0.98,0.985,0.99,0.995 \
  --pair-ceilings 0.95,0.96 \
  --side-alignments high \
  --min-rows 40 \
  --top-n 80
```

输出：

- `data/exports/taker_buy_signal_candidate_search_focused_0427_0501_clip60_v1/taker_buy_signal_candidate_search_summary.json`
- `data/exports/taker_buy_signal_candidate_search_focused_0427_0501_clip60_v1/taker_buy_signal_candidate_search_report.md`

聚焦搜索结果：

| rule | rows | PnL | ROI | min day pnl | closed | first winner | residual winner | negative days |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| current-cache exact old core: `0.55-0.70 size100-150 first0.55-0.75 l1<=0.99 ceiling0.96 block` | 400 | +546.99 | +3.67% | +45.84 | 85.25% | 71.00% | 52.54% | - |
| top PnL stable: `0.55-0.70 size100-150 first0.60-0.75 l1<=0.995 ceiling0.95 block` | 319 | +900.14 | +7.28% | +77.21 | 78.37% | 74.29% | 66.67% | - |
| higher ROI thin: `0.65-0.70 size100-150 first0.55-0.75 l1<=0.995 ceiling0.95 no-block` | 159 | +537.70 | +8.36% | +50.54 | 78.62% | 73.58% | 73.53% | - |
| highest ROI thin subcase: `0.65-0.70 size100-150 first0.55-0.75 l1<=0.995 ceiling0.95` | 102 | +448.26 | +10.88% | +10.44 | 75.49% | 72.55% | 80.00% | - |

裁决修正：

- 当前应以 candidate cache 结果作为迭代主路径，旧 fast backtest 输出只作历史参考。
- 数据更新后，旧 core 的成交频率上升、closed rate 大幅上升，但 ROI 从旧口径的高双位数降到中个位数；这更接近真实可执行策略预期。
- 真正增强点不是放宽 `l1` 到 `1.00+`，而是要求 `first_l2_vwap >= 0.60`，并把 completion ceiling 从 `0.96` 收紧到 `0.95`。
- 高价区 `0.65-0.70` 有更高 ROI 和 residual winner，但样本更薄，适合做 up-clip 或优先级加权，不适合作为唯一主策略。
- 下一步实现应基于 candidate cache/native dynamic backtest，而不是再用慢速 per-market replay 脚本做全量参数搜索。

## Finalist 稳定性与滑点压力测试

新增固定审计脚本：

```bash
uv run python scripts/analyze_taker_buy_candidate_finalists.py \
  --cache-csv data/exports/taker_buy_signal_candidate_cache_0427_0501_clip60_v1/taker_buy_signal_candidate_cache.csv \
  --search-results-csv data/exports/taker_buy_signal_candidate_search_focused_0427_0501_clip60_v1/taker_buy_signal_candidate_search_results.csv \
  --output-dir data/exports/taker_buy_candidate_finalists_0427_0501_clip60_v1 \
  --top-n 12
```

输出：

- `data/exports/taker_buy_candidate_finalists_0427_0501_clip60_v1/taker_buy_candidate_finalists.json`
- `data/exports/taker_buy_candidate_finalists_0427_0501_clip60_v1/taker_buy_candidate_finalists.md`

当前 finalist 排名：

| rank | rule | rows | PnL | ROI | min day | closed | first winner | residual winner |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | `price 0.55-0.70 size 100-150 first 0.60-0.75 l1<=0.995 ceiling0.95 block` | 319 | +900.14 | 7.28% | +77.21 | 78.37% | 74.29% | 66.67% |
| 2 | `price 0.55-0.70 size 100-150 first 0.60-0.70 l1<=0.995 ceiling0.95 block` | 299 | +833.93 | 7.24% | +64.04 | 78.26% | 73.58% | 66.15% |
| 3 | `price 0.55-0.70 size 100-150 first 0.60-0.75 l1<=0.99 ceiling0.95 no-block` | 283 | +671.03 | 6.10% | +69.31 | 79.86% | 74.20% | 63.16% |
| 4 | `price 0.55-0.70 size 100-150 first 0.60-0.75 l1<=0.99 ceiling0.95 block` | 274 | +648.39 | 6.09% | +65.07 | 80.29% | 74.09% | 62.96% |

Top finalist day split:

| day | PnL |
|---|---:|
| 2026-04-27 | +173.22 |
| 2026-04-28 | +77.21 |
| 2026-04-29 | +224.64 |
| 2026-04-30 | +244.29 |
| 2026-05-01 | +180.78 |

Top finalist slippage stress:

| adverse slippage per leg | PnL | ROI | min day | negative days |
|---:|---:|---:|---:|---|
| 0 bps | +900.14 | 7.28% | +77.21 | - |
| 25 bps | +814.79 | 6.57% | +55.01 | - |
| 50 bps | +729.44 | 5.86% | +32.81 | - |
| 100 bps | +558.74 | 4.45% | -11.59 | 2026-04-28 |

距离定型判断：

- 当前阶段：`research_candidate_v1`，不是 enforce-ready。
- 距离策略冻结大约 `65-70%`。
- 已完成：正收益主候选、五天全正、candidate cache 主回测链、near-miss 拒绝证据、滑点压力测试。
- 未完成：更多 out-of-sample replay、原生动态 sizing 回测、public WS shadow fillability/latency、真实滑点/partial-fill 校准。
- 如果新增 5-10 天 replay 中 top finalist 仍全正或仅轻微回撤，且 50 bps/leg 压测仍不破产，就可以进入 shadow freeze；如果 shadow 的实际 fill slippage 接近或超过 100 bps/leg，必须继续降频/降 clip。

## Top Finalist 与 xuan 对齐

新增导出与 overlap 命令：

```bash
uv run python scripts/export_taker_buy_candidate_policy_rows.py \
  --cache-csv data/exports/taker_buy_signal_candidate_cache_0427_0501_clip60_v1/taker_buy_signal_candidate_cache.csv \
  --output-dir data/exports/taker_buy_top_finalist_policy_rows_0427_0501_clip60_v1 \
  --price-lo 0.55 \
  --price-hi 0.70 \
  --size-lo 100 \
  --size-hi 150 \
  --first-lo 0.60 \
  --first-hi 0.75 \
  --max-l1-pair 0.995 \
  --pair-ceiling 0.95 \
  --side-alignment high \
  --block-after-residual \
  --cooldown-s 10

uv run python scripts/analyze_taker_buy_signal_xuan_overlap.py \
  --rows-csv data/exports/taker_buy_top_finalist_policy_rows_0427_0501_clip60_v1/taker_buy_candidate_policy_rows.csv \
  --replay-root data/replay \
  --output-dir data/exports/taker_buy_top_finalist_xuan_overlap_0427_0501_clip60_v1
```

输出：

- `data/exports/taker_buy_top_finalist_policy_rows_0427_0501_clip60_v1/taker_buy_candidate_policy_rows.csv`
- `data/exports/taker_buy_top_finalist_xuan_overlap_0427_0501_clip60_v1/taker_buy_signal_xuan_overlap_summary.json`

结果：

| metric | count | rate |
|---|---:|---:|
| rows | 319 | - |
| markets | 294 | - |
| xuan any in market | 229 | 71.79% |
| xuan same side in market | 229 | 71.79% |
| xuan any near 30s | 198 | 62.07% |
| xuan same side near 30s | 190 | 59.56% |
| xuan same near 30s delta p50 | 2.75s | - |

Cohort 对比：

| cohort | rows | PnL | ROI | first winner | closed |
|---|---:|---:|---:|---:|---:|
| `xuan_same_near30` | 190 | +536.44 | 7.31% | 76.84% | 74.74% |
| `not_xuan_same_near30` | 129 | +363.70 | 7.24% | 70.54% | 83.72% |

裁决：

- top finalist 明确落在 xuan 活动区域：约 60% 的候选在 30s 内有 xuan 同侧 BUY。
- 但非 xuan-overlap cohort 也同样为正，说明这条规则不是“只复制 xuan 的交易”，而是提取了公开 market state edge。
- xuan-overlap cohort 的 first winner 更高，non-overlap cohort 的 close rate 更高；这提示后续可做二级 sizing，而不是简单 hard block 非 overlap。
- 这把策略从“参考 xuan”推进到“基于 xuan 提炼公开可观测信号”，是超越 xuan 的必要前提。

## OOS 验证入口

新数据到位后，不允许先看数据再调参数。固定使用 top finalist，通过一键 OOS 脚本验证：

```bash
uv run python scripts/validate_taker_buy_finalist_oos.py \
  --replay-root data/replay \
  --days 2026-05-02,2026-05-03,2026-05-04 \
  --output-dir data/exports/taker_buy_finalist_oos_20260502_20260504
```

脚本固定规则：

```text
price 0.55-0.70
size 100-150
first_l2_vwap 0.60-0.75
side_alignment = high
l1_immediate_pair <= 0.995
completion_pair_ceiling <= 0.95
clip = 60
block_after_residual = true
cooldown_s = 10
```

输出：

- `candidate_cache/taker_buy_signal_candidate_cache.csv`
- `policy_rows/taker_buy_candidate_policy_rows.csv`
- `xuan_overlap/taker_buy_signal_xuan_overlap_summary.json`
- `taker_buy_finalist_oos_validation.json`
- `taker_buy_finalist_oos_validation.md`

OOS go/no-go 默认阈值：

| gate | threshold |
|---|---:|
| rows | `>= 40/day` |
| negative days | `0` |
| ROI | `>= 3%` |
| closed rate | `>= 70%` |
| first winner rate | `>= 68%` |
| xuan same near30 rate | `>= 45%`，如果 xuan 表存在 |

解释：

- 这个脚本不做参数搜索，只验证当前冻结候选。
- 如果 OOS 通过，进入 `shadow freeze`，不是直接实盘 enforce。
- 如果 OOS 失败，不允许在同一 OOS 窗口内重新搜索参数然后宣布通过；必须回到研究模式，把失败 day/market 单独解释清楚。
- 如果只缺 xuan overlap，但市场侧 PnL/ROI/closed/winner 通过，可以标记为 `market_pass_xuan_gap`，但不能声称“继续逼近 xuan”。

## 当前不能误解的点

- 这不是 xuan 的挂单真值。它是 public replay + xuan public fills 的 tranche-level 归因。
- `min_pair_cost_30s` 是首腿后的 live 可观测变量，不是首腿前的预言变量。
- 因此正确实现不是“开仓前预测 30s 一定完成”，而是“开仓前只进入有 L2 edge 的局，开仓后用 30s 证据窗口决定继续还是修复”。
- 我们此前低频正收益 core 只间接捕捉了这个信号，所以赚钱但频率低；下一步必须直接把 L2 edge 和 30s evidence 写进 backtest，而不是继续调 pair target。

## 可复现命令

```bash
python3 scripts/analyze_xuan_30s_signal_fingerprint.py
```

输出：

- `data/exports/xuan_signal_fingerprint_20260505/signal_fingerprint.json`
- `data/exports/xuan_signal_fingerprint_20260505/selected_rules.csv`
- `data/exports/xuan_signal_fingerprint_20260505/signal_fingerprint.md`
