# poly_trans_research (BTC 5m Public Capture)

本仓库当前主线是 **BTC 5m public market data** 采集与回放，不依赖交易程序，也不默认加载任何私钥/API key。

## 第一阶段范围

- 只采 `BTC 5m`
- 只采 `market + meta`
- `user_ws` 默认关闭
- 连续采样后构建 replay sqlite
- 验收指标聚焦 `market_meta / md_book_l1 / md_trades`

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
│   └── PLAN_Codex.md
└── src/completion_first_data/
```

## 安装

```bash
pip install -r requirements.txt
```

## Research 默认配置

`config/research.env`（默认从 `config/research.env.example` 复制）关键项：

- `CF_MARKET_PREFIXES=btc-updown-5m`
- `CF_MARKET_CHANNELS=book,last_trade_price`
- `CF_DISABLE_USER_WS=true`
- `CF_META_ACTIVE_ONLY=true`
- `CF_MAX_MARKETS_PER_PREFIX=1`
- `CF_META_INTERVAL_SEC=20`
- `CF_RAW_ROOT=data/raw`
- `CF_REPLAY_ROOT=data/replay`

## 采集

### 1) sidecar（推荐）

```bash
python cfdata.py capture-sidecar-env --env-file config/research.env
```

这会：

- 从 `market_meta` 解析当前活跃 BTC 5m round 的 `yes/no token_id`
- 按官方 `type=market` 订阅 schema 连接 market WS
- sidecar 内直接标准化写入 `book` 与 `last_trade_price`
- 当轮次切换（token 集变化）时自动重连切换

### 2) 只采 meta

```bash
python cfdata.py capture-meta --raw-root data/raw --active-only
```

## 构建回放

```bash
python cfdata.py build-replay --raw-root data/raw --replay-root data/replay --day 2026-04-26
```

输出：

- `data/replay/2026-04-26/crypto_5m.sqlite`

## 验证

```bash
python cfdata.py validate-replay --replay-root data/replay --day 2026-04-26 --output data/replay/2026-04-26/validation.json
```

当前 validator 第一阶段默认检查：

- `market_meta` 是否存在且覆盖完整
- `md_book_l1` round 覆盖率是否 >= 95%
- `md_trades` round 覆盖率是否 >= 95%
- `md_book_l1 / md_trades` 是否非空

## 3 天采样建议

```bash
# Day 1-3 持续跑 sidecar
python cfdata.py capture-sidecar-env --env-file config/research.env

# 每天 UTC 切日后构建回放
python cfdata.py build-replay --raw-root data/raw --replay-root data/replay --day <UTC-YYYY-MM-DD>
python cfdata.py validate-replay --replay-root data/replay --day <UTC-YYYY-MM-DD>
```

## user_ws 预留

仓库保留了 `user_ws` 配置结构与可选参数位（`CF_USER_WS_ENABLED` 等），但第一阶段默认关闭，README 主流程不依赖 auth。
