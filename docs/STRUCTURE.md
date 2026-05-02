# Repository Structure

当前主线同时支持公开侧采集和可选的我方 user truth 采集。

当前阶段建议：

- 回测/研究：`public-only`
- 实盘/执行真值验证：`public + user truth`

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
- `md_book_l2`
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
- `md_book_l1` 保留 YES/NO 四价四量
- `md_book_l2` 从 `market_ws/book` 的 `raw_l2` / snapshot / price_change raw 重建每个 YES/NO 资产 top5 bid/ask depth
- `md_trades` 必须保持 `trade_ts_ms / taker_side / market_side / price / size`，用于 size-aware 回测

`md_book_l2` 是高基数 compact 表，默认不复制 `raw_json`，但仍按 top5 depth 实际变化写入，不做时间降采样。需要重新解析原始 payload 时，应回到 `data/raw`，而不是依赖 L2 表承载取证内容。

`md_book_l2` 只能支持 depth-aware / maker-fill-proxy 分析，不能证明 queue priority 或真实成交。真实成交校准需要 `own_order_events` 与 `own_fill_events`。

## 3. Xuan Public Truth

复刻 xuan 必须采集或补拉：

- `xuan_poll/xuan_trades`
- `xuan_poll/xuan_activity`
- `xuan_poll/xuan_poll_log`

没有这些表时，结论只能是“市场侧策略形态相似”，不能说“复刻 xuan”。

## 4. User Truth Pipeline

开启 `CF_USER_WS_ENABLED=true` 后：

1. user WS 认证连接
2. `user_order` 与 `user_trade` 显式分流
3. `positions bootstrap` 写 `own_inventory_events(source_kind=bootstrap)`
4. fill 增量推导库存，写 `source_kind=derived_fill`
5. 周期 reconcile，写 `source_kind=reconcile`
6. `user_ws_log` 记录 `auth_success` 与 `inventory_truth_degraded`

## 5. Config Layering

`capture-sidecar-env` 按这个顺序加载配置：

1. `.env`
2. `config/.env`
3. `--env-file` 指向的文件

因此推荐：

- `config/research.env`：非敏感运行参数
- `config/.env`：敏感密钥

公开侧常见两种配置：

- `BTC-only`：`CF_MARKET_PREFIXES=btc-updown-5m` + `CF_MAX_MARKETS_PER_PREFIX=2`
- `all active crypto 5m`：`CF_MARKET_PREFIXES=*` + `CF_MAX_MARKETS_PER_PREFIX=2`

这里的 `2` 表示滚动跟踪“当前轮 + 下一轮”。不要在长期运行里使用 `CF_MARKET_PREFIXES=*` 配 `CF_MAX_MARKETS_PER_PREFIX=0`，那会把 future rounds 一次性全部订到单个 market WS。

## 6. Ops Docs

- `README.md`
- `docs/RUNBOOK.md`
- `docs/PLAN_Codex_New.md`
