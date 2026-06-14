---
sidebar_position: 2
title: Local Deployment
description: Run Quant Flow locally for development and testing
---

# Local Deployment

Running locally is ideal for development, backtesting, and testing configuration changes without Docker overhead.

## Prerequisites

- Python 3.11 or higher
- [uv](https://docs.astral.sh/uv/) package manager (recommended) — or pip

### Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Setup

### 1. Clone and Enter the Repository

```bash
git clone https://github.com/web3spreads/quant-flow
cd quant-flow
```

### 2. Install Dependencies

```bash
# Install all runtime dependencies
uv sync

# Install with development dependencies (for testing, linting)
uv sync --group dev
```

### 3. Configure Environment

```bash
cp .env.example .env
```

Edit `.env`:

```bash
# LLM API Keys (use at least one)
OPENAI_API_KEY=sk-...
GOOGLE_API_KEY=...              # optional: Gemini
NVIDIA_API_KEY=nvapi-...        # optional: NVIDIA NIM

# Hyperliquid
HYPERLIQUID_PRIVATE_KEY=0x...
HYPERLIQUID_TESTNET=true        # strongly recommended for local testing
```

### 4. Configure Trading

```bash
cp config.yaml.example config.yaml
```

Minimal local config:

```yaml
llm:
  client_type: openai
  model: gpt-4o-mini
  temperature: 0.2

trading:
  perp_enabled: true
  grid_enabled: false
  symbols: [BTC]
  max_trade_amount: 10    # keep small for testing
  max_leverage: 2

scheduler:
  interval_minutes: 5
```

## Running the Bot

Run the unified trading bot:

```bash
uv run python main.py
```

With explicit config and env file:

```bash
uv run python main.py --config config.yaml --env-file .env
```

By toggling `perp_enabled` and `grid_enabled` in `config.yaml`, the runner will execute the Perpetual Futures agent, the Grid Flow market maker, or both concurrently.

## Running Tests

```bash
# Run all tests
uv run pytest tests/

# Run a specific test file
uv run pytest tests/test_decision_validator.py -v

# Run with coverage
uv run pytest tests/ --cov=src

# Run backtests (no live trading)
uv run python backtest.py --symbol BTC --strategy single \
  --start-date 2024-01-01 --end-date 2024-12-01
```

## Syntax Checking

```bash
# Check a specific module
uv run python -m py_compile src/trading/client.py

# Check all source files
find src -name "*.py" | xargs uv run python -m py_compile
```

## Adding Dependencies

```bash
# Add a runtime dependency
uv add requests

# Add a dev-only dependency
uv add --group dev pytest-mock
```

## Log Output

When running locally, logs are printed to stdout and also written to `logs/`:

```bash
tail -f logs/main.log    # perp / grid unified bot logs
```

## Testnet vs Mainnet

:::warning Always Start on Testnet
Set `HYPERLIQUID_TESTNET=true` in `.env` during development. Testnet uses the same interface as mainnet but with no real funds.

Switch to mainnet only after you've validated your configuration:
```bash
HYPERLIQUID_TESTNET=false
```
:::

## Troubleshooting

| Issue | Fix |
|---|---|
| `ModuleNotFoundError` | Run `uv sync` to reinstall dependencies |
| `PermissionError on logs/` | `mkdir -p logs && chmod 755 logs` |
| `Cannot connect to Hyperliquid` | Check `HYPERLIQUID_TESTNET` and network connectivity |
| API wallet can't trade | Authorize the API wallet address on the main Hyperliquid wallet page |

## Next Steps

- [Environment Variables](../configuration/env.md)
- [config.yaml Reference](../configuration/config-yaml.md)
- [Backtesting](../backtesting/single.md) — test strategies without live trading
