## Spot DCA Evaluation: {{ symbol }}

**Task:** Evaluate whether {{ symbol }} is suitable for Spot Dollar-Cost Averaging (DCA).

---

### Market Data Analysis

**Current Indicators:**
- Current Price: ${{ current_price }}
- RSI (7 periods): {{ rsi_7 }}
- RSI (14 periods): {{ rsi_14 }}
- MACD: {{ macd }}
- Bollinger Band Position: {{ bb_position }} (0=Lower Band, 1=Upper Band)

**Multi-Timeframe Trends:**
{{ multi_timeframe_trends }}

**Moving Average System:**
- MA(7): ${{ ma_7 }}
- MA(25): ${{ ma_25 }}
- MA(99): ${{ ma_99 }}

**Account Information:**
- Available Cash: ${{ available_cash }}
- DCA Limit: ${{ dca_max_amount }}

---

### DCA Condition Checklist

Please check the following conditions one by one:

#### ✅ Asset Qualification
- [ ] Is it BTC or ETH? (Reject other assets directly)

#### ✅ Multi-Timeframe Trends
- [ ] Is the Daily timeframe in a downtrend?
- [ ] Is the 4H timeframe in a downtrend?
- [ ] Is the 1H timeframe in a downtrend?
- [ ] Do all three timeframes consistently show a deep downtrend?

#### ✅ Level of Oversold
- [ ] RSI < 30? (BTC can be relaxed to < 35)
- [ ] Is it deeply oversold?

#### ✅ Moving Average Alignment
- [ ] Price < MA(7)?
- [ ] MA(7) < MA(25)?
- [ ] MA(25) < MA(99)?
- [ ] Has a bearish alignment formed?

#### ✅ Bollinger Band Position
- [ ] BB Position < 0.2?
- [ ] Is it at an extreme position?

#### ✅ MACD Status
- [ ] Is the MACD Histogram negative?
- [ ] Has it persisted for multiple periods?

---

### Decision Requirements

If all conditions are met:
- Calculate a reasonable DCA amount (based on the level of oversold).
- Provide a detailed justification.
- Set a confidence score.

If conditions are not met:
- Clearly state the missing conditions.
- Choose `do_nothing`.
- Explain why it is not suitable for DCA.

**Output Format:**
```json
{
  "symbol": "{{ symbol }}",
  "action": "buy_spot" or "do_nothing",
  "amount": DCA Amount,
  "justification": "Detailed justification",
  "confidence": Confidence score between 0-1,
  "checklist_result": {
    "is_major_coin": true/false,
    "multi_timeframe_downtrend": true/false,
    "deep_oversold": true/false,
    "bearish_ma_alignment": true/false,
    "bollinger_extreme": true/false,
    "macd_bottom": true/false
  }
}
```

---

Remember: Spot DCA is a long-term investment. Execute only when there is extreme market panic and all conditions are met. Better to miss out than to force it!
