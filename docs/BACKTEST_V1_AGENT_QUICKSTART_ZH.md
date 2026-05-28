# Backtest V1 Agent Quickstart

这份说明给接手的同事使用。完整背景见 `docs/BACKTEST_ARCHITECTURE_V1_RUNBOOK_ZH.md`；这里只列日常操作入口和边界。

## 1. 默认环境

```bash
cd /Users/hot/web3Scientist/poly_trans_research
export POLY_BT_ROOT=/Users/hot/web3Scientist/poly_backtest_data
```

默认只使用 MacBook 本地数据。不要把 `/Volumes/PolyData` 当作回测 root；外盘只在重建 raw/replay/L2 冷数据时使用。

## 2. 先跑健康检查

```bash
uv run --with duckdb python scripts/validate_multiasset_backtest_v1_local_install.py --strict-duckdb
```

可交付状态应满足：

```text
status=OK
fail_count=0
warn_count=0
external_polydata_runtime_ref_count=0
required_config_git_tracked_count=required_config_count
required_runner_git_tracked_count=required_runner_count
required_support_file_git_tracked_count=required_support_file_count
```

当前已验证：`uv run pytest` 通过，`59 passed`。

## 3. 主要产物

```text
local install gate:
$POLY_BT_ROOT/derived/contract_examples/multiasset_backtest_v1_local_install_validation_latest.json

7-asset search-safe event store:
$POLY_BT_ROOT/derived/multiasset_l1_flow_event_store_v1/20260502_20260518_minsz10/event_store.duckdb

7-asset completion/residual adapter:
$POLY_BT_ROOT/derived/contract_examples/multiasset_completion_state_machine_from_l1_flow_v1/RESULT_SUMMARY_MANIFEST.json

BTC normalized completion/residual adapter:
$POLY_BT_ROOT/derived/contract_examples/btc_completion_state_machine_from_l1_flow_taker_normalized_v1/RESULT_SUMMARY_MANIFEST.json

L2 top-aligned mart:
$POLY_BT_ROOT/derived/contract_examples/l2_top_aligned_mart_20260502_20260518_l2/L2_TOP_ALIGNED_MART_MANIFEST.json

BTC parity gate:
$POLY_BT_ROOT/derived/contract_examples/backtest_v1_btc_parity_latest/BACKTEST_V1_BTC_PARITY_GATE.json

xuan bridge scorecard:
$POLY_BT_ROOT/derived/contract_examples/xuan_bridge_scorecard_latest/XUAN_BRIDGE_SCORECARD_MANIFEST.json

multiasset strict rescue:
$POLY_BT_ROOT/derived/contract_examples/multiasset_strict_rescue_opportunity_latest/MULTIASSET_STRICT_RESCUE_OPPORTUNITY_REPORT.json

multiasset merge/residual turnover:
$POLY_BT_ROOT/derived/contract_examples/multiasset_merge_turnover_latest/MULTIASSET_MERGE_TURNOVER_REPORT.json

xuan strategy readiness gate:
$POLY_BT_ROOT/derived/contract_examples/xuan_backtest_v1_strategy_readiness_latest/XUAN_BACKTEST_V1_STRATEGY_READINESS_GATE.json
```

## 4. 常用查看命令

```bash
jq '.status, .summary' \
  $POLY_BT_ROOT/derived/contract_examples/multiasset_backtest_v1_local_install_validation_latest.json

jq '{status, core_metrics}' \
  $POLY_BT_ROOT/derived/contract_examples/multiasset_completion_state_machine_from_l1_flow_v1/RESULT_SUMMARY_MANIFEST.json

jq '{status, core_metrics}' \
  $POLY_BT_ROOT/derived/contract_examples/btc_completion_state_machine_from_l1_flow_taker_normalized_v1/RESULT_SUMMARY_MANIFEST.json

jq '{status, summary, blockers}' \
  $POLY_BT_ROOT/derived/contract_examples/backtest_v1_btc_parity_latest/BACKTEST_V1_BTC_PARITY_GATE.json

jq '{status, summary, interpretation}' \
  $POLY_BT_ROOT/derived/contract_examples/xuan_bridge_scorecard_latest/XUAN_BRIDGE_SCORECARD_MANIFEST.json

jq '{status, strategy_research_ready, strategy_research_readiness_level, strategy_promotion_ready, warnings}' \
  $POLY_BT_ROOT/derived/contract_examples/xuan_backtest_v1_strategy_readiness_latest/XUAN_BACKTEST_V1_STRATEGY_READINESS_GATE.json
```

## 5. 重新跑 search-safe pipeline

```bash
uv run --with duckdb python scripts/run_backtest_search_matrix.py \
  --config configs/backtest/search_multiasset_l1_flow_matrix_formal_v1.json

uv run --with duckdb python scripts/run_backtest_matrix_batch.py \
  --config configs/backtest/search_multiasset_l1_flow_batch_formal_v1.json
```

后续 catalog、compare、shortlist、validation、audit 入口在完整 runbook 中逐条列出。

## 6. 口径边界

- `best_queue_pnl` 只是 search-safe queue screener 指标，不等于 xuan 完整策略 PnL。
- xuan 口径要看 `xuan_bridge_scorecard` 和 `xuan strategy readiness gate`。Scorecard 的 `bridge_category` 分为 `queue_screener_search_safe`、`completion_adapter_research`、`xuan_compatible_bridge`；A 类不能用于判断 xuan 策略好坏。
- merge/redeem turnover 必须和 residual 风险分开：`paired_mergeable_qty/cost`、`merge_recovered_capital`、`capital_turnover` 是资金复用；`market_end_residual_qty/cost`、`residual_zero_stress_loss`、`actual_settlement_residual_pnl` 是 residual 归因。
- BTC parity gate 当前预期仍是 `BLOCKED_BTC_BASELINE_PARITY_NOT_PROVEN`。这是已知边界，不是安装失败。
- 历史 shadow/no-order 没有 owner private truth，不能标记 `private_truth_ready=true`。
- L2 使用 `md_book_l2_top_aligned`：L1 canonical top + L2 depth/provenance。不要把 raw `md_book_l2` side snapshot 当作 top-of-book truth。
- 当前总 gate 预期是 `PARTIAL_XUAN_BACKTEST_V1_STRATEGY_RESEARCH_READY_NOT_PROMOTION`；可以用于研究推进，不能 promotion/deploy/live。

## 7. 常见问题

如果 gate 报 core replay DuckDB view 仍指向外盘：

```bash
uv run --with duckdb python scripts/repair_replay_store_duckdb_view_paths.py \
  --duckdb $POLY_BT_ROOT/verification_store/replay_store_multiasset_core_v1/20260502_20260518_core/store.duckdb
```

如果需要重建 L2 或 raw/replay 元数据，才连接 PolyData，并先看完整 runbook 的 L2 部分。
