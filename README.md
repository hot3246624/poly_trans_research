# poly-datasource

## 🚀 Polymarket 数据获取与策略分析工具

一个**完整的 Polymarket 数据分析平台**，不仅能获取交易数据，还能深度分析交易策略和市场模式。

### ✨ 核心特性

- **📊 交互式可视化 (`chart.html`)**: 生成气泡图，直观展示交易流向与资金分布。
- **📉 深度报表 (`analysis_table.html`)**: 自动生成包含 **Pair Cost (套利成本)**、**Net Exposure (净敞口)** 和 **Locked Profit (锁定利润)** 的审计级报表。
- **⚡ 高性能**: 单脚本集成数据抓取与分析，无需复杂配置。

### 🔧 安装与依赖

1. 确保已安装 Python 3.10+。
2. 安装所需依赖库：

```bash
pip install requests numpy plotly
```

### 🛠 使用方法

本项目目前推荐使用 **`interactive_chart.py`**作为核心工具，支持两种模式：

#### 模式 1：在线抓取并分析 (API Mode)
直接从 Polymarket 获取最新交易数据并生成报表。

```bash
# 格式: python3 interactive_chart.py "市场名称关键词" <钱包地址>

# 示例:
python3 interactive_chart.py "Bitcoin Up or Down" 0x6031b6eed1c97e853c6e0f03ad3ce3529351f96d
```
> **提示**: 建议使用尽可能详细的市场名称（如包含日期/时间），以确保抓取准确。

#### 模式 2：本地文件分析 (Offline Mode)
对已下载的 `trades.json` 文件进行离线复盘，无需重复请求 API。

```bash
# 格式: python3 interactive_chart.py <JSON文件路径>

# 示例:
python3 interactive_chart.py trades.json
```

### 📂 输出文件说明

运行脚本后，当前目录下会生成以下核心文件：

| 文件名 | 说明 |
|------|-------------|
| **`chart.html`** | 交互式可视化图表（气泡图、曲线图），浏览器打开即可查看。 |
| **`analysis_table.html`** | **核心分析报表**。包含每笔交易的成本核算与策略逻辑还原。 |
| `trades.json` | (仅API模式) 原始交易数据备份。 |

### 📜 经典图表工具 (chartgenerator.py)

如果您习惯使用旧版工具，我们也已将其恢复并修复：

```bash
# 用法: python3 chartgenerator.py <JSON文件路径>
python3 chartgenerator.py trades.json
```
生成 `chart.png` 静态图片。

### 📊 报表核心指标解读

在 `analysis_table.html` 中，您可以关注以下关键列来还原策略逻辑：

- **Pair Cost (组合成本)**: `买入YES均价 + 买入NO均价`。
    - **< 1.00 (绿色)**: 策略已锁定套利利润（无风险）。
    - **> 1.00 (红色)**: 策略当前处于风险敞口状态（Inventory Risk）。
- **Net Diff**: YES 与 NO 的净持仓差额。
- **Locked Profit**: 假设持有到期时的锁定利润（已扣除所有成本）。

---

**注意**: 若您需要使用旧版脚本 (`chartgenerator.py` 等)，请查阅历史文档或自行恢复。本项目现已全面转向 `interactive_chart.py` 单脚本工作流。
