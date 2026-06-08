# Agent 回测数据使用说明 2026-05-15

本文档给研究/backtest agent 使用。目标是避免重复扫大 replay、避免误用降级日期，并统一“搜索、验证、公开账户审计”三层数据源。

## 当前结论

可用于常规研究的完整日期以 manifest 自动发现为准。当前 collector 已验证的有效日期包含：

```text
2026-05-02..2026-05-13
2026-05-16..2026-05-17
```

`2026-05-18` 仍按热采集日处理；只有 replay publish、strict V2 cache 和 completion unwind event store 都发布后，才纳入常规研究。

禁止自动使用的日期：

```text
2026-05-14
2026-05-15
```

原因：这两天 public market_ws L1/L2 capture 曾降级，不能作为完整日回测样本。不要因为看到 raw/replay 目录就使用这两天。只有在人为声明“做故障取证/局部区间分析”时才可以单独读取。

## 先判定数据集范围

任何 agent 在报告“回测结果”前，必须先报告自己实际使用的数据范围。不要把本地 `/tmp` 副本、部分 label、单资产研究集，当成 collector 上的完整回测数据。

报告里至少写清：

```text
data_root: 实际读取的根目录
dataset_type: strict_v2_cache / completion_unwind_event_store_v2 / public_account_execution_truth_v1 / replay_audit
labels: 实际读取的 label 列表
days: 实际覆盖的 day 列表
assets_or_market_prefix: 例如 btc-updown-5m；如果没验证多资产，就写 BTC-only
row_count: 实际读取行数
blocked_days_excluded: 是否确认排除了 20260514/20260515
public_account_truth_available: 是否读取了 public_account_execution_truth_v1
```

典型局部副本示例：

```text
/tmp/xuan_frontier_data/completion_unwind_event_store_v2/20260509..13
```

如果一个 agent 只看到这类本地副本，并且查询结果显示只有 BTC、只覆盖 2026-05-09..2026-05-13，那么它只能得出：

```text
BTC strict-V2 completion/unwind 研究结论，覆盖 2026-05-09..2026-05-13。
```

不能得出：

```text
完整 2026-05-02..2026-05-13 结论
多资产结论
B27/RWO/xuan public account truth 结论
可直接部署结论
```

`completion_unwind_event_store_v2` 里的 `side_bid_level_drop_qty`、`side_ask_level_lift_qty`、L2 VWAP/clip 字段是研究特征。它们可以用来评估 depth/depletion 逻辑，但如果 live/shadow 的 `BookTick` 没有同等字段，就不能直接证明 live 可执行；需要补 live 特征或做不依赖这些字段的 ablation。

同一个 `ts_ms` / market / side 下可能有多个事件。verifier 和策略搜索必须使用稳定 tie-breaker，不要假设 `(day, condition_id, side, ts_ms)` 唯一。建议排序键：

```sql
ORDER BY
  day,
  condition_id,
  ts_ms,
  strict_l1_recv_ms NULLS LAST,
  strict_l1_row_id NULLS LAST,
  strict_l2_recv_ms NULLS LAST,
  strict_l2_row_id NULLS LAST,
  event_kind,
  side,
  public_trade_row_id NULLS LAST,
  event_id
```

快速检查本地 event store 覆盖：

```bash
export STORE=/tmp/xuan_frontier_data/completion_unwind_event_store_v2/20260509/event_store.duckdb

uv run --with duckdb python - <<'PY'
import duckdb, os
store = os.environ["STORE"]
con = duckdb.connect(store, read_only=True)
print(con.execute("""
  select
    min(day) as min_day,
    max(day) as max_day,
    count(distinct day) as days,
    count(distinct slug) as markets,
    count(*) as rows,
    sum(case when slug like 'btc-updown-5m-%' then 1 else 0 end) as btc_rows
  from completion_unwind_events
""").fetchall())
print(con.execute("""
  select day, count(distinct slug) as markets, count(*) as rows
  from completion_unwind_events
  group by 1
  order by 1
""").fetchall())
PY
```

## Collector 和挂载

Collector：

```text
ubuntu@ec2-108-129-167-79.eu-west-1.compute.amazonaws.com
private IP: 172.31.38.62
ssh key: ~/.ssh/polymarket-Ireland.pem
```

Collector 只读 NFS exports：

```text
/home/ubuntu/poly_trans_research/data/backtest_cache
/home/ubuntu/poly_trans_research/data/verification_store
/home/ubuntu/poly_trans_research/data/replay_published
```

建议在回测服务器挂载为：

```bash
sudo mkdir -p /mnt/poly-cache /mnt/poly-verification-store /mnt/poly-replay-published

sudo mount -t nfs -o ro,nfsvers=4.2,hard,noatime,nconnect=4,rsize=1048576,wsize=1048576 \
  172.31.38.62:/home/ubuntu/poly_trans_research/data/backtest_cache \
  /mnt/poly-cache

sudo mount -t nfs -o ro,nfsvers=4.2,hard,noatime,nconnect=4,rsize=1048576,wsize=1048576 \
  172.31.38.62:/home/ubuntu/poly_trans_research/data/verification_store \
  /mnt/poly-verification-store

sudo mount -t nfs -o ro,nfsvers=4.2,hard,noatime,nconnect=4,rsize=1048576,wsize=1048576 \
  172.31.38.62:/home/ubuntu/poly_trans_research/data/replay_published \
  /mnt/poly-replay-published
```

`/mnt/poly-replay-published` 只用于小样本审计，不用于宽参数搜索。

## 自动发现规则

不要通过 `raw` 或 `replay_published` 判断某天可回测。正确规则如下。

### Taker-buy strict V2 搜索数据

目录：

```text
/mnt/poly-cache/taker_buy_signal_core_v2_strict_l1/<label>
```

一个 label 可用于搜索，当且仅当：

```text
CACHE_MANIFEST.json 存在
label 不包含 20260514 或 20260515
```

当前可用 label：

```text
20260502_20260507
20260508
20260509
20260510
20260511
20260512
20260513
20260516
20260517
```

发现命令：

```bash
find /mnt/poly-cache/taker_buy_signal_core_v2_strict_l1 \
  -mindepth 2 -maxdepth 2 -name CACHE_MANIFEST.json \
  -printf '%h\n' | sort | grep -Ev '20260514|20260515'
```

### Completion/unwind event store V2

目录：

```text
/mnt/poly-verification-store/completion_unwind_event_store_v2/<label>
```

一个 label 可用于 maker/inventory/completion-unwind 研究，当且仅当：

```text
EVENT_STORE_MANIFEST.json 存在
event_store.duckdb 存在
label 不包含 20260514 或 20260515
```

当前可用 label：

```text
20260502_20260508
20260509
20260510
20260511
20260512
20260513
20260516
20260517
```

发现命令：

```bash
find /mnt/poly-verification-store/completion_unwind_event_store_v2 \
  -mindepth 2 -maxdepth 2 -name EVENT_STORE_MANIFEST.json \
  -printf '%h\n' | sort | grep -Ev '20260514|20260515'
```

### Public account audit/proxy truth

目录：

```text
/mnt/poly-verification-store/public_account_execution_truth_v1/20260502_20260513
```

DuckDB table：

```text
public_account_execution_events
```

当前已完成：

```text
row_count: 562087
b27bc:     551392
rwo:       10695
fills:     556908
merge:     199
redeem:    2510
settle:    2470
```

用途：分析 B27/RWO 的公开成交行为、maker/taker public inference、严格 L1/L2 上下文、merge/redeem/settlement 结果。

限制：这是 public-account audit/proxy truth，不是 private owner-trade truth。它不能证明私有下单、撤单、真实 queue ahead 或真实 maker resting lifetime。

## 回测分层

### 1. 参数搜索

用 strict-L1 V2 cache：

```text
/mnt/poly-cache/taker_buy_signal_core_v2_strict_l1/<label>
```

不要用旧的非 strict cache：

```text
taker_buy_signal_core_v2
```

不要扫 replay/raw 做宽搜索。

示例：

```bash
AGENT_ID=<agent_id>
LABEL=20260513
CACHE_DIR=/mnt/poly-cache/taker_buy_signal_core_v2_strict_l1/$LABEL
OUT=/tmp/$AGENT_ID/taker_buy_search_v2_strict_l1_$LABEL

uv run --with duckdb python scripts/search_taker_buy_signal_candidate_cache_v2.py \
  --cache-dir "$CACHE_DIR" \
  --output-dir "$OUT"
```

每个 agent 必须使用自己的 output dir，不要共享输出目录。

### 2. Maker / inventory / completion-unwind 研究

用 completion unwind V2 event store：

```text
/mnt/poly-verification-store/completion_unwind_event_store_v2/<label>/event_store.duckdb
```

DuckDB table：

```text
completion_unwind_events
```

示例：

```bash
STORE=/mnt/poly-verification-store/completion_unwind_event_store_v2/20260513/event_store.duckdb

uv run --with duckdb python - <<'PY'
import duckdb
store = "/mnt/poly-verification-store/completion_unwind_event_store_v2/20260513/event_store.duckdb"
con = duckdb.connect(store, read_only=True)
print(con.execute("""
  select event_kind, count(*)
  from completion_unwind_events
  group by 1
  order by 1
""").fetchall())
PY
```

对于跨天训练/评估，优先使用已发布的大 label：

```text
20260502_20260508
```

再追加单日：

```text
20260509..20260513
```

不要自己逐 market 扫 L1/L2。

### 3. B27/RWO public account audit

用：

```text
/mnt/poly-verification-store/public_account_execution_truth_v1/20260502_20260513/event_store.duckdb
```

示例：

```bash
uv run --with duckdb python - <<'PY'
import duckdb
store = "/mnt/poly-verification-store/public_account_execution_truth_v1/20260502_20260513/event_store.duckdb"
con = duckdb.connect(store, read_only=True)
print(con.execute("""
  select account_label, day, event_kind, count(*)
  from public_account_execution_events
  group by 1,2,3
  order by 1,2,3
""").fetchall())
print(con.execute("""
  select account_label, truth_level, order_type, count(*)
  from public_account_execution_events
  where event_kind='fill'
  group by 1,2,3
  order by 1,2,3
""").fetchall())
PY
```

可用来回答：

```text
B27/RWO 在哪些时间和市场成交？
公开推断 maker/taker 角色如何分布？
成交时 strict L1/L2 状态是什么？
与 completion/inventory 模型的候选事件是否同向？
```

不可用来回答：

```text
私有订单是否真实挂单
私有撤单时间
真实 queue ahead
部署级别的 maker fillability 证明
```

## 最终验证原则

搜索结果不能直接作为最终结论。

推荐顺序：

```text
strict V2 cache 搜索
-> completion_unwind_event_store_v2 / public_account_execution_truth_v1 交叉检查
-> 只对极少数 finalist 做 replay audit
```

`replay_published` 可读，但只能用于小样本 audit。不要让多个 agent 并发扫 replay SQLite。尤其不要在 NFS 上做宽参数搜索。

如果必须做 replay audit：

```text
1. 只验证 top finalist，不验证大网格。
2. 每个 agent 先把候选 spec/CSV 写清楚。
3. 由 collector 本地或队列执行，避免多 agent 同时扫 replay。
4. audit 输出必须记录输入 spec、日期、数据路径和脚本版本。
```

## 建议本地复制策略

NFS 可以并发读 cache/event store，但为了减少抖动，单个 agent 可以把小型 DuckDB/Parquet store 复制到本机 `/tmp` 后查询：

```bash
AGENT_ID=<agent_id>
mkdir -p /tmp/$AGENT_ID/poly-cache-local

rsync -a /mnt/poly-cache/taker_buy_signal_core_v2_strict_l1/20260513 \
  /tmp/$AGENT_ID/poly-cache-local/taker_buy_signal_core_v2_strict_l1/

rsync -a /mnt/poly-verification-store/completion_unwind_event_store_v2/20260513 \
  /tmp/$AGENT_ID/poly-cache-local/completion_unwind_event_store_v2/
```

不要复制 replay SQLite 到 250G 机器，空间不划算。

## 当前不要做的事

不要：

```text
使用 20260514 / 20260515 做完整日回测
使用旧 non-strict cache
在回测服务器上扫 replay/raw 做宽搜索
多个 agent 共用同一个 output dir
把 public account proxy truth 当成私有成交真相
看到 replay_published 新目录就自动纳入研究
```

可以：

```text
并发读取 strict V2 cache
并发读取 completion_unwind_event_store_v2
并发读取 public_account_execution_truth_v1
把小型 cache/store 复制到本地 /tmp 后查询
只对少量 finalist 做 replay audit
```

## 一句话版本

搜索用：

```text
/mnt/poly-cache/taker_buy_signal_core_v2_strict_l1
```

maker/inventory 用：

```text
/mnt/poly-verification-store/completion_unwind_event_store_v2
```

B27/RWO audit 用：

```text
/mnt/poly-verification-store/public_account_execution_truth_v1/20260502_20260513
```

只使用 manifest 已发布且不在 blocklist 的有效 label；当前包括 2026-05-02..2026-05-13、2026-05-16..2026-05-17。不要使用 2026-05-14/15；不要宽扫 replay/raw。
