# Agent 本地回测数据使用说明 2026-05-19

本文档给所有研究/backtest agent 使用。当前默认数据根目录是本机：

```bash
export POLY_BT_ROOT=/Users/hot/web3Scientist/poly_backtest_data
```

只认 manifest 已发布的数据。不要扫 collector、raw、replay SQLite；不要把目录存在当成可用信号。

## 当前可用范围

有效完整日：

```text
2026-05-02..2026-05-13
2026-05-16
2026-05-17
2026-05-18
```

禁止使用：

```text
2026-05-14
2026-05-15
2026-05-19
```

`2026-05-14/15` 是采集降级日，不能用于完整日回测。`2026-05-19` 是停止采集后的 partial day，也不能纳入。

## 本地数据目录

### Taker-buy strict V2 cache

用途：快速参数搜索、特征组合、ranking。

```text
$POLY_BT_ROOT/backtest_cache/taker_buy_signal_core_v2_strict_l1/<label>
```

当前本地 label：

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
20260518
```

### Completion/unwind event store V2

用途：maker/inventory/completion-unwind 研究、candidate_base 物化、state-machine 回测。

```text
$POLY_BT_ROOT/verification_store/completion_unwind_event_store_v2/<label>
```

当前本地 label：

```text
20260502_20260508
20260509
20260510
20260511
20260512
20260513
20260516
20260517
20260518
```

### Public account audit/proxy truth

用途：B27/RWO 公开账户审计、公开成交 proxy truth。

```text
$POLY_BT_ROOT/verification_store/public_account_execution_truth_v1/20260502_20260513
```

限制：这是 public-account audit/proxy truth，不是 private owner-trade truth。不能证明私有挂单、撤单、真实 queue ahead 或私有 maker resting lifetime。

## 自动发现

Agent 必须用 manifest 自动发现，不要手写日期。

```bash
python - <<'PY'
from pathlib import Path
root = Path("/Users/hot/web3Scientist/poly_backtest_data")
block = {"20260514", "20260515", "20260519"}

def labels(base, manifest):
    out = []
    for p in sorted((root / base).glob("*")):
        if p.name in block:
            continue
        if (p / manifest).exists():
            out.append(p.name)
    return out

print("strict_v2_labels=", labels(
    "backtest_cache/taker_buy_signal_core_v2_strict_l1",
    "CACHE_MANIFEST.json",
))
print("completion_v2_labels=", labels(
    "verification_store/completion_unwind_event_store_v2",
    "EVENT_STORE_MANIFEST.json",
))
PY
```

## 强制报告字段

任何结果都必须声明：

```text
data_root=/Users/hot/web3Scientist/poly_backtest_data
dataset_type=<strict_v2_cache | completion_unwind_event_store_v2 | completion_unwind_event_store_v2_candidate_base | public_account_execution_truth_v1>
labels=<实际读取 label>
days=<实际覆盖 UTC day>
market_prefix/assets=<例如 btc-updown-5m- / BTC>
excluded=20260514,20260515,20260519
public_account_execution_truth_v1=<true/false>
raw/replay/collector_scanned=false
```

不能把 completion-store-only 结果写成 deployable 或 public-account-truth 结论。

## 推荐回测分层

### 1. 宽搜索

用 strict V2 cache：

```text
$POLY_BT_ROOT/backtest_cache/taker_buy_signal_core_v2_strict_l1
```

不要用旧 non-strict cache，不要扫 replay/raw 做宽搜索。

### 2. Candidate/state-machine pipeline

优先使用已经物化的 candidate_base 和 state-machine pipeline：

```text
$POLY_BT_ROOT/derived/completion_candidate_pipeline_v1/first_v1_local_20260502_20260517
$POLY_BT_ROOT/derived/completion_candidate_pipeline_v1/first_v1_state_machine_clip25_ceil101_cool10
```

当前第一版 candidate_base：

```text
source_row_count=25,111,732
candidate_row_count=2,168,080
ratio=8.63%
labels=20260502_20260508,20260509..20260513,20260516,20260517
```

注意：该 candidate_base 暂未包含 20260518。需要包含 20260518 时，重新运行：

```bash
uv run --with duckdb python scripts/build_completion_candidate_base.py \
  --data-root "$POLY_BT_ROOT" \
  --output-dir "$POLY_BT_ROOT/derived/completion_candidate_pipeline_v1/local_20260502_20260518"

uv run --with duckdb python scripts/run_completion_candidate_state_machine.py \
  --candidate-base "$POLY_BT_ROOT/derived/completion_candidate_pipeline_v1/local_20260502_20260518/candidate_base.duckdb" \
  --output-dir "$POLY_BT_ROOT/derived/completion_candidate_pipeline_v1/state_machine_clip25_ceil101_cool10_20260502_20260518" \
  --clip-size 25 \
  --pair-cost-ceiling 1.01 \
  --cooldown-s 10 \
  --fee-rate 0.0283 \
  --allow-partial \
  --block-after-residual
```

每个阶段必须输出 manifest、row_count、labels/days、schema、耗时。大 SQL/window join 不放进 heartbeat；state machine 只跑在小表上。

当前工程化 residual-cooldown 交付版本：

```text
candidate_base:
$POLY_BT_ROOT/derived/completion_candidate_pipeline_v1/local_20260502_20260518_paircap102

state_machine:
$POLY_BT_ROOT/derived/completion_candidate_pipeline_v1/pass_local_completion_residual_cooldown_e055_t5_imb125_rc30_050_20260502_20260518
```

核心 manifest：

```text
CANDIDATE_BASE_MANIFEST.json
RESULT_SUMMARY_MANIFEST.json
CANDIDATE_REGISTRY_MANIFEST.json
COMPLIANCE_MANIFEST.json
```

当前 residual-cooldown 配置：

```text
edge=0.055
target_qty=5
alignment=all
seed_px_band=0.05..0.90
fill_haircut=0.25
seed_l1_pair_cap=1.02
cooldown_s=5
imbalance_qty_cap=1.25
residual_cooldown_age_s=30
residual_cooldown_cost_cap=0.5
```

当前结果：

```text
status=PASS_LOCAL_COMPLETION_RESEARCH_ONLY
can_support_strategy_promotion=false
candidate_base_rows=3,740,411
candidate_registry_rows=51,618
pair_actions=27,358
pair_qty=30,426.4325
net_pair_cost_wavg=0.887251
net_pnl=+3,618.707937
stress100_worst_pnl=+1,538.959062
qty_residual_rate=5.0178%
```

合规验证：

```text
strict_cache_pass=true
strict_cache_covered_days=2026-05-02..13,2026-05-16,2026-05-17,2026-05-18
strict_cache_validation_error_count=0 for all labels

public_account_execution_truth_v1_present=true
public_account_audit_covered_days=2026-05-02..13
public_account_audit_missing_days=2026-05-16,2026-05-17,2026-05-18
public_account_audit_is_private_truth=false
promotion_gate_pass=false
```

读取示例：

```bash
export OUT="$POLY_BT_ROOT/derived/completion_candidate_pipeline_v1/pass_local_completion_residual_cooldown_e055_t5_imb125_rc30_050_20260502_20260518"

uv run --with duckdb python - <<'PY'
import duckdb, os
out = os.environ["OUT"]
con = duckdb.connect(f"{out}/state_machine_results.duckdb", read_only=True)
print(con.execute("""
  select strict_cache_day_covered, public_audit_day_covered, count(*)
  from candidate_registry
  group by 1,2
  order by 1,2
""").fetchall())
print(con.execute("""
  select day, seed_actions, pair_actions, net_pnl, stress100_worst_pnl
  from summary_by_day
  order by day
""").fetchall())
PY
```

解释边界：`PASS_LOCAL_COMPLETION_RESEARCH_ONLY` 只说明 completion-store 事件层研究通过；它不是部署结论，不是 private owner-trade truth，不是 replay source-of-truth audit。

### 3. Public account audit

用：

```text
$POLY_BT_ROOT/verification_store/public_account_execution_truth_v1/20260502_20260513/event_store.duckdb
```

只能作为 B27/RWO public proxy truth。不要写成私有账户真相。

### 4. Replay audit

当前 replay 冷归档还在远端压缩，未作为本地热数据使用。下载完成后，预期路径：

```text
/Volumes/My Passport/poly_replay_archive/_archives/<YYYY-MM-DD>/crypto_5m.sqlite.zst
```

replay 只用于 top finalist 的最终审计，不用于宽搜索。下载和验收必须使用：

```bash
zstd -tq --long=31 crypto_5m.sqlite.zst
```

因为远端 replay archive 使用了 `zstd --long=31`。

## 当前不要做

不要：

```text
使用 20260514/20260515/20260519
使用旧 non-strict cache
扫描 collector/raw/replay 做宽搜索
把 completion-store-only 结果写成 deployable
把 public account proxy truth 写成 private owner-trade truth
多个 agent 共用同一个 output dir
```

可以：

```text
并发读取 strict V2 cache
并发读取 completion_unwind_event_store_v2
读取 public_account_execution_truth_v1 做公开账户审计
在本地 candidate_base 小表上跑 residual/cooldown state machine
只对极少数 finalist 做 replay audit
```

## 一句话版本

本地回测统一从 `/Users/hot/web3Scientist/poly_backtest_data` 自动发现 manifest；当前可用 2026-05-02..13、05-16、05-17、05-18；严禁 05-14/15/19；搜索用 strict V2 cache，maker/inventory 用 completion_unwind_event_store_v2 或 candidate_base，最终 replay audit 等本地 replay archive 下载完成后只验证 top finalist。
