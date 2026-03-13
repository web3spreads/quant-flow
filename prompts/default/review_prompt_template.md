你将看到关于 {{ symbol }} 的最新交易记录、统计信息以及旧的经验规则。请基于这些输入输出 JSON 结果，以帮助后续交易复用经验或修正错误。

### 最近决策 ({{ decision_digest|length }} 条)
{% for item in decision_digest %}
- {{ item.timestamp }} ｜ {{ item.decision }} ｜ 价格 ${{ "%.2f"|format(item.price) }} ｜ 结果 {{ item.result }} ｜ 理由 {{ item.reason }}
{% endfor %}

### 决策统计
- 总次数: {{ stats.total_decisions }}
- 买入: {{ stats.buy_count }}, 卖出: {{ stats.sell_count }}, 做空: {{ stats.sell_short_count }}, 平空: {{ stats.buy_to_cover_count }}, 观望: {{ stats.idle_count }}
- 平均价: ${{ "%.2f"|format(stats.average_price) }}, 最高价: ${{ "%.2f"|format(stats.max_price) }}, 最低价: ${{ "%.2f"|format(stats.min_price) }}

### 当前市场环境特征
{% set cf = context_features or {} %}
- RSI: {{ cf.get("rsi", "未知") }}
- MACD状态: {{ cf.get("macd_signal", "未知") }} ｜ EMA关系: {{ cf.get("ema_trend", "未知") }}
- 趋势: {{ cf.get("trend_direction", "未知") }} ｜ 波动率: {{ cf.get("volatility_level", "未知") }}
- 成交量相对均值: {{ "%.2f"|format(cf.get("volume_ratio", 1.0)) }}
- 价格区间位置(0-1): {{ "%.2f"|format(cf.get("price_position", 0.5)) }}
- 时间段: {{ cf.get("time_of_day", "未知") }}

### 现有经验 (若为空表示暂无)
{% if existing_lessons %}
{% for lesson in existing_lessons %}
- {{ lesson.rule }} => {{ lesson.action }} (置信度 {{ "%.2f"|format(lesson.confidence) }}, 相似度 {{ "%.2f"|format(lesson.similarity_score|default(0)) }}, 最近更新 {{ lesson.last_seen }})
{% endfor %}
{% else %}
- 暂无历史经验
{% endif %}

### 成交统计
- 期间成交次数: {{ fills_summary.total_fills }}
- 总盈亏: {{ "%.2f"|format(fills_summary.total_pnl) }}

请基于当前环境特征判断经验是否适用，相似度低则降低置信度或标注为不采纳。

{% if fact_subjective_enabled is defined and fact_subjective_enabled %}
### 事实-主观信号分析要求
请在分析中明确区分：
- **事实信号**：基于技术指标（RSI、MACD、EMA、ATR、成交量、布林带、支撑位、阻力位等）的客观判断
- **主观信号**：基于情绪、新闻、恐惧贪婪指数、资金费率、市场氛围等的主观判断
并在每条经验的 `source_type` 字段中标注。
{% endif %}

请输出 JSON：
{
  "summary": "10~40字总述",
  "lessons": [
    {
      "rule": "经验规则（<=40字）",
      "action": "建议执行或规避动作（<=40字）",
      "conditions": ["触发条件A", "触发条件B"],
      "confidence": 0.7,
      "evidence": ["引用第X条决策", "指标/数据"],
      "context_features": {{ context_features_json }},
      "lesson_type": "positive 或 negative",
      "source_type": "factual 或 subjective 或 mixed"
    }
  ],
  "spot_checks": [
    {"timestamp": "可选，指出具体异常", "issue": "问题", "fix": "建议修复"}
  ]
}
