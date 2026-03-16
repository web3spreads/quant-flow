---
sidebar_position: 1
title: Introduction
description: Quant Flow — AI-powered crypto perpetual futures trading bot for Hyperliquid DEX
---

:::info 中文文档
查看 GitHub 上的中文说明：[README.zh-Hans.md](https://github.com/loadchange/quant-flow/blob/main/README.zh-Hans.md)
:::

# Quant Flow

**Quant Flow** is an AI-powered cryptocurrency trading system built on [LangChain](https://langchain.com/) / [LangGraph](https://langchain-ai.github.io/langgraph/) for the [Hyperliquid DEX](https://hyperliquid.xyz/). It combines large language models with quantitative trading logic to make autonomous trading decisions on perpetual futures markets.

## What Is Quant Flow?

Quant Flow is **not** a simple rule-based bot. It uses LLMs as the decision engine, enriched with:

- Multi-timeframe technical indicators (MA, RSI, MACD, Bollinger Bands)
- Real-time CEX funding rate signals from Binance
- On-chain data: MVRV, SOPR, Fear & Greed Index
- Multi-agent debate (bull vs. bear) to eliminate confirmation bias
- Market regime detection (trending / ranging / volatile)
- A persistent review & reflection system that learns from past trades

## Two Independent Strategies

| Strategy | Entry Point | Description |
|---|---|---|
| **Perpetual Agent** | `main.py` | Multi-agent architecture, one independent agent per trading pair with context compression |
| **Grid Flow** | `grid_main.py` | AI-driven grid market-making; LLM judges direction & grid width, math engine computes parameters |

Both strategies are fully decoupled. You can run either or both simultaneously via Docker `RUN_MODE`.

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
| AI Framework | LangChain, LangGraph |
| Exchange SDK | Hyperliquid Python SDK |
| Data Validation | Pydantic |
| Package Manager | uv |
| Deployment | Docker / Docker Compose |

## Quick Start

```bash
# 1. Clone the repository
git clone https://github.com/loadchange/quant-flow
cd quant-flow

# 2. Install dependencies
uv sync

# 3. Configure environment
cp .env.example .env
# Edit .env with your API keys and Hyperliquid private key

# 4. Configure trading parameters
cp config.yaml.example config.yaml
# Edit config.yaml

# 5. Run (testnet recommended first)
uv run python main.py
```

:::warning Test on Testnet First
Always validate your configuration on Hyperliquid **testnet** before trading real funds. Set `HYPERLIQUID_TESTNET=true` in your `.env` file.
:::

## Repository

- **GitHub**: [https://github.com/loadchange/quant-flow](https://github.com/loadchange/quant-flow)

## Next Steps

- [Docker Deployment](./getting-started/docker.md) — Recommended production setup
- [Local Deployment](./getting-started/local.md) — Development and testing
- [Configuration Reference](./configuration/config-yaml.md) — All config options
- [Perpetual Agent Strategy](./strategies/perpetual-agent.md) — How the main agent works
- [Grid Flow Strategy](./strategies/grid-flow.md) — How grid trading works
