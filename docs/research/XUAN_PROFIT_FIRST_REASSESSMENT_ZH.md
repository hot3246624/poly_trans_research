# Xuan Profit-First Reassessment

## 结论

之前把研究重心压到“30s 内完成配对”是方向偏差。`30s completion` 是 xuan 的风险控制层，不是主要利润层。当前最高优先级真值应改为 **market-level trade PnL / weighted pair cost**：

```text
逐市场累计 BUY Up/Down
-> 用 settlement/current value 重估
-> 主要收益来自 weighted pair cost < 1
-> residual PnL 只是小项
```

在这个口径下，xuan 不是“疑似正收益”，而是稳定正收益。我们已经用本地 replay 复现了同事的逐市场 PnL 口径：`2026-04-27T07:25:00Z` 到本地 `2026-05-01` 数据内，xuan trade PnL 为 `+$14,419.08`，ROI `+2.09%`，weighted pair cost `0.980324`。

因此后续研究北极星必须从“高 30s completion rate”改成：

```text
持续把 Up+Down 的市场级加权配对成本买到 < 1，
并且在多日维度保持正 trade PnL。
```

`30s completion` 仍然重要，但它是实现这个目标的库存风险控制工具，不是最终 alpha。

当前 replay truth 更支持下面的结构：

```text
winner-biased first leg
-> fast near-parity completion 控制库存风险
-> cheap-window evidence 后允许 slow-profit continuation
-> MERGE / REDEEM / winner residual 做资本回收和尾部修复
```

因此，任何只复刻 `30s completion`、只追求高闭合率、或用粗暴 repair/exit 清零残仓的策略，都可能看起来安全但实际负 PnL。我们的候选策略失败，正是因为它没有复现 xuan 的 market-level weighted pair-cost edge。

## 已验证事实

数据范围：`2026-04-27T07:25:00Z` 至 `2026-05-01`，只读 replay SQLite，不读 raw。

### Xuan tranche truth

| 指标 | 数值 |
|---|---:|
| xuan BUY trades | `12156` |
| paired tranches | `4587` |
| first-leg winner rate | `65.69%` |
| observed 30s completion rate | `82.08%` |
| observed pair cost p50 | `0.994604` |
| observed tranche surplus | `$10632.17` |

### Xuan market-level PnL truth

本口径使用 `xuan_trades` + `settlement_records`，不使用 `xuan_activity` 的 MERGE/REDEEM cashflow。原因是 activity poll 可能出现 MERGE/REDEEM 但缺对应完整买入历史，容易把生命周期事件误当成利润。

| 指标 | 本地 2026-04-27 至 2026-05-01 |
|---|---:|
| markets | `947` |
| paired markets | `947` |
| profitable / losing paired markets | `686 / 260` |
| BUY / SELL | `12156 / 2` |
| total cost | `$689150.965151` |
| total value | `$703570.04534` |
| trade PnL | `+$14419.080189` |
| ROI on cost | `+2.0923%` |
| weighted pair cost | `0.980324` |
| paired profit | `+$13761.043101` |
| residual PnL | `+$658.037085` |

同事的扩展窗口 `2026-04-30 15:30 BJT` 到约 `2026-05-03 09:47 BJT` 给出：

| 指标 | 数值 |
|---|---:|
| 覆盖 BTC 5m 市场 | `797` |
| xuan active markets | `743` |
| xuan 成交 | `7730` |
| 买入成本 | `$467369.57` |
| 当前/结算价值 | `$476399.04` |
| trade PnL | `+$9029.47` |
| ROI | `+1.93%` |
| weighted pair cost | `0.981017` |
| paired profit | `+$9002.24` |

这个扩展窗口和本地复现窗口在重叠部分完全对齐：

| BJT 日期 | 本地复现 PnL |
|---|---:|
| 4月30日 15:30 后 | `+$1633.2398` |
| 5月1日 | `+$3320.1868` |
| 5月2日早段，本地数据截断 | `+$907.5069` |

结论：xuan 的主收益不是外部充值、前端 PnL 口径或 residual lottery，而是稳定把双边 BUY 的加权配对成本压到 `0.98` 左右。

### 按路径拆解

| path | n | first winner | pair p50 | delay p50 | surplus |
|---|---:|---:|---:|---:|---:|
| `fast_control` | `3765` | `64.04%` | `1.003527` | `8s` | `$2012.44` |
| `slow_profit_lt95` | `495` | `80.20%` | `0.841998` | `50s` | `$11618.14` |
| `slow_bad_ge95` | `327` | `62.69%` | `1.023455` | `44s` | `-$2998.41` |

关键解释：

- `fast_control` 量最大，但单位利润很薄，甚至很多短延迟配对是负 edge 的风险控制。
- `slow_profit_lt95` 才是利润核心，只占 `10.79%` tranche，却贡献远超总利润的正 surplus。
- `slow_bad_ge95` 是必须压制的危险尾部。
- tranche-level surplus 是解释机制，不再作为最终 PnL 口径；最终裁决以 market-level trade PnL 为准。

### 30s cheap-window 是后验分流信号

| first 30s min pair cost | n | slow profit / slow | surplus / tranche | 裁决 |
|---|---:|---:|---:|---|
| `<=0.90` | `1314` | `88.85%` | `$10.08` | 强允许 slow path |
| `0.90-0.95` | `917` | `64.04%` | `$3.47` | 谨慎允许 |
| `0.95-1.00` | `1345` | `36.93%` | `-$0.99` | 倾向 repair |
| `1.00-1.01` | `358` | `22.22%` | `-$3.55` | force repair |
| `>1.01` | `417` | `19.15%` | `-$8.53` | hard repair / no wait |

这解释了 xuan 为什么不像固定 pair target：他不是开仓时预设一个永久目标，而是开仓后观察 early cheap-window evidence，再决定是否进入 slow-profit 路径。

## 对我们当前失败回测的解释

当前 `completion-first proxy` 候选不赚钱，不代表 xuan 不可学，而是说明我们复刻错了层级：

- 我们把 `30s completion` 当成核心 alpha，但它主要是风控层。
- 我们用 `repair_ceiling=1.04` 或快速卖出清残，实际把 slow-profit 的正期望截断。
- 我们的 open proxy 只解决“是否可能快速配对”，没有解决“first leg 是否具备 winner bias / trend continuation edge”。
- 我们接受了太多 `no cheap window` 的 first fill，这批在 xuan truth 里是负期望。
- 我们没有用 market-level weighted pair cost 作为目标函数，因此闭合率提高不等于赚钱。

所以后续验收必须改成：

```text
net PnL after residual / exit / settlement > 0
```

闭合收益、30s 成功率、pair-cost p50 都只能做诊断，不能做通过条件。

## 新策略设计方向

### 1. Open Gate

开仓必须服务两个目标：

- 提高 first-leg winner probability，降低 residual 尾部风险。
- 获得 execution discount，让市场级 weighted pair cost 长期低于 `1`。
- 保持足够机会频率；xuan 近窗口 active rate 很高，过窄 gate 会错过主收益。

可用 shadow 信号：

- `first_l2_vwap - intended_first_price > 3c`：强正向，但样本少，先做 upclip / priority。
- `first_l2_vwap - intended_first_price <= -1c`：负向，适合 hard block 或 clip-down。
- `first_price >= 0.90`：winner probability 高，但 surplus 很弱，不应盲目 upclip。
- `0.50-0.70 + positive L2 edge`：更接近可赚钱主区间。

### 2. Completion Controller

第一版可测试语义：

- `0-15s`: 优先接受 cheap completion，但不应为了闭合率盲目接受长期负 edge。
- `15-30s`: 允许 near-parity completion，例如 `pair_cost <= 1.005/1.01`，但必须以 market-level expected pair cost 为约束。
- `30s checkpoint`: 根据 first 30s min pair cost 分流。
- `min_pair_30s <= 0.90`: 允许 slow-profit continuation。
- `0.90 < min_pair_30s <= 0.95`: 小 clip / 有预算才允许继续。
- `min_pair_30s > 0.99`: 不慢等，转 repair / exit。

新的核心指标不是“这一笔是否 30s close”，而是：

```text
market_weighted_pair_cost <= target
daily_trade_pnl > 0
capital_lock / residual risk within budget
```

### 3. Residual Policy

残仓不能一律卖出，也不能一律持有：

- 强 winner proxy 残仓可以允许小规模持有到 settlement/redeem。
- 弱 winner proxy 或 no-cheap-window 残仓必须快速修复。
- 残仓决策必须用 `expected settlement value - exit loss - capital lock` 比较，而不是固定时间规则。

### 4. Sizing

先不要追求 xuan 的 `100-160` 常规 size。我们的执行真值未验证前，size 应由 edge 和风险预算决定：

- baseline clip 保守。
- `positive L2 edge + cheap-window evidence` 才允许 upclip。
- `no cheap-window` 只能 shrink 或 repair，不能越亏越加。

## 下一步

1. 以后统一使用 `scripts/analyze_xuan_market_pnl_truth.py` 的 market-level PnL 口径作为 xuan target。
2. 将 backtest 从 tranche-close PnL 改成 market-level inventory ledger：累计 YES/NO BUY，按 settlement/current value 计 PnL。
3. 把目标函数改成 `weighted_pair_cost < 0.985`、`daily PnL > 0`、`residual PnL 不主导收益`。
4. 把 controller 改成 `conditional slow-profit path`，而不是固定 30s 后 repair。
5. 用 xuan truth 先验证每个 gate 是否提升 market-level pair cost，再应用到我方 market-side proxy。
6. 若仍不能正 PnL，说明缺口主要在 maker queue / fillability / entry timing，而不是 pair controller。

## 硬门槛

任何候选策略必须同时满足：

- market-level trade PnL > 0。
- weighted pair cost < 1，目标先定为 `<0.985`，最终逼近 xuan 的 `0.980-0.981`。
- 多日稳定，不是单日拟合。
- `no cheap-window` cohort 不得贡献主要亏损。
- daily drawdown 和 capital lock 可接受。
- 不能只用 xuan 的事后 winner_side 做 live 决策。
