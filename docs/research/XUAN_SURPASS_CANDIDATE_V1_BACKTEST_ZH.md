# Xuan Surpass Candidate V1 Backtest

## 结论

我们现在已经可以开始设计“超越 xuan”的策略，但不能用单一参数直接上实盘。

当前最重要的发现是：

- 纯 `xuan-like` near-parity repair 不够赚钱。
- `profit095` 先锁低 pair cost，之后再 repair，能显著提高单位收益。
- 但 repair 越宽，闭合率越高、单位收益越低。
- 当前还没找到 5 日稳定同时超过 xuan `surplus/size` 和 `30s completion` 的单一参数。
- 因此下一步应该做双轨 shadow：`alpha mode` 和 `balanced mode` 同时跑，用我方真实 dry-run/execution truth 决定哪条能实盘。

## 回测模型

脚本：

`/Users/hot/web3Scientist/poly_trans_research/scripts/backtest_xuan_proxy_completion_first_v1.py`

数据：

- replay root: `/Users/hot/web3Scientist/poly_trans_research/data/replay`
- read-only SQLite
- 不读 raw
- 不使用 own execution truth
- 不使用 `winner_side` 做策略决策

执行假设：

- first leg: 在高侧 best bid 挂 maker buy。
- first fill: 用 public `taker_side=SELL` 成交流做成交 proxy。
- completion: 用 opposite side L2 ask sweep 做 bounded taker。
- 单 active tranche。
- 不允许同侧加仓。

## Xuan 参考

| metric | xuan 5d |
|---|---:|
| tranches | `4587` |
| all BTC markets | `1340` |
| markets with reconstructed paired tranche | `769` |
| active market rate | `57.39%` |
| tranches per all market | `3.42` |
| tranches per active market | `5.96` |
| 30s completion | `82.08%` |
| pair cost p50 | `0.994604` |
| surplus/size | `0.020216` |
| first-winner rate | `65.69%` |
| tranche size p50 / p75 / p90 | `100.5 / 155.0 / 216.7` |

## 05-01 参数扫描

基础设置：

```text
base_clip=60
sample_interval=20s
first_fill_timeout=20s
first leg = high-side bid
completion primary = 30s@0.95
repair trigger = min_pair_cost_30s > 0.95 or missing
```

| mode | closed/fill | pair p50 | surplus/size | 裁决 |
|---|---:|---:|---:|---|
| `profit095` no repair | `35.62%` | `0.95` | `0.056198` | 收益很强，残差过大 |
| `profit095 + repair101` | `67.49%` | `0.95` | `0.033937` | alpha 候选 |
| `profit095 + repair102` | `73.45%` | `0.95` | `0.029897` | alpha/risk 折中 |
| `profit095 + repair103` | `79.33%` | `0.95` | `0.024376` | 当前最佳 alpha-risk frontier |
| `profit095 + repair104` | `80.20%` | `0.95` | `0.020827` | 单日略超 xuan，但优势很薄 |
| `balanced 2s@0.95 -> 30s@1.005` | `76.02%` | `1.00` | `0.005333` | 不够赚钱 |

解释：

- 如果目标是超越 xuan 的单位收益，`profit095 + repair103` 比 `repair104` 更健康。
- 如果目标是接近 xuan 的闭合率，`repair104` 接近但收益优势太薄。
- near-parity completion 不是 alpha，只是风险控制。
- 当前我们的候选不是大 clip 策略。`base_clip=60` 经过 open gate 后，绝大多数实际成交 clip 是 `30`；05-01 broad run 的 closed clip p50/p75/p90 都是 `30`，只有少量 positive-L2-edge 样本用 `60/75`。
- xuan 则明显更重仓：重建 tranche size p50 `100.5`、p75 `155.0`、p90 `216.7`。所以当前候选是在用更小仓位换更稳的实验边界，还没有复制 xuan 的资金效率。

## 04-30/05-01 跨日验证

`profit095 + repair104` 两日合并：

| metric | value |
|---|---:|
| attempt markets | `566` |
| first fills | `1223` |
| first fills / attempt market | `2.16` |
| closed pairs / attempt market | `1.76` |
| closed/fill | `81.36%` |
| 30s/fill | `74.00%` |
| pair p50 | `0.966887` |
| surplus/size | `0.016436` |
| actual closed clip p50 / p75 / p90 | `30 / 30 / 30` |

按日：

| day | closed/fill | pair p50 | surplus/size |
|---|---:|---:|---:|
| `2026-04-30` | `82.48%` | `0.975588` | `0.012321` |
| `2026-05-01` | `80.20%` | `0.950000` | `0.020827` |

裁决：

- `repair104` 单日 05-01 略超 xuan，但跨日后低于 xuan。
- 不能把 `repair104` 作为 enforce 参数。
- 它仍然有价值，因为它展示了收益/闭合率 frontier。

## Repair Ceiling Frontier

从 `profit095 + repair104` 的 rows 重新按 pair cost threshold 切分：

| threshold | 04-30/05-01 closed/fill | 04-30/05-01 surplus/size | 05-01 closed/fill | 05-01 surplus/size |
|---:|---:|---:|---:|---:|
| `0.95` | `39.57%` | `0.05545` | `40.77%` | `0.05851` |
| `0.99` | `46.12%` | `0.05071` | `47.59%` | `0.05328` |
| `1.01` | `52.66%` | `0.04402` | `53.91%` | `0.04690` |
| `1.02` | `57.24%` | `0.03921` | `57.90%` | `0.04262` |
| `1.03` | `62.31%` | `0.03338` | `63.06%` | `0.03658` |
| `1.04` | `81.36%` | `0.01644` | `80.20%` | `0.02083` |

裁决：

- 真正超额收益来自 `pair_cost <= 1.03` 以内。
- `1.04` 修复带来大量闭合，但吞掉收益。
- 如果实盘能承受更低闭合率并有强 residual/merge 管理，`repair103` 比 `repair104` 更值得 shadow。
- 如果实盘必须高闭合率，当前策略还需要更强 open gate，而不是继续放宽 repair。

## Late Midprice Filter

从 04-30/05-01 rows 中发现：

```text
180s <= offset < 240s
0.55 <= first_price < 0.70
profit095 + repair104
```

两日表现：

| metric | value |
|---|---:|
| first fills | `52` |
| closed/fill | `84.62%` |
| surplus/size | `0.034239` |
| pair p50 | `0.95` |

但 04-29 直接运行：

| metric | value |
|---|---:|
| first fills | `97` |
| closed/fill | `90.72%` |
| surplus/size | `0.013138` |

裁决：

- 这个 filter 有潜力，但跨日不稳定。
- 它不能单独成为主策略。
- 后续应作为 feature，而不是 hard gate。

## 策略建议

进入开发/回测阶段应同时实现两条 shadow：

### Alpha Mode

```text
open:
  high-side best bid maker-first
  first_fill_timeout=20s
  base_clip=60

completion:
  30s@0.95

repair:
  if no cheap completion:
    repair until 90s@1.03
```

目标：

- `surplus/size > xuan`
- 接受较低 close rate
- 用小 clip 控制残差

### Balanced Mode

```text
open:
  same as alpha

completion:
  30s@0.95

repair:
  repair until 90s@1.04
```

目标：

- close rate 接近 xuan
- 检查收益是否能通过更强 open gate 恢复

## 下一步

1. 把 `xuan_surpass_candidate_v1.json` 接入 shadow/backtest runner。
2. 对 `alpha` 与 `balanced` 同时输出：
   - first fill rate
   - closed/fill
   - 30s/fill
   - surplus/size
   - unclosed residual count
   - pair cost distribution
3. 用我方 dry-run/execution truth 检查 maker bid fill proxy 是否成立。
4. 如果 `alpha` 的真实成交率成立，再研究 residual 风控；如果不成立，转向 bounded taker/更高价格的 first leg。
5. 如果 `balanced` 收益长期低于 xuan，不再靠放宽 repair 解决，必须增强 open gate。

## 当前裁决

可以开始实现超越型策略 shadow。

不能直接 enforce。

最值得推进的是：

- `profit095 + repair103` 作为 alpha shadow。
- `profit095 + repair104` 作为 balanced shadow。
- late-midprice 只作为 explain feature，不作为硬规则。

## 残仓风险重估

上面的 `surplus/size` 只统计已闭合 pair。若把未闭合 first-leg 残仓按最终结算真值计入，当前候选仍不可实盘。

### 05-01 broad run

| mode | fills | closed | residual | closed surplus | residual settle PnL | total settle PnL | settle ROI |
|---|---:|---:|---:|---:|---:|---:|---:|
| `repair103` | `595` | `472` | `123` | `$361.62` | `-$1606.20` | `-$1244.58` | `-7.37%` |
| `repair104` | `601` | `482` | `119` | `$316.78` | `-$1629.30` | `-$1312.52` | `-7.61%` |
| `repair110` | `681` | `607` | `74` | `-$34.78` | `-$1233.45` | `-$1268.23` | `-6.16%` |

解释：

- `repair103/104` 的已闭合 pair 有正收益，但残仓结算后整体亏损。
- `repair110` 提高闭合率，但闭合 pair 本身变成负收益，仍不能解决问题。
- 当前最大风险不是 pair-cost 不够低，而是未闭合残仓大多是 loser residual。

### 残仓来源

在 05-01 `repair104` 中：

| condition | closed | unclosed loser | unclosed winner |
|---|---:|---:|---:|
| `min_pair_cost_30s <= 0.95` | `100%` | `0%` | `0%` |
| `0.99 < min_pair_cost_30s <= 1.04` | `69.8%` | `25.4%` | `4.7%` |
| `min_pair_cost_30s missing/high` | `45.0%` | `42.3%` | `12.6%` |

强结论：

- `min_pair_cost_30s <= 0.95` 是安全/盈利状态。
- `min_pair_cost_30s > 0.99` 或缺失是残仓危险状态。
- 30 秒后没有 cheap-window 的仓位不能继续等待，也不能只靠温和 repair；必须进入 emergency exit。

## V1.1 风控修正

下一版策略必须把“残仓按结算计入”作为主指标，不能只看已闭合 pair。

新增硬规则：

```text
if first leg filled and not paired by 30s:
  if min_pair_cost_30s <= 0.95:
    allow slow continuation with bounded timer
  else:
    enter EmergencyExit
```

`EmergencyExit` 可选动作：

1. `aggressive pair repair`
   - 允许更高 pair_cost，但必须证明比 loser residual 期望更好。
2. `sell first leg`
   - 如果盘口允许，以卖出 first leg 止损，避免二元结算归零。
3. `hard no-reentry`
   - 当天/该市场不再开新 tranche，直到残仓清掉。

新增验收门槛：

| metric | threshold |
|---|---:|
| residual_count / first_fills | `<5%` |
| residual_loser_count / first_fills | `<2%` |
| residual_adjusted_pnl | `>0` |
| residual_adjusted_roi_on_total_spend | `>0` |
| no-cheap-window open continuation | `0` |

当前 `repair103/104/110` 全部未通过这些门槛。
