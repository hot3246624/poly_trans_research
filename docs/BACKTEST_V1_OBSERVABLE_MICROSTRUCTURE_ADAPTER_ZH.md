# Backtest V1 Observable Microstructure Adapter

日期：2026-06-17

## 定位

`observable_microstructure_adapter_v1` 是 Backtest V1 的公开盘口可成交性 gate。它用于消费策略侧 no-submit public/orderbook collector packet，检查真实公开 CLOB book 观测是否支持 no-order replay 中的假想触发、成交、pair cost 和 residual 质量。

它不是 owner private truth，也不是 live/promotion gate。即使通过，也只能表示：

```text
research_observable_microstructure_ready=true
canary_preflight_material_ready=true
private_truth_ready=false
strategy_promotion_ready=false
live_orders_allowed=false
deployable=false
```

## 默认输入

默认 collector contract：

```text
/Users/hot/web3Scientist/pm_as_ofi-localagg/data/exports/research_v4_book_touch_first_collector_contract_packet_20260617T_v4_observable/collector_contract.json
```

默认输入目录当前指向已拉回的 v3 no-submit 包：

```text
/Users/hot/web3Scientist/pm_as_ofi-localagg/data/exports/research_v3_no_submit_public_orderbook_remote_20260617T040342Z
```

这个 v3 包没有 contract 要求的独立 `book_snapshot_csv` / `book_touch_candidate_csv`，所以正确结果应是 fail-closed。等 v4 collector 完成并拉回本地后，用 `--input-dir` 指向 v4 run 目录。

## 运行

```bash
cd /Users/hot/web3Scientist/poly_trans_research
uv run python scripts/evaluate_observable_microstructure_adapter_v1.py
```

指定 v4 包：

```bash
uv run python scripts/evaluate_observable_microstructure_adapter_v1.py \
  --input-dir /Users/hot/web3Scientist/pm_as_ofi-localagg/data/exports/<v4_run_dir>
```

如文件名不符合默认探测规则，可以显式指定：

```bash
uv run python scripts/evaluate_observable_microstructure_adapter_v1.py \
  --contract /path/to/collector_contract.json \
  --book-snapshot-csv /path/to/book_snapshot.csv \
  --candidate-csv /path/to/book_touch_candidate.csv \
  --run-scorecard /path/to/scorecard.json
```

## 输出

默认输出目录：

```text
$POLY_BT_ROOT/derived/contract_examples/observable_microstructure_adapter_v1_latest/
```

核心文件：

```text
OBSERVABLE_MICROSTRUCTURE_ADAPTER_V1_EVAL.json
observable_microstructure_candidate_rows.csv
threshold_failures.csv
stop_condition_events.csv
```

通过状态：

```text
status=OK_RESEARCH_OBSERVABLE_MICROSTRUCTURE_READY_PROMOTION_BLOCKED_PRIVATE_TRUTH
evaluation_passed=true
research_observable_microstructure_ready=true
private_truth_ready=false
strategy_promotion_ready=false
live_orders_allowed=false
deployable=false
```

阻断状态：

```text
status=BLOCKED_OBSERVABLE_MICROSTRUCTURE_ADAPTER_V1_FAIL_CLOSED
```

## Gate 口径

脚本读取 `collector_contract.json` 中的 gates：

```text
min_observed_markets >= 100
intent_market_coverage_over_discovered >= 0.85
filled_market_coverage_over_discovered >= 0.85
market_fill_retention >= 0.90
qty_fill_conversion >= 0.90
pair_cost_proxy <= 0.90
residual_qty_proxy <= 0.12
fee_after_pnl_proxy > 0
```

同时 fail-closed 检查：

```text
collector_contract 存在
run_scorecard 存在
book_snapshot_csv 存在且严格等于 contract schema
book_touch_candidate_csv 存在且严格等于 contract schema
run_scorecard 包含 contract required fields
output_hashes 包含 sha256
submit_allowed/sign_allowed/cancel_allowed 全 false
non_claims.private_truth/order_execution/live/deploy/promotion 全 false
```

## 顶层 readiness

`scripts/build_xuan_backtest_v1_strategy_readiness_gate.py` 已读取：

```text
$POLY_BT_ROOT/derived/contract_examples/observable_microstructure_adapter_v1_latest/OBSERVABLE_MICROSTRUCTURE_ADAPTER_V1_EVAL.json
```

它会在 `readiness_layers.observable_microstructure_adapter` 下展示微结构可成交性结果。这个 layer 可以阻断 canary/promotion 讨论，但不能设置 private truth、promotion、live 或 deployable。

## 边界

禁止把以下证据升级为 private truth：

```text
public CLOB book observation
no-submit hypothetical fill
pair_cost_proxy
residual_proxy
fee_after_pnl_proxy
public/profile/replay/no-order output
```

真实 `private_truth_ready=true` 仍然只能来自未来 owner-side orders/fills/inventory/redeem/fee/PnL reconciliation。
