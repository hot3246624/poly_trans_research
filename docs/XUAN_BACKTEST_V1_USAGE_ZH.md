# Xuan Backtest V1 Usage Guide

这份文档是给接手同事的日常使用入口。完整背景见：

```text
docs/BACKTEST_ARCHITECTURE_V1_RUNBOOK_ZH.md
docs/BACKTEST_V1_AGENT_QUICKSTART_ZH.md
```

## 1. 当前定位

Backtest V1 当前是：

```text
MacBook-local multiasset research platform
search-safe screener + L2 evidence + completion/residual adapter + xuan rescore + capital ledger
```

当前不是：

```text
BTC-only baseline 的已证明替代品
owner private truth
promotion/deploy/live-order ready
```

当前期望 gate 状态：

```text
local install gate: OK
xuan strategy readiness: PARTIAL_XUAN_BACKTEST_V1_STRATEGY_RESEARCH_READY_NOT_PROMOTION
strategy_research_ready=true
strategy_promotion_ready=false
private_truth_ready=false
deployable=false
live_orders_allowed=false
BTC parity: BLOCKED_BTC_BASELINE_PARITY_NOT_PROVEN
```

BTC parity 仍阻塞是正确行为。主要原因是旧 BTC baseline 与新 BTC normalized adapter 的 source/taker-side 语义没有证明等价，且历史 shadow/no-order 没有 owner private truth。

## 2. 环境

```bash
cd /Users/hot/web3Scientist/poly_trans_research
export POLY_BT_ROOT=/Users/hot/web3Scientist/poly_backtest_data
```

默认只读 MacBook 本地 compact artifacts。不要把 `/Volumes/PolyData` 当作默认 backtest root。只有重建 raw/replay/L2 冷数据或做极端审计时才需要外盘。

## 3. 先跑健康检查

```bash
uv run --with duckdb python scripts/validate_multiasset_backtest_v1_local_install.py --strict-duckdb
```

快速查看：

```bash
jq '{status, summary: {fail_count: .summary.fail_count, warn_count: .summary.warn_count, external_polydata_runtime_ref_count: .summary.external_polydata_runtime_ref_count, xuan_strategy_readiness_gate_status: .summary.xuan_strategy_readiness_gate_status, canonical_audit_selected_candidate_count: .summary.canonical_audit_selected_candidate_count, compat_audit_selected_candidate_count: .summary.compat_audit_selected_candidate_count}}' \
  $POLY_BT_ROOT/derived/contract_examples/multiasset_backtest_v1_local_install_validation_latest.json
```

通过标准：

```text
status=OK
fail_count=0
warn_count=0
external_polydata_runtime_ref_count=0
canonical_audit_selected_candidate_count=80
compat_audit_selected_candidate_count=6
```

`canonical_audit_selected_candidate_count=80` 是当前主入口。`compat_audit_selected_candidate_count=6` 是旧 search-safe 兼容产物，不作为默认入口。

## 4. 主入口 Artifacts

```text
local install gate:
$POLY_BT_ROOT/derived/contract_examples/multiasset_backtest_v1_local_install_validation_latest.json

xuan strategy readiness gate:
$POLY_BT_ROOT/derived/contract_examples/xuan_backtest_v1_strategy_readiness_latest/XUAN_BACKTEST_V1_STRATEGY_READINESS_GATE.json

canonical audit pack:
$POLY_BT_ROOT/derived/contract_examples/backtest_candidate_audit_pack_with_l2_evidence_latest/BACKTEST_CANDIDATE_AUDIT_PACK_MANIFEST.json

xuan completion/residual rescore:
$POLY_BT_ROOT/derived/contract_examples/xuan_completion_candidate_rescore_latest/XUAN_COMPLETION_CANDIDATE_RESCORE_MANIFEST.json

xuan rescore top candidates:
$POLY_BT_ROOT/derived/contract_examples/xuan_completion_candidate_rescore_latest/xuan_completion_candidate_rescore_top.csv

xuan capital ledger:
$POLY_BT_ROOT/derived/contract_examples/xuan_capital_ledger_latest/XUAN_CAPITAL_LEDGER_REPORT.json

multiasset coverage scorecard:
$POLY_BT_ROOT/derived/contract_examples/multiasset_backtest_coverage_scorecard_latest/MULTIASSET_BACKTEST_COVERAGE_SCORECARD.json

BTC parity gate:
$POLY_BT_ROOT/derived/contract_examples/backtest_v1_btc_parity_latest/BACKTEST_V1_BTC_PARITY_GATE.json

BTC parity field alignment:
$POLY_BT_ROOT/derived/contract_examples/backtest_v1_btc_parity_latest/btc_parity_field_alignment.csv

xuan bridge scorecard:
$POLY_BT_ROOT/derived/contract_examples/xuan_bridge_scorecard_latest/XUAN_BRIDGE_SCORECARD_MANIFEST.json
```

## 5. 日常查看命令

总 gate：

```bash
jq '{status, strategy_research_ready, strategy_research_readiness_level, strategy_promotion_ready, private_truth_ready, deployable, live_orders_allowed, summary, warnings}' \
  $POLY_BT_ROOT/derived/contract_examples/xuan_backtest_v1_strategy_readiness_latest/XUAN_BACKTEST_V1_STRATEGY_READINESS_GATE.json
```

xuan completion/residual 重打分：

```bash
jq '{status, summary}' \
  $POLY_BT_ROOT/derived/contract_examples/xuan_completion_candidate_rescore_latest/XUAN_COMPLETION_CANDIDATE_RESCORE_MANIFEST.json
```

当前核心数值：

```text
market_candidate_count=17,337
positive_xuan_candidate_count=13,409
xuan_after_fee_pnl≈7,767.871711
net_roi≈9.5414%
```

资本账本：

```bash
jq '{status, summary}' \
  $POLY_BT_ROOT/derived/contract_examples/xuan_capital_ledger_latest/XUAN_CAPITAL_LEDGER_REPORT.json
```

当前核心数值：

```text
max_capital_tied≈6,237.69375
average_capital_tied≈3,129.587336
fee_drag≈2,764.784539
turnover_adjusted_roi_on_max_capital≈1.245311
daily_capacity_estimate_at_notional($1000)≈83.020766
```

`daily_capacity_estimate_at_notional` 是 15 个 valid day 全窗口按 day_count 归一后的研究代理，不是实盘承诺。

多币种覆盖：

```bash
jq '{status, summary, by_asset}' \
  $POLY_BT_ROOT/derived/contract_examples/multiasset_backtest_coverage_scorecard_latest/MULTIASSET_BACKTEST_COVERAGE_SCORECARD.json
```

必须按资产看：

```text
search_safe_row_count
search_safe_market_count
search_safe_day_count
selected_count
pair_qty
residual_qty
net_roi
stress_worst_day_fee_after_pnl
```

不要把 BTC/ETH/HYPE/BNB 等权解释；它们的 row_count 和 market coverage 差异很大。

BTC parity：

```bash
jq '{status, summary, blockers, source_semantics_explanation}' \
  $POLY_BT_ROOT/derived/contract_examples/backtest_v1_btc_parity_latest/BACKTEST_V1_BTC_PARITY_GATE.json

column -s, -t < \
  $POLY_BT_ROOT/derived/contract_examples/backtest_v1_btc_parity_latest/btc_parity_field_alignment.csv | head -30
```

`BLOCKED_BTC_BASELINE_PARITY_NOT_PROVEN` 是当前正确状态。不要把它解释成本地安装失败。

## 6. 正确使用候选

默认候选入口不是 queue screener，而是：

```text
$POLY_BT_ROOT/derived/contract_examples/xuan_completion_candidate_rescore_latest/xuan_completion_candidate_rescore_top.csv
```

推荐流程：

```text
1. 先确认 local install gate OK。
2. 读 xuan strategy readiness gate，确认仍是 research-only。
3. 从 xuan_completion_candidate_rescore_top.csv 取 market-level 候选。
4. 对候选检查 canonical audit pack 和 L2 top-aligned evidence。
5. 用 capital ledger 判断 max capital tied、fee drag、turnover-adjusted ROI。
6. 只做 research/scoring，不允许直接进入 live/import。
```

禁止流程：

```text
queue screener best_queue_pnl -> 直接判断 xuan 策略好坏
audit selected count=6 -> 当作当前 canonical candidate count
历史 shadow/no-order -> 标记 private_truth_ready=true
public/proxy evidence -> 伪装成 owner private truth
residual settlement win -> 证明策略 edge
```

## 7. 重新生成研究层

默认用一键 refresh runner。它会按正确顺序重建 research control-plane artifacts，并输出总摘要：

```bash
uv run --with duckdb python scripts/run_xuan_backtest_v1_research_refresh.py
```

输出：

```text
$POLY_BT_ROOT/derived/contract_examples/xuan_backtest_v1_refresh_latest/XUAN_BACKTEST_V1_RESEARCH_REFRESH_SUMMARY.json
```

快速模式可以跳过最重的 L2 rescue 重扫，只刷新其后依赖产物：

```bash
uv run --with duckdb python scripts/run_xuan_backtest_v1_research_refresh.py --skip-heavy-l2-rescue
```

需要把测试也纳入 refresh：

```bash
uv run --with duckdb python scripts/run_xuan_backtest_v1_research_refresh.py --run-tests
```

手工分步命令保留在 `docs/BACKTEST_ARCHITECTURE_V1_RUNBOOK_ZH.md`，日常不要复制粘贴分步流程。

## 8. 指标口径

```text
best_queue_pnl:
  search-safe queue screener 指标，不等于 xuan 策略 PnL。

pair_pnl:
  matched YES/NO pair completion 的研究层 PnL。

merge_recovered_capital:
  paired_mergeable_qty 可 merge/redeem 的 capital recovery，不是 residual edge。

market_end_residual_qty/cost:
  市场结束时未配平的单腿风险。

actual_settlement_residual_pnl:
  residual 的事后结算归因，不能用来证明策略设计 edge。

residual_zero_stress_loss:
  residual 全部归零的压力损失。

xuan_after_fee_pnl:
  pair_pnl + actual_settlement_residual_pnl - official_taker_fee。

max_capital_tied:
  replay 中全局最大未配平库存成本。

turnover_adjusted_roi_on_max_capital:
  全窗口 after-fee PnL / max_capital_tied。
```

## 9. Private Truth Handoff

历史数据只能到 research/proxy evidence。未来进入 owner private truth 的正式链路是：

```text
search-safe candidate
same-window L2/top-aligned validation
xuan completion/residual rescore
future owner canary/live-small execution
owner orders/fills/inventory/redeem/fee reconciliation
private truth gate
```

只有真实 owner execution 后，且 owner orders/fills/inventory/redeem/fee/PnL 全部 reconcile，才可以让：

```text
private_truth_ready=true
strategy_promotion_ready=true
deployable=true
live_orders_allowed=true
```

当前必须保持：

```text
private_truth_ready=false
strategy_promotion_ready=false
deployable=false
live_orders_allowed=false
```

## 10. 常见误读

```text
误读: BTC parity blocked 说明 V1 坏了。
正确: V1 本地安装可用；BTC parity blocked 是 source/taker-side 语义未证明等价。

误读: positive_queue_candidate_count=0 说明 xuan 没候选。
正确: queue screener 不是 xuan 口径；xuan rescore 当前 positive_xuan_candidate_count=13,409。

误读: audit selected count 有 6 和 80，说明数据冲突。
正确: 80 是 with-L2 canonical current pack；6 是旧兼容 search-safe pack。

误读: 7 币种结果可以等权平均。
正确: 必须按 coverage scorecard 分资产解释。

误读: daily_capacity_estimate_at_notional 是实盘收益。
正确: 它是研究代理，还没有 owner private truth 和 live execution 验证。
```
