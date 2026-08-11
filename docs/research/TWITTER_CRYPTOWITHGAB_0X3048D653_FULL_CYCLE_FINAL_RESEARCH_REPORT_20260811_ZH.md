# 0x3048d653 / twitter-CryptoWithGab 全周期公开账本研究最终报告

生成日期：2026-08-11（BJT）
账户：`0x3048d65321be3497164cdfc2996f94f98a2e7537`
公开主页：[Polymarket profile](https://polymarket.com/zh/@twitter-cryptowithgab)
状态：`FINAL_PUBLIC_LEDGER`。本报告替代同日的 240 小时初稿；它不是认证 CLOB 执行真相，也不是下单建议。

## 0. 最终结论

从公开 `activity` 事件能看到的最早活动开始，到 2026-08-11 的归档边界，这个账户在费后公开现金账上确实是盈利的：

- 全账户：**+$84,290.85**，按实际买入扣款计算 ROI **+1.586%**；
- 其中 BTC 5m：**+$76,402.66**，ROI **+1.437%**；
- 公开 `usdcSize` 扣款相对 `size × price` 的 fee-like 成本约 **$117,025.82**，占毛买入报价 **2.251%**；
- 公开事件显示其绝大多数行为是 **BUY -> 结算 REDEEM**，不是经典的双边买卖做市；`MERGE=0`，全周期 SELL 只有 **$17.96**；
- 事后双侧配对的 fee-inclusive side-VWAP `actual_pair_cost=0.994926`，对应 paired actual profit 约 **+$24,129.91**；但这只解释总现金利润的一部分，不能把它当作实时可锁定套利；
- 按残仓腿实际均价计算的全周期 RER 约 **10.99%**。这不是低残仓、近市场中性的 complete-set arb；其利润明显依赖残余单腿的结算结果和入场选择。

因此，最准确的策略分类是：

> **BTC 5m 高频 complete-set accumulation / completion controller，加上有界但显著的 inventory/residual settlement exposure。**

它是值得研究的盈利样本，但目前不能升级为“可直接复制的 P0 实现对象”。公开数据无法证明 maker/taker、post-only、队列位置、撤单、补腿时序或私有库存管理规则。

## 1. 研究边界与数据来源

### 1.1 可审计窗口

归档窗口为 UTC `2026-06-10T00:00:00Z` 至 `2026-08-11T09:26:04Z`，对应北京时间：

- 账本边界：`2026-06-10 08:00:00` 至 `2026-08-11 17:26:04`；
- 窗口内最早实际事件：`2026-06-10 09:35:12 BJT`；
- 窗口内最后实际事件：`2026-08-11 17:06:10 BJT`。

从更早的公开时间范围做了 offset=0 的预检，返回 0 条；这支持“6 月 10 日是当前公开 API 能看到的最早活动”这一边界，但不能证明该地址的链上创建时间或所有历史钱包流入流出。

### 1.2 归档与校验

公开 API 数据先进入可断点续跑的 SQLite，再由 canonical ledger 脚本读取 `raw_json` 计算现金账。归档不是把 `offset=3000` 之后的结果硬当历史，而是按时间范围切片；当某个切片触及 offset 上限时递归二分。

| 校验项 | 结果 |
|---|---:|
| API 返回行数 | 745,831 |
| SQLite 唯一事件 | 388,645 |
| 活动扫描页数 | 1,865 |
| 完成任务 | 480 |
| 自动拆分任务 | 102 |
| 扫描错误 | 0 |
| JSON 无效行 | 0 |
| 活动中的运行任务 | 0 |
| 唯一事件 ID 与行数一致 | 是 |

原始归档：

`outputs/account_3048d653_full_cycle_archive_20260811/activity.sqlite`

归档完成标记：

`outputs/account_3048d653_full_cycle_archive_20260811/EXIT.json`

独立校验：

`outputs/account_3048d653_full_cycle_archive_20260811/validation.json`

### 1.3 现金账口径

对 BUY 使用 `activity.usdcSize` 作为实际协议层扣款；因此购买手续费已经计入 `buy_actual_cost`。同时保留 `size × price` 的报价成本，二者差额记为 `fee_like_cost`，不把它擅自解释成认证 CLOB fee schedule 的完整真相。

公开现金账为：

```text
cash_pnl = SELL proceeds + MERGE proceeds + REDEEM proceeds
           + MAKER_REBATE + REWARD proceeds
           - BUY usdcSize
```

这是真实公开事件的现金流差额，不等于前端 UI PnL，也不自动等于包含外部转账、未观测起始库存后的完整钱包净资产变化。窗口边界审计发现：

- 没有 `no-buy redeem`：`$0`；
- 没有早于该窗口内首次 BUY 的 redeem：`$0`。

这消除了本次窗口最主要的一类历史库存污染，但仍不能替代链上 transfer 对账。

## 2. 全周期账本结果

### 2.1 全账户与 BTC 5m 对照

| 指标 | 全账户 | BTC 5m |
|---|---:|---:|
| 唯一 activity 事件 | 388,645 | 388,579 |
| 交易市场 | 14,758 | 14,755 |
| TRADE 事件 | 374,018 | 374,014 |
| REDEEM 事件 | 14,566 | 14,565 |
| 买入 shares | 10,671,738.16 | 10,666,730.30 |
| gross BUY cost (`size×price`) | $5,198,154.60 | $5,198,145.25 |
| actual BUY cost (`usdcSize`) | **$5,315,180.42** | **$5,315,170.62** |
| fee-like cost | **$117,025.82** | **$117,025.37** |
| fee-like / gross BUY | **2.251%** | **2.251%** |
| SELL proceeds | $17.96 | $15.98 |
| MERGE proceeds | $0 | $0 |
| REDEEM proceeds | $5,391,557.29 | $5,391,557.29 |
| rebate + reward proceeds | $7,896.02 | $0* |
| fee-inclusive cash PnL | **+$84,290.85** | **+$76,402.66** |
| ROI / actual BUY cost | **+1.586%** | **+1.437%** |

`*` BTC 5m 过滤结果只按 slug 归入市场，无法把没有 slug 的 rebate/reward 归入 BTC 5m，因此全账户列是包含这些公开收入的完整现金账。

全账户毛利润（按 gross BUY cost、尚未扣 fee-like cost）为 **+$201,316.67**。fee-like 成本约吃掉毛利润的 **58.1%**；这正是不能只看前端盈利或 `size×price` 的原因。

### 2.2 活跃日稳定性

按 BJT 日期聚合，仅统计有公开活动的日期，不把无交易日当作亏损日：

| 指标 | 全账户 | BTC 5m |
|---|---:|---:|
| 活跃日 | 61 | 60 |
| 正现金流日 | 51 | 49 |
| 负现金流日 | 10 | 11 |
| 正日比例 | 83.6% | 81.7% |
| 从窗口起点累计最大回撤 | -$3,010.01 | -$3,098.73 |

完整日序在：

`outputs/account_3048d653_full_cycle_ledger_20260811_with_positions/daily_cashflow.csv`

这条权益曲线是公开现金流曲线，不含起始账户余额和外部转账。它说明盈利不是单日偶然，但不能据此推断低风险或未来持续。

## 3. 残仓、配对与利润来源

### 3.1 配对诊断

以下指标是对已经完成的全周期交易按市场做的 side-VWAP 回溯诊断，不是 FIFO 实时锁定成本，也不是 L2 executable price：

| 指标 | 全周期结果 |
|---|---:|
| 有双侧成交的市场 | 13,985 |
| paired quantity | 4,755,245.69 |
| gross side-VWAP pair cost | 0.972755 |
| actual side-VWAP pair cost | **0.994926** |
| paired actual profit | **+$24,129.91** |
| paired gross profit | +$129,557.50 |
| paired actual cost 的市场中位数 | 0.996476 |
| paired actual cost p75 / p90 | 1.038842 / 1.091190 |

`actual_pair_cost` 已用 BUY 的 `usdcSize` 反映 fee-like 成本。可是双侧均价之和会把不同时间抓到的两条腿事后平均到一起，不能回答“当时第一腿之后，第二腿是否能按该价格成交”。因此该数只适合做账户画像，不适合作为复现策略的入场 KPI。

### 3.2 残仓敞口 RER

按每个市场的净未配平 shares 计算：

```text
residual_notional = residual shares × 残仓腿的 actual 入场均价
traded_notional = YES actual cost + NO actual cost
RER = sum(residual_notional) / sum(traded_notional)
```

全周期结果：

- 净残仓 shares：**1,161,246.77**；
- 残仓成本敞口：约 **$584,064.63**；
- 实际 BUY notional：约 **$5,315,180.42**；
- 聚合 RER：**10.99%**；
- 按市场计算的 RER p50 / p90 / p95：**10.46% / 53.28% / 100%**；
- 有任何生命周期双侧不平衡的市场：14,419 / 14,758，即 **97.70%**。

最后一个市场比例不能解释为当前仍有 97.7% 的仓位未平。绝大多数市场已经结算；它只表示生命周期内曾经出现过不平衡。当前公开 positions 快照包含 293 个市场，其中只有 1 个有非零价值、总当前价值约 **$20**，其余大量行是已结算或零值的一侧记录。这个 endpoint 不能作为活跃库存的私有真相。

### 3.3 这笔利润到底来自哪里

全周期 fee-inclusive cash PnL 为 `$84,290.85`，而回溯 paired actual profit 只有约 `$24,129.91`，约占现金利润 **28.6%**。两者不是严格可加的同一会计分解，因此不能把差额机械命名为 alpha；但它明确反驳了“利润几乎全部来自无风险双边 pair arb”的解释。

更稳妥的解释是：

1. 账户在 BTC 5m 中大量买入两侧，形成 completion/accumulation 行为；
2. 很多市场没有及时以第二腿完全收口，残余一侧进入结算；
3. 入场方向、价格分布和结算胜负共同贡献现金结果；
4. rebates/rewards 对全账户有约 `$7,896.02` 的额外贡献；
5. fee-like 成本极大，粗略 gross edge 不能直接转化为可复制净 edge。

## 4. 240 小时交叉复核

从同一份完整 SQLite 归档重新切出：

`2026-07-26 14:59:29 BJT` 至 `2026-08-05 14:59:29 BJT`

结果为：

- actual BUY cost：`$880,198.87`；
- fee-like cost：`$20,729.90`，即 gross BUY 的 `2.412%`；
- REDEEM：`$888,932.30`；
- MAKER_REBATE：`$1,124.37`；
- fee-inclusive cash PnL：**+$9,857.80**；
- ROI / actual BUY cost：**+1.120%**；
- 10 个有活动日期中 8 天正、2 天负；
- RER：约 **10.06%**。

早先 `outputs/tmp_addr_3048d6_240h` 的结果来自较早的 public API 快照。新旧快照在 4 个市场各相差 60 shares，总 actual BUY cost 相差约 `$116.40`；因此早先 240 小时数字不再作为 canonical 结果。新归档的 240 小时切片才是本报告口径。

## 5. 策略分类与可复现性判定

### 5.1 不应归类为纯经典 A-S

公开行为有以下硬特征：

- TRADE 374,018 条，绝大多数是 BUY；
- REDEEM 14,566 条；
- MERGE 0；
- SELL proceeds 仅 `$17.96`；
- BTC 5m 占全部事件和资金流的绝大部分。

这更接近针对二元 5 分钟市场结算机制定制的 **completion/inventory controller**，而不是“在 YES/NO 两边持续报价、靠买卖 spread 赚取 A-S 做市利润”的标准形态。它可能使用 maker order 获取份额，但公开 activity 不能证明这一点，更不能证明我们能获得相同队列成交。

### 5.2 研究级别

| 维度 | 判定 |
|---|---|
| 公开账本是否真实显示盈利 | **是，强证据** |
| 是否 fee-inclusive | **是，使用 BUY `usdcSize`** |
| 是否低风险 market-neutral pair arb | **否，未支持** |
| 是否依赖 residual/settlement | **明显依赖，RER 10.99%** |
| 是否能从公开数据证明 maker-only | **不能** |
| 是否能直接复制该账户 | **不能** |
| 是否值得逆向研究 | **值得，作为高价值研究样本** |
| 是否 P0 implementation | **否，暂定 P1 research target** |

## 6. 给研发同事的可执行方向

### 必须保留的事实约束

1. 所有回测先使用 `activity.usdcSize` 或真实 executable fill，禁止先用 `size×price` 再事后减一个固定费率。
2. `actual_pair_cost` 只能做 retrospective profile；实时策略必须用 FIFO、决策时点可见的 L2 ask/bid 和第二腿成交模型。
3. 目标不是把 RER 强行压到零，而是在 fee 后 PnL 为正的前提下，测量 RER、RER p50/p90、残仓尾部和结算损失。
4. 必须把 `residual_realized_pnl` 单列，明确标记为 settlement outcome，不把它伪装成 pair edge。
5. 不得从这个地址推断 maker/taker、队列优先级或自建外部价格信号。

### 最小复现研究顺序

1. **账户行为重建**：按每个 BTC 5m market 重建第一腿时间、两腿时间差、价格区间、近结算成交比例、第二腿补仓比例和结算方向。
2. **状态变量抽取**：只用当时可见的 PM book/trade/inventory 状态，构造 `no_order` shadow controller；禁止读取未来成交和结算赢家。
3. **两条基线**：
   - fee-inclusive FIFO completion baseline；
   - inventory-aware accumulation baseline，允许有界 residual，但使用冻结的 residual penalty 和 stop 规则。
4. **执行桥**：将同一决策接到历史 L2 top1/top5；若没有同一时间段 L2，报告必须标 `MECHANISM_ONLY`，不能声称可执行复现。
5. **淘汰条件**：在 fee 后、结算计入、非重叠窗口下 ROI 不为正，或 residual loss 吃掉主要利润，立即降级，不再用 UI PnL 挽救。

本账户能提供的是高质量的行为校准和利润来源假设，不能直接提供它的 alpha 信号。研发目标应是学习“何时开始积累、何时暂停、何时优先补第二腿、何时接受残仓”，而不是复制它的逐笔成交。

## 7. 可复现产物

### 归档与账本

- SQLite：`outputs/account_3048d653_full_cycle_archive_20260811/activity.sqlite`
- 归档退出证明：`outputs/account_3048d653_full_cycle_archive_20260811/EXIT.json`
- 独立归档校验：`outputs/account_3048d653_full_cycle_archive_20260811/validation.json`
- 全账户 summary：`outputs/account_3048d653_full_cycle_ledger_20260811_with_positions/summary.json`
- BTC 5m summary：`outputs/account_3048d653_full_cycle_btc5m_ledger_20260811/summary.json`
- 全账户日序：`outputs/account_3048d653_full_cycle_ledger_20260811_with_positions/daily_cashflow.csv`
- 全账户市场现金流：`outputs/account_3048d653_full_cycle_ledger_20260811_with_positions/market_cashflow_metrics.csv`
- 全账户市场交易/配对诊断：`outputs/account_3048d653_full_cycle_ledger_20260811_with_positions/market_trade_metrics.csv`
- 全账户 RER 独立计算：`outputs/account_3048d653_full_cycle_ledger_20260811_with_positions/residual_exposure_metrics.json`
- positions 快照：`outputs/account_3048d653_full_cycle_ledger_20260811_with_positions/current_positions.csv`
- canonical 240 小时切片：`outputs/account_3048d653_recalc_240h_all_from_full_archive_20260811/`

### 工具

- 可断点、按时间递归切片的公开归档器：`scripts/collect_account_activity_history_resumable.py`
- 使用本地归档运行 canonical ledger：`scripts/analyze_account_activity_archive.py`
- 归档一致性与边界校验：`scripts/validate_account_activity_archive.py`
- 残仓敞口 RER 计算：`scripts/compute_market_residual_exposure.py`

典型复核命令：

```bash
cd /Users/hot/web3Scientist/poly_trans_research

python3 scripts/validate_account_activity_archive.py \
  --db outputs/account_3048d653_full_cycle_archive_20260811/activity.sqlite \
  --wallet 0x3048d65321be3497164cdfc2996f94f98a2e7537 \
  --start-iso 2026-06-10T00:00:00Z \
  --end-iso 2026-08-11T09:26:04Z \
  --output outputs/account_3048d653_full_cycle_archive_20260811/validation.json

python3 scripts/analyze_account_activity_archive.py \
  --db outputs/account_3048d653_full_cycle_archive_20260811/activity.sqlite \
  --user 0x3048d65321be3497164cdfc2996f94f98a2e7537 \
  --start-iso 2026-06-10T00:00:00Z \
  --end-iso 2026-08-11T09:26:04Z \
  --output-dir outputs/account_3048d653_full_cycle_ledger_20260811_with_positions
```

## 8. 最终裁定

`0x3048d653` 不是此前那种“前端看起来赚钱、公开账本无法闭合”的账户。当前能取得的公开 activity 已经足够给出一个稳健结论：**从最早可见公开活动到 2026-08-11，它在计入 BUY 实际扣款、REDEEM、rebate/reward 后仍然净赚约 $84.3K。**

但它也不是已被证明的低风险、可直接复制的 maker pair-arb：**RER 约 11%，paired actual profit 只占总现金利润约 28.6%，其余结果与 residual/settlement exposure 高度相关。**

所以正确的研发动作不是继续争论它是不是“神秘专业团队”，而是把它作为一个已经确认盈利、但执行和方向机制尚未识别的 completion/inventory 样本，按照上述状态重建、fee 后回放和 L2 bridge 顺序推进。
