<div align="center">

# Quant Flow

**AI-powered crypto perpetual futures trading bot for Hyperliquid DEX**

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![LangChain](https://img.shields.io/badge/LangChain-latest-green.svg)](https://python.langchain.com/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Docs](https://img.shields.io/badge/Docs-GitHub%20Pages-blue)](https://loadchange.github.io/quant-flow/)

[**📖 Full Documentation**](https://loadchange.github.io/quant-flow/) · [**中文文档**](https://loadchange.github.io/quant-flow/zh-Hans/docs/intro) · [**Testnet Faucet**](https://app.hyperliquid-testnet.xyz/faucet)

</div>

> ⚠️ **Disclaimer**: This project is for educational and research purposes only. Leveraged trading carries substantial risk of loss. Always test on testnet before using real funds.

---

## What is Quant Flow?

Quant Flow is an AI-powered automated trading system for [Hyperliquid DEX](https://hyperliquid.xyz/), built on LangChain/LangGraph. It supports two independent trading strategies:

| Strategy | Entry Point | Description |
|----------|-------------|-------------|
| **Perpetual Agent** | `main.py` | Multi-agent architecture with one independent decision context per trading pair |
| **Grid Flow** | `grid_main.py` | AI-driven grid market making — LLM judges direction & width, math engine calculates params |

## Key Features

### Core Capabilities

- 🤖 **Multi-Agent Architecture** — Independent decision-making per trading pair
- 🔌 **Multi-LLM Support** — OpenAI, NVIDIA, Google, Cloudflare, LiteLLM
- 📊 **Grid Flow Strategy** — AI-driven dynamic grid market making
- 📐 **Kelly Formula Position Sizing** — Dynamic optimal position calculation
- 🛡️ **ATR Dynamic Stop-Loss/Take-Profit** — Volatility-adaptive risk management
- 🔒 **Account Protection** — Max drawdown limits, position timeout
- 🔍 **Decision Validation** — Multi-timeframe trend resonance, signal quality
- 📈 **Backtesting** — `single/grid` strategies with checkpoint resume
- 🔄 **API Fallback** — LLM and Hyperliquid API fallback mechanisms

### AI Decision Enhancements (Research-Backed)

| Feature | Paper | Config | Description |
|---------|-------|--------|-------------|
| **FinCoT Reasoning** | [arXiv:2506.16123](https://arxiv.org/abs/2506.16123) | `prompt.set: nof1-improved` | 6-step forced reasoning chain, +17% accuracy, -8.9x token cost |
| **Bull/Bear Debate** | [arXiv:2412.20138](https://arxiv.org/abs/2412.20138) | `debate.enabled` | Two agents debate bull/bear to eliminate confirmation bias |
| **CEX Signals + On-chain** | [MDPI 2026](https://www.mdpi.com/2227-7390/14/2/346) | `enhanced_analysis.enabled` | Binance funding rate, Fear&Greed, MVRV/SOPR signals |
| **Regime Adaptive** | [Springer 2025](https://link.springer.com/article/10.1007/s42521-024-00123-2) | `regime_adaptive.enabled` | Dynamic params for trending/ranging/volatile market states |
| **Market Monitor** | — | `market_monitor.enabled` | Independent thread triggers decisions on volatility spikes |

All enhancements are **controlled by independent config flags** and are **off by default**.

## Quick Start

### Docker (Recommended)

```bash
# 1. Initialize (auto-configure UID/GID, create directories)
bash init-deployment.sh

# 2. Configure
cp config.yaml.example config.yaml
cp config.grid.yaml.example config.grid.yaml  # optional, for grid mode
vim .env           # API keys and private key
vim config.yaml    # trading parameters

# 3. Start (main strategy by default)
docker compose up -d

# Run grid strategy only
RUN_MODE=grid docker compose up -d

# Run both strategies simultaneously
RUN_MODE=all docker compose up -d

# View logs
docker compose logs -f
```

### Local Development

```bash
# Install uv (if not installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies (Python 3.11+ required)
uv sync

# Configure
cp .env.example .env
cp config.yaml.example config.yaml

# Run main strategy
uv run python main.py

# Run grid strategy
uv run python grid_main.py --config config.grid.yaml --env-file .env
```

## Configuration

### Environment Variables (`.env`)

```bash
# LLM API (configure based on client_type in config.yaml)
NVIDIA_API_KEY=xxx
OPENAI_API_KEY=xxx
OPENAI_API_BASE=xxx

# Hyperliquid
HYPERLIQUID_PRIVATE_KEY=0x...   # wallet private key
HYPERLIQUID_TESTNET=true        # true=testnet, false=mainnet
```

> **Wallet modes**: Single wallet (fill `HYPERLIQUID_PRIVATE_KEY` only) or API wallet proxy (also fill `HYPERLIQUID_ACCOUNT_ADDRESS`, requires authorization on the main wallet webpage).

### Trading Config (`config.yaml`)

```yaml
llm:
  client_type: langchain_nvidia   # openai / cloudflare / google / litellm / nvidia
  model: deepseek-ai/deepseek-v3.2
  temperature: 0.2

trading:
  symbols: [BTC, ETH]
  max_trade_amount: 100
  max_leverage: 10

prompt:
  set: nof1-improved   # recommended: integrates FinCoT 6-step reasoning

enhanced_analysis:
  enabled: true

debate:
  enabled: false       # +2 LLM calls per decision

regime_adaptive:
  enabled: false       # requires enhanced_analysis: true

account_protection:
  enabled: true
  max_drawdown_pct: 0.10
  max_daily_loss_pct: 0.05

market_monitor:
  enabled: false
  alert_threshold_pct: 3.0
```

See [`config.yaml.example`](config.yaml.example) for the full reference.

## Backtesting

```bash
# Single agent backtest
uv run python backtest.py --symbol BTC --strategy single \
  --start-date 2024-01-01 --end-date 2024-12-01

# Grid strategy backtest
uv run python backtest.py --symbol BTC --strategy grid \
  --start-date 2024-01-01 --end-date 2024-12-01

# Resume from checkpoint
uv run python backtest.py --resume-from backtest_results/backtest_BTC_xxx/live_report.json

# A/B comparison (test effect of specific features)
uv run python backtest_comparison.py --symbol BTC --compare all
uv run python backtest_comparison.py --symbol BTC --compare fincot
```

See [`BACKTEST_README.md`](BACKTEST_README.md) for full backtest documentation.

## Testing

```bash
uv run pytest tests/
uv run pytest tests/test_decision_validator.py -v
uv run pytest tests/ --cov=src
```

## Project Structure

```
quant-flow/
├── main.py                    # Main strategy entry
├── grid_main.py               # Grid strategy entry
├── backtest.py                # Backtest runner
├── backtest_comparison.py     # A/B comparison tool
├── src/
│   ├── agent/                 # Agent implementations
│   ├── trading/               # Trading core (client, orders, risk)
│   ├── data/                  # Market data, indicators, enricher
│   ├── llm/                   # LLM client wrappers
│   ├── backtest/              # Backtest engine
│   └── notification/          # Notification module
├── prompts/                   # 8 prompt strategy sets
├── website/                   # Docusaurus documentation site
└── tests/                     # Test suite
```

## Docker Management

```bash
docker compose up -d           # Start
docker compose down            # Stop
docker compose logs -f         # Logs
docker compose ps              # Status

# Update
git pull && docker compose build && docker compose up -d
```

## Troubleshooting

| Error | Solution |
|-------|----------|
| `PermissionError: /app/logs/...` | Run `bash init-deployment.sh` |
| `open interest is at cap` | Asset hit OI cap, use a different trading pair |
| `Leverage exceeds maximum allowed` | Lower `max_leverage` in config |
| API wallet can't trade | Authorize the API wallet on the main wallet webpage |

## Links

- 📖 [Full Documentation](https://loadchange.github.io/quant-flow/)
- 🏦 [Hyperliquid](https://hyperliquid.xyz/)
- 🚰 [Testnet Faucet](https://app.hyperliquid-testnet.xyz/faucet)
- ⛓️ [LangChain](https://python.langchain.com/)

---

<details>
<summary>🇨🇳 中文说明</summary>

## 简介

Quant Flow 是基于 LangChain/LangGraph 的 AI 加密货币自动交易系统，专为 [Hyperliquid DEX](https://hyperliquid.xyz/) 设计，支持两种独立的交易策略：

- **永续合约 Agent**（`main.py`）：多 Agent 架构，每个交易对独立决策上下文
- **网格交易 Grid Flow**（`grid_main.py`）：AI 驱动的网格做市策略，LLM 判断方向和宽度

## 快速开始

### Docker 部署

```bash
bash init-deployment.sh        # 初始化
vim .env && vim config.yaml    # 配置 API 密钥和交易参数
docker compose up -d           # 启动主策略
RUN_MODE=grid docker compose up -d   # 仅网格
RUN_MODE=all docker compose up -d    # 同时运行
```

### 本地运行

```bash
uv sync                        # 安装依赖（需要 Python 3.11+）
uv run python main.py          # 主策略
uv run python grid_main.py --config config.grid.yaml --env-file .env  # 网格策略
```

## AI 决策增强功能

| 功能 | 论文 | 开关 | 说明 |
|------|------|------|------|
| FinCoT 结构化推理 | [arXiv:2506.16123](https://arxiv.org/abs/2506.16123) | `prompt.set: nof1-improved` | 6步推理链，准确率 +17%，token -8.9x |
| 多空辩论 | [arXiv:2412.20138](https://arxiv.org/abs/2412.20138) | `debate.enabled` | 双 Agent 消除确认偏见 |
| CEX 领先信号 | [MDPI 2026](https://www.mdpi.com/2227-7390/14/2/346) | `enhanced_analysis.enabled` | Binance 资金费率、恐惧贪婪、MVRV/SOPR |
| Regime 自适应 | [Springer 2025](https://link.springer.com/article/10.1007/s42521-024-00123-2) | `regime_adaptive.enabled` | 趋势/震荡/高波动三态动态调参 |
| 市场主动监控 | — | `market_monitor.enabled` | 独立线程，异常波动触发决策循环 |

所有功能通过配置文件独立开关控制，默认关闭。

## 回测

```bash
uv run python backtest.py --symbol BTC --strategy single --start-date 2024-01-01 --end-date 2024-12-01
uv run python backtest.py --symbol BTC --strategy grid --start-date 2024-01-01 --end-date 2024-12-01
uv run python backtest_comparison.py --symbol BTC --compare all
```

## 风险提示

- 先在测试网验证策略，主网从小金额开始
- 杠杆交易有爆仓风险，私钥泄露等于资产丢失
- 本项目仅供学习研究，生产环境使用需自行承担风险

📖 [完整中文文档](https://loadchange.github.io/quant-flow/zh-Hans/docs/intro)

</details>
