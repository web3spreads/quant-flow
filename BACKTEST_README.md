# 回测系统使用说明

## 概述

回测系统允许您使用真实历史数据测试交易模型的成功率，无需实际执行交易。系统支持从Hyperliquid API获取历史数据或使用本地数据文件。每次回测都会创建独立的工作空间，包含完整的配置、环境变量和报告文件。

## 功能特性

- ✅ 使用真实历史K线数据回测
- ✅ 支持从Hyperliquid API获取历史数据（主网/测试网）
- ✅ 支持从本地CSV/JSON文件加载数据
- ✅ 模拟完整的交易流程（开仓、平仓、止盈止损）
- ✅ 支持两种策略模式：`single`（默认单币Agent）/ `grid`（网格策略）
- ✅ 计算手续费和滑点
- ✅ **独立的工作空间**：每次回测创建独立目录，包含配置和环境文件
- ✅ **Review Agent 支持**：自动复盘和学习交易经验
- ✅ **实时报告**：回测过程中实时更新进度
- ✅ **详细报告**：生成完整的回测报告（JSON、CSV、盈亏历史、元数据等）
- ✅ **恢复功能**：支持从实时报告恢复并继续回测
- ✅ 统计关键指标（胜率、盈亏比、最大回撤等）

## 使用方法

### 1. 基本回测（从API获取数据）

```bash
python backtest.py --symbol BTC --start-date 2024-01-01 --end-date 2024-12-01
```

### 2. 使用本地数据文件回测

```bash
python backtest.py --symbol BTC --data-file data/btc_history.csv
```

### 3. 指定初始余额和决策间隔

```bash
python backtest.py --symbol ETH \
    --start-date 2024-06-01 \
    --end-date 2024-12-01 \
    --initial-balance 1000 \
    --interval 15
```

### 4. 使用独立的配置和环境文件

```bash
python backtest.py --symbol BTC \
    --start-date 2024-01-01 \
    --end-date 2024-12-01 \
    --config config_backtest.yaml \
    --env-file .env.backtest \
    --initial-balance 1000 \
    --interval 15
```

### 5. 使用主网数据（默认）

```bash
# 默认使用主网数据
python backtest.py --symbol BTC --start-date 2024-01-01 --end-date 2024-12-01

# 明确指定使用测试网
python backtest.py --symbol BTC --start-date 2024-01-01 --end-date 2024-12-01 --testnet
```

### 6. 从实时报告恢复回测

```bash
python backtest.py --symbol BTC --resume-from backtest_results/backtest_BTC_20241205_120000/live_report.json
```

### 7. 控制实时报告刷新频率

```bash
# 每次决策点都更新（默认）
python backtest.py --symbol BTC --start-date 2024-01-01 --end-date 2024-12-01

# 每5个决策点更新一次
python backtest.py --symbol BTC --start-date 2024-01-01 --end-date 2024-12-01 --live-report-interval 5

# 禁用实时报告
python backtest.py --symbol BTC --start-date 2024-01-01 --end-date 2024-12-01 --live-report-interval 0
```

### 8. 使用网格策略回测

```bash
python backtest.py --symbol BTC \
    --start-date 2024-01-01 \
    --end-date 2024-12-01 \
    --strategy grid
```

## 命令行参数

### 必需参数

- `--symbol`: 交易对符号（如 BTC, ETH）
- `--data-file` 或 `--start-date`: 数据源（二选一）

### 可选参数

#### 数据源参数
- `--end-date`: 结束日期（格式: YYYY-MM-DD，与--start-date一起使用）
- `--timeframe`: K线时间周期（默认: 15m，可选: 1m, 5m, 15m, 30m, 1h, 4h, 1d）
- `--testnet`: 使用测试网数据（默认使用主网）

#### 交易参数
- `--initial-balance`: 初始余额（USD，默认: 10000）
- `--interval`: 决策间隔（分钟，默认: 5）
- `--strategy`: 策略模式（`single` 或 `grid`，默认: `single`）

#### 输出参数
- `--output-dir`: 报告输出目录（默认: backtest_results）
- `--live-report-interval`: 实时报告刷新频率（按决策点数量，默认: 1，设为0可禁用）

#### 配置参数
- `--config`: 配置文件路径（默认: config.yaml）
- `--env-file`: 环境变量文件路径（默认: .env，可通过环境变量 DOTENV_PATH 覆盖）

#### 恢复参数
- `--resume-from`: 从 live_report.json 文件恢复并继续回测（提供文件路径）

## 工作空间结构

每次回测都会创建一个独立的工作空间目录，格式为：`backtest_{symbol}_{timestamp}/`

```
backtest_results/
└── backtest_BTC_20241205_120000/
    ├── config.yaml              # 回测专用配置文件（自动生成）
    ├── .env                     # 回测专用环境变量文件（自动生成）
    ├── logs/
    │   └── review_memory.json   # Review Agent 经验存储
    ├── live_report.json         # 实时报告（运行中更新）
    ├── report.json              # 完整回测结果（回测完成后）
    ├── trades.csv               # 所有交易明细
    ├── pnl_history.csv          # 盈亏历史记录（用于图表展示）
    ├── metadata.json            # 回测参数和配置信息
    ├── README.md                # 报告说明文档
```

### 工作空间特点

1. **独立配置**：每个回测使用独立的 `config.yaml` 和 `.env` 文件
2. **自动配置**：
   - 通知功能自动禁用
   - Review Agent 自动启用
   - Review memory 文件自动指向工作空间的 `logs/review_memory.json`
3. **完整记录**：所有回测相关的文件都保存在工作空间中，便于管理和分析

## 数据文件格式

如果使用本地数据文件，文件必须包含以下列：

- `timestamp`: 时间戳（支持datetime字符串或毫秒时间戳）
- `open`: 开盘价
- `high`: 最高价
- `low`: 最低价
- `close`: 收盘价
- `volume`: 成交量

### CSV示例

```csv
timestamp,open,high,low,close,volume
2024-01-01 00:00:00,42000.0,42500.0,41800.0,42200.0,1000.5
2024-01-01 00:15:00,42200.0,42800.0,42100.0,42600.0,1200.3
```

### JSON示例

```json
[
  {
    "timestamp": "2024-01-01T00:00:00",
    "open": 42000.0,
    "high": 42500.0,
    "low": 41800.0,
    "close": 42200.0,
    "volume": 1000.5
  }
]
```

## 回测报告

### 报告文件说明

回测完成后，系统会在工作空间目录中生成以下文件：

1. **report.json**: 完整的回测结果，包含所有交易记录和统计指标
2. **trades.csv**: 每笔交易的明细记录（CSV格式，便于Excel分析）
3. **pnl_history.csv**: 盈亏历史记录，按时间排序，包含：
   - 每笔交易的时间戳
   - 单笔盈亏金额
   - 累计盈亏
   - 是否盈利
   - 交易方向、价格等详细信息
   - **可用于绘制盈亏曲线图**
4. **metadata.json**: 回测元数据，包含：
   - 回测参数（初始余额、决策间隔、时间周期等）
   - 模型信息（API base、模型名称、温度等）
   - 配置信息（最大交易金额、杠杆、止盈止损等）
5. **README.md**: 报告说明文档，包含报告摘要和文件说明
6. **config.yaml**: 使用的配置文件副本

### 实时报告

在回测运行过程中，系统会定期更新 `live_report.json` 文件，包含：
- 当前进度（已处理/总决策点数）
- 当前余额和持仓
- 最近一次决策
- 已完成交易列表

可以通过 `--live-report-interval` 参数控制刷新频率。

### 报告指标说明

- **总盈亏**: 所有交易的累计盈亏
- **总收益率**: 总盈亏 / 初始余额
- **胜率**: 盈利交易数 / 总交易数
- **盈亏比**: 平均盈利 / 平均亏损
- **最大回撤**: 账户价值从峰值到谷值的最大跌幅

## Review Agent

回测系统支持 Review Agent，可以在回测过程中自动复盘和学习交易经验：

- **自动启用**：每个回测工作空间自动启用 Review Agent
- **经验存储**：经验保存在工作空间的 `logs/review_memory.json`
- **独立学习**：每个回测有独立的经验库，不会相互干扰
- **无需通知**：回测中的 Review Agent 不会发送通知

Review Agent 会根据配置的 `review_run_every_cycles` 参数定期运行，分析最近的决策并提取经验规则。

## 恢复功能

如果回测中断，可以从实时报告恢复并继续：

1. 找到工作空间目录中的 `live_report.json` 文件
2. 使用 `--resume-from` 参数指定该文件路径
3. 系统会自动：
   - 恢复账户余额和持仓
   - 恢复已完成的交易记录
   - 从上次中断的位置继续回测

```bash
python backtest.py --resume-from backtest_results/backtest_BTC_20241205_120000/live_report.json
```

## 注意事项

1. **API限制**: 从Hyperliquid API获取数据时，请注意API的速率限制
2. **数据质量**: 确保历史数据完整且准确，缺失数据可能影响回测结果
3. **手续费**: 系统使用Hyperliquid的标准手续费率（0.045% taker / 0.015% maker per side）
4. **滑点**: 回测中使用收盘价作为成交价，实际交易可能存在滑点
5. **资金费率**: 当前版本暂不考虑资金费率的影响
6. **工作空间**: 每次回测都会创建新的工作空间，不会覆盖之前的回测结果
7. **配置文件**: 工作空间中的配置文件是自动生成的，修改原始配置文件不会影响已创建的工作空间

## 示例输出

### 回测过程输出

```
📁 创建回测工作空间: backtest_results/backtest_BTC_20241205_120000
✅ 回测配置文件已创建: backtest_results/backtest_BTC_20241205_120000/config.yaml
✅ 回测环境文件已创建: backtest_results/backtest_BTC_20241205_120000/.env
📋 加载配置...
📥 加载历史数据...
✅ 数据加载完成: 2880 条K线
   时间范围: 2024-01-01 00:00:00 至 2024-12-01 00:00:00
🔧 初始化回测引擎...
✅ 回测引擎初始化完成
   交易对: BTC
   初始余额: $1000.00
   数据点数: 2880
🚀 开始回测...
   决策间隔: 15 分钟
   决策点数量: 96
```

### 回测报告摘要

```
📊 回测报告摘要
============================================================

💰 账户信息:
   初始余额: $1000.00
   最终余额: $1050.00
   总盈亏: $50.00
   总收益率: 5.00%
   总手续费: $3.50

📈 交易统计:
   总交易数: 10
   盈利交易: 6
   亏损交易: 4
   胜率: 60.00%

💵 盈亏分析:
   平均盈利: $15.00
   平均亏损: $10.00
   盈亏比: 1.50

📉 风险指标:
   最大回撤: 2.50%

✅ 回测完成！
   报告目录: backtest_results/backtest_BTC_20241205_120000
   报告文件:
   - JSON: backtest_results/backtest_BTC_20241205_120000/report.json
   - CSV: backtest_results/backtest_BTC_20241205_120000/trades.csv
   - 盈亏历史: backtest_results/backtest_BTC_20241205_120000/pnl_history.csv
```

## 故障排除

### 问题：无法从API获取数据

- 检查网络连接
- 确认交易对符号正确
- 尝试使用 `--testnet` 参数
- 检查 `.env` 文件中的 API 配置（如果需要）

### 问题：本地文件加载失败

- 检查文件路径是否正确
- 确认文件格式符合要求
- 检查文件编码是否为UTF-8
- 确认文件包含必需的列（timestamp, open, high, low, close, volume）

### 问题：回测结果异常

- 检查历史数据是否完整
- 确认配置文件中的参数设置合理
- 查看工作空间目录中的日志文件
- 检查 `metadata.json` 中的配置信息

### 问题：恢复功能不工作

- 确认恢复文件路径正确
- 检查恢复文件是否完整（包含必要的字段）
- 确认恢复文件对应的工作空间目录存在
- 查看恢复过程中的错误提示

### 问题：Review Agent 未运行

- 检查工作空间中的 `config.yaml`，确认 `review_agent.enabled` 为 `true`
- 确认有足够的决策记录（至少 `review_lookback_decisions` 条）
- 查看日志中的 Review Agent 初始化信息

## 高级用法

### 批量回测

可以编写脚本批量运行多个回测：

```bash
#!/bin/bash
for symbol in BTC ETH; do
    python backtest.py \
        --symbol $symbol \
        --start-date 2024-01-01 \
        --end-date 2024-12-01 \
        --initial-balance 1000 \
        --interval 15
done
```

### 使用不同的配置进行对比

```bash
# 保守策略
python backtest.py --symbol BTC --config config_conservative.yaml --env-file .env.backtest

# 激进策略
python backtest.py --symbol BTC --config config_aggressive.yaml --env-file .env.backtest
```

### 分析盈亏历史

`pnl_history.csv` 文件可以直接用于数据可视化：

```python
import pandas as pd
import matplotlib.pyplot as plt

# 读取盈亏历史
df = pd.read_csv('backtest_results/backtest_BTC_20241205_120000/pnl_history.csv')
df['timestamp'] = pd.to_datetime(df['timestamp'])

# 绘制累计盈亏曲线
plt.figure(figsize=(12, 6))
plt.plot(df['timestamp'], df['cumulative_pnl'])
plt.title('累计盈亏曲线')
plt.xlabel('时间')
plt.ylabel('累计盈亏 (USD)')
plt.grid(True)
plt.show()
```
