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
xuan strategy readiness: PARTIAL_XUAN_BACKTEST_V1_SHADOW_DESIGN_READY_PROMOTION_BLOCKED_OWNER_TRUTH
strategy_research_ready=true
shadow_design_ready=true
shadow_start_ready=false
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

xuan same-window handoff:
$POLY_BT_ROOT/derived/contract_examples/xuan_completion_candidate_rescore_latest/xuan_completion_candidate_same_window_handoff.csv
$POLY_BT_ROOT/derived/contract_examples/xuan_completion_candidate_rescore_latest/xuan_completion_candidate_same_window_handoff_actions.csv
$POLY_BT_ROOT/derived/contract_examples/xuan_completion_candidate_rescore_latest/xuan_completion_candidate_same_window_handoff_residual_lots.csv

xuan no-order shadow start preflight:
$POLY_BT_ROOT/derived/contract_examples/xuan_same_window_no_order_shadow_start_preflight_latest/XUAN_SAME_WINDOW_NO_ORDER_SHADOW_START_PREFLIGHT.json
$POLY_BT_ROOT/derived/contract_examples/xuan_same_window_no_order_shadow_start_preflight_latest/xuan_same_window_no_order_shadow_runner_config.json
$POLY_BT_ROOT/derived/contract_examples/xuan_same_window_no_order_shadow_start_preflight_latest/preflight_checklist.csv
$POLY_BT_ROOT/derived/contract_examples/xuan_same_window_no_order_shadow_start_preflight_latest/candidate_binding.csv

xuan no-order shadow manual approval packet:
$POLY_BT_ROOT/derived/contract_examples/xuan_same_window_no_order_shadow_manual_approval_packet_latest/XUAN_SAME_WINDOW_NO_ORDER_SHADOW_MANUAL_APPROVAL_PACKET.json
$POLY_BT_ROOT/derived/contract_examples/xuan_same_window_no_order_shadow_manual_approval_packet_latest/manual_approval_checklist.csv
$POLY_BT_ROOT/derived/contract_examples/xuan_same_window_no_order_shadow_manual_approval_packet_latest/manual_approval_prerequisite_checks.csv
$POLY_BT_ROOT/derived/contract_examples/xuan_same_window_no_order_shadow_manual_approval_packet_latest/manual_approval_summary.md
$POLY_BT_ROOT/derived/contract_examples/xuan_same_window_no_order_shadow_real_ws_start_scope_eval_latest/XUAN_SAME_WINDOW_NO_ORDER_SHADOW_EVAL.json

xuan no-order shadow manual approval decision:
$POLY_BT_ROOT/derived/contract_examples/xuan_same_window_no_order_shadow_manual_approval_decision_latest/XUAN_SAME_WINDOW_NO_ORDER_SHADOW_MANUAL_APPROVAL_DECISION.json
$POLY_BT_ROOT/derived/contract_examples/xuan_same_window_no_order_shadow_manual_approval_decision_latest/manual_approval_decision_summary.md

xuan capital ledger:
$POLY_BT_ROOT/derived/contract_examples/xuan_capital_ledger_latest/XUAN_CAPITAL_LEDGER_REPORT.json

multiasset coverage scorecard:
$POLY_BT_ROOT/derived/contract_examples/multiasset_backtest_coverage_scorecard_latest/MULTIASSET_BACKTEST_COVERAGE_SCORECARD.json

BTC parity gate:
$POLY_BT_ROOT/derived/contract_examples/backtest_v1_btc_parity_latest/BACKTEST_V1_BTC_PARITY_GATE.json

BTC parity field alignment:
$POLY_BT_ROOT/derived/contract_examples/backtest_v1_btc_parity_latest/btc_parity_field_alignment.csv

BTC parity semantic alignment experiment:
$POLY_BT_ROOT/derived/contract_examples/btc_parity_semantic_alignment_latest/BTC_PARITY_SEMANTIC_ALIGNMENT_EXPERIMENT.json

BTC V1 old/new overlap decomposition:
$POLY_BT_ROOT/derived/contract_examples/btc_v1_old_baseline_overlap_decomposition_latest/BTC_V1_OLD_BASELINE_OVERLAP_DECOMPOSITION_REPORT.json
$POLY_BT_ROOT/derived/contract_examples/btc_v1_old_baseline_overlap_decomposition_latest/bucket_summary_rounded.csv

BTC tiny canary preflight packet:
$POLY_BT_ROOT/derived/contract_examples/btc_same_window_residual_share_le_3pct_v1_canary_preflight_latest/manifest.json
$POLY_BT_ROOT/derived/contract_examples/btc_same_window_residual_share_le_3pct_v1_canary_preflight_latest/source_semantics_contract.json
$POLY_BT_ROOT/derived/contract_examples/btc_same_window_residual_share_le_3pct_v1_canary_preflight_latest/filter_capital_ledger.json
$POLY_BT_ROOT/derived/contract_examples/btc_same_window_residual_share_le_3pct_v1_canary_preflight_latest/research_only_import_contract.csv
$POLY_BT_ROOT/derived/contract_examples/btc_same_window_residual_share_le_3pct_v1_canary_preflight_latest/owner_private_truth_schema.json
$POLY_BT_ROOT/derived/contract_examples/btc_same_window_residual_share_le_3pct_v1_canary_preflight_latest/preflight_checklist.json

BTC tiny canary no-order shadow evaluator:
$POLY_BT_ROOT/derived/contract_examples/xuan_btc_tiny_canary_shadow_evaluation_gate_spec_latest/XUAN_BTC_TINY_CANARY_SHADOW_EVALUATION_GATE_SPEC.json
$POLY_BT_ROOT/derived/contract_examples/xuan_btc_tiny_canary_no_order_shadow_eval_latest/XUAN_BTC_TINY_CANARY_NO_ORDER_SHADOW_EVAL.json
$POLY_BT_ROOT/derived/contract_examples/xuan_btc_tiny_canary_no_order_shadow_eval_latest/observed_shadow_report_normalized.csv
$POLY_BT_ROOT/derived/contract_examples/xuan_btc_tiny_canary_no_order_shadow_eval_latest/threshold_failures.csv
$POLY_BT_ROOT/derived/contract_examples/xuan_btc_tiny_canary_no_order_shadow_eval_latest/stop_condition_events.csv
$POLY_BT_ROOT/derived/contract_examples/xuan_same_window_no_order_shadow_real_ws_runner_eval_latest/XUAN_SAME_WINDOW_NO_ORDER_SHADOW_EVAL.json

xuan bridge scorecard:
$POLY_BT_ROOT/derived/contract_examples/xuan_bridge_scorecard_latest/XUAN_BRIDGE_SCORECARD_MANIFEST.json
```

## 5. 日常查看命令

总 gate：

```bash
jq '{status, strategy_research_ready, strategy_research_readiness_level, strategy_promotion_ready, private_truth_ready, deployable, live_orders_allowed, summary, warnings}' \
  $POLY_BT_ROOT/derived/contract_examples/xuan_backtest_v1_strategy_readiness_latest/XUAN_BACKTEST_V1_STRATEGY_READINESS_GATE.json
```

四层 gate 必须分开读：

```bash
jq '{status, readiness_layers, policy}' \
  $POLY_BT_ROOT/derived/contract_examples/xuan_backtest_v1_strategy_readiness_latest/XUAN_BACKTEST_V1_STRATEGY_READINESS_GATE.json
```

当前含义：

```text
strategy_research_ready=true: 本地回测/L2/top-aligned/completion/residual/capital ledger/coverage/rescore 可用于研究。
shadow_design_ready=true: 可基于 search-safe + same-window handoff + capital ledger 设计 shadow/no-order。
manual_approval_packet_ready=true: 启动前审查材料已汇总。
manual_approval_granted 只表示 approval gate 文本是否已匹配；它仍不会启动 runner。
shadow_start_ready=false: 顶层控制面保持不启动 runner；即使 preconditions_met=true，也需要人工另行执行 start command。
strategy_promotion_ready=false: 必须等未来 owner execution truth 全部 reconcile。
```

no-order shadow start preflight：

```bash
uv run python scripts/build_xuan_same_window_shadow_start_preflight.py

jq '{status, engineering_preflight_ready, shadow_start_ready, remaining_blockers, active_runner_conflict_check, runner_config}' \
  $POLY_BT_ROOT/derived/contract_examples/xuan_same_window_no_order_shadow_start_preflight_latest/XUAN_SAME_WINDOW_NO_ORDER_SHADOW_START_PREFLIGHT.json
```

当前期望：

```text
engineering_preflight_ready=true
active_runner_conflict_check.passed=true
remaining_blockers=["manual_shadow_start_approval_missing"]
shadow_start_ready=false
orders_allowed=false
live_orders_allowed=false
```

这表示工程预检已补齐，只差用户明确批准。批准前不要运行 `start_command_preview.txt`。

no-order shadow manual approval packet：

```bash
uv run python scripts/build_xuan_same_window_no_order_shadow_manual_approval_packet.py

jq '{status, approval_packet_ready, manual_approval_granted, shadow_start_ready, remaining_blockers, summary, promotion_gate}' \
  $POLY_BT_ROOT/derived/contract_examples/xuan_same_window_no_order_shadow_manual_approval_packet_latest/XUAN_SAME_WINDOW_NO_ORDER_SHADOW_MANUAL_APPROVAL_PACKET.json
```

当前期望：

```text
status=KEEP_XUAN_SAME_WINDOW_NO_ORDER_SHADOW_MANUAL_APPROVAL_PACKET_READY_APPROVAL_REQUIRED
approval_packet_ready=true
manual_approval_granted=false
shadow_start_ready=false
runner_start_allowed=false
remaining_blockers=["manual_shadow_start_approval_missing"]
private_truth_ready=false
strategy_promotion_ready=false
live_orders_allowed=false
```

审批包只用于人类复核：它绑定 start preflight、runner config、candidate binding、stop conditions 和 kill-switch。`xuan_same_window_no_order_shadow_real_ws_start_scope_eval_latest` 只验证 BTC `[1,2,3]` start-scope 三文件包与审批范围一致，状态应是 approval required；它不是 292 行 evidence-floor，也不会启动 runner、不会授权 import/order/live、不会把 public L2 proxy、same-window handoff 或真实 no-order 盘口观测变成 private truth。

manual approval decision：

```bash
uv run python scripts/apply_xuan_same_window_no_order_shadow_manual_approval.py

jq '{status, manual_approval_granted, runner_start_allowed_by_approval_gate, runner_started, remaining_blockers, required_approval_text, promotion_gate}' \
  $POLY_BT_ROOT/derived/contract_examples/xuan_same_window_no_order_shadow_manual_approval_decision_latest/XUAN_SAME_WINDOW_NO_ORDER_SHADOW_MANUAL_APPROVAL_DECISION.json
```

默认不传 approval text 时的期望：

```text
status=WAITING_XUAN_SAME_WINDOW_NO_ORDER_SHADOW_MANUAL_APPROVAL_NOT_GRANTED
manual_approval_granted=false
runner_start_allowed_by_approval_gate=false
runner_started=false
remaining_blockers=["manual_shadow_start_approval_missing"]
private_truth_ready=false
strategy_promotion_ready=false
live_orders_allowed=false
```

如果之后要批准，只能把 decision artifact 里的 `required_approval_text` 原样传入；该脚本最多打开 approval gate，仍不会启动 runner：

```bash
uv run python scripts/apply_xuan_same_window_no_order_shadow_manual_approval.py \
  --approved-by "<reviewer>" \
  --approval-text "<exact required_approval_text>"
```

xuan completion/residual 重打分：

```bash
jq '{status, summary, same_window_handoff}' \
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

jq '{status, summary, source_semantics_contract, mismatch_attribution, decision}' \
  $POLY_BT_ROOT/derived/contract_examples/btc_parity_semantic_alignment_latest/BTC_PARITY_SEMANTIC_ALIGNMENT_EXPERIMENT.json

column -s, -t < \
  $POLY_BT_ROOT/derived/contract_examples/backtest_v1_btc_parity_latest/btc_parity_field_alignment.csv | head -30
```

`BLOCKED_BTC_BASELINE_PARITY_NOT_PROVEN` 是当前正确状态。不要把它解释成本地安装失败。
`BTC_PARITY_SEMANTIC_ALIGNMENT_EXPERIMENT.json` 是当前 parity 语义统一实验入口；它当前预期仍是 `BLOCKED_BTC_SEMANTIC_ALIGNMENT_NOT_PROVEN`。

BTC old/new action bucket decomposition：

```bash
uv run --with duckdb python scripts/build_btc_v1_old_baseline_overlap_decomposition.py

jq '{status, source_semantics_contract, mismatch_attribution, buckets, policy}' \
  $POLY_BT_ROOT/derived/contract_examples/btc_v1_old_baseline_overlap_decomposition_latest/BTC_V1_OLD_BASELINE_OVERLAP_DECOMPOSITION_REPORT.json
```

它把 V1 normalized selected actions 分成 `old_baseline_overlap`、`v1_normalized_new_only`、`v1_normalized_all` 三个 bucket，并输出 fee-after PnL、ROI、residual zero-stress、worst day、capital tied 和 pair-cost distribution。这个报告只用于 research attribution，不是 old parity pass，也不是 promotion gate。

BTC tiny canary preflight 只用于 review，不启动 shadow/live：

```bash
uv run --with duckdb python scripts/build_btc_same_window_canary_preflight.py

jq '{status, canary_preflight_ready, tiny_canary_start_ready, live_ready, private_truth_ready, summary, policy}' \
  $POLY_BT_ROOT/derived/contract_examples/btc_same_window_residual_share_le_3pct_v1_canary_preflight_latest/manifest.json
```

当前 filter：

```text
filter_name=btc_same_window_residual_share_le_3pct_v1
candidate_count=52
valid_day_count=15
core_pair_after_fee_pnl≈62.562055
market_end_residual_cost≈8.85
zero_stress_after_fee_pnl≈53.712055
candidate_import_allowed=false
```

BTC tiny canary no-order shadow evaluator 分两层读：`xuan_btc_tiny_canary_no_order_shadow_eval_latest` 只保留 public L2 proxy / legacy runner 兼容结果；真实 read-only WS/no-order runner 的 strict gate 固定看 `xuan_same_window_no_order_shadow_real_ws_runner_eval_latest`。public L2 proxy 只能作为历史 proxy 证据，不能通过真实 runner gate。

```bash
uv run python /Users/hot/web3Scientist/pm_as_ofi-xuan-frontier/scripts/evaluate_xuan_same_window_no_order_shadow.py

jq '{status, summary, policy, promotion_gate}' \
  $POLY_BT_ROOT/derived/contract_examples/xuan_same_window_no_order_shadow_real_ws_runner_eval_latest/XUAN_SAME_WINDOW_NO_ORDER_SHADOW_EVAL.json
```

默认真实 runner 三文件包路径：

```text
$POLY_BT_ROOT/derived/contract_examples/xuan_same_window_no_order_shadow_real_runner_report_latest/no_order_shadow_report.csv
$POLY_BT_ROOT/derived/contract_examples/xuan_same_window_no_order_shadow_real_runner_report_latest/no_order_shadow_audit_manifest.json
$POLY_BT_ROOT/derived/contract_examples/xuan_same_window_no_order_shadow_real_runner_report_latest/no_order_shadow_gate_summary.json
$POLY_BT_ROOT/derived/contract_examples/xuan_same_window_no_order_shadow_real_ws_runner_report_latest/no_order_shadow_report.csv
$POLY_BT_ROOT/derived/contract_examples/xuan_same_window_no_order_shadow_real_ws_runner_report_latest/no_order_shadow_audit_manifest.json
$POLY_BT_ROOT/derived/contract_examples/xuan_same_window_no_order_shadow_real_ws_runner_report_latest/no_order_shadow_gate_summary.json
```

严格通过时预期：

```text
status=KEEP_XUAN_SAME_WINDOW_REAL_WS_NO_ORDER_SHADOW_EVALUATED_PROMOTION_BLOCKED_OWNER_TRUTH
evaluation_passed=true
no_order_shadow_real_runner_evaluated=true
threshold_failure_count=0
private_truth_ready=false
strategy_promotion_ready=false
live_orders_allowed=false
```

当前真实 WS 包已通过 strict gate 且达到研究证据样本底线：292 rows、52 candidates、11 markets、33 列主 CSV、`book_transport=WS`、`book_ws_used=true`、`threshold_failure_count=0`。这只证明 public CLOB WS book/latency/fillability proxy 合约通过；`private_truth_ready`、`strategy_promotion_ready`、`live_orders_allowed`、`deployable` 必须继续为 false。

BTC `[1,2,3]` start authorization 必须单独看 start-scope artifact：

```bash
jq '{status, scope_kind, summary, policy, promotion_gate}' \
  $POLY_BT_ROOT/derived/contract_examples/xuan_same_window_no_order_shadow_real_ws_start_scope_eval_latest/XUAN_SAME_WINDOW_NO_ORDER_SHADOW_EVAL.json
```

当前 start-scope 预期：

```text
status=KEEP_XUAN_SAME_WINDOW_REAL_WS_START_SCOPE_VALIDATED_APPROVAL_REQUIRED
scope_kind=start_scope
row_count=12
candidate_count=12
market_count=3
ws_start_scope_validated=true
start_authorizing=false
```

这只表示 `[1,2,3]` 启动范围材料合格；真正打开 approval gate 仍必须把当前 `manual approval decision` 里的 `required_approval_text` 原样传入。缺这个 exact text 时，顶层 gate 应保持 `manual_approval_granted=false`、`shadow_start_preconditions_met=false`、`shadow_start_ready=false`。

注意：主 CSV 必须严格等于 `required_shadow_report_columns.csv` 的 33 列，不能新增 audit/source 字段；`runner_kind=real_readonly_ws_no_order_observer`、`import_enabled=false`、`candidate_import_allowed=false`、source/runtime fingerprint continuity、resolver 全行覆盖、NullOrderClient/no private key、order/cancel/redeem/import call count 为 0 等证据必须放 audit manifest；真实 WS 聚合指标、threshold failures、stop events 必须放 gate summary。任何缺失或 promotion/private/live claim 都会 fail-closed。

## 6. 正确使用候选

默认候选入口不是 queue screener，而是：

```text
$POLY_BT_ROOT/derived/contract_examples/xuan_completion_candidate_rescore_latest/xuan_completion_candidate_rescore_top.csv
```

推荐流程：

```text
1. 先确认 local install gate OK。
2. 读 xuan strategy readiness gate，确认仍是 research-only。
3. 读四层 gate：`strategy_research_ready`、`shadow_design_ready`、`shadow_start_ready`、`strategy_promotion_ready`。
4. 读 no-order shadow start preflight，确认 `engineering_preflight_ready=true` 且只剩 manual approval。
5. 从 xuan_completion_candidate_rescore_top.csv 取 market-level 候选。
6. 用 xuan same-window tiered scorecard/handoff CSV 交接同窗 selected actions 和 residual lots。
7. 对候选检查 canonical audit pack 和 L2 top-aligned evidence。
8. 用 capital ledger 判断 max capital tied、fee drag、turnover-adjusted ROI。
9. 用户批准后才可以启动 no-order shadow，runner 产出 report 后必须先跑 BTC tiny canary no-order shadow evaluator。
10. 只做 research/scoring 或 shadow design；没有 owner truth 之前不允许 live/import。
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
