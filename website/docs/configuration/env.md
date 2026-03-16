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

### API Wallet Authorization

Hyperliquid supports **API wallets** (sub-wallets) for programmatic trading. This is the **recommended approach** — it keeps your main wallet's private key safe and lets you revoke access at any time.

#### Step 1 — Create an API Wallet

1. Open [https://app.hyperliquid.xyz/API](https://app.hyperliquid.xyz/API) with your **main wallet** connected
2. Click **Generate API Wallet** — Hyperliquid will create a new key pair
3. Copy and save the **private key** shown (it will not be shown again)
4. The API wallet address will now appear in your API Wallets list

:::tip Testnet
For testnet, use [https://app.hyperliquid-testnet.xyz/API](https://app.hyperliquid-testnet.xyz/API). Get free test funds from the [Testnet Faucet](https://app.hyperliquid-testnet.xyz/faucet).
:::

#### Step 2 — Configure `.env`

Set the API wallet's private key in your `.env`:

```bash
HYPERLIQUID_PRIVATE_KEY=0x<api-wallet-private-key>
HYPERLIQUID_TESTNET=true   # set to false for mainnet
```

#### Step 3 — Fund the API Wallet

Transfer USDC from your main wallet to the API wallet address on Hyperliquid. The API wallet needs funds to place orders.

:::danger Private Key Security
- Never commit `.env` to any repository
- Never share your private key with anyone
- Loss of the private key means permanent loss of funds
- Use a dedicated trading wallet, not your main holdings wallet
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
