# NAGI777 逆向研究与超越路线

Status: `KEEP_NAGI777_REVERSE_ENGINEERING_REVIEWED_PUBLIC_ONLY_MAKER_QUEUE_TELEMETRY_REQUIRED_NOT_DEPLOYABLE`

## 0. 一句话结论

把 ce25 放下后，nagi777 是当前更值得继续研究的对象，但不能把它理解为简单 pair-arb。最近 96h 的公开 activity 显示：它是盈利的，fee-inclusive cash PnL 为 $12,056.32，ROI 1.60%；但 pair PnL 只有 $-179.34，residual PnL 是 $12,235.66。所以它最近真正赚钱的位置在 residual/inventory，而不是无脑买 YES+NO merge。

可复刻的核心不是“追 taker 成交”，而是：post-only / maker / 零费队列捕获 + 残仓方向模型 + 超时 repair/kill switch。本地 maker-queue frontier 里 fee0 有大面积正边，但官方 taker fee07 规模化全灭；这也是为什么直接复制公开成交会失败。

## 1. 来源与边界

- 账户：`0xbf337426aa856996b8bb79b238345dd1a0276bf7`
- 最新公开 activity profile：`/Users/hot/web3Scientist/poly_trans_research/data/exports/profile_nagi_20260604_1110_to_20260608_1110_bjt`
- 最新窗口：2026-06-04T11:10:00+08:00 -> 2026-06-08T11:10:00+08:00 BJT
- activity rows：98,894；markets：1,147
- 指标口径：BUY 成本使用 `usdcSize`，PnL 是 fee-inclusive public cash PnL。
- 不能从公开 activity 证明第三方真实 maker/taker、私有排队位置、撤单逻辑或 authenticated trader_side。
- 本报告不授权、不准备、不建议任何下单；它只是给实现同事的研究规格。

## 2. 最近 96h：nagi 真的在赚钱，但赚钱结构变了

| 窗口 | markets | buy actual | cash pnl | ROI | pair cost | pair pnl | residual pnl | resid | fee | bad pc>=1 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-06-04 11:10 -> 2026-06-08 11:10 BJT | 1147 | $751,743.49 | $12,056.32 | 1.60% | 1.0003 | $-179.34 | $12,235.66 | 12.12% | $0.00 | 49.09% |
| 旧 4-window 快照 | 1123 | $798,783.27 | $5,399.32 | 0.68% | 0.9747 | $18,448.78 | $-13,049.46 | 12.10% | 0.00% | 47.06% |

解释：旧快照是 pair PnL 大正、residual 大负；最新 96h 变成 pair 接近不赚钱、residual 大正。这说明 nagi 的近期强势不是稳定无风险套利，而是残仓方向暴露打对了。要超越它，必须比它更少吃坏 residual，而不是只模仿配对。

### 日级拆分

| BJT day | markets | buy actual | cash pnl | roi | pair cost | pair pnl | residual pnl | resid | bad pc>=1 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-06-04 | 154 | $68,157.58 | $3,030.44 | 4.45% | 0.9574 | $2,701.60 | $328.84 | 11.67% | 35.41% |
| 2026-06-05 | 283 | $120,262.78 | $721.85 | 0.60% | 0.9865 | $1,436.82 | $-714.97 | 13.51% | 44.68% |
| 2026-06-06 | 288 | $216,470.69 | $992.72 | 0.46% | 1.0304 | $-5,443.63 | $6,436.35 | 11.48% | 53.79% |
| 2026-06-07 | 288 | $267,201.57 | $2,206.61 | 0.83% | 1.0017 | $-403.56 | $2,610.17 | 11.05% | 52.15% |
| 2026-06-08 | 134 | $79,650.88 | $5,104.70 | 6.41% | 0.9769 | $1,529.42 | $3,575.28 | 15.56% | 44.42% |

## 3. nagi 的强桶与弱桶

### 保留研究桶

| candidate | markets | buy cov | buy actual | cash pnl | roi | pair cost | pair pnl | residual pnl | resid | bad pc>=1 | decision |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| NAGI_DOWN_FIRST_CORE | 561 | 46.87% | $352,361.05 | $9,293.24 | 2.64% | 0.9950 | $1,477.73 | $7,815.51 | 13.80% | 46.32% | KEEP_FOR_SIDE_GATE |
| NAGI_DOWN_FIRST_RESID_UP | 245 | 19.80% | $148,881.98 | $5,480.82 | 3.68% | 0.9891 | $1,405.13 | $4,075.70 | 12.54% | 48.48% | KEEP_AS_MODEL_TARGET |
| NAGI_LAST60_35_50_PAIR_CONTROL | 396 | 37.97% | $285,406.82 | $3,813.74 | 1.34% | 0.9926 | $1,941.08 | $1,872.66 | 8.61% | 49.13% | KEEP_FOR_SHADOW_POLICY |
| NAGI_1_5M_50_65_RESIDUAL_ENGINE | 126 | 9.67% | $72,683.22 | $4,294.38 | 5.91% | 1.0038 | $-183.67 | $4,478.04 | 26.85% | 44.11% | KEEP_FOR_RESIDUAL_RESEARCH_ONLY |
| NAGI_LAST60_FASTPAIR_LE5 | 247 | 27.10% | $203,754.98 | $3,291.25 | 1.62% | 1.0027 | $-500.01 | $3,791.26 | 8.37% | 48.64% | TRANSLATE_TO_TIMEOUT_AND_REPAIR_POLICY |
| NAGI_LAST60_PAIR_5_15 | 246 | 23.82% | $179,054.71 | $2,055.31 | 1.15% | 0.9958 | $696.05 | $1,359.26 | 7.23% | 50.90% | KEEP_FOR_REPAIR_POLICY |
| NAGI_1_5M_PAIR_5_15 | 91 | 6.87% | $51,641.10 | $2,815.19 | 5.45% | 0.9931 | $253.47 | $2,561.72 | 25.00% | 45.57% | KEEP_SMALL_SIZE_RESEARCH |
| NAGI_FIRST65_80_SMALL_SAMPLE | 39 | 2.73% | $20,501.51 | $1,484.75 | 7.24% | 0.9484 | $871.34 | $613.41 | 18.10% | 23.94% | WATCH_ONLY |

### 明确要砍掉或加硬超时的桶

| candidate | markets | buy cov | buy actual | cash pnl | roi | pair cost | pair pnl | residual pnl | resid | bad pc>=1 | decision |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| NAGI_UP_FIRST_RESID_DOWN_AVOID | 276 | 25.74% | $193,519.22 | $-1,242.28 | -0.64% | 1.0183 | $-3,018.43 | $1,776.15 | 11.70% | 54.23% | REJECT_OR_HARD_KILL |
| NAGI_LAST60_PAIR_15_30_WEAK | 163 | 14.84% | $111,590.77 | $-788.14 | -0.71% | 1.0176 | $-1,780.34 | $992.20 | 7.16% | 53.36% | REJECT_OR_TIMEOUT |
| NAGI_LAST60_PAIR_1_3M_BAD | 79 | 4.61% | $34,690.00 | $-1,250.34 | -3.60% | 1.0082 | $-250.83 | $-999.51 | 11.86% | 56.64% | HARD_TIMEOUT |

几个关键读法：

- `NAGI_DOWN_FIRST_CORE` 是最新主线：首腿 DOWN 覆盖 48.9% markets，PnL +$9.29k，ROI 2.64%。
- `NAGI_UP_FIRST_RESID_DOWN_AVOID` 是最明确的失败桶：PnL -$1.24k，pair cost 1.0183；这应该成为第一条 kill switch。
- `NAGI_LAST60_35_50_PAIR_CONTROL` 覆盖大、残仓低，是最像可执行主模板的 public bucket。
- `NAGI_1_5M_50_65_RESIDUAL_ENGINE` ROI 高，但 residual 高达 26%+，更像方向模型，不应当用大仓直接复制。
- pair_delay 是结果/控制变量，不能直接当入场信号；只能转译为 own-fill 后的 repair timeout。

### 诊断矩阵 top rows

| group | bucket | markets | buy cov | cash pnl | roi | pair cost | pair pnl | residual pnl | resid | bad pc>=1 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| resid_side | UP | 555 | 47.19% | $9,486.19 | 2.67% | 0.9912 | $2,766.48 | $6,719.71 | 10.88% | 48.78% |
| first_side | DOWN | 561 | 46.87% | $9,293.24 | 2.64% | 0.9950 | $1,477.73 | $7,815.51 | 13.80% | 46.32% |
| first_side_x_resid_side | DOWN / UP | 245 | 19.80% | $5,480.82 | 3.68% | 0.9891 | $1,405.13 | $4,075.70 | 12.54% | 48.48% |
| first_price_x_last_delta | 50-65 / 1-5m | 126 | 9.67% | $4,294.38 | 5.91% | 1.0038 | $-183.67 | $4,478.04 | 26.85% | 44.11% |
| first_side_x_resid_side | UP / UP | 310 | 27.38% | $4,005.36 | 1.95% | 0.9926 | $1,361.35 | $2,644.01 | 9.67% | 49.00% |
| first_side_x_resid_side | DOWN / DOWN | 315 | 27.05% | $3,818.61 | 1.88% | 0.9995 | $78.80 | $3,739.81 | 14.76% | 44.70% |
| first_price_x_last_delta | 35-50 / last_60s | 396 | 37.97% | $3,813.74 | 1.34% | 0.9926 | $1,941.08 | $1,872.66 | 8.61% | 49.13% |
| last_delta_x_pair_delay | last_60s / <=5s | 247 | 27.10% | $3,291.25 | 1.62% | 1.0027 | $-500.01 | $3,791.26 | 8.37% | 48.64% |
| first_price_x_pair_delay | 50-65 / 5-15s | 173 | 15.40% | $3,137.22 | 2.71% | 0.9973 | $269.17 | $2,868.05 | 11.65% | 50.44% |
| last_delta_x_pair_delay | 1-5m / 5-15s | 91 | 6.87% | $2,815.19 | 5.45% | 0.9931 | $253.47 | $2,561.72 | 25.00% | 45.57% |
| first_side | UP | 586 | 53.13% | $2,763.08 | 0.69% | 1.0047 | $-1,657.07 | $4,420.15 | 10.64% | 51.53% |
| resid_side | DOWN | 591 | 52.79% | $2,576.33 | 0.65% | 1.0088 | $-2,939.63 | $5,515.96 | 13.26% | 49.35% |
| first_price_x_pair_delay | 35-50 / <=5s | 162 | 17.00% | $2,512.85 | 1.97% | 1.0068 | $-731.38 | $3,244.23 | 12.57% | 46.23% |
| last_delta_x_pair_delay | last_60s / 5-15s | 246 | 23.82% | $2,055.31 | 1.15% | 0.9958 | $696.05 | $1,359.26 | 7.23% | 50.90% |
| first_price_x_last_delta | 35-50 / 1-5m | 135 | 9.79% | $1,911.54 | 2.60% | 1.0063 | $-321.63 | $2,233.17 | 25.61% | 46.70% |
| first_price_x_pair_delay | 50-65 / <=5s | 147 | 15.24% | $1,896.17 | 1.65% | 1.0043 | $-427.31 | $2,323.48 | 10.61% | 50.74% |
| first_price_x_pair_delay | 35-50 / 30-60s | 73 | 5.49% | $1,619.97 | 3.93% | 0.9700 | $1,085.53 | $534.44 | 14.40% | 43.05% |
| last_delta_x_pair_delay | last_60s / 30-60s | 113 | 8.06% | $1,301.26 | 2.15% | 0.9675 | $1,858.49 | $-557.23 | 9.82% | 44.41% |

## 4. 本地 maker queue 代理：为什么 taker 复刻会死

| proxy | time | side | band | queue markets | queue qty | fee0 edge qty | taker07 edge qty | pair cost p50 | queue market share | p99 touch lag | p99 align lag |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| best broad | full300 | YES | 0.35-0.5 | 2659 | 417158.04 | 4793.82 | -9378.64 | 0.9900 | 72.71% | 113.00ms | 214.25ms |
| nagi anchor | last60 | YES | 0.35-0.5 | 666 | 71830.81 | 821.13 | -1614.05 | 0.9900 | 57.61% | 113.52ms | 164.80ms |
| residual matrix best | last60 | YES | 0.35-0.5 | 665 | 71556.87 | 821.13 | -1604.75 | 0.9900 | 57.53% | 113.54ms | 165.10ms |

frontier 扫描 5010 个 variants：fee0 scale pass 2185，fee0 high coverage pass 1456，taker fee07 scale pass 0。这基本把路径分清了：若我们付 taker fee，nagi 这类策略不应作为可复刻路线；若我们能证明 post-only maker 零费成交，才有研究价值。

## 5. 超越 nagi 的最小可执行研究规格

### P0: 不碰 taker，先证明自己的 maker fill

- 所有候选都必须 post-only maker-only；任何 taker/ambiguous fill 直接失败。
- 必须记录 own authenticated telemetry：intent、submit、ack、fill、maker/taker flag、fee、cancel ack、book age、queue proxy、pair_cost_at_decision。
- 没有 own truth 之前，只能叫 shadow policy，不能叫复刻成功。

### P1: 主队列模板

- 起点模板：BTC 5m，YES bid 0.35-0.50，pair_cap <= 0.995 或 1.000，final 60s 优先，qmin 先从 0/1/5 分层。
- 更广覆盖模板：full300 YES 0.35-0.50 仅作候选池，不直接大仓；它覆盖高但更依赖 residual 风控。
- 目标不是吃完所有信号，而是只吃能证明 maker/no-fee 且 pair_cost 可修复的触点。

### P2: residual killer，比 nagi 更强的关键

- 第一条 hard kill：避免或极小仓参与 `UP first -> DOWN residual` 结构。
- 最后 60s 后，repair 超时必须硬：15-30s 开始降权，1-3m 直接硬止损或不再扩大残仓。
- 如果 first leg 后 5-15s 内无法以 pair_cost <= 0.995/1.000 修复，仓位进入 residual-risk 模式，后续只允许降风险，不允许追单摊大。
- residual 方向模型至少要解释为什么最近 UP residual 明显优于 DOWN residual；否则这部分利润不可复刻。

### P3: 目标函数

- 第一目标：fee0 maker fill truth rate，而不是前端 PnL。
- 第二目标：bad_pc>=1 share 从 nagi 当前约 49% 降到 <35%。
- 第三目标：resid_rate 控在 <10%-12%，同时保留 `DOWN first` 的收益优势。
- 第四目标：以 7 个滚动 24h 窗口验证，而不是只看一天 winner。

## 6. 失败模式清单

- pair_cost >= 1.10 的市场：241 个，PnL $-35,515.47，pair_cost 1.2036。
- high residual >=35% 的市场：156 个，PnL $6,469.41，resid 47.80%。
- pair_cost <0.95 的市场：462 个，PnL $46,662.13，这是可复刻收益池的主要训练目标。

## 7. 交给实现同事的下一步

1. 先实现 dry-run private maker shadow，不发单也不撤单，只记录如果发 post-only 会在哪里、以什么价、是否会被 taker 化。
2. 用同一套 telemetry 重放 `MAKER_QUEUE_LAST60_YES_35_50`，验证 own queue touch/fill 率是否接近本地代理。
3. 把 `NAGI_UP_FIRST_RESID_DOWN_AVOID`、`LAST60_PAIR_15_30_WEAK`、`LAST60_PAIR_1_3M_BAD` 转成 kill switch。
4. 再做 7 个滚动 24h OOS shadow，不达标不进入真实交易讨论。

## 8. 机器可读产物

- Packet JSON: `/Users/hot/web3Scientist/poly_trans_research/data/exports/nagi777_reverse_engineering_packet_20260608/NAGI777_REVERSE_ENGINEERING_PACKET.json`
- Candidate buckets: `/Users/hot/web3Scientist/poly_trans_research/data/exports/nagi777_reverse_engineering_packet_20260608/nagi777_profile_candidate_buckets.csv`
- Group matrix: `/Users/hot/web3Scientist/poly_trans_research/data/exports/nagi777_reverse_engineering_packet_20260608/nagi777_group_matrix.csv`
- Daily summary: `/Users/hot/web3Scientist/poly_trans_research/data/exports/nagi777_reverse_engineering_packet_20260608/nagi777_daily_summary.csv`
- Top/worst examples: `/Users/hot/web3Scientist/poly_trans_research/data/exports/nagi777_reverse_engineering_packet_20260608/nagi777_top_market_examples.csv`
- Frontier candidates: `/Users/hot/web3Scientist/poly_trans_research/data/exports/nagi777_reverse_engineering_packet_20260608/nagi777_maker_queue_candidates.csv`

