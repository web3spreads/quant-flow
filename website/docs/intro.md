---
sidebar_position: 1
title: Introduction
description: Quant Flow — AI-powered crypto perpetual futures & grid trading bot for Hyperliquid DEX
---

# Quant Flow

**Quant Flow** is an AI-powered cryptocurrency trading system built on [Pydantic AI](https://ai.pydantic.dev) for the [Hyperliquid DEX](https://hyperliquid.xyz/). It combines large language models with quantitative trading logic to make autonomous trading decisions on perpetual futures and grid market-making.

## What Is Quant Flow?

Quant Flow is **not** a simple rule-based bot. It uses LLMs as the decision engine, enriched with:

- Multi-timeframe technical indicators (MA, RSI, MACD, Bollinger Bands)
- Real-time CEX funding rate signals from Binance
- On-chain data: MVRV, SOPR, Fear & Greed Index
- Multi-agent debate (bull vs. bear) to eliminate confirmation bias
- Market regime detection (trending / ranging / volatile)
- A persistent review & reflection system that learns from past trades

## Two Integrated Strategies

Both strategies are unified into a single runner program (`main.py`) and can be enabled or disabled via configuration flags in `config.yaml`.

| Strategy | Configuration Flag | Description |
|---|---|---|
| **Perpetual Agent** | `trading.perp_enabled` | Multi-agent architecture, one independent agent per trading pair with context compression |
| **Grid Flow** | `trading.grid_enabled` | AI-driven grid market-making; LLM judges direction & grid width, math engine computes parameters |

## Key Features

- 🤖 **LLM-driven decisions** — GPT-4, DeepSeek, Gemini, Claude, or any OpenAI-compatible API
- 📊 **FinCoT reasoning** — 6-step structured chain-of-thought (+17.3% accuracy, −8.9× tokens vs. vanilla prompting)
- ⚔️ **Bull/Bear debate** — Two independent agents argue long and short before every trade
- 🌐 **CEX leading signals** — Binance funding rate divergence as a leading indicator
- 📡 **Market regime adaptation** — Dynamic parameter switching based on market state
- 🚨 **Active market monitoring** — Independent thread detects volatility spikes and triggers decisions
- 🧠 **6-layer review system** — Instant reflection, weekly reflection, regime-aware memory, bias protection, and more
- 🛡️ **Account protection** — Max drawdown circuit breaker, daily loss limit, position timeout
- ⚡ **Grid trading** — AI-guided grid market-making with layered reduce-only exit orders

## Tech Stack

| Component | Technology |
|---|---|
| Language | Python 3.11+ |
| AI Framework | Pydantic AI |
| Exchange SDK | Hyperliquid Python SDK |
| Data Validation | Pydantic |
| Package Manager | uv |
| Deployment | Docker / Docker Compose |

## Quick Start

```bash
# 1. Clone the repository
git clone https://github.com/web3spreads/quant-flow
cd quant-flow

# 2. Install dependencies
uv sync

# 3. Configure environment
cp .env.example .env
# Edit .env with your API keys and Hyperliquid private key

# 4. Configure trading parameters
cp config.yaml.example config.yaml
# Edit config.yaml (set perp_enabled or grid_enabled)

# 5. Run (testnet recommended first)
uv run python main.py
```

:::warning Test on Testnet First
Always validate your configuration on Hyperliquid **testnet** before trading real funds. Set `HYPERLIQUID_TESTNET=true` in your `.env` file.
:::

## Repository

- **GitHub**: [https://github.com/web3spreads/quant-flow](https://github.com/web3spreads/quant-flow)

## Next Steps

- [Docker Deployment](./getting-started/docker.md) — Recommended production setup
- [Local Deployment](./getting-started/local.md) — Development and testing
- [Configuration Reference](./configuration/config-yaml.md) — All config options
- [Perpetual Agent Strategy](./strategies/perpetual-agent.md) — How the main agent works
- [Grid Flow Strategy](./strategies/grid-flow.md) — How grid trading works
