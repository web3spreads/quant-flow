## 本周交易统计

- 总决策记录: {{ weekly_stats.total_records }} 条

### 各交易对表现
{% for symbol, stats in weekly_stats.per_symbol.items() %}
- **{{ symbol }}**: {{ stats.total }} 条决策, 盈亏 ${{ "%.2f"|format(stats.pnl) }}
  - 决策分布: {% for d, c in stats.decisions.items() %}{{ d }}({{ c }}) {% endfor %}
{% endfor %}

## 系统性偏差检测

{% if systematic_biases %}
{% for bias in systematic_biases %}
- **{{ bias.type }}** [{{ bias.severity }}]: {{ bias.description }}
{% endfor %}
{% else %}
- 未检测到明显系统性偏差
{% endif %}

## 反复错误检测

{% if recurring_errors %}
{% for error in recurring_errors %}
- **{{ error.condition }}**: 发生 {{ error.count }} 次, 累计亏损 ${{ "%.2f"|format(error.total_loss) }}
  - 建议: {{ error.suggestion }}
{% endfor %}
{% else %}
- 未检测到反复错误
{% endif %}

## 各交易对经验摘要

{% for symbol, lessons in all_symbols_summary.items() %}
### {{ symbol }}
{% if lessons %}
{% for lesson in lessons %}
- {{ lesson.rule }} => {{ lesson.action }} (置信度 {{ "%.2f"|format(lesson.confidence) }})
{% endfor %}
{% else %}
- 暂无经验
{% endif %}
{% endfor %}

请基于以上数据，输出策略级分析和调整建议。
