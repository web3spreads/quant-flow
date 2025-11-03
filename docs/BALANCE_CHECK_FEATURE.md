# 余额检查与动态金额调整功能

## 🎯 功能概述

为 Quant Flow 交易机器人添加了智能的余额检查和动态交易金额调整功能，确保交易机器人能够根据实际可用资金智能调整交易策略，避免因资金不足导致的交易失败。

## ✅ 实现的功能

### 1. OrderManager 余额管理扩展

**文件**: `src/trading/order_manager.py`

#### 新增方法

1. **get_available_balance_info()** - 获取详细余额信息

```python
def get_available_balance_info(self, currency: str = 'USDT') -> Dict[str, Any]
```

**功能**：
- 获取账户总余额
- 计算已占用资金（多头持仓 + 模拟空头持仓）
- 计算可用余额
- 返回详细的余额信息字典

**返回格式**：
```python
{
    'total': 10000.0,        # 总余额
    'occupied': 800.0,       # 已占用资金
    'available': 9200.0,     # 可用余额
    'status': 'ok',          # 状态
    'message': '可用余额: 9200.00 USDT'
}
```

2. **calculate_suggested_trade_amount()** - 计算建议交易金额

```python
def calculate_suggested_trade_amount(
    self,
    desired_amount: float,
    currency: str = 'USDT',
    min_trade_amount: float = 10.0,
    reserve_ratio: float = 0.1
) -> Dict[str, Any]
```

**功能**：
- 根据可用余额计算建议的交易金额
- 保留一定比例的资金作为缓冲（默认 10%）
- 检查是否满足最小交易金额
- 智能调整交易金额

**决策逻辑**：

| 场景 | 条件 | 建议金额 | 是否可交易 |
|------|------|----------|-----------|
| 余额充足 | 可用资金 ≥ 期望金额 | 使用期望金额 | ✅ 是 |
| 余额不足 | 最小金额 ≤ 可用资金 < 期望金额 | 使用可用资金 | ✅ 是 |
| 余额极低 | 可用资金 < 最小金额 | 0 | ❌ 否 |

**返回格式**：
```python
# 余额充足
{
    'suggested_amount': 100.0,
    'can_trade': True,
    'reason': '余额充足，使用配置金额 100.0 USDT'
}

# 余额不足但可交易
{
    'suggested_amount': 85.5,
    'can_trade': True,
    'reason': '余额不足，调整为可用金额 85.50 USDT'
}

# 余额过低
{
    'suggested_amount': 0.0,
    'can_trade': False,
    'reason': '可用余额 8.50 USDT 低于最小交易金额 10.0 USDT'
}
```

### 2. 启动时余额检查

**文件**: `main.py`

#### 新增方法

**_check_and_display_balance()** - 检查并显示账户余额

在程序初始化完成后自动调用，显示：
- 总余额
- 占用资金
- 可用余额
- 建议交易金额
- 是否可以交易

**示例输出**：
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 账户余额检查
ℹ️  总余额: 10000.00 USDT
ℹ️  占用资金: 800.00 USDT
ℹ️  可用余额: 9200.00 USDT
ℹ️  ✅ 余额充足，使用配置金额 100.0 USDT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### 3. 交易周期余额检查与动态调整

**文件**: `main.py`

#### trading_cycle() 方法增强

在每个交易周期开始时：

1. **检查账户余额**
   ```
   💰 检查账户余额
   ℹ️  可用余额: 9200.00 USDT
   ℹ️  本次交易金额: 100.00 USDT
   ```

2. **动态调整交易金额**
   - 根据可用余额自动调整
   - 更新所有 Agent 的 `trade_amount`
   - 显示调整原因

3. **资金不足时跳过周期**
   ```
   ⚠️ 可用余额 8.50 USDT 低于最小交易金额 10.0 USDT
   ⚠️ 跳过本次交易周期
   ```

## 📊 占用资金计算

### 多头持仓占用
- 实际购买的币种占用相应的 USDT 价值
- 从 BitgetClient 的持仓信息中获取 `usdt_value`

### 空头持仓占用（模拟）
- 模拟空头持仓记录的 `usdt_amount`
- 防止重复开仓导致资金超支

### 计算公式
```
可用余额 = 总余额 - 多头持仓占用 - 空头持仓占用
可用资金 = 可用余额 × (1 - 保留比例)
```

## 🔄 工作流程

### 启动时流程

```
1. 初始化所有组件
   ↓
2. 检查账户余额
   ├─ 获取总余额
   ├─ 计算占用资金
   ├─ 计算可用余额
   └─ 显示余额信息
   ↓
3. 计算建议交易金额
   ├─ 判断是否可交易
   └─ 显示交易建议
   ↓
4. 开始交易循环
```

### 交易周期流程

```
1. 交易周期开始
   ↓
2. 检查账户余额
   ├─ 获取最新可用余额
   └─ 显示可用余额
   ↓
3. 计算建议交易金额
   ├─ 判断是否可交易
   ├─ 如果不可交易 → 跳过本周期
   └─ 如果可交易 → 继续
   ↓
4. 动态调整交易金额
   ├─ 更新所有 Agent 的 trade_amount
   └─ 显示本次交易金额
   ↓
5. 执行市场数据获取
   ↓
6. AI 批量决策
   ↓
7. 处理决策结果
```

## 📝 使用示例

### 场景 1：余额充足

```
💰 检查账户余额
ℹ️  可用余额: 9200.00 USDT
ℹ️  本次交易金额: 100.00 USDT

📊 批量获取市场数据
...
🤖 AI 批量决策分析
...
```

### 场景 2：余额不足但可调整

```
💰 检查账户余额
ℹ️  可用余额: 85.50 USDT
⚠️  余额不足，调整为可用金额 85.50 USDT
ℹ️  本次交易金额: 85.50 USDT

📊 批量获取市场数据
...
🤖 AI 批量决策分析
...
```

### 场景 3：余额过低

```
💰 检查账户余额
ℹ️  可用余额: 8.50 USDT
⚠️  可用余额 8.50 USDT 低于最小交易金额 10.0 USDT
⚠️  跳过本次交易周期

✅ 交易周期完成 - 等待下一个周期
```

## 🔧 配置参数

### 可调整参数

1. **min_trade_amount** - 最小交易金额
   - 默认：10.0 USDT
   - 说明：低于此金额将无法交易

2. **reserve_ratio** - 保留比例
   - 默认：0.1 (10%)
   - 说明：保留一部分资金作为缓冲

### 配置示例

```python
# 在 calculate_suggested_trade_amount 调用时设置
suggestion = manager.calculate_suggested_trade_amount(
    desired_amount=100.0,
    min_trade_amount=20.0,    # 最小交易金额改为 20 USDT
    reserve_ratio=0.15         # 保留 15% 的资金
)
```

## 📊 测试结果

### 测试脚本：`test_balance_check.py`

#### 测试场景 1：余额充足
```
总余额: 10000.00 USDT
占用资金: 0.00 USDT
可用余额: 10000.00 USDT
建议金额: 100.00 USDT
可以交易: True
```

#### 测试场景 2：空头持仓占用资金
```
总余额: 10000.00 USDT
占用资金: 15.00 USDT
可用余额: 9985.00 USDT
可以交易: True
```

## ⚠️ 注意事项

1. **测试模式**：
   - 测试模式下余额是固定值（10000 USDT）
   - 真实环境会动态获取实际余额

2. **持仓估值**：
   - 多头持仓需要 `usdt_value` 字段
   - 如果交易所 API 未提供，可能需要自行计算

3. **保留比例**：
   - 建议保留 10-20% 的资金
   - 防止市场波动导致资金不足

4. **最小交易金额**：
   - 应根据交易所要求设置
   - Bitget 通常要求最小 10-20 USDT

## 💡 优化建议

### 1. 持仓价值计算优化

如果交易所 API 不提供 `usdt_value`，可以自行计算：

```python
# 在 get_available_balance_info() 中
for pos in positions:
    symbol = pos['symbol']
    amount = pos['amount']
    # 获取当前市价
    current_price = self.client.get_ticker(symbol)['last']
    usdt_value = amount * current_price
    occupied += usdt_value
```

### 2. 动态保留比例

根据市场波动性调整保留比例：

```python
def calculate_dynamic_reserve_ratio(volatility: float) -> float:
    """
    根据市场波动性计算动态保留比例

    Args:
        volatility: 市场波动率 (0-1)

    Returns:
        保留比例 (0.1-0.3)
    """
    # 波动率越高，保留比例越大
    return 0.1 + volatility * 0.2
```

### 3. 分级交易金额

根据可用余额设置不同的交易金额级别：

```python
def calculate_tiered_trade_amount(available: float) -> float:
    """分级交易金额"""
    if available >= 1000:
        return 100  # 大资金使用 100 USDT
    elif available >= 500:
        return 50   # 中等资金使用 50 USDT
    elif available >= 100:
        return 20   # 小资金使用 20 USDT
    else:
        return 10   # 最小金额
```

## 📝 修改的文件列表

1. ✅ `src/trading/order_manager.py` - 添加余额检查和金额计算方法
2. ✅ `main.py` - 添加启动时和交易周期的余额检查
3. ✅ `test_balance_check.py` - 创建余额检查测试脚本

## ✨ 总结

本次更新成功为 Quant Flow 添加了智能的资金管理功能，实现了：

✅ **余额实时监控**：程序启动和每次交易前都检查余额
✅ **动态金额调整**：根据可用资金自动调整交易金额
✅ **风险控制**：保留一定比例的资金缓冲
✅ **智能跳过**：资金不足时自动跳过交易周期
✅ **透明提示**：清晰显示余额和调整原因

通过这次升级，交易机器人能够更智能地管理资金，避免因余额不足导致的交易失败，同时最大化资金利用效率！
