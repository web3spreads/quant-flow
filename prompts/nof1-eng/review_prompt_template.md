You will see the latest trading records, statistics, and old rules of thumb for {{ symbol }}. Please output a JSON result based on these inputs to help reuse experience or correct errors in future trades.

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

Please output JSON:
{
  "summary": "10~40 words summary",
  "lessons": [
    {
      "rule": "Rule of thumb (<=40 words)",
      "action": "Suggested action or avoidance (<=40 words)",
      "conditions": ["Condition A", "Condition B"],
      "confidence": 0.7,
      "evidence": ["Reference Decision X", "Indicator/Data"]
    }
  ],
  "spot_checks": [
    {"timestamp": "Optional, point out specific anomaly", "issue": "Issue", "fix": "Suggested Fix"}
  ]
}
