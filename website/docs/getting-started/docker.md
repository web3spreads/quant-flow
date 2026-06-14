---
sidebar_position: 1
title: Docker Deployment
description: Deploy Quant Flow using Docker Compose for production use
---

# Docker Deployment

Docker Compose is the recommended way to run Quant Flow in production. It handles process supervision, log management, and automatic restarts.

## Prerequisites

- Docker 24+ and Docker Compose v2
- A funded Hyperliquid account
- API keys for at least one LLM provider (OpenAI, DeepSeek, NVIDIA NIM, etc.)

## Setup

### 1. Clone the Repository

```bash
git clone https://github.com/web3spreads/quant-flow
cd quant-flow
```

### 2. Initialize Deployment

Run the initialization script to create required directories and set proper permissions:

```bash
bash init-deployment.sh
```

This creates `logs/`, `data/`, and `backtest_results/` with correct permissions.

:::tip
If you later see `PermissionError: Permission denied: '/app/logs/...'`, re-run `bash init-deployment.sh`.
:::

### 3. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```bash
# LLM Provider
OPENAI_API_KEY=sk-...
# OR for NVIDIA NIM
NVIDIA_API_KEY=nvapi-...

# Hyperliquid
HYPERLIQUID_PRIVATE_KEY=0x...
HYPERLIQUID_TESTNET=true   # set to false for mainnet
```

### 4. Configure Trading Parameters

```bash
cp config.yaml.example config.yaml
```

Set at minimum:

```yaml
llm:
  client_type: openai       # or langchain_nvidia, google, etc.
  model: gpt-4o-mini

trading:
  symbols: [BTC]            # start with one symbol
  max_trade_amount: 50      # USD per trade
  max_leverage: 3           # conservative leverage
```

See [config.yaml Reference](../configuration/config-yaml.md) for all options.

### 5. Choose Run Mode

Set `RUN_MODE` in `docker-compose.yml` or as an environment variable:

| `RUN_MODE` | What Runs |
|---|---|
| `main` (default) | Perpetual futures agent only |
| `grid` | Grid trading only |
| `all` | Both strategies simultaneously |

```yaml
# docker-compose.yml
environment:
  - RUN_MODE=main
```

For grid mode, enable it in the same `config.yaml` — set `trading.grid_enabled: true` (and `trading.perp_enabled: false` for grid-only). No separate grid config file is required.

## Running

```bash
# Start in background
docker compose up -d

# View logs
docker compose logs -f

# View strategy-specific logs
tail -f logs/main.log
tail -f logs/grid.log

# Stop
docker compose down
```

## Health Checks

```bash
# Check container status
docker compose ps

# Check recent logs
docker compose logs --tail=50

# Restart a single service
docker compose restart quant-flow
```

## Updating

```bash
git pull
docker compose build
docker compose up -d
```

## File Structure After Deployment

```
quant-flow/
├── logs/
│   ├── main.log        # perpetual agent logs
│   └── grid.log        # grid trading logs
├── data/
│   └── *.json          # market data cache
├── backtest_results/   # backtest output
├── grid_state.json     # grid manager state (persisted)
└── .env                # credentials (never commit this)
```

:::danger Keep Your Private Key Safe
Never commit `.env` to version control. The repository's `.gitignore` excludes it, but double-check before pushing to any remote.
:::

## Docker Compose Reference

The `docker-compose.yml` uses `supervisord` internally to manage multiple processes when `RUN_MODE=all`. Log output from each strategy is tee'd to the respective log file under `logs/`.

## Next Steps

- [Environment Variables](../configuration/env.md) — Full `.env` reference
- [config.yaml Reference](../configuration/config-yaml.md) — All trading parameters
- [Grid Config](../configuration/grid-config.md) — Grid trading configuration
