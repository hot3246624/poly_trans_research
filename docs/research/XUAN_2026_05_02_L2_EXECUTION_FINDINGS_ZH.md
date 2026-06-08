# xuanxuan008 L2 执行与 30s Completion 研究结论

## 摘要

本轮使用 `2026-04-27` 到 `2026-05-01` 的可信 replay DB，只读 SQLite，不读取 raw，不使用 own execution truth。核心结论是：

- `xuan` 不是纯 maker-first。5 秒 exact price+size public trade match 中，exact 子集全部对应 `taker_side=BUY`。
- `xuan` 也不是“笨 taker sweep”。如果按 xuan 同 size 直接吃本地 L2，成交价格仍比 xuan 观测价格贵。
- 关键修正是时间戳：xuan Data API trade timestamp 相比 public market trade timestamp 中位数约晚 `3.3s`，必须优先使用 exact public trade timestamp 做 L2 对齐。
- xuan 的 30s completion controller 不是只在 `pair_cost <= 0.90` 补腿，而是允许接近 parity，甚至略高于 parity 的快速闭合。
- 若要复刻他的 30s 配对率，L2 反事实显示 completion ceiling 需要落在约 `1.0075-1.010`，而不是 `0.90-0.95`。

## 数据与口径

- Replay root: `/Users/hot/web3Scientist/poly_trans_research/data/replay`
- 日期: `2026-04-27` 至 `2026-05-01`
- 可信起点: `2026-04-27T07:25:00Z`
- xuan tranche 样本: `4587`
- 最新日 `2026-05-01` tranche 样本: `1495`
- 核心输出:
  - `data/exports/xuan_research_runs/replay_20260502_full/xuan_public_trade_match`
  - `data/exports/xuan_research_runs/replay_20260502_full/xuan_l2_counterfactual_5d_matchts_30s_100`
  - `data/exports/xuan_research_runs/replay_20260502_full/xuan_l2_counterfactual_5d_matchts_30s_101`
  - `data/exports/xuan_research_runs/replay_20260502_full/xuan_l2_completion_curve_5d`

## 发现 1：xuan 的 public truth 是 taker-like，不支持纯 maker-first

5 秒窗口 public trade match：

| 指标 | 结果 |
|---|---:|
| xuan BUY trades | `12156` |
| matched rate | `94.12%` |
| exact price+size match rate | `69.27%` |
| exact subset taker-like | `100%` |
| exact subset maker-like | `0%` |

解释：这不证明 xuan 每一笔都是 taker，但足够推翻“主要靠 maker 排队成交”的主叙事。后续策略实现不应把 maker-first 当作唯一执行边界。

## 发现 2：Data API 时间戳有系统性滞后

在 exact price+size match 子集中，public market trade timestamp 相比 xuan Data API timestamp 通常更早：

| phase | exact matches | match_time_diff p50 |
|---|---:|---:|
| open_residual | `4620` | `-3325ms` |
| clean_completion | `3801` | `-3389ms` |

解释：如果直接用 xuan Data API 秒级 timestamp 去取 L2，会晚取约 3 秒。这会把“真实成交时刻的盘口”错看成“成交后几秒的盘口”，进而误判 xuan 的价格优势。

## 发现 3：时间戳修正后，L2 价格差缩小但仍存在

使用 exact public trade timestamp 后，05-01 first-leg 的 L2 sweep VWAP 相比 xuan 观测 first price：

| 口径 | first_l2_vwap - observed_first_price p50 |
|---|---:|
| 未修正 timestamp | `~0.0200` |
| 使用 exact public trade timestamp | `~0.00865` |

解释：约一半以上的“2c 执行优势”来自时间戳错位；剩余约 `0.9c` 仍可能来自拆单、瞬时深度、低于 5 档的真实成交、maker/taker 混合，或 public replay 粒度不足。

## 发现 4：30s completion ceiling 约在 1.0075 到 1.010

使用 xuan 的真实 first-leg time/side/size，并用 matched public trade timestamp 对齐后，扫描 30 秒内 opposite L2 最小可成交 pair cost：

5 天全样本：

| ceiling | L2 hit rate | xuan observed 30s |
|---:|---:|---:|
| `0.9900` | `71.24%` | `82.08%` |
| `1.0000` | `77.96%` | `82.08%` |
| `1.0025` | `79.03%` | `82.08%` |
| `1.0050` | `80.16%` | `82.08%` |
| `1.0075` | `80.95%` | `82.08%` |
| `1.0100` | `85.76%` | `82.08%` |

最新日 `2026-05-01`：

| ceiling | L2 hit rate | xuan observed 30s |
|---:|---:|---:|
| `0.9900` | `70.50%` | `80.33%` |
| `1.0000` | `76.59%` | `80.33%` |
| `1.0050` | `78.73%` | `80.33%` |
| `1.0075` | `79.46%` | `80.33%` |
| `1.0100` | `84.21%` | `80.33%` |

解释：xuan 的快速 completion 行为更接近“在 30 秒内接受接近 parity 的补腿”，而不是“只等低成本补腿”。`0.90` 附近的低成本 completion 是盈利模块，不是闭合率模块。

## 发现 4.1：Completion budget 是状态依赖的，不应写成单一常数

按 first price 分层，达到 xuan observed 30s hit rate 所需的 L2 pair-cost ceiling 明显不同：

| first_price bucket | n | observed 30s | min pair p50 | 需要的 ceiling |
|---|---:|---:|---:|---:|
| `<0.40` | `174` | `88.5%` | `0.9421` | `>1.02 或不可由 L2 解释` |
| `0.40-0.55` | `1223` | `84.2%` | `0.9400` | `~1.010` |
| `0.55-0.70` | `1788` | `79.5%` | `0.9400` | `~1.0075` |
| `>=0.70` | `1402` | `82.7%` | `0.9600` | `~1.0025` |

按 size 分层：

| size bucket | n | observed 30s | min pair p50 | 需要的 ceiling |
|---|---:|---:|---:|---:|
| `<=80` | `1794` | `88.0%` | `0.9400` | `~1.010` |
| `80-160` | `1724` | `79.5%` | `0.9500` | `~1.0075` |
| `>160` | `1069` | `76.3%` | `0.9597` | `~1.010` |

按 offset 分层：

| offset bucket | n | observed 30s | min pair p50 | 需要的 ceiling |
|---|---:|---:|---:|---:|
| `000-030s` | `821` | `83.9%` | `0.9513` | `~1.020` |
| `030-120s` | `1610` | `82.0%` | `0.9530` | `~1.0075` |
| `120-240s` | `1791` | `79.8%` | `0.9500` | `~1.0025` |
| `240-300s` | `365` | `89.3%` | `0.9106` | `>1.02 或不可由 L2 解释` |

解释：如果只写一个 `completion_ceiling=1.005`，会在某些状态下过早放弃，在另一些状态下过度亏损。更合理的实现是状态依赖 budget：

- 中段 `120-240s`、first price 高位：预算可以更紧，约 `1.0025-1.005`。
- 早段 `000-030s`、低 first price、小 size：需要更宽预算，约 `1.010-1.020`，否则复现不了 xuan 的闭合率。
- 尾段 `240-300s`：公开 L2 解释力不足，不能只靠 ceiling 复刻，必须研究拆单、settlement/merge、以及官方结算临近时的流动性变化。

## 发现 5：低成本模块确实存在，但频率不足以解释全部

我们构造的 L2 tail sniper 子策略在 5 天 replay 上：

| 模式 | candidates | closed | pair p50 | pair <0.90 | delay p50 |
|---|---:|---:|---:|---:|---:|
| tail sniper, 30s@0.90 -> 70s@0.95 | `399` | `82.46%` | `0.8986` | `71.43%` | `16.32s` |
| tail sniper, 50s@0.90 -> 70s@0.95 | `399` | `80.95%` | `0.8972` | `87.00%` | `15.84s` |

解释：这是一个可落地的低成本子策略，但每天候选频率远低于 xuan 的交易频率。xuan 的主系统应当是“高频近 parity 快速闭合 + 低频低成本 alpha 模块 + residual/merge 管理”。

## 发现 6：快闭合控风险，慢闭合贡献主要利润

按 xuan observed pair delay 分解 5 天 tranche：

| delay bucket | n | 占比 | pair avg | pair p50 | pair <0.90 | size sum | surplus sum |
|---|---:|---:|---:|---:|---:|---:|---:|
| `<=30s` | `3765` | `82.08%` | `0.992704` | `1.003527` | `10.57%` | `416505.62` | `$2012.44` |
| `31-60s` | `572` | `12.47%` | `0.924786` | `0.925931` | `40.73%` | `76051.71` | `$5147.65` |
| `>60s` | `250` | `5.45%` | `0.899269` | `0.868915` | `56.00%` | `33371.63` | `$3472.08` |

解释：xuan 的 `30s` 神奇配对不是主要利润来源，而是库存风险控制层。利润主要来自少数慢闭合 tranche，它们等待到了明显更低的 opposite price。这对实现的意义很直接：

- 不应把所有 tranche 都强制追求低 pair cost，否则会牺牲闭合率。
- 也不应把所有 tranche 都快速 `1.01` 补掉，否则会牺牲主要利润来源。
- 正确结构是：多数 tranche 用 near-parity completion 快速消灭单边风险；少数被 gate 识别为“可等”的 tranche 才进入 slow-profit path。

## 发现 7：slow-profit path 的第一版可执行裁决信号

把 `delay > 30s` 的 tranche 分成：

- `slow_profit_lt95`: `delay > 30s` 且最终 `pair_cost < 0.95`
- `slow_bad_ge95`: `delay > 30s` 且最终 `pair_cost >= 0.95`

5 天样本：

| label | n | surplus |
|---|---:|---:|
| `fast_control` | `3765` | `$2012.44` |
| `slow_profit_lt95` | `495` | `$11618.14` |
| `slow_bad_ge95` | `327` | `-$2998.40` |

最强的 30s 后续裁决信号是：前 30 秒是否已经出现过 cheap completion window。

| 规则 | eligible n | eligible slow-profit/slow | eligible surplus | blocked n | blocked slow-profit/slow | blocked surplus |
|---|---:|---:|---:|---:|---:|---:|
| `min_pair_cost_30s <= 0.90` | `287` | `88.85%` | `$6454.76` | `535` | `44.86%` | `$2164.97` |
| `min_pair_cost_30s <= 0.95` | `465` | `79.35%` | `$8340.53` | `357` | `35.29%` | `$279.20` |
| `min_pair_cost_30s <= 0.99` | `652` | `67.33%` | `$9016.55` | `170` | `32.94%` | `-$396.82` |
| `min_pair_cost_30s <= 1.00` | `706` | `64.87%` | `$9289.18` | `116` | `31.90%` | `-$669.45` |

解释：`min_pair_cost_30s <= 0.95` 是一个很强的 slow-path allow 信号。它捕获了大部分 slow-profit surplus，同时把大量 slow_bad 排除在外。直观含义是：

- 如果前 30 秒内已经出现过便宜补腿窗口但没有成交，说明盘口状态具备 mean-reversion/cheap-completion 特征，可以继续给它时间。
- 如果前 30 秒一直没有出现便宜窗口，继续等的期望明显变差，应转入 near-parity repair。

这也解释了 xuan 为什么不是每轮结束后立刻再开：他可能在等待“cheap-window evidence”，而不是只看固定 pair target。

## 第一版控制器假设

基于当前证据，`xuan-like` completion controller 应分为三层：

1. `Profit Fill`: first leg 后挂/等 `pair_cost <= 0.95`，这是主要利润来源。
2. `Fast Repair`: 30 秒内若 near-parity L2 可补，则允许 `pair_cost ~= 1.005-1.010` 补腿，核心目标是消灭单边风险。
3. `Slow-Profit Continuation`: 如果 30 秒内出现过 `min_pair_cost <= 0.95` 的 cheap-window evidence，但仍未成交，可以继续等待；否则不应无条件慢等。

这比“固定 pair target”更接近 xuan：pair target 是动态结果，真正的状态变量是 cheap-window evidence、剩余风险、surplus bank 和时间。

## 反证：不能让 profit target 长时间阻塞 fast repair

我们测试了一个直觉策略：先等 profit fill，再放宽到 near-parity repair。

| staged schedule | close rate | pair p50 | delay p50 |
|---|---:|---:|---:|
| `15s@0.95 -> 30s@1.005` | `68.69%` | `0.9500` | `14.46s` |
| `15s@0.95 -> 30s@1.010` | `71.22%` | `0.9585` | `15.00s` |
| xuan observed | `82.08%` | `0.9946` | `10.00s` |

结论：如果先强行等待 profit fill，闭合率会明显低于 xuan。这说明 xuan 的 fast repair 通道不能被 profit target 阻塞太久。更合理的结构是：

- 普通 tranche：near-parity repair 从很早就可用。
- 进入 slow-profit path 的 tranche：必须由更强 gate 筛选，而不是所有 tranche 都先等 profit。
- Profit fill 可能是并行被动订单或状态选择结果，而不是串行地“先等低价，再修复”。

进一步校准 profit-first 的最长窗口：

| staged schedule | close rate | pair avg | pair p50 | surplus |
|---|---:|---:|---:|---:|
| `2s@0.95 -> 30s@1.005` | `78.72%` | `0.989690` | `1.0000` | `$3491.46` |
| `2s@0.95 -> 30s@1.010` | `82.76%` | `0.994037` | `1.0041` | `$1925.96` |
| pure `30s@1.005` | `80.16%` | `1.000274` | `1.0030` | `-$411.21` |
| pure `30s@1.010` | `85.76%` | `1.007172` | `1.0096` | `-$3119.74` |
| xuan observed | `82.08%` | `0.979143` | `0.9946` | `$10632.17` |

解释：

- `2s@0.95 -> 30s@1.010` 基本复现 30s 闭合率，但利润远低于 xuan。
- `2s@0.95 -> 30s@1.005` 保留更多利润，但闭合率低约 3.4pct。
- 纯 near-parity repair 会亏钱，说明必须有 profit-first 或 slow-profit 资金池。
- xuan 的真实优势不只是 repair ceiling，而是能把少数 slow-profit tranche 的 surplus 保留下来。

第一版 shadow 参数应至少并行两套：

- `risk_control_shadow`: `2s@0.95 -> 30s@1.010`
- `balanced_shadow`: `2s@0.95 -> 30s@1.005`

两者都不能视为完整复刻；它们只复刻了“30s 配对控制层”，还缺 slow-profit path。

## 对策略实现的直接含义

1. Completion controller 应该和 alpha open gate 分离。

低成本 open gate 不能承担 30s 闭合率。闭合率需要一个独立的 completion budget，默认应围绕 `1.005-1.010` 做 shadow，而不是 `0.90-0.95`。

2. 快速补腿不是失败，而是库存保险。

如果 first leg 已经成交，继续等 `0.90` 补腿会显著降低闭合率。xuan 更像是在 30 秒内用接近 parity 的价格消灭单边风险，再靠其他 tranche 或尾段机会赚回来。

3. Tail sniper 可以作为盈利子模块，但不能作为主引擎。

尾段小 clip 的 L2 机会质量高，但频率低。它适合作为 surplus generator，不适合作为全策略骨架。

4. 回测必须使用 exact public trade timestamp。

所有基于 xuan public truth 的 L2 对齐都应优先使用 public market trade exact match timestamp。直接用 Data API timestamp 会产生系统性后视偏差。

5. 下一步最该研究的是“如何低成本地补回 completion budget”。

现在最重要的问题不是“是否 30s 可配对”，而是：当 completion ceiling 放宽到 `1.005-1.010` 后，系统靠哪些低成本 tranche、tail sniper、merge/redeem 节律把平均利润拉回正。

## 当前未解问题

- xuan 剩余约 `0.8-1.0c` 的价格改善来自真实执行优势，还是 replay L2 深度不完整？
- xuan 是否把一笔 public trade 拆成多个内部决策 tranche，导致我们按同 size sweep 高估成本？
- xuan 的 `1.005-1.010` completion budget 是否动态依赖 surplus bank、market time、first price、clip size？
- merge/redeem cashflow 是否足以覆盖 near-parity completion 的小额亏损？

## 下一步

- 生成按 `first_price / size / offset / hour / observed pair_cost` 分组的 completion ceiling 曲线。
- 把 `completion budget` 做成 shadow gate：`surplus_bank` 允许时放宽到 `1.010`，否则限制在 `1.000-1.005`。
- 把 `completion budget` 改成状态依赖：`first_price / size / offset / hour / surplus_bank` 共同决定，而不是单一全局常数。
- 用 tail sniper 低成本模块估算 surplus generator 频率与容量。
- 将 xuan observed cycle PnL 与 counterfactual completion cost 连接，评估 near-parity completion 是否可被 surplus bank 覆盖。
- 建立 slow-profit path 分类器：判断哪些 active tranche 可以超过 30s 继续等，而哪些必须 near-parity completion。
- 将 `min_pair_cost_30s <= 0.95` 作为第一版 slow-path shadow allow gate，并评估它在我方 replay 策略上的收益/回撤影响。
