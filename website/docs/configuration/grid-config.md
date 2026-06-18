---
sidebar_position: 3
title: Grid Mode Config
description: Configuration reference for grid trading in config.yaml
---

# Grid Trading Configuration

The grid trading strategy is configured directly in the unified `config.yaml` file and is enabled by setting `trading.grid_enabled` to `true`.

## Trading Parameters

```yaml
trading:
  # Strategy toggles
  perp_enabled: false            # Disable/enable perpetual futures agent
  grid_enabled: true             # Enable grid market-making

  symbols: [ETH]                 # trading pair(s) for grid trading (currently supports first symbol in list)
  max_trade_amount: 500          # total capital allocated (USD)
  max_leverage: 5                # maximum leverage

  # Grid order features
  grid_limit_order_take_profit_enabled: true   # place TP after grid fill
  grid_limit_order_stop_loss_enabled: true     # place SL after grid fill
  grid_reduce_only_exit_orders_enabled: true   # layered reduce-only exit orders
```

### Exit Order Modes

| Option | Description |
|---|---|
| `grid_limit_order_take_profit_enabled` | After each grid level fills, place a limit take-profit order at the next grid boundary |
| `grid_limit_order_stop_loss_enabled` | After each grid level fills, place a stop-loss order below the entry |
| `grid_reduce_only_exit_orders_enabled` | Use reduce-only layered exit orders instead of full close — reduces slippage on large positions |

:::tip
Enable all three exit order modes for full protection. `grid_reduce_only_exit_orders_enabled` is especially important for larger position sizes.
:::

## Agent (AI Decision) Parameters

```yaml
agent:
  grid_width:
    min_pct: 0.02           # minimum grid spacing as % of price (2%)
    max_pct: 0.15           # maximum grid spacing (15%)
    fallback_pct: 0.05      # fallback if data is unavailable (5%)
    ai_blend_weight: 0.35   # weight given to AI output vs. market data
```

### Grid Width Blending

The final grid width is a weighted combination:

```
final_width = (market_data_width × 0.65) + (ai_suggested_width × 0.35)
```

The market data component uses ATR and volatility to compute a data-driven width. The AI component allows the LLM to adjust based on broader context (news, sentiment, regime). The 65/35 blend ensures the math engine dominates while AI adds directional nuance.

## LLM Configuration

```yaml
llm:
  client_type: langchain_openai
  model: qwen/qwen3.5-122b-a10b
  temperature: 0.3               # slightly higher for grid — more creative width estimation
```

## Scheduler

```yaml
scheduler:
  interval_minutes: 5    # grid re-evaluation interval
```

Grid decisions run every 5 minutes. Between intervals, the GridManager monitors fills and places exit orders automatically — no LLM call required for fill handling.

## Data Configuration

```yaml
data:
  timeframe: 15m    # shorter timeframe for grid — more responsive to price action
```

:::info
Grid trading benefits from shorter timeframes (15m, 5m) compared to the perpetual agent (1h). This gives more granular volatility estimates for grid spacing.
:::

## Complete Grid Example

```yaml
llm:
  client_type: langchain_openai
  model: qwen/qwen3.5-122b-a10b
  temperature: 0.3

trading:
  perp_enabled: false
  grid_enabled: true
  symbols: [ETH]
  max_trade_amount: 500
  max_leverage: 5
  grid_limit_order_take_profit_enabled: true
  grid_limit_order_stop_loss_enabled: true
  grid_reduce_only_exit_orders_enabled: true

agent:
  grid_width:
    min_pct: 0.02
    max_pct: 0.15
    fallback_pct: 0.05
    ai_blend_weight: 0.35

scheduler:
  interval_minutes: 5

data:
  timeframe: 15m
```

## Grid State Persistence

The grid manager saves its state to `grid_state.json` after every order operation. The file is written **atomically** (via tempfile + rename) to prevent corruption on unexpected shutdowns.

If you restart the bot, it reads `grid_state.json` and resumes from the last known state — active orders are reconciled with the exchange.

:::warning Do Not Edit grid_state.json Manually
Editing `grid_state.json` directly can cause the grid manager to lose track of active orders, potentially resulting in orphan orders on the exchange.
:::

## Safety Mechanisms

The GridManager includes several built-in safety features:

- **Orphan trigger cleanup** — detects and cancels trigger orders with no corresponding position
- **Reduce-only layered exit** — exits large positions gradually to minimize market impact
- **Cancel hard timeout (20s)** — if a cancel order doesn't confirm within 20 seconds, retries and alerts
- **Atomic state writes** — prevents state file corruption on process interruption

## Next Steps

- [Grid Flow Strategy](../strategies/grid-flow.md) — How the grid trading algorithm works
- [Grid Backtesting](../backtesting/grid.md) — Test grid config before live trading
