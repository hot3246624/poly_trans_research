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

BTC parity semantic alignment experiment:
$POLY_BT_ROOT/derived/contract_examples/btc_parity_semantic_alignment_latest/BTC_PARITY_SEMANTIC_ALIGNMENT_EXPERIMENT.json

BTC V1 old/new overlap decomposition:
$POLY_BT_ROOT/derived/contract_examples/btc_v1_old_baseline_overlap_decomposition_latest/BTC_V1_OLD_BASELINE_OVERLAP_DECOMPOSITION_REPORT.json

xuan bridge scorecard:
$POLY_BT_ROOT/derived/contract_examples/xuan_bridge_scorecard_latest/XUAN_BRIDGE_SCORECARD_MANIFEST.json

multiasset strict rescue:
$POLY_BT_ROOT/derived/contract_examples/multiasset_strict_rescue_opportunity_latest/MULTIASSET_STRICT_RESCUE_OPPORTUNITY_REPORT.json

multiasset merge/residual turnover:
$POLY_BT_ROOT/derived/contract_examples/multiasset_merge_turnover_latest/MULTIASSET_MERGE_TURNOVER_REPORT.json

xuan completion/residual rescore:
$POLY_BT_ROOT/derived/contract_examples/xuan_completion_candidate_rescore_latest/XUAN_COMPLETION_CANDIDATE_RESCORE_MANIFEST.json

xuan same-window handoff:
$POLY_BT_ROOT/derived/contract_examples/xuan_completion_candidate_rescore_latest/xuan_completion_candidate_same_window_handoff.csv
$POLY_BT_ROOT/derived/contract_examples/xuan_completion_candidate_rescore_latest/xuan_completion_candidate_same_window_handoff_actions.csv
$POLY_BT_ROOT/derived/contract_examples/xuan_completion_candidate_rescore_latest/xuan_completion_candidate_same_window_handoff_residual_lots.csv

xuan no-order shadow start preflight:
$POLY_BT_ROOT/derived/contract_examples/xuan_same_window_no_order_shadow_start_preflight_latest/XUAN_SAME_WINDOW_NO_ORDER_SHADOW_START_PREFLIGHT.json

xuan capital ledger:
$POLY_BT_ROOT/derived/contract_examples/xuan_capital_ledger_latest/XUAN_CAPITAL_LEDGER_REPORT.json

BTC tiny canary preflight packet:
$POLY_BT_ROOT/derived/contract_examples/btc_same_window_residual_share_le_3pct_v1_canary_preflight_latest/manifest.json

BTC tiny canary no-order shadow evaluator:
$POLY_BT_ROOT/derived/contract_examples/xuan_btc_tiny_canary_no_order_shadow_eval_latest/XUAN_BTC_TINY_CANARY_NO_ORDER_SHADOW_EVAL.json
$POLY_BT_ROOT/derived/contract_examples/xuan_same_window_no_order_shadow_real_ws_runner_eval_latest/XUAN_SAME_WINDOW_NO_ORDER_SHADOW_EVAL.json
$POLY_BT_ROOT/derived/contract_examples/xuan_same_window_no_order_shadow_real_ws_start_scope_eval_latest/XUAN_SAME_WINDOW_NO_ORDER_SHADOW_EVAL.json

multiasset coverage scorecard:
$POLY_BT_ROOT/derived/contract_examples/multiasset_backtest_coverage_scorecard_latest/MULTIASSET_BACKTEST_COVERAGE_SCORECARD.json

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

jq '{status, summary, source_semantics_contract, mismatch_attribution, decision}' \
  $POLY_BT_ROOT/derived/contract_examples/btc_parity_semantic_alignment_latest/BTC_PARITY_SEMANTIC_ALIGNMENT_EXPERIMENT.json

jq '{status, buckets, policy}' \
  $POLY_BT_ROOT/derived/contract_examples/btc_v1_old_baseline_overlap_decomposition_latest/BTC_V1_OLD_BASELINE_OVERLAP_DECOMPOSITION_REPORT.json

column -s, -t < \
  $POLY_BT_ROOT/derived/contract_examples/backtest_v1_btc_parity_latest/btc_parity_field_alignment.csv | head -20

jq '{status, summary, interpretation}' \
  $POLY_BT_ROOT/derived/contract_examples/xuan_bridge_scorecard_latest/XUAN_BRIDGE_SCORECARD_MANIFEST.json

jq '{status, summary, same_window_handoff}' \
  $POLY_BT_ROOT/derived/contract_examples/xuan_completion_candidate_rescore_latest/XUAN_COMPLETION_CANDIDATE_RESCORE_MANIFEST.json

jq '{status, summary}' \
  $POLY_BT_ROOT/derived/contract_examples/xuan_capital_ledger_latest/XUAN_CAPITAL_LEDGER_REPORT.json

jq '{status, strategy_research_ready, shadow_design_ready, shadow_start_ready, strategy_promotion_ready, readiness_layers, warnings}' \
  $POLY_BT_ROOT/derived/contract_examples/xuan_backtest_v1_strategy_readiness_latest/XUAN_BACKTEST_V1_STRATEGY_READINESS_GATE.json

jq '{status, engineering_preflight_ready, shadow_start_ready, remaining_blockers, active_runner_conflict_check, runner_config}' \
  $POLY_BT_ROOT/derived/contract_examples/xuan_same_window_no_order_shadow_start_preflight_latest/XUAN_SAME_WINDOW_NO_ORDER_SHADOW_START_PREFLIGHT.json

jq '{status, canary_preflight_ready, tiny_canary_start_ready, live_ready, private_truth_ready, summary}' \
  $POLY_BT_ROOT/derived/contract_examples/btc_same_window_residual_share_le_3pct_v1_canary_preflight_latest/manifest.json

jq '{status, summary, policy, promotion_gate}' \
  $POLY_BT_ROOT/derived/contract_examples/xuan_btc_tiny_canary_no_order_shadow_eval_latest/XUAN_BTC_TINY_CANARY_NO_ORDER_SHADOW_EVAL.json
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
- 当前 canonical audit pack 是 `backtest_candidate_audit_pack_with_l2_evidence_latest`，selected count=80；旧 `backtest_candidate_audit_pack_latest` selected count=6 只是兼容产物。
- xuan 口径要看 `xuan_bridge_scorecard` 和 `xuan strategy readiness gate`。Scorecard 的 `bridge_category` 分为 `queue_screener_search_safe`、`completion_adapter_research`、`xuan_compatible_bridge`；A 类不能用于判断 xuan 策略好坏。
- 候选导入前先看 `xuan_completion_candidate_rescore`，它按 `pair_pnl + residual_settlement_pnl - fee` 重打分，不使用 queue PnL；same-window handoff CSV 只用于同窗 action/residual 人工交接，不授权 import/live。
- 资金效率先看 `xuan_capital_ledger`：`max_capital_tied`、`average_capital_tied`、`fee_drag`、`turnover_adjusted_roi`、`daily_capacity_estimate_at_notional`。
- 多币种不能等权解释，先看 coverage scorecard 的 `search_safe_row_count/market_count/day_count/selected_count/pair_qty/residual_qty/net_roi/stress_worst_day`。
- merge/redeem turnover 必须和 residual 风险分开：`paired_mergeable_qty/cost`、`merge_recovered_capital`、`capital_turnover` 是资金复用；`market_end_residual_qty/cost`、`residual_zero_stress_loss`、`actual_settlement_residual_pnl` 是 residual 归因。
- BTC parity gate 当前预期仍是 `BLOCKED_BTC_BASELINE_PARITY_NOT_PROVEN`，semantic alignment experiment 当前预期仍是 `BLOCKED_BTC_SEMANTIC_ALIGNMENT_NOT_PROVEN`。这是已知边界，不是安装失败。
- `btc_v1_old_baseline_overlap_decomposition_latest` 只做 old-overlap / V1-new-only / V1-all attribution；它不是 parity pass，也不是 promotion gate。
- `btc_same_window_residual_share_le_3pct_v1_canary_preflight_latest` 只是 tiny canary preflight review packet；它可以是 `canary_preflight_ready=true`，但 `tiny_canary_start_ready=false`、`live_ready=false`、`private_truth_ready=false` 必须保持。
- `xuan_btc_tiny_canary_no_order_shadow_eval_latest` 是 public L2 proxy / legacy runner 兼容 evaluator；真实 read-only WS/no-order shadow strict evaluator 固定看 `xuan_same_window_no_order_shadow_real_ws_runner_eval_latest`，输入是 `xuan_same_window_no_order_shadow_real_ws_runner_report_latest` 三文件包。没有真实 WS runner report 时应 fail-closed；public L2 proxy 只能作为历史 proxy，不能通过真实 runner gate，也不能据此启动 canary/live。
- `xuan_same_window_no_order_shadow_real_ws_start_scope_eval_latest` 只验证 BTC `[1,2,3]` start-scope 三文件包与审批范围一致；通过状态是 `KEEP_XUAN_SAME_WINDOW_REAL_WS_START_SCOPE_VALIDATED_APPROVAL_REQUIRED`，仍需要当前 exact approval text，不能复用 evidence-floor 或旧 approval。
- `xuan_same_window_no_order_shadow_start_preflight_latest` 是 no-order shadow 的工程预检包；`engineering_preflight_ready=true` 只表示配置、冲突检查、kill-switch、stop conditions 已固化，`shadow_start_ready=false` 仍表示缺用户明确批准。
- 历史 shadow/no-order 没有 owner private truth，不能标记 `private_truth_ready=true`。
- future owner truth 流程固定为 `candidate -> same-window L2/top-aligned validation -> xuan rescore -> canary/live-small owner execution -> owner truth reconciliation -> private truth gate`。
- L2 使用 `md_book_l2_top_aligned`：L1 canonical top + L2 depth/provenance。不要把 raw `md_book_l2` side snapshot 当作 top-of-book truth。
- 当前总 gate 预期是 `KEEP_XUAN_BACKTEST_V1_REAL_NO_ORDER_SHADOW_SAMPLE_SUFFICIENT_PROMOTION_BLOCKED_OWNER_TRUTH`；可以用于 research 和 shadow/no-order design，不能 promotion/deploy/live。

## 7. 常见问题

如果 gate 报 core replay DuckDB view 仍指向外盘：

```bash
uv run --with duckdb python scripts/repair_replay_store_duckdb_view_paths.py \
  --duckdb $POLY_BT_ROOT/verification_store/replay_store_multiasset_core_v1/20260502_20260518_core/store.duckdb
```

如果需要重建 L2 或 raw/replay 元数据，才连接 PolyData，并先看完整 runbook 的 L2 部分。
