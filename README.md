# poly_trans_research

本仓库现在支持两种标准模式：

- `public-only`：公开市场侧 `market + meta + settlement + xuan(optional)`
- `public + user truth`：在公开侧基础上，新增我方 `order + fill + inventory` 真值

启用密钥后增加的是“我方执行真值”，不是“对手挂单真值”。

## 当前阶段建议

当前如果目标是“先采集数据做回测”，推荐直接跑：

- `public-only`
- 先从 `BTC 5m` 开始

`public + user truth` 更适合后续实盘/执行真值验证阶段，因为它解决的是“我自己的挂单、成交、库存”问题，不是公开行情采样本身。

如果目标升级为“复刻 xuan”，必须额外启用或补拉 `xuan public truth`：

- `xuan_trades`
- `xuan_activity`
- `xuan_poll_log`

否则只能做市场侧相似性研究，不能证明 xuan first-leg、completion、merge/redeem、残仓路径。

## 安全

- 真实密钥不要写入 repo-tracked 文件。
- 推荐把敏感项放在 `config/.env` 或项目根目录 `.env`。
- `capture-sidecar-env` 会按顺序加载：`.env` -> `config/.env` -> `--env-file`。
- `.env` / `*.env` / `config/*.env` 已被 `.gitignore` 忽略。

## 目录

```text
.
├── cfdata.py
├── config/
│   ├── capture.sources.example.json
│   ├── research.env
│   └── research.env.example
├── data/
│   ├── raw/
│   ├── replay/
│   ├── replay_published/
│   ├── backtest_cache/
│   └── verification_store/
├── docs/
│   ├── PLAN_Codex.md
│   ├── PLAN_Codex_New.md
│   ├── RUNBOOK.md
│   └── STRUCTURE.md
├── legacy/tools/
│   ├── analyze_trade.py
│   ├── trade_analysis.py
│   └── output_paths.py
├── outputs/
│   └── trade_analysis/
└── src/completion_first_data/
```

## 安装

```bash
pip install -r requirements.txt
```

## 配置

`config/research.env` 放运行参数；敏感项建议放 `config/.env`。

### Public-only 默认值

```env
CF_MARKET_PREFIXES=btc-updown-5m
CF_MARKET_CHANNELS=book,last_trade_price
CF_DISABLE_USER_WS=true
CF_USER_WS_ENABLED=false
CF_META_ACTIVE_ONLY=true
CF_MAX_MARKETS_PER_PREFIX=2
CF_META_INTERVAL_SEC=20
CF_META_SWITCH_DELAY_SEC=8
CF_SETTLEMENT_POLL_ENABLED=true
CF_SETTLEMENT_POLL_SEC=20
CF_SETTLEMENT_POLL_COOLDOWN_SEC=30
CF_RAW_ROOT=data/raw
CF_REPLAY_ROOT=data/replay
```

这也是当前“回测/研究阶段”的推荐默认值。`2` 表示同一 symbol 同时跟踪“当前轮 + 下一轮”，避免 round rollover 时漏掉前几秒。

如果要同时采所有 active crypto `5m` 市场：

```env
CF_MARKET_PREFIXES=*
CF_MAX_MARKETS_PER_PREFIX=2
```

不要用 `CF_MAX_MARKETS_PER_PREFIX=0` 做长期运行。对于 `*`，那会把大量 future rounds 一次性订到同一个 market WS，连接和 settlement 轮询都会被拖垮。

### Public + User Truth 额外项

在 `config/research.env`：

```env
CF_DISABLE_USER_WS=false
CF_USER_WS_ENABLED=true
CF_USER_RECONCILE_SEC=60
CF_USER_RECOVERY_LOOKBACK_SEC=300
```

在 `config/.env` 或 `.env`：

```env
POLYMARKET_FUNDER_ADDRESS=0x...
CF_L1_PRIVATE_KEY=...
CF_API_KEY=...
CF_API_SECRET=...
CF_API_PASSPHRASE=...
```

兼容旧参数名：

- `POLYMARKET_PRIVATE_KEY` -> `CF_L1_PRIVATE_KEY`
- `POLYMARKET_BUILDER_API_KEY` -> `CF_API_KEY`
- `POLYMARKET_BUILDER_SECRET` -> `CF_API_SECRET`
- `POLYMARKET_BUILDER_PASSPHRASE` -> `CF_API_PASSPHRASE`
- `ETHEREUM_ADDRESS` 可作为 `POLYMARKET_FUNDER_ADDRESS` 的回退

认证优先级：

1. `CF_API_KEY / CF_API_SECRET / CF_API_PASSPHRASE`
2. `CF_L1_PRIVATE_KEY` 本地派生 API creds（优先走 CLOB V2 SDK）
3. 两者都缺失则自动退回 `public-only`

## CLOB V2 注意事项

- `2026-04-17` 的 CLOB V2 迁移对当前 `public-only` 采集主链路不是立刻阻断项。
- 但 `public + user truth` 以及未来任何 execution / relayer 提交逻辑，都应基于 `py-clob-client-v2`。
- 当前仓库的 user truth helper 已优先适配 V2 SDK；若运行环境里仍残留旧版 `py_clob_client`，仅作为临时兼容回退。
- `2026-04-21` 之后 relayer `POST /submit` 返回的是 `transactionID`，不是最终 `transactionHash`。未来做 execution 时必须补一跳 `GET /transaction by id`。

## 采集

### public-only

```bash
python cfdata.py capture-sidecar-env --env-file config/research.env
```

### public + user truth

```bash
python cfdata.py capture-sidecar-env --env-file config/research.env
```

同一命令即可；是否启用 user truth 由 `CF_USER_WS_ENABLED` 和密钥是否齐备决定。

## 启动配方

### BTC-only，回测阶段推荐

```bash
cat > config/research.btc.public.env <<'ENV'
CF_MARKET_PREFIXES=btc-updown-5m
CF_MARKET_CHANNELS=book,last_trade_price
CF_DISABLE_USER_WS=true
CF_USER_WS_ENABLED=false
CF_META_ACTIVE_ONLY=true
CF_MAX_MARKETS_PER_PREFIX=2
CF_META_INTERVAL_SEC=20
CF_META_SWITCH_DELAY_SEC=8
CF_SETTLEMENT_POLL_ENABLED=true
CF_SETTLEMENT_POLL_SEC=20
CF_SETTLEMENT_POLL_COOLDOWN_SEC=30
CF_XUAN_POLL_ENABLED=false
CF_RAW_ROOT=data/raw
CF_REPLAY_ROOT=data/replay
ENV

cd /Users/hot/web3Scientist/poly_trans_research
uv run python cfdata.py --log-level INFO capture-sidecar-env --env-file config/research.btc.public.env
```

### 全部 active crypto 5m，滚动全市场版

```bash
cat > config/research.all.public.env <<'ENV'
CF_MARKET_PREFIXES=*
CF_MARKET_CHANNELS=book,last_trade_price
CF_DISABLE_USER_WS=true
CF_USER_WS_ENABLED=false
CF_META_ACTIVE_ONLY=true
CF_MAX_MARKETS_PER_PREFIX=2
CF_META_INTERVAL_SEC=20
CF_META_SWITCH_DELAY_SEC=8
CF_SETTLEMENT_POLL_ENABLED=true
CF_SETTLEMENT_POLL_SEC=20
CF_SETTLEMENT_POLL_COOLDOWN_SEC=30
CF_XUAN_POLL_ENABLED=false
CF_RAW_ROOT=data/raw
CF_REPLAY_ROOT=data/replay
ENV

cd /Users/hot/web3Scientist/poly_trans_research
uv run python cfdata.py --log-level INFO capture-sidecar-env --env-file config/research.all.public.env
```

区别：

- `BTC-only`：只跟踪 BTC 的当前轮和下一轮，流量最低
- `all active crypto 5m`：按 symbol 滚动跟踪“当前轮 + 下一轮”，适合长期全市场采集

当前 sidecar 行为：

- market WS 使用官方 `type=market` 订阅 schema
- user WS 使用官方 authenticated user channel
- user WS 首包发送 auth，随后按 active selection 发送 `markets` subscribe update
- 每 10s 发送 heartbeat
- round rollover 时刷新 user subscribe，不额外起第二套逻辑
- inventory truth 采用 `positions bootstrap + fill derived + periodic reconcile`
- reconnect 后执行 recovery：`open orders + recent public trades/activity + positions`

可选打开 xuan 轮询：

```env
CF_XUAN_POLL_ENABLED=true
CF_XUAN_USER=0xcfb103c37c0234f524c632d964ed31f117b5f694
CF_XUAN_POLL_SEC=300
CF_XUAN_POLL_PAGE_LIMIT=500
CF_XUAN_POLL_MAX_PAGES=30
```

历史补拉 xuan public truth：

```bash
uv run python cfdata.py --log-level INFO backfill-xuan-public \
  --user 0xcfb103c37c0234f524c632d964ed31f117b5f694 \
  --start 2026-04-27T00:00:00Z \
  --end 2026-05-01T00:00:00Z \
  --max-pages 500 \
  --timeout-sec 30 \
  --dry-run

uv run python cfdata.py --log-level INFO backfill-xuan-public \
  --user 0xcfb103c37c0234f524c632d964ed31f117b5f694 \
  --start 2026-04-27T00:00:00Z \
  --end 2026-05-01T00:00:00Z \
  --max-pages 500 \
  --timeout-sec 30 \
  --raw-root data/raw
```

## 构建回放

```bash
python cfdata.py build-replay --raw-root data/raw --replay-root data/replay --day 2026-04-26
```

输出：

- `data/replay/2026-04-26/crypto_5m.sqlite`

滚动构建：

```bash
python cfdata.py build-replay-rolling --hours 24
```

replay builder 会从 `market_ws/book` raw 构建：

- `md_book_l1`：YES/NO 四价四量
- `md_book_l2`：每个 YES/NO 资产的 top5 bid/ask depth 快照，用于 clip-aware 回测和 maker fill proxy
- `md_trades`：保留 `trade_ts_ms / taker_side / price / size / market_side`

sidecar 会在标准化 book raw 中携带 `raw_l2`，包含当前 YES/NO 双边 top5 depth。builder 优先使用 `raw_l2` 写 `md_book_l2`，避免 round 初始 snapshot 只落到单侧 depth。

`md_book_l2` 是 compact replay 表：默认不复制 `raw_json`，但仍按 top5 depth 实际变化写入，不做时间降采样；原始 payload 留在 `data/raw` 的短期取证窗口。

长期后台循环建议直接使用：

```bash
python cfdata.py build-replay-rolling --hours 24 --validate-latest
```

不要在外层 shell 里再单独拼 `validate-replay --day "$(date -u +%F)"`。跨 UTC 零点时，这会让“构建窗口”和“校验日期”来自两个不同时间点，进而误报缺库或 `unable to open database file`。

## 验证

```bash
python cfdata.py validate-replay --replay-root data/replay --day 2026-04-26 --output data/replay/2026-04-26/validation.json
```

市场侧可信度审计可以显式传入可信起点，避免把采集器启动早期缺口误判为策略失败：

```bash
uv run python cfdata.py --log-level INFO audit-replay-market \
  --days 2026-04-27,2026-04-28,2026-04-29 \
  --trusted-start 2026-04-27T07:30:00Z \
  --raw-root data/raw \
  --replay-root data/replay
```

报告会输出 `trusted_start_ms`、planned outage、每个 DB 的 book/trade 最大时间和 `partial_day`。

## 启动前 1h 审计

### public-only

```bash
python cfdata.py audit-startup --day <UTC-YYYY-MM-DD> \
  --output data/replay/<UTC-YYYY-MM-DD>/startup_audit.json
```

### public + user truth

```bash
python cfdata.py audit-startup --day <UTC-YYYY-MM-DD> \
  --require-user-truth \
  --output data/replay/<UTC-YYYY-MM-DD>/startup_audit.json
```

`audit-startup` 现在会统一输出：

- 公开侧：`md_trades.taker_side`、`md_book_l1` 四个 size、`market_meta` round、`settlement_records`、`xuan poll points`
- 真值侧：`user_ws auth success`、`own_order_events`、`own_fill_events`、`bootstrap inventory rows`、`inventory drift degraded events`

## replay 重点表

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
- `settlement_records`

`md_book_l2` 是 replay 结构化深度表，不代表 maker queue truth。它能支持 top-N depth / clip pressure 分析，但不能证明自己的挂单排队成交。真实 maker fill 仍需要后续 `own_order_events / own_fill_events`。

## 回测数据层

多 agent 回测不要直接扫 `raw` 或大体积 `replay_published/*.sqlite`。当前标准分层是：

- `replay_published/YYYY-MM-DD/crypto_5m.sqlite`：source of truth，用于构建 cache/store 和最终少量候选验证。
- `backtest_cache/taker_buy_signal_core_v2_strict_l1/<label>`：严格 L1 对齐后的 taker-buy 搜索 cache，供并发参数搜索读取。
- `verification_store/completion_unwind_event_store_v1/<label>`：completion / unwind / inventory 事件层，供 maker/inventory 相关研究读取。

使用原则：

- 搜索和 ranking 优先读 cache/store。
- 其他 agent 不直接扫 `raw`、不反复全量扫 SQLite replay。
- 最终入选策略必须回到 replay/source-of-truth 做验证；cache 只负责快速筛选，不替代最终验证。
- 新增一天数据的顺序应是：当天 replay publish 完成后，先构建 strict V2 cache 和 completion unwind event store，再进入下一天重任务。

## 3 天运行建议

后台命令与监控方式见 [docs/RUNBOOK.md](docs/RUNBOOK.md)。

## 用户账户分析

`legacy/tools/analyze_trade.py` 是单市场、单账户的诊断工具，用来查看某个钱包在一个 Polymarket 事件/市场里的公开成交、方向、仓位变化和可视化表格。它不是多日策略回测引擎，也不替代 strict V2 cache、completion unwind event store 或最终 replay 验证。

推荐入口：

```bash
uv run python legacy/tools/analyze_trade.py "https://polymarket.com/event/..." --user 0x... --source auto
```

可接受的市场参数包括 Polymarket URL、slug、conditionId 或市场名。`--user` 不传时，工具会优先使用 `POLYMARKET_FUNDER_ADDRESS` / `ETHEREUM_ADDRESS`，再回退到最近一次使用的钱包地址。

### 数据源选择

默认 `--source auto`：

- 如果目标地址匹配本地配置的 CLOB funder，并且 CLOB credentials 可用，工具使用 authenticated execution view。
- 否则使用 public canonical view。

显式数据源：

```bash
# 分析第三方或 xuan 公开账户；适合公开成交诊断
uv run python legacy/tools/analyze_trade.py "btc-up-or-down-may-..." --user 0x... --source public --refresh --no-open

# 分析我方账户执行真值；要求本地 CLOB credentials 与目标 funder 匹配
uv run python legacy/tools/analyze_trade.py "https://polymarket.com/event/..." --user 0x... --source authenticated --no-open
```

数据源边界必须看清：

- `public` / `auto` public fallback 来自 Polymarket public Data API，可用于 xuan 或第三方钱包的公开成交分析，但不能证明对方私有挂单、撤单、排队优先级或完整库存路径。
- `authenticated` 只适合我方账户执行诊断，依赖本地 CLOB credentials；它看到的是我方 execution view，不会让我们看到其他人的私有订单真值。
- `public + user truth` 采集到的是我方 `own_order_events / own_fill_events / own_inventory_events`，不能事后补成任意第三方账户 truth。
- `xuan public truth` 只能来自 xuan 的公开 trades/activity/poll 数据，不能替代 xuan 的私有 maker queue truth。

### 输出与缓存

每次运行会在 `outputs/trade_analysis/` 下创建独立目录，目录名包含市场标识、账户摘要与运行时间。典型产物：

- `trades.json`
- `fetch_meta.json`
- `chart.html`
- `analysis_table.html`
- 旧图表链路可能额外生成 `chart.png`、`report.txt`

分析结论前必须先看 `fetch_meta.json`：

- `data_source`
- `view_mode`
- `warnings`
- 是否命中 cache

缓存位于 `outputs/trade_analysis/_cache/`。常用参数：

- `--refresh`：重新拉取并更新缓存。
- `--no-cache`：完全绕过缓存。
- `--no-open`：只生成文件，不打开浏览器。

### 何时用哪个入口

- 单市场、单账户人工诊断：用 `legacy/tools/analyze_trade.py`。
- 多 agent 参数搜索：用 `backtest_cache/taker_buy_signal_core_v2_strict_l1/<label>`。
- maker / inventory / completion-unwind 搜索：用 `verification_store/completion_unwind_event_store_v1/<label>`。
- 最终候选确认：用 replay/source-of-truth 验证队列，不能只凭账户分析或 cache 搜索结果部署。
