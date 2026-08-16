## {{ symbol }} 交易决策

### 市场快照（{{ timeframe }} K 线）

```
价格: {{ "%.4f"|format(current_price) }}   开: {{ "%.4f"|format(open) }}   高: {{ "%.4f"|format(high) }}   低: {{ "%.4f"|format(low) }}
RSI(14): {{ "%.1f"|format(rsi) }}   MACD: {{ "%.5f"|format(macd) }}   信号线: {{ "%.5f"|format(macd_signal) }}   柱: {{ "%.5f"|format(macd_hist) }}
布林带: 上 {{ "%.4f"|format(bb_upper) }} / 中 {{ "%.4f"|format(bb_middle) }} / 下 {{ "%.4f"|format(bb_lower) }}   位置: {{ "%.0f"|format(bb_position * 100) }}%
均线: {{ ma_text }}
成交量: {{ "%.2f"|format(volume) }}（较均量 {{ "%.1f"|format(volume_change) }}%）
```

### 多周期趋势

```
{{ trends_text }}
```

### 账户状态

```
可用余额: ${{ "%.2f"|format(available_balance) }}   持仓数: {{ position_count }}/{{ max_positions }}
本次投入上限: ${{ "%.2f"|format(max_trade_amount) }}   杠杆上限: {{ max_leverage }}x
止盈/止损: 系统自动按开仓价 ±{{ "%.1f"|format(take_profit_ratio * 100) }}% / ∓{{ "%.1f"|format(stop_loss_ratio * 100) }}% 挂单
```

### 当前持仓

{{ position_text }}

{% if not open_allowed %}
> ⚠️ 本轮禁止开新仓（余额不足或风控暂停），只允许 CLOSE 或 HOLD。
{% endif %}

---

现在按系统提示的推理步骤分析，输出严格 JSON 决策。
