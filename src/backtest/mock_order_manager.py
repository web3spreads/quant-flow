"""
模拟订单管理器
用于回测，提供与真实OrderManager相同的接口
"""

from typing import Optional, Dict, Any, List
from .mock_client import MockHyperliquidClient


class MockOrderManager:
    """模拟订单管理器"""

    def __init__(
        self,
        client: MockHyperliquidClient,
        take_profit_ratio: float = 0.05,
        stop_loss_ratio: float = 0.02,
        default_leverage: int = 10
    ):
        """
        初始化模拟订单管理器
        
        Args:
            client: 模拟客户端
            take_profit_ratio: 止盈比例
            stop_loss_ratio: 止损比例
            default_leverage: 默认杠杆倍数
        """
        self.client = client
        self.take_profit_ratio = take_profit_ratio
        self.stop_loss_ratio = stop_loss_ratio
        self.default_leverage = default_leverage

    def get_available_balance(self) -> float:
        """
        获取可用余额
        
        Returns:
            可用余额
        """
        balance = self.client.get_balance()
        if balance:
            return balance['accountValue'] - balance['totalMarginUsed']
        return 0.0

    def check_sufficient_balance(self, required_amount: float) -> bool:
        """
        检查余额是否充足
        
        Args:
            required_amount: 所需金额
            
        Returns:
            是否有足够余额
        """
        available = self.get_available_balance()
        return available >= required_amount

    def get_available_balance_info(self) -> Dict[str, Any]:
        """
        获取详细的余额信息
        
        Returns:
            余额信息字典
        """
        balance = self.client.get_balance()
        if not balance:
            return {
                'status': 'error',
                'message': '无法获取余额信息',
                'total': 0,
                'occupied': 0,
                'available': 0,
                'unrealized_pnl': 0
            }

        total = balance['accountValue']
        occupied = balance['totalMarginUsed']
        available = total - occupied

        # 计算未实现盈亏
        unrealized_pnl = 0
        positions = self.client.get_positions()
        for position in positions:
            unrealized_pnl += float(position.get('unrealizedPnl', 0))

        return {
            'status': 'ok',
            'total': total,
            'occupied': occupied,
            'available': available,
            'unrealized_pnl': unrealized_pnl,
            'message': f'总价值: ${total:.2f}, 可用: ${available:.2f}, 未实现盈亏: ${unrealized_pnl:+.2f}'
        }

    def get_current_positions(self) -> List[Dict[str, Any]]:
        """
        获取当前持仓列表
        
        Returns:
            持仓列表
        """
        return self.client.get_positions()

    def calculate_position_size(
        self,
        symbol: str,
        usdt_amount: float,
        leverage: Optional[int] = None
    ) -> Optional[float]:
        """
        根据 USDT 金额计算合约数量
        
        Args:
            symbol: 交易对符号
            usdt_amount: 投入的 USDT 金额
            leverage: 杠杆倍数
            
        Returns:
            合约数量
        """
        current_price = self.client.get_current_price(symbol)
        if not current_price:
            return None

        lev = leverage if leverage else self.default_leverage

        # 合约数量 = (投入金额 * 杠杆) / 价格
        size = (usdt_amount * lev) / current_price

        # 获取交易对的精度信息
        asset_info = self.client.get_asset_info(symbol)
        if asset_info and 'szDecimals' in asset_info:
            decimals = asset_info['szDecimals']
            size = round(size, decimals)
        else:
            size = round(size, 3)

        return size

    def execute_long(
        self,
        symbol: str,
        usdt_amount: float,
        leverage: Optional[int] = None,
        with_tpsl: bool = True
    ) -> Optional[Dict[str, Any]]:
        """
        执行做多操作（模拟）
        
        Args:
            symbol: 交易对符号
            usdt_amount: 投入金额
            leverage: 杠杆倍数
            with_tpsl: 是否设置止盈止损
            
        Returns:
            订单信息
        """
        try:
            current_price = self.client.get_current_price(symbol)
            if not current_price:
                return None

            size = self.calculate_position_size(symbol, usdt_amount, leverage)
            if not size:
                return None

            lev = leverage if leverage else self.default_leverage

            # 计算止盈止损价格
            tp_price = None
            sl_price = None
            if with_tpsl:
                tp_price = current_price * (1 + self.take_profit_ratio)
                sl_price = current_price * (1 - self.stop_loss_ratio)
                tp_price = self.client.format_price(symbol, tp_price)
                sl_price = self.client.format_price(symbol, sl_price)

            # 下订单（模拟）
            result = self.client.place_order_with_tpsl(
                symbol=symbol,
                is_buy=True,
                size=size,
                take_profit_price=tp_price if tp_price else 0,
                stop_loss_price=sl_price if sl_price else 0
            )

            # 添加交易信息并创建持仓
            if result and result.get('success'):
                result['quantity'] = size
                result['price'] = current_price
                result['leverage'] = lev
                result['hash'] = f'mock_{len(self.client.trade_history)}'
                
                # 在模拟客户端中添加持仓
                self.client.add_position(
                    symbol=symbol,
                    size=size,
                    entry_price=current_price,
                    leverage=lev,
                    is_long=True,
                    take_profit_price=tp_price,
                    stop_loss_price=sl_price
                )
                
                # 更新已用保证金
                margin_used = current_price * size / lev
                self.client.update_account_value(
                    self.client.account_value,
                    self.client.total_margin_used + margin_used
                )

            return result

        except Exception as e:
            print(f"❌ 执行做多失败: {e}")
            return None

    def execute_short(
        self,
        symbol: str,
        usdt_amount: float,
        leverage: Optional[int] = None,
        with_tpsl: bool = True
    ) -> Optional[Dict[str, Any]]:
        """
        执行做空操作（模拟）
        
        Args:
            symbol: 交易对符号
            usdt_amount: 投入金额
            leverage: 杠杆倍数
            with_tpsl: 是否设置止盈止损
            
        Returns:
            订单信息
        """
        try:
            current_price = self.client.get_current_price(symbol)
            if not current_price:
                return None

            size = self.calculate_position_size(symbol, usdt_amount, leverage)
            if not size:
                return None

            lev = leverage if leverage else self.default_leverage

            # 计算止盈止损价格（做空时方向相反）
            tp_price = None
            sl_price = None
            if with_tpsl:
                tp_price = current_price * abs(1 - self.take_profit_ratio)  # 下跌时止盈
                sl_price = current_price * (1 + self.stop_loss_ratio)  # 上涨时止损
                tp_price = self.client.format_price(symbol, tp_price)
                sl_price = self.client.format_price(symbol, sl_price)

            # 下订单（模拟）
            result = self.client.place_order_with_tpsl(
                symbol=symbol,
                is_buy=False,
                size=size,
                take_profit_price=tp_price if tp_price else 0,
                stop_loss_price=sl_price if sl_price else 0
            )

            # 添加交易信息并创建持仓
            if result and result.get('success'):
                result['quantity'] = size
                result['price'] = current_price
                result['leverage'] = lev
                result['hash'] = f'mock_{len(self.client.trade_history)}'
                
                # 在模拟客户端中添加持仓
                self.client.add_position(
                    symbol=symbol,
                    size=size,
                    entry_price=current_price,
                    leverage=lev,
                    is_long=False,
                    take_profit_price=tp_price,
                    stop_loss_price=sl_price
                )
                
                # 更新已用保证金
                margin_used = current_price * size / lev
                self.client.update_account_value(
                    self.client.account_value,
                    self.client.total_margin_used + margin_used
                )

            return result

        except Exception as e:
            print(f"❌ 执行做空失败: {e}")
            return None

    def close_position(self, symbol: str, size: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """
        平仓操作（模拟）
        
        Args:
            symbol: 交易对符号
            size: 平仓数量（None=全平）
            
        Returns:
            平仓结果
        """
        try:
            # 获取当前持仓
            position = next((p for p in self.client.get_positions() if p.get('coin') == symbol), None)
            if not position:
                return {'status': 'error', 'message': f'没有 {symbol} 的持仓'}
            
            # 获取当前价格
            current_price = self.client.get_current_price(symbol)
            if not current_price:
                return {'status': 'error', 'message': '无法获取当前价格'}
            
            # 移除持仓（交易记录由BacktestEngine处理）
            self.client.remove_position(symbol)
            
            # 返回成功结果
            result = {
                'status': 'ok',
                'message': '平仓成功（模拟）',
                'symbol': symbol,
                'size': size,
                'price': current_price,
                'hash': f'mock_{len(self.client.trade_history)}'
            }
            
            return result
        except Exception as e:
            print(f"❌ 平仓失败: {e}")
            return None

    def calculate_suggested_trade_amount(
        self,
        desired_amount: float,
        min_trade_amount: float = 10.0,
        balance_info: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        计算建议的交易金额
        
        Args:
            desired_amount: 期望的交易金额
            min_trade_amount: 最小交易金额
            balance_info: 余额信息
            
        Returns:
            建议信息字典
        """
        try:
            if not balance_info:
                balance_info = self.get_available_balance_info()

            if balance_info['status'] != 'ok':
                return {
                    'can_trade': False,
                    'suggested_amount': 0,
                    'reason': balance_info['message']
                }

            available = balance_info['available']

            if available < min_trade_amount:
                return {
                    'can_trade': False,
                    'suggested_amount': 0,
                    'reason': f'可用余额 ${available:.2f} 低于最小交易金额 ${min_trade_amount:.2f}'
                }

            if desired_amount <= available:
                return {
                    'can_trade': True,
                    'suggested_amount': desired_amount,
                    'reason': f'使用配置的交易金额 ${desired_amount:.2f}'
                }
            else:
                suggested = available * 0.8
                if suggested >= min_trade_amount:
                    return {
                        'can_trade': True,
                        'suggested_amount': suggested,
                        'reason': f'可用余额不足，调整为 ${suggested:.2f} (可用余额的 80%)'
                    }
                else:
                    return {
                        'can_trade': False,
                        'suggested_amount': 0,
                        'reason': f'可用余额不足，无法交易'
                    }

        except Exception as e:
            return {
                'can_trade': False,
                'suggested_amount': 0,
                'reason': f'计算建议金额失败: {e}'
            }

    def get_spot_holdings(self) -> List[Dict[str, Any]]:
        """
        获取现货持仓列表（回测中暂不支持）
        
        Returns:
            空列表
        """
        return []

    def buy_spot_for_dca(
        self,
        symbol: str,
        usdt_amount: float
    ) -> Optional[Dict[str, Any]]:
        """
        现货定投买入（回测中暂不支持）
        
        Args:
            symbol: 交易对符号
            usdt_amount: 投入金额
            
        Returns:
            None
        """
        return None

    def sell_spot(
        self,
        symbol: str,
        size: Optional[float] = None
    ) -> Optional[Dict[str, Any]]:
        """
        卖出现货（回测中暂不支持）
        
        Args:
            symbol: 交易对符号
            size: 卖出数量
            
        Returns:
            None
        """
        return None

