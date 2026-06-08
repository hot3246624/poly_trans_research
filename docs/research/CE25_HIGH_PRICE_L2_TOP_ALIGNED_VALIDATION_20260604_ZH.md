# CE25 High-Price L2 Top-Aligned Validation 2026-06-04

## 结论

`CE25_BTC5M_HIGH_PRICE_STOP_1_5M_V1` strict paircap 分支通过了第一层本地 capacity/depth replay。首轮 `cap_0.970` L2 验证没有通过 100% clean path；随后新增 `entry_requires_opposite_qty` / top1 qty gate 后，已经得到本地 L2 clean 子集。当前最高状态应更新为：

`KEEP_L2_TOP_ALIGNED_ACTIONS_VALIDATED_REVIEW_REQUIRED`

这仍然不是实盘结论。更准确的判断是：

- 原 `entry_paircap_cap_0.970` 的 L2 blocker 主要来自 opposite leg top1 depth 不足后 top5 VWAP 超 cap；
- 新 top1 qty gate 在 entry 时要求 opposite top1 depth 覆盖当前 qty，把该 tail risk 前置过滤；
- 低尺寸基线 `entry_paircap_top1_qty_cap_0.970_pxhi_0.80` 达到 142/142 L2 pass，137 markets，3.0% fee 后 ROI 5.4090%；
- 当前容量主线 `entry_paircap_top1_qty_target_qty_8_cap_0.970_pxhi_0.80` 达到 134/134 L2 pass，129 markets，3.0% fee 后 ROI 5.6140%，targeted fee/day stability 支撑也已补齐；
- 下一层应围绕 target_qty=8 top1 qty gate 生成 normalized candidate ledger / strategy input，并做更严格 source-of-truth replay，而不是回到 9F5F/Username123123 或共享 WS 方向。

本报告只使用本地 public/replay 数据，不触网，不加载私钥，不 import candidate，不下单/撤单/redeem，不声明 private truth、promotion、live ready 或 deployable。

## 输入与代码

- validator: `/Users/hot/web3Scientist/poly_trans_research/scripts/validate_ce25_high_price_l2_top_aligned_actions.py`
- validator sha256: `bbfa28d570c1f6a77d53ab8a11cb8f96c8e78f46f2a59e3e94d11c144d96f7ab`
- book-shadow source run: `/Users/hot/web3Scientist/poly_backtest_data/derived/ce25_nagi_shadow_policy_autoresearch_v0/ce25_high_price_capacity_depth_iter1_20260604`
- L2 top-aligned mart manifest: `/Users/hot/web3Scientist/poly_backtest_data/derived/contract_examples/l2_top_aligned_mart_20260502_20260518_l2/L2_TOP_ALIGNED_MART_MANIFEST.json`
- candidate base: `/Users/hot/web3Scientist/poly_backtest_data/derived/completion_candidate_pipeline_v1/local_20260502_20260518_paircap102/candidate_base.duckdb`
- raw L2 age threshold: 750ms
- price epsilon: 1e-6

## 验证口径

Validator 使用 exact source-row bridge，而不是全量 L2 mart ASOF 扫描：

1. 从 book-shadow action 映射回 `candidate_base.strict_l1_row_id`。
2. 对 first/completion 两条 leg 读取 `md_book_l2_top_aligned` 中相同 `l1_source_row_id` 的 canonical top + raw L2 top5 depth。
3. `l1_top_pair_pass`: 两条 leg top price 与 action price 匹配、top1 depth 覆盖 action qty、L1 pair cost <= pair cap。
4. `depth_assisted_pair_pass`: 两条 leg top price 匹配、raw L2 age <= 750ms、top5 depth 覆盖 action qty、top5 VWAP pair cost <= pair cap。
5. `l2_top_aligned_vwap_pass`: `l1_top_pair_pass OR depth_assisted_pair_pass`。

这个口径把 canonical L1 top 当作主证据，raw L2 只用于 top1 以外的深度辅助。它不证明真实成交、排队优先级或私有 order truth。

## 代表分支结果

### Top1 Qty Gate Iter2

source replay:

`/Users/hot/web3Scientist/poly_backtest_data/derived/ce25_nagi_shadow_policy_autoresearch_v0/ce25_high_price_top1_qty_gate_iter2_20260604`

hashes:

- `AUTORESEARCH_MANIFEST.json` sha256 `67fad3376896b2be6405d62d7b82a8e41cd7fab68b894b0638d935f80b62ff91`
- `autoresearch_ledger.csv` sha256 `b946a97f1ee9fb4569fe95bcbfc0f9b51b70ac10a81471707bfb073092d5a32c`
- `variant_plan.csv` sha256 `e95b4c7cfae7988e31c08649eca04b507aacde4cec2d6ebbc7843d95f37f4475`

L2 clean results:

| branch | actions | markets | L2 pass actions | L2 pass rate | ROI | overlay review count | status |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| entry_paircap_top1_qty_cap_0.970_pxhi_0.80 | 142 | 137 | 142 | 100.0000% | 5.4090% | 90 | KEEP |
| same_row_top1_qty_cap_0.970_pxhi_0.80 | 143 | 137 | 143 | 100.0000% | 5.3811% | 91 | KEEP |
| entry_paircap_top1_qty_cap_0.970_pxhi_0.79 | 121 | 118 | 121 | 100.0000% | 5.5942% | 74 | KEEP |
| same_row_top1_qty_cap_0.970_pxhi_0.79 | 122 | 118 | 122 | 100.0000% | 5.5599% | 75 | KEEP |

L2 output manifests:

- `entry_paircap_top1_qty_cap_0p970_pxhi_0p80_iter2_l2_v2`: sha256 `30b39e27b60e082c5756c93ee36f575a356234d951d160f2df9899fa57272e83`
- `same_row_top1_qty_cap_0p970_pxhi_0p80_iter2_l2_v2`: sha256 `04487a331e97156e2620ae872050ee3263f8d12b8edbd5d0020ad6357f8b0926`
- `entry_paircap_top1_qty_cap_0p970_pxhi_0p79_iter2_l2_v2`: sha256 `ddb4c3e4679a7f4f59c36a227c118f193ef35d02b2df3d03c8d5ba354a77ef20`
- `same_row_top1_qty_cap_0p970_pxhi_0p79_iter2_l2_v2`: sha256 `7365185167a85e7c1d7d5310185f982d8782a991d24c72541112475c8aaff8cc`

The overlay count is a review note, not a default hard fail. `top_overlay_required` means the L2 mart needed canonical L1 top overlay for raw L2 top alignment; the validator still requires action top price match, top1/top5 depth, and pair cost gates. To hard-block on overlays, run the validator with `--max-top-overlay-required-actions 0`.

### Targeted Fee Stress

Targeted fee stress was computed from the fixed local top1-gate action sets with the runner's official taker fee formula. It does not rerun strategy selection per fee.

Output:

`/Users/hot/web3Scientist/poly_backtest_data/derived/ce25_high_price_top1_qty_gate_fee_stress_targeted_20260604`

hashes:

- `CE25_TOP1_QTY_GATE_TARGETED_FEE_STRESS_MANIFEST.json` sha256 `076ca2dfa4530ff28cd0e7e2fb3d34af84831e04158037ab0a2e55a7364a00fd`
- `ce25_top1_qty_gate_targeted_fee_stress.csv` sha256 `f32cc2e5fd80751a9ee45623c2d3f4aefe58be3ed8b557390df67cc40e30616a`

| branch | fee 0% ROI | fee 0.8% ROI | fee 2.5% ROI | fee 2.83% ROI | fee 3.0% ROI |
| --- | ---: | ---: | ---: | ---: | ---: |
| entry_paircap_top1_qty_cap_0.970_pxhi_0.80 | 6.6239% | 6.2972% | 5.6096% | 5.4771% | 5.4090% |
| same_row_top1_qty_cap_0.970_pxhi_0.80 | 6.5951% | 6.2687% | 5.5816% | 5.4492% | 5.3811% |
| entry_paircap_top1_qty_cap_0.970_pxhi_0.79 | 6.8560% | 6.5166% | 5.8025% | 5.6649% | 5.5942% |
| same_row_top1_qty_cap_0.970_pxhi_0.79 | 6.8201% | 6.4811% | 5.7679% | 5.6305% | 5.5599% |

Interpretation: fee stress does not flip the top1 gate branch. The 0.79 branch has slightly higher ROI but lower coverage; 0.80 remains preferred mainline because it keeps 142 actions / 137 markets while staying L2-clean.

### Day Stability

The preferred `entry_paircap_top1_qty_cap_0.970_pxhi_0.80` branch was summarized by day at 3.0% fee.

Output:

`/Users/hot/web3Scientist/poly_backtest_data/derived/ce25_high_price_top1_qty_gate_day_stability_20260604`

hashes:

- `CE25_TOP1_QTY_GATE_DAY_STABILITY_MANIFEST.json` sha256 `3c3877d9f910112d039e5fa55f0d89d45e1ead3093678349686c5d254fd583a2`
- `ce25_top1_qty_gate_day_stability.csv` sha256 `393ad2bfc8fb244521fdd08be569e7900c9c1cf5cd5dce9855e35c74e516bc42`

Result:

- 15 active days.
- 15/15 profitable days.
- 142 actions / 137 markets.
- weakest day ROI: 2.2672% on 2026-05-06.
- strongest day ROI: 12.6770% on 2026-05-04.

Interpretation: current result is not a single-day artifact. It is still limited to the local May 2-18 replay universe and does not prove forward/live execution.

### Top1 Gate Capacity Lane

Capacity lane added `target_qty=5/8` while preserving the top1 qty gate and 3.0% fee stress.

Replay output:

`/Users/hot/web3Scientist/poly_backtest_data/derived/ce25_nagi_shadow_policy_autoresearch_v0/ce25_high_price_top1_qty_capacity_iter4_20260604`

hashes:

- `AUTORESEARCH_MANIFEST.json` sha256 `7d40da6a2569a7076cea22167787d6d7488d19a477b16f7245d4dbd52e4aba31`
- `autoresearch_ledger.csv` sha256 `145c4f0f016a069f3c11ea11fd2ab95fb340ffcab115c44cd5fb5ae21f1772d1`
- `variant_plan.csv` sha256 `6be9eff12acdbb2a810ce783748410a8038cbd58d56ef9b019fcbab28d5df9da`

L2 clean results:

| branch | target_qty | actions | markets | pair_qty | PnL | ROI | L2 pass | status |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| entry_paircap_top1_qty_target_qty_5_cap_0.970_pxhi_0.80 | 5 | 141 | 136 | 582.7125 | 30.367153 | 5.4979% | 141/141 | KEEP |
| same_row_top1_qty_target_qty_5_cap_0.970_pxhi_0.80 | 5 | 142 | 136 | 587.7125 | 30.464908 | 5.4670% | 142/142 | KEEP |
| entry_paircap_top1_qty_target_qty_8_cap_0.970_pxhi_0.80 | 8 | 134 | 129 | 788.7000 | 41.923672 | 5.6140% | 134/134 | KEEP |
| same_row_top1_qty_target_qty_8_cap_0.970_pxhi_0.80 | 8 | 135 | 129 | 796.7000 | 42.080080 | 5.5763% | 135/135 | KEEP |

L2 manifest hashes:

- `entry_paircap_top1_qty_target_qty_5_cap_0p970_pxhi_0p80_iter4_l2_v1` sha256 `f84cb74d88f2c0db52c0230e2100d017328a1c7660d088c09c03c56880db0339`
- `same_row_top1_qty_target_qty_5_cap_0p970_pxhi_0p80_iter4_l2_v1` sha256 `24d66c1d020a694483acfc8f52d8ede6b9440aa8be058ba857e803fa607b9086`
- `entry_paircap_top1_qty_target_qty_8_cap_0p970_pxhi_0p80_iter4_l2_v1` sha256 `178761cfeaff9479b25ee5cd6c812e7979d6d4328f05100668a232d71b7ef2b2`
- `entry_paircap_top1_qty_target_qty_8_cap_0p970_pxhi_0p80_iter4_l2_v2_leg_evidence` sha256 `eff2dfccafb89b6b3925106c1f318a4f6c2cf4afe542c1c7fd17220222f1438d`
- `entry_paircap_top1_qty_target_qty_8_cap_0p970_pxhi_0p80_iter4_l2_v2_leg_evidence` leg evidence CSV sha256 `0c98672df350f188ab614e239a8f1e230ba190d9805bd042d8023b55028db4d9`
- `same_row_top1_qty_target_qty_8_cap_0p970_pxhi_0p80_iter4_l2_v1` sha256 `fa0827cb8115ad9fcf6818d06817d85d43ec567c62cbdf4c90799d21d17a1557`

Interpretation: `target_qty=8` is now the preferred capacity branch: it preserves 100% L2 pass and raises PnL materially while keeping 134 actions / 129 markets. `target_qty=3` remains the lower-size baseline.

### Target Qty 8 Support Check

`target_qty=8` capacity branch was rechecked with fixed-action fee stress and day stability at 3.0% fee. This check uses the already L2-clean `entry_paircap_top1_qty_target_qty_8_cap_0.970_pxhi_0.80` action set; it does not rerun selection per fee.

Output:

`/Users/hot/web3Scientist/poly_backtest_data/derived/ce25_high_price_top1_qty_target_qty8_support_20260604`

hashes:

- manifest sha256 `37267da99522bdef05dba6c38382f18b246267c4ef9637cc5c86cc369f8a8acd`
- fee stress CSV sha256 `7186de6fad432de08d84857f3f87884e118758594abf66302d4c64563f2b4cf2`
- day stability CSV sha256 `2cf7762e3f3fbf029e324c663ebf05b213063a3001df48d03f6dcce15c404206`

Fee stress:

| fee rate | PnL | ROI |
| ---: | ---: | ---: |
| 0.00% | 50.449900 | 6.8337% |
| 0.80% | 48.176238 | 6.5057% |
| 2.50% | 43.344707 | 5.8153% |
| 2.83% | 42.406822 | 5.6823% |
| 3.00% | 41.923669 | 5.6140% |

Day stability:

- 15 active days.
- 15/15 profitable days.
- 134 actions / 129 markets.
- weakest day: 2026-05-06, 9 actions / 9 markets, PnL 1.369499, ROI 2.3505%.

Interpretation: target_qty=8 did not buy extra PnL by concentrating into one day. The branch remains local replay only; it still needs a normalized candidate ledger and independent source-of-truth replay before any OOS-style review.

### Target Qty 8 Candidate Ledger

The target_qty=8 branch has been converted into a local review-only normalized candidate ledger. This is a historical replay-bound artifact, not a current/future OOS target set.

Output:

`/Users/hot/web3Scientist/poly_trans_research/data/exports/ce25_high_price_top1_qty_target_qty8_candidate_ledger_20260604`

hashes:

- hash manifest sha256 `078e029de53d747edc7b02fc570867fbc803f344d725ef2daa1bb36b099e6f9c`
- candidate ledger CSV sha256 `8e8789de811081e23cac855ed1339d3546a6bd155e55af31be9aeba93c413c65`
- strategy input JSON sha256 `b8553cbcea1e2fe88cb72a8993aeee7c7884b8bef9d146e33398db920eb32594`
- review note sha256 `9c9f76c3072d27a5837330042f1666b58a50f2973293cfbbcff4e19d93fb2bee`

Ledger summary:

- status: `KEEP_CE25_TARGET_QTY8_NORMALIZED_CANDIDATE_LEDGER_REVIEW_REQUIRED_NOT_OOS_READY`
- strategy id: `CE25_BTC5M_HIGH_PRICE_TOP1_QTY_GATE_V1`
- candidate rows: 134.
- markets/slugs: 129.
- active days: 15.
- buy_actual_est: 746.776329.
- cash_pnl_est: 41.923672.
- ROI estimate: 5.6140%.
- max rows per market: 3.
- raw L2 age OK pair rows: 88/134.
- per-leg evidence rows: 268, with YES/NO leg-side semantics and non-empty L1/L2 source row ids.
- top overlay review rows: 84/134.

Fail-closed checks in the builder require exact 134 rows, unique candidate ids, BTC 5m slugs, opposite YES/NO legs, high-price leg in 0.65-0.80, paired_qty <= 8, source pair cost <= 0.970, all L2 pass rows, residual_qty=0, positive PnL, exact branch id, and all non-claims false.

Interpretation: this ledger is now the handoff object for the next local replay/source bridge. It deliberately remains `REVIEW_REQUIRED_NOT_OOS_READY` because top overlay and raw-L2 freshness limitations need independent source-of-truth treatment.

### Target Qty 8 Source Bridge

The normalized candidate ledger has been independently bridged back to source rows:

- ledger -> source book-shadow actions
- source book-shadow actions -> `candidate_base.duckdb`
- per-leg evidence -> `md_book_l2_top_aligned`

Output:

`/Users/hot/web3Scientist/poly_trans_research/data/exports/ce25_high_price_top1_qty_target_qty8_source_bridge_20260604`

hashes:

- hash manifest sha256 `a6f90926ede0455806e70a105e940a9db08317159b3d9b70988c7e12703b03fe`
- summary JSON sha256 `f841b7948b7b379198bbb06eab139fe13bcd14df8b13d4f2cd8eb96ff3c1048c`
- row audit CSV sha256 `ddfc98abc451f97309d7ff4f1bdef3a4b9b2be91f95b915a0be63153dfa5471d`
- review note sha256 `a96e5c6c638d3de28d768e6f57a021cd1e21ca41399806868adc054c86247497`

Bridge summary:

- status: `KEEP_CE25_TARGET_QTY8_SOURCE_BRIDGE_VALIDATED_REVIEW_REQUIRED_NOT_OOS_READY`
- candidates: 134.
- markets: 129.
- source actions loaded: 134.
- candidate_base rows loaded: 134.
- L2 mart rows loaded: 268.
- leg evidence rows: 268.
- row audit failures: 0.
- row audit errors: 0.

Interpretation: source identity and evidence lineage now match exactly at the local replay layer. The remaining blocker is not source-row drift; it is evidence class: 84 rows still need top-overlay review and only 88/134 pairs have raw L2 age OK, so the next pass should explain or reduce that dependency before any OOS-style packet.

### Overlay/Freshness Attribution

The target_qty=8 source bridge was decomposed by canonical L1 overlay and raw L2 freshness dependency.

Output:

`/Users/hot/web3Scientist/poly_trans_research/data/exports/ce25_high_price_top1_qty_target_qty8_overlay_freshness_attribution_20260604`

hashes:

- hash manifest sha256 `cef55d13888387aad1a6875a7bdbf2f7f31ff9d6c553fb45d9838489dd44736b`
- summary JSON sha256 `7db805e16146a6ec87240c9c915506a21617892c76a4339ff3618215f2ca556f`
- action audit CSV sha256 `6a8574afcf5b76e391df67170a86254378abe2a01b9580f68c9dd06122f5912f`

Dependency categories:

| category | actions |
| --- | ---: |
| NO_OVERLAY_RAW_L2_OK | 5 |
| OVERLAY_ONLY | 83 |
| RAW_L2_STALE_ONLY | 45 |
| OVERLAY_AND_RAW_L2_STALE | 1 |

Additional facts:

- All 134 rows still pass canonical L1 top pair and top1 depth checks.
- All 134 rows have `l2_top_aligned_fail_reason=PASS`.
- raw L2 age OK pairs: 88/134.
- raw L2 stale pairs: 46/134 across 46 markets.
- top overlay rows: 84/134 across 81 markets.
- max raw L2 age p50/p95/max: 22.0 / 10128.35 / 10506 ms.

Interpretation: strict source bridge is clean, but evidence class is not yet strong enough for OOS/live. A hard no-overlay + raw-L2-fresh rule would leave only 5 actions; that is not a viable full strategy. The next useful local work is to decide whether canonical L1 top evidence is acceptable for this research layer, or whether the candidate must be narrowed to a much smaller raw-L2-fresh subset.

### Evidence Policy Decision Packet

The evidence-policy decision has been materialized as a review-only packet. It accepts canonical L1 top1 depth evidence only for local historical replay review, while keeping top-overlay/raw-L2-stale dependencies as blockers for OOS/live/private-truth claims.

Output:

`/Users/hot/web3Scientist/poly_trans_research/data/exports/ce25_high_price_top1_qty_target_qty8_evidence_policy_packet_20260604`

hashes:

- hash manifest sha256 `c765d86b017a45a094ca8b89f1bc52ba363218b3edbafcb7e89a09215aaf4cc2`
- evidence policy decision sha256 `250cf6a2f4fc583aed513ff4285ff5e5fb0424c41136d8da08cc188ddcca1ab4`
- review-only strategy packet sha256 `a680db3b47acd5735aa2bd1ebc5662611087afcfb191817ad47d229234c478e2`
- threshold spec sha256 `7f0c2f4a57f897fa93f9c12abe463aaf1e55abeecadda3184d2d12d13a5a9dd3`
- command preview sha256 `31c8ffc829801926eb8713a74490b95630929fc33702a30f1f8da7a36591f8ad`

Decision:

`ACCEPT_CANONICAL_L1_TOP1_DEPTH_FOR_LOCAL_REVIEW_ONLY`

Highest status:

`KEEP_CE25_TARGET_QTY8_CANONICAL_L1_EVIDENCE_POLICY_ACCEPTED_REVIEW_ONLY_NOT_OOS_READY`

The command preview exits 66 and no exact approval is issued. This packet can support a historical replay-bound strategy review, but cannot be used as OOS pass, live/canary approval, private truth, promotion readiness, or deployability evidence.

### Pre-Gate Baseline

| branch | actions | markets | pass actions | pass markets | pass rate | pass buy | pass PnL | pass ROI | total ROI |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| entry_paircap_cap_0.970 | 151 | 146 | 144 | 139 | 95.3642% | 362.173630 | 19.511370 | 5.3873% | 5.3110% |
| same_row_cap_0.965 | 100 | 96 | 95 | 91 | 95.0000% | 238.582989 | 17.037011 | 7.1409% | 7.0610% |
| entry_paircap_cap_0.965 | 100 | 96 | 95 | 91 | 95.0000% | 238.582989 | 17.037011 | 7.1409% | 7.0610% |
| entry_paircap_target_qty_8 | 210 | 203 | 189 | 182 | 90.0000% | 1076.695824 | 46.176679 | 4.2887% | 4.0508% |
| entry_paircap_target_qty_13 | 210 | 203 | 186 | 179 | 88.5714% | 1512.834000 | 64.648503 | 4.2733% | 4.1055% |

Per-run output dirs:

- `/Users/hot/web3Scientist/poly_backtest_data/derived/ce25_high_price_l2_top_aligned_validation_v0/entry_paircap_cap_0p970_fee_0p0300_exact_bridge_v5_20260604`
- `/Users/hot/web3Scientist/poly_backtest_data/derived/ce25_high_price_l2_top_aligned_validation_v0/same_row_cap_0p965_fee_0p0300_exact_bridge_v5_20260604`
- `/Users/hot/web3Scientist/poly_backtest_data/derived/ce25_high_price_l2_top_aligned_validation_v0/entry_paircap_cap_0p965_fee_0p0300_exact_bridge_v5_20260604`
- `/Users/hot/web3Scientist/poly_backtest_data/derived/ce25_high_price_l2_top_aligned_validation_v0/entry_paircap_target_qty_8_fee_0p0300_exact_bridge_v5_20260604`
- `/Users/hot/web3Scientist/poly_backtest_data/derived/ce25_high_price_l2_top_aligned_validation_v0/entry_paircap_target_qty_13_fee_0p0300_exact_bridge_v5_20260604`

## 分支排序

1. `entry_paircap_top1_qty_target_qty_8_cap_0.970_pxhi_0.80`
   - 当前容量主分支。
   - 134 actions / 129 markets，134 actions 通过 L2 合并口径。
   - 3.0% fee 后 PnL 41.923672，ROI 5.6140%，0 residual。
   - target_qty=8 support check 中 15/15 active days profitable，2.83% fee ROI 5.6823%。

2. `entry_paircap_top1_qty_cap_0.970_pxhi_0.80`
   - 当前低尺寸基线。
   - 142 actions / 137 markets，142 actions 通过 L2 合并口径。
   - 3.0% fee 后 ROI 5.4090%，0 residual。
   - 比 pre-gate `cap_0.970` 少 9 actions，但去掉了全部 L2 fail rows。

3. `same_row_top1_qty_cap_0.970_pxhi_0.80`
   - 143 actions / 137 markets，143 actions 通过。
   - ROI 5.3811%。
   - 与 entry-paircap 主分支几乎等价，可作为 implementation shape 对照。

4. `entry_paircap_top1_qty_cap_0.970_pxhi_0.79`
   - 121 actions / 118 markets，121 actions 通过。
   - ROI 5.5942%。
   - 更保守，适合作为 tighter price-band 对照。

5. `same_row_cap_0.965` / `entry_paircap_cap_0.965`
   - 保守对照分支。
   - 100 actions / 96 markets，95 actions 通过。
   - 通过子集 ROI 7.1409%，但覆盖刚过 floor，不能单独代表完整策略。

6. `entry_paircap_target_qty_8`
   - 中等容量分支。
   - 210 actions / 203 markets，189 actions 通过。
   - 通过子集 ROI 4.2887%，但 clean pass rate 只有 90%。

7. `entry_paircap_target_qty_13`
   - 高容量边界分支。
   - 210 actions / 203 markets，186 actions 通过。
   - 通过子集 ROI 4.2733%，但 tail cost 更差，max top5 pair VWAP cost 到 1.035385。

## 当前 Fail-Closed 原因

pre-gate 代表分支是 `BLOCKED_L2_TOP_ALIGNED_ACTION_VALIDATION_GAPS`，因为 clean path 要求 100% action 通过，而 pre-gate 最高仅 95.3642%。

这条 blocking 不是低级数据缺失：

- top price match pair rate = 100%；
- top5 depth fillable pair rate = 100%；
- L2 pass 子集仍 fee-positive；
- tail failure 主要是 raw L2 freshness 与 top5 VWAP/worst cost 超 cap。

v5 validator 增加了 per-action `l2_top_aligned_fail_reason`。代表分支 fail reason 分布：

| branch | PASS | TOP1_DEPTH_TOP5_VWAP_GT_CAP | TOP1_DEPTH_AND_RAW_L2_STALE |
| --- | ---: | ---: | ---: |
| entry_paircap_cap_0.970 | 144 | 6 | 1 |
| same_row_cap_0.965 | 95 | 4 | 1 |
| entry_paircap_cap_0.965 | 95 | 4 | 1 |
| entry_paircap_target_qty_8 | 189 | 17 | 4 |
| entry_paircap_target_qty_13 | 186 | 19 | 5 |

Interpretation: blocker is mostly price/depth quality at the tail, not L2 age. Do not “fix” this by relaxing age threshold; tighten cap/price/qty first.

## 下一步

1. 把 `entry_paircap_top1_qty_target_qty_8_cap_0.970_pxhi_0.80` 固化为 `CE25_BTC5M_HIGH_PRICE_TOP1_QTY_GATE_V1` capacity mainline。
2. 基于 evidence-policy packet 做 historical replay-bound strategy review；仍然不生成 OOS/live approval。
3. 若要提升到 OOS-style evidence，必须先解决 top-overlay/raw-L2-fresh 覆盖问题，不能直接复用本 packet。
4. 只把 old `target_qty=8/13` pre-gate branch 作为容量研究，不进入 clean OOS/production style packet。
5. 不恢复 9F5F/Username123123 主线，除非后续 CE25 top1 gate 被更严格 replay/source-of-truth 明确否定。
