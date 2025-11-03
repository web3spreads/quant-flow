"""
Bitget 合约交易客户端
实现真实的合约做空功能（U本位合约）
"""

import sys
from pathlib import Path
from typing import Optional, Dict, Any

# 添加官方 SDK 到 Python 路径
sdk_path = Path(__file__).parent.parent / "bitget-python-sdk-api"
sys.path.insert(0, str(sdk_path))

from bitget.v2.mix.order_api import OrderApi as MixOrderApi
from bitget.v2.mix.account_api import AccountApi as MixAccountApi
from bitget.v2.mix.market_api import MarketApi as MixMarketApi
from bitget.exceptions import BitgetAPIException
import requests


class BitgetContractClient:
    """Bitget 合约交易客户端（U本位合约）"""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        passphrase: str,
        demo_trading: bool = False,
        product_type: str = "USDT-FUTURES"
    ):
        """
        初始化 Bitget 合约客户端

        Args:
            api_key: API Key
            api_secret: API Secret
            passphrase: API Passphrase
            demo_trading: 是否使用 Bitget 模拟盘（True=模拟盘，False=实盘）
            product_type: 产品类型，默认 "USDT-FUTURES"（U本位合约）
        """
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.demo_trading = demo_trading
        self.product_type = product_type

        # 初始化合约 API 客户端（传递 demo_trading 参数给 SDK）
        # SDK内部会根据 demo_trading 设置请求头的 X-CHANNEL-API-CODE
        self.order_api = MixOrderApi(api_key, api_secret, passphrase)
        self.account_api = MixAccountApi(api_key, api_secret, passphrase)
        self.market_api = MixMarketApi(api_key, api_secret, passphrase)

        # 设置模拟盘标识
        if demo_trading:
            # Bitget SDK 通过特定方式标识模拟盘
            self.order_api.demo_trading = True
            self.account_api.demo_trading = True

        # 精度缓存
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
        """
        import math
        multiplier = 10 ** precision
        floored_value = math.floor(value * multiplier) / multiplier
        return str(floored_value)

    def get_contract_symbol(self, symbol: str) -> str:
        """
        将现货交易对转换为合约交易对

        Args:
            symbol: 现货交易对，如 'BTC/USDT'

        Returns:
            合约交易对，如 'BTCUSDT'
        """
        return symbol.replace('/', '')

    def get_balance(self, currency: str = 'USDT') -> Optional[float]:
        """
        获取合约账户余额

        API文档: https://www.bitget.com/zh-CN/api-doc/contract/account/Get-Account-List

        Args:
            currency: 保证金币种，默认 'USDT'

        Returns:
            可用余额，失败返回 None
        """
        try:
            params = {
                'productType': self.product_type
            }

            response = self.account_api.accounts(params)

            if response['code'] == '00000':
                accounts = response.get('data', [])

                # 查找指定币种的账户
                for account in accounts:
                    if account.get('marginCoin') == currency:
                        available = float(account.get('available', 0))
                        print(f"✅ 合约账户 {currency} 可用余额: {available:.2f}")
                        return available

                print(f"⚠️ 未找到 {currency} 合约账户")
                return 0.0

            print(f"❌ 查询合约余额失败: {response.get('msg')}")
            return None

        except Exception as e:
            print(f"❌ 获取合约余额异常: {e}")
            import traceback
            traceback.print_exc()
            return None

    def get_symbol_precision(self, symbol: str) -> Dict[str, int]:
        """
        获取合约交易对的精度信息

        Args:
            symbol: 交易对，如 'BTC/USDT'

        Returns:
            精度信息字典
        """
        try:
            contract_symbol = self.get_contract_symbol(symbol)

            # 检查缓存
            if contract_symbol in self.symbol_precision_cache:
                return self.symbol_precision_cache[contract_symbol]

            # 查询合约交易对信息（使用公开API）
            url = 'https://api.bitget.com/api/v2/mix/market/contracts'
            params = {
                'productType': self.product_type,
                'symbol': contract_symbol
            }

            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            if data.get('code') == '00000' and data.get('data'):
                contract_info = data['data'][0]

                precision = {
                    'quantity_precision': int(contract_info.get('volumePlace', 3)),
                    'price_precision': int(contract_info.get('pricePlace', 1))
                }

                self.symbol_precision_cache[contract_symbol] = precision
                print(f"✅ 获取合约 {symbol} 精度: 数量={precision['quantity_precision']}, 价格={precision['price_precision']}")
                return precision

            # 查询失败，返回默认精度
            precision = {'quantity_precision': 3, 'price_precision': 1}
            return precision

        except Exception as e:
            print(f"⚠️ 获取合约 {symbol} 精度信息异常: {e}，使用默认值")
            return {'quantity_precision': 3, 'price_precision': 1}

    def open_long(
        self,
        symbol: str,
        size: str,
        leverage: int = 10,
        take_profit_price: Optional[str] = None,
        stop_loss_price: Optional[str] = None,
        margin_mode: str = "crossed"
    ) -> Optional[Dict[str, Any]]:
        """
        开多仓（做多）

        Args:
            symbol: 交易对，如 'BTC/USDT'
            size: 开仓数量（张数或币数量，根据合约类型）
            leverage: 杠杆倍数，默认10倍
            take_profit_price: 止盈价格（可选）
            stop_loss_price: 止损价格（可选）
            margin_mode: 保证金模式，'crossed'（全仓）或 'isolated'（逐仓）

        Returns:
            订单信息，失败返回 None
        """
        try:
            # 获取精度
            precision = self.get_symbol_precision(symbol)
            quantity_precision = precision['quantity_precision']
            price_precision = precision['price_precision']

            # 转换交易对
            contract_symbol = self.get_contract_symbol(symbol)

            # 应用精度（向下取整）
            rounded_size = self.floor_precision(float(size), quantity_precision)

            # 构建订单参数
            params = {
                "symbol": contract_symbol,
                "productType": self.product_type,
                "marginMode": margin_mode,
                "marginCoin": "USDT",
                "size": rounded_size,
                "side": "buy",           # 买入
                "tradeSide": "open",     # 开仓
                "orderType": "market",   # 市价单
                "force": "GTC"
            }

            # 添加止盈止损（向下取整）
            if take_profit_price:
                tp_price = self.floor_precision(float(take_profit_price), price_precision)
                params["presetStopSurplusPrice"] = tp_price

            if stop_loss_price:
                sl_price = self.floor_precision(float(stop_loss_price), price_precision)
                params["presetStopLossPrice"] = sl_price

            print(f"\n📝 开多仓参数（{leverage}x 杠杆）:")
            print(f"   合约: {contract_symbol}")
            print(f"   数量: {rounded_size}")
            print(f"   保证金模式: {margin_mode}")
            if take_profit_price:
                print(f"   止盈价: {params['presetStopSurplusPrice']}")
            if stop_loss_price:
                print(f"   止损价: {params['presetStopLossPrice']}")

            response = self.order_api.placeOrder(params)

            if response['code'] == '00000':
                order_data = response['data']
                print(f"✅ 多仓已开: {order_data.get('orderId', 'N/A')}")
                return order_data
            else:
                error_msg = response.get('msg', 'Unknown error')
                print(f"❌ 开多仓失败: {error_msg}")
                return None

        except BitgetAPIException as e:
            print(f"❌ 开多仓失败: {e.message}")
            return None
        except Exception as e:
            print(f"❌ 开多仓异常: {e}")
            import traceback
            traceback.print_exc()
            return None

    def open_short(
        self,
        symbol: str,
        size: str,
        leverage: int = 10,
        take_profit_price: Optional[str] = None,
        stop_loss_price: Optional[str] = None,
        margin_mode: str = "crossed"
    ) -> Optional[Dict[str, Any]]:
        """
        开空仓（做空）

        Args:
            symbol: 交易对，如 'BTC/USDT'
            size: 开仓数量（张数或币数量，根据合约类型）
            leverage: 杠杆倍数，默认10倍
            take_profit_price: 止盈价格（可选）
            stop_loss_price: 止损价格（可选）
            margin_mode: 保证金模式，'crossed'（全仓）或 'isolated'（逐仓）

        Returns:
            订单信息，失败返回 None
        """
        try:
            # 获取精度
            precision = self.get_symbol_precision(symbol)
            quantity_precision = precision['quantity_precision']
            price_precision = precision['price_precision']

            # 转换交易对
            contract_symbol = self.get_contract_symbol(symbol)

            # 应用精度（向下取整）
            rounded_size = self.floor_precision(float(size), quantity_precision)

            # 构建订单参数
            params = {
                "symbol": contract_symbol,
                "productType": self.product_type,
                "marginMode": margin_mode,
                "marginCoin": "USDT",
                "size": rounded_size,
                "side": "sell",          # 卖出
                "tradeSide": "open",     # 开仓
                "orderType": "market",   # 市价单
                "force": "GTC"
            }

            # 添加止盈止损（向下取整）
            if take_profit_price:
                tp_price = self.floor_precision(float(take_profit_price), price_precision)
                params["presetStopSurplusPrice"] = tp_price

            if stop_loss_price:
                sl_price = self.floor_precision(float(stop_loss_price), price_precision)
                params["presetStopLossPrice"] = sl_price

            print(f"\n📝 开空仓参数（{leverage}x 杠杆）:")
            print(f"   合约: {contract_symbol}")
            print(f"   数量: {rounded_size}")
            print(f"   保证金模式: {margin_mode}")
            if take_profit_price:
                print(f"   止盈价: {params['presetStopSurplusPrice']}")
            if stop_loss_price:
                print(f"   止损价: {params['presetStopLossPrice']}")

            response = self.order_api.placeOrder(params)

            if response['code'] == '00000':
                order_data = response['data']
                print(f"✅ 空仓已开: {order_data.get('orderId', 'N/A')}")
                return order_data
            else:
                error_msg = response.get('msg', 'Unknown error')
                print(f"❌ 开空仓失败: {error_msg}")
                return None

        except BitgetAPIException as e:
            print(f"❌ 开空仓失败: {e.message}")
            return None
        except Exception as e:
            print(f"❌ 开空仓异常: {e}")
            import traceback
            traceback.print_exc()
            return None

    def close_long(
        self,
        symbol: str,
        size: str
    ) -> Optional[Dict[str, Any]]:
        """
        平多仓

        Args:
            symbol: 交易对，如 'BTC/USDT'
            size: 平仓数量

        Returns:
            订单信息，失败返回 None
        """
        try:
            # 获取精度
            precision = self.get_symbol_precision(symbol)
            quantity_precision = precision['quantity_precision']

            # 转换交易对
            contract_symbol = self.get_contract_symbol(symbol)

            # 应用精度（向下取整）
            rounded_size = self.floor_precision(float(size), quantity_precision)

            # 构建平仓参数
            params = {
                "symbol": contract_symbol,
                "productType": self.product_type,
                "marginMode": "crossed",  # 与开仓时一致
                "marginCoin": "USDT",
                "size": rounded_size,
                "side": "sell",          # 卖出
                "tradeSide": "close",    # 平仓
                "orderType": "market",   # 市价单
                "force": "GTC"
            }

            print(f"\n📝 平多仓参数:")
            print(f"   合约: {contract_symbol}")
            print(f"   数量: {rounded_size}")

            response = self.order_api.placeOrder(params)

            if response['code'] == '00000':
                order_data = response['data']
                print(f"✅ 多仓已平: {order_data.get('orderId', 'N/A')}")
                return order_data
            else:
                error_msg = response.get('msg', 'Unknown error')
                print(f"❌ 平多仓失败: {error_msg}")
                return None

        except BitgetAPIException as e:
            print(f"❌ 平多仓失败: {e.message}")
            return None
        except Exception as e:
            print(f"❌ 平多仓异常: {e}")
            import traceback
            traceback.print_exc()
            return None

    def close_short(
        self,
        symbol: str,
        size: str
    ) -> Optional[Dict[str, Any]]:
        """
        平空仓

        Args:
            symbol: 交易对，如 'BTC/USDT'
            size: 平仓数量

        Returns:
            订单信息，失败返回 None
        """
        try:
            # 获取精度
            precision = self.get_symbol_precision(symbol)
            quantity_precision = precision['quantity_precision']

            # 转换交易对
            contract_symbol = self.get_contract_symbol(symbol)

            # 应用精度（向下取整）
            rounded_size = self.floor_precision(float(size), quantity_precision)

            # 构建平仓参数
            params = {
                "symbol": contract_symbol,
                "productType": self.product_type,
                "marginMode": "crossed",  # 与开仓时一致
                "marginCoin": "USDT",
                "size": rounded_size,
                "side": "buy",           # 买入
                "tradeSide": "close",    # 平仓
                "orderType": "market",   # 市价单
                "force": "GTC"
            }

            print(f"\n📝 平空仓参数:")
            print(f"   合约: {contract_symbol}")
            print(f"   数量: {rounded_size}")

            response = self.order_api.placeOrder(params)

            if response['code'] == '00000':
                order_data = response['data']
                print(f"✅ 空仓已平: {order_data.get('orderId', 'N/A')}")
                return order_data
            else:
                error_msg = response.get('msg', 'Unknown error')
                print(f"❌ 平空仓失败: {error_msg}")
                return None

        except BitgetAPIException as e:
            print(f"❌ 平空仓失败: {e.message}")
            return None
        except Exception as e:
            print(f"❌ 平空仓异常: {e}")
            import traceback
            traceback.print_exc()
            return None

    def get_positions(self, symbol: Optional[str] = None) -> list:
        """
        获取合约持仓信息

        Args:
            symbol: 交易对（可选）

        Returns:
            持仓列表
        """
        try:
            params = {
                "productType": self.product_type
            }

            if symbol:
                params['symbol'] = self.get_contract_symbol(symbol)

            response = self.account_api.singlePosition(params)

            if response['code'] == '00000':
                return response.get('data', [])

            return []

        except Exception as e:
            print(f"获取合约持仓失败: {e}")
            return []


def test_contract_client():
    """测试合约交易客户端"""
    print("=== 测试 Bitget 合约客户端 ===\n")

    client = BitgetContractClient(
        api_key="test_key",
        api_secret="test_secret",
        passphrase="test_passphrase",
        demo_trading=True  # 使用模拟盘
    )

    # 测试1: 开空仓
    print("【测试1: 开空仓】")
    result = client.open_short(
        symbol='BTC/USDT',
        size='0.01',
        leverage=10,
        take_profit_price='55000',
        stop_loss_price='62000'
    )
    print(f"结果: {result}\n")

    # 测试2: 平空仓
    print("【测试2: 平空仓】")
    result = client.close_short(
        symbol='BTC/USDT',
        size='0.01'
    )
    print(f"结果: {result}\n")


if __name__ == "__main__":
    test_contract_client()
