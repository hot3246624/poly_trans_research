# ce25 / nagi 历史赚钱模式实现交接文档

生成时间：2026-06-04 BJT

本文档目标不是继续跟踪 ce25 和 nagi 的最新 PnL，而是从已经抓取的公开历史 profile 中提炼可复现的策略原型，交给实现同事做 shadow/replay 验证。

## 技术结论

最值得实现的不是“复制账户”，而是三个窄策略原型：

1. `CE25_BTC5M_LOW_PRICE_TAIL_DOWN_V1`
   - 核心模式：BTC 5m 市场，临近收盘，第一腿价格在 20c-35c，优先 DOWN 方向，快速完成对腿。
   - 公开历史证据最强：`last_60s|20-35|DOWN` 在 125 个市场、买入 29,615.34 USDC、现金 PnL +2,977.76、ROI 10.05%、pair_cost 0.8885、残仓率 11.26%、bad pair-cost share 24.53%。
   - 这是当前最像“可实现 alpha”的桶，因为核心条件可以转成实时可观测条件：资产、5m 周期、距收盘时间、盘口价格、方向。

2. `CE25_BTC5M_HIGH_PRICE_STOP_1_5M_V1`
   - 核心模式：价格 65c-80c 的高概率腿，只在收盘前 1-5 分钟参与，不进入最后 60 秒。
   - 公开历史证据稳：7/7 窗口盈利，380 个市场、买入 37,544.00 USDC、现金 PnL +1,891.57、ROI 5.04%、pair_cost 0.9738、残仓率 15.15%。
   - 这更像风控/时段过滤规则，不是纯 alpha。注意同样的 65c-80c 进入最后 60 秒后，pair_cost 约 1.0018，质量明显变差。

3. `NAGI_LAST60_MIDPRICE_FASTPAIR_V1`
   - 核心模式：最后 60 秒，中间价格带，强制快速配对；不是全账户复制。
   - 历史证据：`last_60s|35-50|fast pair <=15s` 在 4 个窗口、195 个市场、买入 157,044.49 USDC、现金 PnL +2,969.11、ROI 1.89%、pair_cost 0.9720、残仓率 7.27%。
   - 风险：bad pair-cost share 43.62%，4 个窗口中有 1 个亏损窗口；必须加 pair_cost ceiling 和限时退出，否则容易把好看的成交量变成低质量库存。

结论排序：

| 优先级 | 策略原型 | 角色 | 是否建议实现 |
| --- | --- | --- | --- |
| P0 | `CE25_BTC5M_LOW_PRICE_TAIL_DOWN_V1` | 主 alpha 候选 | 是，先做 shadow/replay |
| P1 | `CE25_BTC5M_HIGH_PRICE_STOP_1_5M_V1` | 风控/过滤器 | 是，作为独立策略和过滤器都测 |
| P2 | `NAGI_LAST60_MIDPRICE_FASTPAIR_V1` | 执行模板 | 可以测，但必须更严格 |
| Reject | 全账户复制 ce25/nagi | 画像，不是策略 | 不做 |
| Reject | `ce25_15m_first50_65_delay30_60_fragile` | 脆弱桶 | 不做 |

## 数据范围和口径

本报告只使用公开 activity/profile 和本地生成的聚合结果，不证明第三方私有 maker/taker 真相、挂单生命周期、撤单、queue priority 或 authenticated `trader_side`。

核心来源：

- 最新迭代报告：`/Users/hot/web3Scientist/poly_trans_research/data/exports/account_autoresearch_iter_ce25_7win_nagi_4win_20260604_hb2_bjt/iteration_report.md`
- 账户汇总：`/Users/hot/web3Scientist/poly_trans_research/data/exports/account_autoresearch_iter_ce25_7win_nagi_4win_20260604_hb2_bjt/account_rollup.tsv`
- 预注册 proxy 汇总：`/Users/hot/web3Scientist/poly_trans_research/data/exports/account_autoresearch_iter_ce25_7win_nagi_4win_20260604_hb2_bjt/pre_registered_proxy_summary.tsv`
- 预注册 proxy 分窗口：`/Users/hot/web3Scientist/poly_trans_research/data/exports/account_autoresearch_iter_ce25_7win_nagi_4win_20260604_hb2_bjt/pre_registered_proxy_window_rollup.tsv`
- proxy 排行榜：`/Users/hot/web3Scientist/poly_trans_research/data/exports/account_autoresearch_iter_ce25_7win_nagi_4win_20260604_hb2_bjt/proxy_scoreboard.tsv`

覆盖窗口：

- ce25：7 个 rolling 24h 窗口，2026-05-28 11:45 BJT 到 2026-06-04 11:45 BJT。
- nagi：4 个非连续 24h 窗口，包括 2026-05-28、05-29、05-30、06-03 的 11:45 BJT 起点窗口。

指标定义：

| 指标 | 定义 |
| --- | --- |
| `buy_actual` | BUY 的真实 USDC 流出，优先使用 activity `usdcSize` |
| `cash_pnl` | SELL + MERGE + REDEEM + rebate - BUY `usdcSize` |
| `pair_cost` | 买到一份 YES + 一份 NO 的费后平均成本 |
| `pair_pnl` | `paired_qty * (1 - pair_cost)` |
| `resid_rate` | 未配对残仓份额 / 总买入份额 |
| `bad_pc_ge_100_share` | pair_cost >= 1.00 的市场所占买入金额比例 |
| `fee_rate` | fee-like 成本 / pre-fee BUY notional |

## 不要复制全账户

账户级历史表现是正的，但质量不足以直接复制。

| 账户 | 窗口 | 市场数 | 买入额 | 现金 PnL | ROI | pair_cost | 残仓率 | bad pc>=1 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| ce25 | 7 | 6,082 | 1,228,238.67 | 16,923.26 | 1.38% | 0.9695 | 12.50% | 47.41% |
| nagi | 4 | 1,123 | 798,783.27 | 5,399.32 | 0.68% | 0.9747 | 12.10% | 47.06% |

解释：

- 两个账户全量 bad pair-cost share 都接近 47%，说明大量市场 pair_cost >= 1.00。赚钱来自少数窄桶和执行质量，而不是所有成交都有 alpha。
- ce25 的公开证据覆盖更广，适合先提炼规则。
- nagi 的无 fee 历史表现不等于可复制；它的核心更像“最后 60 秒快速完成库存”的执行模板。

## 秘籍 1：ce25 的低价尾段桶

最强可实现桶：

| 条件 | 市场数 | 买入额 | 现金 PnL | ROI | pair_cost | 残仓率 | bad pc>=1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `last_60s|20-35|DOWN` | 125 | 29,615.34 | 2,977.76 | 10.05% | 0.8885 | 11.26% | 24.53% |
| `BTC|last_60s|20-35` | 74 | 31,406.73 | 2,833.16 | 9.02% | 0.8893 | 9.79% | 20.23% |
| `last_60s|20-35` | 220 | 44,966.46 | 3,798.21 | 8.45% | 0.8959 | 11.92% | 24.76% |
| `BTC|5m|20-35` | 129 | 44,586.92 | 2,152.47 | 4.83% | 0.9076 | 13.16% | 30.21% |

可实现解释：

- `first_price_bucket=20-35` 在 profile 中表示账户第一腿成交价格，不应直接拿公开账户 activity 当实时信号。
- 实现时应转译为：在自己观察到的 CLOB L1/L2 中，目标侧 best ask 或可成交价格进入 0.20-0.35。
- `last_delta_bucket=last_60s` 可转译为：市场结束前最后 60 秒。
- `first_side=DOWN` 可转译为：优先测 DOWN 侧作为第一腿，UP 侧作为对照。

实现候选：

```text
policy_id = CE25_BTC5M_LOW_PRICE_TAIL_DOWN_V1
market_filter:
  asset = BTC
  timeframe = 5m
  time_to_close_s <= 60
entry_filter:
  preferred_first_side = DOWN
  first_leg_ask_price in [0.20, 0.35]
  projected_pair_cost_ceiling <= 0.97
execution_controls:
  first_leg_max_notional_per_market <= configured_cap
  completion_leg_sla_s <= 15 for aggressive version, <= 30 for conservative version
  stop_new_entry_if_time_to_close_s <= 10
  force_unwind_or_no_more_size_if_resid_rate_est > 10%
kill_switch:
  rolling_bad_pair_cost_share >= 25%
  rolling_resid_rate >= 15%
  rolling_max_market_loss exceeds cap
```

关键假设：

- 盈利不是来自“猜方向”，而是来自尾段错误定价和快速补对腿，使 pair_cost 明显低于 1。
- DOWN 方向在这段历史里更强，但不能假设永久有效；实现必须保留 UP 对照桶。

## 秘籍 2：ce25 的高价腿不要进最后一分钟

预注册结果显示，65c-80c 高价腿在 1-5 分钟窗口内稳定，但进入最后 60 秒后显著变差。

| 条件 | 窗口盈利 | 市场数 | 买入额 | 现金 PnL | ROI | pair_cost | 残仓率 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `65-80` 且 `last_delta=1-5m` | 7/7 | 380 | 37,544.00 | 1,891.57 | 5.04% | 0.9738 | 15.15% |
| `65-80` 且 `last_delta=last_60s` | 5/7 | 206 | 40,269.95 | 335.94 | 0.83% | 1.0018 | 10.29% |

可实现解释：

- 这不是最高收益 alpha，而是一个风控结论：高价腿如果拖到最后一分钟，pair_cost 质量变差，盈亏高度依赖残仓方向。
- 该规则适合作为全局过滤器：`65-80` 区间只能在收盘前 1-5 分钟做小规模、低风险试探，最后 60 秒停止新开。

实现候选：

```text
policy_id = CE25_BTC5M_HIGH_PRICE_STOP_1_5M_V1
market_filter:
  asset in [BTC, ETH, SOL, XRP] initially run BTC-only then expand
  timeframe = 5m
  60 < time_to_close_s <= 300
entry_filter:
  first_leg_ask_price in [0.65, 0.80]
  prefer DOWN on BTC branch, keep side-neutral control
execution_controls:
  smaller size than LOW_PRICE_TAIL
  do not open new first leg when time_to_close_s <= 60
  pair_cost_ceiling <= 0.98
  residual cap stricter than account history, target <= 10%
```

## 秘籍 3：nagi 的优势是快速完成，不是全账户

nagi 的全账户 4 窗口 ROI 只有 0.68%，但最后 60 秒中位价区间有可学习的执行模式。

预注册 fastpair：

| 条件 | 窗口盈利 | 市场数 | 买入额 | 现金 PnL | ROI | pair_cost | 残仓率 | bad pc>=1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `last_60s|35-50|pair_delay<=15s` | 3/4 | 195 | 157,044.49 | 2,969.11 | 1.89% | 0.9720 | 7.27% | 43.62% |
| `last_60s|35-50|pair_delay=15-60s` | 3/4 | 197 | 142,205.81 | 795.21 | 0.56% | 0.9692 | 9.43% | 49.80% |

可实现的非 `pair_delay` 历史桶：

| 条件 | 市场数 | 买入额 | 现金 PnL | ROI | pair_cost | 残仓率 | bad pc>=1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `last_60s|35-50|UP` | 216 | 152,633.74 | 5,143.55 | 3.37% | 0.9547 | 8.49% | 43.43% |
| `last_60s|50-65|DOWN` | 141 | 103,067.16 | 3,592.29 | 3.49% | 0.9613 | 6.55% | 42.87% |
| `last_60s|50-65` | 313 | 226,717.90 | 6,072.01 | 2.68% | 0.9692 | 7.96% | 44.79% |

实现候选：

```text
policy_id = NAGI_LAST60_MIDPRICE_FASTPAIR_V1
market_filter:
  asset = BTC
  timeframe = 5m
  time_to_close_s <= 60
entry_filter:
  branch_a: first_side = UP, first_leg_ask_price in [0.35, 0.50]
  branch_b: first_side = DOWN, first_leg_ask_price in [0.50, 0.65]
execution_controls:
  pair_completion_sla_s <= 15
  pair_cost_ceiling <= 0.97 for initial shadow
  no averaging if projected pair_cost would cross 1.00
  hard residual cap <= 8%
kill_switch:
  stop branch if bad_pair_cost_share >= 30% in rolling shadow window
  stop branch if any single market loss exceeds configured loss cap
```

关键注意：

- `pair_delay<=15s` 是历史画像中的结果，不是开仓前可观测信号。实现时只能把它转成强制执行 SLA：开第一腿后，如果 15 秒内不能以合格 pair_cost 完成对腿，就停止加仓并进入补救/退出逻辑。
- nagi 的坏 pair-cost 占比太高。实现版本必须比 nagi 更保守，不能照抄它的全量行为。

## 明确拒绝的模式

1. 全账户复制
   - ce25 和 nagi 全账户 bad pc>=1 share 都约 47%。
   - 复制全量会把盈利桶和垃圾桶一起复制。

2. 只看残仓率
   - 低残仓是风险控制，不是 alpha。
   - 必须同时看 pair_cost、cash_pnl、bad_pc_ge_100_share 和最大单市场亏损。

3. `ce25_15m_first50_65_delay30_60_fragile`
   - 7 个窗口只有 3 个盈利。
   - ROI 0.23%，pair_cost 0.9941，top3 concentration 很高。
   - 不作为实现目标。

4. 把公开 activity 当私有 maker truth
   - 公开数据不能证明 maker-only、队列位置、撤单速度、私有 API 行为。
   - 实现同事需要用自己的 shadow telemetry 和 authenticated order telemetry 验证。

## 实现架构建议

先实现 shadow，不要直接实盘：

```text
market scanner
  -> market classifier(asset, timeframe, close_time)
  -> L1/L2 feature builder(time_to_close, best_bid/ask, sizes, spread)
  -> candidate policy router
  -> shadow fill simulator
  -> inventory state machine
  -> pair_cost / residual / max_loss monitor
  -> report writer
```

状态机：

```text
IDLE
  -> FIRST_LEG_OPEN when entry_filter true
FIRST_LEG_OPEN
  -> PAIRED when opposite leg filled and pair_cost <= ceiling
  -> RESCUE when completion_sla exceeded
  -> STOPPED when market too close to settlement
PAIRED
  -> MERGED or SETTLED
RESCUE
  -> PAIRED if acceptable completion appears
  -> UNWOUND if exit is cheaper than residual risk
  -> SETTLED_RESIDUAL if no safe exit
```

每个 candidate 必须输出：

```text
condition_id
slug
asset
timeframe
policy_id
branch_id
first_leg_side
first_leg_ts_ms
first_leg_price
first_leg_size
completion_leg_ts_ms
completion_leg_price
pair_delay_s
pair_cost
paired_qty
resid_qty
resid_rate
buy_actual_est
cash_pnl_est
fee_model
max_adverse_excursion
decision_reason
kill_switch_reason
```

## 回放验证路径

必须使用本地 manifest 发布数据，不要扫 raw/replay/collector 目录。

本地数据根：

```text
/Users/hot/web3Scientist/poly_backtest_data
```

有效日：

```text
2026-05-02..2026-05-13
2026-05-16
2026-05-17
2026-05-18
```

排除：

```text
2026-05-14
2026-05-15
2026-05-19
```

优先数据层：

| 阶段 | 数据层 | 用途 |
| --- | --- | --- |
| 宽筛 | `taker_buy_signal_core_v2_strict_l1` | 搜索价格/时间/方向桶 |
| 状态机 | `completion_unwind_event_store_v2` | 验证补腿、unwind、残仓 |
| 候选物化 | `completion_candidate_pipeline_v1` | 小表跑 state machine |

建议命令入口：

```bash
export POLY_BT_ROOT=/Users/hot/web3Scientist/poly_backtest_data

python - <<'PY'
from pathlib import Path
root = Path("/Users/hot/web3Scientist/poly_backtest_data")
block = {"20260514", "20260515", "20260519"}

def labels(base, manifest):
    out = []
    for p in sorted((root / base).glob("*")):
        if p.name in block:
            continue
        if (p / manifest).exists():
            out.append(p.name)
    return out

print("strict_v2_labels=", labels(
    "backtest_cache/taker_buy_signal_core_v2_strict_l1",
    "CACHE_MANIFEST.json",
))
print("completion_v2_labels=", labels(
    "verification_store/completion_unwind_event_store_v2",
    "EVENT_STORE_MANIFEST.json",
))
PY
```

回放报告必须声明：

```text
data_root=/Users/hot/web3Scientist/poly_backtest_data
dataset_type=<strict_v2_cache | completion_unwind_event_store_v2 | completion_unwind_event_store_v2_candidate_base>
labels=<实际读取 label>
days=<实际覆盖 UTC day>
market_prefix/assets=<例如 btc-updown-5m- / BTC>
excluded=20260514,20260515,20260519
public_account_execution_truth_v1=false
raw/replay/collector_scanned=false
```

## 验收标准

一个策略原型只有满足下面条件，才可以从 shadow 进入更严格的 paper/live-review 阶段：

| 指标 | P0 建议门槛 |
| --- | --- |
| profitable days/windows | >= 70% |
| aggregate cash_pnl | > 0 |
| pair_cost | <= 0.96 优先，<= 0.98 可观察 |
| bad_pc_ge_100_share | <= 25%，nagi 模板初始必须 <= 30% |
| residual rate | <= 10%-12% |
| max single-market loss | 必须小于预设市场亏损上限 |
| top3 net concentration | 不能由少数市场贡献大部分盈利 |
| fee stress | 同时跑 0%、2.5%、2.83%、3.0% fee stress |

## 交给实现同事的任务清单

1. 建 `shadow_policy_runner`，读取 L1/L2 或 completion candidate base，不下真实订单。
2. 实现三个 policy id：
   - `CE25_BTC5M_LOW_PRICE_TAIL_DOWN_V1`
   - `CE25_BTC5M_HIGH_PRICE_STOP_1_5M_V1`
   - `NAGI_LAST60_MIDPRICE_FASTPAIR_V1`
3. 每个 policy 保留 branch/control：
   - ce25 low price：DOWN 主分支，UP 对照。
   - ce25 high price：1-5m 主分支，last_60s 对照。
   - nagi mid price：fastpair SLA 主分支，slowpair 对照。
4. 输出统一 state-machine 事件表和日级/窗口级 summary。
5. 在有效日上跑 fee stress 和 residual stress。
6. 不接入任何私钥、不发送/取消真实订单、不声称 maker-only 真相。

## 最短实现伪代码

```python
def route_policy(market, book, now_ms):
    ttc = market.close_ms - now_ms
    candidates = []

    if market.asset == "BTC" and market.timeframe == "5m" and ttc <= 60_000:
        down_ask = book.ask("DOWN")
        up_ask = book.ask("UP")

        if 0.20 <= down_ask.price <= 0.35:
            candidates.append(("CE25_BTC5M_LOW_PRICE_TAIL_DOWN_V1", "DOWN"))

        if 0.35 <= up_ask.price <= 0.50:
            candidates.append(("NAGI_LAST60_MIDPRICE_FASTPAIR_V1", "UP"))

        if 0.50 <= down_ask.price <= 0.65:
            candidates.append(("NAGI_LAST60_MIDPRICE_FASTPAIR_V1", "DOWN"))

    if market.timeframe == "5m" and 60_000 < ttc <= 300_000:
        for side in ("UP", "DOWN"):
            ask = book.ask(side)
            if 0.65 <= ask.price <= 0.80:
                candidates.append(("CE25_BTC5M_HIGH_PRICE_STOP_1_5M_V1", side))

    return candidates

def accept_completion(first_leg, opposite_quote, policy):
    projected_pair_cost = first_leg.price + opposite_quote.price
    if projected_pair_cost > policy.pair_cost_ceiling:
        return False
    if now_ms() - first_leg.ts_ms > policy.completion_sla_ms:
        return False
    return True
```

## 关键复核问题

实现同事开始前需要确认三件事：

1. 当前 fee 模型到底按哪个市场收费，shadow 必须跑 fee stress。
2. replay/state-machine 的 fill assumption 是 taker-fill、maker-fill 还是 book-cross shadow，不同假设必须分开报。
3. `first_price` 只能用自己的实时入场价格替代，不能用公开账户 activity 里的事后 first trade。

## 最终判断

当前最值得学的是 ce25 的窄桶，不是 nagi 全账户。

如果只能做一个策略原型，先做 `CE25_BTC5M_LOW_PRICE_TAIL_DOWN_V1`。它的公开历史 evidence 最集中、条件最容易实时观测、pair_cost 优势最大。nagi 的价值在执行约束：快补腿、低残仓、严格 pair_cost ceiling；这可以作为执行模块借鉴，但不应该直接复制账户行为。
