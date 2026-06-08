# Xuan Surpass Iteration 2026-05-04: Existing Replay Only

## Scope

- 数据源只使用 `data/replay/2026-04-27` 到 `data/replay/2026-05-01`。
- 不使用 `data/raw`。
- 不使用本地临时 `2026-05-03/2026-05-04` replay 作为研究依据。
- 本轮目标不是继续复述 xuan，而是把可执行候选收敛成 shadow 默认和下一批验证门槛。

## Reference

xuan public truth 仍是当前收益目标：

- `data/exports/xuan_market_pnl_truth_0427_0501/xuan_market_pnl_truth_summary.json`
- 市场数：`947`
- 成交数：`12158`
- 成本：`$689150.97`
- 交易收益：`$14419.08`
- ROI：`2.09%`
- 加权 pair cost：`0.980324`

关键结构：

- `first-leg winner rate = 65.69%`
- `slow_profit_lt95` 路径 pair p50 约 `0.842`
- winner-first tranche 贡献绝大部分正 surplus

## What Failed

### 1. Simple all-market upcross is not xuan

脚本：

```bash
uv run python scripts/analyze_btc5m_upcross_predictor.py \
  --days 2026-05-01 \
  --output-dir data/exports/btc5m_upcross_predictor_0501_now
```

结果：

- 全市场样本：`145687`
- baseline `bid_jump_1s_ge_3c_rate = 6.81%`
- `bid_40_55_spread_le1` 的 jump rate 只有 `6.33%`
- `offset_lt60_bid_40_55_spread_le1` 只有 `4.56%`
- 只有 `prev_bid_delta>=2c + bid_40_55 + spread<=1` 提升到 `13.49%`

结论：xuan 的 edge 不是“任意 40-55 中价状态 + 短动量”。xuan 的样本锚点明显落在更接近真实成交/队列/重定价前沿的状态。

### 2. Pure L1 taker追涨不够

脚本：

```bash
uv run python scripts/backtest_btc5m_upcross_l1_taker.py \
  --days 2026-05-01 \
  --output-dir data/exports/btc5m_upcross_l1_taker_0501_now \
  --first-price-source ask \
  --min-offset-s 0 --max-offset-s 240 \
  --min-side-bid 0.40 --max-side-bid 0.55 \
  --max-spread-ticks 1 \
  --min-prev-bid-delta-1s 0.02 \
  --completion-pair-ceiling 0.95 \
  --completion-deadline-s 30 \
  --repair-pair-ceiling 1.04 \
  --repair-deadline-s 60 \
  --clip 60
```

结果：

- trades：`550`
- completed：`436`
- residual：`114`
- first_winner_rate：`47.09%`
- PnL：`-$14.40`
- ROI：`-0.09%`

结论：不具备 maker/queue/timing edge 时，L1 taker 追涨基本没有优势，不应作为主线。

## Current Best Executable Candidate

### Core Profile v1: `40-60s delta>=6c`

状态：已被 `core_v1_1` 替代为当前 shadow 默认核心，仍保留为更窄窗口对照。

参数：

- `40s <= round_offset < 60s`
- `prev_bid_delta_1s >= 0.06`
- `0.40 <= side_bid < 0.55`
- `spread_ticks <= 2`
- `opp_spread_ticks <= 2`
- `top_bid_sz <= 100`
- `immediate_pair_cost <= 1.00`
- first leg maker at current best bid
- first fill timeout `15s`
- base clip `60`

验证脚本：

```bash
uv run python scripts/validate_fastcancel_l2_candidates.py \
  --rows data/exports/backtest_btc5m_maker_fill_triggered_0427_0501_wide_emitall_delta2_bid35_55_top400_slow99/btc5m_maker_fill_triggered_rows.csv \
  --search-summary data/exports/fastcancel_param_search_0427_0501_wide_conservative/fastcancel_param_search_summary.json \
  --output-dir data/exports/fastcancel_l2_validate_core40_60_delta6_now \
  --top-n 20 \
  --slippage 0,0.005,0.01,0.02,0.03,0.04,0.05 \
  --residual-exit-policy price_lt_050_180_else_default \
  --non-clean-exit-delay-s 120
```

结果：

- attempts：`105`
- proxy fills：`31`
- raw PnL：`$171.00`
- L2 PnL：`$146.67`
- L2 +2c：`$109.47`
- L2 +5c：`$53.67`
- positive days：`5/5`
- weakest L2 +5c day：`$4.85`
- completion rate among fills：`93.55%`
- residual rate：`6.45%`
- pair_cost p50：`0.94`

裁决：这是当前最适合作为 shadow 默认的 core profile。它不够大，但最干净。

### Core replay pipeline result

core profile 已经接入 replay-to-shadow pipeline：

```bash
uv run python scripts/run_fastcancel_shadow_replay_pipeline.py \
  --config configs/xuan/fastcancel_shadow_core_v1.json \
  --tag core_v1_full \
  --rebuild-rows \
  --skip-existing \
  --require-replay-ready
```

输出：

- `data/exports/fastcancel_shadow_replay_0427_0501_core_v1_full/`
- episodes：`105`
- proxy fills：`31`
- proxy fill rate：`29.52%`
- raw replay PnL：`$171.00`
- shadow L2 PnL：`$146.67`
- shadow L2 +2c PnL：`$109.47`
- positive days after +2c：`5/5`
- min day after +2c：`$13.85`
- `replay_ready_for_live_shadow = true`
- `own_execution_truth_ready = false`
- `promote_to_enforce_discussion = false`

裁决：core profile 已经可以作为 live shadow 观察口径，但不能 enforce。它的频率低于 xuan，不可能单独超越 xuan；它的价值是作为“不会先炸”的执行质量校准内核。

### Core v1.1: `30-60s delta>=6c`

新增 targeted scan 证明，最佳稳定区域不是继续扩 early/late，而是把 core 从 `40-60s` 提前到 `30-60s`：

- 配置：`configs/xuan/fastcancel_shadow_core_v1_1.json`
- 参数：`30s <= round_offset < 60s`
- `prev_bid_delta_1s >= 0.06`
- `0.40 <= side_bid < 0.55`
- `spread_ticks <= 2`
- `opp_spread_ticks <= 2`
- `top_bid_sz <= 100`
- `immediate_pair_cost <= 1.00`
- clip 固定 `60`

验证脚本：

```bash
uv run python scripts/run_fastcancel_shadow_replay_pipeline.py \
  --config configs/xuan/fastcancel_shadow_core_v1_1.json \
  --tag core_v1_1_full \
  --rebuild-rows \
  --skip-existing \
  --require-replay-ready
```

结果：

- episodes：`151`
- proxy fills：`40`
- proxy fill rate：`26.49%`
- raw replay PnL：`$137.40`
- shadow L2 PnL：`$164.05`
- shadow L2 +2c PnL：`$116.05`
- positive days after +2c：`5/5`
- min day after +2c：`$15.60`
- residual：`4`
- completion / fill：`90.00%`
- `replay_ready_for_live_shadow = true`
- `own_execution_truth_ready = false`

裁决：`core_v1_1` 是当前更优的 shadow 默认核心。它比旧 core 频率更高、+2c 后总收益略高、最弱日也更高；但仍然不是 enforce 候选，因为没有我方真实 maker queue 成交真值。

### Why clip 60 should be default

同一 profile 用 `clip=160`：

- raw PnL：`$129.60`
- L2 PnL：`$126.37`
- L2 +2c：`$84.77`
- L2 +5c：`$22.37`
- L2 +2c / +5c positive days：`4/5`
- 2026-04-30 在滑点后转负

裁决：不应默认固定 160。必须先用 `clip=60` 验证真实 maker fill quality，再根据 live fill truth 做动态 upclip。

## Expansion Candidate

当前 v1 broader fast-cancel 仍有更高总收益：

- 配置：`configs/xuan/fastcancel_shadow_sidecar_v1.json`
- replay pipeline：`scripts/run_fastcancel_shadow_replay_pipeline.py --tag now_0427_0501 --require-replay-ready`
- events：`994`
- episodes：`237`
- proxy fills：`46`
- raw PnL：`$284.60`
- L2 PnL：`$259.20`
- L2 +2c：`$198.00`
- L2 +5c：`$106.20`
- positive days：`5/5`

裁决：v1 是收益扩展模式，不是第一条 enforce 候选。它需要更强的 own fill truth，因为收益对真实 queue priority 更敏感。

新增 attribution 后，扩展层内部结构如下：

| bucket | shadow L2 | shadow +2c |
|---|---:|---:|
| early window | `$67.20` | `$58.80` |
| late window | `$192.00` | `$139.20` |
| clip 60 | `$223.90` | `$172.30` |
| dynamic upclip 160 | `$35.30` | `$25.70` |

解释：

- 主要收益仍来自 late window，不是 early window。
- dynamic upclip 在这 5 天 replay 内是正贡献，但样本只有 `10` 个 episode，不能作为默认放大仓位依据。
- expansion 的关键风险不是 PnL 曲线，而是 public queue proxy 是否高估真实 maker fill priority。

## Core vs Expansion vs Xuan

对照脚本：

```bash
uv run python scripts/compare_fastcancel_shadow_profiles.py \
  --profile core=data/exports/fastcancel_shadow_replay_0427_0501_core_v1_full/shadow_events/fastcancel_shadow_report.json \
  --profile expansion=data/exports/fastcancel_shadow_replay_0427_0501_expansion_v1_reuse/shadow_events/fastcancel_shadow_report.json \
  --xuan-summary data/exports/xuan_market_pnl_truth_0427_0501/xuan_market_pnl_truth_summary.json \
  --output-json data/exports/fastcancel_shadow_profile_compare_0427_0501/summary.json \
  --output-md data/exports/fastcancel_shadow_profile_compare_0427_0501/report.md
```

核心对比：

| profile | episodes | proxy fills | fill rate | L2 +2c PnL | positive days | min attempts/day |
|---|---:|---:|---:|---:|---:|---:|
| core_v1 | 105 | 31 | 29.52% | `$109.47` | 5/5 | 12 |
| core_v1_1 | 151 | 40 | 26.49% | `$116.05` | 5/5 | 20 |
| expansion | 237 | 46 | 19.41% | `$198.00` | 5/5 | 30 |
| xuan truth | 947 markets | 12158 trades | N/A | `$14419.08 trade PnL` | N/A | N/A |

裁决：

- core 质量更高，频率不够。
- expansion 频率更好，但更依赖 queue proxy。
- 两者都不能和 xuan 绝对收益直接对齐，因为当前是 public queue proxy，不是我方真实挂单排队成交。
- 下一步不是重新采集，而是等待远程更长 replay 后做 out-of-sample；如果接 shadow，则只用共享 WS/现有 live 主程序旁路，不单独启动本地采集器。

## Implementation Implications

当前策略路线应收敛成双层：

1. `Core shadow`: 默认跑 `30-60s delta>=6c clip60`，目标是验证真实 maker fill quality 和 queue priority。
2. `Expansion shadow`: 继续记录 v1 broader fast-cancel，用于观察频率和收益扩展，但不先 enforce。

不能做的事：

- 不用 pure L1 taker 追涨作为主策略。
- 不因 xuan pair cost 低就直接放大 clip。
- 不用 `clip=160` 作为默认第一版。
- 不把 public SELL proxy 当 own execution truth。

## Online Public WS Shadow Findings

新增脚本：

```bash
uv run python scripts/run_fastcancel_public_ws_shadow.py \
  --config configs/xuan/fastcancel_shadow_sidecar_v1.json \
  --duration-sec 660 \
  --round-offsets 0,1,2 \
  --output-dir data/exports/fastcancel_public_ws_shadow/expansion_diag_20260504T1231Z
```

边界：

- 只连接 public market WS。
- 不使用 key。
- 不发送 REST。
- 不下单。
- 不写 raw。
- 不写 replay DB。
- 只写 compact events/report 到 `data/exports`。

关键发现：

- observer 必须逐 book update 评估，不能“每秒只评估一次”。Polymarket 5m 的关键盘口跳变经常发生在同一秒内，按秒限流会造成 false negative。
- 修复后，observer 能捕捉到窗口外 `max_prev_bid_delta_1s = 0.11/0.12/0.13` 的真实跳变。
- 12:35 UTC market 在 mid window 出现 `0.06` 动量，但动量侧 bid 只有 `0.36`，被 `min_side_bid=0.40` 正确过滤。
- 12:40 UTC market 在 early window 出现 `0.10` 动量，但触发时价格多已冲到 `0.63`，被 `max_side_bid=0.50` 正确过滤；少数回落到区间时又卡 spread。

裁决：

- 在线 observer 可用于 opportunity / gate 解释和 live shadow 旁路观测。
- 当前在线样本显示：core/expansion 的低频不是实现坏了，而是 gate 在主动跳过“动量已变贵”或“窗口内无信号”的状态。
- 仍不能用 public WS shadow 证明真实 queue priority；enforce 前必须有 own execution truth 或极小额 post-only 实盘真值。

## Gates Before Enforce Discussion

必须等真实 shadow / dry-run execution truth：

- `actual_first_fill_qty / proxy_fill_qty` p50 接近 `1.0`
- `extra_required_size_equivalent_p90 <= 60`
- core profile 至少 `>= 100` live candidates
- actual-filled cohort 在 L2 +2c 口径仍为正
- 任一自然日 actual shadow PnL 不显著转负

如果这些没有数据，就停在 replay/shadow，不进入实盘。

## Current Status

- 已修复 `scripts/validate_fastcancel_l2_candidates.py` 的 `residual_exit_policy` 调用问题。
- 已把 `scripts/run_fastcancel_shadow_replay_pipeline.py` / `scripts/analyze_dual_window_fastcancel_combo.py` 从“双窗口写死”升级成任意窗口配置兼容。
- 已新增 `configs/xuan/fastcancel_shadow_core_v1.json`，作为 core shadow 默认候选。
- 已新增 `configs/xuan/fastcancel_shadow_core_v1_1.json`，替代 v1 成为当前 core shadow 默认候选。
- 已新增 `scripts/compare_fastcancel_shadow_profiles.py`，用于对比 core / expansion / xuan 目标。
- 已新增 `scripts/run_fastcancel_public_ws_shadow.py`，用于不采集 raw、不下单的 online public WS shadow。
- 已修复 public WS observer 的逐秒限流假阴性，并新增 gate 失败统计 / near-miss metrics。
- 本轮没有新增/修改任何 replay 数据。
- 后续如果没有新的 replay 或 own execution truth，能推进的主要是：更多参数稳健性、shadow 接线准备、以及等待更长样本验证。
