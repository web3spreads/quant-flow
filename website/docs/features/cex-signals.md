---
sidebar_position: 3
title: CEX Leading Signals & On-Chain Data
description: Binance funding rate, Fear & Greed index, and MVRV/SOPR on-chain signals
---

# CEX Leading Signals & On-Chain Data

Quant Flow enriches every trading decision with three external data sources that have academic backing as leading indicators for crypto price action.

## Academic Basis

| Source | Paper | Key Finding |
|---|---|---|
| CEX Funding Rate | [MDPI Mathematics 2026](https://www.mdpi.com/2227-7390/14/2/346) | CEX price discovery is 61% more efficient than DEX |
| MVRV / SOPR | [ScienceDirect 2025](https://www.sciencedirect.com/science/article/pii/S266682702500057X) | MVRV and SOPR validated as strong directional signals |
| Fear & Greed | Contrarian indicator (widely replicated) | Extreme readings predict reversals |

## Three Data Sources

### 1. Binance CEX Funding Rate

**What it is**: Binance perpetual futures funding rate for the same asset being traded on Hyperliquid.

**Signal logic**: When the Binance funding rate diverges significantly from the Hyperliquid funding rate, it's a leading indicator:
- Binance funding spikes positive → CEX longs crowded → potential reversal signal
- Binance funding negative while HL is neutral → short pressure building on CEX

**API**: Public Binance Futures API (no key required).

**Template variable**: `{{ cex_funding_signal }}`

### 2. Fear & Greed Index

**What it is**: [alternative.me](https://alternative.me/crypto/fear-and-greed-index/) composite sentiment index (0 = Extreme Fear, 100 = Extreme Greed).

**Signal logic** (contrarian):

| Reading | Signal |
|---|---|
| 0–20 (Extreme Fear) | Contrarian buy signal |
| 20–40 (Fear) | Mild bullish lean |
| 40–60 (Neutral) | No signal |
| 60–80 (Greed) | Mild bearish lean |
| 80–100 (Extreme Greed) | Contrarian sell signal |

**Template variable**: included in `{{ onchain_summary }}`

### 3. On-Chain: MVRV & SOPR

**MVRV (Market Value to Realized Value)**:
- **MVRV > 3.5**: Market overheated, historically precedes major corrections
- **MVRV < 1.0**: Market undervalued, historically precedes major rallies

**SOPR (Spent Output Profit Ratio)**:
- **SOPR > 1**: Coins are being spent at a profit — trend continuation or distribution
- **SOPR < 1**: Coins spent at a loss — potential capitulation / reversal

**API**: blockchain.info and Glassnode public feeds.

**Template variable**: `{{ onchain_summary }}`

## Enabling Data Enrichment

```yaml
# config.yaml
enhanced_analysis:
  enabled: true    # base switch — enables all external data collection
```

All three sources are collected automatically when `enhanced_analysis.enabled: true`.

## Graceful Degradation

:::info Auto-Degradation
If any external API is unavailable (network error, rate limit, service down), the system:
1. Logs a warning
2. Omits that data from the prompt
3. **Continues trading** — decisions are not blocked

Trading never halts due to missing enrichment data.
:::

## How Data Is Injected

The `MarketDataEnricher` class (`src/data/data_enricher.py`) collects all external data and formats it for prompt injection:

```python
enriched = await enricher.enrich(symbol, market_data)

# enriched contains:
# - cex_funding_signal: str  (narrative description)
# - onchain_summary: str     (MVRV + SOPR + F&G in one paragraph)
# - fear_greed_value: int    (raw 0-100 score)
# - mvrv: float | None
# - sopr: float | None
```

These are passed to `PromptManager` and rendered into the Jinja2 template.

## Interaction with FinCoT

CEX signals and on-chain data feed directly into **Step 3 (Sentiment Check)** of the [FinCoT reasoning chain](./fincot.md):

```
Step 3 - Sentiment Check:
  - Binance funding: +0.021% vs HL +0.008% → CEX premium building
  - Fear & Greed: 74 (Greed) → slight contrarian caution
  - MVRV: 2.1 → neutral (below 3.5 overheating threshold)
  - SOPR: 1.04 → coins spent at mild profit, continuation signal
```

## Interaction with Debate Agent

When [Debate](./debate.md) is enabled, both BullAgent and BearAgent also receive the enriched data. This allows the bears to argue MVRV overheating while bulls argue SOPR continuation — a more realistic debate.

## Performance Impact

- **Latency**: External API calls add ~500ms–2s per decision cycle (async, parallel fetching)
- **Token cost**: ~200–400 additional tokens per decision (the summaries are kept concise)
- **Rate limits**: Binance and alternative.me public APIs have generous limits; blockchain.info may throttle under heavy use

## Next Steps

- [Market Regime Adaptive](./regime-adaptive.md) — regime detection also uses enriched data
- [FinCoT Reasoning](./fincot.md)
- [config.yaml Reference](../configuration/config-yaml.md)
