# Xuan-Proxy Completion-First Strategy V1

## 结论

现有数据已经足够设计一套可回测、可 shadow 的策略，但还不够直接 enforce 或实盘放量。

足够的部分：

- `xuan_trades/xuan_activity + market L1/L2 + settlement` 已覆盖 `2026-04-27T07:25:00Z` 到 `2026-05-01`。
- 已能重建 `4587` 个 xuan-like tranche。
- 已能解释 `30s completion`、`slow-profit path`、`winner-path`、`L2 execution edge` 的主要结构。
- 已经有可实盘观测的 proxy：`first_l2_edge`、`round_offset`、`clip_size`、`first_price`、`recent flow`、`min_pair_cost_30s`。

不足的部分：

- 仍拿不到 xuan 的真实挂单时间、取消时间、队列位置。
- `first_l2_edge > 3c` 仍可能混有 timestamp / public trade match / depth 粒度误差，必须先 shadow。
- 现有数据没有 own execution truth，不能证明我们的成交概率和 xuan 一样。
- 当前样本只覆盖 5 天，足够设计第一版，不足以做强 enforce 声明。

因此策略定位固定为：

```text
research-derived shadow strategy
-> market-side replay validation
-> own dry-run truth validation
-> small-size enforce discussion
```

## 核心策略假设

xuan 的优势不是固定 pair target，也不是纯 maker pair arb。更合理的结构是：

```text
Open-time state selection
  + execution edge / winner proxy
  + strict inventory gating
  -> first leg
  -> fast near-parity completion for risk control
  -> cheap-window evidence controls slow-profit continuation
  -> no cheap-window means repair, not slow wait
```

对应数据证据：

| 模块 | 证据 |
|---|---|
| first leg winner proxy | `first_winner_rate = 65.69%` |
| fast risk control | `<=30s` tranche 占 `82.08%`，pair p50 `1.003527` |
| slow-profit alpha | `slow_profit_lt95` 只占 `10.79%`，但贡献 `$11618.14` surplus |
| cheap-window evidence | `min_pair_cost_30s <= 0.90` 的 surplus/size `0.095495` |
| no cheap-window bad | `min_pair_cost_30s > 0.99 or missing` 的 surplus/size `-0.030027` |
| open-time L2 edge | `first_l2_edge > 3c` 的 surplus/size `0.045587` |

## V1 状态机

```text
Idle
  -> OpenCandidate
  -> FirstLegWorking
  -> CompletionFast
  -> CompletionSlowAllowed | RepairForced
  -> PairCovered
  -> Merge/Redeem/ResidualAccounting
  -> Cooldown
  -> Idle
```

### Idle

只允许在以下条件满足时寻找 first leg：

- 无 active tranche。
- global residual 在 eps 内，或 residual 已被明确标记为 repair-only。
- 非停盘/低质量数据窗口。
- `book_age_ms <= 500`。
- `tail_freeze=false`。

### OpenCandidate

生成候选 first leg，但不立即执行。候选必须输出 explain：

- `intended_first_side`
- `intended_first_price`
- `clip_size`
- `round_offset_s`
- `first_l2_vwap`
- `first_l2_edge = first_l2_vwap - intended_first_price`
- `first_l1_spread_ticks`
- `first_l1_ask_depth`
- `recent_same_minus_opp_buy_size_15s`
- `open_gate_decision`
- `open_gate_reason`

### FirstLegWorking

V1 只允许单 first leg，不做 two-sided seed。第一腿成交后立即进入 `CompletionFast`，禁止继续同侧加仓。

### CompletionFast

目标是控制单边风险，而不是最大化 pair discount。

默认 completion ceiling：

```text
2s: pair_cost <= 0.95
30s: pair_cost <= 1.005 / 1.010 by mode
```

两个 shadow mode：

- `balanced_shadow`: `2s@0.95 -> 30s@1.005`
- `risk_control_shadow`: `2s@0.95 -> 30s@1.010`

当前证据：

| mode | close rate | pair p50 | surplus |
|---|---:|---:|---:|
| `balanced_shadow` | `78.72%` | `1.0000` | `$3491.46` |
| `risk_control_shadow` | `82.76%` | `1.004139` | `$1925.96` |
| xuan observed | `82.08%` | `0.994604` | `$10632.17` |

解释：`risk_control_shadow` 能接近 xuan 的 30s completion，但利润远低于 xuan；差额来自 open selection、execution edge、slow-profit path。

### CompletionSlowAllowed

只有出现 cheap-window evidence 才能继续慢等。

默认规则：

| first 30s evidence | action |
|---|---|
| `min_pair_cost_30s <= 0.90` | allow slow path |
| `0.90 < min_pair_cost_30s <= 0.95` | allow slow path but clip/budget conservative |
| `0.95 < min_pair_cost_30s <= 0.99` | prefer repair |
| `0.99 < min_pair_cost_30s <= 1.01` | force repair |
| `min_pair_cost_30s > 1.01 or missing` | hard force repair |

### RepairForced

未覆盖 tranche 如果没有 cheap-window evidence，不允许继续用“再等等”来赌 mean reversion。

修复目标：

- 尽快转成 pair-covered。
- 可使用 near-parity completion。
- 不允许同侧 risk-increasing add。
- 不允许为了改善均价而扩大单边风险。

### PairCovered

`PairCovered != Merged`。配对覆盖只说明单边风险已解除，不代表资金已释放。

V1 必须继续跟踪：

- `pairable_qty`
- `mergeable_full_sets`
- `locked_in_pair_covered`
- `surplus_bank`
- `repair_budget_spent`

### Cooldown

不得在上一轮刚 pair-covered 后立即重新开仓。重新开仓必须重新通过 open gate。

初版建议：

- 默认 cooldown `5s-15s`，shadow sweep。
- 如果最近一轮是 `RepairForced`，cooldown 加倍。
- 如果最近一轮是 `slow_profit_lt95`，不自动 upclip，仍重新评估 open gate。

## Open Gate V1

### P0 Hard Blocks

这些可以直接作为 shadow 默认 block，未来最有机会进入 enforce：

| rule | reason |
|---|---|
| `book_age_ms > 500` | 数据陈旧 |
| `first_l1_spread_ticks > 3` 且无强 L2 edge | fillability 差 |
| `first_l2_edge <= -0.01` | 负执行 edge，pair p50 `1.026723` |
| `round_offset < 30s AND first_l2_edge <= -0.01` | 早段负 edge 更差，surplus/tranche `-$3.54` |
| active tranche exists | 防止滑回 same-side averaging |

### Positive Priority Signals

这些不应单独决定开仓，但可用于优先级和 clip multiplier：

| signal | evidence | action |
|---|---|---|
| `first_l2_edge > 0.03` | surplus/size `0.045587` | allow / upclip candidate |
| `0.55 <= first_price < 0.70 AND first_l2_edge > 0.03` | first_winner `68.24%` | allow |
| `round_offset < 30s AND first_l2_edge > 0.03` | surplus/size `0.055937` | allow with repair budget |
| `0.50 <= first_price < 0.55 AND size > 160` | surplus/size `0.039042` | allow but not winner-based upclip |

### Clip Multiplier

V1 不追求复杂 sizing，只做可审计分层：

| condition | clip_mult |
|---|---:|
| hard block | `0` |
| no positive signal, no block | `0.5` |
| `first_l2_edge > 0.03` | `1.0` |
| `first_l2_edge > 0.03` 且 `min spread/depth checks pass` | `1.25` shadow only |
| after repair failure in previous tranche | cap at `0.5` |

## Completion Controller V1

V1 应同时跑两套 shadow：

### Balanced

```json
[
  {"deadline_s": 2, "pair_cost_ceiling": 0.95},
  {"deadline_s": 30, "pair_cost_ceiling": 1.005}
]
```

优点：保留更多利润。缺点：30s close rate 低于 xuan。

### Risk Control

```json
[
  {"deadline_s": 2, "pair_cost_ceiling": 0.95},
  {"deadline_s": 30, "pair_cost_ceiling": 1.01}
]
```

优点：30s close rate 接近 xuan。缺点：如果没有 open edge 和 slow path，会明显少赚。

### 状态依赖修正

后续可以从 shadow 中加入状态依赖 ceiling：

| state | suggested ceiling |
|---|---:|
| `0.40 <= first_price < 0.55` | `1.010` |
| `0.55 <= first_price < 0.70` | `1.0075` |
| `first_price >= 0.70` | `1.0025-1.005` |
| `120s <= offset < 240s` | `1.0025-1.005` |
| `offset < 30s` | `1.010-1.020` shadow only |

## Live-Enforce 不足项

要进入实盘前，至少还缺三类证据：

1. Own execution truth

需要证明我们自己的 maker/taker/fill model 能真实成交，而不只是 replay L1/L2 可见。

2. `first_l2_edge > 3c` 真因

需要区分：

- xuan 真实排队 maker 优势
- Data API timestamp 滞后
- public trade match 不完整
- L2 depth 粒度不够
- 我方也能复制的 taker/maker 执行优势

3. Out-of-sample 稳定性

至少需要用 `2026-05-02+` 新 replay 验证：

- `open_block_negative_l2_edge` 仍有效。
- `min_pair_cost_30s <= 0.90/0.95` 仍是 slow-path 强信号。
- `first_l2_edge > 3c` 不是某一天或某段行情特有。

## 回测验收

第一版策略回测不应直接追求 PnL 最大化，应先验证结构相似性：

| metric | target |
|---|---:|
| active tranche overlap | `0` |
| same-side add before covered | `0` |
| 30s completion rate | `>= 78%` shadow |
| clean close rate | `>= 90%` shadow |
| slow wait allowed rate | `20%-50%` |
| no-cheap-window slow wait rate | `<5%` |
| open block negative edge selected_rate | about `4%-8%` |
| residual before new open p90 | near `0` |

真正接近 xuan 的目标：

| metric | xuan 5d |
|---|---:|
| first_winner_rate | `65.69%` |
| 30s completion rate | `82.08%` |
| pair_cost p50 | `0.994604` |
| pair_delay p50 | `10s` |
| surplus/size | `0.020216` |

## V1 实施顺序

1. 在 market-side replay 中实现 open-time explain，不做真实成交假设。
2. 接入 `open_block_negative_l2_edge`、`first_l2_edge > 3c`、`low_price_without_edge` 三个 shadow gate。
3. 接入 `balanced_shadow` 与 `risk_control_shadow` completion controller。
4. 接入 `min_pair_cost_30s` slow continuation gate。
5. 输出 xuan gap report：
   - open_allowed_count
   - open_blocked_count
   - 30s completion
   - pair cost distribution
   - slow continuation quality
   - no-cheap-window repair rate
6. 只有 shadow 优于 baseline 且 out-of-sample 稳定，才讨论 live enforce。

## 当前建议

现在可以开始设计和实现策略，但只能作为 shadow/backtest。

最小可行版本：

```text
Open:
  block stale book
  block active tranche
  block first_l2_edge <= -1c
  clip-down low first_price without L2 edge
  priority/upclip first_l2_edge > 3c

Completion:
  run balanced and risk_control dual shadow
  no same-side add before covered

After 30s:
  if min_pair_cost_30s <= 0.95: allow slow continuation
  else: force repair
```

这不是完整复刻 xuan，但已经是可执行、可证伪、与当前数据最一致的第一版策略。
