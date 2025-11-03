# 真实合约做空功能实现

## 🎯 重大改进

### 问题
之前的做空功能是**模拟的**，使用现货账户"买入+卖出"来模拟做空，这种方式：
- ❌ 不是真正的做空
- ❌ 无法获得真实的做空收益
- ❌ 需要占用双倍资金
- ❌ 有时间差风险

### 解决方案
实现**真实的合约做空**，使用 Bitget U本位合约：
- ✅ 真实的做空交易
- ✅ 支持杠杆（默认10倍）
- ✅ 资金利用率高
- ✅ 一次API调用完成

---

## 📋 实现内容

### 1. 新增合约客户端

**文件**: `src/trading/bitget_contract_client.py`

核心功能：
- `open_short()` - 开空仓
- `close_short()` - 平空仓
- `get_symbol_precision()` - 获取合约精度
- `get_positions()` - 查询合约持仓

**示例代码**:
```python
from src.trading.bitget_contract_client import BitgetContractClient

# 创建合约客户端
contract_client = BitgetContractClient(
    api_key=api_key,
    api_secret=api_secret,
    passphrase=passphrase,
    demo_trading=True,
    product_type="USDT-FUTURES"  # U本位合约
)

# 开空仓（10倍杠杆）
order = contract_client.open_short(
    symbol='BTC/USDT',
    size='0.01',           # 数量
    leverage=10,           # 杠杆倍数
    take_profit_price='55000',  # 止盈价
    stop_loss_price='62000'     # 止损价
)

# 平空仓
close_order = contract_client.close_short(
    symbol='BTC/USDT',
    size='0.01'
)
```

### 2. 更新订单管理器

**文件**: `src/trading/order_manager.py`

**新增参数**:
```python
OrderManager(
    client=spot_client,              # 现货客户端
    contract_client=contract_client, # 合约客户端（新增）
    use_contract_for_short=True,     # 是否使用合约做空（新增）
    leverage=10                       # 杠杆倍数（新增）
)
```

**功能增强**:
- `execute_sell_short_with_protection()` - 支持合约和现货两种模式
- `execute_buy_to_cover()` - 支持合约平仓

---

## 🔄 合约做空流程

### 开空仓流程
```
1. 计算做空数量和止盈止损价格
   ↓
2. 调用合约API开空仓
   - symbol: BTCUSDT
   - side: sell (卖出)
   - tradeSide: open (开仓)
   - size: 0.01
   - leverage: 10x
   ↓
3. 设置止盈止损
   - presetStopSurplusPrice (止盈价)
   - presetStopLossPrice (止损价)
   ↓
4. 记录持仓信息
   - is_contract: True
   - leverage: 10
```

### 平空仓流程
```
1. 检查持仓类型（合约 vs 现货模拟）
   ↓
2. 调用合约API平空仓
   - symbol: BTCUSDT
   - side: buy (买入)
   - tradeSide: close (平仓)
   - size: 0.01
   ↓
3. 移除持仓记录
   ↓
4. 返回平仓结果
```

---

## 📊 合约 vs 现货模拟对比

| 特性 | 现货模拟做空 | 合约真实做空 |
|------|-------------|-------------|
| **实现方式** | 买入+卖出 | 直接做空 |
| **资金占用** | 2倍（买+卖） | 1/杠杆倍数 |
| **杠杆** | 无 | 支持（1-125倍） |
| **做空收益** | 模拟的 | 真实的 |
| **API调用** | 2次 | 1次 |
| **时间差风险** | 有 | 无 |
| **止盈止损** | 分开设置 | 一次设置 |
| **适用场景** | 测试/演示 | 真实交易 |

### 资金占用示例

**场景**: 做空 0.01 BTC，当前价格 $60,000

| 模式 | 资金占用 | 说明 |
|------|----------|------|
| 现货模拟 | $1,200 | 需要买入0.01 BTC ($600) + 卖出保证金 ($600) |
| 合约 10x | $60 | 只需 $600 / 10 = $60 保证金 |
| 合约 20x | $30 | 只需 $600 / 20 = $30 保证金 |

**资金效率**: 合约做空的资金效率是现货模拟的 **10-20倍**！

---

## ⚙️ API 参数说明

### 开空仓参数

根据 [Bitget API 文档](https://www.bitget.com/zh-CN/api-doc/contract/trade/Place-Order):

```json
{
  "symbol": "BTCUSDT",
  "productType": "USDT-FUTURES",
  "marginMode": "crossed",      // crossed(全仓) / isolated(逐仓)
  "marginCoin": "USDT",
  "size": "0.01",
  "side": "sell",               // 卖出 = 做空
  "tradeSide": "open",          // 开仓
  "orderType": "market",        // 市价单
  "presetStopSurplusPrice": "55000",  // 止盈价（可选）
  "presetStopLossPrice": "62000"       // 止损价（可选）
}
```

### 平空仓参数

```json
{
  "symbol": "BTCUSDT",
  "productType": "USDT-FUTURES",
  "marginMode": "crossed",
  "marginCoin": "USDT",
  "size": "0.01",
  "side": "buy",                // 买入 = 平空
  "tradeSide": "close",         // 平仓
  "orderType": "market"
}
```

---

## 💡 使用示例

### 方式1: 直接使用合约客户端

```python
from src.trading.bitget_contract_client import BitgetContractClient

client = BitgetContractClient(
    api_key=api_key,
    api_secret=api_secret,
    passphrase=passphrase,
    demo_trading=True
)

# 开空仓
client.open_short(
    symbol='BTC/USDT',
    size='0.01',
    leverage=10,
    take_profit_price='55000',
    stop_loss_price='62000'
)

# 平空仓
client.close_short('BTC/USDT', '0.01')
```

### 方式2: 通过订单管理器

```python
from src.trading.bitget_client import BitgetClient
from src.trading.bitget_contract_client import BitgetContractClient
from src.trading.order_manager import OrderManager

# 创建客户端
spot_client = BitgetClient(...)
contract_client = BitgetContractClient(...)

# 创建订单管理器（启用合约做空）
manager = OrderManager(
    client=spot_client,
    contract_client=contract_client,
    use_contract_for_short=True,  # 使用合约做空
    leverage=10
)

# 执行做空（自动使用合约）
manager.execute_sell_short_with_protection(
    symbol='BTC/USDT',
    usdt_amount=100,
    current_price=60000
)

# 平空仓
manager.execute_buy_to_cover(
    symbol='BTC/USDT',
    amount=0.001,
    current_price=58000
)
```

---

## 🔧 配置选项

在主程序中可以通过配置切换做空模式：

```python
# 使用合约做空（推荐）
manager = OrderManager(
    client=spot_client,
    contract_client=contract_client,
    use_contract_for_short=True,   # 启用合约做空
    leverage=10                     # 10倍杠杆
)

# 使用现货模拟做空（仅测试）
manager = OrderManager(
    client=spot_client,
    use_contract_for_short=False   # 禁用合约，使用现货模拟
)
```

---

## ⚠️ 注意事项

### 1. 杠杆风险
- 杠杆放大收益的同时也放大风险
- 建议初始使用 5-10倍杠杆
- 设置好止损价格，避免爆仓

### 2. 资金要求
- 合约做空需要足够的保证金
- 保证金 = 开仓价值 / 杠杆倍数
- 预留一定余额应对价格波动

### 3. 测试建议
- 先在模拟盘测试（`demo_trading=True`）
- 确认功能正常后再用于实盘
- 小额测试，逐步增加

### 4. API 限制
- 合约API有频率限制
- 注意单笔最小/最大下单量
- 不同合约的精度要求不同

---

## 📁 涉及文件

### 新增文件
- `src/trading/bitget_contract_client.py` - 合约交易客户端

### 修改文件
- `src/trading/order_manager.py` - 订单管理器（支持合约）
  - `__init__()` - 新增合约客户端参数
  - `execute_sell_short_with_protection()` - 支持合约做空
  - `execute_buy_to_cover()` - 支持合约平仓

### 相关文档
- [Bitget 合约下单 API](https://www.bitget.com/zh-CN/api-doc/contract/trade/Place-Order)
- [止盈止损优化文档](ORDER_WITH_TPSL_OPTIMIZATION.md)
- [精度修复文档](TPSL_FIX_2025-11-02.md)

---

## ✅ 实现状态

- [x] 创建合约交易客户端
- [x] 实现开空仓功能
- [x] 实现平空仓功能
- [x] 集成到订单管理器
- [x] 支持止盈止损
- [x] 动态精度查询
- [x] 测试模式支持
- [x] 模拟盘支持
- [x] 文档编写

**实现日期**: 2025-11-02
**状态**: ✅ 已完成
**类型**: 功能增强（从模拟做空升级到真实合约做空）

---

## 🎯 总结

通过实现真实的合约做空功能，系统获得了以下提升：

1. **真实做空**: 不再是模拟，而是真实的合约交易
2. **资金效率**: 使用杠杆，资金效率提升10-20倍
3. **功能完整**: 支持止盈止损、精度动态查询
4. **灵活配置**: 可选择合约或现货模拟两种模式
5. **降低风险**: 一次API调用完成，无时间差风险

**推荐配置**: 使用合约做空 + 10倍杠杆 + 严格止损
