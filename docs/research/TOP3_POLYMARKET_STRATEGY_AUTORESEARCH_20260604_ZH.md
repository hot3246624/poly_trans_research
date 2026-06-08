# TOP3 Polymarket Strategy Autoresearch 2026-06-04

## 结论

本轮把 `TOP3_POLYMARKET_STRATEGY_HANDOFF_20260604_ZH.md` 中的 9F5F / CE25 / Username123123 三条线转成了本地 book-shadow replay，而不是继续停留在账户 profile 叙述。

核心判断：

1. `9F5F_BTC_LAST60_MIDPRICE_V1` 不能直接按公开 24h profile 放大执行；普通分支在本地 2026-05-02 至 2026-05-18 盘口回放中表现不稳。
2. 加入 residual-killer 入口后，9F5F 出现低覆盖、高质量 micro-alpha：2.83% fee stress 后仍为正，残差 0，但单分支只有 23 到 48 个 paired actions，分类应为 `KEEP_WATCH_LOW_COVERAGE`。
3. `CE25_BTC5M_LOW_PRICE_TAIL_DOWN_V1` 的主要价值从“高覆盖尾部策略”转成“低残差成对盘口模板”：原始 longer SLA 分支有更高 nominal PnL，但残差约 49.5%；same-row / entry-paircap 分支残差 0、fee stress 后仍为正，但 paired actions 只有 49 到 65，也应视为低覆盖 watch。
4. `CE25_BTC5M_HIGH_PRICE_STOP_1_5M_V1` 原本是风险过滤器；在 strict paircap 分支下出现 200+ paired actions、0 residual、fee stress 后正收益，是当前唯一达到 full-keep 覆盖阈值的本地 replay 候选，但仍不能声明可执行策略。
5. `USERNAME123123_SHORT_BURST_LOW_RESID_V1` 不建议继续刷新作为当前主线。它有两个漂亮 20 分钟 burst，但 2026-05-27 12:00-13:30 BJT 扩大窗口为负，说明它更像短窗口 pocket，不是当前值得继续消耗 API/profile 时间的主线。

所有产物仅为本地 public-data research。没有私钥、import、order、cancel、redeem、live、deploy、funding、shared-WS 依赖或 readiness claim。

## 输入

- 研究 handoff：`/Users/hot/web3Scientist/poly_trans_research/docs/research/TOP3_POLYMARKET_STRATEGY_HANDOFF_20260604_ZH.md`
- TOP3 strategy input：`/Users/hot/web3Scientist/poly_trans_research/configs/top3/TOP3_POLYMARKET_STRATEGY_INPUT_v0.json`
- 本地 replay master base：`/Users/hot/web3Scientist/poly_backtest_data/derived/completion_candidate_pipeline_v1/local_20260502_20260518_paircap102`
- runner：`/Users/hot/web3Scientist/poly_trans_research/scripts/run_ce25_nagi_shadow_policy_runner.py`
- runner sha256：`69e083f59de55730d53c0cb6da165af0b45770394d971fe4e35f8dc31a896daf`
- CE25 L2/top-aligned validator：`/Users/hot/web3Scientist/poly_trans_research/scripts/validate_ce25_high_price_l2_top_aligned_actions.py`
- CE25 L2/top-aligned validator sha256：`bbfa28d570c1f6a77d53ab8a11cb8f96c8e78f46f2a59e3e94d11c144d96f7ab`
- TOP3 input sha256：`665e94171cc87243f088c894d5ce1939a894372b4296d9a49a77672fb821394a`

## 已完成运行

### TOP3 iter0

路径：

`/Users/hot/web3Scientist/poly_backtest_data/derived/ce25_nagi_shadow_policy_autoresearch_v0/top3_book_shadow_iter0_20260604`

结果摘要：

- 37 variants x 5 fee rates = 185 results。
- 绝大多数普通迁移分支为 `DISCARD_NEGATIVE_FEE_AFTER_PNL`。
- 9F5F 普通 branch 在公开 profile 上很强，但本地历史 book-shadow OOS-like replay 没复制出可用强度。
- iter0 结论：不能把 9F5F 公开 24h profile 直接升级为 implementation P0。

关键哈希：

- `AUTORESEARCH_MANIFEST.json` sha256 `4e7178d64180b7806fc1c5223a9d524f885f33e7644558144a7fc21b3ad07f2d`
- `autoresearch_ledger.csv` sha256 `1b392838ed2faa41f764f58d6720dbf9c5ca5d10e46a8898b562235caec71d47`

### CE25/NAGI residual-killer iter0

路径：

`/Users/hot/web3Scientist/poly_backtest_data/derived/ce25_nagi_shadow_policy_autoresearch_v0/ce25_residual_killer_iter0_20260604`

结果摘要：

- 53 variants x 2 fee rates = 106 results。
- `KEEP_LOCAL_REPLAY_CANDIDATE`: 20。
- `DISCARD_NEGATIVE_FEE_AFTER_PNL`: 78。
- `KEEP_WATCH_RESIDUAL_HIGH`: 8。

2.83% fee stress 下的 0 residual 正向分支：

| policy | branch | pnl | roi | paired actions | residual | pair cost |
|---|---:|---:|---:|---:|---:|---:|
| NAGI_LAST60_MIDPRICE_FASTPAIR_V1 | last60_up_35_50_fastpair_same_row_pair_only | 15.524077 | 0.118707 | 38 | 0 | 0.880714 |
| NAGI_LAST60_MIDPRICE_FASTPAIR_V1 | last60_down_50_65_fastpair_same_row_pair_only | 13.928891 | 0.091444 | 44 | 0 | 0.903503 |
| CE25_BTC5M_LOW_PRICE_TAIL_DOWN_V1 | last60_down_20_35_same_row_pair_only | 13.691226 | 0.088158 | 49 | 0 | 0.907347 |
| CE25_BTC5M_HIGH_PRICE_STOP_1_5M_V1 | one_to_five_min_any_65_80_entry_paircap_required | 21.951794 | 0.040246 | 210 | 0 | 0.950805 |

关键哈希：

- `AUTORESEARCH_MANIFEST.json` sha256 `3326e6c683070c8e88009b020388d91ee967001bdf52b9b5e1151d215962fe55`
- `autoresearch_ledger.csv` sha256 `660fbb1f388185ba27b0bcdd29862e28761aee3fc708d2079ad1d10d3342d1a0`

### TOP3 residual-killer cached iter3

路径：

`/Users/hot/web3Scientist/poly_backtest_data/derived/ce25_nagi_shadow_policy_autoresearch_v0/top3_residual_killer_cached_iter3_20260604`

结果摘要：

- 94 variants x 3 fee rates = 282 results。
- 精确 scan cache 命中 226/282 次；56 次为首次创建精确 variant scan cache。
- `KEEP_WATCH_LOW_COVERAGE`: 48。
- `KEEP_WATCH_RESIDUAL_HIGH`: 15。
- `KEEP_LOCAL_REPLAY_CANDIDATE`: 6。
- `DISCARD_NEGATIVE_FEE_AFTER_PNL`: 213。

2.83% fee stress 下，full-keep 分支只有 CE25 high-price control/template：

| policy | branch | pnl | roi | paired actions | residual | pair cost |
|---|---:|---:|---:|---:|---:|---:|
| CE25_BTC5M_HIGH_PRICE_STOP_1_5M_V1 | one_to_five_min_any_65_80_same_row_pair_only | 22.073280 | 0.039823 | 214 | 0 | 0.951185 |
| CE25_BTC5M_HIGH_PRICE_STOP_1_5M_V1 | one_to_five_min_any_65_80_entry_paircap_required | 21.951794 | 0.040246 | 210 | 0 | 0.950805 |

2.83% fee stress 下，9F5F 与 CE25 low-tail 的 strict residual-killer 分支为正，但低覆盖：

| policy | branch | pnl | roi | paired actions | residual | pair cost |
|---|---:|---:|---:|---:|---:|---:|
| 9F5F_BTC_LAST60_MIDPRICE_V1 | last60_up_35_50_same_row_pair_only | 14.418688 | 0.172811 | 23 | 0 | 0.839810 |
| 9F5F_BTC_LAST60_MIDPRICE_V1 | last60_up_50_65_same_row_pair_only | 16.778780 | 0.165710 | 30 | 0 | 0.845899 |
| 9F5F_BTC_LAST60_MIDPRICE_V1 | last60_up_50_65_same_row_pair_only | 18.230057 | 0.110303 | 48 | 0 | 0.888168 |
| 9F5F_BTC_LAST60_MIDPRICE_V1 | last60_down_50_65_same_row_pair_only | 13.928891 | 0.091444 | 44 | 0 | 0.903503 |
| CE25_BTC5M_LOW_PRICE_TAIL_DOWN_V1 | last60_down_20_35_same_row_pair_only | 14.185377 | 0.066333 | 65 | 0 | 0.926158 |

关键哈希：

- `AUTORESEARCH_MANIFEST.json` sha256 `3bbdd06b871e1bf3560eaee603befdf5826da6c767f82fbe1211dea133f1649d`
- `autoresearch_ledger.csv` sha256 `05864e4c14d76d8f3047325397ecf51f2c5e973824ba296f162e771991cbb494`
- `policy_fee_summary.csv` sha256 `efe340ee9a5d7094013d76db19806456107b87cb84dad546d0afcde4004c3136`
- `fee_stress_summary.csv` sha256 `b14d9cb79a319534b869a641553d4e8d41fb38527f75f50d5e77109470dacd95`
- `residual_stress_summary.csv` sha256 `f1dbc31583896ee26ec297eaf413c976396002b0911ebf25ec2cb5279c69a7ab`

### CE25 high-price capacity/depth iter1

路径：

`/Users/hot/web3Scientist/poly_backtest_data/derived/ce25_nagi_shadow_policy_autoresearch_v0/ce25_high_price_capacity_depth_iter1_20260604`

详细报告：

`/Users/hot/web3Scientist/poly_trans_research/docs/research/CE25_HIGH_PRICE_CAPACITY_DEPTH_AUTORESEARCH_20260604_ZH.md`

结果摘要：

- 43 variants x 5 fee rates = 215 results。
- `KEEP_LOCAL_REPLAY_CANDIDATE`: 150。
- `DISCARD_NEGATIVE_FEE_AFTER_PNL`: 65。
- 2.83% fee 和 3.0% fee 下均为 `KEEP_LOCAL_REPLAY_CANDIDATE=30/43`。
- `target_qty=13` 的 same-row / entry-paircap strict 分支在 3.0% fee 下仍为正、0 residual、210 到 215 paired actions。
- `paircap=0.965` 分支 ROI 更高、pair cost 更好，但覆盖压到 100 paired actions，刚好触及 full-keep floor。
- concentration pass 显示 `target_qty=13` 分支覆盖 203 paired markets，最大单市场 pair_qty share 约 1.4%，不是单一市场集中堆出来的。

3.0% fee stress 下的关键分支：

| branch | pnl | roi | paired actions | residual | pair cost | target_qty | pair cap |
|---|---:|---:|---:|---:|---:|---:|---:|
| same_row_cap_0.965 | 17.774517 | 0.070610 | 100 | 0 | 0.923318 | 3 | 0.965 |
| entry_paircap_cap_0.965 | 17.774517 | 0.070610 | 100 | 0 | 0.923318 | 3 | 0.965 |
| entry_paircap_target_qty_13 | 73.752535 | 0.041055 | 210 | 0 | 0.949482 | 13 | 0.980 |
| same_row_target_qty_13 | 74.226211 | 0.040535 | 215 | 0 | 0.949956 | 13 | 0.980 |

关键哈希：

- `AUTORESEARCH_MANIFEST.json` sha256 `670712f75f212edaac8535209f63f088dbf0e30c2797fffe0d2417a9fb99fcc9`
- `autoresearch_ledger.csv` sha256 `84d4fa8d95f92bed28e23d6003c6f749f9c26bb7e522b93ed5f3dffdebfb3ea8`
- `branch_control_summary.csv` sha256 `9613daf9bf79935914d2784970b833667e3daad68ed6d03a84b9c881ab3d8bec`
- `capacity_stress_summary.csv` sha256 `7b5cb83f6036a7171864fdffc37ca93d1538809df2361fae9542d1ad698e8ee8`
- concentration `autoresearch_ledger.csv` sha256 `4ee9ce8bddd270919433075e56e28acd61c5a1d17d50513aebabe7d10a9770ae`

### CE25 high-price L2/top-aligned validation

详细报告：

`/Users/hot/web3Scientist/poly_trans_research/docs/research/CE25_HIGH_PRICE_L2_TOP_ALIGNED_VALIDATION_20260604_ZH.md`

Pre-gate 状态：

`BLOCKED_L2_TOP_ALIGNED_ACTION_VALIDATION_GAPS`

首轮 L2 结论：

- 没有任何代表分支达到 100% clean L2 pass，不能进入 promotion/live/private-truth 路径。
- 但 L2 通过子集仍为正收益，说明严格 paircap 信号没有被 L2/top-depth 证据直接否定。
- 所有代表分支 top price match rate = 100%，top5 depth fillable rate = 100%；tail blocker 是 freshness / top5 VWAP cost / worst price quality。

Top1 qty gate follow-up:

`ce25_high_price_top1_qty_gate_iter2_20260604` produced the first CE25 high-price local L2-clean research candidates:

| branch | actions | markets | L2 pass | ROI | status |
|---|---:|---:|---:|---:|---|
| entry_paircap_top1_qty_cap_0.970_pxhi_0.80 | 142 | 137 | 142/142 | 5.4090% | KEEP |
| same_row_top1_qty_cap_0.970_pxhi_0.80 | 143 | 137 | 143/143 | 5.3811% | KEEP |
| entry_paircap_top1_qty_cap_0.970_pxhi_0.79 | 121 | 118 | 121/121 | 5.5942% | KEEP |
| same_row_top1_qty_cap_0.970_pxhi_0.79 | 122 | 118 | 122/122 | 5.5599% | KEEP |

This changes CE25 high-price from L2-blocked to local L2-clean research candidate. It still does not imply private truth, promotion, live readiness, deployability, or canary authorization.

Targeted fee stress on fixed top1-gate action sets also stayed positive. The preferred `entry_paircap_top1_qty_cap_0.970_pxhi_0.80` branch has ROI 6.6239% at 0% fee, 5.4771% at 2.83% fee, and 5.4090% at 3.0% fee.

Day stability also passed as local review evidence: 15 active days, 15/15 profitable days, 142 actions / 137 markets. Weakest day ROI was 2.2672%, strongest was 12.6770%.

Top1-gate capacity lane also passed L2 clean validation. `entry_paircap_top1_qty_target_qty_8_cap_0.970_pxhi_0.80` has 134 actions / 129 markets, L2 pass 134/134, PnL 41.923672, ROI 5.6140% at 3.0% fee.

Target_qty=8 support is now complete as local review evidence. Fixed-action fee stress stays positive at every tested fee: ROI 6.8337% at 0%, 5.8153% at 2.5%, 5.6823% at 2.83%, and 5.6140% at 3.0%. Day stability also remains clean: 15 active days, 15/15 profitable days; weakest day was 2026-05-06 with 9 actions / 9 markets, PnL 1.369499, ROI 2.3505%.

The target_qty=8 branch has also been frozen as a review-only normalized candidate ledger:

- output dir: `/Users/hot/web3Scientist/poly_trans_research/data/exports/ce25_high_price_top1_qty_target_qty8_candidate_ledger_20260604`
- status: `KEEP_CE25_TARGET_QTY8_NORMALIZED_CANDIDATE_LEDGER_REVIEW_REQUIRED_NOT_OOS_READY`
- candidate ledger CSV sha256 `8e8789de811081e23cac855ed1339d3546a6bd155e55af31be9aeba93c413c65`
- strategy input JSON sha256 `b8553cbcea1e2fe88cb72a8993aeee7c7884b8bef9d146e33398db920eb32594`
- hash manifest sha256 `078e029de53d747edc7b02fc570867fbc803f344d725ef2daa1bb36b099e6f9c`
- scope: 134 historical replay-bound candidates / 129 markets, not current/future OOS.
- limitations: 84/134 rows require top-overlay review; raw L2 age OK is 88/134, so next step is source-of-truth bridge rather than OOS/live.
- leg evidence: v2 writes 268 per-leg rows with YES/NO semantics and non-empty L1/L2 source row ids.

Source bridge is also complete:

- output dir: `/Users/hot/web3Scientist/poly_trans_research/data/exports/ce25_high_price_top1_qty_target_qty8_source_bridge_20260604`
- status: `KEEP_CE25_TARGET_QTY8_SOURCE_BRIDGE_VALIDATED_REVIEW_REQUIRED_NOT_OOS_READY`
- hash manifest sha256 `a6f90926ede0455806e70a105e940a9db08317159b3d9b70988c7e12703b03fe`
- row audit CSV sha256 `ddfc98abc451f97309d7ff4f1bdef3a4b9b2be91f95b915a0be63153dfa5471d`
- summary JSON sha256 `f841b7948b7b379198bbb06eab139fe13bcd14df8b13d4f2cd8eb96ff3c1048c`
- bridge result: 134/134 row audit PASS, 134 candidate_base rows loaded, 268 L2 mart rows loaded.

Overlay/freshness attribution is complete and is the current evidence-policy blocker:

- output dir: `/Users/hot/web3Scientist/poly_trans_research/data/exports/ce25_high_price_top1_qty_target_qty8_overlay_freshness_attribution_20260604`
- status: `KEEP_CE25_TARGET_QTY8_OVERLAY_FRESHNESS_ATTRIBUTION_REVIEW_REQUIRED_NOT_OOS_READY`
- hash manifest sha256 `cef55d13888387aad1a6875a7bdbf2f7f31ff9d6c553fb45d9838489dd44736b`
- category counts: NO_OVERLAY_RAW_L2_OK=5, OVERLAY_ONLY=83, RAW_L2_STALE_ONLY=45, OVERLAY_AND_RAW_L2_STALE=1.
- interpretation: no-overlay + raw-L2-fresh strictness leaves only 5 actions, so the next decision is whether canonical L1 top evidence is acceptable for this local research layer. It is not enough for OOS/live/private truth.

Evidence-policy decision packet is now prepared:

- output dir: `/Users/hot/web3Scientist/poly_trans_research/data/exports/ce25_high_price_top1_qty_target_qty8_evidence_policy_packet_20260604`
- status: `KEEP_CE25_TARGET_QTY8_CANONICAL_L1_EVIDENCE_POLICY_ACCEPTED_REVIEW_ONLY_NOT_OOS_READY`
- decision: `ACCEPT_CANONICAL_L1_TOP1_DEPTH_FOR_LOCAL_REVIEW_ONLY`
- hash manifest sha256 `c765d86b017a45a094ca8b89f1bc52ba363218b3edbafcb7e89a09215aaf4cc2`
- review-only strategy packet sha256 `a680db3b47acd5735aa2bd1ebc5662611087afcfb191817ad47d229234c478e2`
- command preview exits 66; no exact approval is issued.

代表结果：

| branch | actions | markets | L2 pass actions | L2 pass rate | L2 pass PnL | L2 pass ROI | role |
|---|---:|---:|---:|---:|---:|---:|---|
| entry_paircap_cap_0.970 | 151 | 146 | 144 | 95.3642% | 19.511370 | 5.3873% | mainline |
| same_row_cap_0.965 | 100 | 96 | 95 | 95.0000% | 17.037011 | 7.1409% | conservative control |
| entry_paircap_target_qty_8 | 210 | 203 | 189 | 90.0000% | 46.176679 | 4.2887% | capacity study |
| entry_paircap_target_qty_13 | 210 | 203 | 186 | 88.5714% | 64.648503 | 4.2733% | capacity boundary |

Updated interpretation:

- 当前主线收敛到 `entry_paircap_cap_0.970`。
- `cap_0.965` 是保守对照，不是唯一主分支，因为覆盖刚过 floor。
- `target_qty=8/13` 暂时只能作为容量边界研究，不适合 clean path。

## 策略排序更新

当前不应按原文直接排序为 9F5F > CE25 > Username123123。工程排序应调整为：

1. `P0_IMPLEMENTATION_TEMPLATE`: strict residual-killer same-row / entry-paircap execution template。
2. `P0_LOCAL_L2_CLEAN_CAPACITY_MAINLINE`: CE25 high-price `entry_paircap_top1_qty_target_qty_8_cap_0.970_pxhi_0.80`；134 actions / 129 markets，L2 pass 134/134，3% fee ROI 5.6140%。
3. `P0_LOW_SIZE_BASELINE`: CE25 high-price `entry_paircap_top1_qty_cap_0.970_pxhi_0.80`；142 actions / 137 markets，L2 pass 142/142，3% fee ROI 5.4090%。
4. `P0_CONSERVATIVE_CONTROL`: CE25 high-price `entry_paircap_top1_qty_cap_0.970_pxhi_0.79`；121 actions / 118 markets，ROI 5.5942%。
5. `P1_CAPACITY_BOUNDARY`: old pre-gate `target_qty=8/13`；容量潜力存在，但 pre-gate L2 pass rate 只有 90.00% / 88.57%，不得替代 top1-gate capacity branch。
5. `P2_MICRO_ALPHA_WATCH`: 9F5F last60 UP 35-50 / UP 50-65；CE25 low-price tail DOWN。它们正收益、0 residual，但低覆盖，不可直接放大。
6. `PARKED`: Username123123；除非出现新的独立正窗口或用户明确重排优先级，否则不做近期 profile refresh。

## 下一步

### P0: residual-killer runner v1

需要把当前 book-shadow 实验升级为更接近执行的 local runner：

- 已完成精确 variant scan cache，避免同一 variant 的多 fee stress 重复扫 DuckDB；canonical cached iter3 中 cache hit 为 226/282。
- 已完成 `max_pair_delay_ms` buckets：0ms、250ms、500ms、1000ms、3000ms；结论是 delay bucket 会制造高残腿负收益，不是推进方向。
- 已完成 capacity/depth stress：`target_qty=1,2,3,5,8,13`、`fill_haircut=25/50/75/100%`、strict paircap `0.965/0.970/0.975/0.980`。
- 已新增 `--book-shadow-summary-only`，用于后续大矩阵默认跳过 per-action/per-residual 明细写盘。
- 已新增 paired market count / max-market concentration metrics，用于识别低覆盖或单市场集中风险。
- 下一步再做 policy/price-band 分组缓存或并行执行；当前没有做跨 pair-cap superset 缓存，以避免 `source_row_count` / `active_markets` 口径漂移。
- 输出 per-market participation floor，防止 23 paired actions 这种低覆盖分支被误读为完整策略。
- 已完成 top1 qty gate L2-clean pass；下一步不是继续扩账户，而是把 `entry_paircap_top1_qty_cap_0.970_pxhi_0.80` 固化为 CE25 local research candidate。
- Top1-gate target_qty 5/8 capacity lane has passed L2 clean validation; target_qty=8 fee/day support, normalized candidate ledger, per-leg row-id evidence, source bridge, overlay/freshness attribution, and evidence-policy decision packet are complete. Next local step is historical replay-bound strategy review; do not directly jump to OOS/live/canary.

### P2: 9F5F 多窗口验证，暂停执行

9F5F 公开 24h profile 不能单独作为实施依据。理论上如果要继续研究，需要补：

- 至少 2 个非连续窗口 profile。
- 最近 72h profile。
- 与本地 May 2-18 replay 的 bucket 对齐报告。

但当前不建议立刻执行 profile refresh。原因：

- 本地 cached iter3 已把它降为 `KEEP_WATCH_LOW_COVERAGE`，2.83% fee 后为正但 paired actions 只有 23 到 48。
- 2026-05-19 至 2026-05-22 的 72h public PnL 里，9F5F 全账户 pair cost 为 1.053474、lifetime residual rate 为 30.2898%，说明全账户不可复制。
- 当前更高价值工作是 CE25 high-price strict paircap 的 200+ paired-action full-keep 分支，以及 residual-killer runner v1。

已生成 public-only refresh dry-run plan：

`/Users/hot/web3Scientist/poly_trans_research/data/exports/top3_profile_refresh_plan_9f5f_username123123_20260604_bjt/command_log.json`

计划窗口：

- 2026-06-01T09:00:00Z 至 2026-06-02T09:00:00Z。
- 2026-06-02T09:00:00Z 至 2026-06-03T09:00:00Z。
- 2026-06-03T09:00:00Z 至 2026-06-04T09:00:00Z。

账户：

- 9F5F `0x9f5ffe76a818dce37c70f947998b52b70671a008`。
- Username123123 `0xd950a1a89f3e61a7a9efc85a46e440ce58c15e86`。

该 plan 仅作为记录，不应默认执行。

### PARKED: Username123123

不建议继续做 refresh，除非用户明确重排优先级。已有材料：

- 2026-05-27 12:25-12:45 BJT：13 markets，buy_actual 13,708.15，cash_plus_current_plus_rebate 1,133.01，resid_rate 1.9156%。
- 2026-05-27 15:30-15:50 BJT：14 markets，buy_actual 19,056.81，cash_plus_current_plus_rebate 5,360.54，resid_rate 2.6426%。
- 2026-05-27 12:00-13:30 BJT：48 markets，buy_actual 83,313.24，cash_plus_current_plus_rebate -6,992.18，cash_pnl -7,197.27，resolved_roi -0.9075%，actual_pair_cost 0.985643。

结论：它是短 burst pocket，不是当前主线策略候选。继续 refresh 的机会成本高于预期收益。

### P1: evidence schema

所有后续策略都需要输出：

- `strategy_input.json`
- `autoresearch_ledger.csv`
- `run_manifest.json`
- `branch_control_summary.csv`
- `fee_stress_summary.csv`
- `residual_stress_summary.csv`
- `next_generation_candidates.json`

并且最高只能是 local research candidate，不得声明 private truth、promotion、live、deployable。
