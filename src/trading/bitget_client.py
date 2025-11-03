"""
Bitget 交易客户端 - 基于官方 SDK 的统一接口
专注于 Bitget 平台，使用官方 Python SDK
"""
from typing import Optional, Dict, Any
from src.trading.bitget_official_client import BitgetOfficialClient


class BitgetClient:
    """
    Bitget 交易客户端 - 官方 SDK 包装器

    提供统一的交易接口，内部使用 Bitget 官方 Python SDK
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        passphrase: str,
        demo_trading: bool = False
    ):
        """
        初始化 Bitget 客户端

        Args:
            api_key: API Key
            api_secret: API Secret
            passphrase: API Passphrase
            demo_trading: 是否使用 Bitget 模拟盘（True=模拟盘，False=实盘）
        """
        self.api_key = api_key
        self.demo_trading = demo_trading

        mode_info = "模拟盘" if demo_trading else "实盘"
        print(f"📌 使用 Bitget 官方 SDK（{mode_info}）")

        # 直接使用官方 SDK 客户端
        self._client = BitgetOfficialClient(api_key, api_secret, passphrase, demo_trading)

    def get_balance(self, currency: str = 'USDT') -> Optional[float]:
        """获取账户余额"""
        return self._client.get_balance(currency)

    def place_market_buy(
        self,
        symbol: str,
        amount: float
    ) -> Optional[Dict[str, Any]]:
        """创建市价买单"""
        # 确保数量精度不超过 8 位小数（Bitget API 限制）
        # 买入时传入的是 USDT 金额，使用向下取整避免超出余额
        import math
        amount_str = str(math.floor(amount * 100000000) / 100000000)
        return self._client.place_market_order(symbol, 'buy', amount_str)

    def place_market_sell(
        self,
        symbol: str,
        amount: float
    ) -> Optional[Dict[str, Any]]:
        """创建市价卖单"""
        # 确保数量精度不超过 6 位小数（Bitget API 限制），直接截断
        import math
        amount_str = str(math.floor(amount * 1000000) / 1000000)
        return self._client.place_market_order(symbol, 'sell', amount_str)

    def place_plan_order(
        self,
        symbol: str,
        side: str,
        amount: str,
        trigger_price: str,
        plan_type: str = 'amount'
    ) -> Optional[Dict[str, Any]]:
        """创建计划单（止盈止损）"""
        return self._client.place_plan_order(
            symbol=symbol,
            side=side,
            amount=amount,
            trigger_price=trigger_price,
            plan_type=plan_type
        )

    def place_order_with_tpsl(
        self,
        symbol: str,
        side: str,
        amount: Optional[float] = None,
        usdt_amount: Optional[float] = None,
        take_profit_price: Optional[float] = None,
        stop_loss_price: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        创建带止盈止损的市价单（一次API调用）

        使用 Bitget 的 presetTakeProfitPrice 和 presetStopLossPrice 参数
        """
        return self._client.place_order_with_tpsl(
            symbol=symbol,
            side=side,
            amount=amount,
            usdt_amount=usdt_amount,
            take_profit_price=take_profit_price,
            stop_loss_price=stop_loss_price
        )

    def create_order_with_tpsl(
        self,
        symbol: str,
        side: str,
        amount: Optional[float] = None,
        usdt_amount: Optional[float] = None,
        take_profit_price: Optional[float] = None,
        stop_loss_price: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        创建带止盈止损的市价单（别名方法，用于向后兼容）

        这是 place_order_with_tpsl 的别名方法
        """
        return self.place_order_with_tpsl(
            symbol=symbol,
            side=side,
            amount=amount,
            usdt_amount=usdt_amount,
            take_profit_price=take_profit_price,
            stop_loss_price=stop_loss_price
        )

    def create_market_buy_order(self, symbol: str, usdt_amount: float) -> Optional[Dict[str, Any]]:
        """创建市价买单（别名方法，用于向后兼容）"""
        return self.place_market_buy(symbol, usdt_amount)

    def create_market_sell_order(self, symbol: str, amount: float) -> Optional[Dict[str, Any]]:
        """创建市价卖单（别名方法，用于向后兼容）"""
        return self.place_market_sell(symbol, amount)

    def cancel_order(self, order_id: str, symbol: str) -> bool:
        """取消订单"""
        return self._client.cancel_order(order_id, symbol)

    def cancel_plan_order(self, order_id: str, symbol: str) -> bool:
        """取消计划单"""
        return self._client.cancel_plan_order(order_id, symbol)

    def get_order(self, order_id: str, symbol: str) -> Optional[Dict[str, Any]]:
        """查询订单状态"""
        # 官方 SDK 可以通过 historyOrders 查询
        return {'id': order_id, 'symbol': symbol}  # 简化实现

    def get_open_orders(self, symbol: Optional[str] = None) -> list:
        """获取未完成的订单"""
        return self._client.get_open_orders(symbol)

    def get_positions(self, symbol: Optional[str] = None) -> list:
        """获取持仓信息（现货账户余额）"""
        try:
            # 使用官方 SDK 查询余额
            response = self._client.account_api.assets({})
            if response['code'] == '00000':
                positions = []
                assets = response['data']

                for asset in assets:
                    available = float(asset.get('available', 0))
                    frozen = float(asset.get('frozen', 0))
                    total = available + frozen

                    # 跳过零余额和 USDT（USDT 不作为持仓，而是计价货币）
                    coin = asset['coin']
                    if total > 0 and coin != 'USDT':
                        # 转换为交易对格式（如 BTC -> BTC/USDT）
                        trading_pair = f"{coin}/USDT"
                        
                        # 如果指定了 symbol，只返回匹配的
                        if symbol and trading_pair != symbol:
                            continue
                        
                        positions.append({
                            'symbol': trading_pair,  # 使用交易对格式
                            'coin': coin,            # 保留币种信息
                            'amount': total,
                            'available': available,
                            'frozen': frozen
                        })

                return positions

            return []

        except Exception as e:
            print(f"获取持仓失败: {e}")
            return []


def test_bitget_client():
    """测试 Bitget 客户端（模拟盘）"""
    print("=== 测试 Bitget 客户端 ===\n")

    client = BitgetClient(
        api_key="test_key",
        api_secret="test_secret",
        passphrase="test_passphrase",
        demo_trading=True
    )

    # 测试获取余额
    print("1. 测试获取余额...")
    balance = client.get_balance('USDT')
    print(f"USDT 余额: {balance}\n")

    # 测试市价买单
    print("2. 测试创建市价买单...")
    order = client.place_market_buy('BTC/USDT', 100)
    print(f"订单结果: {order}\n")


if __name__ == "__main__":
    test_bitget_client()
