You will see the latest trading records, statistics, and old rules of thumb for {{ symbol }}. Please output a JSON result based on these inputs to refine actionable lessons and help future trades make better decisions.

**Focus Points (Reference, not strict requirements):**
- Performance in low volatility/no volume conditions.
- Impact of risk-reward ratio and fee costs.
- Match between signal quality and position/leverage.
- Common patterns in successful and failed trades.
- Distill characteristics of trend/volume/volatility/price structure/position & leverage in successes.
- Identify common issues in failures (noise zone, insufficient volume, poor R:R, or oversized positions).

### Recent Decisions ({{ decision_digest|length }} items)
{% for item in decision_digest %}
- {{ item.timestamp }} | {{ item.decision }} | Price ${{ "%.2f"|format(item.price) }} | Result {{ item.result }} | Reason {{ item.reason }}
{% endfor %}

### Decision Statistics
- Total Count: {{ stats.total_decisions }}
- Buy: {{ stats.buy_count }}, Sell: {{ stats.sell_count }}, Sell Short: {{ stats.sell_short_count }}, Buy to Cover: {{ stats.buy_to_cover_count }}, Do Nothing: {{ stats.idle_count }}
- Average Price: ${{ "%.2f"|format(stats.average_price) }}, Max Price: ${{ "%.2f"|format(stats.max_price) }}, Min Price: ${{ "%.2f"|format(stats.min_price) }}

### Current Environment Signals
{% set cf = context_features or {} %}
- RSI: {{ cf.get("rsi", "n/a") }} | MACD: {{ cf.get("macd_signal", "n/a") }} | EMA trend: {{ cf.get("ema_trend", "n/a") }}
- Trend: {{ cf.get("trend_direction", "n/a") }} | Volatility: {{ cf.get("volatility_level", "n/a") }}
- Volume vs avg: {{ "%.2f"|format(cf.get("volume_ratio", 1.0)) }} | Price position (0-1): {{ "%.2f"|format(cf.get("price_position", 0.5)) }}
- Time of day: {{ cf.get("time_of_day", "n/a") }}

### Existing Lessons (if empty, means none)
{% if existing_lessons %}
{% for lesson in existing_lessons %}
- {{ lesson.rule }} => {{ lesson.action }} (Confidence {{ "%.2f"|format(lesson.confidence) }}, Similarity {{ "%.2f"|format(lesson.similarity_score|default(0)) }}, Last Updated {{ lesson.last_seen }})
{% endfor %}
{% else %}
- No history available
{% endif %}

### Fill Statistics
- Total Fills during period: {{ fills_summary.total_fills }}
- Total PnL: {{ "%.2f"|format(fills_summary.total_pnl) }}

Use the current environment features to judge applicability. Lower similarity should reduce confidence or mark as not adopted.

Please output JSON (Only the following structure, no extra text):
{
  "summary": "10~40 words summary",
  "lessons": [
    {
      "rule": "Rule of thumb (<=40 words, including filters or improvements)",
      "action": "Execution/Avoidance action (<=40 words, clear do_nothing/reduce size/filter)",
      "conditions": ["Condition A", "Condition B"],
      "confidence": 0.7,
      "evidence": ["Reference Decision X", "Indicator/Data"],
      "support_count": 1,
      "context_features": {{ context_features_json }}
    }
  ]
}
