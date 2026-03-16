---
sidebar_position: 3
title: A/B Comparison Backtesting
description: Compare the impact of individual AI features using side-by-side backtests
---

# A/B Comparison Backtesting

`backtest_comparison.py` runs multiple backtests side by side with specific features toggled on/off, giving you a controlled measurement of each feature's contribution.

## Quick Start

```bash
# Compare all major features at once
uv run python backtest_comparison.py --symbol BTC --compare all

# Compare a specific feature
uv run python backtest_comparison.py --symbol BTC --compare debate
uv run python backtest_comparison.py --symbol BTC --compare fincot
uv run python backtest_comparison.py --symbol BTC --compare regime
uv run python backtest_comparison.py --symbol BTC --compare onchain
```

## Available Comparisons

| `--compare` value | What It Tests | A (Baseline) | B (With Feature) |
|---|---|---|---|
| `fincot` | FinCoT structured reasoning | `prompt.set: default` | `prompt.set: nof1-improved` |
| `debate` | Bull/Bear debate agent | `debate.enabled: false` | `debate.enabled: true` |
| `onchain` | CEX + on-chain data | `enhanced_analysis.enabled: false` | `enhanced_analysis.enabled: true` |
| `regime` | Regime adaptive strategy | `regime_adaptive.enabled: false` | `regime_adaptive.enabled: true` |
| `all` | All features combined | All off | All on |

## Command Reference

```
usage: backtest_comparison.py [--symbol SYMBOL] [--compare FEATURE]
                               [--start-date DATE] [--end-date DATE]
                               [--config CONFIG]

Options:
  --symbol        Asset to backtest
  --compare       Feature to compare: fincot, debate, onchain, regime, all
  --start-date    Start date (default: 3 months ago)
  --end-date      End date (default: today)
  --config        Base config file (default: config.backtest.yaml)
```

## How Comparison Works

For each comparison, two parallel backtests run over identical historical data with identical random seeds:

```
Historical Data (same period)
         ↓            ↓
  Backtest A     Backtest B
  (baseline)     (feature on)
         ↓            ↓
   metrics A     metrics B
         ↓
  Delta Report
```

Both backtests use the same LLM model and temperature, so differences in results are attributable to the feature under test.

## Output

```
backtest_results/
└── comparison_BTC_debate_20241201/
    ├── baseline/
    │   ├── summary.json
    │   └── equity_curve.png
    ├── with_feature/
    │   ├── summary.json
    │   └── equity_curve.png
    └── comparison_report.json    # delta metrics
```

### Comparison Report Format

```json
{
  "feature": "debate",
  "symbol": "BTC",
  "period": "2024-01-01 to 2024-12-01",
  "baseline": {
    "total_return_pct": 28.4,
    "sharpe_ratio": 1.62,
    "max_drawdown_pct": -14.2,
    "win_rate": 0.55
  },
  "with_feature": {
    "total_return_pct": 34.8,
    "sharpe_ratio": 1.91,
    "max_drawdown_pct": -11.8,
    "win_rate": 0.59
  },
  "delta": {
    "return_delta_pct": "+6.4pp",
    "sharpe_delta": "+0.29",
    "drawdown_delta": "+2.4pp (reduced)",
    "win_rate_delta": "+4pp"
  },
  "verdict": "Debate agent improves all metrics. Recommended to enable."
}
```

## Interpreting Results

### Positive Signals
- Higher total return
- Higher Sharpe ratio (better risk-adjusted return)
- Lower max drawdown (smaller worst-case loss)
- Higher win rate OR higher profit factor (not always both)

### Negative Signals
- Feature adds latency/cost with no measurable improvement
- Higher drawdown despite higher returns (unfavorable risk profile)
- Win rate improves but profit factor drops (wins more frequently but smaller)

:::warning Context Matters
A feature that helps during a trending bull market may hurt during ranging markets. Always run comparisons over multiple date ranges covering different regimes:
- Trending period (e.g., Oct–Dec 2023)
- Ranging period (e.g., Jun–Sep 2024)
- Volatile period (e.g., Jan–Mar 2024)
:::

## Cost Considerations

Each comparison run effectively doubles your backtest API cost. For `--compare all`, it's baseline + one test per feature = 5× the base cost.

Reduce cost with a longer `scheduler.interval_minutes` in your backtest config:

```yaml
# config.backtest.yaml
scheduler:
  interval_minutes: 60    # hourly decisions for comparison runs
```

## Example: Full Feature Comparison

```bash
# Run a full comparison over 2024
uv run python backtest_comparison.py \
  --symbol BTC \
  --compare all \
  --start-date 2024-01-01 \
  --end-date 2024-12-31 \
  --config config.backtest.yaml
```

After completion, examine `comparison_report.json` for each feature pair to decide which to enable in your live config.

## Next Steps

- [Single Agent Backtesting](./single.md)
- [Grid Strategy Backtesting](./grid.md)
- [AI Features Overview](../features/fincot.md)
