---
sidebar_position: 4
title: Market Regime Adaptive Strategy
description: Dynamic parameter switching based on trending/ranging/volatile market states
---

# Market Regime Adaptive Strategy

The regime adaptive feature detects the current market state (trending / ranging / volatile) and automatically adjusts trading parameters — signal thresholds, leverage limits, and position sizes — to match the environment.

## Academic Basis

**Paper**: [Springer Digital Finance 2025](https://link.springer.com/article/10.1007/s42521-024-00123-2)

**Key finding**: Regime-aware strategies significantly outperform static parameter strategies across all market conditions.

## Three Market Regimes

| Regime | Detection | Behavior |
|---|---|---|
| **Trending** | Price consistently above/below MA, ADX high | Relax thresholds, allow higher leverage |
| **Ranging** | Price oscillating between support/resistance, ADX low | Tighten thresholds, reduce leverage |
| **Volatile** | High ATR, large price swings, low trend clarity | Strict thresholds, minimum leverage |

## Parameter Adjustments

When regime detection is active, these parameters are overridden dynamically:

### Trending Market
```
signal_threshold:  0.50   (relaxed — momentum strategies work)
min_confidence:    0.35   (lower bar — trend has conviction)
max_leverage:      10×    (leverage benefits from direction)
max_position_pct:  80%    (use most of allocated capital)
```

### Ranging Market
```
signal_threshold:  0.75   (tightened — mean-reversion needs precision)
min_confidence:    0.55   (higher bar — no clear direction)
max_leverage:      5×     (limited — range can break any time)
max_position_pct:  40%    (conservative sizing)
```

### Volatile Market
```
signal_threshold:  0.85   (very strict — avoid whipsaws)
min_confidence:    0.65   (high confidence required)
max_leverage:      3×     (protect against liquidation on spikes)
max_position_pct:  30%    (minimal exposure)
```

## The 11 Market States

The `MarketState` enum defines 11 granular states that map to the 3 regimes:

| MarketState | Regime |
|---|---|
| STRONG_UPTREND, UPTREND, WEAK_UPTREND | Trending |
| STRONG_DOWNTREND, DOWNTREND, WEAK_DOWNTREND | Trending |
| RANGING_HIGH, RANGING_LOW, RANGING | Ranging |
| HIGH_VOLATILITY | Volatile |
| UNCERTAIN | Volatile |

## Signal Scorer Regime Adaptation

The `SignalScorer` (`src/data/signal_scorer.py`) adjusts factor weights based on regime:

| Signal Factor | Trending Weight | Ranging Weight | Volatile Weight |
|---|---|---|---|
| Trend (MA alignment) | High | Low | Low |
| Momentum (RSI, MACD) | Medium | Low | Low |
| Mean reversion | Low | High | Low |
| Volatility | Low | Low | Low (raw; dampened) |

In trending markets, trend-following signals are amplified. In ranging markets, mean-reversion signals dominate.

## Enabling Regime Adaptive

```yaml
# config.yaml
enhanced_analysis:
  enabled: true           # required — provides market state data

regime_adaptive:
  enabled: true
```

:::info Dependency
`regime_adaptive` requires `enhanced_analysis.enabled: true` to function. The enhanced analysis pipeline computes the market state from enriched data.
:::

## Custom Parameter Overrides

You can override the default regime parameters:

```yaml
regime_adaptive:
  enabled: true
  trending:
    signal_threshold: 0.45    # even more relaxed
    min_confidence: 0.30
    max_leverage: 12
  ranging:
    signal_threshold: 0.80
    max_leverage: 4
  volatile:
    signal_threshold: 0.90
    max_leverage: 2
```

## Regime Hint in Prompt

The current regime is injected into the LLM prompt as `{{ regime_hint }}`:

```
Current market regime: RANGING
Parameters active: signal_threshold=0.75, max_leverage=5x
Recommended bias: Mean-reversion favored. Avoid breakout trades.
Wait for clear support/resistance rejection before entry.
```

This gives the LLM explicit context about why certain parameters are tighter, improving reasoning quality.

## Interaction with Other Features

| Feature | Interaction |
|---|---|
| **FinCoT** | Regime hint aids Step 1 (Trend Confirmation) and Step 6 (Final Decision) |
| **Debate** | Regime context is available to both BullAgent and BearAgent |
| **Review System** | Regime-aware memory tags lessons with `source_regime` for future filtering |
| **Signal Scorer** | Factor weights dynamically adjusted per regime |

## Monitoring Regime Changes

Regime transitions are logged:

```
[INFO] Regime changed: RANGING → TRENDING for BTC
[INFO] Applying trending parameters: threshold=0.50, leverage_max=10
```

Watch for frequent regime flipping — if you see many transitions in short periods, consider widening the ADX threshold in `src/data/market_state.py`.

## Next Steps

- [Market Active Monitoring](./market-monitor.md)
- [Review System](./review-system.md)
- [config.yaml Reference](../configuration/config-yaml.md)
