# 数量精度修复说明

## 问题描述

在使用 Bitget API 进行交易时，遇到以下错误：

```
{"code":"40808","msg":"参数校验异常 delegateAmount checkBDScale error value=0.0003333333333333333 checkScale=8"}
```

**错误原因**：
- 买入数量的小数位数超过了 Bitget API 允许的最大精度（8位小数）
- 例如：20 USDT / 60000 USD = 0.0003333333333333333（19位小数）
- Bitget API 要求：`checkScale=8`（最多 8 位小数）

## 修复方案

### 1. 在计算数量时控制精度

**文件**: `src/trading/order_manager.py`

**修改位置**: `calculate_amount_from_usdt()` 方法

```python
def calculate_amount_from_usdt(
    self,
    symbol: str,
    usdt_amount: float,
    current_price: float
) -> float:
    """
    根据 USDT 金额计算购买数量

    Args:
        symbol: 交易对
        usdt_amount: USDT 金额
        current_price: 当前价格

    Returns:
        购买数量（精度控制在 8 位小数）
    """
    amount = usdt_amount / current_price
    # Bitget API 要求数量精度不超过 8 位小数
    # 使用 round 函数进行四舍五入
    amount = round(amount, 8)
    return amount
```

### 2. 在转换字符串时添加双重保险

**文件**: `src/trading/bitget_client.py`

**修改位置 1**: `create_market_buy_order()` 方法

```python
def create_market_buy_order(self, symbol: str, amount: float, params: Dict[str, Any] = None):
    """创建市价买单"""
    if self.use_official_sdk:
        # 确保数量精度不超过 8 位小数（Bitget API 限制）
        amount_str = str(round(amount, 8))
        return self._client.place_market_order(symbol, 'buy', amount_str)
```

**修改位置 2**: `create_market_sell_order()` 方法

```python
def create_market_sell_order(self, symbol: str, amount: float, params: Dict[str, Any] = None):
    """创建市价卖单"""
    if self.use_official_sdk:
        # 确保数量精度不超过 8 位小数（Bitget API 限制）
        amount_str = str(round(amount, 8))
        return self._client.place_market_order(symbol, 'sell', amount_str)
```

**修改位置 3**: `create_order_with_tpsl()` 方法（止盈止损订单）

```python
if self.use_official_sdk:
    # 确保数量和价格精度不超过 8 位小数（Bitget API 限制）
    return self._client.place_order_with_tpsl(
        symbol=symbol,
        side=side,
        amount=str(round(amount, 8)),
        take_profit_price=str(round(take_profit_price, 8)) if take_profit_price else None,
        stop_loss_price=str(round(stop_loss_price, 8)) if stop_loss_price else None
    )
```

## 测试验证

### 精度控制验证

```python
# 原始计算
amount = 20 / 60000.0
# 结果: 0.0003333333333333333 (19位小数) ❌

# 应用精度控制
rounded_amount = round(amount, 8)
# 结果: 0.00033333 (8位小数) ✅

# 字符串转换
amount_str = str(rounded_amount)
# 结果: "0.00033333" ✅
```

### 测试脚本

使用 `test_trading_functions.py` 测试买入功能：

```bash
uv run python test_trading_functions.py
# 选择选项 2 - 测试买入
```

**预期结果**：
- ✅ 买入数量精度控制在 8 位小数
- ✅ API 请求成功，不再出现 `checkBDScale error`
- ✅ 订单创建成功

## 影响范围

### 修复涵盖的操作

1. ✅ 市价买入（开多）
2. ✅ 市价卖出（平多）
3. ✅ 做空（开空）
4. ✅ 平空
5. ✅ 止盈止损订单

### 适用场景

- 所有使用 Bitget 官方 SDK 的交易操作
- 所有计算买入/卖出数量的场景
- 所有价格精度要求的场景

## 技术细节

### Python round() 函数特性

```python
round(0.0003333333333333333, 8)
# 返回: 0.00033333

str(0.00033333)
# 返回: "0.00033333" (自动去除末尾的零)
```

### Bitget API 精度限制

根据 Bitget API 文档：
- **数量精度**: 最多 8 位小数 (`checkScale=8`)
- **价格精度**: 最多 8 位小数
- 超过精度限制会返回 `40808` 错误码

### 边界情况处理

1. **极小数量**:
   - 原始: `0.0000000001` (10位小数)
   - 四舍五入: `0.0` (8位小数)
   - 建议: 设置最小交易金额避免此情况

2. **高价币种**:
   - BTC 价格 $100,000，买入 10 USDT
   - 数量: 10 / 100000 = 0.0001 (4位小数) ✅

3. **低价币种**:
   - 某山寨币价格 $0.0001，买入 10 USDT
   - 数量: 10 / 0.0001 = 100000 (0位小数) ✅

## 相关文档

- [Bitget API 官方文档](https://bitgetlimited.github.io/apidoc/en/spot/)
- [测试脚本使用指南](TEST_TRADING_FUNCTIONS_README.md)
- [快速开始指南](QUICK_START.md)

## 修复日期

2025-11-01

## 版本历史

- v1.0 (2025-11-01): 初始修复，添加 8 位小数精度控制
