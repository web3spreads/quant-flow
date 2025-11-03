"""
检查订单的止盈止损设置
"""
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

print("=" * 80)
print("检查历史订单的TPSL设置")
print("=" * 80)

# 查询历史订单
params = {
    'symbol': 'BTCUSDT',
    'limit': '20'
}

print(f"\n查询最近20个历史订单...")
response = client.order_api.historyOrders(params)

if response.get('code') == '00000':
    orders = response.get('data', [])
    print(f"找到 {len(orders)} 个历史订单\n")
    
    for order in orders:
        order_id = order.get('orderId')
        status = order.get('status')
        side = order.get('side')
        size = order.get('size')
        tpsl_type = order.get('tpslType', 'N/A')
        
        print(f"订单 {order_id}:")
        print(f"  状态: {status}")
        print(f"  方向: {side}")
        print(f"  数量: {size}")
        print(f"  TPSL类型: {tpsl_type}")
        
        # 检查是否有止盈止损字段
        if 'presetStopSurplusPrice' in order:
            print(f"  止盈价: {order.get('presetStopSurplusPrice')}")
        if 'presetStopLossPrice' in order:
            print(f"  止损价: {order.get('presetStopLossPrice')}")
        if 'stopSurplusTriggerPrice' in order:
            print(f"  止盈触发价: {order.get('stopSurplusTriggerPrice')}")
        if 'stopLossTriggerPrice' in order:
            print(f"  止损触发价: {order.get('stopLossTriggerPrice')}")
            
        print()
else:
    print(f"❌ 查询失败: {response.get('msg')}")

print("=" * 80)
print("\n检查账户信息和冻结余额：")
try:
    account_response = client.account_api.assets({})
    if account_response.get('code') == '00000':
        assets = account_response.get('data', [])
        for asset in assets:
            coin = asset.get('coin')
            available = float(asset.get('available', 0))
            frozen = float(asset.get('frozen', 0))
            if frozen > 0:
                print(f"\n{coin}:")
                print(f"  可用: {available}")
                print(f"  冻结: {frozen}")
                print(f"  总额: {available + frozen}")
except Exception as e:
    print(f"❌ 查询失败: {e}")
