---
sidebar_position: 1
title: Perpetual Futures Agent
description: How the multi-agent perpetual futures trading strategy works
---

# Perpetual Futures Agent

The perpetual futures agent (`main.py`) is the primary trading strategy. It uses a multi-agent Pydantic AI architecture where each trading pair runs with its own independent decision context.

## Architecture Overview

```
ExternalInfoAgent (Exa API market news)
         ↓
MarketData (OHLCV + technical indicators)
         ↓
EnhancedSingleSymbolAgent ←── MarketMonitor (volatility alerts)
         ↓
ExecutionAgent (structured output)
         ↓
DecisionValidator + PositionSizer + RiskManager
         ↓
ProtectionManager (plugin chain) + OrderManager
         ↓
HyperliquidClient [safety: SL fail → auto close with retries]
         ↓
SummaryAgentV2 (context compression) + ReviewAgent (learning)
```

## Decision Pipeline

### 1. Market Data Collection

Every decision cycle collects:
- **OHLCV candlesticks** at the configured timeframe (default: 1h)
- **Technical indicators**: MA (20/50/200), RSI(14), MACD(12,26,9), Bollinger Bands(20,2)
- **Funding rate** from Hyperliquid and Binance (CEX leading signal)
- **On-chain data**: MVRV, SOPR, Fear & Greed Index (when `enhanced_analysis.enabled`)
- **Market news** via ExternalInfoAgent (Exa API)

### 2. AI Decision Making

The `EnhancedSingleSymbolAgent` compiles all data into a structured prompt and calls the LLM. When enabled, the decision is enriched with:

- **Bull/Bear debate** results (see [Debate](../features/debate.md))
- **Market regime hint** (see [Regime Adaptive](../features/regime-adaptive.md))
- **Volatility alert context** (see [Market Monitor](../features/market-monitor.md))

The LLM uses [FinCoT 6-step reasoning](../features/fincot.md):

1. Trend confirmation — are multi-timeframe trends aligned?
2. Entry signal — which indicators triggered? (with exact values)
3. Sentiment check — any contrarian signals from funding/F&G/debate?
4. Review match — which historical lesson applies?
5. Risk calculation — stop-loss/take-profit distance, R:R ratio, fee coverage
6. Final decision — LONG / SHORT / HOLD with confidence score

### 3. Execution Planning

`ExecutionAgent` converts the LLM decision into a structured `ExecutionPlan` (Pydantic model):

```python
class ExecutionPlan(BaseModel):
    action: Literal["LONG", "SHORT", "HOLD", "CLOSE"]
    symbol: str
    confidence: float          # 0.0 – 1.0
    leverage: int
    position_size_usd: float
    stop_loss_pct: float
    take_profit_pct: float
    reasoning: str
```

### 4. Validation Layer

Before any order is placed, the decision passes through multiple validators:

| Validator | What It Checks |
|---|---|
| `DecisionValidator` | Multi-timeframe trend alignment, signal quality, R:R ratio, market state suitability |
| `PositionSizer` | Kelly criterion position sizing, volatility adjustment, drawdown shrinkage |
| `RiskManager` | ATR-based dynamic SL/TP, max risk per trade (default 2%), max total exposure (50%) |
| `ProtectionManager` | Plugin chain: max drawdown (close-all + pause), daily loss (pause), consecutive loss (global or per-symbol lock), position timeout (force-close). Each plugin independently togglable via `protections:` config. |

Validation results:
- `PASS` — proceed with trade
- `WARN` — log warning but allow
- `BLOCK` — reject trade, log reason

### 5. Order Execution

`OrderManager` submits orders to Hyperliquid via the SDK:

```
Open position → [if limit_order_enabled: limit order, else market order]
Place stop-loss → [if fails: CRITICAL SAFETY — auto close position with up to 3 retries]
Place take-profit → [if limit TP enabled]
```

:::danger Critical Safety Mechanism
If placing the stop-loss order fails after opening a position, `HyperliquidClient` immediately attempts to close the position (market order, up to 3 retries). This prevents running an open position without downside protection.
:::

### 6. Context Compression

Each trading pair maintains its own context window. `SummaryAgentV2` compresses the conversation history when it grows too long, preserving key trade decisions and outcomes while reducing token costs.

### 7. Review & Learning

After each closed position, the `ReviewAgent` records the outcome and updates the experience database. The [Review System](../features/review-system.md) uses this to improve future decisions.

## Per-Symbol Independence

Each symbol in `trading.symbols` runs with:
- Its own Pydantic AI agent instance
- Its own conversation history and context window
- Its own experience/lesson database
- Independent cooldown timers for market monitor alerts

This means `BTC` and `ETH` agents never interfere with each other's context.

## Scheduling

The agent runs on two triggers:

1. **Scheduled interval** — `scheduler.interval_minutes` (default: 3 min) polls all symbols
2. **Volatility alert** — `MarketMonitor` triggers an immediate decision for a specific symbol when a price spike is detected

## Configuration

Key settings in `config.yaml`:

```yaml
trading:
  symbols: [BTC, ETH]
  max_trade_amount: 100
  max_leverage: 10

scheduler:
  interval_minutes: 3

prompt:
  set: nof1-improved
```

## Next Steps

- [FinCoT Reasoning](../features/fincot.md)
- [Bull/Bear Debate](../features/debate.md)
- [Regime Adaptive Strategy](../features/regime-adaptive.md)
- [Single Agent Backtesting](../backtesting/single.md)
