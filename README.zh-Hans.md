中文 | **[English](README.md)**

<div align="center">

# Quant Flow

**基于 LangChain/LangGraph 的 AI 加密货币永续合约交易机器人，支持 Hyperliquid DEX**

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![LangChain](https://img.shields.io/badge/LangChain-latest-green.svg)](https://python.langchain.com/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![文档](https://img.shields.io/badge/文档-GitHub%20Pages-blue)](https://loadchange.github.io/quant-flow/zh-Hans/docs/intro)

[**📖 完整文档**](https://loadchange.github.io/quant-flow/zh-Hans/docs/intro) · [**English README**](README.md) · [**测试网水龙头**](https://app.hyperliquid-testnet.xyz/faucet)

</div>

> ⚠️ **免责声明**：本项目仅供学习研究使用。杠杆交易存在重大亏损风险，请先在测试网验证策略，生产环境使用需自行承担风险。

---

## 项目简介

Quant Flow 是基于 [Hyperliquid DEX](https://hyperliquid.xyz/) 的 AI 自动交易系统，构建在 LangChain/LangGraph 之上，支持两种独立的交易策略：

| 策略 | 入口 | 说明 |
|------|------|------|
| **永续合约 Agent** | `main.py` | 多 Agent 架构，每个交易对独立决策上下文 |
| **网格交易 Grid Flow** | `grid_main.py` | AI 驱动的动态网格做市，LLM 判断方向和宽度，数学引擎计算参数 |

## 核心功能

### 基础能力

- 🤖 **多 Agent 架构** — 每个交易对独立决策，互不干扰
- 🔌 **多 LLM 支持** — OpenAI、NVIDIA、Google、Cloudflare、LiteLLM
- 📊 **网格交易策略** — AI 驱动的动态网格做市
- 📐 **凯利公式仓位管理** — 动态计算最优仓位
- 🛡️ **ATR 动态止盈止损** — 波动率自适应风险管理
- 🔒 **账户保护** — 最大回撤限制、持仓超时机制
- 🔍 **决策验证** — 多周期趋势共振、信号质量评估
- 📈 **回测支持** — `single/grid` 双策略，支持中断恢复
- 🔄 **API 回退机制** — LLM 和 Hyperliquid API 双重回退

### AI 决策增强功能（基于论文研究）

| 功能 | 论文 | 配置开关 | 说明 |
|------|------|----------|------|
| **FinCoT 结构化推理** | [arXiv:2506.16123](https://arxiv.org/abs/2506.16123) | `prompt.set: nof1-improved` | 6步强制推理链，准确率 +17%，token 消耗 -8.9x |
| **多空辩论 Agent** | [arXiv:2412.20138](https://arxiv.org/abs/2412.20138) | `debate.enabled` | Bull/Bear 双 Agent 消除确认偏见 |
| **CEX 领先信号 + 链上数据** | [MDPI 2026](https://www.mdpi.com/2227-7390/14/2/346) | `enhanced_analysis.enabled` | Binance 资金费率、恐惧贪婪指数、MVRV/SOPR |
| **Regime 自适应策略** | [Springer 2025](https://link.springer.com/article/10.1007/s42521-024-00123-2) | `regime_adaptive.enabled` | 趋势/震荡/高波动三种市场状态动态调参 |
| **市场主动监控** | — | `market_monitor.enabled` | 独立线程，异常波动触发决策循环 |

所有增强功能通过配置文件**独立开关控制，默认关闭**，不影响现有流程。

## 快速开始

### Docker 部署（推荐）

```bash
# 1. 初始化（自动配置 UID/GID、创建目录）
bash init-deployment.sh

# 2. 配置
cp config.yaml.example config.yaml
cp config.grid.yaml.example config.grid.yaml  # 可选：网格模式
vim .env           # 填入 API 密钥和私钥
vim config.yaml    # 配置交易参数

# 3. 启动（默认运行主策略）
docker compose up -d

# 仅运行网格策略
RUN_MODE=grid docker compose up -d

# 同时运行主策略 + 网格策略
RUN_MODE=all docker compose up -d

# 查看日志
docker compose logs -f
```

### 本地开发

```bash
# 安装 uv（如未安装）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 安装依赖（需要 Python 3.11+）
uv sync

# 配置
cp .env.example .env
cp config.yaml.example config.yaml

# 运行主策略
uv run python main.py

# 运行网格策略
uv run python grid_main.py --config config.grid.yaml --env-file .env
```

## 配置说明

### 环境变量 (`.env`)

```bash
# LLM API（按 config.yaml 中的 client_type 选择配置）
NVIDIA_API_KEY=xxx
OPENAI_API_KEY=xxx
OPENAI_API_BASE=xxx

# Hyperliquid
HYPERLIQUID_PRIVATE_KEY=0x...   # 钱包私钥
HYPERLIQUID_TESTNET=true        # true=测试网，false=主网
```

> **钱包模式**：单钱包模式只填 `HYPERLIQUID_PRIVATE_KEY`；API 钱包代理模式额外填 `HYPERLIQUID_ACCOUNT_ADDRESS`，需在主钱包网页端授权。

### 交易配置 (`config.yaml`)

```yaml
llm:
  client_type: langchain_nvidia   # openai / cloudflare / google / litellm / nvidia
  model: deepseek-ai/deepseek-v3.2
  temperature: 0.2

trading:
  symbols: [BTC, ETH]
  max_trade_amount: 100           # 单笔上限（美元）
  max_leverage: 10

prompt:
  set: nof1-improved              # 推荐：集成 FinCoT 6步推理链

enhanced_analysis:
  enabled: true

debate:
  enabled: false                  # 启用后每次决策额外 2 次 LLM 调用

regime_adaptive:
  enabled: false                  # 依赖 enhanced_analysis: true

account_protection:
  enabled: true
  max_drawdown_pct: 0.10          # 最大回撤 10%
  max_daily_loss_pct: 0.05        # 单日亏损 5%

market_monitor:
  enabled: false
  alert_threshold_pct: 3.0        # 波动超过 3% 触发决策
```

完整配置参考 [`config.yaml.example`](config.yaml.example)。

## 回测

```bash
# 永续合约策略回测
uv run python backtest.py --symbol BTC --strategy single \
  --start-date 2024-01-01 --end-date 2024-12-01

# 网格策略回测
uv run python backtest.py --symbol BTC --strategy grid \
  --start-date 2024-01-01 --end-date 2024-12-01

# 从检查点恢复中断的回测
uv run python backtest.py \
  --resume-from backtest_results/backtest_BTC_xxx/live_report.json

# A/B 对比回测（验证各功能效果）
uv run python backtest_comparison.py --symbol BTC --compare all
uv run python backtest_comparison.py --symbol BTC --compare fincot
```

详细说明参考 [`BACKTEST_README.md`](BACKTEST_README.md)。

## 测试

```bash
uv run pytest tests/
uv run pytest tests/test_decision_validator.py -v
uv run pytest tests/ --cov=src
```

## 项目结构

```
quant-flow/
├── main.py                    # 主策略入口（永续合约 Agent）
├── grid_main.py               # 网格策略入口（Grid Flow）
├── backtest.py                # 回测运行器
├── backtest_comparison.py     # A/B 对比工具
├── src/
│   ├── agent/                 # Agent 实现
│   ├── trading/               # 交易核心（客户端、订单、风控）
│   ├── data/                  # 市场数据、指标、增强器
│   ├── llm/                   # LLM 客户端封装
│   ├── backtest/              # 回测引擎
│   └── notification/          # 通知模块
├── prompts/                   # 8 套 Prompt 策略模板
├── website/                   # Docusaurus 文档站点
└── tests/                     # 测试套件
```

## Docker 管理

```bash
docker compose up -d           # 启动
docker compose down            # 停止
docker compose logs -f         # 查看日志
docker compose ps              # 查看状态

# 更新到最新版本
git pull && docker compose build && docker compose up -d
```

**运行模式**（通过 `RUN_MODE` 环境变量控制）：
- `main` — 仅主交易策略（默认）
- `grid` — 仅网格交易策略
- `all` — 同时运行两种策略

## 常见问题

| 错误 | 解决方案 |
|------|----------|
| `PermissionError: /app/logs/...` | 运行 `bash init-deployment.sh` |
| `open interest is at cap` | 该资产达到开放利益上限，换其他交易对 |
| `Leverage exceeds maximum allowed` | 降低配置中的 `max_leverage` |
| API 钱包能查余额但不能下单 | 在主钱包网页端授权 API 钱包地址 |

## 相关链接

- 📖 [完整文档](https://loadchange.github.io/quant-flow/zh-Hans/docs/intro)
- 🏦 [Hyperliquid](https://hyperliquid.xyz/)
- 🚰 [测试网水龙头](https://app.hyperliquid-testnet.xyz/faucet)
- ⛓️ [LangChain](https://python.langchain.com/)
- 📊 [回测文档](BACKTEST_README.md)

---

免责声明：本项目仅供学习研究，生产环境使用需自行承担风险。
