# ce25 / nagi 策略研发转型计划

生成时间：2026-06-04 BJT

状态：`REVIEW_ONLY_STRATEGY_TRANSITION_PLAN_PREPARED_NOT_EXECUTED`

本文档把 `CE25_NAGI_HISTORICAL_ALPHA_HANDOFF_ZH.md` 转成后续策略开发路线。目标不是复制公开账户，也不是直接进入实盘，而是按 B/C 类似的 owner-line 流程，把历史公开 profile 中的窄桶转成可 replay、可 OOS、可审计的策略候选。

## 核心判断

这份 handoff 里真正有价值的是三个窄原型，不是 ce25/nagi 全账户。

优先级：

| 优先级 | 原型 | 定位 | 当前判断 |
| --- | --- | --- | --- |
| P0 | `CE25_BTC5M_LOW_PRICE_TAIL_DOWN_V1` | 主 alpha 候选 | 先实现，但必须用更窄的 last60/DOWN 分支 |
| P1 | `CE25_BTC5M_HIGH_PRICE_STOP_1_5M_V1` | 时段/风险过滤器 | 值得实现，不能当纯 alpha |
| P2 | `NAGI_LAST60_MIDPRICE_FASTPAIR_V1` | 执行模板 | 可实现为快补腿约束，但不照抄 nagi |
| Reject | 全账户复制 ce25/nagi | 账户画像 | 不做 |
| Reject | `ce25_15m_first50_65_delay30_60_fragile` | 脆弱桶 | 不做 |

关键原因：

- ce25/nagi 全账户 `bad_pc_ge_100_share` 都接近 47%，复制全量会把垃圾桶一起复制。
- `pair_delay<=15s` 是历史结果，不是入场前信号；只能转成 execution SLA。
- `first_price` 是公开账户第一笔成交价格，不能直接当实时信号；只能用我们自己看到的 best ask / L2 executable price 替代。
- 公开 activity 不能证明第三方 private maker truth、撤单速度、queue priority 或 authenticated trader side。

## 已复核的历史证据

来源：

```text
/Users/hot/web3Scientist/poly_trans_research/data/exports/account_autoresearch_iter_ce25_7win_nagi_4win_20260604_hb2_bjt
```

代表性 proxy 指标：

| proxy_id | 窗口 | 盈利窗口 | 市场 | buy_actual | cash_pnl | ROI | pair_cost | resid_rate | bad_pc>=1 | top3_net_share |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `ce25_btc5m_first20_35` | 7 | 5 | 129 | 44,586.92 | 2,152.47 | 4.83% | 0.9076 | 13.16% | 30.21% | 52.63% |
| `ce25_first65_80_stop_1_5m` | 7 | 7 | 380 | 37,544.00 | 1,891.57 | 5.04% | 0.9738 | 15.15% | 36.87% | 30.91% |
| `ce25_first65_80_last60_control` | 7 | 5 | 206 | 40,269.95 | 335.94 | 0.83% | 1.0018 | 10.29% | 63.92% | 136.38% |
| `nagi_last60_first35_50_fastpair` | 4 | 3 | 195 | 157,044.49 | 2,969.11 | 1.89% | 0.9720 | 7.27% | 43.62% | 37.29% |
| `ce25_15m_first50_65_delay30_60_fragile` | 7 | 3 | 142 | 64,087.34 | 150.31 | 0.23% | 0.9941 | 8.54% | 45.43% | 610.77% |

解释：

- `ce25_btc5m_first20_35` 广义桶仍有 `bad_pc>=1=30.21%` 和 `top3_net_share=52.63%`，不能直接进入 OOS。必须从 handoff 里的更窄 `last_60s|20-35|DOWN` 分支开始。
- `ce25_first65_80_stop_1_5m` 7/7 盈利，但 pair_cost 优势不大、残仓偏高，更适合作为时间窗过滤器或小规模独立策略。
- `nagi_last60_first35_50_fastpair` 现金 PnL 正，但坏 pair-cost 太高，应抽象成“快补腿、低残仓、pair_cost ceiling”的执行约束。

## 新 owner-line 研发模式

建议建立独立 owner-line：

```text
strategy_owner_line=CE25_NAGI_RESEARCH
```

边界：

- 不复用 B/C artifacts。
- 不把 B 输出当 C 输入，也不把本策略输出当 B/C 输入。
- 不依赖已退役的 shared-WS/shared-ingress。
- 默认只做 local replay / public-only OOS。
- no private key, no import, no order, no cancel, no redeem, no live, no deploy, no funding, no latest accepted pointer。

Runner 的角色应限制为：

- review/compliance coordinator；
- packet/template helper；
- evidence packaging helper；
- cross-owner boundary checker。

策略逻辑本身应由 `CE25_NAGI_RESEARCH` owner-line 的 strategy input 和 runner source 明确绑定，不由 runner 临时决定。

## 阶段计划

### Phase S0: Strategy Input

产物：

```text
CE25_NAGI_STRATEGY_INPUT_v0.json
CE25_NAGI_POLICY_SPEC_v0.md
CE25_NAGI_REPLAY_THRESHOLD_SPEC_v0.json
```

必须包含：

- `strategy_id`
- `strategy_version`
- `strategy_owner_line=CE25_NAGI_RESEARCH`
- `policy_id`
- `branch_id`
- asset/timeframe/window/side/price-band
- ex-ante observable features
- outcome-only fields blacklist
- non-claims
- fee stress levels
- acceptance gates

初始 policy：

```text
CE25_BTC5M_LOW_PRICE_TAIL_DOWN_V1
CE25_BTC5M_HIGH_PRICE_STOP_1_5M_V1
NAGI_LAST60_MIDPRICE_FASTPAIR_V1
```

### Phase S1: Local Replay Runner

优先复用现有本地数据和脚本，不从零造一套。

数据根：

```text
/Users/hot/web3Scientist/poly_backtest_data
```

优先层：

| 阶段 | 数据层 | 用途 |
| --- | --- | --- |
| 宽筛 | `backtest_cache/taker_buy_signal_core_v2_strict_l1` | 时间、价格、方向桶筛选 |
| 状态机 | `verification_store/completion_unwind_event_store_v2` | 补腿、残仓、unwind 验证 |
| 候选物化 | `derived/completion_candidate_pipeline_v1` | 小表跑 state machine |

实现建议：

- 优先扩展或包裹 `scripts/build_completion_candidate_base.py`。
- 优先复用 `scripts/run_completion_candidate_state_machine.py`。
- 新增一个薄入口，例如 `scripts/run_ce25_nagi_shadow_policy_runner.py`，负责把 policy spec 转成 candidate filters，再调用现有 state-machine/replay 组件。

不要直接扫描 raw/replay/collector 目录。报告必须声明读取的 manifest、labels、days、excluded days。

### Phase S2: Replay Acceptance

每个 policy/branch 输出：

- candidate event table；
- market/window/day summary；
- fee stress 0%, 2.5%, 2.83%, 3.0%；
- residual stress；
- pair_cost distribution；
- bad pair-cost share；
- max single-market loss；
- top3 concentration；
- branch/control comparison。

进入下一阶段的最低门槛：

| 指标 | 初始门槛 |
| --- | --- |
| profitable windows | >= 70% |
| aggregate cash_pnl | > 0 |
| pair_cost | <= 0.98，优先 <= 0.96 |
| bad_pc_ge_100_share | P0 <= 25%，NAGI 模板 <= 30% |
| residual rate | <= 10%-12% |
| max single-market loss | 小于预设 cap |
| top3 concentration | 不能由少数市场主导 |

### Phase S3: Public OOS

只有 replay 通过后才准备 public OOS packet。

OOS 必须：

- 使用新 owner-line 独立 namespace；
- fresh current/future targets；
- public book / latency / fillability proxy only；
- no shared-WS legacy dependency；
- no REST book 替代 top-depth evidence；
- no disconnect/reconnect/recovered round in clean path；
- readiness flags 全 false。

OOS 即使通过，也只能证明 public fillability/top-depth proxy，不证明 private truth、promotion、live readiness 或 deployable。

### Phase S4: Owner Truth / Canary

这不是当前阶段。

只有在 public OOS 多窗口通过、风险预算单独批准后，才讨论极小 canary。canary 必须另出 packet，包含 custody/signing/order/fill/fee/inventory/redeem/cancel/error truth schema 和硬资金上限。

## 三个策略的可执行定义

### P0: CE25_BTC5M_LOW_PRICE_TAIL_DOWN_V1

入口：

```text
asset=BTC
timeframe=5m
time_to_close_s <= 60
first_leg_side=DOWN
first_leg_executable_price in [0.20, 0.35]
projected_pair_cost <= 0.97 initial, <= 0.98 observation
```

控制：

```text
completion_sla_s <= 15 aggressive, <= 30 conservative
stop_new_entry_if_time_to_close_s <= 10
resid_rate_cap <= 10%-12%
rolling_bad_pc_ge_100_share_stop >= 25%
```

对照：

```text
UP same price/time window
last_60s side-neutral
1-5m 20-35 bucket
```

### P1: CE25_BTC5M_HIGH_PRICE_STOP_1_5M_V1

入口：

```text
asset=BTC first, then optional ETH/SOL/XRP review
timeframe=5m
60 < time_to_close_s <= 300
first_leg_executable_price in [0.65, 0.80]
```

控制：

```text
do_not_open_new_first_leg_when_time_to_close_s <= 60
pair_cost_ceiling <= 0.98
size lower than P0
resid_rate target <= 10%
```

对照：

```text
same 65-80 bucket in last_60s
side-neutral vs DOWN-preferred branch
```

### P2: NAGI_LAST60_MIDPRICE_FASTPAIR_V1

入口：

```text
asset=BTC
timeframe=5m
time_to_close_s <= 60
branch_a: first_side=UP, price in [0.35, 0.50]
branch_b: first_side=DOWN, price in [0.50, 0.65]
```

控制：

```text
completion_sla_s <= 15
pair_cost_ceiling <= 0.97 initial
no averaging if projected_pair_cost >= 1.00
hard_resid_rate_cap <= 8%
rolling_bad_pc_ge_100_share_stop >= 30%
```

## 立即要做的工程任务

1. 准备 `CE25_NAGI_STRATEGY_INPUT_v0`，只描述策略输入和分支，不执行。
2. 准备 local replay runner patch，优先复用 completion candidate pipeline 和 state-machine。
3. 在有效 manifest labels 上跑三条 policy 的 replay，不扫 raw。
4. 输出 review bundle：event table、summary、fee stress、residual stress、branch/control、hash manifest。
5. 根据 replay 结果决定是否准备 public OOS packet。

## 明确禁止

- 不复制 ce25/nagi 全账户。
- 不把 public account profile 写成 private execution truth。
- 不把 pair_delay 当入场前信号。
- 不跳过 replay 直接 OOS。
- 不跳过 public OOS 直接 canary/live。
- 不复活 shared-WS/shared-ingress 作为新策略依赖。

## 当前最高状态

```text
KEEP_CE25_NAGI_STRATEGY_TRANSITION_PLAN_PREPARED_LOCAL_REPLAY_PACKET_NEXT_NOT_LIVE_READY
```

