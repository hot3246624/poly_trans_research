# Runbook (Crypto 5m Public Capture)

本文档是第一阶段的实操手册：启动、监控、验收、停止。

## 0. 前置条件

- 使用 `config/research.env`（不要把真实私钥写入仓库文件）。
- 第一阶段默认不启用 `user_ws`，不需要钱包私钥/API 私钥。
- 建议在项目根目录执行：`/Users/hot/web3Scientist/poly_trans_research`
- 默认配置是 `BTC 5m`；如果要采所有 active crypto `5m`，把 `CF_MARKET_PREFIXES=*` 与 `CF_MAX_MARKETS_PER_PREFIX=0` 写入 `config/research.env`。

## 1. 启动前 1h 门槛验证

```bash
cd /Users/hot/web3Scientist/poly_trans_research
bash scripts/ops/startup_validation_1h.sh config/research.env
```

完成后查看：

```bash
cat /Users/hot/web3Scientist/poly_trans_research/data/replay/$(date -u +%F)/startup_audit.json
```

目标：`all_passed=true`。

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

## 4. 每日验收（UTC）

```bash
DAY_UTC=$(date -u +%F)
uv run python cfdata.py --log-level INFO build-replay --day "$DAY_UTC"
uv run python cfdata.py --log-level INFO validate-replay --day "$DAY_UTC"
```

关键产物：

- `data/raw/YYYY-MM-DD/...`
- `data/replay/YYYY-MM-DD/crypto_5m.sqlite`

## 5. 停止任务

```bash
pkill -f 'cfdata.py --log-level INFO capture-sidecar-env' || true
pkill -f 'cfdata.py --log-level INFO build-replay-rolling' || true
```

## 6. 第一阶段与第二阶段边界

- 第一阶段：只做 market/meta/xuan 的公开数据研究与回测，不需要 auth。
- 第二阶段：如果要采执行真值（你自己的订单与成交回报），才需要启用 `user_ws` 与私钥/API 凭证。
- 第二阶段不需要推倒重采第一阶段；但 user 通道历史无法补采。
