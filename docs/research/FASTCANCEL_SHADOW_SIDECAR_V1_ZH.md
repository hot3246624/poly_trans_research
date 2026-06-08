# Fast-Cancel Shadow Sidecar V1

## 定位

本文件把当前 replay 中表现最好的 `fast-cancel` 候选收敛为 shadow 验证方案。它不是 xuanxuan008 的完整复刻，也不是可直接 enforce 的实盘策略。

当前裁决：

- replay 已足够支持进入 shadow。
- shadow 只能证明我方真实成交质量、补腿滑点、清残滑点是否接近 replay 假设。
- 未通过 shadow 前，不允许用该策略下实盘订单。

## 默认候选

### Open Gate

只做 `BTC 5m`，每个 market 最多一条 active tranche。

早段窗口：

```text
10s <= round_offset < 20s
prev_bid_delta_1s >= 0.04
0.40 <= side_bid < 0.50
spread_ticks <= 1
top_bid_sz <= 250
```

中段窗口：

```text
30s <= round_offset < 60s
prev_bid_delta_1s >= 0.05
0.40 <= side_bid < 0.55
spread_ticks <= 2
opp_spread_ticks <= 2
immediate_pair_cost <= 1.00
top_bid_sz <= 100
```

### First Leg

```text
side = 当前满足 gate 的 side
order_type = maker buy
order_price = current best bid
base_clip = 60
fill_timeout = 15s
unfilled -> cancel, block this market for 15s
```

强状态动态加仓：

```text
if prev_bid_delta_1s >= 0.14:
    effective_clip = 160
else:
    effective_clip = 60
```

该规则只能作为 shadow sizing。不得改成固定 `120/160`，固定放大会降低成交样本质量。

### Completion Controller

首腿成交后进入 completion-only：

```text
primary completion:
    deadline = 30s
    opposite pair_cost <= 0.95

slow path:
    if min_pair_cost_seen_30s <= 0.99:
        allow until 120s
        opposite pair_cost <= 0.98

repair:
    30s-60s
    opposite pair_cost <= 1.04
```

同侧加仓固定禁止。不得因为首腿未配对而继续买同侧。

### Residual Exit

清残默认规则：

```text
if first_price < 0.50:
    residual_exit_delay = 180s
else:
    residual_exit_delay = 120s
```

退出价格按同秒 L2 bid VWAP 做 shadow 估值。该规则是当前收益/稳定性最优项，但样本少、单边暴露更长，必须作为 shadow P0 验证点。

## Replay 证据

当前 leader 来自：

```text
data/exports/dual_window_fastcancel_combo_0427_0501_early_plus_mid30_60_delta5_dynamic_upclip160_delta014_l2_price_lt050_180_else120_slipstress
```

核心结果：

| metric | value |
|---|---:|
| attempts | `237` |
| fills | `46` |
| raw PnL | `+$284.60` |
| L2 PnL | `+$259.20` |
| positive days | `5/5` |
| weakest day | `+$22.80` |
| L2 +1c friction | `+$228.60` |
| L2 +2c friction | `+$198.00` |
| L2 +5c friction | `+$106.20` |

与 xuan 的关系：

- 这不是 xuan 主引擎。
- 它是从 xuan 研究中提炼出的独立 market-side edge。
- 规模远低于 xuan，但已满足 shadow 验证门槛。

## P0 风险

## 收益结构诊断

基于 replay selected rows 的 raw PnL 拆解：

| bucket | attempts | fills | raw PnL | note |
|---|---:|---:|---:|---|
| early | `68` | `7` | `+$91.20` | 机会少但贡献高；最接近 xuan 早段开仓时点 |
| late | `169` | `39` | `+$193.40` | 当前主频率来源 |
| completion | `28` | `28` | `+$140.20` | clean close 主利润 |
| slow_completion | `6` | `6` | `+$36.40` | slow99/pair98 有增量但样本少 |
| repair | `7` | `7` | `+$1.80` | repair 几乎不创造利润，只是风险缓冲 |
| residual_settle | `5` | `5` | `+$106.20` | raw 贡献高，必须用真实 exit VWAP 重新验证 |
| dynamic upclip `160` | `10` | `3` | `+$36.80` | 强状态加仓有效但不是主收益来源 |

裁决：

- 该候选不是靠单一极端 winner 撑起来，但 residual path 贡献约三分之一 raw PnL，是 shadow P0。
- `repair` 不应被视为盈利模块；如果 completion 失败，优先验证 slow path 和 residual exit，而不是放宽 repair。
- early window 频率低但质量高，后续扩展应寻找更多早段确认信号，而不是继续放宽 mid-window delta。
- dynamic upclip 可以保留，但必须记录 `would_have_filled_at_60/120/160`，确认它没有因为大 clip 改变样本选择。

### 1. Queue Priority

当前收益高度依赖首腿 maker fill priority。

压力测试：

| fill stress | fills | L2 PnL | positive days | weakest day | L2 +2c |
|---|---:|---:|---:|---:|---:|
| `queue_full` | `46` | `+$259.20` | `5/5` | `+$22.80` | `+$198.00` |
| `queue_full + 60` | `27` | `+$76.61` | `4/5` | `-$15.54` | `+$40.21` |

裁决：

- 如果真实 fill quality 接近 `queue_full`，可以继续推进。
- 如果真实 fill quality 接近 `queue_full+60` 或更差，不能 enforce。
- 如果没有 user execution truth，只能做 market-side shadow，不能证明成交可行。

### 2. Completion VWAP Drift

replay 使用 L2 VWAP 估算补腿成本。shadow 必须记录真实补腿路径的可成交价格偏差。

阻断条件：

```text
completion_vwap_drift_p50 > 0.01
completion_vwap_drift_p90 > 0.03
```

若长期偏差超过 `1c`，当前收益安全垫会明显收缩。

### 3. Residual Exit

`first_price<0.50 -> 180s` 是当前收益最强的残仓规则，但也增加单边暴露。

阻断条件：

```text
180s residual exit realized/shadow VWAP edge 显著差于 replay L2
或者 180s residual path 单独转负
```

若该项失败，降级测试：

```text
if min_pair_cost_seen_30s <= 1.01:
    exit_delay = 180s
else:
    exit_delay = 120s
```

### 4. Daily Loss

阻断条件：

```text
任一完整日 candidate_count >= 20
且 shadow_pnl_with_2c_friction <= 0
```

该策略当前绝对收益不大，不能容忍 shadow 阶段已经出现稳定亏损日。

## 必须记录的 Shadow Events

shadow runner 至少输出以下事件：

```text
fastcancel_open_candidate
fastcancel_would_place_first_maker
fastcancel_first_fill_truth_or_proxy
fastcancel_completion_window
fastcancel_slow_path_decision
fastcancel_repair_decision
fastcancel_residual_exit_plan
fastcancel_episode_summary
```

必须字段：

```text
market_slug
condition_id
candidate_ts_ms
candidate_offset_s
window_name
first_side
side_bid
opp_ask
prev_bid_delta_1s
spread_ticks
opp_spread_ticks
top_bid_sz
queue_same
base_clip
effective_clip
upclip_reason
order_price
required_size_proxy
actual_first_order_placed
actual_first_fill_ts_ms
actual_first_fill_qty
actual_first_fill_vwap
proxy_queue_full_fill_ts_ms
extra_required_size_equivalent
completion_pair_cost_l1
completion_l2_vwap
completion_vwap_drift
min_pair_cost_seen_30s
slow_path_allowed
repair_used
residual_exit_delay_s
residual_exit_vwap
episode_status
shadow_pnl_l2
shadow_pnl_with_2c_friction
```

## Shadow Runbook

### Live Shadow Observer

真实 shadow / dry-run 的下一步不是下单，而是运行 rolling replay observer：

```bash
python3 scripts/run_fastcancel_live_shadow_observer.py \
  --config configs/xuan/fastcancel_shadow_sidecar_v1.json \
  --day 2026-05-04 \
  --output-dir data/exports/fastcancel_live_shadow/2026-05-04 \
  --loop \
  --poll-sec 30
```

该 observer 的边界：

- 只读 `data/replay/YYYY-MM-DD/crypto_5m.sqlite`。
- 不读 raw。
- 不修改 replay DB。
- 不读取私钥。
- 不发 REST。
- 不创建真实订单。
- 只输出 `fastcancel_live_shadow_events.jsonl`、`fastcancel_live_shadow_report.json`、`fastcancel_live_shadow_report.md`。

历史 smoke 示例：

```bash
python3 scripts/run_fastcancel_live_shadow_observer.py \
  --config configs/xuan/fastcancel_shadow_sidecar_v1.json \
  --day 2026-05-01 \
  --output-dir /tmp/fastcancel_live_shadow_smoke \
  --max-markets 20
```

重复运行同一个 output dir 会读取 `.fastcancel_live_shadow_state.json`，避免重复写入同一 candidate。

### 0. Replay Event Fixture

在 live shadow runner 完成前，可以先用 replay 结果生成同形状事件，作为事件 schema 和 report 消费端的固定夹具：

```bash
python3 scripts/emit_fastcancel_shadow_events_from_replay.py \
  --config configs/xuan/fastcancel_shadow_sidecar_v1.json \
  --output-dir /tmp/fastcancel_shadow_events_full
```

输出：

```text
fastcancel_shadow_events.jsonl
fastcancel_shadow_events_summary.json
```

同一事件流可以用统一汇总器生成 report：

```bash
python3 scripts/summarize_fastcancel_shadow_events.py \
  --events /tmp/fastcancel_shadow_events_full/fastcancel_shadow_events.jsonl \
  --output-json /tmp/fastcancel_shadow_events_full/fastcancel_shadow_report.json \
  --output-md /tmp/fastcancel_shadow_events_full/fastcancel_shadow_report.md
```

也可以用一键 pipeline 复用当前 leader selected rows：

```bash
python3 scripts/run_fastcancel_shadow_replay_pipeline.py \
  --config configs/xuan/fastcancel_shadow_sidecar_v1.json \
  --tag v1
```

默认模式只复用当前 leader rows，秒级生成事件与 report。若新 replay 数据到位，需要完整重扫 early/late rows，再显式打开：

```bash
python3 scripts/run_fastcancel_shadow_replay_pipeline.py \
  --config configs/xuan/fastcancel_shadow_sidecar_v1.json \
  --tag next_window \
  --days 2026-05-02,2026-05-03,2026-05-04 \
  --rebuild-rows
```

`--days` 覆盖配置日期时必须同时传 `--rebuild-rows`。这是故意的：复用模式绑定当前 leader selected rows，不能冒充新窗口。

`--rebuild-rows` 会顺序跑 4 组 replay backtest，耗时明显更长；不要在采集机器高峰期运行。

快速 smoke 可限制 market 数：

```bash
python3 scripts/run_fastcancel_shadow_replay_pipeline.py \
  --config configs/xuan/fastcancel_shadow_sidecar_v1.json \
  --tag smoke \
  --rebuild-rows \
  --max-markets 5 \
  --output-root /tmp/fastcancel_rebuild_smoke
```

当前 report 会自动输出裁决字段：

```text
replay_ready_for_live_shadow
own_execution_truth_ready
enforce_evaluable
promote_to_enforce_discussion
event_schema_pass
l2_2c_positive_all_days
completion_vwap_drift_pass
```

当前 `v1` fixture 的正确状态应为：

```text
replay_ready_for_live_shadow = true
own_execution_truth_ready = false
enforce_evaluable = false
promote_to_enforce_discussion = false
shadow_pnl_l2 = 259.2029
shadow_pnl_with_2c_friction = 198.0029
```

replay fixture 默认会 per-episode 附加 L2 truth：

```text
completion_l2_vwap
completion_vwap_drift
residual_exit_vwap
shadow_pnl_l2
shadow_pnl_with_2c_friction
```

这些字段使 replay fixture 和未来 live shadow report 使用同一套 PnL 口径。

自动化里可以要求 gate 通过：

```bash
python3 scripts/run_fastcancel_shadow_replay_pipeline.py \
  --config configs/xuan/fastcancel_shadow_sidecar_v1.json \
  --tag v1 \
  --require-replay-ready
```

`--require-enforce-ready` 目前应返回 `2`，因为缺少真实 user execution truth。

该夹具只来自 replay selected rows：

- 不读 raw。
- 不改 replay DB。
- 不含真实 user execution truth。
- `winner_side` / `first_is_winner` 只允许作为 research-only 字段，不得进入 live decision。

### 1. 启动前

确认：

```text
public market capture enabled
replay builder healthy
BTC 5m market_meta / md_book_l1 / md_book_l2 / md_trades available
user execution truth enabled if validating actual maker fill quality
```

如果 user truth 没开，本轮只能输出 market-side would-fill，不允许进入 enforce 讨论。

### 2. 运行窗口

最低窗口：

```text
3 full BTC trading days
```

推荐窗口：

```text
5 full BTC trading days
```

每日最小样本：

```text
candidate_count >= 20
```

若候选过少，不手动放宽参数，必须回到 replay 搜索重新导出配置。

### 3. 每日审计

每天生成：

```text
candidate_count
would_first_fill_count
actual_first_fill_count
fill_quality_bucket
completion_count
slow_completion_count
repair_count
residual_exit_count
shadow_pnl_l2
shadow_pnl_with_2c_friction
completion_vwap_drift_p50
completion_vwap_drift_p90
residual_exit_vwap_summary
```

每日问题顺序：

1. 候选是否足够。
2. first leg 是否能真实成交。
3. 成交质量是否接近 `queue_full`。
4. completion/repair 是否被 L2 replay 低估。
5. residual exit 是否承担了主要利润或主要风险。

### 4. Promote 条件

只有同时满足以下条件，才允许进入 enforce 讨论：

```text
3-5 full days shadow positive after 2c friction
actual maker fill quality materially better than queue_full+60 stress
completion_vwap_drift_p50 <= 0.01
completion_vwap_drift_p90 <= 0.03
180s residual exit path not negative
no hidden same-side inventory accumulation
```

即使通过，也只能先进入小仓 enforce 设计，不允许直接放大。

## 禁止事项

- 不允许用 replay 结果直接 enforce。
- 不允许把 `winner_side` 用作 live open/sizing gate。
- 不允许把 `sell_vol_until_fill`、`fill_delay` 这类事后字段用于开仓。
- 不允许把 mid-window delta 放宽到 `0.04`，除非新 out-of-sample replay 重新证明。
- 不允许把 fixed `clip=120/160` 作为默认。
- 不允许在未验证 user truth 的情况下声称该策略可真实成交。

## 下一步实现合同

下一步要实现的是 shadow sidecar，不是交易主引擎：

```text
read live/replay BTC 5m L1/L2/trades
evaluate fastcancel open candidate
emit would-order event
track proxy queue fill and actual fill truth if available
simulate completion-only lifecycle
emit episode summary
daily aggregate report
```

策略主程序必须保持：

```text
place_real_orders = false
send_rest = false
```

直到 shadow 通过 P0 验收。
