It has been {{ elapsed_minutes }} minutes since you started trading.

Below is various state data, price data, and prediction signals for you to identify alpha. Following this data is your current account information, value, performance, positions, etc.

**Order of all price or signal data: OLDEST → NEWEST**

**Timeframe Note:** Unless otherwise stated in the section title, intraday sequences are provided at **3-minute intervals**. If a coin uses a different interval, it will be clearly stated in that coin's section.

---

## Current Market State of All Assets

### {{ symbol }} Data

**Current Indicators:**
- Current Price: ${{ current_price }}
- Current EMA(20): ${{ current_ema20 }}
- Current MACD: {{ current_macd }}
- Current RSI (7 periods): {{ current_rsi }}

**Perpetual Contract Data:**
- Open Interest: Latest: {{ oi_latest }} | Average: {{ oi_average }}
- Funding Rate: {{ funding_rate }}

**Intraday Sequence (minute by minute, Oldest → Newest):**

Mid Prices: {{ mid_prices }}

EMA Indicators (20 periods): {{ ema_indicators }}

MACD Indicators: {{ macd_indicators }}

RSI Indicators (7 periods): {{ rsi_7_indicators }}

RSI Indicators (14 periods): {{ rsi_14_indicators }}

**Long-Term Context (4-Hour Timeframe):**

- 20-period EMA: {{ ema_20_4h }} vs. 50-period EMA: {{ ema_50_4h }}
- 3-period ATR: {{ atr_3_4h }} vs. 14-period ATR: {{ atr_14_4h }}
- Current Volume: {{ current_volume }} vs. Average Volume: {{ avg_volume }}

MACD Indicators (4-Hour): {{ macd_4h_indicators }}

RSI Indicators (14-period, 4-Hour): {{ rsi_14_4h_indicators }}

**📊 Technical Indicator Analysis (Basic Interpretation for Reference):**

- Price Trend: {{ price_trend_analysis }}
- MACD Status: {{ macd_analysis }}
- RSI Status: {{ rsi_analysis }}
- EMA Relationship: {{ ema_analysis }}
- Volume: {{ volume_analysis }}
- 4H Trend: {{ h4_trend_analysis }}
- Composite Signal: {{ composite_signal }}

---

## Account Information & Performance

**Current Total Return (%):** {{ total_return_pct }}%

**Available Cash:** ${{ available_cash }}

**Current Account Value:** ${{ account_value }}

**Current Positions & Performance:**

{{ position_details_text }}

**Sharpe Ratio:** {{ sharpe_ratio }}

---

## Decision Guidelines

### Core Principles:

1. **Fee Awareness**: Expected profit must exceed fee costs (0.07%) by at least 3-5x.
2. **Basic Positioning Logic**:
   - Do not close a position if you do not hold one.
   - Do not open duplicate positions.
   - Do not hold both long and short positions simultaneously.
3. **Multi-Timeframe Confirmation**: Prioritize directions where trends across multiple timeframes align.
4. **Comprehensive Technical Analysis**: Combine MACD, RSI, EMA, Volume, and other indicators.

### Open Long (Buy):
- Clear bullish market signals.
- Technical indicators show an uptrend or oversold bounce.
- Volume supports the price increase.

### Close Long (Sell):
- Must already hold a long position.
- Technical indicators show bullish momentum exhaustion or a turn to bearish.
- Take-profit target reached or stop-loss triggered.

### Open Short (Sell Short):
- Clear bearish market signals.
- Technical indicators show a downtrend or overbought reversal.
- Volume supports the price decrease.

### Close Short (Buy to Cover):
- Must already hold a short position.
- Technical indicators show bearish momentum exhaustion or a turn to bullish.
- Take-profit target reached or stop-loss triggered.

### Hold (Wait):
- Market signals are unclear or balanced.
- Multi-timeframe trends are inconsistent.
- Expected profit cannot cover fee costs by 3-5 times.

---

## Position & Leverage Suggestions

- **High Confidence** (Strong resonance across multiple indicators): Use larger position size (60-100%) and higher leverage (5-8x).
- **Medium Confidence** (Supported by some indicators): Use medium position size (40-60%) and medium leverage (3-5x).
- **Low Confidence** (Weak signal): Use small position size (20-40%) and low leverage (2-3x), or wait.

---

## Now, please make your decision!

**Output Decision Analysis**
- Clear trading signal.
- If opening a position, must specify position size and leverage.
- Clear justification for the decision, including:
  * Which technical conditions were met.
  * Signal strength assessment (Strong/Medium/Weak).
  * Reasons for choosing the position size and leverage.
  * Ratio of expected profit to fee costs.
  * Risk-reward analysis.
