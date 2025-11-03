"""
订单管理器
管理交易订单的创建、监控和执行，包括止盈止损逻辑
"""

from typing import Optional, Dict, Any
from src.trading.bitget_client import BitgetClient
from src.trading.bitget_contract_client import BitgetContractClient


class OrderManager:
    """订单管理器"""

    def __init__(
        self,
        client: BitgetClient,
        take_profit_ratio: float = 0.05,
        stop_loss_ratio: float = 0.02,
        contract_client: Optional[BitgetContractClient] = None,
        use_contract_for_short: bool = True,
        leverage: int = 10
    ):
        """
        初始化订单管理器

        Args:
            client: Bitget 现货客户端
            take_profit_ratio: 止盈比例（默认 5%）
            stop_loss_ratio: 止损比例（默认 2%）
            contract_client: Bitget 合约客户端（可选）
            use_contract_for_short: 是否使用合约进行做空（默认 True）
            leverage: 合约杠杆倍数（默认 10倍）
        """
        self.client = client
        self.contract_client = contract_client
        self.use_contract_for_short = use_contract_for_short
        self.leverage = leverage
        self.take_profit_ratio = take_profit_ratio
        self.stop_loss_ratio = stop_loss_ratio

        # 跟踪活跃订单
        self.active_orders: Dict[str, Dict[str, Any]] = {}

        # 跟踪模拟空头持仓（用于现货账户模拟做空，或合约持仓跟踪）
        # 结构: {symbol: {'amount': float, 'entry_price': float, 'side': 'short', 'is_contract': bool}}
        self.simulated_short_positions: Dict[str, Dict[str, Any]] = {}

        # 输出做空模式
        if use_contract_for_short:
            if contract_client:
                print(f"✅ 做空模式: 合约交易（{leverage}x 杠杆）")
            else:
                print(f"⚠️  做空模式设置为合约，但未提供合约客户端，将回退到现货模拟")
                self.use_contract_for_short = False
        else:
            print("✅ 做空模式: 现货模拟（买入+卖出）")

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
        # 使用向下取整，避免数量超过实际可购买数量
        import math
        amount = math.floor(amount * 100000000) / 100000000
        return amount

    def execute_buy_with_protection(
        self,
        symbol: str,
        usdt_amount: float,
        current_price: float
    ) -> Optional[Dict[str, Any]]:
        """
        执行带止盈止损保护的买入操作

        Args:
            symbol: 交易对
            usdt_amount: 买入金额（USDT）
            current_price: 当前价格

        Returns:
            包含所有订单信息的字典，失败返回 None
        """
        try:
            # 计算购买数量
            amount = self.calculate_amount_from_usdt(symbol, usdt_amount, current_price)

            # 计算止盈和止损价格
            take_profit_price = current_price * (1 + self.take_profit_ratio)
            stop_loss_price = current_price * (1 - self.stop_loss_ratio)

            print(f"\n{'='*60}")
            print(f"  交易对: {symbol}")
            print(f"  买入金额: {usdt_amount} USDT")
            print(f"  当前价格: {current_price:.2f}")
            print(f"  买入数量: {amount:.6f}")
            print(f"  止盈价格: {take_profit_price:.2f} (+{self.take_profit_ratio*100}%)")
            print(f"  止损价格: {stop_loss_price:.2f} (-{self.stop_loss_ratio*100}%)")
            print(f"{'='*60}\n")

            # 使用统一接口创建带止盈止损的订单
            # 注意：买入时传入 usdt_amount（USDT金额），卖出时传入 amount（币数量）
            result = self.client.create_order_with_tpsl(
                symbol=symbol,
                side='buy',
                amount=amount,  # 用于止盈止损单的数量
                take_profit_price=take_profit_price,
                stop_loss_price=stop_loss_price,
                usdt_amount=usdt_amount  # 市价买入使用 USDT 金额
            )

            if not result.get('success'):
                print("❌ 订单创建失败")
                if result.get('errors'):
                    for error in result['errors']:
                        print(f"  错误: {error}")
                return None

            # 记录订单信息
            order_info = {
                'symbol': symbol,
                'buy_order': result.get('market_order'),
                'take_profit_order': result.get('take_profit_order'),
                'stop_loss_order': result.get('stop_loss_order'),
                'amount': amount,
                'entry_price': current_price,
                'take_profit_price': take_profit_price,
                'stop_loss_price': stop_loss_price,
                'usdt_amount': usdt_amount,
            }

            # 保存到活跃订单
            if order_info['buy_order']:
                order_id = order_info['buy_order'].get('id') or order_info['buy_order'].get('orderId')
                if order_id:
                    self.active_orders[order_id] = order_info

            return order_info

        except Exception as e:
            print(f"❌ 执行买入操作时出错: {e}")
            return None

    def execute_sell(
        self,
        symbol: str,
        amount: float
    ) -> Optional[Dict[str, Any]]:
        """
        执行卖出操作（平仓）

        Args:
            symbol: 交易对
            amount: 卖出数量

        Returns:
            订单信息，失败返回 None
        """
        try:
            print(f"\n{'='*60}")
            print(f"执行卖出操作:")
            print(f"  交易对: {symbol}")
            print(f"  卖出数量: {amount:.6f}")
            print(f"{'='*60}\n")

            # 创建市价卖单
            sell_order = self.client.create_market_sell_order(symbol, amount)
            if not sell_order:
                print("❌ 卖出订单创建失败")
                return None

            print(f"✅ 卖出订单已创建: {sell_order['orderId']}")

            # TODO: 取消相关的止盈止损单
            # 这需要跟踪之前创建的订单

            return sell_order

        except Exception as e:
            print(f"❌ 执行卖出操作时出错: {e}")
            return None

    def execute_sell_short_with_protection(
        self,
        symbol: str,
        usdt_amount: float,
        current_price: float
    ) -> Optional[Dict[str, Any]]:
        """
        执行带止盈止损保护的做空操作（开空仓）

        现货模式：先买入币，然后立即卖出，模拟做空效果
        合约模式：直接创建空头订单

        Args:
            symbol: 交易对
            usdt_amount: 做空金额（USDT）
            current_price: 当前价格

        Returns:
            包含所有订单信息的字典，失败返回 None
        """
        try:
            # 计算做空数量
            amount = self.calculate_amount_from_usdt(symbol, usdt_amount, current_price)

            # 计算止盈和止损价格（做空时方向相反）
            take_profit_price = current_price * (1 - self.take_profit_ratio)  # 价格下跌止盈
            stop_loss_price = current_price * (1 + self.stop_loss_ratio)      # 价格上涨止损

            # 使用合约做空
            if self.use_contract_for_short and self.contract_client:
                print(f"\n{'='*60}")
                print(f"执行做空操作 (合约模式 - {self.leverage}x 杠杆):")
                print(f"  交易对: {symbol}")
                print(f"  做空金额: {usdt_amount} USDT")
                print(f"  当前价格: {current_price:.2f}")
                print(f"  做空数量: {amount:.6f}")
                print(f"  杠杆倍数: {self.leverage}x")
                print(f"  止盈价格: {take_profit_price:.2f} (-{self.take_profit_ratio*100}%)")
                print(f"  止损价格: {stop_loss_price:.2f} (+{self.stop_loss_ratio*100}%)")
                print(f"{'='*60}\n")

                # 开空仓
                order = self.contract_client.open_short(
                    symbol=symbol,
                    size=str(amount),
                    leverage=self.leverage,
                    take_profit_price=str(take_profit_price),
                    stop_loss_price=str(stop_loss_price)
                )

                if not order:
                    print("❌ 合约开空失败")
                    return None

                # 记录合约空头持仓
                order_info = {
                    'symbol': symbol,
                    'side': 'short',
                    'amount': amount,
                    'entry_price': current_price,
                    'take_profit_price': take_profit_price,
                    'stop_loss_price': stop_loss_price,
                    'usdt_amount': usdt_amount,
                    'leverage': self.leverage,
                    'contract_order': order,
                    'is_contract': True
                }

                self.simulated_short_positions[symbol] = order_info
                print(f"✅ 合约空头持仓已创建: {symbol}")
                return order_info

            # 使用现货模拟做空
            else:
                print(f"\n{'='*60}")
                print(f"执行做空操作 (现货模拟 - 买入后卖出):")
                print(f"  交易对: {symbol}")
                print(f"  做空金额: {usdt_amount} USDT")
                print(f"  当前价格: {current_price:.2f}")
                print(f"  做空数量: {amount:.6f}")
                print(f"  止盈价格: {take_profit_price:.2f} (-{self.take_profit_ratio*100}%)")
                print(f"  止损价格: {stop_loss_price:.2f} (+{self.stop_loss_ratio*100}%)")
                print(f"{'='*60}\n")

                # 步骤1: 先买入币
                print("步骤1: 买入币...")
                buy_order = self.client.create_market_buy_order(symbol, usdt_amount)
                if not buy_order:
                    print("❌ 买入失败，无法开空")
                    return None

                print(f"✅ 买入成功: {buy_order.get('orderId')}")

                # 步骤2: 立即卖出（开空）
                print(f"步骤2: 卖出 {amount:.6f} 币（开空）...")
                sell_order = self.client.create_market_sell_order(symbol, amount)
                if not sell_order:
                    print("❌ 卖出失败")
                    return None

                print(f"✅ 卖出成功: {sell_order.get('orderId', sell_order.get('id'))}")

                # 记录空头持仓信息
                order_info = {
                    'symbol': symbol,
                    'side': 'short',
                    'amount': amount,
                    'entry_price': current_price,
                    'take_profit_price': take_profit_price,
                    'stop_loss_price': stop_loss_price,
                    'usdt_amount': usdt_amount,
                    'buy_order': buy_order,
                    'sell_order': sell_order,
                    'is_contract': False
                }

                self.simulated_short_positions[symbol] = order_info
                print(f"✅ 现货模拟空头持仓已创建: {symbol}")
                return order_info

        except Exception as e:
            print(f"❌ 执行做空操作时出错: {e}")
            import traceback
            traceback.print_exc()
            return None

    def execute_buy_to_cover(
        self,
        symbol: str,
        amount: float,
        current_price: Optional[float] = None
    ) -> Optional[Dict[str, Any]]:
        """
        执行买入平仓操作（平空仓）

        现货模式：买入相同数量的币来平仓
        合约模式：直接创建平仓订单

        Args:
            symbol: 交易对
            amount: 平仓数量（币数量）
            current_price: 当前价格（可选，用于计算USDT金额）

        Returns:
            订单信息，失败返回 None
        """
        try:
            # 检查是否有空头持仓
            if symbol not in self.simulated_short_positions:
                print(f"❌ 未找到 {symbol} 的空头持仓")
                return None

            position = self.simulated_short_positions[symbol]

            # 如果没有提供当前价格，使用开仓价格估算
            if current_price is None:
                current_price = position['entry_price']
                print(f"⚠️ 未提供当前价格，使用开仓价格: {current_price:.2f}")

            # 判断是合约持仓还是现货模拟持仓
            is_contract = position.get('is_contract', False)

            if is_contract and self.contract_client:
                # 合约平仓
                print(f"\n{'='*60}")
                print(f"执行平空仓操作 (合约模式):")
                print(f"  交易对: {symbol}")
                print(f"  平仓数量: {amount:.6f}")
                print(f"  开仓价格: {position['entry_price']:.2f}")
                print(f"  当前价格: {current_price:.2f}")
                print(f"{'='*60}\n")

                # 平空仓
                close_order = self.contract_client.close_short(symbol, str(amount))
                if not close_order:
                    print("❌ 合约平空失败")
                    return None

                # 移除持仓记录
                self.simulated_short_positions.pop(symbol)

                cover_order = {
                    'orderId': close_order.get('orderId'),
                    'symbol': symbol,
                    'side': 'buy_to_cover',
                    'amount': amount,
                    'entry_price': position['entry_price'],
                    'cover_price': current_price,
                    'close_order': close_order,
                    'is_contract': True
                }

                print(f"✅ 合约空头持仓已平仓: {symbol}")
                return cover_order

            else:
                # 现货模拟平仓
                print(f"\n{'='*60}")
                print(f"执行平空仓操作 (现货模拟 - 买入平仓):")
                print(f"  交易对: {symbol}")
                print(f"  平仓数量: {amount:.6f}")
                print(f"{'='*60}\n")

                # 计算需要的USDT金额
                usdt_amount = amount * current_price
                print(f"买入 {amount:.6f} 币，预计需要 {usdt_amount:.2f} USDT")

                # 执行买入（平空）
                buy_order = self.client.create_market_buy_order(symbol, usdt_amount)
                if not buy_order:
                    print("❌ 买入失败，无法平空")
                    return None

                print(f"✅ 买入成功: {buy_order.get('orderId', buy_order.get('id'))}")

                # 移除空头持仓记录
                self.simulated_short_positions.pop(symbol)

                # 创建平仓记录
                cover_order = {
                    'orderId': buy_order.get('orderId', buy_order.get('id')),
                    'symbol': symbol,
                    'side': 'buy_to_cover',
                    'amount': amount,
                    'entry_price': position['entry_price'],
                    'cover_price': current_price,
                    'buy_order': buy_order,
                    'is_contract': False
                }

                print(f"✅ 现货模拟空头持仓已平仓: {symbol}")
                return cover_order

        except Exception as e:
            print(f"❌ 执行平仓操作时出错: {e}")
            import traceback
            traceback.print_exc()
            return None

    def get_current_positions(self) -> list:
        """
        获取当前持仓（包含多头和空头）

        Returns:
            持仓列表，每项包含 symbol, amount, side ('long' 或 'short')
        """
        # 获取多头持仓（实际持仓）
        long_positions = self.client.get_positions()

        # 为多头持仓添加方向标记
        for pos in long_positions:
            pos['side'] = 'long'

        # 添加模拟空头持仓
        short_positions = []
        for symbol, pos_info in self.simulated_short_positions.items():
            short_positions.append({
                'symbol': symbol,
                'amount': pos_info['amount'],
                'side': 'short',
                'entry_price': pos_info['entry_price'],
                'simulated': True
            })

        return long_positions + short_positions

    def cancel_all_orders(self, symbol: str) -> bool:
        """
        取消指定交易对的所有订单

        Args:
            symbol: 交易对

        Returns:
            是否成功
        """
        try:
            open_orders = self.client.get_open_orders(symbol)
            for order in open_orders:
                self.client.cancel_order(order['id'], symbol)
                print(f"已取消订单: {order['id']}")
            return True
        except Exception as e:
            print(f"取消订单时出错: {e}")
            return False

    def get_balance(self, currency: str = 'USDT') -> Optional[float]:
        """
        获取余额

        Args:
            currency: 货币类型

        Returns:
            余额
        """
        return self.client.get_balance(currency)

    def check_sufficient_balance(
        self,
        required_amount: float,
        currency: str = 'USDT'
    ) -> bool:
        """
        检查余额是否足够

        Args:
            required_amount: 需要的金额
            currency: 货币类型

        Returns:
            是否足够
        """
        balance = self.get_balance(currency)
        if balance is None:
            return False
        return balance >= required_amount

    def get_available_balance_info(
        self,
        currency: str = 'USDT',
        current_positions: Optional[list] = None
    ) -> Dict[str, Any]:
        """
        获取可用余额详细信息

        Args:
            currency: 货币类型
            current_positions: 可选的当前持仓列表（如果提供，则不再查询API）

        Returns:
            包含余额信息的字典
        """
        balance = self.get_balance(currency)
        if balance is None:
            return {
                'available': 0.0,
                'status': 'error',
                'message': '无法获取余额信息'
            }

        # 计算已占用资金（持仓 + 模拟空头）
        occupied = 0.0

        # 如果没有提供持仓信息，则查询
        if current_positions is None:
            current_positions = self.get_current_positions()

        # 遍历所有持仓（包含多头和空头）
        for pos in current_positions:
            side = pos.get('side', 'long')

            if side == 'long':
                # 多头持仓占用的资金
                occupied += pos.get('usdt_value', 0)
            elif side == 'short':
                # 空头持仓占用的资金（模拟）
                # 从 simulated_short_positions 中获取
                symbol = pos['symbol']
                if symbol in self.simulated_short_positions:
                    occupied += self.simulated_short_positions[symbol].get('usdt_amount', 0)

        return {
            'total': balance,
            'occupied': occupied,
            'available': balance - occupied,
            'status': 'ok',
            'message': f'可用余额: {balance - occupied:.2f} {currency}'
        }

    def calculate_suggested_trade_amount(
        self,
        desired_amount: float,
        currency: str = 'USDT',
        min_trade_amount: float = 10.0,
        reserve_ratio: float = 0.1,
        balance_info: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        根据可用余额计算建议的交易金额

        Args:
            desired_amount: 期望的交易金额
            currency: 货币类型
            min_trade_amount: 最小交易金额
            reserve_ratio: 保留比例（保留一部分资金不使用）
            balance_info: 可选的余额信息（如果提供，则不再查询API）

        Returns:
            包含建议金额和状态的字典
        """
        # 如果没有提供余额信息，则查询
        if balance_info is None:
            balance_info = self.get_available_balance_info(currency)

        if balance_info['status'] != 'ok':
            return {
                'suggested_amount': 0.0,
                'can_trade': False,
                'reason': balance_info['message']
            }

        available = balance_info['available']
        # 保留一定比例的资金作为缓冲
        usable = available * (1 - reserve_ratio)

        # 如果可用资金少于最小交易金额，无法交易
        if usable < min_trade_amount:
            return {
                'suggested_amount': 0.0,
                'can_trade': False,
                'reason': f'可用余额 {usable:.2f} {currency} 低于最小交易金额 {min_trade_amount} {currency}'
            }

        # 如果可用资金充足，使用期望金额
        if usable >= desired_amount:
            return {
                'suggested_amount': desired_amount,
                'can_trade': True,
                'reason': f'余额充足，使用配置金额 {desired_amount} {currency}'
            }

        # 如果可用资金不足但大于最小交易金额，使用可用资金
        return {
            'suggested_amount': usable,
            'can_trade': True,
            'reason': f'余额不足，调整为可用金额 {usable:.2f} {currency}'
        }


def test_order_manager():
    """测试订单管理器（测试模式）"""
    print("=== 测试订单管理器（模拟盘）===\n")

    # 创建客户端（模拟盘）
    client = BitgetClient(
        api_key="test_key",
        api_secret="test_secret",
        passphrase="test_passphrase",
        demo_trading=True  # 使用模拟盘
    )

    # 创建订单管理器
    manager = OrderManager(
        client=client,
        take_profit_ratio=0.05,  # 5% 止盈
        stop_loss_ratio=0.02     # 2% 止损
    )

    # 测试买入操作
    print("1. 测试带保护的买入操作...")
    order_info = manager.execute_buy_with_protection(
        symbol='BTC/USDT',
        usdt_amount=100,
        current_price=60000
    )

    if order_info:
        print("\n订单信息:")
        print(f"  买入订单ID: {order_info['buy_order']['id']}")
        print(f"  入场价格: {order_info['entry_price']}")
        print(f"  止盈价格: {order_info['take_profit_price']}")
        print(f"  止损价格: {order_info['stop_loss_price']}")

    # 测试卖出操作
    print("\n2. 测试卖出操作...")
    if order_info:
        sell_order = manager.execute_sell(
            symbol='BTC/USDT',
            amount=order_info['amount']
        )
        if sell_order:
            print(f"  卖出订单ID: {sell_order['id']}")

    # 测试余额检查
    print("\n3. 测试余额检查...")
    has_balance = manager.check_sufficient_balance(100, 'USDT')
    print(f"  是否有足够余额: {has_balance}")


if __name__ == "__main__":
    test_order_manager()
