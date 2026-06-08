# Xuan Surpass Iteration - 2026-05-03

## 当前裁决

盈利是唯一硬目标。按 market-level trade PnL 口径，xuan 在本地 `2026-04-27` 至 `2026-05-01` replay 中：

- trade PnL: `+$14419.08`
- ROI: `+2.09%`
- weighted pair cost: `0.980324`
- paired profit: `+$13761.04`

所以我们的策略候选必须直接对标这个目标，而不是只看 30s 配对率、闭合率或单 tranche surplus。

## 本轮新增实验

### 1. Naive two-sided maker-grid

假设：xuan 的 edge 来自同时在 YES/NO best bid 上吃卖方流动性。

05-01 结果，`clip=60`、`bid_sum<=0.99`、`imbalance<=180`：

| metric | value |
|---|---:|
| active markets | `281` |
| fills | `3237` |
| trade PnL | `-$4649.40` |
| ROI | `-4.78%` |
| weighted pair cost | `1.035467` |
| residual-is-winner | `36.89%` |

结论：naive 双边 maker 是错的。即使每个时点的 bid-sum 看似便宜，跨时间累积后 YES/NO 均价会漂移，最终 market-level pair cost 变成 `>1`。更严重的是，残仓 winner rate 很低。

### 2. Strict bid-sum two-sided maker-grid

05-01 结果，`clip=60`、`bid_sum<=0.95`、`imbalance<=60`：

| metric | value |
|---|---:|
| active markets | `27` |
| fills | `30` |
| trade PnL | `-$273.60` |
| ROI | `-33.63%` |
| weighted pair cost | `0.775` |
| residual-is-winner | `26.92%` |

结论：只压低 bid-sum 会变成“买到单边便宜残仓”，不是套利。没有 completion controller 和 winner-proxy，低 pair cost 信号没有意义。

### 3. Pair-gated maker-maker

假设：先双边挂首腿，任一侧成交后取消同侧，只用 opposite maker 补到 `pair_target`。

05-01 结果，`pair_target=0.98`：

| metric | value |
|---|---:|
| active markets | `281` |
| fills | `501` |
| trade PnL | `-$406.80` |
| ROI | `-2.77%` |
| weighted pair cost | `0.991824` |
| paired profit | `+$54.45` |
| residual PnL | `-$461.25` |
| residual-is-winner | `45.52%` |

结论：pair-gated maker-maker 能把 paired loss 控住，但残仓仍然负。原因是首腿没有 winner-proxy，未补上的腿变成负期望 residual。

### 4. Winner-proxy completion-first

当前唯一正向候选来自高 winner-proxy：

```text
first_price in [0.80, 0.90)
first_fill_delay <= 2s
completion pair ceiling 0.95 within 30s
no-cheap-window -> repair/exit
```

04-30/05-01 两天，`base_clip=60`：

| metric | value |
|---|---:|
| first fills | `111` |
| closed | `99` |
| exits | `12` |
| first-winner rate | `86.49%` |
| weighted pair cost closed | `0.966761` |
| net PnL | `+$7.33` |
| ROI | `+0.22%` |

5日全窗口，`base_clip=60`：

| metric | value |
|---|---:|
| first fills | `262` |
| closed | `238` |
| exits | `24` |
| first-winner rate | `88.55%` |
| weighted pair cost closed | `0.973712` |
| net PnL | `+$16.68` |
| ROI | `+0.21%` |

按日看并不稳定：

| day | PnL |
|---|---:|
| 2026-04-27 | `-$24.69` |
| 2026-04-28 | `+$4.65` |
| 2026-04-29 | `+$29.40` |
| 2026-04-30 | `-$6.74` |
| 2026-05-01 | `+$14.06` |

裁决：这是可复用 edge，但不是主策略。它证明高 winner-proxy 能带来正期望，但收益规模和稳定性都远低于 xuan。

退出诊断：

| day | exits | exit PnL | if hold to settle |
|---|---:|---:|---:|
| 2026-04-27 | `6` | `-$55.00` | `-$29.10` |
| 2026-04-28 | `2` | `-$17.40` | `-$21.90` |
| 2026-04-29 | `4` | `-$10.80` | `+$17.40` |
| 2026-04-30 | `5` | `-$54.58` | `-$66.90` |
| 2026-05-01 | `7` | `-$42.30` | `-$55.50` |

裁决：固定 sell-exit 不是最优；残仓是否卖出要由 residual winner-proxy / expected settlement value 决定。

### 5. Residual Exit Classifier

在 `0.80-0.90 / base_clip=60` 的 5日结果上，只针对已经触发 exit 的 24 个残仓做 hold-vs-exit 搜索。该搜索只使用 exit 决策前可见特征，不使用 live 不可见的 `winner_side`。

| policy | net PnL | min day | positive days | held exits |
|---|---:|---:|---:|---:|
| always exit | `+$16.68` | `-$24.69` | `3/5` | `0/24` |
| always hold | `+$40.76` | `-$19.06` | `4/5` | `24/24` |
| hold `min30 0.95-1.01 OR offset 120-180` | `+$53.36` | `-$19.06` | `3/5` | `22/24` |
| hold `first_price 0.84-0.86 OR offset 120-150` | `+$86.60` | `-$1.13` | `4/5` | `10/24` |

当前最佳稳定性规则：

```text
hold residual if 0.84 <= first_price < 0.86
OR 120s <= candidate_offset_s < 150s
otherwise sell/exit
```

裁决：

- 残仓一律卖出不是最优。
- 残仓一律持有也不是最优。
- 残仓处理需要 classifier，但当前样本只有 24 个 exit rows，必须标记为 shadow-only。
- 这个规则已经接入 `--residual-hold-policy price_084_086_or_offset_120_150`。

05-01 单日接线验证：

| metric | value |
|---|---:|
| first fills | `59` |
| closed | `52` |
| exits | `4` |
| residuals held | `3` |
| residual winner / loser | `2 / 1` |
| net PnL | `+$14.96` |
| ROI | `+0.85%` |

04-30/05-01 两天，`base_clip=120`：

| metric | value |
|---|---:|
| first fills | `64` |
| closed | `57` |
| exits | `7` |
| first-winner rate | `89.06%` |
| weighted pair cost closed | `0.966520` |
| net PnL | `+$18.60` |
| ROI | `+0.48%` |

放宽到 `first_price in [0.75, 0.90)` 后转负：

| metric | value |
|---|---:|
| first fills | `176` |
| first-winner rate | `83.52%` |
| weighted pair cost closed | `0.970015` |
| net PnL | `-$20.84` |
| ROI | `-0.40%` |

结论：edge 很窄。`0.80-0.90` 是当前可盈利区间，不能为了机会频率放宽到 `0.75`。

### 6. Market-Level Edge Feature Reassessment

新增脚本：`scripts/analyze_xuan_market_edge_features.py`。

输入：`xuan_market_pnl_truth_0427_0501` 的 market-level PnL truth，加 replay L1/flow 特征。它只读 SQLite，不读 raw，不使用 winner_side 作为开仓特征。

核心发现：`0.80-0.90` 高价策略不是 xuan 主利润来源，只是小型 sidecar。xuan 主体利润更接近：

```text
早段或中价位 first leg
+ first public trade 相对同刻 L1 bid 有执行折价
+ 后续 completion controller
```

关键 policy probes：

| policy | markets | selected | PnL | ROI | w-pair cost | good <=0.98 | bad >=1.02 |
|---|---:|---:|---:|---:|---:|---:|---:|
| all xuan markets | `947` | `100.00%` | `+$14419.08` | `2.09%` | `0.980324` | `54.17%` | `13.52%` |
| `exec_edge_to_bid >= 2c` | `363` | `38.33%` | `+$7422.04` | `2.77%` | `0.973316` | `62.26%` | `10.47%` |
| `exec_edge_to_bid >= 0.5c` | `456` | `48.15%` | `+$8985.62` | `2.70%` | `0.973940` | `62.28%` | `10.31%` |
| `price 0.40-0.55` | `513` | `54.17%` | `+$9442.43` | `2.37%` | `0.977665` | `55.95%` | `13.26%` |
| `price 0.40-0.55 AND edge >=0.5c` | `260` | `27.46%` | `+$5944.53` | `2.98%` | `0.971045` | `65.38%` | `9.62%` |
| `offset <30s AND edge >=0.5c` | `344` | `36.33%` | `+$7746.12` | `2.85%` | `0.973302` | `62.21%` | `9.59%` |
| `first_price 0.80-0.90` | `20` | `2.11%` | `+$40.68` | `0.89%` | `0.994794` | `50.00%` | `5.00%` |

裁决：

- 当前 `0.80-0.90` 策略应降级为 high-confidence sidecar，不是主策略。
- 真正值得主攻的是 `mid-price execution-edge`：它在 xuan 自己的市场级 PnL 中同时提升 ROI、降低 pair cost、降低坏 pair-cost 率。
- `exec_edge_to_bid` 可能混有 timestamp / maker queue / public trade 延迟误差，不能直接 enforce，但它是目前最强的“超越 xuan”研究方向。
- 如果我方能在这些状态获得相同或更好的队列成交，理论上可以通过放大 clip 和更严格地跳过弱状态，做到高于 xuan 全样本 ROI。

### 7. Mid-Price Edge 直接复制失败

为了验证 `mid-price execution-edge` 是否能被当前执行模型直接吃到，新增 `--first-bid-improvement` 参数，测试在 best bid 或 best bid 下方挂首腿。

05-01，`first_price 0.40-0.55`，`fill_timeout=2s`，`base_clip=60`：

| first bid improvement | first fills | closed | exits | first winner | w-pair cost | surplus | exit pnl | net PnL | ROI |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `0` | `42` | `31` | `11` | `52.38%` | `0.975165` | `+$26.82` | `-$65.96` | `-$39.13` | `-3.13%` |
| `0.005` | `27` | `20` | `7` | `44.44%` | `0.975007` | `+$17.99` | `-$39.92` | `-$21.93` | `-2.62%` |
| `0.020` | `14` | `9` | `5` | `57.14%` | `0.958536` | `+$27.37` | `-$112.03` | `-$84.67` | `-10.28%` |

裁决：

- `mid-price execution-edge` 在 xuan 账户上是真实高 ROI 子集，但不能被我方当前 high-side maker proxy 直接复刻。
- 单纯把挂价下移只会降低 fill rate，并不能解决首腿方向和失败退出问题。
- 这强烈暗示 xuan 的主 edge 至少包含一项我们尚未复制的机制：更准的首腿方向、提前排队、撤挂时机、或 public timestamp 与真实挂单时点差。
- 研发主线不能把 `mid-price` 直接上线；必须先构建“可成交 detector”，否则它比高价 sidecar 更危险。

### 8. Queue Timing Audit：同价提前排队假设基本不成立

新增脚本：`scripts/analyze_xuan_queue_timing.py`。

问题：xuan 的 `mid-price edge` 是否只是“提前在同价挂单排队”，我们能不能照着提前挂？

审计对象：`price 0.40-0.55` 且具备 L2 edge 的 `696` 条 xuan tranche，向前看 30 秒 L2 top-5 bid。

| cohort | n | L2 coverage | same-price visible | bid>=price visible | same p50 s | same >=5s | bid>= p50 s | cum bid>price p50 | size p50 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| mid-price edge | `696` | `100%` | `7.04%` | `30.89%` | `0.0s` | `1.29%` | `0.0s` | `0.0` | `113.35` |
| `l2_edge>=3c` subset | `208` | `100%` | `8.65%` | `73.08%` | `0.0s` | `2.40%` | `2.58s` | `526.47` | `116.15` |
| `slow_profit_lt95` subset | `77` | `100%` | `7.79%` | `31.17%` | `0.0s` | `1.30%` | `0.0s` | `0.0` | `126.60` |

Public sell match 进一步支持这个判断：

| cohort | same-price sell within 5s | same-price sell within 1s | sweep-through possible |
|---|---:|---:|---:|
| mid-price edge | `3.02%` | `1.72%` | `0.14%` |
| `l2_edge>=3c` subset | `0.00%` | `0.00%` | `0.00%` |
| `slow_profit_lt95` subset | `5.19%` | `3.90%` | `0.00%` |

裁决：

- “提前在同价挂单排队”不是主解释。同价档在成交前可见率只有 `7%`，超过 5 秒的可排队窗口只有 `1.3%`。
- `l2_edge>=3c` 的 `bid>=price` 可见率很高，说明很多 xuan 成交价低于当时更高 bid 档；这更像 public timestamp/聚合、扫单穿透、更深档 maker、或盘口瞬变，而不是我们简单挂同价就能复制。
- public sell 同价匹配也很低，尤其 `l2_edge>=3c` 为 `0%`；所以不能把这些成交简单解释成“公开 SELL flow 打到同价 maker”。
- 这也解释了为什么 `first_bid_improvement` 降价回测没有转正：低价挂单本身并不会自动获得 xuan 那种成交时机。
- 结论上，xuan 主引擎需要进一步拆成两个问题：`direction/timing detector` 和 `queue/fillability model`。没有这两个，mid-price edge 只能作为研究目标，不能作为实盘策略。

### 9. Timestamp / Post-Move Sensitivity：xuan 像是买在盘口上移前后

队列审计加入 `timestamp shift` 后出现一个强信号：如果把观察点移动到 xuan 首腿成交后 `+1s`，`bid>=xuan_price` 的可见率大幅上升。

| cohort | shift | same-price visible | bid>=price visible |
|---|---:|---:|---:|
| mid-price edge | `-1s` | `6.03%` | `26.44%` |
| mid-price edge | `0s` | `7.04%` | `30.89%` |
| mid-price edge | `+1s` | `15.80%` | `70.83%` |
| mid-price edge | `+3s` | `13.65%` | `70.98%` |
| `l2_edge>=3c` | `0s` | `8.65%` | `73.08%` |
| `l2_edge>=3c` | `+1s` | `9.13%` | `89.90%` |
| `slow_profit_lt95` | `0s` | `7.79%` | `31.17%` |
| `slow_profit_lt95` | `+1s` | `12.99%` | `79.22%` |

这个 post-move label 本身不是 live 信号，但它解释了 xuan 的 edge 形态：

- `post+1s bid>=xuan_price` 的样本：first-winner rate `58.42%`，pair p50 `0.975938`。
- `post+1s not-gte` 的样本：first-winner rate `42.86%`，pair p50 `1.023074`。
- `l2_edge>=3c AND post+1s gte`：first-winner rate `59.36%`，pair p50 `0.960000`。

初步可提前观察的候选特征：

| feature bucket | n | post+1s gte | first-winner | pair p50 | verdict |
|---|---:|---:|---:|---:|---|
| `l2_edge>=3c` | `208` | `89.90%` | `59.13%` | `0.968397` | 强正向 |
| `first_offset 30-60s` | `131` | `78.63%` | `64.89%` | `0.981714` | 正向 |
| `first_spread<=1` | `354` | `77.12%` | `56.78%` | `0.986880` | 正向 |
| `first_price 50-55c` | `475` | `72.21%` | `55.79%` | `0.990718` | 中等正向 |
| `l2_edge 0.5c-3c` | `488` | `62.70%` | `51.64%` | `1.004183` | 负向 |
| `first_offset 120-180s` | `97` | `62.89%` | `42.27%` | `1.010000` | 负向 |
| `first_price 40-45c` | `53` | `64.15%` | `41.51%` | `1.014589` | 负向 |

裁决：

- xuan 主引擎更像 “pre-move / during-move 捕捉” 而不是静态 pair target。
- `l2_edge>=3c` 不是直接成交规则，而是“盘口即将或刚刚重定价”的强代理。
- 下一版可执行 detector 应该优先找 `l2_edge>=3c + 30-60s + tight spread` 的组合，而不是泛化到所有 0.40-0.55 中价位。
- 仍然不能跳过成交验证：这些信号解释 xuan 为什么赚钱，但我们要证明自己能以可接受 fill rate 进入。

### 10. Post-Move Signal Formalization：强信号成立，但大部分不是可直接复制的 up-cross

新增脚本：`scripts/analyze_xuan_post_move_signal.py`。

输入：`xuan_queue_timing_rows.csv`。标签固定为 `shift_1000_bid_gte_price_in_top5`，即 xuan 首腿成交后 `+1s`，L2 top-5 bid 是否仍然/已经高于 xuan 成交价。这个标签只用于解释和研究，不能直接进入 live 策略。

全样本基线：

| metric | value |
|---|---:|
| samples | `696` |
| post+1s bid>=price | `70.83%` |
| first-winner rate | `53.88%` |
| slow-profit rate | `11.06%` |
| slow-bad rate | `5.89%` |
| pair cost p50 | `0.992951` |

最强规则探针：

| rule | n | selected | post+1s gte | first-winner | pair cost p50 |
|---|---:|---:|---:|---:|---:|
| `l2_edge>=3c AND offset<60s AND spread<=1` | `108` | `15.52%` | `96.30%` | `67.59%` | `0.940135` |
| `l2_edge>=3c AND offset 30-60s` | `44` | `6.32%` | `95.45%` | `72.73%` | `0.940135` |
| `l2_edge>=3c AND spread<=1` | `140` | `20.11%` | `95.00%` | `61.43%` | `0.958902` |
| `avoid weak mid` | `174` | `25.00%` | `90.23%` | `64.94%` | `0.966616` |
| `l2_edge>=3c` | `208` | `29.89%` | `89.90%` | `59.13%` | `0.968360` |
| all | `696` | `100.00%` | `70.83%` | `53.88%` | `0.992951` |

按日稳定性：

| rule | day coverage | observation |
|---|---:|---|
| `l2_edge>=3c` | `5/5` | 每天都提高 post+1s 命中，并降低 pair cost p50；4/28 至 5/1 的 p50 改善约 `2.0c` 到 `6.7c`。 |
| `l2_edge>=3c AND spread<=1` | `5/5` | 样本更少但改善更强；4/30、5/1 的 post+1s 命中分别 `95.24%`、`100%`。 |
| `l2_edge>=3c AND offset<60s AND spread<=1` | `2/5` 可用 | 4/30、5/1 很强，但早期样本不足，不能直接作为 enforce gate。 |

关键拆解：把 `post+1s bid>=price` 分成“成交前 1s 尚未满足、成交后上穿”的 `upcross`，以及“成交前 1s 已经满足”的 `already_gte`。

| cohort | transition | n | rate | first-winner | pair cost p50 |
|---|---|---:|---:|---:|---:|
| all | `upcross` | `340` | `48.85%` | `55.88%` | `0.977218` |
| all | `already_gte` | `153` | `21.98%` | `64.05%` | `0.970000` |
| all | `never_gte` | `172` | `24.71%` | `40.70%` | `1.023000` |
| `l2_edge>=3c` | `upcross` | `69` | `33.17%` | `44.93%` | `0.967773` |
| `l2_edge>=3c` | `already_gte` | `118` | `56.73%` | `67.80%` | `0.954006` |
| strict rule | `upcross` | `13` | `12.04%` | `46.15%` | `0.972453` |
| strict rule | `already_gte` | `91` | `84.26%` | `72.53%` | `0.938720` |

裁决：

- `l2_edge>=3c` 是真实强解释变量，不是单日噪声。
- 但最强 strict rule 的主要收益来自 `already_gte`，即 xuan 成交前 1 秒 L2 bid 已经高于其成交价。这更像队列/时间戳/撮合同步优势，不是我们用普通 live 策略能直接复制的 up-cross。
- 真正可学习的 live 子问题是 `upcross`：成交前还没有高于入场价，但 1 秒后上穿。这个子集仍有正 pair cost p50 `0.9772`，但 winner rate 和机会质量弱于 `already_gte`。
- 因此下一步不能把 strict rule 直接写成交易 gate；必须把研究拆成两条：
  - `replicable upcross predictor`：用成交前 L1/L2/flow 预测 1 秒内 bid 上穿，适合我方执行。
  - `non-replicable xuan structural edge`：队列、时间戳、撮合同步、隐藏流动性或 API 延迟优势，只能作为解释和风险上限，不能直接计入我方预期收益。

### 11. All-Market Upcross Predictor：xuan 成交锚定规则不能直接泛化

新增脚本：`scripts/analyze_btc5m_upcross_predictor.py`。

输入：全市场 BTC 5m replay L1/trades，不再以 xuan 成交为采样锚点。最近两日窗口 `2026-04-30`、`2026-05-01`：

| metric | value |
|---|---:|
| sampled states | `294481` |
| markets | `578` |
| label `bid_jump_1s>=3c` baseline | `7.14%` |
| label `bid_jump_1s>=2c` baseline | `11.83%` |
| `future_bid>=current_ask` baseline | `21.84%` |
| `future_bid>=current_bid` baseline | `76.84%` |

规则探针：

| rule | n | selected | jump>=3c | future bid>=ask | interpretation |
|---|---:|---:|---:|---:|---|
| `prev_bid_delta>=2c AND bid 40-55 AND spread<=1` | `6963` | `2.36%` | `14.03%` | `36.44%` | 最强可复制短动量 |
| `prev_bid_delta>=2c OR flow>=300, bid 40-55, spread<=1` | `23408` | `7.95%` | `8.81%` | `25.63%` | 机会多但 edge 稀释 |
| `flow>=300, bid 40-55, spread<=1` | `19575` | `6.65%` | `7.86%` | `23.49%` | flow 单独较弱 |
| all | `294481` | `100.00%` | `7.14%` | `21.84%` | 基线 |
| `bid 40-55 AND spread<=1` | `62143` | `21.10%` | `6.73%` | `21.56%` | 低于基线 |
| `offset<60 AND bid 40-55 AND spread<=1` | `26237` | `8.91%` | `5.01%` | `19.08%` | 明显低于基线 |

按日稳定性：

| rule | 2026-04-30 jump>=3c | 2026-05-01 jump>=3c | daily lift |
|---|---:|---:|---|
| all | `7.47%` | `6.81%` | 基线 |
| `prev_bid_delta>=2c AND bid 40-55 AND spread<=1` | `14.50%` | `13.49%` | 两天都约 `+6.7pct` 到 `+7.0pct` |
| `offset<60 AND bid 40-55 AND spread<=1` | `5.46%` | `4.56%` | 两天都负向 |

裁决：

- xuan 的 `l2_edge>=3c + offset<60 + spread<=1` 强规则不能直接泛化为全市场 open gate。它在 xuan 样本里很强，但全市场 `offset<60 + mid-price + tight spread` 对 1 秒上跳反而是负向。
- 当前唯一明确可复制的公开盘面 upcross 信号是短动量：`prev_bid_delta_1s>=2c`。它能把 `jump>=3c` 从 `7.14%` 提高到 `14.03%`，把 `future_bid>=ask` 从 `21.84%` 提高到 `36.44%`。
- 但这个信号仍不足以直接 taker 买入：`future_bid>=ask` 只有 `36.44%`，如果没有 completion edge 和清残机制，单腿方向交易仍大概率不够。
- 这进一步说明 xuan 的超额收益很可能分成两部分：
  - 一部分是可复制的短动量/盘口跳变；
  - 另一部分是他特有的成交结构、队列位置或时间戳优势。
- 我们要超越 xuan，不能把不可复制部分计入策略预期；必须用可复制短动量做 open gate，再用 pair-completion 和 residual classifier 降低尾部亏损。

### 12. Upcross L1 Taker Proxy：短动量不是稳定正收益主引擎

新增脚本：`scripts/backtest_btc5m_upcross_l1_taker.py`。

目的：快速测试全市场 upcross 信号是否有足够盈利上限。该脚本是轻量 proxy：

- first leg 假设按当前 L1 ask 立即成交；
- completion 假设未来 L1 opposite ask 可成交；
- 不做 L2 sweep、不建模排队、不计 fees/rebates；
- 因此它偏乐观，只适合快速否定或保留方向。

先测试 broad gate：

```text
best_prev_bid_momentum side
prev_bid_delta_1s >= 2c
0.40 <= side_bid < 0.55
spread <= 1 tick
completion pair ceiling 0.95 / 30s
repair pair ceiling 1.04 / 60s
```

最近两日 `2026-04-30..2026-05-01`：

| metric | value |
|---|---:|
| trades | `1132` |
| completed | `897` |
| residual | `235` |
| completion rate | `79.24%` |
| first-winner rate | `46.82%` |
| PnL | `-$750.0` |
| ROI | `-2.29%` |
| pair cost p50 | `0.95` |

路径拆解：

| path | n | first-winner | PnL |
|---|---:|---:|---:|
| completion | `585` | `54.87%` | `+$2633.4` |
| repair | `312` | `48.40%` | `-$65.4` |
| residual_settle | `235` | `24.68%` | `-$3318.0` |

裁决：闭合路径赚钱，但残仓质量极差，完全吃掉利润。这说明短动量能改善“补腿窗口”，但不能保证首腿方向正确。

再测试一个同样本筛出的窄 gate：

```text
30s <= offset < 60s
prev_bid_delta_1s >= 4c
0.40 <= side_bid < 0.55
spread <= 1 tick
```

最近两日结果：

| day | trades | completed | residual | PnL | ROI |
|---|---:|---:|---:|---:|---:|
| 2026-04-30 | `58` | `45` | `13` | `-$25.2` | `-1.51%` |
| 2026-05-01 | `62` | `51` | `11` | `+$126.0` | `+6.89%` |
| total | `120` | `96` | `24` | `+$100.8` | `+2.88%` |

但 5 日验证失败：

| day | trades | completed | residual | PnL | ROI |
|---|---:|---:|---:|---:|---:|
| 2026-04-27 | `41` | `32` | `9` | `-$130.8` | `-10.85%` |
| 2026-04-28 | `81` | `57` | `24` | `-$186.0` | `-7.88%` |
| 2026-04-29 | `75` | `64` | `11` | `-$139.2` | `-6.41%` |
| 2026-04-30 | `58` | `45` | `13` | `-$25.2` | `-1.51%` |
| 2026-05-01 | `62` | `51` | `11` | `+$126.0` | `+6.89%` |
| total | `317` | `249` | `68` | `-$355.2` | `-3.84%` |

5 日路径拆解：

| path | n | first-winner | PnL |
|---|---:|---:|---:|
| completion | `156` | `57.05%` | `+$630.6` |
| repair | `93` | `60.22%` | `-$36.0` |
| residual_settle | `68` | `25.00%` | `-$949.8` |

裁决：

- `prev_bid_delta` 短动量可以产生局部正样本，但不是稳定主策略。
- 只要 residual first-winner 只有约 `25%`，任何 closed-pair surplus 都会被尾部残仓吞掉。
- 这个结果再次逼近 xuan 的关键：他不只是抓到补腿窗口，还必须显著降低“未补腿时的残仓 loser 概率”，或者拥有我们没有的 fill/queue 优势让残仓比例更低。
- `2026-05-01` 的正结果可以标记为 post-outage regime 观察项，但不能按 5 日全窗口直接进入策略。

### 13. Execution Discount Sensitivity：1-2c 折价是生死线

在同一个短动量窄 gate 上，测试不同首腿成交价假设：

```text
30s <= offset < 60s
prev_bid_delta_1s >= 4c
0.40 <= side_bid < 0.55
spread <= 1 tick
completion pair ceiling 0.95 / 30s
repair pair ceiling 1.04 / 60s
```

5 日结果：

| first price assumption | trades | completed | residual | pair p50 | PnL | ROI | daily profile |
|---|---:|---:|---:|---:|---:|---:|---|
| current ask | `317` | `249` | `68` | `0.95` | `-$355.2` | `-3.84%` | 仅 5/1 正 |
| current bid | `318` | `256` | `62` | `0.95` | `-$115.2` | `-1.27%` | 仅 5/1 正 |
| bid - 1c | `318` | `259` | `59` | `0.94` | `+$49.8` | `+0.56%` | 4/30、5/1 正 |
| bid - 2c | `319` | `270` | `49` | `0.94` | `+$226.8` | `+2.60%` | 3/5 正，4/27、4/28 小亏 |

这说明：

- 普通 taker 追 ask 必然不行；
- 只做到 best bid 仍不够；
- 稳定拿到 `bid-1c` 到 `bid-2c` 的 maker/队列折价，才开始接近或超过 xuan 的 5 日 ROI；
- `bid-2c` 的 5 日 ROI `2.60%` 已经高于 xuan 本地 5 日 market-level ROI `2.09%`，但它仍是上限假设，不是已证明可执行策略。

再看 xuan 自己的 strict 子集成交价：

| xuan cohort | n | `latest_bid - xuan_price` p50 | `l2_vwap - xuan_price` p50 | pair p50 |
|---|---:|---:|---:|---:|
| all mid-price rows | `696` | `-1.08c` | `1.86c` | `0.992994` |
| `l2_edge>=3c` | `208` | `3.11c` | `5.74c` | `0.968397` |
| strict rule | `108` | `6.84c` | `8.36c` | `0.940270` |
| already_gte | `153` | `5.00c` | `6.90c` | `0.970000` |
| strict already_gte | `91` | `8.23c` | `9.68c` | `0.938720` |

裁决：

- xuan 最强子集里体现出来的 execution discount 远高于我们策略转正所需的 `1-2c`，这解释了为什么他的 pair cost 能做到 `0.98` 甚至更低。
- 但这个 discount 很可能混有 public timestamp、撮合同步、L2 更新延迟、队列位置等结构因素；不能假设我方能拿到。
- 当前最可执行的研发目标不是“预测 winner”，而是先证明我方在短动量窗口能实际拿到 `>=1c` 的 maker 折价，且不会显著降低 fill rate。
- 若 dry-run/实盘 shadow 只能拿到 ask 或 best bid，短动量主线应继续拒绝；若能拿到 `bid-1c` 以上成交质量，再进入完整 L2 maker-first 回测。

### 14. Maker Fillability Audit：折价可达性不足，且 fillability 不能解释全部 regime

新增脚本：`scripts/analyze_btc5m_maker_fillability.py`。

目的：对上一节的 `bid-1c/bid-2c` 盈利上限做成交可行性审计。仍使用同一个短动量 gate：

```text
30s <= offset < 60s
prev_bid_delta_1s >= 4c
0.40 <= side_bid < 0.55
spread <= 1 tick
clip = 60
```

fill 口径：

- `optimistic`：30 秒内 public SELL flow 打到 `order_price`，且累计 size >= clip；忽略同价排队。
- `queue_full`：在 optimistic 基础上，要求累计 size >= clip + 可见同价队列；仍不把高价队列完全计入，因此仍偏乐观。
- `queue_same p50`：下单价位已有同价可见队列中位数。

5/1 单日：

| order price | candidates | opt 2s | full 2s | opt 5s | full 5s | opt 30s | full 30s | queue_same p50 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| bid | `113` | `4.42%` | `0.00%` | `11.50%` | `1.77%` | `45.13%` | `22.12%` | `126.66` |
| bid - 1c | `113` | `0.89%` | `0.00%` | `6.19%` | `1.77%` | `36.28%` | `12.39%` | `147.02` |
| bid - 2c | `113` | `0.89%` | `0.89%` | `4.42%` | `1.77%` | `30.09%` | `12.39%` | `109.25` |

4/28、4/30、5/1 的 30 秒 fillability 对比：

| day | candidates | bid opt/full | bid-1c opt/full | bid-2c opt/full |
|---|---:|---:|---:|---:|
| 4/28 | `157` | `41.40% / 17.83%` | `36.94% / 19.75%` | `34.39% / 16.56%` |
| 4/30 | `141` | `48.23% / 24.82%` | `40.43% / 19.86%` | `34.04% / 17.73%` |
| 5/1 | `113` | `45.13% / 22.12%` | `36.28% / 12.39%` | `30.09% / 12.39%` |

裁决：

- `bid-2c` 的理论 ROI 很高，但 30 秒 optimistic fill 也只有约 `30%`，queue-aware 只有约 `12%`；这不是可以直接放大的高频主引擎。
- 2 秒、5 秒成交率尤其低，说明它不像 xuan 那样“几秒内自然完成首腿”；如果我们挂很深，机会成本和漏单会很高。
- 更重要的是，fillability 本身不能解释日内/日间 PnL regime：4/28 的 fillability 不低于 5/1，但 PnL 是负的。这说明被成交本身可能有 adverse selection，或者 open gate 对方向/残仓质量仍不够。
- 所以当前短动量路线需要同时满足两个 enforce 前置：
  - 自己的 maker shadow 证明能拿到 `>=1c` 折价且 fill rate 可接受；
  - fill 后未补腿残仓的 winner-proxy 显著改善，不能继续只有约 `25%`。
- 没有这两个条件，`bid-1c/bid-2c` 只能作为理论上限，不是实盘策略。

### 15. Fill-Triggered Maker Proxy：第一条跨日正收益候选来自“快撤”，不是更深折价

新增脚本：`scripts/backtest_btc5m_maker_fill_triggered.py`。

目的：修正上一节的最大偏差，不再假设 `bid/bid-1c/bid-2c` 一定成交；只有当 public SELL flow 在限定时间内打到我方 maker 价，并且覆盖可见同价队列后，才认为首腿成交。

第一轮失败基线：

```text
30s <= offset < 60s
prev_bid_delta_1s >= 4c
0.40 <= side_bid < 0.55
spread <= 1 tick
queue_full
first_fill_timeout = 30s
clip = 60
```

5 天结果：

| mode | attempts | fills | fill rate | completed | residual | PnL | ROI filled | positive days |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| bid queue_full | `476` | `110` | `23.11%` | `72` | `38` | `-$388.80` | `-12.61%` | `0/5` |
| bid-1c queue_full | `476` | `92` | `19.33%` | `61` | `31` | `-$396.60` | `-15.69%` | `1/5` |
| bid-2c queue_full | `476` | `76` | `15.97%` | `46` | `30` | `-$366.00` | `-18.04%` | `1/5` |

裁决：`30s` 等待会吸入 adverse selection，单纯更深折价不能解决残仓质量。

随后做可解释网格搜索，只使用开单前可见状态和可执行撤单时限。最佳稳定候选：

```text
40s <= offset < 60s
prev_bid_delta_1s >= 5c
0.40 <= side_bid < 0.55
spread <= 1 tick
top_bid_sz <= 250
order_price = best_bid
fill_model = queue_full
first_fill_timeout = 15s
completion_pair_ceiling = 0.95
completion_deadline = 30s
repair_pair_ceiling = 1.04
repair_deadline = 60s
clip = 60
```

精确回测结果：

| clip | attempts | fills | fill rate | completed | residual | PnL | ROI filled | pair p50 | positive days |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `60` | `166` | `33` | `19.88%` | `30` | `3` | `+$119.40` | `+12.78%` | `0.94` | `4/5` |
| `120` | `166` | `21` | `12.65%` | `20` | `1` | `+$60.00` | `+5.10%` | `0.94` | `3/5` |
| `160` | `166` | `17` | `10.24%` | `16` | `1` | `+$40.00` | `+3.10%` | `0.94` | `3/5` |

进一步加 `side_bid < 50c` 后：

| variant | clip | attempts | fills | fill rate | completed | residual/settle | PnL | ROI filled | positive days |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| base `side_bid<55c` | `60` | `166` | `33` | `19.88%` | `30` | `3` | `+$119.40` | `+12.78%` | `4/5` |
| `side_bid<50c` | `60` | `110` | `25` | `22.73%` | `24` | `1` | `+$103.80` | `+15.23%` | `5/5` |
| `side_bid<50c` | `120` | `110` | `16` | `14.55%` | `16` | `0` | `+$87.60` | `+10.18%` | `4/5` |

`side_bid<50c` 的含义：

- 去掉过热的一侧，牺牲一部分总 PnL，但显著改善日稳定性和残仓。
- clip=120 仍为正且无 residual-settle，但 4/30 轻微亏损；第一版默认仍应是 clip=60，clip=120 只作为容量 shadow。
- 这和早段结果一致：过热一侧更容易成为 loser residual，xuan 的早段优势可能也来自“尚未过热时进入”。

clip=60 日拆：

| day | attempts | fills | PnL | ROI filled |
|---|---:|---:|---:|---:|
| 2026-04-27 | `24` | `7` | `-$1.80` | `-0.92%` |
| 2026-04-28 | `36` | `6` | `+$37.80` | `+22.42%` |
| 2026-04-29 | `38` | `9` | `+$32.40` | `+12.80%` |
| 2026-04-30 | `43` | `7` | `+$31.20` | `+15.76%` |
| 2026-05-01 | `25` | `4` | `+$19.80` | `+16.75%` |

关键解释：

- 正收益来自 `15s cancel`，不是来自更深挂价。`30s` 等待版本残仓过多且系统性亏损；`15s` 版本只留下 3 个残仓。
- `best_bid` 比 `bid-1c/bid-2c` 更好，因为这个候选已经靠 `queue_full + top_bid_sz<=250 + 15s cancel` 控制 adverse selection；继续降价主要降低成交率和容量。
- clip 放大后收益不线性增长，说明这条 edge 的真实容量有限。当前更像一个高质量 sidecar，而不是 xuan 主引擎。
- 与 xuan 对齐：166 个候选中 106 个市场 xuan 也交易过，33 个模拟成交中 19 个市场 xuan 也交易过；但 xuan 在这些市场的首笔中位 offset 约 `16-18s`，而该候选在 `40-60s` 才触发。
- 因此它不是完整复刻 xuan；它是从 xuan 活跃市场中提炼出的“后半分钟确认型 maker edge”。

和 xuan 全窗口对比：

| cohort | markets | PnL | ROI | first offset p50 | first winner rate |
|---|---:|---:|---:|---:|---:|
| xuan all BTC 5m | `947` | `+$14,403.36` | `+2.09%` | `16s` | `57.44%` |
| xuan in our candidate attempt markets | `106` | `+$1,034.03` | `+1.26%` | `16s` | `56.60%` |
| xuan in our candidate filled markets | `19` | `+$351.32` | `+2.82%` | `18s` | `52.63%` |
| our candidate clip60 | `166 attempts` | `+$119.40` | `+12.78% filled spend` | `40-60s rule` | `57.58%` |

早段复现尝试：

| window | delta threshold | attempts | fills | completed | residual | PnL | ROI filled | positive days |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `10-40s` | `5c` | `241` | `29` | `20` | `9` | `-$77.40` | `-9.44%` | `2/5` |
| `10-20s` | `5c` | `77` | `5` | `3` | `2` | `+$86.40` | `+64.29%` | `3/5` |
| `10-20s` | `4c` | `124` | `10` | `7` | `3` | `+$61.20` | `+21.52%` | `3/5` |
| `10-20s` + `side_bid < 50c` | `4c` | `68` | `7` | `5` | `2` | `+$91.20` | `+48.10%` | `4/5` |

早段裁决：

- `10-20s` 确实出现高 ROI 小样本，和 xuan 的 `16s` 首腿中位数对齐，但成交太少，不能作为默认策略。
- `10-40s` 失败，说明不能简单把后段确认信号前移；`20-40s` 是主要亏损来源。
- 放宽到 `4c` 增加 fill 数，但引入亏损日，说明早段更需要额外过滤，而不是降低动量门槛。
- 排除 `side_bid >= 50c` 后，早段质量明显改善，说明早段高位追涨更容易成为 loser residual；xuan 的早段 edge 可能包含“只买尚未过热的一侧”。
- `opp_spread<=1` 精确重跑没有提供增量，早段质量主要由 `side_bid<50c` 解释。
- 下一步早段研究必须增加方向、外部 BTC、盘口恢复信号，否则无法解释 xuan 为什么能早进而我们不能。

双窗口组合近似状态机：

新增复算脚本：`scripts/analyze_dual_window_fastcancel_combo.py`。

```text
early probe: 10s <= offset < 20s, prev_bid_delta>=4c, 40c <= side_bid < 50c
late sidecar: 40s <= offset < 60s, prev_bid_delta>=5c, 40c <= side_bid < 50c
共同执行：best_bid, queue_full, top_bid_sz<=250, 15s cancel, clip=60
```

组合结果：

| attempts | fills | completion | repair | residual_settle | PnL | spend est | ROI est | positive days |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `178` | `32` | `19` | `10` | `3` | `+$195.00` | `~$1,725` | `+11.30%` | `5/5` |

日拆：

| day | attempts | fills | PnL | early a/f | late a/f |
|---|---:|---:|---:|---:|---:|
| 2026-04-27 | `20` | `6` | `+$64.20` | `7/2` | `13/4` |
| 2026-04-28 | `40` | `6` | `+$39.00` | `16/1` | `24/5` |
| 2026-04-29 | `52` | `9` | `+$70.80` | `22/2` | `30/7` |
| 2026-04-30 | `42` | `8` | `+$4.20` | `15/2` | `27/6` |
| 2026-05-01 | `24` | `3` | `+$16.80` | `8/0` | `16/3` |

组合裁决：

- 这是目前最接近“可超越方向”的候选：双窗口补齐频率，5/5 天为正，ROI 明显高于 xuan 账户级 ROI。
- 但绝对规模仍小，且 `residual_settle` 使用官方结果估值，不能直接视为 live 可执行清残。
- 下一步不是继续放大 clip，而是给 `residual_settle` 加真实清残策略，确认不靠幸运结算也能保持正收益。

xuan 对齐：

| cohort | markets | xuan PnL | xuan ROI | xuan first offset p50 | xuan first winner rate |
|---|---:|---:|---:|---:|---:|
| xuan all BTC 5m | `947` | `+$14,403.36` | `+2.09%` | `16s` | `57.44%` |
| xuan in combo selected markets | `107` | `+$1,007.23` | `+1.25%` | `14s` | `50.47%` |
| xuan in combo filled markets | `21` | `+$309.12` | `+2.51%` | `18s` | `33.33%` |
| xuan not combo | `840` | `+$13,396.13` | `+2.20%` | `16s` | `58.33%` |

同侧近时点重叠：

- 以候选时间为 anchor，`-5s/+20s` 内 xuan 同侧成交覆盖 `71/178` 个 attempts，其中 `12/32` 个 fills。
- 以我方 proxy fill 时间为 anchor，`-5s/+20s` 内 xuan 同侧成交覆盖 `10/32` 个 fills。

对齐裁决：

- 双窗口候选不是 xuan 主引擎；xuan 在这些 selected markets 的 ROI 反而低于全样本。
- filled markets 的 xuan ROI 较高，但样本只有 `21` 个，不能证明 xuan 正在使用同一个信号。
- 当前候选应定位为“受 xuan 启发的独立 market-side fast-cancel edge”，不是“已复刻 xuan”。
- 继续研究 xuan 主引擎时，不能让这个 sidecar 代替 xuan 的 `16s` 首腿机制解释。

`residual_settle` 清残替代审计：

| residual policy | total PnL | positive days | daily summary |
|---|---:|---:|---|
| use official settlement | `+$195.00` | `5/5` | `64.2 / 39.0 / 70.8 / 4.2 / 16.8` |
| force sell first leg at L2 bid VWAP after `30s` | `+$72.71` | `4/5` | close to L1 |
| force sell first leg at L2 bid VWAP after `60s` | `+$67.22` | `4/5` | close to L1 |
| force sell first leg at L2 bid VWAP after `120s` | `+$118.20` | `5/5` | `62.4 / 3.6 / 31.2 / 4.2 / 16.8` |
| force sell first leg at L2 bid VWAP after `180s` | `+$156.00` | `5/5` | `63.0 / 24.0 / 48.0 / 4.2 / 16.8` |

清残裁决：

- 不等结算也仍为正，说明组合不是完全靠结算残仓幻觉。
- 但 `30-60s` 强卖会大幅吃掉收益；`120s` 是当前较合理的 shadow 清残点，仍 5/5 天为正。
- L2 bid VWAP 和 L1 bid 结果几乎一致，且三笔 residual 在测试时点都能被可见 L2 bid 深度覆盖；这降低了“清残只是 L1 幻觉”的风险。
- 这仍不是 live 结论，因为卖出行为会改变盘口，且没有我方真实成交回执；但它已经足够把下一步从“是否有 edge”推进到“如何设计残仓 exit controller”。

进一步把所有 `repair` 也视为 non-clean close，并统一用 L2 bid VWAP 强卖：

| non-clean policy | total PnL | positive days | daily summary |
|---|---:|---:|---|
| replace repair + residual with L2 sell after `30s` | `+$26.81` | `4/5` | `2.4 / 0.7 / 30.6 / -23.7 / 16.8` |
| replace repair + residual with L2 sell after `60s` | `+$44.33` | `3/5` | `21.0 / -8.1 / 25.6 / -10.9 / 16.8` |
| replace repair + residual with L2 sell after `90s` | `+$113.03` | `4/5` | `51.6 / 21.6 / 33.6 / -10.6 / 16.8` |
| replace repair + residual with L2 sell after `120s` | `+$144.10` | `5/5` | `56.4 / 4.6 / 31.2 / 35.1 / 16.8` |
| replace repair + residual with L2 sell after `180s` | `+$239.89` | `5/5` | `56.4 / 34.0 / 48.0 / 84.7 / 16.8` |

non-clean exit 裁决：

- 当前 `repair` 路径本身只贡献 `+$1.2`，不是主要利润来源。
- 若 30 秒后仍未 clean close，继续 taker repair 的价值很弱；`120s` first-leg exit 反而更稳。
- `180s` 收益最高但单边风险暴露更长，第一版 shadow 不应直接采用；`120s` 是更审慎的默认。
- 动态 break-even / +2c / +3c exit 搜索没有明显优于简单 `120s`，因为过早退出会错过一部分有利反弹。

L2 补腿重定价审计：

原回测的 completion/repair 使用 L1 ask。为避免低估 taker 补腿成本，按同一秒内最新 L2 ask VWAP 重算所有 completion/repair，并把 residual 用 `120s` L2 bid VWAP 出场：

| case | total PnL | positive days | daily summary |
|---|---:|---:|---|
| original L1 completion + settlement residual | `+$195.00` | `5/5` | `64.2 / 39.0 / 70.8 / 4.2 / 16.8` |
| L2 completion VWAP + residual 120s L2 exit | `+$116.65` | `5/5` | `61.9 / 3.1 / 31.2 / 3.7 / 16.8` |
| L2 completion + `0.5c` extra friction | `+$107.05` | `5/5` | `60.1 / 1.3 / 28.5 / 1.3 / 15.9` |
| L2 completion + `1.0c` extra friction | `+$97.45` | `3/5` | `58.2 / -0.5 / 25.8 / -1.1 / 15.0` |
| L2 completion + `2.0c` extra friction | `+$78.25` | `3/5` | `54.7 / -4.1 / 20.4 / -5.9 / 13.2` |

补腿裁决：

- L2 补腿后仍正且 5/5 天为正，但收益从 `+$195` 收缩到 `+$116.65`，说明 L1 回测确实偏乐观。
- 额外 `0.5c` 摩擦仍可接受；`1c` 以上摩擦会让 4/28、4/30 转负。
- 因此 enforce 前必须证明真实补腿 VWAP 与 L2 replay 的偏差 `<0.5c`，否则该策略只能小仓 shadow。

慢路径控制器复测：

xuan 的 slow-profit path 显示：如果首腿后 30 秒内出现过足够便宜的 opposite 证据，则慢路径更可能变成高利润 tranche。把这个思想接到双窗口 sidecar 后，先测试严格版：

```text
slow95: min_pair_cost_seen_30s <= 0.95
then allow slow completion until 120s at pair_cost <= 0.95
```

结果：无变化。当前 sidecar 的未补齐样本在前 30 秒内最低 pair cost 多为 `0.97-1.00`，没有达到 xuan slow-profit 的 `<=0.95` 强证据。

再测试放宽版：

```text
slow99/pair98:
if min_pair_cost_seen_30s <= 0.99
then allow slow completion until 120s at pair_cost <= 0.98
else keep original repair/exit
```

| policy | paths | raw PnL | L2 PnL | L2 +0.5c | L2 +1c | L2 +2c | weakest L2 day |
|---|---|---:|---:|---:|---:|---:|---:|
| original | `19 completion / 10 repair / 3 residual` | `+$195.00` | `+$116.65` | `+$107.05` | `+$97.45` | `+$78.25` | `+$3.10` |
| `slow99/pair98` | `19 completion / 5 slow_completion / 5 repair / 3 residual` | `+$208.80` | `+$130.45` | `+$120.85` | `+$111.25` | `+$92.05` | `+$9.10` |

慢路径裁决：

- xuan 的严格 `<=0.95` 证据不能直接套到当前 sidecar；我们的未补齐样本没有那么便宜。
- 放宽到 `<=0.99` 作为 evidence、`<=0.98` 作为慢补腿上限后，5 个原 repair 转成 slow completion，L2 后收益提升 `+$13.8`。
- 这不是主引擎，但说明“看到便宜证据后延长等待”确实优于机械 repair。
- 第一版 shadow 可加入 `slow99/pair98`，但必须与 `120s non-clean exit` 共存；不能无限等待。

宽候选搜索与 raw 排名反例：

为避免每组参数重复扫描 SQLite，新增：

- `--emit-all-candidates`：生成不受当前 cursor/cooldown 截断的宽候选 rows。
- `scripts/search_fastcancel_from_rows.py`：在候选 rows 上离线枚举 filter + state machine。
- `find_maker_fill()` 二分优化：避免每个候选从当日第一笔 trade 开始扫，宽扫描从不可用降到分钟级。

本轮生成：

```text
0-120s / delta>=2c / side_bid 35-55c / spread<=2 / top_bid<=400
slow99/pair98 / queue_full / emit-all
```

得到 `8317` 条候选 rows，离线搜索 `26484` 个参数组合。raw 排名第一：

```text
40-60s
prev_bid_delta_1s >= 6c
40c <= side_bid < 55c
spread<=2, opp_spread<=2
top_bid_sz<=100
immediate_pair_cost<=1.00
```

raw 结果：

| attempts | fills | paths | raw PnL | positive days | weakest raw day |
|---:|---:|---|---:|---:|---:|
| `105` | `31` | `20 completion / 4 slow / 5 repair / 2 residual` | `+$171.00` | `5/5` | `+$19.80` |

但 L2 重定价后：

| L2 PnL | positive days | weakest L2 day | L2 +0.5c | L2 +1c |
|---:|---:|---:|---:|---:|
| `+$114.65` | `4/5` | `-$4.90` | `+$105.35` | `+$96.05` |

搜索裁决：

- raw 排名第一不如当前双窗口 `slow99/pair98`，后者 L2 PnL `+$130.45` 且 `5/5` 天为正。
- `top_bid_sz<=100` 和 `delta>=6c` 提高了 raw 弱日，但没有提高 L2 可执行性；4/28 的 L2 补腿成本直接推翻它。
- 后续搜索流程固定为两阶段：raw/offline 只筛候选，L2 completion + non-clean exit 才能裁决。
- 当前 leader 仍是 `10-20s delta4c side_bid<50c + 40-60s delta5c side_bid<50c + slow99/pair98`。

批量 L2 复验：

新增脚本 `scripts/validate_fastcancel_l2_candidates.py`，用于读取 raw search top N 并批量做 L2 completion / residual exit 重定价。对 top100 复验后，最稳的单窗口候选是：

```text
40-60s
prev_bid_delta_1s >= 6c
40c <= side_bid < 55c
spread<=1, top_bid_sz<=100
```

| attempts | fills | raw PnL | L2 PnL | positive days | weakest L2 day | L2 +1c | L2 +2c |
|---:|---:|---:|---:|---:|---:|---:|---:|
| `84` | `26` | `+$130.80` | `+$124.85` | `5/5` | `+$13.70` | `+$109.25` | `+$93.65` |

把该 late 窗口与 early `10-20s` 组合后：

| combo | attempts | fills | raw PnL | L2 PnL @120s residual exit | positive days | weakest L2 day |
|---|---:|---:|---:|---:|---:|---:|
| current leader | `178` | `32` | `+$208.80` | `+$130.45` | `5/5` | `+$9.10` |
| early + late delta6/top100 | `172` | `38` | `+$262.20` | `+$164.45` | `5/5` | `+$1.70` |

裁决：

- `early + late delta6/top100` 是新的高收益候选，但 `120s` residual exit 下弱日太薄，`0.5c` 摩擦即可让 4/28 转负。
- 原 leader 收益低一点，但在 `1c` 摩擦下仍 5/5 天正；它更适合作为稳健默认。
- 新候选应进入 aggressive shadow，不应替代默认。

Residual exit 180s 变体：

仅把 residual exit 从 `120s` 改为 `180s`，completion/repair 不变：

| combo | L2 PnL @120s | L2 PnL @180s | 180s weakest day | 180s +1c | 180s +2c |
|---|---:|---:|---:|---:|---:|
| current leader | `+$130.45` | `+$168.25` | `+$9.10` | `+$149.05` | `+$129.85` |
| early + late delta6/top100 | `+$164.45` | `+$212.07` | `+$19.80` | `+$189.27` | `+$166.47` |

180s 裁决：

- `180s` residual exit 显著提高收益和弱日，但它依赖少数 residual rows 的有利反弹，单边暴露更长。
- 第一版 live/shadow 不能直接把 `180s` 当默认；必须作为 aggressive residual policy 独立打标。
- 若更长样本和 own execution truth 显示 `180s` residual exit 的 VWAP 没有显著恶化，它可能是当前 sidecar 从“小正收益”走向可观收益的关键杠杆。

首腿排队劣化压力测试：

在 `queue_full` 之外额外增加 required size，模拟我方排队更靠后：

| fill stress | attempts | fills | PnL | ROI est | positive days | path summary |
|---|---:|---:|---:|---:|---:|---|
| `queue_full` | `178` | `32` | `+$195.00` | `+11.30%` | `5/5` | `19 completion / 10 repair / 3 residual` |
| `queue_full + 60` | `178` | `19` | `+$57.60` | `+5.32%` | `5/5` | `13 completion / 6 repair / 0 residual` |
| `queue_full + 120` | `178` | `11` | `+$20.40` | `+3.19%` | `3/5` | `6 completion / 5 repair / 0 residual` |

联合 L2 补腿与排队压力：

| fill stress | fills | raw PnL | L2 PnL | L2 +0.5c | L2 +1c | L2 +2c | L2 positive days |
|---|---:|---:|---:|---:|---:|---:|---:|
| `queue_full` | `32` | `+$195.00` | `+$116.65` | `+$107.05` | `+$97.45` | `+$78.25` | `5/5` |
| `queue_full + 60` | `19` | `+$57.60` | `+$56.05` | `+$50.35` | `+$44.65` | `+$33.25` | `5/5` |
| `queue_full + 120` | `11` | `+$20.40` | `+$19.90` | `+$16.60` | `+$13.30` | `+$6.70` | `3/5` |

仓位容量压力测试：

在同一双窗口规则下，把 `clip` 从 `60` 提高到 `120/160`，仍使用 `queue_full` 成交触发和 L2 补腿重定价：

| clip | attempts | fills | raw PnL | L2 PnL | L2 positive days | weakest L2 day | L2 +0.5c all-positive |
|---:|---:|---:|---:|---:|---:|---:|---:|
| `60` | `178` | `32` | `+$195.00` | `+$116.65` | `5/5` | `+$3.10` | yes |
| `120` | `178` | `19` | `+$121.20` | `+$119.00` | `5/5` | `+$3.70` | yes |
| `160` | `178` | `14` | `+$110.40` | `+$107.46` | `4/5` | `-$3.20` | no |

容量裁决：

- 放大不是线性的。`clip=120` 没有把 `clip=60` 的 PnL 翻倍，只是筛掉了 13 个较小成交，最终 L2 PnL 近似持平。
- `clip=160` 继续减少成交，并在 4/30 转负；它不适合作为默认仓位。
- `clip=120` 可以作为 strong-state upclip shadow，但默认仓位仍应从 `60` 开始。
- 这条 sidecar 的自然容量目前大约是 `60-120 shares`，远小于 xuan 的主引擎容量；超越 xuan 不能只靠放大这条规则。

条件残仓退出更新：

固定 `120s` residual exit 过早，固定 `180s` residual exit 虽然收益高但暴露更长。新增条件口径：

```text
if residual first_price < 0.50:
    use 180s L2 bid VWAP exit
else:
    use default 120s L2 bid VWAP exit
```

在 aggressive early+late 组合上，完整 L2 completion + 条件 residual exit：

| variant | attempts | fills | raw PnL | L2 PnL | L2 positive days | weakest day | L2 +1c | L2 +2c |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| stable `early + late bid<50` | `178` | `32` | `+$208.80` | `+$168.25` | `5/5` | `+$9.10` | `+$149.05` | `+$129.85` |
| aggressive `early + late delta6/top100` | `172` | `38` | `+$262.20` | `+$213.87` | `5/5` | `+$19.80` | `+$191.07` | `+$168.27` |

裁决：

- 条件 residual exit 比固定 `120s` 更合理，把 aggressive 组合从 `+$164.45` 提升到 `+$213.87`，且 +2c completion friction 后仍 5/5 天为正。
- 它接近固定 `180s` 的收益，但只让 `first_price<0.50` 的残仓延长到 `180s`，风险语义更清晰。
- 仍然必须标记为 shadow-only，因为 residual 样本很少，且这部分最依赖真实退出 VWAP。

动态 upclip 更新：

无脑把 clip 从 `60` 提高到 `120/160` 被否定。按同一 aggressive 组合、同一条件 residual exit：

| sizing | fills | raw PnL | L2 PnL | L2 positive days | weakest day | L2 +2c |
|---|---:|---:|---:|---:|---:|---:|
| fixed `clip=60` | `38` | `+$262.20` | `+$213.87` | `5/5` | `+$19.80` | `+$168.27` |
| fixed `clip=120` | `21` | `+$112.80` | `+$118.45` | `4/5` | `-$33.98` | `+$68.05` |
| fixed `clip=160` | `15` | `+$163.20` | `+$159.97` | `5/5` | `+$9.76` | `+$111.97` |

固定扩容失败的原因不是 pair-cost 变差，而是 `queue_full` 模型下大 clip 会筛掉大量原本能成交的首腿，并改变成交样本。

新增动态 upclip 规则只使用开仓前可见特征：

```text
default clip = 60
if prev_bid_delta_1s >= 0.14:
    shadow clip = 160
```

规则扫结果：

| rule | clip when true | fills | raw PnL | L2 PnL | L2 positive days | weakest day | L2 +2c |
|---|---:|---:|---:|---:|---:|---:|---:|
| none | `60` | `38` | `+$262.20` | `+$213.87` | `5/5` | `+$19.80` | `+$168.27` |
| `prev_delta>=0.14` | `160` | `37` | `+$287.00` | `+$238.22` | `5/5` | `+$19.80` | `+$187.82` |
| `prev_delta>=0.15` | `160` | `37` | `+$283.00` | `+$234.22` | `5/5` | `+$19.80` | `+$185.82` |
| `prev_delta>=0.13` | `160` | `35` | `+$281.20` | `+$232.42` | `5/5` | `+$12.00` | `+$182.42` |
| `top_bid_sz<=6.21` | `160` | `36` | `+$278.40` | `+$229.07` | `5/5` | `+$19.80` | `+$179.87` |
| `prev_delta>=0.14` | `120` | `37` | `+$277.80` | `+$229.42` | `5/5` | `+$19.80` | `+$181.42` |

动态 upclip 裁决：

- `prev_bid_delta_1s>=0.14 -> clip160` 是当前最优单规则，完整 L2 比 fixed clip60 多 `+$24.35`，且 +2c completion friction 仍全日为正。
- 这是“强状态扩仓”，不是默认扩仓；默认 clip 仍为 `60`。
- `sell_vol_until_fill` 和 `fill_delay` 虽然能更好解释容量，但它们是事后信息，不能作为开仓 sizing gate。
- 下一步需要把这条规则接入 shadow explain：记录 `base_clip`、`effective_clip`、`upclip_reason`、`would_have_filled_at_60/120/160`。

频率扩展更新：

把 late window 从 `40-60s` 扩到 `30-60s`，单独看 raw PnL 下降，但完整 L2 + 条件 residual exit 后反而更好：

| variant | attempts | fills | raw PnL | L2 PnL | L2 positive days | weakest day | L2 +1c | L2 +2c |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `early + 40-60 dynamic upclip160@0.14` | `172` | `37` | `+$287.00` | `+$238.22` | `5/5` | `+$19.80` | `+$213.02` | `+$187.82` |
| `early + 30-60 clip60` | `218` | `47` | `+$228.60` | `+$231.25` | `5/5` | `+$22.80` | `+$203.05` | `+$174.85` |
| `early + 30-60 dynamic upclip160@0.14` | `218` | `44` | `+$276.20` | `+$250.80` | `5/5` | `+$22.80` | `+$221.40` | `+$192.00` |
| `early + 30-60 dynamic upclip160@0.15` | `218` | `45` | `+$248.20` | `+$250.40` | `5/5` | `+$22.80` | n/a | `+$192.40` |

频率扩展裁决：

- 新 leader 是 `early 10-20s delta>=4c side_bid<50c` + `mid 30-60s delta>=6c side_bid<55c spread<=2 top_bid_sz<=100` + 条件 residual exit + `prev_delta>=0.14 -> clip160`。
- `0.14` 与 `0.15` 阈值接近：`0.14` base L2 略高，`0.15` +2c friction 略高。收益优先先用 `0.14`，若实盘 completion friction 偏高再切 `0.15`。
- 这仍是 sidecar，不是 xuan 主体。5 天 `+$250.80` 远低于 xuan `+$14419.08`，但它已经比上一版 leader 高 `+$12.59`，且机会数从 `172` 提到 `218`。

继续放宽 delta 的结果：

| mid-window delta | attempts | fills | raw PnL | L2 PnL | L2 positive days | weakest day | L2 +2c |
|---:|---:|---:|---:|---:|---:|---:|---:|
| `>=6c` + dynamic upclip | `218` | `44` | `+$276.20` | `+$250.80` | `5/5` | `+$22.80` | `+$192.00` |
| `>=5c` + dynamic upclip | `237` | `46` | `+$284.60` | `+$259.20` | `5/5` | `+$22.80` | `+$198.00` |
| `>=4c` + dynamic upclip | `315` | `63` | `+$228.20` | `+$169.85` | `4/5` | `-$16.70` | `+$88.25` |

delta 裁决：

- `>=5c` 是当前最优频率边界，新增机会是正贡献。
- `>=4c` 机会数大幅增加，但 05-01 转负，说明它引入了大量低质量状态；不得进入 shadow 默认。
- 当前 shadow 候选应固定为 `mid 30-60s delta>=5c`，而不是 `>=6c` 或 `>=4c`。

Residual exit 保守性检查：

同一 leader 在不同 residual exit 规则下：

| residual exit policy | L2 PnL | positive days | weakest day | L2 +2c |
|---|---:|---:|---:|---:|
| fixed `120s` | `+$205.11` | `5/5` | `+$5.21` | `+$143.91` / `4/5` |
| `min_pair_seen_30s<=1.01 -> 180s else 120s` | `+$254.52` | `5/5` | `+$22.80` | `+$193.32` / `5/5` |
| `first_price<0.50 -> 180s else 120s` | `+$259.20` | `5/5` | `+$22.80` | `+$198.00` / `5/5` |

Residual 裁决：

- 策略不是完全靠 residual，固定 `120s` 仍为正，但高 friction 下不稳。
- `first_price<0.50 -> 180s else 120s` 是当前收益/稳定性最优规则，但必须在 shadow 中重点验证真实 180s exit VWAP。
- 如果 shadow 中 180s exit 滑点显著恶化，应降级到 `min_pair_seen_30s<=1.01` 版本。

Completion/exit friction 压力测试：

当前 leader 在额外 friction 下仍保持：

| extra friction | L2 PnL | positive days | weakest day |
|---:|---:|---:|---:|
| `0c` | `+$259.20` | `5/5` | `+$22.80` |
| `1c` | `+$228.60` | `5/5` | `+$19.20` |
| `2c` | `+$198.00` | `5/5` | `+$15.60` |
| `3c` | `+$167.40` | `5/5` | `+$12.00` |
| `4c` | `+$136.80` | `5/5` | `+$8.40` |
| `5c` | `+$106.20` | `5/5` | `+$4.80` |

Shadow 准入裁决：

- 这已经达到“值得 shadow 验证”的回测门槛。
- 进入 shadow 前不应再继续用 5 天数据做大量参数拟合；下一步核心是验证真实 maker fill 质量、completion VWAP drift、180s residual exit VWAP。
- Shadow 必须默认关闭实盘成交，只输出 would-order / would-fill / would-complete / would-exit 事件。

排队压力复核：

当前 leader 在 `queue_full` public fill proxy 下很好，但加 `extra_required_size=+60` 后显著退化：

| queue stress | attempts | fills | raw PnL | L2 PnL | positive days | weakest day | L2 +2c |
|---|---:|---:|---:|---:|---:|---:|---:|
| `queue_full` + dynamic upclip | `237` | `46` | `+$284.60` | `+$259.20` | `5/5` | `+$22.80` | `+$198.00` |
| `queue_full+60` no upclip | `237` | `29` | `+$50.40` | `+$73.86` | `4/5` | `-$15.54` | `+$39.06` / `4/5` |
| `queue_full+60` + dynamic upclip | `237` | `27` | `+$53.60` | `+$76.61` | `4/5` | `-$15.54` | `+$40.21` / `4/5` |

排队裁决：

- 该 sidecar 的收益高度依赖 maker fill priority；它不是“随便挂就能赚钱”的策略。
- Shadow 可以开始，但 enforce 的 P0 条件必须是：我方真实成交质量接近 `queue_full`，不能接近 `queue_full+60`。
- 如果 dry-run / user truth 显示真实成交率或成交时延接近 `queue_full+60`，该候选自动降级为 research-only，不能进入实盘。

排队裁决：

- `+60` buffer 后仍 5/5 天为正，说明 edge 对 moderate queue disadvantage 有一定韧性。
- `+120` buffer 后几乎失效，说明收益高度依赖成交优先级；如果实盘 fill truth 显示我们长期接近 `queue_full+120`，这条策略不能 enforce。
- 第一阶段 shadow 的核心验收指标应是：真实 maker fill 与 replay proxy 的距离必须落在 `queue_full` 到 `queue_full+60` 之间，不能接近 `+120`。
- 联合 L2 补腿后，`queue_full+60` 仍保持 `+$50.35` at `0.5c` friction，说明 moderate queue disadvantage 仍有生存空间。

裁决：

- 这是目前第一条同时满足 `public fill proxy`、`queue-aware`、`残仓少`、`5 天总体正收益` 的候选。
- 它尚不能“超越 xuan”收益规模：机会数和容量远小于 xuan。
- 它可以作为下一版 `shadow` 的强默认 sidecar，但不能替代主策略。
- 下一步应围绕这个候选扩展：寻找 `15s 内高质量成交` 的前置信号、增加候选频率、并验证我方真实 maker fill 是否接近 `queue_full` 代理。

## 当前策略认知

xuan 不是简单的：

- 固定 pair target；
- 纯 30s 配对；
- 无脑双边 maker；
- 无脑高价 winner bet。

更接近的结构是：

```text
open gate 先保证 first leg 具备 execution-edge / winner-proxy
-> completion controller 把多数库存快速 pair-covered
-> cheap-window evidence 才允许 slow-profit path
-> no-cheap-window 快速 repair/exit
-> market-level weighted pair cost 必须长期 < 1
```

需要更新的策略认知：

- `first_price 0.80-0.90` 是“残仓更安全”的高 winner-proxy，不是 xuan 的主利润引擎。
- xuan 的主利润引擎更像“中价位大量捕获成交 + execution edge + pair-cost controller”。
- 超越 xuan 不是只复制他的全样本，而是识别并放大其高 ROI 子集，同时丢弃低 ROI 子集。
- 但目前我们只能复制 sidecar，尚不能复制主引擎；这就是当前与 xuan 利润规模差距的核心。
- 主引擎不是简单同价排队；更可能涉及更深档成交、盘口瞬变、真实挂单时点领先 public print，或者我们尚未建模的方向/时序信号。
- 最新解释更具体：xuan 很可能在一侧盘口上移前后拿到首腿，`post+1s bid>=price` 是强 ex-post 标签；我们的任务是找出它的 pre-signal。
- 最新风险也更具体：xuan 最强子集里大量是 `already_gte`，这部分可能是不可复制的队列/时间戳优势。超越 xuan 不能假设我们也能拿到这些成交，必须用全市场 `upcross` predictor 和我方 fill truth 重新估算。
- 全市场 recent 数据已经否定了“简单套用 xuan offset<60/mid-price/tight-spread”作为 open gate；可复制信号目前更像短动量，而不是 xuan 成交锚定规则。
- 轻量 taker proxy 又否定了“短动量本身足够盈利”：它的问题仍是残仓质量，而不是闭合 pair cost。
- execution discount 是硬门槛：同一信号在 ask/bid 下亏，在 bid-1c/bid-2c 下才开始转正。
- fillability 审计进一步说明：折价越深，成交率越低；而且成交率高并不保证当天盈利，说明 adverse selection / residual winner-proxy 仍是主风险。
- 第一条稳定正收益候选不是 `bid-2c`，而是 `best_bid + queue_full + 15s cancel`；撤单速度是核心风险控制。
- 这条候选解释了 xuan 的一部分行为：它偏向 xuan 也活跃的市场，但触发时间晚于 xuan 首笔，因此更像后段确认 sidecar。
- 最新收敛：`side_bid<50c` 是关键质量过滤。它同时改善早段和后段，说明“不过热进入”可能是比单纯动量更接近 xuan 的开仓思想。
- 但双窗口候选和 xuan 主收益分布并不一致；它是一个独立可交易 edge，不是 xuan 策略主体。

## 下一步

1. 把 `mid-price detector` 从 xuan 成交锚定改成全市场 `upcross predictor`，第一版候选是 `prev_bid_delta_1s>=2c AND bid 40-55 AND spread<=1`。
2. 队列研究从“同价提前挂”转向“盘口上移前信号”：用 L1/L2 斜率、recent flow、best-bid jump、external BTC micro-move 预测 `upcross`，并验证它能否转化成 maker fill 或 bounded taker edge。
3. `0.80-0.90` 保留为 sidecar 和 residual classifier 研究，不再作为主策略。
4. 快速参数搜索器应优先搜索 `mid-price execution-edge`，而不是继续放宽高价 winner-proxy。
5. 主策略不再考虑 naive two-sided maker-grid，除非加入强 execution-edge、强 completion controller 和清残规则。
6. `already_gte` 子集只能用于解释 xuan 的上限，不得直接折算成我方预期 PnL。
7. 下一轮必须优先研究 residual winner-proxy：任何 open gate 如果不能让未补腿残仓 winner rate 显著高于 `50%`，都不能成为主策略。
8. 同时必须启动 maker fillability / queue priority shadow：目标不是先盈利，而是量化我们能否在短动量窗口拿到 `bid-1c` 到 `bid-2c` 的实际成交质量。
9. 下一版回测要从“假设 bid-2c 成交”升级为“public SELL fill proxy 触发后才开仓”，否则会高估成交频率和收益规模。
10. 将 `40-60s / prev_bid_delta>=5c / top_bid_sz<=250 / 15s cancel` 作为第一个 maker sidecar 默认候选，进入更长窗口和真实 dry-run fill truth 验证。
11. 围绕该候选继续搜索更早触发版本：目标是把 xuan 的 `16s` 首腿时点提前复现，而不是只在 `40-60s` 后确认。
12. `10-20s / side_bid<50c` 早段候选只作为 research probe，不作为默认；它最接近 xuan 开仓时点，但仍需要更长样本和更高频率。
13. 下一轮默认研究配置应测试“双窗口 fast-cancel”：`10-20s delta4c side_bid<50c` + `40-60s delta5c side_bid<50c`，但必须把 `residual_settle` 改成清残/止损口径。
14. 第一版 non-clean exit controller 可用 `120s` L2 bid VWAP 强制卖出作为 shadow baseline；`30-60s` 太急，会显著损害收益，`180s` 虽更赚钱但风险暴露过长。
15. 实盘 shadow 必须记录 `required_size_proxy`、`sell_vol_until_fill`、`extra_required_size_equivalent`；若真实成交质量接近 `queue_full+120`，策略自动降级为研究，不进入 enforce。
16. 实盘 shadow 必须记录 completion/exit 的真实 VWAP 偏差；若相对 replay L2 VWAP 长期劣化 `>=1c`，策略不得 enforce。
17. 双窗口 sidecar 默认 `clip=60`；不允许 fixed `clip=120/160` 作为默认。当前唯一可继续推进的 upclip 是 `prev_bid_delta_1s>=0.14 -> clip160` 的 shadow 强状态规则。
18. 双窗口 sidecar 应加入 `slow99/pair98` 慢路径控制器：30 秒内看见 `pair_cost<=0.99` 才允许继续等到 120 秒补 `<=0.98`，否则进入原 repair/exit。
19. 参数搜索必须使用两阶段裁决：宽候选 raw search 只能提名候选，所有候选必须再跑 L2 completion reprice；raw 第一的 `delta>=6c/top_bid<=100` 已被 L2 负日推翻。
20. `first_price<0.50 -> 180s residual exit, else 120s` 是新的 aggressive shadow residual 规则；固定 `180s` 仍不是默认，必须等待更长样本和 own execution truth 证明单边暴露可控。
21. Shadow explain 必须新增 `effective_clip`、`upclip_reason`、`residual_exit_policy`，否则后续无法判断收益来自 open gate、sizing，还是 residual 暴露。

## 当前硬结论

- xuan 的目标可量化为 `weighted pair cost ~= 0.980`，不是单纯 30s completion。
- 我们目前还没有接近 xuan 的收益规模。
- 已经找到一个小正收益可执行方向：高 winner-proxy + 快速补腿 + 强清残，但它不是主策略。
- 新的主攻方向是 `mid-price execution-edge`：xuan 自己的高 ROI 子集已经超过全样本 ROI，但我方当前执行模型复刻失败，必须先解决成交机制。
- 超越 xuan 的下一步不是无脑放宽机会，而是复制高 ROI 子集、提高 fill rate/队列优先级，并系统性避开低 ROI 状态。
- 当前最接近主引擎的可验证目标是 `upcross predictor + bounded completion + residual classifier`，不是静态 maker bid。
- 最近两日全市场结果给出的第一版可复制 open gate 是短动量，不是 xuan 成交锚定的 `l2_edge` 规则。
- 但 5 日 L1 taker proxy 说明短动量 open gate 仍是负收益；下一阶段的核心不是再提高 closed pair surplus，而是让 residual 不再系统性成为 loser。
- 如果没有 `>=1c` 的实际 maker 折价，当前短动量框架不应进入实盘 enforce；如果能稳定拿到 `1-2c` 折价，它才具备超过 xuan ROI 的理论空间。
- 当前还没有证明它能稳定超越 xuan；最新结论是“存在理论空间，但执行/残仓两道门槛未过”。
- 最新更新：已经找到一个 `public fill proxy` 下正收益的小容量候选，说明方向不是零；但它的收益规模仍远低于 xuan，超越路径必须继续解决“更早进场 + 更高频率 + 可放大容量”。
- 当前最强候选是双窗口 fast-cancel，5 天 `+$195`、5/5 天为正；即使用 `120s` L2 bid VWAP 强制卖出替代所有 repair/residual non-clean close，仍有 `+$144.1`、5/5 天为正。
- 排队压力下限已明确：`queue_full+60` 仍可接受，`queue_full+120` 不够稳。成交真值是下一阶段第一优先级。
- 仓位容量边界已更新：fixed `clip=120/160` 不适合默认；但 `prev_bid_delta_1s>=0.14 -> clip160` 动态 upclip 在完整 L2 口径下达到 `+$238.22`、5/5 天为正、+2c friction 后仍 `+$187.82`。
- xuan 的慢路径思想在 sidecar 上有小幅有效迁移：`slow99/pair98` 把 L2 PnL 从 `+$116.65` 提到 `+$130.45`，但严格 `slow95` 无效，说明当前 sidecar 仍不是 xuan 主 slow-profit 引擎。
- 宽搜索没有找到更强主引擎；raw 最强候选 L2 后变成 `4/5` 天为正。当前 leader 仍是双窗口 `slow99/pair98`，但它仍是 sidecar，不是超越 xuan 的完整答案。
- 新增 aggressive 变体：`early + 30-60 delta5/top100 + price<0.50 residual 180s else 120s + prev_delta>=0.14 -> clip160` 的 L2 PnL 达到 `+$259.20`，5/5 天为正，+5c friction 后仍 `+$106.20`。它仍远低于 xuan 的 `+$14.4k` 规模，但已经是当前最强 public-replay sidecar，达到 shadow 验证门槛。

## Shadow 交付状态

已把当前最强 replay 候选收敛成 shadow sidecar 包：

- 配置：`configs/xuan/fastcancel_shadow_sidecar_v1.json`
- runbook：`docs/research/FASTCANCEL_SHADOW_SIDECAR_V1_ZH.md`
- replay event fixture：`scripts/emit_fastcancel_shadow_events_from_replay.py`
- shadow event report：`scripts/summarize_fastcancel_shadow_events.py`

事件夹具 smoke/full 均已通过：

| metric | value |
|---|---:|
| replay episodes | `237` |
| emitted events | `994` |
| proxy fills | `46` |
| proxy fill rate | `19.41%` |
| raw replay PnL | `+$284.60` |
| actual execution truth | `false` |
| enforce evaluable | `false` |

收益结构拆解：

| bucket | raw PnL | interpretation |
|---|---:|---|
| completion | `+$140.20` | clean close 主利润 |
| slow_completion | `+$36.40` | slow99/pair98 有增量但样本少 |
| repair | `+$1.80` | repair 基本不是盈利模块 |
| residual_settle | `+$106.20` | raw 贡献高，必须用真实 exit VWAP 复核 |
| early window | `+$91.20` | 频率低但质量高 |
| late window | `+$193.40` | 当前主频率来源 |

新增裁决：

- 现在可以进入 shadow runner 实现/运行阶段，但仍不可 enforce。
- `residual_settle` raw 贡献约三分之一，所以真实 `120s/180s` exit VWAP 是 P0。
- 若真实 maker fill quality 接近 `queue_full+60` 或更差，该 sidecar 直接降级为 research-only。
- 若 completion VWAP drift 超过 `p50 1c / p90 3c`，当前 replay 收益安全垫不足。
