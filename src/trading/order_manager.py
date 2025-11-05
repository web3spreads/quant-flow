"""
Hyperliquid 订单管理器
管理永续合约的订单创建、监控和执行，包括止盈止损逻辑
"""

from typing import Optional, Dict, Any, List
from src.trading.client import HyperliquidClient


class OrderManager:
    """Hyperliquid 订单管理器"""

    def __init__(
        self,
        client: HyperliquidClient,
        take_profit_ratio: float = 0.05,
        stop_loss_ratio: float = 0.02,
        default_leverage: int = 10
    ):
        """
        初始化订单管理器
        
        Args:
            client: Hyperliquid 客户端
            take_profit_ratio: 止盈比例（默认 5%）
            stop_loss_ratio: 止损比例（默认 2%）
            default_leverage: 默认杠杆倍数（默认 10倍）
        """
        self.client = client
        self.take_profit_ratio = take_profit_ratio
        self.stop_loss_ratio = stop_loss_ratio
        self.default_leverage = default_leverage
        
        print(f"✅ 订单管理器初始化完成")
        print(f"   止盈比例: {take_profit_ratio*100}%")
        print(f"   止损比例: {stop_loss_ratio*100}%")
        print(f"   默认杠杆: {default_leverage}x")

    def get_available_balance(self) -> float:
        """
        获取可用余额（USD）
        
        Returns:
            可用余额
        """
        balance = self.client.get_balance()
        if balance:
            return balance['totalRawUsd']
        return 0.0

    def check_sufficient_balance(self, required_amount: float) -> bool:
        """
        检查余额是否充足

        Args:
            required_amount: 所需金额

        Returns:
            是否有足够余额
        """
        try:
            available = self.get_available_balance()
            return available >= required_amount
        except Exception as e:
            print(f"❌ 检查余额失败: {e}")
            return False

    def get_available_balance_info(self) -> Dict[str, Any]:
        """
        获取详细的余额信息

        Returns:
            {
                'status': 'ok' | 'error',
                'total': float,  # 总价值
                'occupied': float,  # 已占用
                'available': float,  # 可用
                'message': str
            }
        """
        try:
            balance = self.client.get_balance()
            if not balance:
                return {
                    'status': 'error',
                    'message': '无法获取余额信息',
                    'total': 0,
                    'occupied': 0,
                    'available': 0
                }

            total = balance['accountValue']
            occupied = balance['totalMarginUsed']
            available = balance['totalRawUsd']

            return {
                'status': 'ok',
                'total': total,
                'occupied': occupied,
                'available': available,
                'message': f'总价值: ${total:.2f}, 可用: ${available:.2f}'
            }

        except Exception as e:
            return {
                'status': 'error',
                'message': f'获取余额失败: {e}',
                'total': 0,
                'occupied': 0,
                'available': 0
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
            symbol: 交易对符号（如 'ETH'）
            usdt_amount: 投入的 USDT 金额
            leverage: 杠杆倍数（None=使用默认）
            
        Returns:
            合约数量
        """
        try:
            current_price = self.client.get_current_price(symbol)
            if not current_price:
                return None
            
            lev = leverage if leverage else self.default_leverage
            
            # 合约数量 = (投入金额 * 杠杆) / 价格
            size = (usdt_amount * lev) / current_price
            
            # Hyperliquid 对数量精度有要求，保留合理的小数位
            # 一般保留到 0.001 的精度
            size = round(size, 3)
            
            return size
            
        except Exception as e:
            print(f"❌ 计算仓位大小失败: {e}")
            return None

    def execute_long(
        self,
        symbol: str,
        usdt_amount: float,
        leverage: Optional[int] = None,
        with_tpsl: bool = True
    ) -> Optional[Dict[str, Any]]:
        """
        执行做多操作（带止盈止损保护）
        
        Args:
            symbol: 交易对符号
            usdt_amount: 投入金额
            leverage: 杠杆倍数
            with_tpsl: 是否设置止盈止损
            
        Returns:
            订单信息
        """
        try:
            # 1. 获取当前价格
            current_price = self.client.get_current_price(symbol)
            if not current_price:
                return None
            
            # 2. 计算仓位大小
            size = self.calculate_position_size(symbol, usdt_amount, leverage)
            if not size:
                return None
            
            print(f"📈 做多 {symbol}: {size} 张合约 @ ${current_price:.2f}")

            # 3. 设置杠杆
            lev = leverage if leverage else self.default_leverage
            print(f"   设置杠杆: {lev}x (逐仓模式)")
            leverage_result = self.client.update_leverage(symbol, lev, is_cross=False)

            # 检查杠杆设置是否成功
            if leverage_result.get('status') == 'error':
                print(f"❌ 杠杆设置失败: {leverage_result.get('message')}")
                print(f"❌ 无法继续下单")
                return None

            # 4. 计算止盈止损价格
            if with_tpsl:
                tp_price = current_price * (1 + self.take_profit_ratio)
                sl_price = current_price * (1 - self.stop_loss_ratio)
                
                # 格式化价格，避免精度问题
                tp_price = self.client.format_price(symbol, tp_price)
                sl_price = self.client.format_price(symbol, sl_price)
                
                print(f"   止盈价: ${tp_price:.2f} (+{self.take_profit_ratio*100}%)")
                print(f"   止损价: ${sl_price:.2f} (-{self.stop_loss_ratio*100}%)")
                
                # 下带 TP/SL 的订单
                result = self.client.place_order_with_tpsl(
                    symbol=symbol,
                    is_buy=True,
                    size=size,
                    take_profit_price=tp_price,
                    stop_loss_price=sl_price
                )
            else:
                # 只下市价单
                market_order = self.client.place_market_order(
                    symbol=symbol,
                    is_buy=True,
                    size=size
                )
                result = {
                    'success': market_order.get('status') == 'ok',
                    'market_order': market_order,
                    'take_profit_order': None,
                    'stop_loss_order': None,
                    'errors': [] if market_order.get('status') == 'ok' else [market_order]
                }
            
            # 添加交易信息到返回结果
            if result:
                result['quantity'] = size
                result['price'] = current_price
                result['leverage'] = lev
                # 从 market_order 中提取 hash（支持多种格式）
                order_hash = ''
                if result.get('market_order') and isinstance(result['market_order'], dict):
                    response = result['market_order'].get('response', {})
                    data = response.get('data', {})
                    statuses = data.get('statuses', [])
                    if statuses and len(statuses) > 0:
                        status = statuses[0]
                        # 尝试多个可能的 hash 位置
                        if 'filled' in status and isinstance(status['filled'], dict):
                            order_hash = status['filled'].get('hash', '')
                        if not order_hash:
                            order_hash = status.get('hash', '')
                        if not order_hash:
                            order_hash = status.get('txHash', '')
                result['hash'] = order_hash
            
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
        执行做空操作（带止盈止损保护）
        
        Args:
            symbol: 交易对符号
            usdt_amount: 投入金额
            leverage: 杠杆倍数
            with_tpsl: 是否设置止盈止损
            
        Returns:
            订单信息
        """
        try:
            # 1. 获取当前价格
            current_price = self.client.get_current_price(symbol)
            if not current_price:
                return None
            
            # 2. 计算仓位大小
            size = self.calculate_position_size(symbol, usdt_amount, leverage)
            if not size:
                return None
            
            print(f"📉 做空 {symbol}: {size} 张合约 @ ${current_price:.2f}")

            # 3. 设置杠杆
            lev = leverage if leverage else self.default_leverage
            print(f"   设置杠杆: {lev}x (逐仓模式)")
            leverage_result = self.client.update_leverage(symbol, lev, is_cross=False)

            # 检查杠杆设置是否成功
            if leverage_result.get('status') == 'error':
                print(f"❌ 杠杆设置失败: {leverage_result.get('message')}")
                print(f"❌ 无法继续下单")
                return None

            # 4. 计算止盈止损价格（做空时方向相反）
            if with_tpsl:
                tp_price = current_price * (1 - self.take_profit_ratio)  # 下跌时止盈
                sl_price = current_price * (1 + self.stop_loss_ratio)    # 上涨时止损
                
                # 格式化价格，避免精度问题
                tp_price = self.client.format_price(symbol, tp_price)
                sl_price = self.client.format_price(symbol, sl_price)
                
                print(f"   止盈价: ${tp_price:.2f} (-{self.take_profit_ratio*100}%)")
                print(f"   止损价: ${sl_price:.2f} (+{self.stop_loss_ratio*100}%)")
                
                # 下带 TP/SL 的订单
                result = self.client.place_order_with_tpsl(
                    symbol=symbol,
                    is_buy=False,
                    size=size,
                    take_profit_price=tp_price,
                    stop_loss_price=sl_price
                )
            else:
                # 只下市价单
                market_order = self.client.place_market_order(
                    symbol=symbol,
                    is_buy=False,
                    size=size
                )
                result = {
                    'success': market_order.get('status') == 'ok',
                    'market_order': market_order,
                    'take_profit_order': None,
                    'stop_loss_order': None,
                    'errors': [] if market_order.get('status') == 'ok' else [market_order]
                }
            
            # 添加交易信息到返回结果
            if result:
                result['quantity'] = size
                result['price'] = current_price
                result['leverage'] = lev
                # 从 market_order 中提取 hash（支持多种格式）
                order_hash = ''
                if result.get('market_order') and isinstance(result['market_order'], dict):
                    response = result['market_order'].get('response', {})
                    data = response.get('data', {})
                    statuses = data.get('statuses', [])
                    if statuses and len(statuses) > 0:
                        status = statuses[0]
                        # 尝试多个可能的 hash 位置
                        if 'filled' in status and isinstance(status['filled'], dict):
                            order_hash = status['filled'].get('hash', '')
                        if not order_hash:
                            order_hash = status.get('hash', '')
                        if not order_hash:
                            order_hash = status.get('txHash', '')
                result['hash'] = order_hash
            
            return result
            
        except Exception as e:
            print(f"❌ 执行做空失败: {e}")
            return None

    def close_position(self, symbol: str, size: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """
        平仓操作
        
        Args:
            symbol: 交易对符号
            size: 平仓数量（None=全平）
            
        Returns:
            平仓结果
        """
        try:
            result = self.client.close_position(symbol, size)
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
            balance_info: 余额信息（如果已查询过可传入，避免重复查询）
            
        Returns:
            {
                'can_trade': bool,
                'suggested_amount': float,
                'reason': str
            }
        """
        try:
            # 获取余额信息
            if not balance_info:
                balance_info = self.get_available_balance_info()
            
            if balance_info['status'] != 'ok':
                return {
                    'can_trade': False,
                    'suggested_amount': 0,
                    'reason': balance_info['message']
                }
            
            available = balance_info['available']
            
            # 检查是否有足够余额
            if available < min_trade_amount:
                return {
                    'can_trade': False,
                    'suggested_amount': 0,
                    'reason': f'可用余额 ${available:.2f} 低于最小交易金额 ${min_trade_amount:.2f}'
                }
            
            # 如果期望金额小于等于可用余额，直接使用
            if desired_amount <= available:
                return {
                    'can_trade': True,
                    'suggested_amount': desired_amount,
                    'reason': f'使用配置的交易金额 ${desired_amount:.2f}'
                }
            else:
                # 使用可用余额的一部分（留一些余量）
                suggested = available * 0.8  # 使用 80% 的可用余额
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

    # ==================== 现货交易方法 ====================

    def buy_spot_for_dca(
        self,
        symbol: str,
        usdt_amount: float
    ) -> Optional[Dict[str, Any]]:
        """
        现货定投买入（用于长期持有策略）

        Args:
            symbol: 交易对符号
            usdt_amount: 投入金额

        Returns:
            订单信息
        """
        try:
            # 获取当前价格
            current_price = self.client.get_current_price(symbol)
            if not current_price:
                print(f"❌ 无法获取 {symbol} 价格")
                return None

            print(f"📦 现货定投 {symbol}")
            print(f"   投入金额: ${usdt_amount:.2f}")
            print(f"   当前价格: ${current_price:.2f}")

            # 调用客户端的现货买入方法
            result = self.client.buy_spot(
                symbol=symbol,
                usdt_amount=usdt_amount
            )

            if result.get('status') == 'ok':
                print(f"✅ 现货定投成功")
                return {
                    'success': True,
                    'spot_order': result,
                    'symbol': symbol,
                    'usdt_amount': usdt_amount,
                    'price': current_price
                }
            else:
                print(f"❌ 现货定投失败: {result.get('message')}")
                return None

        except Exception as e:
            print(f"❌ 现货定投异常: {e}")
            return None

    def sell_spot(
        self,
        symbol: str,
        size: Optional[float] = None
    ) -> Optional[Dict[str, Any]]:
        """
        卖出现货

        Args:
            symbol: 交易对符号
            size: 卖出数量（None=全部卖出）

        Returns:
            订单信息
        """
        try:
            result = self.client.sell_spot(symbol=symbol, size=size)

            if result.get('status') == 'ok':
                return {
                    'success': True,
                    'spot_order': result
                }
            else:
                return None

        except Exception as e:
            print(f"❌ 现货卖出异常: {e}")
            return None

    def get_spot_holdings(self) -> List[Dict[str, Any]]:
        """
        获取现货持仓列表

        Returns:
            现货持仓列表
        """
        try:
            return self.client.get_spot_balances()
        except Exception as e:
            print(f"❌ 获取现货持仓失败: {e}")
            return []
