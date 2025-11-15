## 现货定投机会评估 | {{ symbol }}

**评估时间:** {{ current_time }}
**评估轮次:** 第 {{ iteration }} 次

---

## 📋 6重验证清单

> 必须**全部通过**才能执行定投,缺一不可!

---

### ✅ 验证 1/6: 币种资格检查

**问题:** {{ symbol }} 是 BTC 或 ETH 吗?

```
{{ symbol }} = {{ 'BTC' if is_btc else 'ETH' if is_eth else '其他币种' }}

结果: {{ '✅ 通过' if is_major_coin else '❌ 失败 - 仅支持BTC/ETH' }}
```

{% if not is_major_coin %}
**立即终止评估**

{{ symbol }} 不是BTC或ETH,不符合定投标准。

**输出决策:**
```json
{
  "symbol": "{{ symbol }}",
  "action": "do_nothing",
  "justification": "{{ symbol }}不是主流币种,仅支持BTC和ETH的现货定投。山寨币风险过高,不适合长期价值投资。",
  "confidence": 0.0
}
```
{% else %}

---

### ✅ 验证 2/6: 多周期一致深度下跌

**要求:** 日线、4小时、1小时**全部**显示下跌趋势

**多周期分析:**
```
{{ multi_timeframe_analysis }}
```

**详细检查:**

#### 日线趋势
```
价格 vs MA(25): ${{ current_price }} vs ${{ daily_ma25 }}
MACD: {{ daily_macd }}
RSI: {{ daily_rsi }}
判断: {{ daily_trend }}
```

#### 4小时趋势
```
价格 vs MA(25): ${{ current_price }} vs ${{ h4_ma25 }}
MACD: {{ h4_macd }}
RSI: {{ h4_rsi }}
判断: {{ h4_trend }}
```

#### 1小时趋势
```
价格 vs MA(25): ${{ current_price }} vs ${{ h1_ma25 }}
MACD: {{ h1_macd }}
RSI: {{ h1_rsi }}
判断: {{ h1_trend }}
```

**一致性统计:**
- 下跌周期数: {{ downtrend_count }}/3
- 结果: {{ '✅ 通过 - 三周期一致下跌' if downtrend_count == 3 else '❌ 失败 - 周期不一致' }}

---

### ✅ 验证 3/6: 深度超卖检查

**当前RSI指标:**
```
RSI(7): {{ rsi_7 }}
RSI(14): {{ rsi_14 }}
4H RSI(14): {{ rsi_14_4h }}
日线RSI(14): {{ rsi_14_daily }}
```

**超卖标准:**
{% if is_btc %}
```
BTC超卖档位:
- 极度恐慌: RSI < 20 ⭐⭐ (最佳)
- 深度超卖: RSI < 25 ⭐ (推荐)
- 一般超卖: RSI < 30 (合格)

当前RSI(14): {{ rsi_14 }}
档位: {{
  '极度恐慌 ⭐⭐' if rsi_14 < 20 else
  '深度超卖 ⭐' if rsi_14 < 25 else
  '一般超卖' if rsi_14 < 30 else
  '未达标'
}}

结果: {{ '✅ 通过' if rsi_14 < 30 else '❌ 失败 - RSI未达标' }}
```
{% elif is_eth %}
```
ETH超卖档位 (更严格):
- 极度恐慌: RSI < 18 ⭐⭐ (最佳)
- 深度超卖: RSI < 23 ⭐ (推荐)
- 一般超卖: RSI < 28 (合格)

当前RSI(14): {{ rsi_14 }}
档位: {{
  '极度恐慌 ⭐⭐' if rsi_14 < 18 else
  '深度超卖 ⭐' if rsi_14 < 23 else
  '一般超卖' if rsi_14 < 28 else
  '未达标'
}}

结果: {{ '✅ 通过' if rsi_14 < 28 else '❌ 失败 - RSI未达标' }}
```
{% endif %}

---

### ✅ 验证 4/6: 空头均线排列

**要求:** 价格 < MA(7) < MA(25) < MA(99)

**当前均线系统:**
```
当前价格: ${{ current_price }}
MA(7):    ${{ ma_7 }}
MA(25):   ${{ ma_25 }}
MA(99):   ${{ ma_99 }}

检查:
{{ current_price }} < {{ ma_7 }}? {{ '✓' if current_price < ma_7 else '✗' }}
{{ ma_7 }} < {{ ma_25 }}? {{ '✓' if ma_7 < ma_25 else '✗' }}
{{ ma_25 }} < {{ ma_99 }}? {{ '✓' if ma_25 < ma_99 else '✗' }}

结果: {{ '✅ 通过 - 完整空头排列' if (current_price < ma_7 and ma_7 < ma_25 and ma_25 < ma_99) else '❌ 失败 - 均线排列不符' }}
```

**价格偏离度:**
```
价格 vs MA(7): {{ ((current_price - ma_7) / ma_7 * 100) | round(2) }}%
价格 vs MA(25): {{ ((current_price - ma_25) / ma_25 * 100) | round(2) }}%
价格 vs MA(99): {{ ((current_price - ma_99) / ma_99 * 100) | round(2) }}%
```

---

### ✅ 验证 5/6: 布林带极限位置

**当前布林带数据:**
```
布林带上轨: ${{ bb_upper }}
布林带中轨: ${{ bb_middle }}
布林带下轨: ${{ bb_lower }}
当前价格: ${{ current_price }}

布林带位置 (BB Position): {{ bb_position }}
(0.0 = 下轨, 0.5 = 中轨, 1.0 = 上轨)

标准: BB Position < 0.2
结果: {{ '✅ 通过 - 极限低位' if bb_position < 0.2 else '❌ 失败 - 位置不够极端' }}
```

**价格与轨道关系:**
```
距离下轨: {{ ((current_price - bb_lower) / bb_lower * 100) | round(2) }}%
距离中轨: {{ ((current_price - bb_middle) / bb_middle * 100) | round(2) }}%
```

---

### ✅ 验证 6/6: MACD底部确认

**MACD指标分析:**

**日内MACD (最旧→最新):**
```
{{ macd_series }}
当前MACD: {{ current_macd }} (最后一个元素)
```

**4小时MACD (最旧→最新):**
```
{{ macd_4h_series }}
当前4H MACD: {{ current_macd_4h }} (最后一个元素)
```

**检查标准:**
```
当前MACD < 0? {{ '✓' if current_macd < 0 else '✗' }}
MACD持续负值周期: {{ macd_negative_periods }}
负值持续 ≥ 3周期? {{ '✓' if macd_negative_periods >= 3 else '✗' }}

结果: {{ '✅ 通过 - MACD底部' if (current_macd < 0 and macd_negative_periods >= 3) else '❌ 失败 - MACD未达底部' }}
```

---

## 📊 验证总结

**6重验证结果:**
```
1. 币种资格: {{ '✅' if is_major_coin else '❌' }}
2. 多周期下跌: {{ '✅' if multi_timeframe_pass else '❌' }}
3. 深度超卖: {{ '✅' if oversold_pass else '❌' }}
4. 空头排列: {{ '✅' if ma_alignment_pass else '❌' }}
5. 布林带极限: {{ '✅' if bb_extreme_pass else '❌' }}
6. MACD底部: {{ '✅' if macd_bottom_pass else '❌' }}

总通过数: {{ pass_count }}/6
```

---

{% if pass_count == 6 %}
## 💰 定投金额计算

**全部验证通过! 计算定投金额...**

**账户信息:**
```
可用现金: ${{ available_cash }}
定投上限: ${{ dca_max_amount }}
```

**基于RSI档位的金额决策:**

{% if is_btc %}
```python
# BTC定投金额表
RSI当前值: {{ rsi_14 }}

if {{ rsi_14 }} < 20:  # 极度恐慌
    建议比例: 80-100%
    建议金额: ${{ dca_max_amount * 0.9 | round(2) }}
elif {{ rsi_14 }} < 25:  # 深度超卖
    建议比例: 60-80%
    建议金额: ${{ dca_max_amount * 0.7 | round(2) }}
elif {{ rsi_14 }} < 30:  # 一般超卖
    建议比例: 40-60%
    建议金额: ${{ dca_max_amount * 0.5 | round(2) }}
```
{% elif is_eth %}
```python
# ETH定投金额表 (更保守)
RSI当前值: {{ rsi_14 }}

if {{ rsi_14 }} < 18:  # 极度恐慌
    建议比例: 70-100%
    建议金额: ${{ dca_max_amount * 0.85 | round(2) }}
elif {{ rsi_14 }} < 23:  # 深度超卖
    建议比例: 50-70%
    建议金额: ${{ dca_max_amount * 0.6 | round(2) }}
elif {{ rsi_14 }} < 28:  # 一般超卖
    建议比例: 30-50%
    建议金额: ${{ dca_max_amount * 0.4 | round(2) }}
```
{% endif %}

**最终决策金额:** ${{ recommended_amount }}

---

## 🎯 输出决策

请按以下JSON格式输出定投决策:

```json
{
  "symbol": "{{ symbol }}",
  "action": "buy_spot",
  "amount": {{ recommended_amount }},
  "justification": "
    【6重验证全部通过】
    1. 币种: {{ symbol }} ✓
    2. 多周期: 日线/4H/1H 全部下跌 ✓
    3. 超卖: RSI={{ rsi_14 }} < {{ 30 if is_btc else 28 }} ({{ oversold_tier }}) ✓
    4. 均线: 完整空头排列 ✓
    5. 布林带: 位置={{ bb_position }} < 0.2 ✓
    6. MACD: 负值{{ macd_negative_periods }}周期 ✓

    【定投理由】
    {{ symbol }}处于{{ oversold_tier }},RSI={{ rsi_14 }}。
    三个时间周期一致性确认市场底部区域。
    价格${{ current_price }}远低于所有主要均线。
    布林带位置{{ bb_position }}显示极端低位。

    【金额选择】
    RSI={{ rsi_14}} 属于{{ oversold_tier }}档,使用{{ (recommended_amount / dca_max_amount * 100) | round }}%上限 = ${{ recommended_amount }}

    【长期视角】
    这是{{ symbol }}长期价值积累的优质机会,符合逆向投资原则。
  ",
  "confidence": {{ confidence }},
  "checklist_result": {
    "is_major_coin": true,
    "multi_timeframe_downtrend": true,
    "deep_oversold": true,
    "bearish_ma_alignment": true,
    "bollinger_extreme": true,
    "macd_bottom": true
  },
  "rsi_level": {{ rsi_14 }},
  "dca_tier": "{{ oversold_tier }}"
}
```

{% else %}
## ❌ 验证未通过

**缺失条件:**
{{ missing_conditions }}

**不符合定投标准,选择观望。**

---

## 🎯 输出决策

```json
{
  "symbol": "{{ symbol }}",
  "action": "do_nothing",
  "justification": "
    【验证失败 {{ pass_count }}/6】
    {% for i in range(1, 7) %}
    {{ i }}. {{ validation_names[i-1] }}: {{ validation_results[i-1] }}
    {% endfor %}

    【结论】
    未满足6重验证的全部条件,不符合定投标准。
    {{ failure_summary }}

    【建议】
    继续观望,等待以下条件改善:
    {{ missing_conditions }}

    【纪律】
    宁可错过机会,不做勉强定投。
    保持耐心,等待更极端的市场恐慌。
  ",
  "confidence": 0.0,
  "checklist_result": {
    "is_major_coin": {{ is_major_coin | lower }},
    "multi_timeframe_downtrend": {{ multi_timeframe_pass | lower }},
    "deep_oversold": {{ oversold_pass | lower }},
    "bearish_ma_alignment": {{ ma_alignment_pass | lower }},
    "bollinger_extreme": {{ bb_extreme_pass | lower }},
    "macd_bottom": {{ macd_bottom_pass | lower }}
  },
  "missing_conditions": {{ missing_conditions | tojson }}
}
```

{% endif %}

---

## 💭 最后提醒

- **定投是战略,不是战术**
- **极度选择性,不要降低标准**
- **长期主义,关注价值而非价格**
- **纪律至上,严格执行6重验证**

现在,请基于以上分析,做出你的现货定投决策!

{% endif %}
