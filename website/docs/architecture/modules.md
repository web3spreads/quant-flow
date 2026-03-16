---
sidebar_position: 2
title: Module Reference
description: Detailed reference for all source modules in Quant Flow
---

# Module Reference

Complete reference for all modules under `src/`.

## Directory Structure

```
src/
├── agent/          # LLM agent implementations
├── trading/        # Order execution and risk modules
├── data/           # Market data and indicators
├── utils/          # Shared utilities
├── llm/            # LLM client abstraction
├── backtest/       # Backtest engine
└── notification/   # Alert and notification system
```

---

## `src/agent/`

### `single_symbol_agent.py`
Base single-symbol trading agent. Handles the LangGraph state graph, context window management, and decision cycle for one trading pair.

### `enhanced_single_symbol_agent.py`
Extends the base agent with:
- Debate integration
- Regime adaptive parameters
- Volatility alert context
- Enhanced data enrichment

**Key method**: `trading_cycle(triggered_by_alert=False)`

### `grid_agent.py`
AI decision engine for grid trading. Calls the LLM to determine grid direction (LONG/SHORT/NEUTRAL) and suggested grid width percentage.

### `debate.py`
Multi-agent debate engine.

```python
async def run_bull_bear_debate(
    market_data: dict,
    symbol: str,
    llm_client: BaseLLM
) -> DebateResult
```

### `summary_agent_v2.py`
Context compression agent. Compresses long conversation histories into concise summaries to keep token usage bounded.

### `review_agent.py`
Lesson storage and retrieval. Stores trade outcomes as structured lessons with metadata (regime, lesson type, source type).

**Key methods**:
- `store_lesson(trade_result, regime, lesson_type, source_type)`
- `get_similar_lessons(current_state, regime, top_k=5)`
- `get_verbal_finetuning_section(regime) → str`

### `instant_reflection.py`
Rule-based instant reflector. No LLM — updates lesson confidence scores based on trade outcome.

```python
class InstantReflector:
    def reflect(self, closed_trade: ClosedTrade) -> None
```

### `weekly_reflection.py`
LLM-based weekly strategy reflector. Runs on schedule (`weekly_reflection_day` + `weekly_reflection_hour`).

### `prompt_meta_reflection.py`
Prompt quality evaluator. Scores 4 dimensions and produces improvement suggestions (human review required).

### `generalization.py`
Experience generalizer. Abstracts specific trade lessons into general principles to reduce overfitting.

### `execution_agent.py`
Converts LLM narrative decisions into structured `ExecutionPlan` Pydantic models with validated fields.

### `helpers.py`
Shared utility functions for agents (formatting, data transformation).

---

## `src/trading/`

### `client.py`
Hyperliquid SDK wrapper.

**Critical safety mechanism**:
```python
# If stop-loss placement fails after opening a position,
# immediately close the position (up to 3 retries)
if require_stop_loss and not stop_loss_success:
    for attempt in range(1, max_rollback_retries + 1):
        rollback_result = self.close_position(symbol, size)
        if rollback_success:
            break
```

**Key methods**:
- `place_order(symbol, side, size, price, order_type)`
- `close_position(symbol, size)`
- `get_position(symbol) → Position`
- `get_account_balance() → float`
- `all_mids() → dict[str, float]`

### `order_manager.py`
High-level order management. Handles limit order take-profit and stop-loss placement, order tracking, and fill detection.

**Config flag**: `limit_order_enabled` — toggles limit vs. market order entry.

### `grid_manager.py`
Grid order lifecycle management.

**Key responsibilities**:
- Grid level calculation and placement
- Fill detection and exit order placement
- Orphan order cleanup
- Atomic state persistence to `grid_state.json`
- Cancel hard timeout (20s)

### `decision_validator.py`
Multi-dimensional pre-trade validation.

```python
class ValidationResult(Enum):
    PASS = "pass"
    WARN = "warn"
    BLOCK = "block"
```

Validation checks:
- Multi-timeframe trend alignment
- Signal quality threshold
- Risk/reward ratio
- Market state suitability
- Entry timing quality

### `position_sizer.py`
Kelly criterion position sizing with multiple sizing methods:

```python
class PositionSizeMethod(Enum):
    KELLY = "kelly"
    VOLATILITY_ADJUSTED = "volatility_adjusted"
    SIGNAL_BASED = "signal_based"
```

### `risk_manager.py`
ATR-based dynamic stop-loss and take-profit calculation.

**Defaults**:
- `max_risk_per_trade: 0.02` (2% of account)
- `max_total_exposure: 0.50` (50% of account)
- `min_risk_reward_ratio: 1.5`

### `account_protector.py`
Account-level circuit breakers.

```python
class ProtectionAction(Enum):
    PAUSE_NEW_TRADES = "pause_new_trades"
    CLOSE_LOSING_POSITIONS = "close_losing_positions"
    CLOSE_ALL_POSITIONS = "close_all_positions"
```

### `enhanced_engine.py`
Regime-adaptive parameter override engine. Applies `regime_adaptive` config parameters over the base trading parameters.

---

## `src/data/`

### `market_data.py`
OHLCV data fetching and caching. Downloads candles from Hyperliquid and caches locally.

### `indicators.py`
Technical indicator calculations:
- Moving Averages: MA20, MA50, MA200
- RSI(14)
- MACD(12, 26, 9)
- Bollinger Bands(20, 2)
- ATR(14)

### `data_enricher.py`
External data collection.

```python
class MarketDataEnricher:
    async def enrich(self, symbol, market_data) -> EnrichedData
```

Fetches: Binance funding rate, Fear & Greed index, MVRV, SOPR.

### `market_monitor.py`
Background thread price monitor.

```python
class MarketMonitor:
    def start() → None          # launches background thread
    def stop() → None
    def get_pending_alert(symbol) → VolatilityAlert | None
```

### `market_state.py`
11-state market state classifier.

```python
class MarketState(Enum):
    STRONG_UPTREND, UPTREND, WEAK_UPTREND,
    STRONG_DOWNTREND, DOWNTREND, WEAK_DOWNTREND,
    RANGING_HIGH, RANGING_LOW, RANGING,
    HIGH_VOLATILITY, UNCERTAIN
```

### `signal_scorer.py`
Multi-factor signal scoring with regime-adaptive weights.

### `regime_adapter.py`
Maps `MarketState` → regime (`trending`/`ranging`/`volatile`) and looks up parameter overrides.

---

## `src/utils/`

### `hyperliquid.py`
Safe Hyperliquid SDK initialization. Filters `spotMeta` index-out-of-bounds errors that can occur on certain asset listings.

### `grid_math.py`
Grid parameter math engine.

```python
def calculate_grid_config(
    current_price: float,
    direction: str,
    ai_width_pct: float,
    atr: float,
    total_investment: float,
    config: GridConfig,
) -> GridConfig
```

### `logger.py`
Structured logger setup. Configures file handler (tee to `logs/`) and console handler.

---

## `src/llm/`

### `llm_client.py`
Multi-provider LLM client factory.

```python
class LLMClientManager:
    @classmethod
    def get_client(cls, config: LLMConfig) -> BaseLLM
```

Supported providers: OpenAI, NVIDIA NIM, Google Gemini, Cloudflare Workers AI, LiteLLM.

---

## `src/backtest/`

Backtest engine modules: data replay, trade simulation, P&L tracking, report generation. Used by `backtest.py` and `backtest_comparison.py`.

---

## `src/notification/`

Alert and notification modules. Supports logging-based alerts and can be extended for Telegram/Discord notifications.

## Next Steps

- [Architecture Overview](./overview.md)
- [Perpetual Agent Strategy](../strategies/perpetual-agent.md)
- [Grid Flow Strategy](../strategies/grid-flow.md)
