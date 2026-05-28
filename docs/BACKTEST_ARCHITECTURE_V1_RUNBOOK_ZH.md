# Backtest Architecture V1 Runbook

本文件是多币种回测 V1 的固定入口。常规回测、gate、catalog 查询默认只读 MacBook 本地 compact artifacts：

```bash
export POLY_BT_ROOT=/Users/hot/web3Scientist/poly_backtest_data
```

不要把 `/Volumes/PolyData` 当作默认 backtest root。外盘只用于 raw/replay 冷归档重建、L2 深度复核、或极端元数据审计。

## 本地安装校验

```bash
cd /Users/hot/web3Scientist/poly_trans_research
uv run --with duckdb python scripts/validate_multiasset_backtest_v1_local_install.py --strict-duckdb
```

通过标准：

```text
status=OK
assets=BNB,BTC,DOGE,ETH,HYPE,SOL,XRP
days=2026-05-02..2026-05-13,2026-05-16,2026-05-17,2026-05-18
blocklisted days 2026-05-14/15/19 rows=0
search-safe forbidden columns=[]
audit DuckDB external validation_manifest count=0
core replay DuckDB query_ok=true
core replay DuckDB external /Volumes view count=0
```

如果 core replay DuckDB 的 persistent views 仍指向旧外盘路径，先修复本地视图路径：

```bash
uv run --with duckdb python scripts/repair_replay_store_duckdb_view_paths.py \
  --duckdb $POLY_BT_ROOT/verification_store/replay_store_multiasset_core_v1/20260502_20260518_core/store.duckdb
```

修复脚本只重建 DuckDB views，不移动数据、不删除源文件。之后重新运行本地安装校验。

## 主要本地 artifacts

```text
$POLY_BT_ROOT/derived/multiasset_l1_flow_event_store_v1/20260502_20260518_minsz10/event_store.duckdb
$POLY_BT_ROOT/derived/multiasset_l1_flow_bucket_mart_v1/20260502_20260518_l1flow_buckets_30s_1c/bucket_mart.duckdb
$POLY_BT_ROOT/derived/multiasset_market_cycle_feature_mart_v1/20260502_20260518_cycle_features_l1flow/feature_mart.duckdb
$POLY_BT_ROOT/verification_store/replay_store_multiasset_core_v1/20260502_20260518_core/store.duckdb
$POLY_BT_ROOT/derived/contract_examples/backtest_candidate_audit_pack_latest/backtest_candidate_audit_pack.duckdb
```

Audit DuckDB 的 base tables 是 `audit_candidates` 和 `audit_candidate_evidence`；便捷 views 是 `search_safe_private_blocked` 和 `candidate_evidence_by_experiment`。CSV 文件只是导出副本，不是 DuckDB 表名。

## 系统定位

当前 V1 有两层：

```text
search-safe screener/audit pack: 高并发 7 币种搜索、catalog、shortlist、audit、L2 evidence。
completion/residual adapter: 把 search-safe L1 flow 映射进旧 completion state-machine schema，用于研究层 pair/residual/after-fee PnL 对照。
```

它仍不是旧 BTC completion/residual baseline 的完整替代品。`best_queue_pnl` 只代表当前 search-safe queue 筛选口径，不包含：

```text
pair-completion PnL
strict rescue close
residual FIFO lots
mature after-fee mark recovery
merge/redeem 后资金复用与 turnover
owner private fills
```

completion/residual adapter 已经补齐研究层的 pair-completion、FIFO residual、after-fee settlement PnL，并额外输出 strict rescue opportunity、rescue-adjusted capital ledger、merge/redeem turnover、source semantics delta。仍不能补历史 owner private truth。因此 V1 的负 `best_queue_pnl` 不能被解释为 xuan completion/residual 策略死亡。进入策略结论前，必须先通过 crosswalk、BTC parity、xuan bridge scorecard、delta attribution 和 L2 parity。

## Crosswalk / Parity / Bridge

## 可复现 Pipeline 入口

V1 的 search-safe pipeline 不再只依赖已有产物。以下入口脚本已进入 repo，可在 MacBook 本地重跑：

```bash
uv run --with duckdb python scripts/run_backtest_search_matrix.py \
  --config configs/backtest/search_multiasset_l1_flow_matrix_formal_v1.json

uv run --with duckdb python scripts/run_backtest_matrix_batch.py \
  --config configs/backtest/search_multiasset_l1_flow_batch_formal_v1.json

uv run --with duckdb python scripts/build_backtest_result_catalog.py \
  --batch-config configs/backtest/search_multiasset_l1_flow_batch_formal_v1.json \
  --output-dir $POLY_BT_ROOT/derived/contract_examples/backtest_result_catalog_repro

uv run --with duckdb python scripts/compare_backtest_result_catalog.py \
  --catalog-csv $POLY_BT_ROOT/derived/contract_examples/backtest_result_catalog_repro/backtest_result_catalog.csv \
  --output-dir $POLY_BT_ROOT/derived/contract_examples/backtest_result_compare_repro

uv run --with duckdb python scripts/select_backtest_candidate_shortlist.py \
  --compare-csv $POLY_BT_ROOT/derived/contract_examples/backtest_result_compare_repro/backtest_result_compare.csv \
  --output-dir $POLY_BT_ROOT/derived/contract_examples/backtest_candidate_shortlist_repro

uv run --with duckdb python scripts/build_backtest_validation_queue.py \
  --shortlist-csv $POLY_BT_ROOT/derived/contract_examples/backtest_candidate_shortlist_repro/backtest_candidate_shortlist.csv \
  --output-dir $POLY_BT_ROOT/derived/contract_examples/backtest_validation_queue_repro

uv run --with duckdb python scripts/run_backtest_validation_queue.py \
  --queue-jsonl $POLY_BT_ROOT/derived/contract_examples/backtest_validation_queue_repro/backtest_validation_queue.jsonl \
  --output-dir $POLY_BT_ROOT/derived/contract_examples/backtest_validation_results_repro

uv run --with duckdb python scripts/build_backtest_validation_result_catalog.py \
  --result-csv $POLY_BT_ROOT/derived/contract_examples/backtest_validation_results_repro/backtest_validation_results.csv \
  --output-dir $POLY_BT_ROOT/derived/contract_examples/backtest_validation_result_catalog_repro

uv run --with duckdb python scripts/build_backtest_candidate_audit_pack.py \
  --validation-catalog-csv $POLY_BT_ROOT/derived/contract_examples/backtest_validation_result_catalog_repro/backtest_validation_result_catalog.csv \
  --output-dir $POLY_BT_ROOT/derived/contract_examples/backtest_candidate_audit_pack_repro
```

这些入口只生成 search-safe screener/audit 产物；不会补 strict rescue、merge turnover，也不会把历史 shadow 标记为 private truth。

生成 7 币种 completion/residual adapter：

```bash
uv run --with duckdb python scripts/build_multiasset_completion_candidate_base_from_l1_flow.py \
  --output-dir $POLY_BT_ROOT/derived/contract_examples/multiasset_completion_candidate_base_from_l1_flow_v1

uv run --with duckdb python scripts/run_completion_candidate_state_machine.py \
  --candidate-base-dir $POLY_BT_ROOT/derived/contract_examples/multiasset_completion_candidate_base_from_l1_flow_v1 \
  --output-dir $POLY_BT_ROOT/derived/contract_examples/multiasset_completion_state_machine_from_l1_flow_v1 \
  --edge 0.055 --target-qty 5 --alignment all \
  --seed-px-lo 0.05 --seed-px-hi 0.90 \
  --fill-haircut 0.25 --max-seed-qty 60 --max-open-cost 250 --min-seed-px 0.01 \
  --seed-offset-max-s 120 --seed-l1-pair-cap 1.02 --cooldown-s 5 \
  --imbalance-qty-cap 1.25 \
  --residual-cooldown-age-s 30 --residual-cooldown-cost-cap 0.5 \
  --fee-model official_taker --official-fee-rate 0.07 --force
```

当前 7 币种 adapter 期望：

```text
status=PASS_LOCAL_COMPLETION_RESEARCH_ONLY
candidate_count=4,670,188
selected_candidate_count=147,423
pair_actions=67,802
fee_after_pnl=7,767.861943
private truth=false
deployable=false
```

生成 BTC-only completion/residual adapter：

```bash
uv run --with duckdb python scripts/build_multiasset_completion_candidate_base_from_l1_flow.py \
  --assets BTC \
  --taker-side-source core_md_trades \
  --output-dir $POLY_BT_ROOT/derived/contract_examples/btc_completion_candidate_base_from_l1_flow_taker_normalized_v1

uv run --with duckdb python scripts/run_completion_candidate_state_machine.py \
  --candidate-base-dir $POLY_BT_ROOT/derived/contract_examples/btc_completion_candidate_base_from_l1_flow_taker_normalized_v1 \
  --output-dir $POLY_BT_ROOT/derived/contract_examples/btc_completion_state_machine_from_l1_flow_taker_normalized_v1 \
  --edge 0.055 --target-qty 5 --alignment all \
  --seed-px-lo 0.05 --seed-px-hi 0.90 \
  --fill-haircut 0.25 --max-seed-qty 60 --max-open-cost 250 --min-seed-px 0.01 \
  --seed-offset-max-s 120 --seed-l1-pair-cap 1.02 --cooldown-s 5 \
  --imbalance-qty-cap 1.25 \
  --public-trade-taker-side BUY \
  --residual-cooldown-age-s 30 --residual-cooldown-cost-cap 0.5 \
  --fee-model official_taker --official-fee-rate 0.07 --force
```

当前 BTC adapter 期望：

```text
status=PASS_LOCAL_COMPLETION_RESEARCH_ONLY
candidate_count=3,818,803
selected_candidate_count=84,151
pair_actions=40,868
pair_pnl=5,610.9625
fee_after_pnl=4,147.977896
net_roi=0.088919
residual_cost_rate=0.025183
```

生成 funnel 报告：

```bash
cd /Users/hot/web3Scientist/poly_trans_research
uv run --with duckdb python scripts/build_backtest_v1_crosswalk_report.py
```

关键输出：

```text
$POLY_BT_ROOT/derived/contract_examples/backtest_v1_crosswalk_latest/BACKTEST_V1_CROSSWALK_REPORT.json
$POLY_BT_ROOT/derived/contract_examples/backtest_v1_crosswalk_latest/backtest_v1_crosswalk_layers.csv
$POLY_BT_ROOT/derived/contract_examples/backtest_v1_crosswalk_latest/backtest_v1_crosswalk_by_asset.csv
```

当前 BTC 路径事实：

```text
search_safe_rows=3,806,334
matrix_rows=32
catalog_rows=110
shortlist_rows=0
validation_rows=0
audit_rows=0
```

这说明 BTC 在 V1 后段被筛掉了，不是旧 BTC baseline 已被新系统复现。

但 BTC-only completion adapter 已经给出同币种研究层对照。生成 delta attribution：

```bash
uv run --with duckdb python scripts/build_btc_completion_adapter_delta_report.py
```

当前 delta 结论：

```text
selected_action_ratio_new_over_old=1.630265
gross_buy_cost_ratio_new_over_old=1.651472
pair_pnl_ratio_new_over_old=1.635593
fee_after_pnl_ratio_new_over_old=1.552503
net_roi_delta=-0.005669
residual_cost_rate_delta=-0.019102
largest_new_seed_blockers=offset,cooldown,l1_pair_cap
```

生成 BTC strict rescue opportunity report：

```bash
uv run --with duckdb python scripts/build_btc_strict_rescue_opportunity_report.py
```

当前 strict rescue 研究层结论：

```text
status=OK_BTC_STRICT_RESCUE_OPPORTUNITY_READY
residual_lot_count=2,415
break_even_after_fee_lot_rate=1.0
rescue_beats_settlement_lot_rate=0.5685300207
best_after_fee_rescue_pnl=957.531001
```

该报告只说明 BTC adapter residual lots 在 top-aligned L2 中存在严格救援报价机会；尚未把 rescue action 编入完整 capital ledger，因此不能直接当作最终策略 PnL。

生成 BTC rescue-adjusted capital ledger：

```bash
uv run --with duckdb python scripts/build_btc_rescue_adjusted_capital_ledger.py
```

当前 rescue-adjusted 研究场景：

```text
adapter_settlement_baseline.fee_after_pnl=4,147.977896
strict_rescue_all_best_quote.fee_after_pnl=4,977.758897
strict_rescue_all_best_quote.net_roi=0.106707
oracle_rescue_if_beats_settlement.fee_after_pnl=4,996.433292
```

`strict_rescue_all_best_quote` 是研究上界：每个 residual lot 在救援窗口内取最佳 top-aligned after-fee bid 平仓。`oracle_rescue_if_beats_settlement` 使用 hindsight，不能用于实盘策略评估。

生成 BTC merge/redeem turnover report：

```bash
uv run --with duckdb python scripts/build_btc_merge_turnover_report.py
```

当前 turnover 口径：

```text
pair_merge_redeem_value=51,085.0
pair_merge_value_over_gross_cost=1.095098
capital_return_settlement_over_gross_cost=1.123020
capital_return_strict_rescue_over_gross_cost=1.140807
rounds_per_market=9.488739
```

生成 BTC source semantics delta report：

```bash
uv run --with duckdb python scripts/build_btc_source_semantics_delta_report.py
```

当前 source semantics 结论：

```text
old_runner_candidate_rows=1,425,196
new_runner_candidate_rows=3,818,803
runner_candidate_ratio_new_over_old=2.679493
```

主因：BTC adapter 现在从 core replay `md_trades` 归一化 taker side，并按真实 `BUY` runner event 进入 state machine；旧 baseline 的 runner 入口则是 `SELL` over mixed `public_trade/l1_price_change` 语义。两边不是同一个 source-event 定义，因此 BTC parity 不能标记通过，除非未来明确接受这个 source semantics bridge，或把旧 baseline 也迁移到同一套 normalized source-event 口径。

生成 BTC parity gate：

```bash
uv run --with duckdb python scripts/build_backtest_v1_btc_parity_gate.py
```

当前期望状态是：

```text
BLOCKED_BTC_BASELINE_PARITY_NOT_PROVEN
```

这是正确阻塞：BTC adapter 已有 pair-completion、FIFO residual、after-fee PnL、strict rescue、rescue-adjusted ledger、merge turnover 对照；但历史 owner private truth 不存在，且 source/event-generation 语义差异还不能被当作旧 baseline parity pass。

生成 xuan bridge scorecard：

```bash
python scripts/build_xuan_bridge_scorecard.py
```

当前期望状态是：

```text
PARTIAL_XUAN_BRIDGE_COMPLETION_ADAPTER_READY
```

Scorecard 会把旧 BTC baseline 的 `pair_pnl / residual / ROI`、V1 completion adapter 的 `pair/residual/after-fee`、以及 V1 audit pack 的 `queue_pnl` 分栏输出，避免把不同口径混在一起。

Scorecard 的 `bridge_category` 必须按三类理解：

```text
queue_screener_search_safe: 只用于 search-safe queue screener，不代表 xuan 策略 PnL。
completion_adapter_research: pair/residual state-machine 研究层，可看 bridge 方向，不是 parity/pass。
xuan_compatible_bridge: 最接近 xuan completion/residual 审计口径，但历史仍然不是 owner private truth。
```

生成 7 币种 strict rescue opportunity report：

```bash
uv run --with duckdb python scripts/build_multiasset_strict_rescue_opportunity_report.py
```

当前期望：

```text
status=OK_MULTIASSET_STRICT_RESCUE_OPPORTUNITY_READY
residual_lot_count=11,819
break_even_after_fee_lot_rate≈0.997462
rescue_beats_settlement_lot_rate≈0.513157
best_after_fee_rescue_pnl≈4,846.484666
```

生成 7 币种 merge/residual turnover report：

```bash
uv run --with duckdb python scripts/build_multiasset_merge_turnover_report.py
```

该报告固定拆分：

```text
paired_mergeable_qty/cost
merge_recovered_capital
capital_turnover / rounds_per_market
market_end_residual_qty/cost
residual_zero_stress_loss
actual_settlement_residual_pnl
```

不要用 residual settlement 盈利来证明策略 edge；它只能作为事后归因。策略设计阶段应看 paired merge/reuse、bad-tail residual risk、strict rescue 可行性和 source-age/L2 evidence。

生成 xuan-ready 总 gate：

```bash
uv run --with duckdb python scripts/build_xuan_backtest_v1_strategy_readiness_gate.py
```

当前期望状态：

```text
status=PARTIAL_XUAN_BACKTEST_V1_STRATEGY_RESEARCH_READY_NOT_PROMOTION
strategy_research_ready=true
strategy_research_readiness_level=partial
strategy_promotion_ready=false
private_truth_ready=false
deployable=false
live_orders_allowed=false
```

这个 gate 是给同事和后续 agent 的单一入口：基础设施、top-aligned L2、completion adapter、multiasset strict rescue、merge/residual split 已可用于研究；BTC parity/source semantics、xuan bridge complete、owner private truth 仍未闭环，所以不能 promotion/deploy/live。

## L2 边界

旧 BTC-only `replay_store_v2` 常驻了 `md_book_l2`，因此体积和行数远大于当前多币种 compact core。多币种 V1 的日常热路径故意使用 L1/trades/search-safe，适合高并发搜索和候选筛选。

如果策略逻辑需要 L2 深度形状、深度穿透、排队位置、盘口恢复路径或微结构冲击验证，不能只依赖 search-safe V1，必须进入 L2 validation tier。建议做法是把 L2 作为分层验证层，而不是默认全量热路径：

```text
tier 0: search-safe L1/trades 高并发搜索
tier 1: compact core replay 回放复核
tier 2: L2 targeted validation，只验证 shortlist/top candidates
tier 3: raw/full replay 重建或审计，仅在必要时使用 PolyData
```

V1 的目标不是在数据行数上超过旧 BTC-only replay store，而是在常规搜索吞吐、可审计 gating、跨币种覆盖、路径稳定性上超过旧体系；准确性则通过 tiered validation 补齐，不能把 L2 需求误压到 search-safe 层。

生成 L2 validation plan：

```bash
python scripts/build_multiasset_l2_validation_plan.py --top-n 20
```

如果还没有本地 multiasset L2 store，期望状态是：

```text
NEEDS_L2_BUILD_POLYDATA_ARCHIVES_AVAILABLE
```

可先跑单日 smoke：

```bash
uv run --with duckdb python scripts/build_replay_store_v2.py \
  --archive-root /Volumes/PolyData/poly_replay_archive/_archives \
  --store-root /Users/hot/web3Scientist/poly_backtest_data/verification_store \
  --store-name replay_store_multiasset_l2_v1 \
  --label smoke_20260517_l2 \
  --days 2026-05-17 \
  --assets all \
  --tables market_meta,settlement_records,md_trades,md_book_l1,md_book_l2,xuan_trades,xuan_activity,xuan_poll_log \
  --temp-root /Users/hot/web3Scientist/poly_backtest_data/tmp \
  --duckdb-threads 2 \
  --parallel-days 1 \
  --min-store-free-gb 160 \
  --min-temp-free-gb 160
```

完整 L2 构建也必须 `--parallel-days 1`，避免多个未压缩 replay SQLite 同时占用本地盘。L2 构建完成后还必须新增并通过 L1-from-L2 parity report，至少覆盖 bid/ask mismatch、stale/gap、crossed/locked book、source sequence gap、asset/day coverage。

L1-from-L2 parity gate：

```bash
uv run --with duckdb python scripts/validate_l1_from_l2_parity.py
```

如果本地 multiasset L2 还没构建，当前期望状态是：

```text
BLOCKED_L2_STORE_MISSING
```

L2 构建完成后，状态有两个可接受层级：

```text
OK: 纯 md_book_l2 side snapshot 可以重建 top-of-book。
OK_L1_TOP_OVERLAY_REQUIRED: legacy replay 需要 L1 canonical top + L2 depth/provenance mart。
```

如果是 `OK_L1_TOP_OVERLAY_REQUIRED`，不能把纯 `md_book_l2` side snapshot 当作准确性证明；候选级 L2 validation 必须先构建并使用 `md_book_l2_top_aligned`。

2026-05-27 smoke 结果：

```text
store=/Users/hot/web3Scientist/poly_backtest_data/verification_store/replay_store_multiasset_l2_v1/smoke_20260517_l2
day=2026-05-17
md_book_l2 rows=31,612,353
md_book_l1 rows=4,371,965
pure_l2_parity=failed
parity_status=OK_L1_TOP_OVERLAY_REQUIRED
```

当前失败形态不是 L2 缺失，而是 legacy replay archive 的 `md_book_l2` 在部分 `price_change` / `best_bid_ask` 事件中没有随 `md_book_l1` top-of-book 一起刷新深度快照。纯 L2 重建 top-of-book 时，每资产 sample 的 match rate 约 96%-100%，但 bid/ask mismatch rate 约 6%-17%。因此不能把纯 L2 视为 top-of-book truth。

可用修正是显式分层：

```text
top level: md_book_l1 canonical
depth/provenance: latest md_book_l2 side snapshot at or before L1 capture_seq
```

构建 top-aligned L2 mart：

```bash
uv run --with duckdb python scripts/build_l2_top_aligned_mart.py \
  --l2-manifest $POLY_BT_ROOT/verification_store/replay_store_multiasset_l2_v1/smoke_20260517_l2/REPLAY_STORE_V2_MANIFEST.json \
  --output-dir $POLY_BT_ROOT/derived/contract_examples/l2_top_aligned_mart_smoke_20260517_l2_full \
  --days 2026-05-17 \
  --assets all
```

2026-05-17 全日 smoke mart：

```text
table=md_book_l2_top_aligned
rows=8,743,902
missing_depth_rows=0
top_overlay_required_rows=716,120
top_overlay_required_rate=8.1899%
max_raw_l2_age_ms=63,990
size=944M
```

这解锁的是“L1 canonical top + L2 depth provenance”的受限 L2 validation tier，不是纯 L2 parity，也不是旧 BTC completion/residual baseline 已复现。

L2 validation plan 已经把这个语义固化进控制面：

```bash
uv run --with duckdb python scripts/build_multiasset_l2_validation_plan.py
```

当前 7 币种全量 L2 已经本地构建完成时，期望是：

```text
status=OK_LOCAL_L2_TOP_ALIGNED_READY
l1_from_l2_parity.status=OK_L1_TOP_OVERLAY_REQUIRED
l2_top_aligned_mart.status=OK
```

全量 L2 build 后，如果 parity 仍是 `OK_L1_TOP_OVERLAY_REQUIRED`，下一步必须构建全量 top-aligned mart。全窗口不要再用单事务 monolithic ASOF join；必须使用可恢复的按 `day/asset` 分区 builder，并对 BTC 开启 `condition_id` shard：

```bash
mkdir -p $POLY_BT_ROOT/tmp/l2_top_aligned_20260502_20260518

uv run --with duckdb python scripts/build_l2_top_aligned_mart_partitioned.py \
  --l2-manifest $POLY_BT_ROOT/verification_store/replay_store_multiasset_l2_v1/20260502_20260518_l2/REPLAY_STORE_V2_MANIFEST.json \
  --output-dir $POLY_BT_ROOT/derived/contract_examples/l2_top_aligned_mart_20260502_20260518_l2 \
  --days all \
  --assets all \
  --duckdb-threads 2 \
  --duckdb-temp-dir $POLY_BT_ROOT/tmp/l2_top_aligned_20260502_20260518 \
  --condition-shards-for-assets BTC \
  --condition-shards 8
```

全量 top-aligned mart 的最终产物保留在 MacBook 本地；`--duckdb-temp-dir` 只是 DuckDB 临时 spill 保险。只有本地临时空间不足时，才临时把 spill 目录放到外盘；不能把外盘路径写入最终 manifest 或默认 root。分区 builder 支持 resume：已完成的 `(day, asset)` 会跳过，失败后原命令重跑即可。

当前 2026-05-02..2026-05-18 valid-day 全窗口产物：

```text
status=OK
partition_count=105
rows=149,494,478
missing_depth_rows=0
top_overlay_required_rows=36,271,303
top_overlay_required_rate=24.2626%
size≈15G
progress_status=COMPLETE
```

候选级 L2 validation 必须走 top-aligned runner，不能直接查 raw `md_book_l2`：

```bash
uv run --with duckdb python scripts/run_backtest_l2_top_aligned_validation_queue.py
```

在没有完整全窗口 mart 时，默认全窗口运行应阻塞：

```text
status=BLOCKED
BLOCKED_L2_TOP_ALIGNED_MART_INCOMPLETE=80
raw_md_book_l2_direct_read_allowed=false
```

如果只是验证 smoke 接口，可显式允许 partial day：

```bash
uv run --with duckdb python scripts/run_backtest_l2_top_aligned_validation_queue.py \
  --allow-partial-days \
  --output-dir $POLY_BT_ROOT/derived/contract_examples/backtest_l2_validation_results_smoke_20260517_top_aligned
```

当前全窗口结果：

```text
L2_TOP_ALIGNED_CANDIDATE_EVIDENCE_READY=80
coverage_modes=FULL
forbidden_result_columns=[]
blocklisted_day mentions=0
```

L2 evidence 可以挂到 audit pack，但不能改变 private truth gate：

```bash
uv run --with duckdb python scripts/build_backtest_candidate_audit_pack.py \
  --validation-catalog-csv $POLY_BT_ROOT/derived/contract_examples/backtest_validation_result_catalog_deep_v1/backtest_validation_result_catalog.csv \
  --l2-validation-results-csv $POLY_BT_ROOT/derived/contract_examples/backtest_l2_validation_results_latest/backtest_l2_validation_results.csv \
  --output-dir $POLY_BT_ROOT/derived/contract_examples/backtest_candidate_audit_pack_with_l2_evidence_latest
```

当前全窗口 L2 evidence pack 的期望状态：

```text
selected_candidate_count=80
l2_top_aligned_evidence_row_count=80
l2_top_aligned_evidence_ready_count=80
l2_top_aligned_evidence_blocked_count=0
status=OK
private_promotion_ready_count=0
```

## Xuan 接入 gate

```bash
cd /Users/hot/web3Scientist/pm_as_ofi-xuan-frontier
uv run python scripts/xuan_shadow_review_backtest_v1_integration_gate.py
uv run --with duckdb python scripts/xuan_shadow_review_backtest_v1_integration_gate.py
```

期望状态：

```text
KEEP_XUAN_BACKTEST_V1_INTEGRATION_GATE_READY_LOCAL_ONLY
```

该状态只说明 upstream search-safe screening layer 可用，不授权 candidate import、remote runner、deploy 或 live order。历史 shadow/no-order 不能补 owner private truth；只有未来受控 canary/live-small 的 owner order/fill/inventory/redeem/fee reconciliation 通过后，才可能进入 private truth ready。
