"""
Hyperliquid 订单管理器 (网格版)
支持自定义止盈止损比例，并彻底解决测试网授权与限价单逻辑
"""

import time
from typing import Optional, Dict, Any, List

class OrderManager:
    """Hyperliquid 订单管理器"""

    def __init__(
        self,
        client: Any,
        take_profit_ratio: float = 0.05,
        stop_loss_ratio: float = 0.02,
        default_leverage: int = 10
    ):
        self.client = client
        self.take_profit_ratio = take_profit_ratio
        self.stop_loss_ratio = stop_loss_ratio
        self.default_leverage = default_leverage
        print(f"✅ 订单管理器初始化完成 (网格版)")

    def get_available_balance_info(self) -> Dict[str, Any]:
        try:
            balance = self.client.get_balance()
            if not balance:
                return {'status': 'error', 'message': '无法获取余额', 'total': 0, 'occupied': 0, 'available': 0}
            return {'status': 'ok', 'total': balance['accountValue'], 'occupied': balance['totalMarginUsed'], 'available': balance['available']}
        except Exception as e:
            return {'status': 'error', 'message': str(e), 'total': 0, 'occupied': 0, 'available': 0}

    def get_current_positions(self) -> List[Dict[str, Any]]:
        return self.client.get_positions()

    def execute_long_limit(
        self,
        symbol: str,
        usdt_amount: float,
        limit_price: float,
        leverage: Optional[int] = None,
        tp_ratio: Optional[float] = None,
        sl_ratio: Optional[float] = None
    ) -> Optional[Dict[str, Any]]:
        """执行限价开多，并计划止盈止损"""
        try:
            lev = leverage if leverage else self.default_leverage
            # 计算数量
            size = (usdt_amount * lev) / limit_price
            asset_info = self.client.get_asset_info(symbol)
            if asset_info and 'szDecimals' in asset_info:
                size = round(size, asset_info['szDecimals'])
            else:
                size = round(size, 3)
            
            # 记录预设
            use_tp = tp_ratio if tp_ratio is not None else self.take_profit_ratio
            use_sl = sl_ratio if sl_ratio is not None else self.stop_loss_ratio
            tp_px = self.client.format_price(symbol, limit_price * (1 + use_tp))
            sl_px = self.client.format_price(symbol, limit_price * (1 - use_sl))

            print(f"   [Limit Buy] {symbol} {size} @ {limit_price} | TP: {tp_px} SL: {sl_px}")
            
            # 下限价单
            limit_order = self.client.place_limit_order(symbol, True, size, limit_price)
            
            if isinstance(limit_order, dict) and limit_order.get('status') == 'ok':
                return {
                    'success': True, 
                    'limit_order': limit_order,
                    'tp_price': tp_px,
                    'sl_price': sl_px
                }
            print(f"   [OrderManager] ❌ 下单失败: {limit_order}")
            return {'success': False, 'message': str(limit_order)}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    def execute_short_limit(
        self,
        symbol: str,
        usdt_amount: float,
        limit_price: float,
        leverage: Optional[int] = None,
        tp_ratio: Optional[float] = None,
        sl_ratio: Optional[float] = None
    ) -> Optional[Dict[str, Any]]:
        """执行限价开空，并计划止盈止损"""
        try:
            lev = leverage if leverage else self.default_leverage
            size = (usdt_amount * lev) / limit_price
            asset_info = self.client.get_asset_info(symbol)
            if asset_info and 'szDecimals' in asset_info:
                size = round(size, asset_info['szDecimals'])
            else:
                size = round(size, 3)
            
            use_tp = tp_ratio if tp_ratio is not None else self.take_profit_ratio
            use_sl = sl_ratio if sl_ratio is not None else self.stop_loss_ratio
            tp_px = self.client.format_price(symbol, limit_price * (1 - use_tp))
            sl_px = self.client.format_price(symbol, limit_price * (1 + use_sl))

            print(f"   [Limit Sell] {symbol} {size} @ {limit_price} | TP: {tp_px} SL: {sl_px}")
            
            limit_order = self.client.place_limit_order(symbol, False, size, limit_price)
            
            if isinstance(limit_order, dict) and limit_order.get('status') == 'ok':
                return {
                    'success': True, 
                    'limit_order': limit_order,
                    'tp_price': tp_px,
                    'sl_price': sl_px
                }
            print(f"   [OrderManager] ❌ 下单失败: {limit_order}")
            return {'success': False, 'message': str(limit_order)}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    def check_sufficient_balance(self, amount: float) -> bool:
        """检查是否有足够的可用余额"""
        try:
            balance_info = self.get_available_balance_info()
            if balance_info['status'] == 'ok':
                return balance_info['available'] >= amount
            return False
        except Exception:
            return False

    def execute_long(
        self,
        symbol: str,
        usdt_amount: float,
        leverage: Optional[int] = None,
        with_tpsl: bool = True
    ) -> Dict[str, Any]:
        """执行市价开多"""
        try:
            lev = leverage if leverage else self.default_leverage
            # 获取当前市场价格用于计算数量
            market_price = self.client.get_current_price(symbol)
            if not market_price:
                return {'success': False, 'message': '无法获取市场价格'}

            size = (usdt_amount * lev) / market_price
            asset_info = self.client.get_asset_info(symbol)
            if asset_info and 'szDecimals' in asset_info:
                size = round(size, asset_info['szDecimals'])
            else:
                size = round(size, 3)

            print(f"   [Market Buy] {symbol} {size} @ Market (Est. {market_price})")
            
            # 下市价单
            result = self.client.place_market_order(symbol, True, size)
            
            if isinstance(result, dict) and result.get('status') == 'ok':
                result['success'] = True
                result['quantity'] = size
                return result
            return {'success': False, 'message': str(result)}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    def execute_short(
        self,
        symbol: str,
        usdt_amount: float,
        leverage: Optional[int] = None,
        with_tpsl: bool = True
    ) -> Dict[str, Any]:
        """执行市价开空"""
        try:
            lev = leverage if leverage else self.default_leverage
            market_price = self.client.get_current_price(symbol)
            if not market_price:
                return {'success': False, 'message': '无法获取市场价格'}

            size = (usdt_amount * lev) / market_price
            asset_info = self.client.get_asset_info(symbol)
            if asset_info and 'szDecimals' in asset_info:
                size = round(size, asset_info['szDecimals'])
            else:
                size = round(size, 3)

            print(f"   [Market Sell] {symbol} {size} @ Market (Est. {market_price})")
            
            # 下市价单
            result = self.client.place_market_order(symbol, False, size)
            
            if isinstance(result, dict) and result.get('status') == 'ok':
                result['success'] = True
                result['quantity'] = size
                return result
            return {'success': False, 'message': str(result)}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    def cancel_limit_order(self, symbol: str, order_id: int) -> Dict[str, Any]:
        return self.client.cancel_order(symbol, order_id)

    def calculate_suggested_trade_amount(
        self,
        desired_amount: float,
        min_trade_amount: float = 10.0,
        balance_info: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """计算建议的交易金额"""
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
