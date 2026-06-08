# CE25 BTC5M Low-Tail Side-Split V2 Handoff

Status: `KEEP_CE25_LOW_TAIL_SIDE_SPLIT_V2_WATCH_L2_VALIDATION_NEXT_NOT_OOS_READY`

## 结论

`CE25_BTC5M_LOW_PRICE_TAIL_DOWN_V1` 应升级为 `CE25_BTC5M_LOW_PRICE_TAIL_SIDE_SPLIT_V2`，但只能升级为 watch / L2 validation candidate，不能升级为 OOS/live。

原因：最新 9 个 public profile 窗口显示 `BTC 5m / last_60s / first_price 20-35` 在 DOWN 与 UP 两侧都为正；本轮修正 runner 后，UP strict residual-killer 也在本地 2026-05-02 至 2026-05-18 book-shadow 里过了 fee stress。旧工具链把 UP 标为 control，导致之前没有公平扫描 UP strict，这是一个真实工具偏差。

但它不是 full-keep 主策略：strict 分支残差为 0、ROI 很高，但 paired markets 只有 29 到 48 个，属于高质量低覆盖 micro-alpha。不能用宽松 longer SLA 版本放大，因为它虽然 PnL 更高，但分类是 `KEEP_WATCH_RESIDUAL_HIGH`。

## Public Profile 证据

- source window: 2026-05-28 11:45 BJT 到 2026-06-06 11:10 BJT，9 个 24h-ish profile。
- bucket: BTC 5m / last_60s / first_price 20-35。
- markets: 75。
- buy_actual: $31,175.11。
- cash_pnl: $2,941.86，ROI 9.44%。
- weighted pair_cost: 0.8667。
- resid_rate: 11.24%。
- bad_pc>=1 share: 18.47%。
- profitable windows: 7/9。

| side | markets | buy_actual | cash_pnl | ROI | pair_cost | resid_rate | bad_pc>=1 | pc<0.98 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| DOWN | 48 | $20,947.62 | $1,860.31 | 8.88% | 0.8768 | 10.37% | 23.68% | 74.17% |
| UP | 27 | $10,227.49 | $1,081.55 | 10.57% | 0.8464 | 12.94% | 7.79% | 85.94% |

Interpretation: public profile 不支持继续把它理解成纯 DOWN-only。DOWN 规模更大，UP ROI 与 pair_cost 更好。两边都应进入 side-split 验证。

## 本地 Book-Shadow 证据

- run: `/Users/hot/web3Scientist/poly_backtest_data/derived/ce25_nagi_shadow_policy_autoresearch_v0/ce25_low_tail_side_split_v2_iter0_20260606`
- source data: local completion candidate base / book-shadow，2026-05-02 至 2026-05-18。
- variants: 154，results: 308。
- fee stress: 2.83% 与 3.0%。下表使用 3.0%。

| side | strict branch | pnl | ROI | pairs | markets | pair_cost | residual | class |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| DOWN | entry_paircap_cap_0.965 | $12.99 | 10.54% | 39 | 39 | 0.8923 | 0.00% | KEEP_WATCH_LOW_COVERAGE |
| UP | same_row_cap_0.965 | $12.22 | 13.22% | 30 | 29 | 0.8706 | 0.00% | KEEP_WATCH_LOW_COVERAGE |

Capacity stress 仍是低覆盖，但 target_qty=8 没有破坏质量：

| side | target_qty=8 branch | pnl | ROI | pairs | markets | pair_cost | residual |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| DOWN | same_row_target_qty_8 | $20.00 | 8.59% | 50 | 48 | 0.9086 | 0.00% |
| UP | same_row_target_qty_8 | $18.40 | 9.24% | 41 | 40 | 0.9030 | 0.00% |

不要被 longer SLA 的绝对 PnL 误导：

| side | longer SLA pnl | ROI | pairs | residual | class |
| --- | ---: | ---: | ---: | ---: | --- |
| DOWN | $71.62 | 2.27% | 613 | 49.46% | KEEP_WATCH_RESIDUAL_HIGH |
| UP | $80.60 | 2.51% | 629 | 49.90% | KEEP_WATCH_RESIDUAL_HIGH |

Interpretation: 核心不是“低价就买”，而是“低价尾部 + 对手腿已经能在同一行或短 SLA 内以 paircap 完成”。宽松追 completion 会把 residual 风险重新打开。

## 可复现秘籍

1. 时间：只看 BTC 5m 最后 60 秒，对应 fixed clock / public book，不允许直接使用 CE25 的 `source_last_delta_bucket`。
2. 价格：第一腿 executable price 在 0.20 到 0.35；0.10 到 0.20 邻居是负控，不是主线。
3. 配对：入口必须要求 projected pair_cost <= 0.965/0.970，并且 opposite depth 可覆盖；same-row 优先。
4. 方向：V2 应同时允许 DOWN 与 UP，但分别记账、分别限额；不要把两侧合并成无差别仓位。
5. 风控：strict residual-killer 才是主线；longer SLA 只能研究，不能作为默认。
6. 容量：target_qty=5 是基线，target_qty=8 可验证；target_qty=13 虽仍正，但必须等 L2 depth/capacity 通过后再讨论。

## 下一步

P0：对 `last60_up/down_20_35_side_split_same_row_cap_0.965` 与 `entry_paircap_cap_0.965` 做 L2 top-aligned validation。

P1：对 target_qty=8 做同样 L2 验证，要求 top1/opposite depth 覆盖，不能只看 book-shadow。

P2：把 V2 policy 写入正式 strategy input，但保持 `orders_authorized=false`、`live_ready=false`。

## 边界

本报告只使用 public profile 与本地 public/replay book-shadow。它不证明 CE25 私有 maker/taker、真实成交、排队优先级、可部署性或 live 预期收益。
