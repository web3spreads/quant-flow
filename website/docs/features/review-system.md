---
sidebar_position: 6
title: Review & Reflection System
description: 6-layer learning system — instant reflection, weekly reflection, regime-aware memory, bias protection, fact-subjective split, prompt meta-reflection
---

# Review & Reflection System

The review system gives Quant Flow a memory that persists across trading sessions and improves over time. It consists of 6 independently toggleable enhancements, each backed by academic research.

## Overview

```
Trade closes
    ↓
6a: InstantReflector      — rule-based, no LLM, confidence update
    ↓
ReviewAgent               — stores lesson in experience database
    ↓
6b: Regime tag            — lesson tagged with market regime
6c: Lesson type tag       — positive / negative / unknown
6d: Source type tag       — factual / subjective / mixed
    ↓
Next decision
    ↓
ReviewAgent.get_similar_lessons()
    → filters by regime match (6b)
    → applies bias correction (6c)
    → applies fact/subjective weighting (6d)
    ↓
VFT section injected into prompt (Step 4: Review Match)

Weekly trigger:
6a: WeeklyReflector       — LLM-based strategy analysis
6e: PromptMetaReflector   — suggests prompt improvements
```

---

## Enhancement 6a: Dual-Granularity Reflection

**Paper**: [Adaptive Multi-Agent Bitcoin Trading (arXiv:2510.08068)](https://arxiv.org/abs/2510.08068)

### Instant Reflection (after every trade close)
- Pure rule-based, **no LLM call**
- If the trade was profitable: matched lesson confidence × 1.05
- If the trade was unprofitable: matched lesson confidence × 0.95
- Ultra-fast — completes in milliseconds

### Weekly Reflection (every Monday at 8:00 by default)
- Full LLM call — analyzes the past week's trades
- Detects systematic biases (e.g., "consistently held too long in ranging markets")
- Generates strategy-level adjustment suggestions
- Produces a summary report saved to `logs/`

```yaml
review_agent:
  instant_reflection_enabled: true
  weekly_reflection_enabled: true
  weekly_reflection_day: 0     # 0=Monday
  weekly_reflection_hour: 8
```

---

## Enhancement 6b: Regime-Aware Memory

**Paper**: Adaptive Memory for Bitcoin Regime Detection (engrXiv 2025)

Every lesson is stored with a `source_regime` field (`trending` / `ranging` / `volatile` / `unknown`). When retrieving similar lessons for a new decision:

- **Regime matches**: normal similarity score
- **Regime mismatches**: similarity score × `regime_mismatch_factor` (default 0.4)

The Verbal Fine-Tuning (VFT) section in the prompt labels lessons:
```
[趋势市经验] / [Trending Market Lesson]
[震荡市经验] / [Ranging Market Lesson]
```

```yaml
review_agent:
  regime_aware_enabled: true
  regime_mismatch_factor: 0.4
```

---

## Enhancement 6c: Confirmation Bias Protection

**Paper**: [FinCon (arXiv:2407.06567)](https://arxiv.org/abs/2407.06567) + Selective Memory Equilibrium

Without protection, the experience database naturally fills with positive lessons (trades that worked) while negative lessons (mistakes) get evicted. This creates a false picture of past performance.

**Protection mechanisms**:
- Maximum ratio of positive lessons enforced (`max_positive_ratio: 0.7` → at most 70% positive)
- When evicting old lessons, negative lessons are protected from disproportionate removal
- Negative lessons get a confidence boost (`negative_confidence_boost: 1.15`) so they rank higher in retrieval

In the VFT prompt section, negative lessons are prefixed with `[Avoid]`:
```
[Avoid] Do not enter long on RSI > 72 during ranging markets — 
historically results in mean-reversion losses.
```

```yaml
review_agent:
  bias_protection_enabled: true
  max_positive_ratio: 0.7
  negative_confidence_boost: 1.15
```

---

## Enhancement 6d: Fact-Subjective Split

**Paper**: [FS-ReasoningAgent (arXiv:2410.12464, ICLR 2025)](https://arxiv.org/abs/2410.12464)

Lessons are tagged by source type:
- **Factual**: based on hard indicator data (RSI value, price level, funding rate)
- **Subjective**: based on interpretation or qualitative analysis (sentiment, news impact)
- **Mixed**: combination of both

**Dynamic weighting**:
- Trending markets: subjective lessons boosted (`trending_subjective_boost: 1.3`)
- Ranging/volatile markets: factual lessons boosted (`ranging_factual_boost: 1.3`)

In the VFT section, lessons are labeled `[Factual]` or `[Subjective]` so the LLM can appropriately weight them.

```yaml
review_agent:
  fact_subjective_split_enabled: true
  trending_subjective_boost: 1.3
  ranging_factual_boost: 1.3
```

---

## Enhancement 6e: Prompt Meta-Reflection

**Paper**: [ATLAS Adaptive-OPRO (arXiv:2510.15949)](https://arxiv.org/abs/2510.15949)

After each weekly reflection, `PromptMetaReflector` evaluates prompt quality on 4 dimensions:

| Dimension | What It Measures |
|---|---|
| FinCoT completeness | Did the LLM complete all 6 steps? |
| Lesson citation rate | How often did it reference past lessons? |
| Decision consistency | Did confidence scores align with outcomes? |
| Confidence calibration | Were high-confidence decisions actually more accurate? |

Based on the evaluation, it generates specific prompt improvement suggestions. These are **saved to `logs/prompt_optimization/`** for **human review** — they are not automatically applied.

```yaml
review_agent:
  prompt_meta_reflection_enabled: false
  prompt_optimization_dir: "logs/prompt_optimization"
```

:::info Human-in-the-Loop
Prompt meta-reflection generates suggestions but does not modify prompts automatically. A human must review and apply changes to files in `prompts/`.
:::

---

## All Configuration Options

```yaml
review_agent:
  # 6a
  instant_reflection_enabled: false
  weekly_reflection_enabled: false
  weekly_reflection_day: 0
  weekly_reflection_hour: 8

  # 6b
  regime_aware_enabled: false
  regime_mismatch_factor: 0.4

  # 6c
  bias_protection_enabled: false
  max_positive_ratio: 0.7
  negative_confidence_boost: 1.15

  # 6d
  fact_subjective_split_enabled: false
  trending_subjective_boost: 1.3
  ranging_factual_boost: 1.3

  # 6e
  prompt_meta_reflection_enabled: false
  prompt_optimization_dir: "logs/prompt_optimization"
```

## Recommended Starter Config

Start with 6a and 6c — they have the most immediate impact and lowest cost:

```yaml
review_agent:
  instant_reflection_enabled: true
  bias_protection_enabled: true
```

Add 6b and 6d after you have at least 50 closed trades in the database (regime-aware memory needs sufficient history to be meaningful).

## Next Steps

- [FinCoT Reasoning](./fincot.md) — Step 4 uses review lessons
- [Regime Adaptive](./regime-adaptive.md) — 6b depends on regime detection
- [A/B Comparison Backtesting](../backtesting/comparison.md)
