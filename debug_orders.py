"""
调试订单查询问题
"""
import os
from src.config import Config
from src.trading.bitget_official_client import BitgetOfficialClient

# 读取配置
config = Config()

# 初始化客户端
client = BitgetOfficialClient(
    api_key=config.bitget_api_key,
    api_secret=config.bitget_api_secret,
    passphrase=config.bitget_passphrase,
    demo_trading=config.demo_trading
)

print("=" * 60)
print("调试订单查询")
print("=" * 60)

# 查询BTC/USDT的订单
symbol = "BTC/USDT"
print(f"\n1️⃣ 查询 {symbol} 的挂单：")
orders = client.get_open_orders(symbol)
print(f"\n最终返回的订单数量: {len(orders)}")
if orders:
    for i, order in enumerate(orders, 1):
        print(f"\n订单 {i}:")
        print(f"  OrderID: {order.get('orderId')}")
        print(f"  Symbol: {order.get('symbol')}")
        print(f"  Side: {order.get('side')}")
        print(f"  Size: {order.get('size')}")
        print(f"  Status: {order.get('status')}")

print("\n" + "=" * 60)
print("2️⃣ 查询所有交易对的挂单：")
all_orders = client.get_open_orders()
print(f"\n最终返回的订单数量: {len(all_orders)}")

print("\n" + "=" * 60)
print("3️⃣ 测试直接调用unfilledOrders API：")
try:
    params = {'symbol': 'BTCUSDT'}
    response = client.order_api.unfilledOrders(params)
    print(f"Response: {response}")
except Exception as e:
    print(f"错误: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 60)
print("4️⃣ 测试不带参数调用unfilledOrders：")
try:
    response = client.order_api.unfilledOrders({})
    print(f"Response: {response}")
except Exception as e:
    print(f"错误: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 60)
print("5️⃣ 测试currentPlanOrder：")
try:
    params = {'symbol': 'BTCUSDT'}
    response = client.order_api.currentPlanOrder(params)
    print(f"Response: {response}")
except Exception as e:
    print(f"错误: {e}")
    import traceback
    traceback.print_exc()
