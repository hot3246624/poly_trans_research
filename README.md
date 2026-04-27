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
│   └── replay/
├── docs/
│   ├── PLAN_Codex.md
│   ├── PLAN_Codex_New.md
│   ├── RUNBOOK.md
│   └── STRUCTURE.md
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
2. `CF_L1_PRIVATE_KEY` 本地 `create_or_derive_api_creds`
3. 两者都缺失则自动退回 `public-only`

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
CF_XUAN_USER=0x...
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

## 验证

```bash
python cfdata.py validate-replay --replay-root data/replay --day 2026-04-26 --output data/replay/2026-04-26/validation.json
```

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
- `md_trades`
- `own_order_events`
- `own_fill_events`
- `own_inventory_events`
- `user_ws_log`
- `xuan_trades`
- `xuan_activity`
- `settlement_records`

## 3 天运行建议

后台命令与监控方式见 [docs/RUNBOOK.md](docs/RUNBOOK.md)。

## Legacy 分析工具输出

- `chartgenerator.py` 与 `interactive_chart.py` 的产出统一写到 `outputs/trade_analysis/`
- 每次运行会创建独立目录，目录名包含市场标识、账户摘要与运行时间
- 典型产物：`trades.json`、`chart.html`、`analysis_table.html`、`chart.png`、`report.txt`
