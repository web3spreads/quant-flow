---
sidebar_position: 1
title: FinCoT Structured Reasoning
description: 6-step financial chain-of-thought prompting for LLM trading decisions
---

# FinCoT Structured Reasoning

FinCoT (Financial Chain-of-Thought) is a structured prompting technique that forces the LLM to reason through trading decisions in a fixed 6-step sequence, instead of generating a conclusion directly.

## Academic Basis

**Paper**: [FinCoT (arXiv:2506.16123)](https://arxiv.org/abs/2506.16123)

**Reported results**:
- **+17.3 percentage points** accuracy improvement over vanilla prompting
- **−8.9× token consumption** compared to unconstrained chain-of-thought

## The 6-Step Reasoning Chain

Every trading decision follows this exact sequence:

| Step | Name | Question Asked |
|---|---|---|
| 1 | **Trend Confirmation** | Are multi-timeframe trends aligned? |
| 2 | **Entry Signal** | Which technical indicators triggered? (list exact values) |
| 3 | **Sentiment Check** | Any contrarian signals from funding rate / F&G / debate? |
| 4 | **Review Match** | Which historical lesson matches this setup? |
| 5 | **Risk Calculation** | Stop-loss/take-profit distance, R:R ratio, fee coverage |
| 6 | **Final Decision** | Synthesize steps 1–5 → LONG / SHORT / HOLD + confidence |

### Why This Works

- **Reduces hallucination** — the LLM cannot skip to a conclusion without completing each step
- **Forces evidence citation** — step 2 requires exact indicator values, not vague impressions
- **Contrarian gate** — step 3 prevents ignoring signals that conflict with the desired direction
- **Explicit risk math** — step 5 requires calculating fee coverage before committing to a trade

## How to Enable

FinCoT is built into the prompt templates. Select a FinCoT-enabled prompt set:

```yaml
# config.yaml
prompt:
  set: nof1-improved    # recommended — full FinCoT + enhanced data
```

All 8 prompt sets (`default`, `aggressive`, `conservative`, `nof1`, `nof1-improved`, `realtime`, `realtime-eng`) have FinCoT integrated.

:::info No Extra LLM Calls
FinCoT is a prompt engineering technique — it does **not** add extra API calls. The 6 steps are completed within a single LLM response.
:::

## Prompt Template Location

Templates are Jinja2 files under `prompts/`. The FinCoT structure is embedded in the system prompt. Key template variables:

| Variable | Source | Content |
|---|---|---|
| `{{ debate_summary }}` | Debate agent | Bull/bear arguments |
| `{{ volatility_alert }}` | Market monitor | Price spike context |
| `{{ regime_hint }}` | Regime adapter | Market state hint |
| `{{ cex_funding_signal }}` | Data enricher | CEX vs DEX funding divergence |
| `{{ onchain_summary }}` | Data enricher | MVRV, SOPR, Fear & Greed |

## Comparing Prompt Sets

| Set | Risk Tolerance | R:R Minimum | Best For |
|---|---|---|---|
| `default` | Moderate | 1.5 | General use |
| `conservative` | Low | 2.0 | Bear markets, uncertain conditions |
| `aggressive` | High | 1.2 | Strong trending markets |
| `nof1-improved` | Moderate | 1.5 | **Recommended**: full feature integration |
| `realtime` | Moderate | 1.5 | Fast-moving markets (uses price action over indicators) |

## Example LLM Output (Abridged)

```
Step 1 - Trend Confirmation:
  - 1h: Bullish (price above MA20, MA50, MA200)
  - 4h: Bullish (price above MA20, slight divergence at MA200)
  - Alignment: 2/2 confirmed bullish

Step 2 - Entry Signal:
  - RSI(14): 58.3 — momentum without overbought
  - MACD: bullish crossover 3 candles ago, histogram positive
  - Bollinger: price at mid-band, room to upper band

Step 3 - Sentiment Check:
  - Binance funding: +0.01% (slightly positive, no contrarian signal)
  - Fear & Greed: 62 (Greed) — mild caution
  - Debate: Bulls 72% vs Bears 28% — no strong contrarian signal

Step 4 - Review Match:
  - Matches "RSI momentum entry after consolidation" lesson (confidence: 0.78)
  - Lesson outcome: positive in 7/9 similar setups

Step 5 - Risk Calculation:
  - Entry: $43,250 | Stop: $42,100 (2.66%) | Target: $45,800 (5.90%)
  - R:R ratio: 2.21 ✓ (minimum 1.5)
  - Fee coverage: spread 0.05% × 2 = 0.10% vs 5.90% target = 1.7% of profit

Step 6 - Final Decision:
  Action: LONG | Confidence: 0.72 | Leverage: 5x
```

## Next Steps

- [Bull/Bear Debate](./debate.md) — the debate feeds into Step 3
- [Review System](./review-system.md) — historical lessons feed into Step 4
- [config.yaml Reference](../configuration/config-yaml.md) — prompt set selection
