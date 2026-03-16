---
sidebar_position: 1
title: Environment Variables
description: Complete reference for all .env configuration variables
---

# Environment Variables

All secrets and environment-specific settings are stored in `.env`. This file is excluded from version control by `.gitignore`.

```bash
cp .env.example .env
```

## LLM Provider Keys

At least one LLM API key is required. The key used depends on `client_type` in `config.yaml`.

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | If using OpenAI | OpenAI API key (`sk-...`) |
| `CLOUDFLARE_ACCOUNT_ID` | If using Cloudflare | Cloudflare account ID |
| `CLOUDFLARE_API_TOKEN` | If using Cloudflare | Cloudflare Workers AI token |
| `GOOGLE_API_KEY` | If using Gemini | Google AI Studio API key |
| `NVIDIA_API_KEY` | If using NVIDIA NIM | NVIDIA NIM API key (`nvapi-...`) |

:::info LiteLLM Proxy
If you use `client_type: litellm`, set `OPENAI_API_KEY` or configure the LiteLLM proxy URL in `config.yaml`.
:::

## Hyperliquid Credentials

| Variable | Required | Description |
|---|---|---|
| `HYPERLIQUID_PRIVATE_KEY` | **Yes** | Ethereum private key for signing orders (`0x...`) |
| `HYPERLIQUID_TESTNET` | No | `true` to use testnet, `false` for mainnet (default: `false`) |

:::danger Private Key Security
- Never commit `.env` to any repository
- Never share your private key with anyone
- Loss of the private key means permanent loss of funds
- Use a dedicated trading wallet, not your main holdings wallet
:::

### API Wallet Authorization

Hyperliquid supports API wallets (sub-wallets) for programmatic trading. If you are using an API wallet address:

1. The API wallet can query balances but **cannot trade** until authorized
2. Go to the Hyperliquid web interface with your **main wallet**
3. Navigate to **Settings → API Wallets**
4. Authorize the API wallet address

:::tip
Using an API wallet is safer than using your main wallet's private key directly — you can revoke API wallet access at any time from the web UI.
:::

## Optional: External Data APIs

These APIs are used by the [CEX Signals & On-chain Data](../features/cex-signals.md) feature. All are optional — the system degrades gracefully if they're unavailable.

| Variable | Description |
|---|---|
| `EXA_API_KEY` | [Exa](https://exa.ai) API key for the ExternalInfoAgent (market news) |

:::info Auto-Degradation
If external API calls fail (network error, rate limit, missing key), the system logs a warning and continues without that data source. Trading is not blocked.
:::

## Example `.env` File

```bash
# ── LLM ────────────────────────────────────────────────────────────
OPENAI_API_KEY=sk-proj-xxxxxxxxxxxxxxxxxxxx

# ── Hyperliquid ────────────────────────────────────────────────────
HYPERLIQUID_PRIVATE_KEY=0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890
HYPERLIQUID_TESTNET=true

# ── Optional External Data ─────────────────────────────────────────
EXA_API_KEY=exa-xxxxxxxxxxxxxxxxxxxx
```

## Next Steps

- [config.yaml Reference](./config-yaml.md) — Trading parameters and feature toggles
- [Docker Deployment](../getting-started/docker.md) — Pass `.env` via Docker Compose
