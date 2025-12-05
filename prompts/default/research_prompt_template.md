# 市场信息研究任务

## 研究时间范围

**当前时间**: {{ current_time }}
**研究周期**: {{ interval_description }}（{{ interval_hours }} 小时）
**开始时间**: {{ start_time }}

## 研究主题

请针对以下加密货币进行市场信息研究：
{% if symbols %}
**目标币种**: {{ symbols | join(', ') }}
{% else %}
**目标范围**: 加密货币市场整体（重点关注 BTC、ETH 及主流山寨币）
{% endif %}

## 搜索结果

以下是从网络搜索获取的相关信息：

{{ search_results }}

## 任务要求

请基于上述搜索结果，生成一份结构化的市场信息报告。报告需要包含：

### 1. 市场概况
- 总结该时间段内加密货币市场的整体表现
- 主要趋势和价格变动

### 2. 重要事件
列出该时间段内的重要市场事件，每个事件包括：
- 事件描述
- 发生时间
- 相关币种
- 市场影响评估（使用数值 1-5）
- 信息来源

### 3. 监管动态
- 各国监管政策变化
- 法律法规更新

### 4. 行业动态
- 重大项目进展
- 技术升级和网络事件
- 融资和合作信息

### 5. 市场情绪
- 整体市场情绪评估（极度恐惧/恐惧/中性/贪婪/极度贪婪）
- 资金流向分析
- 机构动向

### 6. 风险提示
- 需要关注的潜在风险
- 可能影响市场的未来事件

### 7. 总结
- 对交易决策的关键参考点
- 建议关注的重点

## 输出格式

**重要说明**：所有 `impact` 和 `severity` 字段请使用数值（1-5）表示：
- **1** = 极低影响/风险
- **2** = 低影响/风险
- **3** = 中等影响/风险
- **4** = 高影响/风险
- **5** = 极高影响/严重风险

请以 JSON 格式输出，结构如下：

```json
{
  "interval_hours": {{ interval_hours }},
  "generated_at": "{{ current_time }}",
  "market_overview": {
    "summary": "市场概况总结",
    "trend": "上涨/下跌/震荡",
    "sentiment": "市场情绪"
  },
  "key_events": [
    {
      "title": "事件标题",
      "description": "事件描述",
      "time": "发生时间",
      "coins": ["相关币种"],
      "impact": 1-5,
      "source": "信息来源"
    }
  ],
  "regulatory_updates": [
    {
      "region": "地区",
      "content": "监管内容",
      "impact": "影响评估"
    }
  ],
  "industry_news": [
    {
      "category": "分类（技术/融资/合作/其他）",
      "content": "新闻内容",
      "coins": ["相关币种"]
    }
  ],
  "market_sentiment": {
    "overall": "整体情绪",
    "fear_greed_index": "恐惧贪婪指数估计（0-100）",
    "fund_flow": "资金流向描述",
    "whale_activity": "鲸鱼活动"
  },
  "risk_alerts": [
    {
      "type": "风险类型",
      "description": "风险描述",
      "severity": 1-5
    }
  ],
  "trading_implications": {
    "bullish_factors": ["利多因素"],
    "bearish_factors": ["利空因素"],
    "key_levels": "关键价格水平",
    "suggestions": "交易建议参考"
  }
}
```

请确保输出是有效的 JSON 格式。
