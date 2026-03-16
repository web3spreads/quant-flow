---
sidebar_position: 1
title: System Architecture
description: Quant Flow system architecture and data flow
---

# System Architecture

Quant Flow is built as a modular, multi-agent system on LangChain/LangGraph. Two strategies share core infrastructure but operate independently.

## High-Level Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         Quant Flow                              │
│                                                                 │
│  ┌───────────────────────┐   ┌──────────────────────────────┐  │
│  │   Perpetual Agent     │   │       Grid Flow              │  │
│  │   (main.py)           │   │   (grid_main.py)             │  │
│  │                       │   │                              │  │
│  │  MultiAgent/LangGraph │   │  GridAgent + GridManager     │  │
│  │  One agent per symbol │   │  AI direction + Math engine  │  │
│  └──────────┬────────────┘   └─────────────┬────────────────┘  │
│             │                              │                   │
│             └──────────────┬───────────────┘                   │
│                            ↓                                   │
│              ┌─────────────────────────┐                       │
│              │   Shared Infrastructure  │                       │
│              │  HyperliquidClient       │                       │
│              │  OrderManager            │                       │
│              │  MarketData + Indicators │                       │
│              │  LLMClientManager        │                       │
│              └─────────────────────────┘                       │
└─────────────────────────────────────────────────────────────────┘
```

## Perpetual Agent Data Flow

```
ExternalInfoAgent (Exa API market news)
         ↓
MarketData (OHLCV + technical indicators)
 + DataEnricher (CEX funding, F&G, MVRV/SOPR)
         ↓
EnhancedSingleSymbolAgent ←── MarketMonitor (volatility alerts, 30s)
 │  FinCoT 6-step prompt
 │  + {{ debate_summary }}      ← BullAgent + BearAgent
 │  + {{ regime_hint }}         ← RegimeAdapter
 │  + {{ cex_funding_signal }}  ← DataEnricher
 │  + {{ volatility_alert }}    ← MarketMonitor
         ↓
ExecutionAgent (Pydantic structured output: ExecutionPlan)
         ↓
┌────────────────────────────────────────────┐
│  Validation & Sizing Layer                 │
│  DecisionValidator  — multi-dim checks     │
│  PositionSizer      — Kelly criterion      │
│  RiskManager        — ATR-based SL/TP      │
│  AccountProtector   — drawdown/timeout CB  │
└────────────────────────┬───────────────────┘
         ↓
OrderManager → HyperliquidClient
 [Safety: SL fail → auto-close (3 retries)]
         ↓
SummaryAgentV2 (context compression)
ReviewAgent    (lesson storage + reflection)
```

## Grid Flow Data Flow

```
MarketData (OHLCV 15m)
         ↓
GridAgent (LLM direction: LONG/SHORT/NEUTRAL + width%)
         ↓
calculate_grid_config (math engine)
 65% ATR-based width + 35% AI suggestion
         ↓
GridManager
 ├── Place limit orders at each price level
 ├── Monitor fills (via HyperliquidClient sync)
 ├── Place exit orders (TP/SL/reduce-only)
 └── Atomic state save (grid_state.json)
         ↓
HyperliquidClient + OrderManager
```

## Component Interaction Map

```
config.yaml ──────────────────────────────────────────────────────┐
                                                                   │
.env ──────────────────────────────────────────────────────────────┤
                                                                   ↓
                                              ┌──────────────────────┐
                                              │  LLMClientManager    │
                                              │  (singleton)         │
                                              └──────────┬───────────┘
                                                         │ used by all agents
┌────────────────────────────────────────┐               │
│  MarketMonitor (background thread)     │               │
│  polls all_mids() every 30s            │               │
│  → alerts → pending_queue              │               │
└───────────────────┬────────────────────┘               │
                    │ trigger                             │
                    ↓                                     ↓
              main.py event loop ◄─── scheduler (3 min fallback)
                    │
                    ↓ for each symbol
        EnhancedSingleSymbolAgent
                    │
          ┌─────────┼─────────┐
          ↓         ↓         ↓
     DebateAgent RegimeAd. DataEnricher
          └─────────┼─────────┘
                    ↓
            ExecutionAgent
                    │
            Validation Layer
                    │
            OrderManager ──→ HyperliquidClient ──→ Hyperliquid DEX
                    │
            ReviewAgent ──→ Experience DB (JSON)
                    │
            InstantReflector (no LLM)
            WeeklyReflector  (LLM, scheduled)
```

## LangGraph State Machine

Each `EnhancedSingleSymbolAgent` is a LangGraph graph with these nodes:

```
START
  → collect_market_data
  → enrich_data (CEX, on-chain)
  → [conditional] run_debate (if enabled)
  → detect_regime (if enabled)
  → make_decision (LLM with FinCoT)
  → validate_decision
  → execute_or_hold
  → post_trade_review
END
```

State is maintained per symbol across cycles, enabling context compression via `SummaryAgentV2`.

## Design Principles

| Principle | Implementation |
|---|---|
| **Feature isolation** | All enhancements are config-flag-gated, default off |
| **Fail-safe execution** | SL failure → immediate position close with retries |
| **Independent symbols** | No shared state between BTC/ETH agents |
| **Atomic persistence** | State files written via tempfile + rename |
| **Graceful degradation** | External API failures don't block trading |
| **Lazy imports** | `src/agent/__init__.py` uses `__getattr__` to prevent circular imports |

## Next Steps

- [Module Reference](./modules.md) — detailed per-module documentation
- [Perpetual Agent Strategy](../strategies/perpetual-agent.md)
- [Grid Flow Strategy](../strategies/grid-flow.md)
