<div align="center">

# Quant Flow

**AI-powered crypto perpetual futures trading bot for Hyperliquid DEX**

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![LangChain](https://img.shields.io/badge/LangChain-latest-green.svg)](https://python.langchain.com/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Docs](https://img.shields.io/badge/Docs-GitHub%20Pages-blue)](https://loadchange.github.io/quant-flow/)

[**📖 Full Documentation**](https://loadchange.github.io/quant-flow/) · [**中文**](README.zh-Hans.md)

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

| Feature | Research Inspiration | Config | Description |
|---------|----------------------|--------|-------------|
| **FinCoT Reasoning** | FinCoT (arXiv:2506.16123) | `prompt.set: nof1-improved` | 6-step forced reasoning chain, +17% accuracy, -8.9x token cost |
| **Bull/Bear Debate** | TradingAgents (arXiv:2412.20138) | `debate.enabled` | Two agents debate bull/bear to eliminate confirmation bias |
| **CEX Signals + On-chain** | [MDPI Mathematics 14(2):346](https://www.mdpi.com/2227-7390/14/2/346) | `enhanced_analysis.enabled` | Binance funding rate, Fear&Greed, MVRV/SOPR signals |
| **Regime Adaptive** | [Springer Digital Finance](https://link.springer.com/article/10.1007/s42521-024-00123-2) | `regime_adaptive.enabled` | Dynamic params for trending/ranging/volatile market states |
| **Market Monitor** | — | `market_monitor.enabled` | Independent thread triggers decisions on volatility spikes |

> Papers listed are the academic research that inspired each feature's implementation.

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

[🇨🇳 中文说明 README.zh-Hans.md](README.zh-Hans.md)
