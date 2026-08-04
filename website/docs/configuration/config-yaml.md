---
sidebar_position: 2
title: config.yaml Reference
description: Complete reference for all config.yaml settings
---

# config.yaml Reference

`config.yaml` is the main configuration file for the perpetual futures agent. Copy from the example:

```bash
cp config.yaml.example config.yaml
```

## LLM Configuration

```yaml
llm:
  client_type: langchain_nvidia   # openai | cloudflare | google | litellm | nvidia
  model: qwen/qwen3-next-80b-a3b-instruct   # pick any model your provider supports
  temperature: 0.2                 # low temperature recommended for trading decisions
```

### Supported `client_type` Values

| Value | Provider | Notes |
|---|---|---|
| `openai` | OpenAI | Requires `OPENAI_API_KEY` |
| `langchain_nvidia` | NVIDIA NIM | Requires `NVIDIA_API_KEY` |
| `google` | Google Gemini | Requires `GOOGLE_API_KEY` |
| `cloudflare` | Cloudflare Workers AI | Requires `CLOUDFLARE_*` vars |
| `litellm` | LiteLLM proxy | Flexible multi-provider routing |

:::tip Temperature
Keep `temperature` at `0.1`–`0.3` for trading decisions. Higher values produce more creative but less reliable JSON outputs.
:::

## Trading Parameters

```yaml
trading:
  # Strategy toggles
  perp_enabled: true         # Enable perpetual futures agent
  grid_enabled: false        # Enable grid market-making

  symbols: [BTC, ETH]       # use simple symbols, NOT pair format (not BTC/USDT)
  max_trade_amount: 100     # maximum USD per single trade / grid investment
  max_leverage: 10          # maximum leverage multiplier
  limit_order_enabled: false  # use limit orders for entry (vs. market orders)
```

:::warning Symbol Format
Use `BTC`, `ETH` — **not** `BTC/USDT` or `BTC-PERP`. Hyperliquid uses simple asset symbols.
:::

### Leverage Notes

- Different assets have different maximum leverage on Hyperliquid
- If `max_leverage` exceeds the asset's limit, orders will be rejected
- Start conservative: `max_leverage: 3` or `5` until you're confident in the strategy

## Scheduler

```yaml
scheduler:
  interval_minutes: 3    # fallback polling interval between decisions
```

The scheduler is the fallback decision loop. When [Market Monitor](../features/market-monitor.md) is enabled, volatility spikes trigger decisions immediately without waiting for the interval.

## Prompt Strategy

```yaml
prompt:
  set: nof1-improved    # see table below
```

| Value | Description |
|---|---|
| `default` | Standard FinCoT reasoning chain |
| `conservative` | Hold on any trend divergence, requires R:R ≥ 2.0 |
| `aggressive` | Enters on 3 conditions, accepts R:R ≥ 1.2 |
| `nof1` | Enhanced strategy with full FinCoT integration |
| `nof1-improved` | **Recommended** — complete FinCoT + enhanced data integration |
| `realtime` | Prioritizes price action over lagging indicators |
| `realtime-eng` | English version of realtime |

## Enhanced Analysis

```yaml
enhanced_analysis:
  enabled: true    # enables CEX funding rate, on-chain data collection
```

This is the base switch for [CEX Signals](../features/cex-signals.md) and [Regime Adaptive](../features/regime-adaptive.md). Other features that depend on this:

```
enhanced_analysis.enabled: true
  ├── debate.enabled: true         # independent switch
  └── regime_adaptive.enabled: true  # depends on enhanced_analysis
```

## Debate Agent

```yaml
debate:
  enabled: false    # adds 2 extra LLM calls per decision cycle
```

See [Bull/Bear Debate](../features/debate.md) for details.

## Regime Adaptive

```yaml
regime_adaptive:
  enabled: false
  # Optional parameter overrides:
  # trending:
  #   signal_threshold: 0.5
  #   min_confidence: 0.35
  #   max_leverage: 10
  # ranging:
  #   signal_threshold: 0.75
  #   max_leverage: 5
  # volatile:
  #   signal_threshold: 0.85
  #   max_leverage: 3
```

See [Market Regime Adaptive](../features/regime-adaptive.md) for details.

## Account Protection (Plugin-based)

The new format is a list of independently togglable plugins. The legacy
`account_protection: { enabled: true, ... }` block still works and is
auto-migrated for backward compatibility.

```yaml
# Empty list = no risk control
protections:
  - name: max_drawdown
    max_drawdown_pct: 0.10       # close all + pause when drawdown ≥ 10% from peak
    pause_hours: 4

  - name: daily_loss
    max_daily_loss_pct: 0.05     # pause new trades when daily loss ≥ 5%
    pause_hours: 4

  - name: consecutive_loss
    max_consecutive_losses: 5
    per_symbol: true             # true = lock only the losing symbol; false = global pause
    pause_hours: 4

  - name: position_timeout
    max_position_hours: 48       # force-close positions held longer than this
```

:::warning Always Keep Some Protections Enabled
The protection plugins are critical safety mechanisms. Only disable them if you fully understand the risks.
:::

## Market Monitor

```yaml
market_monitor:
  enabled: false
  check_interval_seconds: 30   # price check frequency
  alert_threshold_pct: 3.0     # HIGH alert threshold
  elevated_threshold_pct: 1.5  # ELEVATED threshold (logged, no trigger)
  extreme_threshold_pct: 5.0   # EXTREME threshold
  cooldown_minutes: 5          # cooldown after a trigger
  reference_window_minutes: 10 # price baseline window
```

See [Market Active Monitoring](../features/market-monitor.md) for alert levels and behavior.

## Review Agent

```yaml
review_agent:
  # 6a: Dual-granularity reflection
  instant_reflection_enabled: false    # after every closed position
  weekly_reflection_enabled: false     # weekly LLM-based strategy review
  weekly_reflection_day: 0             # 0=Monday
  weekly_reflection_hour: 8

  # 6b: Regime-aware memory
  regime_aware_enabled: false
  regime_mismatch_factor: 0.4          # weight penalty for regime mismatch

  # 6c: Confirmation bias protection
  bias_protection_enabled: false
  max_positive_ratio: 0.7              # max ratio of positive lessons in memory
  negative_confidence_boost: 1.15      # boost factor for negative lessons

  # 6d: Fact-subjective split
  fact_subjective_split_enabled: false
  trending_subjective_boost: 1.3
  ranging_factual_boost: 1.3

  # 6e: Prompt meta-reflection
  prompt_meta_reflection_enabled: false
  prompt_optimization_dir: "logs/prompt_optimization"
```

See [Review & Reflection System](../features/review-system.md) for all 6 enhancements.

## Data Configuration

```yaml
data:
  timeframe: 1h    # OHLCV candle timeframe for main decisions
```

## Complete Example

```yaml
llm:
  client_type: langchain_nvidia
  model: qwen/qwen3-next-80b-a3b-instruct
  temperature: 0.2

trading:
  perp_enabled: true
  grid_enabled: false
  symbols: [BTC, ETH]
  max_trade_amount: 100
  max_leverage: 10
  limit_order_enabled: false

scheduler:
  interval_minutes: 3

data:
  timeframe: 1h

prompt:
  set: nof1-improved

enhanced_analysis:
  enabled: true

debate:
  enabled: true

regime_adaptive:
  enabled: true

protections:
  - name: max_drawdown
    max_drawdown_pct: 0.10
    pause_hours: 4
  - name: daily_loss
    max_daily_loss_pct: 0.05
    pause_hours: 4
  - name: consecutive_loss
    max_consecutive_losses: 5
    per_symbol: true
    pause_hours: 4
  - name: position_timeout
    max_position_hours: 48

market_monitor:
  enabled: true
  check_interval_seconds: 30
  alert_threshold_pct: 3.0
  cooldown_minutes: 5

review_agent:
  instant_reflection_enabled: true
  weekly_reflection_enabled: true
  regime_aware_enabled: true
  bias_protection_enabled: true
  fact_subjective_split_enabled: true
  prompt_meta_reflection_enabled: false
```
