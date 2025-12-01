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
- Buy: {{ stats.buy_count }}, Sell: {{ stats.sell_count }}, Sell Short: {{ stats.sell_short_count }}, Buy to Cover: {{ stats.buy_to_cover_count }}, Hold: {{ stats.idle_count }}
- Average Price: ${{ "%.2f"|format(stats.average_price) }}, Max Price: ${{ "%.2f"|format(stats.max_price) }}, Min Price: ${{ "%.2f"|format(stats.min_price) }}

### Existing Lessons (if empty, means none)
{% if existing_lessons %}
{% for lesson in existing_lessons %}
- {{ lesson.rule }} => {{ lesson.action }} (Confidence {{ "%.2f"|format(lesson.confidence) }}, Last Updated {{ lesson.last_seen }})
{% endfor %}
{% else %}
- No history available
{% endif %}

### Fill Statistics
- Total Fills during period: {{ fills_summary.total_fills }}
- Total PnL: {{ "%.2f"|format(fills_summary.total_pnl) }}

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
      "support_count": 1
    }
  ],
  "spot_checks": [
    {"timestamp": "Optional, point out specific anomaly", "issue": "Issue", "fix": "Suggested Fix or Filter Threshold"}
  ]
}
