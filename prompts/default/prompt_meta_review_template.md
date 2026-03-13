## Prompt 效果评估报告

### 综合评分: {{ "%.1f"|format(effectiveness_report.overall_score * 100) }}/100

### FinCoT 6步完成度
- 综合评分: {{ "%.1f"|format(effectiveness_report.fincot_completion.score * 100) }}%
{% if effectiveness_report.fincot_completion.step_rates %}
{% for step, rate in effectiveness_report.fincot_completion.step_rates.items() %}
- {{ step }}: {{ "%.0f"|format(rate * 100) }}%
{% endfor %}
{% endif %}

### 复盘经验引用率
- 引用率: {{ "%.1f"|format(effectiveness_report.lesson_citation_rate.score * 100) }}%
- 引用次数: {{ effectiveness_report.lesson_citation_rate.cited_count }} / {{ effectiveness_report.lesson_citation_rate.total_with_reason }}

### 决策一致性
- 一致性评分: {{ "%.1f"|format(effectiveness_report.decision_consistency.score * 100) }}%

### 置信度校准
- 校准评分: {{ "%.1f"|format(effectiveness_report.confidence_calibration.score * 100) }}%
- 高置信胜率: {{ "%.1f"|format(effectiveness_report.confidence_calibration.high_confidence_win_rate * 100) }}%
- 低置信胜率: {{ "%.1f"|format(effectiveness_report.confidence_calibration.low_confidence_win_rate * 100) }}%

{% if historical_trend %}
### 历史趋势
{% for entry in historical_trend %}
- {{ entry.week }}: 综合 {{ "%.0f"|format(entry.overall_score * 100) }}%, FinCoT {{ "%.0f"|format(entry.fincot * 100) }}%, 引用 {{ "%.0f"|format(entry.citation * 100) }}%
{% endfor %}
{% endif %}

请基于以上评估结果，生成具体的 Prompt 微调建议。每条建议需指明目标步骤、问题和具体修改方案。
