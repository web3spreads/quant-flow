---
sidebar_position: 99
title: FAQ & Troubleshooting
description: Frequently asked questions and troubleshooting guide for Quant Flow
---

# FAQ & Troubleshooting

## Installation & Setup

### Q: Which Python version is required?

Python **3.11 or higher** is required. LangGraph and some dependencies use features not available in earlier versions.

```bash
python --version    # should show 3.11+
```

### Q: Can I use pip instead of uv?

Yes, but uv is strongly recommended for consistent dependency resolution. If using pip:

```bash
pip install -r requirements.txt    # if a requirements.txt is provided
# or
pip install -e .
```

### Q: How do I install on Windows?

Quant Flow is tested on Linux and macOS. Windows should work via WSL2 (Windows Subsystem for Linux). Native Windows is not officially supported.

---

## Configuration

### Q: What format should symbols be in?

Use simple asset symbols — **not** trading pair format:

```yaml
# ✅ Correct
trading:
  symbols: [BTC, ETH, SOL]

# ❌ Wrong
trading:
  symbols: [BTC/USDT, ETH-PERP]
```

Hyperliquid uses simple asset names internally.

### Q: Which LLM should I use?

For trading decisions, use a model that:
- Supports reliable JSON output (structured outputs)
- Has low latency (< 5s response time recommended)
- Is cost-effective at scale

Recommended options:

| Model | Provider | Config `client_type` | Notes |
|---|---|---|---|
| `qwen/qwen3.5-122b-a10b` | NVIDIA NIM | `langchain_nvidia` | Good balance of speed/quality (MoE, supports tools) |
| `gpt-4o-mini` | OpenAI | `openai` | Fast and cheap |
| `gpt-4o` | OpenAI | `openai` | Higher quality, higher cost |
| `gemini-1.5-flash` | Google | `google` | Fast, generous free tier |

:::tip Model availability changes over time
NVIDIA NIM rotates models periodically. Pick any model your provider currently supports — the system is provider-agnostic.
:::

### Q: How do I switch between testnet and mainnet?

Set `HYPERLIQUID_TESTNET` in `.env`:

```bash
HYPERLIQUID_TESTNET=true    # testnet
HYPERLIQUID_TESTNET=false   # mainnet (real funds)
```

---

## Errors & Issues

### `PermissionError: Permission denied: '/app/logs/...'`

The `logs/` directory has incorrect permissions. Run:

```bash
bash init-deployment.sh
```

This script creates and sets permissions on `logs/`, `data/`, and `backtest_results/`.

### `Cannot increase position when open interest is at cap`

The asset has reached Hyperliquid's open interest limit. Solutions:
1. Switch to a different trading pair
2. Wait for OI to decrease naturally
3. Trade smaller size (may still fail if cap is per-user)

### `Leverage exceeds maximum allowed`

Different assets have different maximum leverage on Hyperliquid. For example, some altcoins may have a max of 5× while BTC/ETH support 50×.

Fix: Reduce `max_leverage` in `config.yaml`:

```yaml
trading:
  max_leverage: 5    # lower this value
```

### `API wallet can query balance but cannot place orders`

Your API wallet needs to be authorized on the main Hyperliquid interface:

1. Open [app.hyperliquid.xyz](https://app.hyperliquid.xyz)
2. Connect your **main wallet** (not the API wallet)
3. Go to **Settings → API Wallets**
4. Add your API wallet address and authorize it

### `ModuleNotFoundError: No module named 'langchain_...'`

Dependencies are not installed. Run:

```bash
uv sync
```

### LLM returns invalid JSON

The LLM occasionally returns malformed JSON for the `ExecutionPlan`. The `ExecutionAgent` handles this with retries. If it persists:
- Lower `temperature` to `0.1`
- Use a model with better structured output support (GPT-4o, DeepSeek V3)
- Check if your model supports function calling / JSON mode

---

## Trading Behavior

### Q: Why is the bot holding (not trading)?

Common reasons:
1. **Validation blocked the trade** — check logs for `BLOCK` decisions from `DecisionValidator`
2. **Account protection triggered** — check if daily loss or drawdown limit was hit
3. **Confidence too low** — the LLM returned a confidence below the threshold for the current regime
4. **Market state** — `decision_validator` avoids certain market states (e.g., `UNCERTAIN`)

Check the logs:
```bash
tail -f logs/main.log | grep -i "block\|hold\|skip\|protection"
```

### Q: Can I run multiple symbols simultaneously?

Yes. List all symbols in `trading.symbols`:

```yaml
trading:
  symbols: [BTC, ETH, SOL]
```

Each symbol runs with an independent agent and context. They share the same LLM client but have separate conversation histories and experience databases.

### Q: How does the bot handle internet outages?

If the Hyperliquid API is unreachable:
- The current decision cycle fails and is retried on the next scheduler tick
- Existing open positions remain open (the bot cannot cancel or close them while offline)
- Market monitor stops alerting (no price data)

:::warning
Ensure your hosting environment has reliable internet. Consider using a VPS in a data center rather than a home network.
:::

### Q: Can I run both perpetual agent and grid simultaneously?

Yes, use Docker with `RUN_MODE=all`:

```yaml
# docker-compose.yml
environment:
  - RUN_MODE=all
```

Both strategies run under `supervisord` and log to separate files (`logs/main.log`, `logs/grid.log`).

---

## Risk & Safety

### Q: Is there a maximum loss protection?

Yes — `protections` (plugin-based, each independently togglable):

```yaml
protections:
  - name: max_drawdown
    max_drawdown_pct: 0.10    # close all + pause when drawdown ≥ 10% from peak
    pause_hours: 4
  - name: daily_loss
    max_daily_loss_pct: 0.05  # pause new trades when daily loss ≥ 5%
    pause_hours: 4
  - name: consecutive_loss
    max_consecutive_losses: 5
    per_symbol: true          # lock only the losing symbol
    pause_hours: 4
  - name: position_timeout
    max_position_hours: 48    # force-close any position held > 48 hours
```

The legacy `account_protection: { enabled: true, ... }` block is still
auto-migrated for backward compatibility.

:::warning
Never disable all protection plugins without understanding the consequences. A runaway LLM or API error could otherwise accumulate unlimited losses.
:::

### Q: What happens if the stop-loss order fails?

`HyperliquidClient` has a critical fallback:

> If placing a stop-loss fails after an order is already open, the system immediately attempts to close the position via market order — up to 3 retries.

This prevents running an open position with no downside protection.

### Q: Should I use testnet before mainnet?

**Yes, always.** Set `HYPERLIQUID_TESTNET=true` and run for at least 24–48 hours to verify:
- Correct order placement
- Stop-loss and take-profit behavior
- Account protection triggers
- Log output is sensible

Only switch to mainnet (`HYPERLIQUID_TESTNET=false`) after successful testnet validation.

---

## Getting Help

- **GitHub Issues**: [https://github.com/web3spreads/quant-flow/issues](https://github.com/web3spreads/quant-flow/issues)
- **Logs**: Always include relevant log excerpts when reporting issues (`logs/main.log`)
