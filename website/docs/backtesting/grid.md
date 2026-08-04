---
sidebar_position: 2
title: Grid Strategy Backtesting
description: Backtest the grid trading strategy on historical data
---

# Grid Strategy Backtesting

The grid backtest simulates the AI-driven grid market-making strategy against historical data. It tests the grid placement, fill simulation, and exit order logic without placing real orders.

## Quick Start

```bash
uv run python backtest.py \
  --symbol ETH \
  --strategy grid \
  --start-date 2024-01-01 \
  --end-date 2024-12-01
```

## How the Grid Backtest Works

1. **Data download**: Historical OHLCV data is fetched and cached.

2. **AI grid decisions**: Every `scheduler.interval_minutes`, the GridAgent calls the LLM to determine direction and grid width — same as live trading.

3. **Grid simulation**: The math engine computes grid levels. Fill simulation checks whether each historical candle's high/low would have triggered fills at each grid level.

4. **Fill handling**: When a simulated fill occurs, exit orders (TP/SL) are also simulated. The grid is re-evaluated at each scheduler interval.

5. **P&L tracking**: Realized P&L from fills, unrealized P&L from open grid positions, and fees are all tracked.

## Grid Backtest Configuration

Grid backtesting uses the same unified `config.yaml` — set `trading.grid_enabled: true` (and `trading.perp_enabled: false` to focus on grid). Override the config file with `--config`:

```bash
uv run python backtest.py --symbol ETH --strategy grid \
  --start-date 2024-01-01 --end-date 2024-12-01 \
  --config config.yaml
```

Relevant `config.yaml` keys for grid backtesting:

```yaml
trading:
  perp_enabled: false
  grid_enabled: true
  symbols: [ETH]
  max_trade_amount: 100          # per-layer investment cap (USD)
  max_leverage: 3                # conservative for backtesting
  grid_limit_order_take_profit_enabled: true
  grid_limit_order_stop_loss_enabled: true
  grid_reduce_only_exit_orders_enabled: true

agent:
  grid_width:
    min_pct: 0.02
    max_pct: 0.10
    fallback_pct: 0.04
    ai_blend_weight: 0.35

scheduler:
  interval_minutes: 15          # longer interval = fewer LLM calls = cheaper backtest
```

## Output

```
backtest_results/
└── ETH_grid_20241201_143022/
    ├── live_report.json         # grid state snapshots + fills
    ├── summary.json             # aggregate metrics
    ├── grid_levels.png          # visualization of grid placement over time
    └── fill_log.csv             # every simulated fill
```

### Key Metrics for Grid Strategy

```json
{
  "total_return_pct": 18.7,
  "grid_fills": 1847,
  "avg_profit_per_fill_usd": 0.43,
  "max_concurrent_levels": 8,
  "max_unrealized_loss_usd": -234.50,
  "direction_accuracy": 0.61,
  "regime_distribution": {
    "trending": 0.35,
    "ranging": 0.48,
    "volatile": 0.17
  }
}
```

### Direction Accuracy
Shows what percentage of AI direction calls (LONG/SHORT/NEUTRAL) aligned with the subsequent price movement. Higher is better; 50% is chance level.

### Regime Distribution
Shows how much time was spent in each market regime during the backtest period. Grid strategies perform best in ranging markets (high fill rate) and worst in strong trends (accumulates directional exposure).

## Grid Backtest vs. Single Agent Backtest

| Aspect | Single Agent | Grid |
|---|---|---|
| Decision frequency | Every interval | Every interval (grid re-eval) + continuous fills |
| LLM calls | One per interval | One per interval (grid direction) |
| P&L drivers | Directional trades | Fill frequency × spread |
| Risk | Stop-out from adverse direction | Trend riding against grid direction |
| Best market condition | Trending | Ranging |

## Analyzing Grid Performance by Regime

To understand when your grid config performs well, look at the `regime_distribution` in the backtest results. If most backtest time was ranging and the strategy was profitable, verify it also holds up during the trending/volatile periods.

:::tip
Run the grid backtest over multiple date ranges to capture different market regimes. A grid config tuned only on 2024's ranging periods may underperform in 2025's trending markets.
:::

## Resuming Interrupted Grid Backtests

```bash
uv run python backtest.py --symbol ETH --strategy grid \
  --start-date 2024-01-01 --end-date 2024-12-01 \
  --resume-from backtest_results/ETH_grid_20241201_143022/live_report.json
```

## Comparing Grid Configurations

Use [A/B Comparison Backtesting](./comparison.md) to test different `ai_blend_weight` or `grid_width` settings side by side.

## Next Steps

- [A/B Comparison Backtesting](./comparison.md)
- [Grid Flow Strategy](../strategies/grid-flow.md)
- [Grid Config Reference](../configuration/grid-config.md)
