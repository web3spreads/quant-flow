# 🤖 Quant Flow - AI 驱动的加密货币自动交易机器人

> 基于 LangChain Agent 和 LLM 的 24/7 智能交易系统

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![LangChain](https://img.shields.io/badge/LangChain-1.0+-green.svg)](https://python.langchain.com/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

## ✨ 核心特性

- 🧠 **AI 驱动决策**: 使用 GPT-4 等 LLM 进行智能交易决策
- 📊 **技术指标分析**: 支持 RSI、MACD、MA、布林带等技术指标
- 🛡️ **风险管理**: 自动止盈止损保护，熔断机制
- ⏰ **24/7 运行**: 基于 APScheduler 的定时执行
- 💱 **Bitget 集成**: 支持现货交易，完整的止盈止损功能
- 🎨 **美观日志**: 使用 Rich 库提供彩色控制台输出
- 📝 **结构化日志**: 支持 JSON/CSV 格式的决策日志
- 🔧 **灵活配置**: 支持多种 LLM 提供商，YAML 配置文件

## 📦 交易 SDK 说明

本项目支持两种 Bitget 交易 SDK，可通过配置文件选择：

### 1. Bitget 官方 SDK（推荐）⭐

**优点：**
- ✅ 官方维护，功能完整，第一时间支持新特性
- ✅ **完整止盈止损支持**：使用计划单（Plan Order）实现可靠的 TP/SL
- ✅ 支持专业功能：跟单、网格交易等

**使用场景：**
- 只在 Bitget 交易所交易
- 需要完整的止盈止损功能
- 需要使用 Bitget 特有的高级功能

**配置方式：**
```yaml
trading:
  use_official_sdk: true  # 推荐设置
```

### 2. CCXT（通用交易所接口）

**优点：**
- ✅ 统一接口，支持 100+ 交易所
- ✅ 社区活跃，维护良好
- ✅ 市场数据获取标准化

**限制：**
- ⚠️ 止盈止损功能在现货交易中支持受限
- ⚠️ 交易所 API 更新后需要等待 CCXT 跟进

**使用场景：**
- 需要同时支持多个交易所
- 未来可能切换到其他交易所
- 只需要基本的市场订单功能

**配置方式：**
```yaml
trading:
  use_official_sdk: false
```

### 混合架构设计

为了充分利用两者的优势，本项目采用以下架构：

```
┌─────────────────────────────────────────┐
│         Quant Flow Architecture          │
├─────────────────────────────────────────┤
│  Market Data (CCXT)                     │
│  - 获取 K 线数据                         │
│  - 获取 Ticker                           │
│  - 标准化接口                            │
├─────────────────────────────────────────┤
│  Trading Execution (可选)                │
│  - Bitget 官方 SDK (推荐)               │
│    • 完整止盈止损                        │
│    • 计划单功能                          │
│  - CCXT (备选)                          │
│    • 基本订单功能                        │
└─────────────────────────────────────────┘
```

**推荐配置：**
- 市场数据：使用 CCXT（已内置，无需配置）
- 交易执行：使用 Bitget 官方 SDK（在配置文件中设置 `use_official_sdk: true`）

## 🏗️ 系统架构

```
┌──────────────────────────────────────────────────────────────┐
│                                                               │
│   Scheduler          →  Data Fetcher &       →  Prompt Engine│
│  (每 3 分钟)           Indicator Engine        (生成提示词)  │
│                                                               │
│       ↑                                                   ↓   │
│       │                                                   │   │
│                                                               │
│   Bitget API    ←       Tool Executor   ←    LangChain Agent │
│    Wrapper             (buy, sell, ...)       (LLM + Memory) │
│                                                               │
│       │                                                   │   │
│       │                                                   │   │
│       ↓                                                   ↓   │
│                                                               │
│                      Logging & Monitoring                     │
│            (Console Output, Structured Log Files)             │
│                                                               │
└──────────────────────────────────────────────────────────────┘
```

## 📋 安装部署

### 环境要求

- Python 3.10+
- [uv](https://github.com/astral-sh/uv) (Python 包管理器) 或 pip
- Bitget 交易所账号（需要 API Key）
- OpenAI API Key 或兼容的 LLM API

### 安装步骤

1. **克隆仓库**

```bash
git clone https://github.com/yourusername/quant-flow.git
cd quant-flow
```

2. **安装依赖**

使用 uv（推荐）：

```bash
uv sync
```

或使用 pip：

```bash
pip install -e .
```

3. **环境变量配置**

复制环境变量模板：

```bash
cp .env.example .env
```

编辑 `.env` 文件，填入你的 API 密钥：

```env
# OpenAI 或兼容 API 配置
OPENAI_API_BASE=https://api.openai.com/v1
OPENAI_API_KEY=your_openai_api_key_here
OPENAI_MODEL=gpt-4

# Bitget 交易所 API 配置
BITGET_API_KEY=your_bitget_api_key_here
BITGET_API_SECRET=your_bitget_api_secret_here
BITGET_PASSPHRASE=your_bitget_passphrase_here

# 测试模式（建议先用测试模式）
TEST_MODE=true
```

4. **配置文件设置**

复制配置文件模板：

```bash
cp config.yaml.example config.yaml
```

根据你的需求修改 `config.yaml` 配置：

```yaml
trading:
  # 交易 SDK 选择
  use_official_sdk: true    # true: Bitget 官方 SDK (推荐), false: CCXT

  symbols:
    - BTC/USDT
    - ETH/USDT
  trade_amount: 100          # 每次交易金额（USDT）
  take_profit_ratio: 0.05    # 止盈比例 5%
  stop_loss_ratio: 0.02      # 止损比例 2%
  max_positions: 2           # 最大持仓数量
```

## 📖 使用指南

### 启动机器人

```bash
python main.py
```

使用 uv：

```bash
uv run python main.py
```

### 测试模式

机器人默认在测试模式下运行（在 `.env` 中设置 `TEST_MODE=true`）。这样不会真实下单，只会模拟交易流程。

**测试完成后，切换到生产模式：**
1. 在 `.env` 中设置 `TEST_MODE=false`
2. 确保已正确配置 Bitget API Key
3. **强烈建议先用小金额测试！**

### 停止机器人

按 `Ctrl+C` 即可安全停止机器人。

## 🔄 工作流程

1. **定时触发**: 每隔 N 分钟（默认 3 分钟）触发一次交易循环
2. **数据获取**: 获取最新的 K 线数据
3. **指标计算**: 计算 RSI、MACD、MA、布林带等技术指标
4. **AI 决策**: 数据传递给 LangChain Agent，由 LLM 进行智能决策
5. **执行交易**: 根据 AI 决策执行交易（带止盈止损保护）
6. **风险管理**: 自动设置止盈止损单
7. **日志记录**: 记录决策过程和交易结果

## 📁 项目结构

```
quant-flow/
├── main.py                    # 主程序入口
├── config.yaml                # 配置文件
├── .env                       # 环境变量（敏感信息）
├── pyproject.toml             # 项目依赖
├── src/
│   ├── config.py              # 配置管理
│   ├── agent/                 # AI Agent 模块
│   │   ├── tools.py           # LangChain 工具定义
│   │   ├── prompts.py         # Prompt 模板
│   │   └── trading_agent.py   # Agent 核心逻辑
│   ├── data/                  # 数据获取模块
│   │   ├── market_data.py     # 市场数据获取
│   │   └── indicators.py      # 技术指标计算
│   ├── trading/               # 交易执行模块
│   │   ├── bitget_official_client.py  # Bitget 官方 SDK 客户端
│   │   ├── bitget_client.py   # 统一交易客户端接口
│   │   └── order_manager.py   # 订单管理器
│   ├── utils/                 # 工具模块
│   │   └── logger.py          # 日志模块
│   └── bitget-python-sdk-api/ # Bitget 官方 SDK
├── logs/                      # 日志文件
│   ├── decisions/             # 决策日志
│   └── trades/                # 交易日志
└── tests/                     # 测试代码
```

## ⚙️ 配置说明

### OpenAI API 配置

本项目支持 OpenAI 或兼容的 API：

- OpenAI 官方 API
- DeepSeek
- 智谱 AI (GLM)
- 本地部署的 LLM（如 Ollama）

只需在 `.env` 中配置相应的 `OPENAI_API_BASE` 和 `OPENAI_API_KEY`。

### 交易配置

| 配置项 | 说明 | 默认值 |
|------|------|--------|
| `use_official_sdk` | 是否使用 Bitget 官方 SDK | true (推荐) |
| `trade_amount` | 每次交易金额（USDT） | 100 |
| `take_profit_ratio` | 止盈比例 | 0.05 (5%) |
| `stop_loss_ratio` | 止损比例 | 0.02 (2%) |
| `max_positions` | 最大持仓数量 | 2 |
| `interval_minutes` | 决策间隔（分钟） | 3 |

### 技术指标

| 指标 | 说明 |
|------|------|
| RSI | 相对强弱指数，默认周期 14 |
| MACD | 指数平滑异同移动平均线 (12, 26, 9) |
| MA | 移动平均线 (7, 25, 99) |
| Bollinger Bands | 布林带 (20, 2) |

## 📊 日志系统

### 决策日志

位于 `logs/decisions/` 目录，记录 AI 决策的详细信息：

- 市场数据和技术指标
- 完整的 AI 决策提示词 Prompt
- AI 的响应和决策过程
- 执行结果

### 交易日志

位于 `logs/trades/` 目录，记录交易信息：

- 订单 ID 和执行详情
- 止盈止损价格
- 执行状态

## ⚠️ 风险提示

**重要提醒：**

1. ⚠️ **高风险警告**：加密货币交易具有极高风险，可能导致资金损失！
2. 🧪 **先测试**：务必使用测试模式充分测试
3. 💰 **小金额起步**：初次使用请使用小金额
4. 📚 **理解原理**：充分理解交易策略和风险
5. 🔑 **保管密钥**：妥善保管 API Key，不要泄露
6. 🛡️ **设置保护**：合理设置止盈止损比例，控制风险

## 💡 常见问题

### Q: 如何使用其他 LLM（如 DeepSeek）？

A: 在 `.env` 中修改 `OPENAI_API_BASE` 为其他 API 的地址，并填入对应的 API Key。

### Q: 如何添加更多交易对？

A: 在 `config.yaml` 的 `trading.symbols` 列表中添加更多交易对。

### Q: 如何修改决策间隔？

A: 修改 `config.yaml` 中的 `scheduler.interval_minutes`。

### Q: 如何查看历史决策？

A: 在 `logs/decisions/` 目录查看结构化的 JSON 或 CSV 格式日志。

### Q: CCXT 和官方 SDK 有什么区别？

A:
- **Bitget 官方 SDK**：支持完整的止盈止损计划单功能，推荐使用
- **CCXT**：通用接口，支持多交易所，但止盈止损功能受限

在 `config.yaml` 中通过 `use_official_sdk` 选择。

## 🗺️ 开发路线

- [ ] 支持更多技术指标
- [ ] 支持期货交易
- [ ] 策略回测功能
- [ ] Web UI 控制面板
- [ ] 实时监控面板
- [ ] 策略优化器

## 📄 许可证

MIT License

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 🚀 快速开始示例

```bash
# 1. 克隆项目
git clone https://github.com/yourusername/quant-flow.git
cd quant-flow

# 2. 安装依赖
uv sync

# 3. 配置
cp .env.example .env
cp config.yaml.example config.yaml
# 编辑 .env 和 config.yaml

# 4. 启动（测试模式）
python main.py
```

## 📚 文档

- [Bitget SDK 对比分析](./BITGET_SDK_COMPARISON.md) - 详细的 SDK 选择指南
- [依赖说明](./DEPENDENCIES.md) - 技术指标实现说明
- [更新日志](./CHANGELOG.md) - 版本更新历史

## 🔗 相关链接

- [LangChain 官方文档](https://python.langchain.com/)
- [Bitget API 文档](https://www.bitget.com/api-doc/)
- [CCXT 文档](https://docs.ccxt.com/)

---

**免责声明**: 本项目仅供学习和研究使用。作者不对使用本软件导致的任何损失负责。加密货币交易风险极高，请谨慎使用！
