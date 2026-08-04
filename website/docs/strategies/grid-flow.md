---
sidebar_position: 2
title: Grid Flow Strategy
description: How the AI-driven grid market-making strategy works
---

# Grid Flow Strategy

Grid Flow is an AI-driven grid market-making strategy. Instead of static grid levels, the AI dynamically adjusts the grid direction and width based on market conditions. In the unified architecture, this strategy is run by enabling `trading.grid_enabled` in `config.yaml` and launching `main.py`.

## How Grid Trading Works

A grid strategy places limit orders at regular price intervals — buy orders below the current price, sell orders above. As the market oscillates, orders fill and generate profit from the spread.

Quant Flow enhances this with:
- **AI direction judgment** (LONG / SHORT / NEUTRAL bias)
- **Dynamic grid width** based on ATR volatility + AI input
- **Automatic exit orders** after fills (TP/SL/reduce-only)

## Architecture

```
MarketData (OHLCV + indicators)
         ↓
GridAgent (AI decision)
│  → Direction: LONG / SHORT / NEUTRAL
│  → Suggested grid width %
│  → Grid levels count
         ↓
calculate_grid_config (math engine)
│  65% market data + 35% AI blend
│  → final_width, price_levels[], order_sizes[]
         ↓
GridManager
│  → Place limit orders at each level
│  → Monitor fills
│  → Place exit orders (TP/SL/reduce-only)
│  → Safety checks (orphan cleanup, cancel timeout)
         ↓
HyperliquidClient + OrderManager
```

## AI Decision Engine (GridAgent)

Every `scheduler.interval_minutes` (default: 5 min), the GridAgent calls the LLM with:
- Recent OHLCV candlesticks (15m timeframe recommended)
- RSI, MACD, Bollinger Bands
- Current funding rate
- Existing grid state (active orders, current P&L)

The LLM returns:
- **Direction**: `LONG` (buy-heavy grid), `SHORT` (sell-heavy grid), or `NEUTRAL` (symmetric)
- **Grid width percentage**: suggested spacing between levels
- **Number of levels**: how many grid levels to deploy

## Math Engine: Grid Config Calculation

The math engine (`src/utils/grid_math.py`) computes final parameters by blending AI output with market data:

```python
# Blend formula
final_width = (atr_based_width × 0.65) + (ai_suggested_width × 0.35)
```

The ATR-based width uses a rolling ATR to compute statistically appropriate spacing. The blend prevents the AI from setting unrealistically tight or wide grids.

**Output of `calculate_grid_config`:**
- `price_levels`: list of exact price points for limit orders
- `order_sizes`: position size per level (uniform or proportional)
- `direction_bias`: net long/short tilt
- `total_investment`: total capital to deploy

## GridManager: Order Lifecycle

### Initial Placement
When a new grid is deployed (or after re-evaluation), GridManager:
1. Cancels all existing orders (with 20s hard timeout safety)
2. Places new limit orders at each price level
3. Saves state atomically to `grid_state.json`

### Fill Handling
When a grid order fills:
1. GridManager detects the fill during the next sync
2. Places exit orders based on config:
   - **Take-profit**: limit order at the next grid boundary
   - **Stop-loss**: stop order below/above the fill price
   - **Reduce-only**: layered reduce-only orders for gradual position exit

### State Persistence
State is saved to `grid_state.json` after every operation using atomic writes (tempfile + rename). On restart, GridManager reconciles with the exchange to detect any fills that occurred while offline.

## Safety Mechanisms

| Mechanism | Description |
|---|---|
| Orphan trigger cleanup | Detects trigger orders with no matching position and cancels them |
| Reduce-only exit | Exits large positions in layers to minimize market impact |
| Cancel hard timeout | If a cancel doesn't confirm within 20s, retries and alerts |
| Atomic state writes | Prevents JSON corruption if process is interrupted mid-write |

## Direction Modes

| AI Direction | Grid Behavior |
|---|---|
| `LONG` | More buy orders below current price, fewer above — accumulates long exposure as price drops |
| `SHORT` | More sell orders above current price, fewer below — accumulates short exposure as price rises |
| `NEUTRAL` | Symmetric grid — equal buy/sell orders, pure range-trading |

## Configuration

```yaml
# config.yaml
trading:
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
```

See [Grid Config Reference](../configuration/grid-config.md) for all options.

## Running Grid Flow

Run the unified bot entry point with `grid_enabled` set to `true`:

```bash
uv run python main.py
```

Or with Docker:
```bash
# docker-compose.yml
# Make sure perp_enabled: false / grid_enabled: true is configured in config.yaml
```

## Backtest Before Live

:::warning Test First
Always backtest your grid configuration before deploying live funds. Grid trading in trending markets can accumulate large losing positions.

```bash
uv run python backtest.py --symbol ETH --strategy grid \
  --start-date 2024-01-01 --end-date 2024-12-01
```
:::

## Next Steps

- [Grid Config Reference](../configuration/grid-config.md)
- [Grid Backtesting](../backtesting/grid.md)
- [A/B Comparison Backtesting](../backtesting/comparison.md)
