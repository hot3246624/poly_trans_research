# Runbook

本文档覆盖两种运行模式：`public-only` 和 `public + user truth`。

## 0. 前置条件

- 工作目录：`/Users/hot/web3Scientist/poly_trans_research`
- 运行参数放 `config/research.env`
- 敏感项放 `config/.env` 或 `.env`
- 不要把真实密钥写进 repo-tracked 文件

### public-only

```env
CF_DISABLE_USER_WS=true
CF_USER_WS_ENABLED=false
```

### public + user truth

`config/research.env`：

```env
CF_DISABLE_USER_WS=false
CF_USER_WS_ENABLED=true
CF_USER_RECONCILE_SEC=60
CF_USER_RECOVERY_LOOKBACK_SEC=300
```

`config/.env` 或 `.env`：

```env
POLYMARKET_FUNDER_ADDRESS=0x...
CF_L1_PRIVATE_KEY=...
CF_API_KEY=...
CF_API_SECRET=...
CF_API_PASSPHRASE=...
```

## 1. 启动前 1h 门槛验证

### public-only

```bash
cd /Users/hot/web3Scientist/poly_trans_research
bash scripts/ops/startup_validation_1h.sh config/research.env
```

### public + user truth

```bash
cd /Users/hot/web3Scientist/poly_trans_research
uv run python cfdata.py --log-level INFO capture-sidecar-env \
  --env-file config/research.env \
  --duration-sec 3600
DAY_UTC=$(date -u +%F)
uv run python cfdata.py --log-level INFO build-replay --day "$DAY_UTC"
uv run python cfdata.py --log-level INFO audit-startup \
  --day "$DAY_UTC" \
  --require-user-truth \
  --output "data/replay/$DAY_UTC/startup_audit.json"
```

验收目标：`startup_audit.json` 中 `all_passed=true`。

## 2. 连续 3 天运行（后台）

启动 sidecar（72 小时）：

```bash
cd /Users/hot/web3Scientist/poly_trans_research
mkdir -p data/logs
nohup uv run python cfdata.py --log-level INFO capture-sidecar-env \
  --env-file config/research.env \
  --duration-sec 259200 \
  > data/logs/sidecar_3d_$(date +%Y%m%d_%H%M%S).log 2>&1 &
```

启动每小时滚动构建：

```bash
cd /Users/hot/web3Scientist/poly_trans_research
nohup bash -lc '
while true; do
  uv run python cfdata.py --log-level INFO build-replay-rolling --hours 24
  DAY_UTC=$(date -u +%F)
  uv run python cfdata.py --log-level INFO validate-replay --day "$DAY_UTC" || true
  sleep 3600
done
' > data/logs/rebuild_3d_$(date +%Y%m%d_%H%M%S).log 2>&1 &
```

## 3. 运行期监控

进程：

```bash
pgrep -fl 'capture-sidecar-env|build-replay-rolling'
```

日志：

```bash
tail -f /Users/hot/web3Scientist/poly_trans_research/data/logs/sidecar_3d_*.log
tail -f /Users/hot/web3Scientist/poly_trans_research/data/logs/rebuild_3d_*.log
```

真值侧额外建议关注：

- 是否出现 `auth_error`
- 是否出现 `inventory_truth_degraded`
- 是否持续有 `own_order_events / own_fill_events / own_inventory_events`

## 4. 每日验收（UTC）

### public-only

```bash
DAY_UTC=$(date -u +%F)
uv run python cfdata.py --log-level INFO build-replay --day "$DAY_UTC"
uv run python cfdata.py --log-level INFO validate-replay --day "$DAY_UTC"
uv run python cfdata.py --log-level INFO audit-startup --day "$DAY_UTC"
```

### public + user truth

```bash
DAY_UTC=$(date -u +%F)
uv run python cfdata.py --log-level INFO build-replay --day "$DAY_UTC"
uv run python cfdata.py --log-level INFO validate-replay --day "$DAY_UTC"
uv run python cfdata.py --log-level INFO audit-startup --day "$DAY_UTC" --require-user-truth
```

关键产物：

- `data/raw/YYYY-MM-DD/...`
- `data/replay/YYYY-MM-DD/crypto_5m.sqlite`
- `data/replay/YYYY-MM-DD/startup_audit.json`

## 5. 停止任务

```bash
pkill -f 'cfdata.py --log-level INFO capture-sidecar-env' || true
pkill -f 'cfdata.py --log-level INFO build-replay-rolling' || true
```

## 6. 边界

- `public-only` 不需要私钥/API 私钥。
- `public + user truth` 新增的是我方执行真值，不是对手挂单真值。
- user 通道历史无法事后补采；只能从开启时刻开始累积。
- 第一阶段公开侧数据不需要因为第二阶段而推倒重采。
