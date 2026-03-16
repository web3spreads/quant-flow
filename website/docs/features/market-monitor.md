---
sidebar_position: 5
title: Market Active Monitoring
description: Independent thread that detects volatility spikes and triggers immediate decisions
---

# Market Active Monitoring

The market monitor runs as an independent background thread that continuously polls prices at short intervals. When it detects an abnormal price spike, it immediately triggers a trading decision — without waiting for the next scheduled interval.

## Why This Matters

The scheduler runs every few minutes (default: 3 min). In fast-moving crypto markets, a significant move can complete in under 2 minutes. Without the monitor, you'd be reacting to the tail-end of the move.

The monitor checks every **30 seconds** and triggers a decision the moment a threshold is crossed, giving the agent first-mover advantage.

## Alert Levels

| Level | Threshold | Behavior |
|---|---|---|
| `NORMAL` | < 1.5% | Ignored |
| `ELEVATED` | ≥ 1.5% | Logged (throttled to once per 60s), no decision trigger |
| `HIGH` | ≥ 3.0% | **Triggers decision loop** + cooldown period begins |
| `EXTREME` | ≥ 5.0% | **Triggers decision loop** + cooldown + extra alert logged |

Thresholds are configurable. The reference is a rolling window of prices (default: 10 min).

## Architecture

```
MarketMonitor thread (every 30s)
   ↓
all_mids() — lightweight Hyperliquid price query
   ↓
Compare vs. reference window baseline
   ↓
If spike ≥ threshold:
   → Generate VolatilityAlert
   → Add to pending_alerts queue (thread-safe)
   → Enter per-symbol cooldown
         ↓
main.py event loop (every scheduler tick)
   → _consume_pending_alert()
   → trading_cycle(triggered_by_alert=True)
   → {{ volatility_alert }} injected into prompt
```

## Thread Safety

Four locks protect shared state:

| Lock | Protects |
|---|---|
| `_history_lock` | `_price_history` dict (monitor writes, main reads) |
| `_stats_lock` | Alert statistics counters |
| `_alert_lock` | Pending alert queue |
| Per-symbol cooldown | Prevents repeated triggers for the same move |

## Configuration

```yaml
# config.yaml
market_monitor:
  enabled: true
  check_interval_seconds: 30      # how often to poll prices
  alert_threshold_pct: 3.0        # HIGH alert threshold
  elevated_threshold_pct: 1.5     # ELEVATED threshold (log only)
  extreme_threshold_pct: 5.0      # EXTREME threshold
  cooldown_minutes: 5             # per-symbol cooldown after trigger
  reference_window_minutes: 10    # baseline price window
```

## Alert Context in Prompt

When a volatility-triggered decision is made, the alert context is injected as `{{ volatility_alert }}`:

```
⚠️ VOLATILITY ALERT [HIGH] — BTC
Price moved +3.8% in the last 4 minutes.
Current price: $44,210 | Reference baseline: $42,588
Alert triggered at: 2024-03-15 14:23:07 UTC

This is an opportunistic decision triggered by detected volatility.
Consider whether this is a breakout, a fakeout, or a stop-hunt.
```

This context feeds directly into the [FinCoT](./fincot.md) Step 1 (Trend Confirmation) and Step 3 (Sentiment Check), allowing the LLM to reason about the nature of the spike.

## Cooldown Mechanism

After triggering a decision for a symbol, that symbol enters a cooldown period (`cooldown_minutes`, default: 5 min). During cooldown:
- New spikes for that symbol are detected and logged
- But no new decision is triggered
- Prevents repeated back-to-back decisions on the same move

Cooldown is **per-symbol** — a BTC alert does not block ETH decisions.

## Enabling the Monitor

```yaml
market_monitor:
  enabled: true
```

:::tip Recommended Settings
For liquid assets like BTC/ETH:
- `alert_threshold_pct: 3.0` — catches significant moves without noise
- `cooldown_minutes: 5` — prevents decision spam

For lower-liquidity assets, consider raising the threshold to 5.0% to avoid false triggers from thin order books.
:::

## Log Output

When the monitor runs, you'll see:

```
[INFO] MarketMonitor started for symbols: [BTC, ETH]
[DEBUG] BTC price check: $44,100 (baseline: $43,850, change: +0.57%) — NORMAL
[INFO] ETH price check: $2,890 (baseline: $2,782, change: +3.88%) — HIGH ALERT
[INFO] Triggering decision for ETH (volatility alert)
[INFO] ETH cooldown started (5 min)
```

## Next Steps

- [Review & Reflection System](./review-system.md)
- [FinCoT Reasoning](./fincot.md)
- [config.yaml Reference](../configuration/config-yaml.md)
