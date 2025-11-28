你将看到关于 {{ symbol }} 的最新交易记录、统计信息以及旧的经验规则。请基于这些输入输出 JSON 结果，提炼可参考的经验规则，帮助后续交易做出更好的决策。

**关注点（参考，非硬性要求）:**
- 低波动/无量情况下的交易表现
- 盈亏比和手续费成本的影响
- 信号质量与仓位/杠杆的匹配度
- 成功与失败交易的共性模式
- 提炼成功时的趋势/量能/波动/价位结构/仓位与杠杆特征
- 识别失败时的常见问题（噪声区、量能不足、盈亏比不足或过大仓位等）

### 最近决策 ({{ decision_digest|length }} 条)
{% for item in decision_digest %}
- {{ item.timestamp }} ｜ {{ item.decision }} ｜ 价格 ${{ "%.2f"|format(item.price) }} ｜ 结果 {{ item.result }} ｜ 理由 {{ item.reason }}
{% endfor %}

### 决策统计
- 总次数: {{ stats.total_decisions }}
- 买入: {{ stats.buy_count }}, 卖出: {{ stats.sell_count }}, 做空: {{ stats.sell_short_count }}, 平空: {{ stats.buy_to_cover_count }}, 观望: {{ stats.idle_count }}
- 平均价: ${{ "%.2f"|format(stats.average_price) }}, 最高价: ${{ "%.2f"|format(stats.max_price) }}, 最低价: ${{ "%.2f"|format(stats.min_price) }}

### 现有经验 (若为空表示暂无)
{% if existing_lessons %}
{% for lesson in existing_lessons %}
- {{ lesson.rule }} => {{ lesson.action }} (置信度 {{ "%.2f"|format(lesson.confidence) }}, 最近更新 {{ lesson.last_seen }})
{% endfor %}
{% else %}
- 暂无历史经验
{% endif %}

### 成交统计
- 期间成交次数: {{ fills_summary.total_fills }}
- 总盈亏: {{ "%.2f"|format(fills_summary.total_pnl) }}

请输出 JSON（仅以下结构，不要额外文本）：
{
  "summary": "10~40字总述",
  "lessons": [
    {
      "rule": "经验规则（<=40字，包含过滤或改进点）",
      "action": "执行/规避动作（<=40字，明确 do_nothing/减仓/过滤）",
      "conditions": ["触发条件A", "触发条件B"],
      "confidence": 0.7,
      "evidence": ["引用第X条决策", "指标/数据"],
      "support_count": 1
    }
  ],
  "spot_checks": [
    {"timestamp": "可选，指出具体异常", "issue": "问题", "fix": "建议修复或过滤阈值"}
  ]
}
