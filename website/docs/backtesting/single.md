---
sidebar_position: 1
title: Single Agent Backtesting
description: Backtest the perpetual futures agent strategy on historical data
---

# Single Agent Backtesting

The single agent backtest runs the perpetual futures decision logic against historical OHLCV data, simulating trades without placing real orders. Use it to validate your configuration before going live.

## Quick Start

```bash
uv run python backtest.py \
  --symbol BTC \
  --strategy single \
  --start-date 2024-01-01 \
  --end-date 2024-12-01
```

## Command Reference

```
usage: backtest.py [--symbol SYMBOL] [--strategy {single,grid}]
                   [--start-date DATE] [--end-date DATE]
                   [--config CONFIG] [--resume-from REPORT]
                   [--output-dir DIR]

Options:
  --symbol          Asset to backtest (e.g. BTC, ETH)
  --strategy        Backtest strategy: single (default) or grid
  --start-date      Start date in YYYY-MM-DD format
  --end-date        End date in YYYY-MM-DD format
  --config          Path to config.yaml (default: config.yaml)
  --resume-from     Resume from a previous run's live_report.json
  --output-dir      Output directory (default: backtest_results/)
```

## How the Backtest Works

1. **Data download**: Historical OHLCV data for the symbol and date range is fetched from Hyperliquid. Data is cached locally under `data/` to avoid re-downloading.

2. **Indicator computation**: The same `indicators.py` functions used in live trading compute MA, RSI, MACD, Bollinger Bands from the historical candles.

3. **LLM decisions**: The actual LLM is called for each decision point using your configured model. This means **backtesting uses real API credits**.

4. **Simulated execution**: Trades are simulated using the historical close price. Slippage and fees are modeled (Hyperliquid Tier 0 fees).

5. **Results output**: A detailed report is saved to `backtest_results/`.

:::info Real LLM Calls
Unlike some backtest frameworks that replay cached decisions, Quant Flow calls the LLM for each historical decision point. This gives realistic results but costs real API credits. A 12-month backtest on BTC with 3-minute intervals = ~175,000 decision points. Configure `scheduler.interval_minutes` appropriately to control cost.
:::

## Output

After the backtest completes, you'll find in `backtest_results/<symbol>_<timestamp>/`:

```
backtest_results/
└── BTC_20241201_143022/
    ├── live_report.json       # full trade-by-trade log
    ├── summary.json           # aggregate statistics
    ├── equity_curve.png       # equity curve chart
    └── trade_log.csv          # CSV export of all trades
```

### Key Metrics in summary.json

```json
{
  "total_return_pct": 34.2,
  "sharpe_ratio": 1.87,
  "max_drawdown_pct": -12.4,
  "win_rate": 0.58,
  "total_trades": 247,
  "avg_profit_per_trade_usd": 1.83,
  "largest_win_usd": 48.20,
  "largest_loss_usd": -23.10,
  "profit_factor": 1.94
}
```

## Resuming Interrupted Backtests

If a backtest is interrupted (network issue, API error), resume from where it stopped:

```bash
uv run python backtest.py \
  --symbol BTC \
  --strategy single \
  --start-date 2024-01-01 \
  --end-date 2024-12-01 \
  --resume-from backtest_results/BTC_20241201_143022/live_report.json
```

The backtest reads existing trades from `live_report.json` and continues from the last completed decision point.

## Configuration Tips for Backtesting

Use a separate config file for backtesting to avoid accidentally changing live settings:

```bash
cp config.yaml config.backtest.yaml
```

Adjust for cost efficiency:
```yaml
# config.backtest.yaml
scheduler:
  interval_minutes: 30     # hourly decisions instead of 3-min (reduces API calls)

debate:
  enabled: false           # save API credits during backtest

market_monitor:
  enabled: false           # not applicable in backtest
```

Run with:
```bash
uv run python backtest.py --symbol BTC --strategy single \
  --start-date 2024-01-01 --end-date 2024-12-01 \
  --config config.backtest.yaml
```

## Understanding Results

### On Win Rate
A win rate of 50–60% is typical for a well-tuned strategy. More important is **profit factor** (gross profit ÷ gross loss) — aim for > 1.5.

### On Drawdown
Max drawdown of 10–15% is generally acceptable for a leveraged crypto strategy. Higher drawdown means higher risk of hitting your `protections.max_drawdown.max_drawdown_pct` in live trading.

### On Sharpe Ratio
Sharpe > 1.5 is good; > 2.0 is excellent for crypto markets.

:::warning Backtest ≠ Live Performance
Historical results do not guarantee future performance. LLM behavior, market microstructure, and funding rate dynamics change over time. Always start live trading with minimum position sizes.
:::

## Next Steps

- [Grid Backtesting](./grid.md)
- [A/B Comparison Backtesting](./comparison.md)
- [config.yaml Reference](../configuration/config-yaml.md)
