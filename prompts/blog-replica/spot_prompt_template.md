## 现货定投评估: {{ symbol }}

**任务:** 评估 {{ symbol }} 是否适合现货定投

---

### 市场数据分析

**当前指标:**
- 当前价格: ${{ current_price }}
- RSI (7周期): {{ rsi_7 }}
- RSI (14周期): {{ rsi_14 }}
- MACD: {{ macd }}
- 布林带位置: {{ bb_position }} (0=下轨, 1=上轨)

**多周期趋势:**
{{ multi_timeframe_trends }}

**均线系统:**
- MA(7): ${{ ma_7 }}
- MA(25): ${{ ma_25 }}
- MA(99): ${{ ma_99 }}

**账户信息:**
- 可用现金: ${{ available_cash }}
- 定投上限: ${{ dca_max_amount }}

---

### 定投条件检查清单

请逐一检查以下条件:

#### ✅ 币种资格
- [ ] 是否为BTC或ETH? (其他币种直接拒绝)

#### ✅ 多周期趋势
- [ ] 日线是否下跌?
- [ ] 4小时是否下跌?
- [ ] 1小时是否下跌?
- [ ] 是否三个周期一致深度下跌?

#### ✅ 超卖程度
- [ ] RSI < 30? (BTC可放宽到< 35)
- [ ] 是否达到深度超卖?

#### ✅ 均线排列
- [ ] 价格 < MA(7)?
- [ ] MA(7) < MA(25)?
- [ ] MA(25) < MA(99)?
- [ ] 是否形成空头排列?

#### ✅ 布林带位置
- [ ] BB位置 < 0.2?
- [ ] 是否处于极限位置?

#### ✅ MACD状态
- [ ] MACD柱状图为负值?
- [ ] 是否持续多个周期?

---

### 决策要求

如果所有条件都满足:
- 计算合理的定投金额 (基于RSI程度)
- 提供详细的理由
- 设置信心分数

如果条件不满足:
- 明确说明缺失的条件
- 选择 do_nothing
- 解释为什么不适合定投

**输出格式:**
```json
{
  "symbol": "{{ symbol }}",
  "action": "buy_spot" 或 "do_nothing",
  "amount": 定投金额,
  "justification": "详细理由",
  "confidence": 0-1之间的信心分数,
  "checklist_result": {
    "is_major_coin": true/false,
    "multi_timeframe_downtrend": true/false,
    "deep_oversold": true/false,
    "bearish_ma_alignment": true/false,
    "bollinger_extreme": true/false,
    "macd_bottom": true/false
  }
}
```

---

记住: 现货定投是长期投资,只在极端市场恐慌、所有条件都满足时才执行。宁可错过,不要勉强!
