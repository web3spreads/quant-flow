---
sidebar_position: 1
title: System Architecture
description: Quant Flow system architecture and data flow
---

# System Architecture

Quant Flow is built as a modular, multi-agent system on Pydantic AI. The perpetual futures agent and grid market-making strategies share core infrastructure but operate in a unified system.

## High-Level Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         Quant Flow                              │
│                                                                 │
│              ┌───────────────────────────────────┐              │
│              │            main.py                │              │
│              │       (Unified Runner)            │              │
│              │                                   │              │
│              │  ┌───────────────┐ ┌────────────┐ │              │
│              │  │  Perp Agent   │ │ Grid Flow  │ │              │
│              │  │  (Multi-Agent)│ │ (Dynamic)  │ │              │
│              │  └───────┬───────┘ └─────┬──────┘ │              │
│              └──────────┼───────────────┼────────┘              │
│                         │               │                       │
│                         └───────┬───────┘                       │
│                                 ↓                               │
│                   ┌─────────────────────────┐                   │
│                   │   Shared Infrastructure │                   │
│                   │  HyperliquidClient      │                   │
│                   │  OrderManager           │                   │
│                   │  MarketData + Indicators│                   │
│                   │  LLMClientManager       │                   │
│                   └─────────────────────────┘                   │
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
│  ProtectionManager  — plugin-based CB      │
│   (drawdown / daily / consec / timeout)    │
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
│  │  alerts → pending_queue             │               │
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

## Pydantic AI Agent Workflow

Each symbol is managed by an independent `SingleSymbolAgent` built on Pydantic AI:

1. **Context Collection**: The main loop gathers technical indicators, CEX funding rates, and on-chain sentiment.
2. **Pydantic AI Run**: The agent is executed with the dynamic system prompt and structured input data, using `RunContext` to access dependency clients.
3. **Tool Dispatching**: Pydantic AI calls the appropriate registered trading tool (`buy`, `sell`, `sell_short`, `buy_to_cover`, `do_nothing`) based on model reasoning.
4. **Validation & Execution**: Order execution parameters are verified against the risk protection layer, and size is adjusted via the Kelly Criterion before placing orders.
5. **State Summary**: `SummaryAgentV2` compresses the output to maintain a highly-condensed memory context.

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
