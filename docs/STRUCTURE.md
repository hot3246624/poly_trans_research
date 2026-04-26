# Repository Structure (BTC 5m Public Capture)

当前主线围绕 `docs/PLAN_Codex.md` 的第一阶段目标：

1. **Raw capture (public only)**
- 来源：`market_ws + meta_poll`
- sidecar 内标准化写入 `book` 与 `last_trade_price`
- 日分区路径：`data/raw/YYYY-MM-DD/<source>/<channel>.jsonl.gz`

2. **Replay builder**
- 从 raw 构建标准化 SQLite
- 输出：`data/replay/YYYY-MM-DD/crypto_5m.sqlite`

3. **Validator**
- 重点检查 `market_meta / md_book_l1 / md_trades` round 覆盖率与非空

## Main Entry

统一入口：`cfdata.py`

- `init-layout`
- `capture-meta`
- `capture-sidecar`
- `capture-sidecar-env`
- `build-replay`
- `validate-replay`

## Research Config

- `config/research.env`
- `config/research.env.example`

默认值为 BTC 5m public capture，不依赖私钥。
