# poly_trans_research (Crypto 5m Public Capture)

本仓库当前主线是 **crypto 5m public market data** 采集与回放，不依赖交易程序，也不默认加载任何私钥/API key。

## 第一阶段范围

- 默认采 `BTC 5m`
- 可切到“所有 active crypto `5m` 市场”
- 只采 `market + meta`
- `user_ws` 默认关闭
- 连续采样后构建 replay sqlite
- 验收指标聚焦 `market_meta / md_book_l1 / md_trades`
- 预留并支持 `xuan trades/activity` 轮询校验链路（默认关闭）

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
│   ├── RUNBOOK.md
│   └── STRUCTURE.md
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
- `CF_META_SWITCH_DELAY_SEC=8`
- `CF_SETTLEMENT_POLL_ENABLED=true`
- `CF_SETTLEMENT_POLL_SEC=20`
- `CF_RAW_ROOT=data/raw`
- `CF_REPLAY_ROOT=data/replay`

如果要同时采所有活跃的 crypto `5m` 市场，而不是只采 `BTC`：

- `CF_MARKET_PREFIXES=*`
- `CF_MAX_MARKETS_PER_PREFIX=0`

这里的 `*` 表示“所有 crypto 5m 市场”，`0` 表示“不限每个前缀的数量”。

## 采集

### 1) sidecar（推荐）

```bash
python cfdata.py capture-sidecar-env --env-file config/research.env
```

这会：

- 从 `market_meta` 解析当前被选中的 active crypto `5m` rounds 的 `yes/no token_id`
- 按官方 `type=market` 订阅 schema 连接 market WS
- sidecar 内直接标准化写入 `book` 与 `last_trade_price`
- 当轮次切换（token 集变化）时自动重连切换
- 轮次切换时默认延迟 8s 切换订阅，降低旧 round 尾部丢样概率
- 自动补抓 `settlement_records`（基于 `clob /markets/{condition_id}` 的 winner）

如果你使用：

- `CF_MARKET_PREFIXES=btc-updown-5m`
- `CF_MAX_MARKETS_PER_PREFIX=1`

那么行为是“只订阅当前 active 的 BTC `5m` round”。

如果你使用：

- `CF_MARKET_PREFIXES=*`
- `CF_MAX_MARKETS_PER_PREFIX=0`

那么行为是“订阅所有 active crypto `5m` 市场”。

可选打开 xuan 轮询（默认关闭）：

```bash
# 在 config/research.env 中设置
CF_XUAN_POLL_ENABLED=true
CF_XUAN_USER=0x...
```

`CF_XUAN_USER` 需要填钱包地址（`0x...`），不是昵称。

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

滚动构建（建议每小时）：

```bash
python cfdata.py build-replay-rolling --hours 24
```

## 验证

```bash
python cfdata.py validate-replay --replay-root data/replay --day 2026-04-26 --output data/replay/2026-04-26/validation.json
```

当前 validator 第一阶段默认检查：

- `market_meta` 是否存在且覆盖完整
- `md_book_l1` round 覆盖率是否 >= 95%
- `md_trades` round 覆盖率是否 >= 95%
- `md_book_l1 / md_trades` 是否非空

## 启动前 1h 审计

```bash
# 1h 采样 + build + 启动审计
bash scripts/ops/startup_validation_1h.sh config/research.env
```

或手动执行：

```bash
python cfdata.py capture-sidecar-env --env-file config/research.env --duration-sec 3600
python cfdata.py build-replay --day <UTC-YYYY-MM-DD>
python cfdata.py audit-startup --day <UTC-YYYY-MM-DD> --output data/replay/<UTC-YYYY-MM-DD>/startup_audit.json
```

`audit-startup` 默认 gate：

- `md_trades.taker_side` 空值比例 <= 5%
- `md_book_l1` 四个 size 字段无空值
- `market_meta` round 数 >= 12
- `settlement_records` 行数 >= 1
- `xuan` 轮询点（trades/activity）各 >= 12

## 3 天采样建议

```bash
# Day 1-3 持续跑 sidecar
python cfdata.py capture-sidecar-env --env-file config/research.env

# 每天 UTC 切日后构建回放
python cfdata.py build-replay --raw-root data/raw --replay-root data/replay --day <UTC-YYYY-MM-DD>
python cfdata.py validate-replay --replay-root data/replay --day <UTC-YYYY-MM-DD>
```

推荐实操脚本与后台运行方式见：`docs/RUNBOOK.md`

## user_ws 预留

仓库保留了 `user_ws` 配置结构与可选参数位（`CF_USER_WS_ENABLED` 等），但第一阶段默认关闭，README 主流程不依赖 auth。

## 第一阶段 vs 第二阶段

- 第一阶段（当前）：公开市场侧研究与回测，不需要钱包私钥/API 私钥。
- 第二阶段（以后）：执行真值采样（挂单/撤单/成交回报），才需要开启 `user_ws` 与对应 auth。
- 第一阶段数据不会作废；第二阶段只是在现有链路上新增 user 通道数据。
- 第二阶段开启前未采到的 user 事件不可补回，只能从开启时刻起累积。

## 回放构建行为

- `build-replay` / `build-replay-rolling` 对同一 UTC 日会先重建该日 sqlite，再写入最新 raw 数据。
- 目的：保证滚动重建幂等，避免重复行随时间累积。

## 7x24 运维脚本

- `scripts/ops/com.polytrans.sidecar.plist.template`: launchd 模板
- `scripts/ops/watchdog_replay_lag.py`: 60s 检查 `md_book_l1` 最新时间，超阈值可触发重启命令
- `scripts/ops/disk_guard.py`: 数据目录体积/磁盘可用空间阈值检查
- `scripts/ops/hourly_rebuild.sh`: 每小时滚动 build + validate

## Legacy 分析工具输出

- `chartgenerator.py` 与 `interactive_chart.py` 的产出会统一写到 `outputs/trade_analysis/`
- 每次运行会创建一个独立目录，目录名包含市场标识、账户地址摘要与运行时间
- 典型产物包括：`trades.json`、`chart.html`、`analysis_table.html`、`chart.png`、`report.txt`
