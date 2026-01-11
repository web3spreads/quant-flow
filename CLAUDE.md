# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

Quant Flow 是一个基于 LangChain/LangGraph 的 AI 永续合约自动交易机器人，专为 Hyperliquid DEX 设计。采用多 Agent 架构，每个交易对独立决策，支持上下文压缩以降低 Token 成本。

## 常用命令

```bash
# 安装依赖
pip install -e .

# 运行主程序
python main.py

# 指定配置文件运行
python main.py --config config.yaml --env .env

# 运行测试
pytest tests/

# 运行单个测试文件
pytest tests/test_agents_langgraph.py -v

# Docker 部署
docker-compose up -d
docker-compose logs -f
```

## 核心架构

### 多 Agent 协作流程

```
SingleSymbolAgent (BTC/ETH/...) → SummaryAgentV2 (历史压缩) → SpotAgent (现货定投)
         ↓                              ↓                           ↓
    独立决策上下文                   分层汇总压缩                  严格评估定投
```

### 关键模块

- **`main.py`**: 入口点，`QuantFlowBot` 类协调所有组件
- **`src/config.py`**: 配置管理，从 `config.yaml` 和 `.env` 加载配置
- **`src/llm/`**: LLM 客户端工厂，支持多种供应商 (OpenAI、Cloudflare、Google、LiteLLM、NVIDIA)

### Agent 模块 (`src/agent/`)

| Agent | 职责 |
|-------|------|
| `SingleSymbolAgent` | 单币种交易决策，每个交易对独立上下文 |
| `SummaryAgentV2` | 使用 LangChain 上下文压缩技术汇总历史决策 |
| `SpotAgent` | 现货定投决策，保守策略 |
| `ReviewAgent` | 复盘经验学习，生成结构化规则 |
| `ExecutionAgent` | 将决策意图转为工具调用 (structured output) |
| `ExternalInfoAgent` | 外部信息收集 (Exa API) |

### 交易模块 (`src/trading/`)

- **`client.py`**: Hyperliquid SDK 封装，支持单钱包和 API 钱包代理两种模式
- **`order_manager.py`**: 订单管理，包含止盈止损逻辑

### 数据模块 (`src/data/`)

- **`market_data.py`**: K线和市场数据获取
- **`indicators.py`**: 技术指标计算 (MA, RSI, MACD, Bollinger)
- **`data_enricher.py`**: 数据增强，为高级 Prompt 提供额外上下文

## Prompt 策略系统

项目支持多套 Prompt 策略，位于 `prompts/` 目录：

| 策略 | 说明 |
|------|------|
| `default` | 默认策略 |
| `conservative` | 保守策略 |
| `aggressive` | 激进策略 |
| `nof1` / `nof1-improved` | 增强版策略 |
| `realtime` / `realtime-eng` | 实时策略 |

通过 `config.yaml` 中的 `prompt.set` 切换策略。`PromptManager` 负责加载和渲染 Jinja2 模板。

## 配置结构

- **`.env`**: API 密钥和私钥 (OPENAI_API_KEY, HYPERLIQUID_PRIVATE_KEY 等)
- **`config.yaml`**: 交易参数、Agent 配置、Prompt 策略选择

主要配置项：
- `llm.client_type`: LLM 客户端类型
- `trading.symbols`: 交易对列表
- `trading.max_trade_amount`: 单笔交易上限
- `trading.max_leverage`: 最大杠杆
- `scheduler.interval_minutes`: 决策间隔
- `review_agent.enabled`: 是否启用复盘

## 设计模式

1. **工具回调模式**: `TradingTools` 通过回调函数将 LLM 决策映射到实际交易操作
2. **延迟导入**: `src/agent/__init__.py` 使用 `__getattr__` 延迟导入避免循环依赖
3. **单例模式**: `LLMClientManager` 和 `Config` 使用单例确保全局一致性
4. **结构化输出**: `ExecutionAgent` 使用 Pydantic 模型 (`ExecutionPlan`) 确保决策格式正确

## 测试

测试位于 `tests/` 目录，使用 pytest：
- `test_agents_langgraph.py`: Agent 架构测试
- `test_external_info_agent.py`: 外部信息收集测试
- `test_review_daily_logger.py`: 复盘日志测试

## 注意事项

- 代码和注释主要使用中文
- Hyperliquid 使用简单符号 (如 `BTC`, `ETH`)，不是交易对格式
- 测试网和主网通过 `HYPERLIQUID_TESTNET` 环境变量切换
- 手续费率从 Hyperliquid API 动态获取，失败时回退到默认 Tier0
