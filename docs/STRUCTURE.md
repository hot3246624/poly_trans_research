# Repository Structure

当前主线同时支持公开侧采集和可选的我方 user truth 采集。

## 1. Raw Capture

原始日分区路径：`data/raw/YYYY-MM-DD/<source>/<channel>.jsonl.gz`

主要 source/channel：

- `market_ws/book`
- `market_ws/last_trade_price`
- `meta/market_meta`
- `settlement/market_resolved`
- `user_ws/user_order`
- `user_ws/user_trade`
- `user_ws/user_ws_log`
- `inventory/inventory_snapshot`
- `xuan_poll/xuan_trades`
- `xuan_poll/xuan_activity`
- `xuan_poll/xuan_poll_log`

## 2. Replay Builder

输出：`data/replay/YYYY-MM-DD/crypto_5m.sqlite`

核心表：

- `market_meta`
- `md_book_l1`
- `md_trades`
- `own_order_events`
- `own_fill_events`
- `own_inventory_events`
- `user_ws_log`
- `xuan_trades`
- `xuan_activity`
- `xuan_poll_log`
- `settlement_records`

构建口径：

- 同一 UTC 日按“重建”覆盖写入，保证滚动构建幂等
- user 分流只认显式 channel，不再依赖 `source.startswith("user")`

## 3. User Truth Pipeline

开启 `CF_USER_WS_ENABLED=true` 后：

1. user WS 认证连接
2. `user_order` 与 `user_trade` 显式分流
3. `positions bootstrap` 写 `own_inventory_events(source_kind=bootstrap)`
4. fill 增量推导库存，写 `source_kind=derived_fill`
5. 周期 reconcile，写 `source_kind=reconcile`
6. `user_ws_log` 记录 `auth_success` 与 `inventory_truth_degraded`

## 4. Config Layering

`capture-sidecar-env` 按这个顺序加载配置：

1. `.env`
2. `config/.env`
3. `--env-file` 指向的文件

因此推荐：

- `config/research.env`：非敏感运行参数
- `config/.env`：敏感密钥

## 5. Ops Docs

- `README.md`
- `docs/RUNBOOK.md`
- `docs/PLAN_Codex_New.md`
