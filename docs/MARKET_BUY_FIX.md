# 市价买入修复说明

## ✅ 已修复的问题

### 1. 数量精度问题

**问题**:
```
{"code":"40808","msg":"参数校验异常 delegateAmount checkBDScale error value=0.0003333333333333333 checkScale=8"}
```

**原因**: 买入数量的小数位数超过 Bitget API 允许的 8 位精度

**修复**: 在所有数量计算和字符串转换处添加 `round(amount, 8)` 控制精度

**修改文件**:
- `src/trading/order_manager.py` - `calculate_amount_from_usdt()`
- `src/trading/bitget_client.py` - `create_market_buy_order()`, `create_market_sell_order()`, `create_order_with_tpsl()`

### 2. 市价买入参数问题

**问题**:
```
{"code":"45110","msg":"触发最小下单价值限制 1 USDT"}
```

**原因**: Bitget 现货市价单要求：
- **买入时**: `size` 参数应该是要花费的 **USDT 金额**
- **卖出时**: `size` 参数应该是要卖出的 **币数量**

但代码在买入时传入的是币数量（0.000333），导致 API 误以为只想花 0.000333 USDT

**修复**: 修改调用链，买入时传入 USDT 金额

**修改文件**:
- `src/trading/bitget_official_client.py` - `place_order_with_tpsl()` 添加 `usdt_amount` 参数
- `src/trading/bitget_client.py` - `create_order_with_tpsl()` 添加 `usdt_amount` 参数
- `src/trading/order_manager.py` - `execute_buy_with_protection()` 传递 `usdt_amount` 参数

## ✅ 测试结果

### 市价买入测试

```bash
uv run python test_market_buy_fix.py
```

**测试参数**:
- 交易对: BTC/USDT
- 买入金额: 20.0 USDT
- 当前价格: 60000.0 USD
- 计算数量: 0.00033333 BTC (8位小数 ✅)

**测试结果**:
```
response: {"code":"00000","msg":"success","requestTime":1762003808738,
"data":{"orderId":"1368508651027791873","clientOid":"68d265ad-daa5-48ce-bab3-1d359c6d3602"}}

✅ 市价单已创建: 1368508651027791873
✅ 买入测试成功！
```

## ⚠️ 待解决的问题

### 止盈止损单创建失败

**问题**:
```
{"code":"40020","msg":"参数 planType: normal_plan 异常"}
```

**原因**: Bitget 计划单 API 的 `planType` 参数值可能不正确

**当前状态**:
- 市价买入成功 ✅
- 止盈止损单失败 ❌

**影响**:
- 主要交易功能（买入）正常工作
- 止盈止损需要手动设置或使用其他方式

**临时解决方案**:
1. 手动在 Bitget 网站设置止盈止损
2. 或在程序中监控价格并触发平仓

**后续工作**:
- 需要查阅 Bitget API 官方文档确认正确的计划单参数
- 可能需要使用不同的 API 端点创建止盈止损单

## 📊 API 调用流程

### 买入流程

```
1. execute_buy_with_protection(usdt_amount=20, price=60000)
   ↓
2. calculate_amount_from_usdt() → amount=0.00033333 (8位小数)
   ↓
3. create_order_with_tpsl(side='buy', amount=0.00033333, usdt_amount=20)
   ↓
4. place_order_with_tpsl(side='buy', amount='0.00033333', usdt_amount='20.0')
   ↓
5. place_market_order(side='buy', size='20.0')  ← 使用 USDT 金额 ✅
   ↓
6. Bitget API: {"symbol": "BTCUSDT", "side": "buy", "size": "20.0"}
   ↓
7. 成功: orderId=1368508651027791873
```

### 卖出流程

```
1. execute_sell_order(symbol, amount=0.00033333)
   ↓
2. create_market_sell_order(symbol, amount=0.00033333)
   ↓
3. place_market_order(side='sell', size='0.00033333')  ← 使用币数量 ✅
   ↓
4. Bitget API: {"symbol": "BTCUSDT", "side": "sell", "size": "0.00033333"}
```

## 🔧 使用建议

### 完整测试

使用交互式测试脚本：

```bash
uv run python test_trading_functions.py
```

**测试顺序**:
1. 选项 1 - 查询账户信息 ✅
2. 选项 2 - 测试买入 ✅
3. 选项 3 - 测试卖出 ✅
4. 选项 4 - 测试做空 ✅ (模拟模式)
5. 选项 5 - 测试平空 ✅ (模拟模式)

### 注意事项

1. **模拟盘测试**: 先在模拟盘测试所有功能
2. **精度控制**: 所有数量和价格自动控制在 8 位小数
3. **止盈止损**: 暂时无法自动设置，需要手动管理
4. **最小金额**: Bitget 要求最小下单价值 1 USDT

## 📝 代码示例

### 买入 BTC

```python
from src.trading.bitget_client import BitgetClient
from src.trading.order_manager import OrderManager

# 初始化
client = BitgetClient(
    api_key="your_key",
    api_secret="your_secret",
    passphrase="your_passphrase",
    demo_trading=True
)

manager = OrderManager(client)

# 买入 20 USDT 的 BTC
result = manager.execute_buy_with_protection(
    symbol="BTC/USDT",
    usdt_amount=20.0,
    current_price=60000.0
)

if result:
    print(f"✅ 买入成功，订单ID: {result['buy_order']['orderId']}")
```

## 🔗 相关文档

- [精度修复说明](PRECISION_FIX.md)
- [测试脚本使用指南](TEST_TRADING_FUNCTIONS_README.md)
- [快速开始指南](QUICK_START.md)

## 📅 修复日期

2025-11-01

## ✅ 修复状态

- [x] 数量精度控制（8位小数）
- [x] 市价买入参数修复（使用 USDT 金额）
- [x] 市价卖出参数（使用币数量）
- [ ] 止盈止损单创建（待解决）

---

**总结**: 主要交易功能（买入/卖出）已完全修复并通过测试。止盈止损功能需要进一步研究 Bitget API 文档。
