---
sidebar_position: 2
title: Bull/Bear Debate Agent
description: Multi-agent debate to eliminate confirmation bias in trading decisions
---

# Bull/Bear Debate Agent

The debate feature uses two independent LLM agents to argue opposing sides before every trade. This eliminates the confirmation bias that occurs when a single agent naturally gravitates toward a direction.

## Academic Basis

**Paper**: [TradingAgents (arXiv:2412.20138)](https://arxiv.org/abs/2412.20138)

**Key finding**: Multi-agent frameworks comprehensively outperform single-agent baselines across all tested metrics in trading decision tasks.

## How It Works

```
Market Data
     ↓          ↓
BullAgent    BearAgent
(forced      (forced
 bullish)     bearish)
     ↓          ↓
  3 arguments  3 arguments
  + confidence + confidence
        ↓
  Debate Summary
        ↓
  Injected into main
  decision prompt as
  {{ debate_summary }}
```

### BullAgent

Receives the same market data as the main agent but is **forced to argue bullish**. It must:
- Provide exactly 3 arguments supporting a long position
- Assign a confidence score to its bull case
- Cite specific data points (indicator values, price levels)

### BearAgent

Receives the same data but is **forced to argue bearish**. It must:
- Provide exactly 3 counter-arguments supporting a short or hold
- Assign a confidence score to its bear case
- Cite specific data points

### Summary Injection

The combined output is summarized and injected into the main decision prompt at `{{ debate_summary }}`. The main agent then uses this in **Step 3 (Sentiment Check)** of the [FinCoT chain](./fincot.md).

## Enabling the Debate

```yaml
# config.yaml
debate:
  enabled: true    # adds 2 extra LLM calls per decision cycle
```

:::info Token Cost
When debate is enabled, each decision cycle makes **3 LLM calls total**:
1. BullAgent
2. BearAgent
3. Main decision agent

Budget accordingly if you're on a metered API plan.
:::

## Example Debate Output

```
=== Bull Arguments (confidence: 0.68) ===
1. RSI at 58 shows momentum building without overbought conditions.
   Clear room to run to 70 before mean reversion pressure.

2. Funding rate at +0.008% — well below the +0.03% threshold that
   historically signals crowded longs. No squeeze risk.

3. Binance spot CVD positive for 4 consecutive hours, indicating
   net buyer accumulation at current levels.

=== Bear Arguments (confidence: 0.44) ===
1. Price rejected the $43,500 resistance level twice in 6 hours.
   Third test rarely breaks cleanly without consolidation.

2. 4H MACD histogram shrinking — momentum deceleration before
   the daily trend has fully turned bullish.

3. BTC dominance rising (+0.3% today) suggests altcoin rotation
   away, reducing ETH's relative strength.

=== Summary ===
Bulls present moderate conviction with funding and CVD support.
Bears flag resistance rejection and momentum deceleration.
Net sentiment: Cautiously bullish with reduced position sizing recommended.
```

## Why Forced Arguments?

Without the forced constraint, a single LLM asked to "consider both sides" will:
1. Generate a weak straw-man counterargument
2. Immediately dismiss it
3. Confirm its initial bias

By using two **separate** agents with **mandatory** position constraints, each side is argued with full effort.

## Core Code

The debate logic lives in `src/agent/debate.py`:

```python
async def run_bull_bear_debate(
    market_data: dict,
    symbol: str,
    llm_client: BaseLLM,
) -> DebateResult:
    bull_result = await run_bull_agent(market_data, symbol, llm_client)
    bear_result = await run_bear_agent(market_data, symbol, llm_client)
    return summarize_debate(bull_result, bear_result)
```

## Interaction with Other Features

| Feature | Interaction |
|---|---|
| **FinCoT** | Debate summary feeds into Step 3 (Sentiment Check) |
| **Regime Adaptive** | In trending regimes, the bull/bear confidence delta is used to amplify the regime signal |
| **Market Monitor** | Debate is still run for volatility-triggered decisions (same cost applies) |

## When Debate Adds the Most Value

- **Choppy / ranging markets** — prevents over-committing to a direction
- **Near key resistance/support** — forces explicit argument about rejection probability
- **After a losing streak** — counters the human-like tendency to reverse-bias after losses (the review system can exhibit this)

## Next Steps

- [FinCoT Reasoning](./fincot.md)
- [CEX Signals & On-chain Data](./cex-signals.md)
- [config.yaml Reference](../configuration/config-yaml.md)
