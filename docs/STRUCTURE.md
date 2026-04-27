# Repository Structure (Crypto 5m Public Capture)

当前主线围绕 `docs/PLAN_Codex.md` 的第一阶段目标：

1. **Raw capture (public only)**
- 来源：`market_ws + meta_poll`
- sidecar 内标准化写入 `book` 与 `last_trade_price`
- settlement 补抓：`clob /markets/{condition_id}` -> `market_resolved`
- 可选 xuan worker：`data-api trades/activity` -> `xuan_trades/xuan_activity`
- 日分区路径：`data/raw/YYYY-MM-DD/<source>/<channel>.jsonl.gz`

2. **Replay builder**
- 从 raw 构建标准化 SQLite
- 输出：`data/replay/YYYY-MM-DD/crypto_5m.sqlite`
- 包含：`md_book_l1/raw_json`、`md_trades/raw_json+maker/taker`、`xuan_*` 表
- 同一 UTC 日按重建口径覆盖写入，保证滚动构建幂等

3. **Validator**
- 重点检查 `market_meta / md_book_l1 / md_trades` round 覆盖率与非空
- `audit-startup` 额外检查启动前门槛（taker_side、size 空值、settlement、xuan 轮询点）

## Main Entry

统一入口：`cfdata.py`

- `init-layout`
- `capture-meta`
- `capture-sidecar`
- `capture-sidecar-env`
- `build-replay`
- `build-replay-rolling`
- `validate-replay`
- `audit-startup`

## Research Config

- `config/research.env`
- `config/research.env.example`

默认值为 BTC 5m public capture，不依赖私钥。
如需采所有 active crypto `5m`，设置 `CF_MARKET_PREFIXES=*` 与 `CF_MAX_MARKETS_PER_PREFIX=0`。

## Ops Docs

- `docs/RUNBOOK.md`：1h 门槛验证、3 天后台运行、监控与停机命令
