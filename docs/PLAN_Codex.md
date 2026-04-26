# poly_trans_research BTC 5m Public Capture Refactor

## 摘要

把 `poly_trans_research` 重构成独立的 `BTC 5m` 研究采集器，主线只做 **public market data**，不依赖交易程序，不默认加载任何密钥。  
本次设计同时**预留一个可选的 auth/user_ws 接口位**，但默认关闭，不进入第一阶段采样主流程。

第一阶段目标固定为：

- 只采 `BTC 5m`
- 只采 `market + meta`
- 连续采 `3` 个 UTC 日
- 自动构建 replay sqlite
- 验证 `md_book_l1 / md_trades / market_meta` 覆盖率
- 不做执行真值，不做实盘依赖

## 关键改动

### 1. 配置与入口改成 research-only

收敛 `completion_first_data/cli.py` 和 README：

- `capture-sidecar-env` 默认读取 `poly_trans_research` 自己的 research env/config
- 不再默认指向 `../pm_as_ofi/.env`
- 新默认配置固定为：
  - `market_prefix=btc-updown-5m`
  - `market_channels=book,last_trade_price`
  - `disable_user_ws=true`
  - `meta_active_only=true`
  - `max_markets_per_prefix=1`
  - `meta_interval_sec=20`

新增独立 research 配置项：

- `CF_MARKET_PREFIXES=btc-updown-5m`
- `CF_MARKET_CHANNELS=book,last_trade_price`
- `CF_DISABLE_USER_WS=true`
- `CF_META_ACTIVE_ONLY=true`
- `CF_MAX_MARKETS_PER_PREFIX=1`
- `CF_META_INTERVAL_SEC=20`
- `CF_RAW_ROOT`
- `CF_REPLAY_ROOT`

### 2. 修正 market WS 订阅协议

在 `capture/websocket_sidecar.py` 重写 market 订阅构造：

- 不再按 `condition_id + channel` 发订阅
- 改为从 `capture/meta.py` 获取当前活跃 `BTC 5m` round 的 `yes_token_id/no_token_id`
- 用现行官方 market WS 口径订阅：
  - `type=market`
  - `assets_ids=[yes_token_id,no_token_id]`
  - `asset_ids=[yes_token_id,no_token_id]`
  - `markets=[]`
  - `initial_dump=true`
- `market_channels` 只作为本地过滤条件，不再映射为多条订阅报文
- meta 轮询发现活跃 round token 变化时，sidecar 主动重连并切换到下一轮

### 3. sidecar 内直接标准化 market 数据

不要把 full raw market WS 文本作为主采样资产。sidecar 直接落结构化 gzip jsonl：

- 为每个 `condition_id` 维护 lightweight `BookAssembler`
- 从 `book / price_change / best_bid_ask` 合成完整 L1
- 只写 replay 友好的标准化 `book` payload：
  - `condition_id`
  - `yes_bid_px yes_ask_px no_bid_px no_ask_px`
  - `yes_bid_sz yes_ask_sz no_bid_sz no_ask_sz`
  - `source_ts_ms`
- 从 `last_trade_price` 写标准化 `trade` payload：
  - `condition_id`
  - `trade_id`
  - `market_side`
  - `price`
  - `size`
  - `trade_ts_ms`
  - `source_ts_ms`
- `market_meta` 保持现有结构
- raw 文本只允许作为可选 debug 通道，默认关闭，不进入主 replay 口径

### 4. replay builder 继续消费标准化 payload

在 `replay/normalize.py` 和 `replay/builder.py` 固化输入契约：

- `normalize_book_row()` 只消费结构化 L1 payload
- `normalize_md_trade()` 只消费结构化 trade payload
- `build-replay` 不再把“从 raw_text 猜字段”作为主路径
- 若现有 normalizer 缺字段别名，只补别名，不引入第二套 schema

### 5. 预留可选 auth hook，但默认关闭

本次不接入执行真值，但在 sidecar 结构上预留第二阶段接口：

- 保留 `user_ws` 配置与对象定义
- 把 auth 配置拆成独立可选块，例如：
  - `CF_USER_WS_ENABLED=false`
  - `CF_API_KEY`
  - `CF_API_SECRET`
  - `CF_API_PASSPHRASE`
- 第一阶段主路径中：
  - README 不展示 auth 用法
  - 默认配置不加载 key
  - 验收不依赖 `user_ws`
- 第二阶段若要做极小额执行真值采样，只需要补 user channel，不改第一阶段 market/meta 主链路

## 测试计划

### 单测

1. `meta.normalize_market_meta`
- 正确解析 `condition_id / slug / yes_token_id / no_token_id / start_ms / end_ms`

2. market subscription builder
- 给定活跃 `BTC 5m` meta，生成官方 market 订阅报文
- token 变化时生成新订阅集合
- `max_markets_per_prefix=1` 时只保留当前活跃 round

3. book normalization
- `book` 初始快照可形成完整 L1
- `price_change` 可刷新对应侧 L1
- 单侧不完整更新不会写脏四价

4. trade normalization
- `last_trade_price` 能稳定生成 `trade_id/price/size/market_side`
- 缺关键字段时丢弃

### 集成测试

1. 单 round 冒烟
- 启动 sidecar，连接官方 `market` WS
- 订阅当前活跃 `BTC 5m`
- 生成 gzip raw 数据
- `build-replay` 产出 sqlite，且 `md_book_l1 > 0`、`md_trades > 0`

2. round rollover
- 在轮次切换附近运行
- meta 发现 token 变化后，sidecar 自动转订阅
- 相邻两轮都能在 replay 中出现 `market_meta + md_book_l1 + md_trades`

3. validator
- `validate-replay` 对连续 1 天数据通过核心 coverage 检查

### 3天验收

- 连续 `3` 个 UTC 日都能生成 replay sqlite
- 每日 `BTC 5m` round 覆盖率 `>= 95%`
- 每日 `md_book_l1` 与 `md_trades` 均非空
- 无需交易私钥、API key、`user_ws`
- 存储量显著低于旧 full raw recorder 方案

## 假设与默认值

- 第一阶段只研究公开市场侧行为，不研究我方真实成交
- public market data 足以回答第一阶段的机会频率、尾段参与、30s 配对候选环境问题
- 执行真值是第二阶段问题，不纳入本次重构验收
- 第二阶段如需执行真值，优先复用本次预留的可选 auth hook，而不是重做采集主链路
