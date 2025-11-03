"""
Bitget 官方 SDK 客户端
使用 Bitget 官方 Python SDK 进行交易
"""

import sys
from pathlib import Path
from typing import Optional, Dict, Any

# 添加官方 SDK 到 Python 路径
sdk_path = Path(__file__).parent.parent / "bitget-python-sdk-api"
sys.path.insert(0, str(sdk_path))

from bitget.v2.spot.order_api import OrderApi
from bitget.v2.spot.account_api import AccountApi
from bitget.v2.spot.market_api import MarketApi
from bitget.exceptions import BitgetAPIException


class BitgetOfficialClient:
    """Bitget 官方 SDK 客户端"""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        passphrase: str,
        demo_trading: bool = False
    ):
        """
        初始化 Bitget 官方客户端

        Args:
            api_key: API Key
            api_secret: API Secret
            passphrase: API Passphrase
            demo_trading: 是否使用 Bitget 模拟盘（True=模拟盘，False=实盘）
        """
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.demo_trading = demo_trading

        # 初始化 API 客户端
        self.order_api = OrderApi(api_key, api_secret, passphrase, demo_trading=demo_trading)
        self.account_api = AccountApi(api_key, api_secret, passphrase, demo_trading=demo_trading)
        # MarketApi 不支持 demo_trading 参数（市场数据对所有环境相同）
        self.market_api = MarketApi(api_key, api_secret, passphrase)

        # 交易对精度信息缓存
        self.symbol_precision_cache: Dict[str, Dict[str, int]] = {}

    @staticmethod
    def floor_precision(value: float, precision: int) -> str:
        """
        向下取整到指定精度（舍弃多余位数，不四舍五入）
        
        Args:
            value: 要处理的数值
            precision: 小数位数
            
        Returns:
            处理后的字符串
            
        Example:
            floor_precision(0.05696, 4) -> "0.0569"  # 舍弃，不是 0.057
            floor_precision(1.23456789, 6) -> "1.234567"
        """
        import math
        multiplier = 10 ** precision
        floored_value = math.floor(value * multiplier) / multiplier
        return str(floored_value)

    def get_symbol_precision(self, symbol: str) -> Optional[Dict[str, int]]:
        """
        获取交易对的精度信息（使用公开API，不需要认证）

        Args:
            symbol: 交易对，如 'BTC/USDT'

        Returns:
            精度信息字典，包含 quantity_precision 和 price_precision
        """
        try:
            # 移除斜杠（Bitget API 格式）
            api_symbol = symbol.replace('/', '')

            # 检查缓存
            if api_symbol in self.symbol_precision_cache:
                return self.symbol_precision_cache[api_symbol]

            # 使用公开API查询交易对信息（不需要签名认证）
            # API文档: https://www.bitget.com/zh-CN/api-doc/spot/market/Get-Symbols
            import requests
            url = 'https://api.bitget.com/api/v2/spot/public/symbols'
            params = {'symbol': api_symbol}

            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()

            data = response.json()

            if data.get('code') == '00000' and data.get('data'):
                symbol_info = data['data'][0]

                # 提取精度信息（注意字段名是 quantityPrecision 和 pricePrecision）
                precision = {
                    'quantity_precision': int(symbol_info.get('quantityPrecision', 6)),
                    'price_precision': int(symbol_info.get('pricePrecision', 2))
                }

                # 缓存精度信息
                self.symbol_precision_cache[api_symbol] = precision
                print(f"✅ 获取 {symbol} 精度: 数量={precision['quantity_precision']}, 价格={precision['price_precision']}")
                return precision

            # 如果查询失败，返回默认精度
            print(f"⚠️ 查询 {symbol} 精度失败，使用默认值")
            precision = {'quantity_precision': 6, 'price_precision': 2}
            return precision

        except Exception as e:
            print(f"⚠️ 获取 {symbol} 精度信息异常: {e}，使用默认值")
            # 返回默认精度
            return {'quantity_precision': 6, 'price_precision': 2}

    def get_balance(self, currency: str = 'USDT') -> Optional[float]:
        """
        获取账户余额

        Args:
            currency: 货币类型，默认 USDT

        Returns:
            可用余额，失败返回 None
        """
        try:
            # 查询资产
            response = self.account_api.assets({})

            if response['code'] == '00000':
                assets = response['data']

                # 模拟盘可能返回空资产列表，这种情况下返回默认余额
                if not assets and self.demo_trading:
                    print(f"[模拟盘] 资产列表为空，返回默认余额 10000.0 {currency}")
                    return 10000.0

                # 查找指定货币
                for asset in assets:
                    if asset['coin'] == currency:
                        return float(asset['available'])

                # 如果是模拟盘且找不到指定货币，返回默认余额
                if self.demo_trading:
                    print(f"[模拟盘] 未找到 {currency} 资产，返回默认余额 10000.0")
                    return 10000.0

            return None

        except BitgetAPIException as e:
            print(f"获取余额失败: {e.message}")
            # 模拟盘模式下，API 异常时也返回默认余额
            if self.demo_trading:
                print(f"[模拟盘] API 异常，返回默认余额 10000.0 {currency}")
                return 10000.0
            return None
        except Exception as e:
            print(f"获取余额异常: {e}")
            # 模拟盘模式下，异常时也返回默认余额
            if self.demo_trading:
                print(f"[模拟盘] 异常，返回默认余额 10000.0 {currency}")
                return 10000.0
            return None

    def place_market_order(
        self,
        symbol: str,
        side: str,
        amount: str
    ) -> Optional[Dict[str, Any]]:
        """
        创建市价单（自动使用动态精度）

        Args:
            symbol: 交易对，如 'BTC/USDT' 或 'BTCUSDT'
            side: 买卖方向 'buy' 或 'sell'
            amount: 数量（字符串格式，买入时为USDT金额，卖出时为币数量）

        Returns:
            订单信息，失败返回 None
        """
        try:
            # 获取动态精度
            precision = self.get_symbol_precision(symbol)
            quantity_precision = precision['quantity_precision']

            # 移除斜杠（Bitget API 格式）
            api_symbol = symbol.replace('/', '')

            # 应用精度向下取整（仅对卖出操作，买入使用USDT金额不需要精度）
            order_size = amount
            if side == 'sell':
                order_size = self.floor_precision(float(amount), quantity_precision)
                print(f"✅ 卖出数量应用精度 {quantity_precision}: {amount} -> {order_size}")

            params = {
                "symbol": api_symbol,
                "side": side,
                "orderType": "market",
                "force": "gtc",
                "size": order_size
            }

            response = self.order_api.placeOrder(params)

            if response['code'] == '00000':
                return response['data']
            else:
                print(f"下单失败: {response.get('msg', 'Unknown error')}")
                return None

        except BitgetAPIException as e:
            print(f"创建订单失败: {e.message}")
            return None
        except Exception as e:
            print(f"创建订单异常: {e}")
            return None

    def place_plan_order(
        self,
        symbol: str,
        side: str,
        amount: str,
        trigger_price: str,
        plan_type: str = "amount"
    ) -> Optional[Dict[str, Any]]:
        """
        创建计划单（用于止盈止损，自动使用动态精度）

        Args:
            symbol: 交易对，如 'BTC/USDT'
            side: 买卖方向 'buy' 或 'sell'
            amount: 数量
            trigger_price: 触发价格
            plan_type: 计划类型，现货API使用 'amount'（币数量）或 'total'（USDT总额）

        Returns:
            订单信息，失败返回 None
        """
        try:
            # 获取动态精度
            precision = self.get_symbol_precision(symbol)
            quantity_precision = precision['quantity_precision']
            price_precision = precision['price_precision']

            # 移除斜杠
            api_symbol = symbol.replace('/', '')

            # 应用精度向下取整
            rounded_amount = self.floor_precision(float(amount), quantity_precision)
            rounded_trigger_price = self.floor_precision(float(trigger_price), price_precision)

            print(f"✅ 计划单应用精度 数量={quantity_precision}, 价格={price_precision}")
            print(f"   数量: {amount} -> {rounded_amount}")
            print(f"   触发价: {trigger_price} -> {rounded_trigger_price}")

            # 现货计划单参数
            # planType: 'amount'（使用币数量）或 'total'（使用USDT总额）
            params = {
                "symbol": api_symbol,
                "side": side,
                "orderType": "market",
                "size": rounded_amount,
                "triggerPrice": rounded_trigger_price,
                "triggerType": "fill_price",  # 按成交价触发
                "planType": plan_type,
            }

            response = self.order_api.placePlanOrder(params)

            if response['code'] == '00000':
                return response['data']
            else:
                print(f"创建计划单失败: {response.get('msg', 'Unknown error')}")
                print(f"完整响应: {response}")
                return None

        except BitgetAPIException as e:
            print(f"创建计划单失败: {e.message}")
            print(f"接口参数: {params}")
            return None
        except Exception as e:
            print(f"创建计划单异常: {e}")
            return None

    def place_order_with_tpsl(
        self,
        symbol: str,
        side: str,
        amount: str,
        take_profit_price: Optional[str] = None,
        stop_loss_price: Optional[str] = None,
        usdt_amount: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        创建带止盈止损的订单（一次性API调用）

        Args:
            symbol: 交易对
            side: 买卖方向
            amount: 数量（卖出时使用）或 None（买入时使用 usdt_amount）
            take_profit_price: 止盈价格（可选）
            stop_loss_price: 止损价格（可选）
            usdt_amount: USDT 金额（买入时使用，优先级高于 amount）

        Returns:
            包含订单信息的字典
        """
        result = {
            'success': False,
            'market_order': None,
            'take_profit_order': None,
            'stop_loss_order': None,
            'errors': [],
            'filled_amount': None
        }

        try:
            # 获取交易对精度信息
            precision = self.get_symbol_precision(symbol)
            quantity_precision = precision['quantity_precision']
            price_precision = precision['price_precision']

            # 移除斜杠
            api_symbol = symbol.replace('/', '')

            # 对于买入，使用 usdt_amount（USDT 金额）
            # 对于卖出，使用 amount（币数量）
            if side == 'buy' and usdt_amount:
                order_size = usdt_amount
            else:
                # 卖出时，数量需要向下取整到指定精度
                order_size = self.floor_precision(float(amount), quantity_precision)

            # 构建订单参数
            params = {
                "symbol": api_symbol,
                "side": side,
                "orderType": "market",
                "force": "gtc",
                "size": order_size
            }

            # 添加止盈止损参数（Bitget API 支持一次性创建）
            # 注意：参数名是 presetTakeProfitPrice 和 presetStopLossPrice
            # 买入时：止盈=卖出价高于买入价，止损=卖出价低于买入价
            # 卖出时：止盈=买入价低于卖出价，止损=买入价高于卖出价
            if take_profit_price:
                # 价格向下取整到指定精度
                tp_price = self.floor_precision(float(take_profit_price), price_precision)
                params["presetTakeProfitPrice"] = tp_price

            if stop_loss_price:
                # 价格向下取整到指定精度
                sl_price = self.floor_precision(float(stop_loss_price), price_precision)
                params["presetStopLossPrice"] = sl_price

            # 一次性创建订单（包含止盈止损）
            print(f"\n📝 创建订单参数（使用动态精度 {quantity_precision}/{price_precision}）:")
            print(f"   交易对: {symbol}")
            print(f"   方向: {side}")
            print(f"   数量/金额: {order_size}")
            if "presetTakeProfitPrice" in params:
                print(f"   止盈价: {params['presetTakeProfitPrice']}")
            if "presetStopLossPrice" in params:
                print(f"   止损价: {params['presetStopLossPrice']}")

            response = self.order_api.placeOrder(params)

            if response['code'] == '00000':
                market_order = response['data']
                result['market_order'] = market_order
                result['success'] = True

                print(f"✅ 订单已创建: {market_order.get('orderId', 'N/A')}")
                if "presetTakeProfitPrice" in params:
                    print(f"✅ 止盈价已设置: {params['presetTakeProfitPrice']}")
                    result['take_profit_order'] = {'price': params['presetTakeProfitPrice']}
                if "presetStopLossPrice" in params:
                    print(f"✅ 止损价已设置: {params['presetStopLossPrice']}")
                    result['stop_loss_order'] = {'price': params['presetStopLossPrice']}

                return result
            else:
                error_msg = response.get('msg', 'Unknown error')
                result['errors'].append(f'下单失败: {error_msg}')
                print(f"❌ 下单失败: {error_msg}")
                return result

        except BitgetAPIException as e:
            result['errors'].append(f'API异常: {e.message}')
            print(f"❌ 创建订单失败: {e.message}")
            return result
        except Exception as e:
            result['errors'].append(f'异常: {str(e)}')
            print(f"❌ 下单过程出错: {e}")
            import traceback
            traceback.print_exc()
            return result

    def cancel_order(self, order_id: str, symbol: str) -> bool:
        """
        取消订单

        Args:
            order_id: 订单ID
            symbol: 交易对

        Returns:
            是否成功
        """
        try:
            symbol = symbol.replace('/', '')

            params = {
                "orderId": order_id,
                "symbol": symbol
            }

            response = self.order_api.cancelOrder(params)
            return response['code'] == '00000'

        except BitgetAPIException as e:
            print(f"取消订单失败: {e.message}")
            return False
        except Exception as e:
            print(f"取消订单异常: {e}")
            return False

    def cancel_plan_order(self, order_id: str, symbol: str) -> bool:
        """
        取消计划单

        Args:
            order_id: 订单ID
            symbol: 交易对

        Returns:
            是否成功
        """
        try:
            symbol = symbol.replace('/', '')

            params = {
                "orderId": order_id,
                "symbol": symbol
            }

            response = self.order_api.cancelPlanOrder(params)
            return response['code'] == '00000'

        except BitgetAPIException as e:
            print(f"取消计划单失败: {e.message}")
            return False
        except Exception as e:
            print(f"取消计划单异常: {e}")
            return False

    def get_open_orders(self, symbol: Optional[str] = None) -> list:
        """
        获取未完成的订单（包括普通订单和计划单）

        Args:
            symbol: 交易对（可选）

        Returns:
            订单列表
        """
        try:
            all_orders = []
            
            params = {
                "limit": '100'
            }
            if symbol:
                params['symbol'] = symbol.replace('/', '')
            
            # 1. 获取未完成的普通订单
            try:
                print(f"🔍 调试 - 查询参数: {params}")
                response = self.order_api.unfilledOrders(params)
                print(f"🔍 调试 - unfilledOrders响应: {response}")
                if response.get('code') == '00000':
                    orders = response.get('data', [])
                    print(f"🔍 调试 - 获取到的订单数据: {orders}")
                    if orders:
                        all_orders.extend(orders)
                        print(f"✅ 查询到 {len(orders)} 个未成交订单")
                    else:
                        print(f"⚠️ unfilledOrders返回空列表")
                else:
                    print(f"❌ unfilledOrders API返回错误: code={response.get('code')}, msg={response.get('msg')}")
            except Exception as e:
                print(f"⚠️ 查询未成交订单失败: {e}")
                import traceback
                traceback.print_exc()
            # 2. 获取计划单（止盈止损单）
            try:
                print(f"🔍 调试 - 查询计划单参数: {params}")
                plan_response = self.order_api.currentPlanOrder(params)
                print(f"🔍 调试 - currentPlanOrder响应: {plan_response}")
                if plan_response.get('code') == '00000':
                    # 计划单API返回的data是一个字典，包含orderList
                    plan_data = plan_response.get('data', {})
                    print(f"🔍 调试 - 计划单data类型: {type(plan_data)}, 内容: {plan_data}")
                    if isinstance(plan_data, dict):
                        plan_orders = plan_data.get('orderList', [])
                    else:
                        plan_orders = []
                    
                    if plan_orders:
                        all_orders.extend(plan_orders)
                        print(f"✅ 查询到 {len(plan_orders)} 个计划单")
                    else:
                        print(f"⚠️ currentPlanOrder返回空列表")
                else:
                    print(f"❌ currentPlanOrder API返回错误: code={plan_response.get('code')}, msg={plan_response.get('msg')}")
            except Exception as e:
                print(f"⚠️ 查询计划单失败: {e}")
                import traceback
                traceback.print_exc()

            return all_orders

        except BitgetAPIException as e:
            print(f"❌ 获取订单失败: {e.message}")
            return []
        except Exception as e:
            print(f"❌ 获取订单异常: {e}")
            import traceback
            traceback.print_exc()
            return []


def test_bitget_official_client():
    """测试 Bitget 官方客户端（模拟盘）"""
    print("=== 测试 Bitget 官方 SDK 客户端（模拟盘）===\n")

    # 使用测试凭证
    client = BitgetOfficialClient(
        api_key="test_key",
        api_secret="test_secret",
        passphrase="test_passphrase",
        demo_trading=True  # 使用模拟盘
    )

    # 测试获取余额
    print("1. 测试获取余额...")
    balance = client.get_balance('USDT')
    print(f"USDT 余额: {balance}\n")

    # 测试带止盈止损的订单
    print("2. 测试创建带止盈止损的订单...")
    result = client.place_order_with_tpsl(
        symbol='BTC/USDT',
        side='buy',
        amount='0.001',
        take_profit_price='65000',
        stop_loss_price='58000'
    )

    print(f"\n订单结果:")
    print(f"  成功: {result['success']}")
    print(f"  市价单: {result['market_order']}")
    print(f"  止盈单: {result['take_profit_order']}")
    print(f"  止损单: {result['stop_loss_order']}")
    if result['errors']:
        print(f"  错误: {result['errors']}")


if __name__ == "__main__":
    test_bitget_official_client()
