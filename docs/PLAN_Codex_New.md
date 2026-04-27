# `poly_trans_research` 真值采集升级实施清单

## 摘要

目标是把 `poly_trans_research` 升级为唯一主采集器：同一程序同时采 `market/meta/xuan` 公开侧数据，以及我方 `订单 + 成交 + 库存` 真值。`pm_as_ofi` 不再承担主采集职责；只有当后续确实需要 `pair_tranche / budget / capital / intent` 语义时，才做薄桥接补位。

实现必须按官方能力边界设计：

- `xuan` 的挂单/撤单/排队位置拿不到，不纳入目标。
- 有密钥后新增的是“我方私有真值”，不是“对手私有真值”。
- `user_ws` 必须按官方 authenticated user channel 协议实现。
- `inventory` 必须靠 `positions bootstrap + fill 增量 + periodic reconcile` 建立，不靠猜测。

## 实施顺序

### 1. 先把 auth 与 user WS 接通

- 在 `capture-sidecar-env` 中新增 authenticated 模式，默认关闭，仅当 `CF_USER_WS_ENABLED=true` 时启用。
- 新增并固定以下 env：
  - `CF_USER_WS_ENABLED`
  - `CF_L1_PRIVATE_KEY`
  - `CF_API_KEY`
  - `CF_API_SECRET`
  - `CF_API_PASSPHRASE`
  - `POLYMARKET_FUNDER_ADDRESS`
- 认证优先级固定为：
  - 若 `CF_API_KEY/SECRET/PASSPHRASE` 完整存在，直接使用
  - 否则若 `CF_L1_PRIVATE_KEY` 存在，则调用 CLOB auth create/derive API 本地生成或恢复 API creds
  - 两者都没有则 user path 直接禁用，但 market/meta/xuan 主路径继续运行
- `user_ws` 连接协议固定为：
  - 连接 `wss://ws-subscriptions-clob.polymarket.com/ws/user`
  - 首包发送 `{"auth":{"apiKey","secret","passphrase"},"type":"user"}`
  - 成功后发送 `{"operation":"subscribe","markets":[condition_id...]}`，markets 使用当前 active selection 的 condition IDs
  - 每 `10s` 发送 heartbeat
  - active round 切换时只更新 markets subscribe，不重建第二套逻辑
- 当前 `cli.py` 里基于 `user address + channel=order/trade` 的订阅逻辑全部替换，不保留双路径。

完成定义：
- 用真实 creds 能稳定连上 user channel
- market sidecar 和 user sidecar 可并发运行
- 断线后自动重连，重连后重新 auth + resubscribe

### 2. 固化 raw envelope 与 replay 分流

- raw 层保留统一 envelope，但 user 事件必须分成显式 channel：
  - `user_order`
  - `user_trade`
  - `inventory_snapshot`
- `market` 侧保持现有标准化输出：
  - `book` 继续要求完整四价四量
  - `trade` 继续要求 `trade_ts_ms / taker_side / maker_address / taker_address`
- `user_order` 只承载 order lifecycle 类消息：
  - placement / live / update / partial_fill / canceled / rejected / merge / redeem
- `user_trade` 只承载 trade match 类消息：
  - `trade_id / taker_order_id / trader_side / maker_orders / price / size / fee_rate_bps / matchtime / tx_hash`
- replay schema 固定新增：
  - `own_fill_events`
- replay schema 保留并继续使用：
  - `own_order_events`
  - `own_inventory_events`

`own_fill_events` 字段固定为：
- `condition_id`
- `asset_id`
- `order_id`
- `taker_order_id`
- `trade_id`
- `market_side`
- `direction`
- `trader_side`
- `price`
- `size`
- `fee_rate_bps`
- `match_ts_ms`
- `recv_ms`
- `recv_monotonic_ns`
- `capture_seq`
- `maker_address`
- `tx_hash`
- `raw_json`

`own_order_events` 保持生命周期口径，不再混入 fill rows。

完成定义：
- build replay 后，`own_order_events` 与 `own_fill_events` 均可独立查询
- 同一笔真实成交能同时对应到 order lifecycle 与 fill truth
- 不再依赖 `source.startswith("user")` 的模糊分流

### 3. 建立库存真值链路

- 启动时调用 Data API positions 做 bootstrap。
- bootstrap 输出写入 `own_inventory_events`，`source_kind=bootstrap`。
- 收到 `own_fill_events` 后，内存库存做增量更新，并写 `own_inventory_events`，`source_kind=derived_fill`。
- 新增 periodic reconcile：
  - 默认每 `60s` 对 touched markets 重新调用 positions
  - fill burst 后允许提前 reconcile
  - reconcile 结果写 `own_inventory_events`，`source_kind=reconcile`
- `own_inventory_events` 字段固定为：
  - `condition_id`
  - `asset_id`
  - `outcome`
  - `size`
  - `avg_price`
  - `redeemable`
  - `mergeable`
  - `source_kind`
  - `recv_ms`
  - `recv_monotonic_ns`
  - `capture_seq`
- v1 不要求 `usdc_available / capital_state / tranche_state` 进入主 schema。
- drift 裁决固定为：
  - 若 reconcile 与 derived inventory 在 touched markets 上偏差 `<= 1e-6 shares`，视为一致
  - 超过阈值则记 `inventory_truth_degraded`
  - 不中断采集，只在 audit/report 中标红

完成定义：
- 冷启动时能拿到非空 positions snapshot
- 下单成交后，inventory 能先由 fill 推进，再被 reconcile 校正
- replay 中能看到 bootstrap -> derived_fill -> reconcile 的完整链条

### 4. 增加 gap recovery 与 backfill

- user WS 重连成功后，固定执行一次 recovery：
  - 拉取近期 user orders
  - 拉取用户公开 trades/activity
  - 拉取当前 positions
- recovery 的目的不是补“全历史”，而是补断线窗口里的最近状态。
- 去重规则固定为：
  - order 用 `(order_id, event_type, status, timestamp-ish)` 去重
  - fill 用 `trade_id` 主去重，缺失时退化到 `(condition_id, asset_id, match_ts_ms, price, size)`
  - inventory snapshot 不去重到逐条唯一，只保留时间序列
- 不引入第二套 backfill source；所有 backfill 仍走 `poly_trans_research` 自己的 raw store 与 replay builder。

完成定义：
- 模拟断线后，重连能恢复后续 user truth
- recent 窗口内不出现明显缺口或重复洪泛

### 5. 升级 audit、validator 与 runbook

- `startup_audit` 新增真值侧门槛：
  - `user_ws auth success = true`
  - `own_order_events > 0`
  - `own_fill_events > 0`
  - `positions bootstrap rows > 0`
  - `inventory drift within epsilon`
- 公开侧原门槛保留：
  - `md_book_l1` 四个 size 空值为 `0`
  - `md_trades.trade_ts_ms` 空值为 `0`
  - `md_trades.taker_side` 空值率 `<= 5%`
- runbook 固定新增两套启动方式：
  - `public-only`
  - `public + user truth`
- README 必须明确写出一句：
  - 启用密钥后增加的是“我方执行真值”，不是“对手挂单真值”

完成定义：
- 1h startup audit 可以同时裁决公开侧和真值侧
- 文档足够让另一个工程师按 env 启动 authenticated capture

### 6. 暂不做的内容

- 不在 v1 主线中加入 `pair_tranche_events / pair_budget_events / capital_state_events`
- 不把 `pm_as_ofi` 作为 market capture fallback
- 不尝试恢复 `xuan` 的挂单、撤单、queue position
- 不要求 v1 就支持多策略意图级 explain

只有当后续研究证明以下问题无法通过 `订单 + 成交 + 库存` 回答时，才开 bridge 子计划：
- repair / cooldown / merge / redeem 的策略内部语义
- 资金压力与 tranche 级别库存管理

## 关键接口与文件

优先修改的主文件固定为：

- `src/completion_first_data/cli.py`
- `src/completion_first_data/capture/websocket_sidecar.py`
- `src/completion_first_data/replay/schema.py`

配套必须同步：

- `src/completion_first_data/replay/normalize.py`
- `src/completion_first_data/replay/builder.py`
- `src/completion_first_data/quality/startup_audit.py`
- `README.md`
- `docs/RUNBOOK.md`

## 测试与验收

### 单测

- user auth payload 生成正确
- user subscribe update 在 round rollover 时正确刷新 markets
- user order / user trade 能正确分流
- `own_fill_events` 能正确解析 `trader_side / matchtime / maker_orders`
- positions bootstrap 与 reconcile 能生成标准 inventory snapshot

### 集成测试

- `BTC 5m` 单市场，public-only 跑 `1h`，公开侧 audit 通过
- `BTC 5m` 单市场，public + user truth 跑 `1h`，公开侧与真值侧 audit 同时通过
- 放一个 microlot 测试单后，同日 replay 中必须看到：
  - `own_order_events`
  - `own_fill_events`
  - `own_inventory_events`

### 完成门槛

- 单库足以支持：
  - `xuan` 公开侧 episode 研究
  - 我方 fill calibration
  - 我方 inventory drift 检查
- 不再需要手工拼接 `pm_as_ofi` recorder 才能研究 `30s completion`
- 若 bridge 仍未实现，不影响 v1 上线使用

## 假设与默认值

- 默认只跑 `BTC 5m`
- 当前推荐先跑 `public-only`，用于回测/研究阶段
- `public + user truth` 留给后续实盘/执行真值验证阶段
- 研究主线优先使用 `public + xuan + own truth`，不是全市场平台化
- API creds 可由现有 key 恢复或现地生成；不得写入 repo-tracked 文件
- `positions` 作为 canonical inventory source，`fills` 作为高频增量 source
- `pm_as_ofi` 只在后续需要策略内部语义时补位，不进入本次实施范围
