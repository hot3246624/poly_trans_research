# CE25 High-Price Strict Paircap Capacity/Depth Autoresearch 2026-06-04

## 结论

`CE25_BTC5M_HIGH_PRICE_STOP_1_5M_V1` 的 strict paircap 分支继续是当前最值得推进的本地 replay 候选。

本轮最重要的变化不是扩大时间窗口，而是确认：在 `same_row_pair_only` / `entry_paircap_required` 两个 strict residual-killer 模板下，容量从 `target_qty=3` 提到 `target_qty=13` 后，在 3.0% fee stress 下仍为正、残差仍为 0、paired action 覆盖仍为 210 到 215。

但这仍然只是 local `book_shadow` replay。它不能证明 private order truth、真实成交、排队优先级、live readiness 或 deployability。下一层应进入 L2/top-depth/summary-only runner，而不是共享 WS、OOS、canary 或 live。

## 输入与边界

- strategy input: `/Users/hot/web3Scientist/poly_trans_research/configs/ce25_high_price/CE25_HIGH_PRICE_PAIRCAP_STRICT_INPUT_v0.json`
- strategy input sha256: `5a3fb8abc7f51d4894769c23c1719ea6161fc6ce78396c6695853710b9369eb1`
- runner: `/Users/hot/web3Scientist/poly_trans_research/scripts/run_ce25_nagi_shadow_policy_runner.py`
- runner sha256 after capacity/depth + summary-only + concentration metrics + top1 qty gate patch: `69e083f59de55730d53c0cb6da165af0b45770394d971fe4e35f8dc31a896daf`
- L2/top-aligned validator: `/Users/hot/web3Scientist/poly_trans_research/scripts/validate_ce25_high_price_l2_top_aligned_actions.py`
- L2/top-aligned validator sha256: `bbfa28d570c1f6a77d53ab8a11cb8f96c8e78f46f2a59e3e94d11c144d96f7ab`
- master candidate base: `/Users/hot/web3Scientist/poly_backtest_data/derived/completion_candidate_pipeline_v1/local_20260502_20260518_paircap102`
- local only: no private key, no import, no order/cancel/redeem, no live/deploy/funding, no shared-WS dependency.

## 已完成运行

路径：

`/Users/hot/web3Scientist/poly_backtest_data/derived/ce25_nagi_shadow_policy_autoresearch_v0/ce25_high_price_capacity_depth_iter1_20260604`

规模：

- 43 variants x 5 fee rates = 215 results。
- classification: `KEEP_LOCAL_REPLAY_CANDIDATE=150`, `DISCARD_NEGATIVE_FEE_AFTER_PNL=65`。
- 2.83% fee: `KEEP_LOCAL_REPLAY_CANDIDATE=30/43`。
- 3.0% fee: `KEEP_LOCAL_REPLAY_CANDIDATE=30/43`。

关键哈希：

- `AUTORESEARCH_MANIFEST.json` sha256 `670712f75f212edaac8535209f63f088dbf0e30c2797fffe0d2417a9fb99fcc9`
- `autoresearch_ledger.csv` sha256 `84d4fa8d95f92bed28e23d6003c6f749f9c26bb7e522b93ed5f3dffdebfb3ea8`
- `branch_control_summary.csv` sha256 `9613daf9bf79935914d2784970b833667e3daad68ed6d03a84b9c881ab3d8bec`
- `capacity_stress_summary.csv` sha256 `7b5cb83f6036a7171864fdffc37ca93d1538809df2361fae9542d1ad698e8ee8`
- `fee_stress_summary.csv` sha256 `667f6b7774ea45b80e0751ee088c21a8628820feb6ec1db33a4e2d05d5220a72`
- `residual_stress_summary.csv` sha256 `b861d0257ed3e0299c7c7403ac31ee50ff9a023f09fe9be177ab7a9ff5567b9d`

## 3.0% Fee Stress Survivors

| branch | pnl | roi | pairs | residual | pair cost | target_qty | haircut | pair cap |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| same_row_cap_0.965 | 17.774517 | 7.0610% | 100 | 0 | 0.923318 | 3 | 25% | 0.965 |
| entry_paircap_cap_0.965 | 17.774517 | 7.0610% | 100 | 0 | 0.923318 | 3 | 25% | 0.965 |
| entry_paircap_target_qty_13 | 73.752535 | 4.1055% | 210 | 0 | 0.949482 | 13 | 25% | 0.980 |
| same_row_target_qty_13 | 74.226211 | 4.0535% | 215 | 0 | 0.949956 | 13 | 25% | 0.980 |
| entry_paircap_cap_0.970 | 20.307935 | 5.3110% | 151 | 0 | 0.938757 | 3 | 25% | 0.970 |
| same_row_cap_0.970 | 20.366588 | 5.2857% | 152 | 0 | 0.938988 | 3 | 25% | 0.970 |
| entry_paircap_target_qty_8 | 50.186675 | 4.0508% | 210 | 0 | 0.949973 | 8 | 25% | 0.980 |
| same_row_target_qty_8 | 50.533527 | 3.9951% | 215 | 0 | 0.950480 | 8 | 25% | 0.980 |

Interpretation:

- Stricter paircap `0.965` improves ROI/pair cost but drops coverage to exactly 100 paired actions.
- Paircap `0.970` / `0.975` keeps 151-152 paired actions and materially better pair cost than `0.980`.
- Increasing `target_qty` from 3 to 13 increases PnL roughly with size while preserving 0 residual in this local replay.
- `same_row` and `entry_paircap` are nearly identical in quality; `entry_paircap` has slightly fewer pairs but slightly better weighted pair cost.

## Concentration Pass

为避免把 210 到 215 个 action 误读为 210 到 215 个独立市场，本轮又用 `--book-shadow-summary-only` 跑了 3.0% fee concentration pass：

`/Users/hot/web3Scientist/poly_backtest_data/derived/ce25_nagi_shadow_policy_autoresearch_v0/ce25_high_price_capacity_concentration_iter2_20260604`

关键哈希：

- `AUTORESEARCH_MANIFEST.json` sha256 `558629cbc192720dc9d31d974d7a75edce14c7e8682498faf0a5fc994ceef441`
- `autoresearch_ledger.csv` sha256 `4ee9ce8bddd270919433075e56e28acd61c5a1d17d50513aebabe7d10a9770ae`
- `branch_control_summary.csv` sha256 `2698aa09580b0c235b1eefeedb49128a6bfd29494f1792eca9ea58a0b82f2260`
- `capacity_stress_summary.csv` sha256 `ff408530bdc323d821983935e3b5e3de161d9f57834f45444f6188e050422701`

Concentration findings:

| branch | pairs | paired markets | max market qty share | max market action share |
| --- | ---: | ---: | ---: | ---: |
| same_row_cap_0.965 | 100 | 96 | 2.2263% | 3.0000% |
| entry_paircap_cap_0.965 | 100 | 96 | 2.2263% | 3.0000% |
| entry_paircap_target_qty_13 | 210 | 203 | 1.3902% | 1.4286% |
| same_row_target_qty_13 | 215 | 203 | 1.3827% | 1.3953% |
| entry_paircap_cap_0.970 | 151 | 146 | 1.4900% | 1.9868% |
| same_row_cap_0.970 | 152 | 146 | 1.4790% | 1.9737% |

Interpretation:

- `target_qty=13` 的收益不是由单一市场堆出来的；203 paired markets，最大单市场 pair_qty share 约 1.4%。
- `paircap=0.965` 更干净但覆盖只有 96 paired markets / 100 paired actions，刚好过 floor，后续应作为 conservative control，而不是唯一主分支。
- Summary-only 有效：同类 run 不再写 per-action/per-residual 明细，输出目录从 GB 级降到 KB/MB 级。

## L2 / Top-Aligned Validation Pass

本轮新增 `md_book_l2_top_aligned` 验证层，目标是把 book-shadow action 对齐到 canonical L1 top 与 top5 raw L2 depth。验证口径：

- L1 canonical top price 必须与 action price 匹配；
- top1 深度足够且 L1 pair cost <= pair cap 时，视为 `l1_top_pair_pass`；
- top1 深度不足时，允许 raw L2 top2-top5 辅助，但 raw L2 age 必须 <= 750ms，且 top5 VWAP pair cost <= pair cap；
- 合并通过字段为 `l2_top_aligned_vwap_pass`；
- 输出通过子集的 `buy_actual_est`、`cash_pnl_est`、`roi_est`，但仍不声明 private fill truth 或 live readiness。

Representative 3.0% fee results:

| branch | actions | markets | L2 pass actions | L2 pass markets | L2 pass rate | L2 pass PnL | L2 pass ROI | full replay ROI | status |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| entry_paircap_cap_0.970 | 151 | 146 | 144 | 139 | 95.3642% | 19.511370 | 5.3873% | 5.3110% | BLOCKED |
| same_row_cap_0.965 | 100 | 96 | 95 | 91 | 95.0000% | 17.037011 | 7.1409% | 7.0610% | BLOCKED |
| entry_paircap_cap_0.965 | 100 | 96 | 95 | 91 | 95.0000% | 17.037011 | 7.1409% | 7.0610% | BLOCKED |
| entry_paircap_target_qty_8 | 210 | 203 | 189 | 182 | 90.0000% | 46.176679 | 4.2887% | 4.0508% | BLOCKED |
| entry_paircap_target_qty_13 | 210 | 203 | 186 | 179 | 88.5714% | 64.648503 | 4.2733% | 4.1055% | BLOCKED |

All representative branches remain `BLOCKED_L2_TOP_ALIGNED_ACTION_VALIDATION_GAPS` because none reaches 100% clean L2 pass. The positive result is narrower: the L2-passing subsets remain fee-positive and are not just low-quality leftovers.

Interpretation:

- Current best mainline is `entry_paircap_cap_0.970`: broader than 0.965, still 95.36% L2 pass, and pass-subset ROI remains 5.39%.
- Conservative control is `cap_0.965`: lower coverage but pass-subset ROI 7.14%.
- `target_qty=8/13` are capacity studies, not clean-path candidates yet. Their pass rates fall to 90.00% / 88.57%; remaining failures include top5 VWAP cost/depth, not only stale L2.
- The validator found top price match rate = 100% and top5 depth fillable rate = 100% for all tested branches, so the blocker is quality/cost freshness on the tail, not missing book coverage.

Follow-up top1 qty gate result:

`ce25_high_price_top1_qty_gate_iter2_20260604` added `entry_requires_opposite_qty=true`, so entry is skipped unless opposite top1 size covers the current paired qty. This directly targets the prior fail reason `TOP1_DEPTH_TOP5_VWAP_GT_CAP`.

| branch | actions | markets | L2 pass | ROI | status |
| --- | ---: | ---: | ---: | ---: | --- |
| entry_paircap_top1_qty_cap_0.970_pxhi_0.80 | 142 | 137 | 142/142 | 5.4090% | KEEP |
| same_row_top1_qty_cap_0.970_pxhi_0.80 | 143 | 137 | 143/143 | 5.3811% | KEEP |
| entry_paircap_top1_qty_cap_0.970_pxhi_0.79 | 121 | 118 | 121/121 | 5.5942% | KEEP |
| same_row_top1_qty_cap_0.970_pxhi_0.79 | 122 | 118 | 122/122 | 5.5599% | KEEP |

Updated interpretation: `entry_paircap_top1_qty_cap_0.970_pxhi_0.80` is now the preferred local L2-clean research candidate. It is still not private truth, live, promotion, or deployable.

## 已拒绝路线

Delay buckets are not the path. Earlier `ce25_high_price_delay_bucket_iter0_20260604` showed `250ms/500ms/1000ms/3000ms` relaxed completion windows were negative with huge residual. The high-price branch is not “enter high price then chase the opposite side”; it only works when entry itself already has immediate paircap/opposite-depth support.

Broad branch and last60 control remain negative controls. They should stay in runner only as guardrails, not as candidate generators.

## Runner v1 Upgrade

Implemented:

- capacity mutations for `target_qty=1,2,3,5,8,13`;
- depth stress mutations for `fill_haircut=0.25,0.50,0.75,1.00`;
- strict paircap sweep for `0.965,0.970,0.975,0.980`;
- paired market count and max-market concentration metrics;
- `branch_control_summary.csv`;
- `capacity_stress_summary.csv`;
- `--book-shadow-summary-only`, which skips per-action/per-residual detail CSVs for large sweeps.

Smoke:

`ce25_high_price_summary_only_smoke_20260604` ran 4 result rows with `--book-shadow-summary-only`; no `book_shadow_actions.csv` or `book_shadow_residual_lots.csv` were emitted, and the output directory was 84KB.

## 下一步

1. Run the next CE25 high-price sweep with `--book-shadow-summary-only` by default.
2. Promote only the strict survivor family to L2/top-depth validation:
   - `same_row_pair_only`
   - `entry_paircap_required`
   - paircap `0.965/0.970/0.975/0.980`
   - `target_qty=3/8/13`
3. Add per-market participation and capacity concentration checks, because current `target_qty=13` still uses the same 210-215 action set.
4. Do not refresh 9F5F/Username123123 by default. They are no longer the critical path.
5. Do not claim private truth, promotion, live readiness, or deployability until own execution telemetry or a stricter replay/source-of-truth layer exists.
