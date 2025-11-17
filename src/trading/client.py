"""
Hyperliquid 永续合约客户端
基于官方 hyperliquid-python-sdk
"""
from typing import Optional, Dict, Any, List
import eth_account
from eth_account.signers.local import LocalAccount

from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants


class HyperliquidClient:
    """
    Hyperliquid 永续合约交易客户端
    
    提供统一的交易接口，使用 Hyperliquid 官方 Python SDK
    """

    def __init__(
        self,
        private_key: str,
        account_address: Optional[str] = None,
        testnet: bool = False
    ):
        """
        初始化 Hyperliquid 客户端

        支持两种模式：
        1. 单钱包模式：private_key 对应的地址就是交易地址，account_address 留空
        2. API 钱包模式：private_key 是 API 钱包私钥，account_address 是主钱包地址

        Args:
            private_key: 钱包私钥（0x开头的十六进制字符串）
            account_address: 主钱包地址（API 钱包模式下必填）
            testnet: 是否使用测试网（True=测试网，False=主网）
        """
        self.testnet = testnet
        self.base_url = constants.TESTNET_API_URL if testnet else constants.MAINNET_API_URL

        # 从私钥创建账户（用于签名）
        if not private_key.startswith('0x'):
            private_key = '0x' + private_key
        self.account: LocalAccount = eth_account.Account.from_key(private_key)

        # 判断是否为 API 钱包模式
        self.is_api_wallet_mode = (account_address is not None and
                                   account_address.lower() != self.account.address.lower())

        # 设置账户地址（余额和持仓查询的地址）
        if account_address:
            self.address = account_address
        else:
            self.address = self.account.address

        # 显示连接信息
        print(f"🔗 连接 Hyperliquid {'测试网' if testnet else '主网'}")
        if self.is_api_wallet_mode:
            print(f"🤖 模式: API 钱包代理")
            print(f"📍 主钱包地址: {self.address}")
            print(f"🔑 API 钱包地址: {self.account.address}")
        else:
            print(f"👤 模式: 单钱包")
            print(f"📍 钱包地址: {self.address}")

        # 初始化 Info API（市场数据查询）
        self.info = Info(self.base_url, skip_ws=True)

        # 初始化 Exchange API（交易执行）
        # 在 API 钱包模式下，account_address 参数告诉 Exchange 代理哪个主钱包
        self.exchange = Exchange(
            self.account,
            self.base_url,
            account_address=self.address if self.is_api_wallet_mode else None
        )

    def get_balance(self) -> Optional[Dict[str, Any]]:
        """
        获取账户余额信息
        
        Returns:
            {
                'accountValue': str,  # 账户总价值（USD）
                'totalMarginUsed': str,  # 已用保证金
                'totalRawUsd': str,  # 可用余额
                ...
            }
        """
        try:
            user_state = self.info.user_state(self.address)
            margin_summary = user_state.get('marginSummary', {})
            return {
                'accountValue': float(margin_summary.get('accountValue', 0)),
                'totalMarginUsed': float(margin_summary.get('totalMarginUsed', 0)),
                'totalRawUsd': float(margin_summary.get('totalRawUsd', 0)),
                'withdrawable': user_state.get('withdrawable', '0')
            }
        except Exception as e:
            print(f"❌ 获取余额失败: {e}")
            return None

    def get_positions(self) -> List[Dict[str, Any]]:
        """
        获取当前持仓
        
        Returns:
            List of positions:
            [{
                'coin': str,  # 交易对名称（如 'ETH'）
                'szi': str,  # 仓位大小（正数=多仓，负数=空仓）
                'entryPx': str,  # 入场价格
                'positionValue': str,  # 仓位价值
                'unrealizedPnl': str,  # 未实现盈亏
                'leverage': dict,  # 杠杆信息
                ...
            }]
        """
        try:
            user_state = self.info.user_state(self.address)
            positions = []
            for asset_position in user_state.get('assetPositions', []):
                position = asset_position.get('position', {})
                if float(position.get('szi', 0)) != 0:  # 只返回有持仓的
                    positions.append(position)
            return positions
        except Exception as e:
            print(f"❌ 获取持仓失败: {e}")
            return []

    def get_asset_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        获取交易对的元数据信息

        Args:
            symbol: 交易对符号（如 'ETH'）

        Returns:
            交易对元数据，包括精度信息
        """
        try:
            meta = self.info.meta()
            universe = meta.get('universe', [])
            for asset in universe:
                if asset.get('name') == symbol:
                    return asset
            print(f"⚠️ 找不到交易对 {symbol} 的元数据")
            return None
        except Exception as e:
            print(f"❌ 获取交易对信息失败: {e}")
            return None

    def get_current_price(self, symbol: str) -> Optional[float]:
        """
        获取当前价格

        Args:
            symbol: 交易对符号（如 'ETH'）

        Returns:
            当前价格
        """
        try:
            # 获取所有市场元数据
            all_mids = self.info.all_mids()
            if symbol in all_mids:
                return float(all_mids[symbol])
            else:
                print(f"⚠️ 找不到交易对 {symbol}")
                return None
        except Exception as e:
            print(f"❌ 获取价格失败: {e}")
            return None

    def format_price(self, symbol: str, price: float) -> float:
        """
        根据交易对的 tick size 格式化价格

        Hyperliquid 要求价格必须是 tick size 的整数倍
        对于大多数资产，tick size 是 0.1

        Args:
            symbol: 交易对符号
            price: 原始价格

        Returns:
            格式化后的价格
        """
        try:
            # Hyperliquid 的标准 tick size 是 0.1
            # 将价格四舍五入到 0.1 的整数倍
            tick_size = 0.1
            formatted = round(price / tick_size) * tick_size

            # 确保结果是 1 位小数
            formatted = round(formatted, 1)

            return formatted

        except Exception as e:
            print(f"❌ 格式化价格失败: {e}")
            import traceback
            traceback.print_exc()
            # 返回四舍五入到 0.1 的值
            return round(round(price / 0.1) * 0.1, 1)

    @staticmethod
    def check_order_success(order_result: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """
        检查订单是否成功

        Args:
            order_result: 订单结果字典

        Returns:
            (是否成功, 错误信息)
        """
        if not order_result:
            return False, "订单结果为空"

        # 检查顶层 status
        if order_result.get('status') != 'ok':
            error_msg = order_result.get('message', '未知错误')
            return False, f"订单请求失败: {error_msg}"

        # 检查 response 中的详细状态
        response = order_result.get('response', {})
        response_type = response.get('type')

        # 对于订单类型的响应，检查 statuses
        if response_type == 'order':
            data = response.get('data', {})
            statuses = data.get('statuses', [])

            # 检查是否有错误
            errors = []
            for status in statuses:
                if 'error' in status:
                    errors.append(status['error'])

            if errors:
                return False, '; '.join(errors)

            # 如果没有错误，检查是否有成功的订单
            if statuses:
                return True, None
            else:
                return False, "没有返回订单状态"

        # 对于其他类型的响应，默认认为成功
        return True, None

    def place_market_order(
        self,
        symbol: str,
        is_buy: bool,
        size: float,
        reduce_only: bool = False,
        slippage: float = 0.01
    ) -> Optional[Dict[str, Any]]:
        """
        下市价单

        Args:
            symbol: 交易对符号（如 'ETH'）
            is_buy: True=买入（做多），False=卖出（做空）
            size: 下单数量（合约数量）
            reduce_only: 是否只减仓（注意：market_open 不直接支持此参数）
            slippage: 滑点容忍度（默认 1%，官方推荐）

        Returns:
            订单结果
        """
        try:
            # 格式化数量，根据交易对的 szDecimals 精度要求
            asset_info = self.get_asset_info(symbol)
            if asset_info and 'szDecimals' in asset_info:
                decimals = asset_info['szDecimals']
                size = round(size, decimals)
            else:
                size = round(size, 3)

            # 使用官方的 market_open 方法
            # 注意：market_open 不直接支持 reduce_only 参数
            # 如果需要 reduce_only，应该在调用前验证持仓
            order_result = self.exchange.market_open(
                symbol,
                is_buy,
                size,
                None,      # px=None 表示使用当前市价
                slippage   # 滑点容忍度
            )

            return order_result

        except Exception as e:
            print(f"❌ 下单失败: {e}")
            return {'status': 'error', 'message': str(e)}

    def place_limit_order(
        self,
        symbol: str,
        is_buy: bool,
        size: float,
        price: float,
        reduce_only: bool = False
    ) -> Optional[Dict[str, Any]]:
        """
        下限价单

        Args:
            symbol: 交易对符号（如 'ETH'）
            is_buy: True=买入，False=卖出
            size: 下单数量
            price: 限价
            reduce_only: 是否只减仓

        Returns:
            订单结果
        """
        try:
            # 格式化数量，根据交易对的 szDecimals 精度要求
            asset_info = self.get_asset_info(symbol)
            if asset_info and 'szDecimals' in asset_info:
                decimals = asset_info['szDecimals']
                size = round(size, decimals)
            else:
                size = round(size, 3)

            # 格式化价格
            price = self.format_price(symbol, price)

            order_result = self.exchange.order(
                symbol,
                is_buy,
                size,
                price,
                {"limit": {"tif": "Gtc"}},  # Good-til-Cancel
                reduce_only=reduce_only
            )
            return order_result
        except Exception as e:
            print(f"❌ 下单失败: {e}")
            return {'status': 'error', 'message': str(e)}

    def place_order_with_tpsl(
        self,
        symbol: str,
        is_buy: bool,
        size: float,
        take_profit_price: Optional[float] = None,
        stop_loss_price: Optional[float] = None,
        slippage: float = 0.01
    ) -> Dict[str, Any]:
        """
        下带止盈止损的市价单（使用官方 market_open 方法）

        Args:
            symbol: 交易对符号
            is_buy: True=买入，False=卖出
            size: 下单数量
            take_profit_price: 止盈价格
            stop_loss_price: 止损价格
            slippage: 滑点容忍度（默认 1%，官方推荐）
            
        Returns:
            {
                'success': bool,
                'market_order': dict,
                'take_profit_order': dict,
                'stop_loss_order': dict,
                'errors': list
            }
        """
        result = {
            'success': False,
            'market_order': None,
            'take_profit_order': None,
            'stop_loss_order': None,
            'errors': []
        }
        
        try:
            # 1. 下市价单
            market_order = self.place_market_order(symbol, is_buy, size, slippage=slippage)
            result['market_order'] = market_order
            
            if market_order.get('status') != 'ok':
                result['errors'].append(f"市价单失败: {market_order}")
                return result
            
            # 2. 下止盈单（如果提供）
            if take_profit_price:
                tp_result = self.place_tpsl_order(
                    symbol=symbol,
                    trigger_price=take_profit_price,
                    is_buy=not is_buy,  # 平仓方向相反
                    size=size,
                    is_tp=True
                )
                result['take_profit_order'] = tp_result
                if tp_result.get('status') != 'ok':
                    result['errors'].append(f"止盈单失败: {tp_result}")
            
            # 3. 下止损单（如果提供）
            if stop_loss_price:
                sl_result = self.place_tpsl_order(
                    symbol=symbol,
                    trigger_price=stop_loss_price,
                    is_buy=not is_buy,  # 平仓方向相反
                    size=size,
                    is_tp=False
                )
                result['stop_loss_order'] = sl_result
                if sl_result.get('status') != 'ok':
                    result['errors'].append(f"止损单失败: {sl_result}")
            
            # 判断整体成功
            result['success'] = (
                market_order.get('status') == 'ok' and
                (not take_profit_price or result['take_profit_order'].get('status') == 'ok') and
                (not stop_loss_price or result['stop_loss_order'].get('status') == 'ok')
            )
            
            return result
            
        except Exception as e:
            result['errors'].append(f"异常: {str(e)}")
            return result

    def place_tpsl_order(
        self,
        symbol: str,
        trigger_price: float,
        is_buy: bool,
        size: float,
        is_tp: bool = True
    ) -> Dict[str, Any]:
        """
        下止盈或止损单

        Args:
            symbol: 交易对符号
            trigger_price: 触发价格
            is_buy: True=买入，False=卖出
            size: 数量
            is_tp: True=止盈单，False=止损单

        Returns:
            订单结果
        """
        try:
            # 格式化触发价格，避免精度问题
            trigger_price = self.format_price(symbol, trigger_price)

            # 格式化数量，根据交易对的 szDecimals 精度要求
            asset_info = self.get_asset_info(symbol)
            if asset_info and 'szDecimals' in asset_info:
                decimals = asset_info['szDecimals']
                size = round(size, decimals)
            else:
                size = round(size, 3)

            # 构造触发单类型
            # 注意：triggerPx 必须是字符串
            # 官方文档: https://github.com/hyperliquid-dex/hyperliquid-python-sdk/blob/master/examples/basic_tpsl.py
            order_type = {
                "trigger": {
                    "isMarket": True,
                    "triggerPx": str(trigger_price),
                    "tpsl": "tp" if is_tp else "sl"
                }
            }

            # 计算限价（止盈止损单的备用限价）
            if is_tp:
                limit_price = trigger_price * 0.95 if not is_buy else trigger_price * 1.05
            else:
                limit_price = trigger_price * 1.05 if not is_buy else trigger_price * 0.95

            # 格式化限价，避免精度问题
            limit_price = self.format_price(symbol, limit_price)

            # 下单
            order_result = self.exchange.order(
                symbol,
                is_buy,
                size,
                limit_price,
                order_type,
                reduce_only=True
            )

            return order_result

        except Exception as e:
            print(f"❌ 下止盈止损单失败: {e}")
            return {'status': 'error', 'message': str(e)}

    def cancel_order(self, symbol: str, oid: int) -> Dict[str, Any]:
        """
        取消订单
        
        Args:
            symbol: 交易对符号
            oid: 订单ID
            
        Returns:
            取消结果
        """
        try:
            cancel_result = self.exchange.cancel(symbol, oid)
            return cancel_result
        except Exception as e:
            print(f"❌ 取消订单失败: {e}")
            return {'status': 'error', 'message': str(e)}

    def update_leverage(self, symbol: str, leverage: int, is_cross: bool = True) -> Dict[str, Any]:
        """
        更新杠杆倍数

        Args:
            symbol: 交易对符号
            leverage: 杠杆倍数
            is_cross: True=全仓模式，False=逐仓模式

        Returns:
            更新结果
        """
        try:
            # 先获取资产信息，检查最大杠杆
            asset_info = self.get_asset_info(symbol)
            if asset_info:
                max_leverage = asset_info.get('maxLeverage', 50)
                if leverage > max_leverage:
                    error_msg = f"杠杆 {leverage}x 超过 {symbol} 的最大杠杆 {max_leverage}x"
                    print(f"❌ {error_msg}")
                    return {'status': 'error', 'message': error_msg}

            # 设置杠杆
            result = self.exchange.update_leverage(leverage, symbol, is_cross=is_cross)

            # 验证结果
            if result.get('status') == 'err':
                error_msg = result.get('response', '未知错误')
                print(f"❌ 杠杆设置失败: {error_msg}")
                return {'status': 'error', 'message': error_msg}

            return result
        except Exception as e:
            print(f"❌ 更新杠杆失败: {e}")
            return {'status': 'error', 'message': str(e)}

    def get_candles(
        self,
        symbol: str,
        interval: str = "15m",
        start_time: Optional[int] = None,
        end_time: Optional[int] = None
    ) -> Optional[List[Dict[str, Any]]]:
        """
        获取K线数据
        
        Args:
            symbol: 交易对符号
            interval: 时间间隔（1m, 5m, 15m, 1h, 4h, 1d）
            start_time: 开始时间（毫秒时间戳）
            end_time: 结束时间（毫秒时间戳）
            
        Returns:
            K线数据列表
        """
        try:
            candles = self.info.candles_snapshot(
                coin=symbol,
                interval=interval,
                startTime=start_time,
                endTime=end_time
            )
            return candles
        except Exception as e:
            print(f"❌ 获取K线数据失败: {e}")
            return None

    def check_api_wallet_authorization(self) -> Dict[str, Any]:
        """
        检查 API 钱包授权状态

        仅在 API 钱包模式下有效

        Returns:
            {
                'is_api_wallet_mode': bool,  # 是否为 API 钱包模式
                'is_authorized': bool,  # API 钱包是否被授权
                'main_wallet': str,  # 主钱包地址
                'api_wallet': str,  # API 钱包地址
            }
        """
        result = {
            'is_api_wallet_mode': self.is_api_wallet_mode,
            'is_authorized': False,
            'main_wallet': self.address,
            'api_wallet': self.account.address,
        }

        if not self.is_api_wallet_mode:
            print("ℹ️  当前为单钱包模式，无需检查授权")
            return result

        try:
            # 查询主钱包的授权列表
            # Hyperliquid 的 user_state 中包含 authorized agents
            user_state = self.info.user_state(self.address)

            # 检查是否有 authorized agents（不同版本的 API 可能返回不同的字段）
            # 通常授权信息可能在不同的地方，这里先打印完整信息以便调试
            print(f"\n🔍 检查 API 钱包授权状态")
            print(f"   主钱包: {self.address}")
            print(f"   API 钱包: {self.account.address}")

            # 由于 Hyperliquid API 的授权检查方式可能因版本而异
            # 这里提供一个基本的检查逻辑
            # 实际的授权检查可能需要根据 API 文档调整

            # 尝试获取授权列表（如果 API 支持）
            if 'authorizedAgents' in user_state:
                agents = user_state.get('authorizedAgents', [])
                is_authorized = self.account.address.lower() in [a.lower() for a in agents]
                result['is_authorized'] = is_authorized
                print(f"   授权状态: {'✅ 已授权' if is_authorized else '❌ 未授权'}")
                return result

            # 如果没有明确的授权列表，尝试通过测试交易来判断
            # 但这里我们只做查询，不做实际交易
            print(f"   ⚠️  无法直接查询授权状态")
            print(f"   💡 建议: 在主钱包中授权 API 钱包，或进行测试交易验证")

        except Exception as e:
            print(f"❌ 检查授权失败: {e}")

        return result

    def close_position(self, symbol: str, size: Optional[float] = None) -> Dict[str, Any]:
        """
        平仓（使用官方 market_close 或 market_open 方法）

        Args:
            symbol: 交易对符号
            size: 平仓数量（None=全平）

        Returns:
            平仓结果
        """
        try:
            if size is None:
                # 全仓平仓 - 使用官方 market_close 方法（最简单最可靠）
                print(f"🔴 市价全平 {symbol}")
                result = self.exchange.market_close(symbol)
                return result
            else:
                # 部分平仓 - 需要判断持仓方向
                positions = self.get_positions()
                position = next((p for p in positions if p['coin'] == symbol), None)

                if not position:
                    return {'status': 'error', 'message': f'没有 {symbol} 的持仓'}

                # 获取持仓数量和方向
                position_size = float(position['szi'])
                if position_size == 0:
                    return {'status': 'error', 'message': f'{symbol} 仓位为 0'}

                # 判断平仓方向：持仓为正(多仓)则卖出平仓，持仓为负(空仓)则买入平仓
                is_buy = position_size < 0
                close_size = abs(float(size))

                print(f"🔴 市价部分平仓 {symbol}: {'买入' if is_buy else '卖出'} {close_size}")

                # 使用官方 market_open 方法，配合 1% 滑点
                result = self.exchange.market_open(
                    symbol,
                    is_buy,
                    close_size,
                    None,    # px=None 使用市价
                    0.01     # 1% 滑点（官方推荐，比原来的5%更合理）
                )

                return result

        except Exception as e:
            print(f"❌ 平仓失败: {e}")
            return {'status': 'error', 'message': str(e)}

    # ==================== 现货交易方法 ====================

    def get_spot_asset_index(self, symbol: str) -> Optional[int]:
        """
        获取现货资产索引（用于计算真正的现货资产 ID）

        Hyperliquid 现货资产 ID = 10000 + index（index 来自 spotMeta.universe）

        Args:
            symbol: 币种符号（如 'BTC', 'ETH', 'PURR'）

        Returns:
            现货资产索引，失败返回 None
        """
        try:
            spot_meta = self.info.spot_meta()
            universe = spot_meta.get('universe', [])

            # 构造现货交易对名称（如 "BTC/USDC"）
            spot_pair = f"{symbol}/USDC"

            # 在 spotMeta.universe 中查找
            for idx, spot in enumerate(universe):
                if spot.get('name') == spot_pair:
                    print(f"✓ 找到现货交易对 {spot_pair}，索引 = {idx}")
                    return idx

            print(f"❌ 未找到现货交易对: {spot_pair}")
            print(f"   可用的现货交易对: {[s.get('name') for s in universe[:5]]}...")
            return None

        except Exception as e:
            print(f"❌ 获取现货资产索引失败: {e}")
            import traceback
            traceback.print_exc()
            return None

    def buy_spot(
        self,
        symbol: str,
        usdt_amount: float,
        slippage: float = 0.01
    ) -> Dict[str, Any]:
        """
        买入现货（真正的无杠杆现货持有）

        使用 Hyperliquid 真现货 API，资产 ID = 10000 + spotMeta.universe.index

        Args:
            symbol: 币种符号（如 'BTC', 'ETH'）
            usdt_amount: 投入的 USDT 金额
            slippage: 滑点容忍度（默认 1%）

        Returns:
            订单结果 {'status': 'ok'/'error', 'message': str, 'data': dict}
        """
        try:
            # 1. 获取现货资产索引
            spot_index = self.get_spot_asset_index(symbol)
            if spot_index is None:
                return {'status': 'error', 'message': f'无法获取 {symbol} 现货资产索引'}

            # 2. 计算现货资产 ID（10000 + index）
            asset_id = 10000 + spot_index
            print(f"📦 现货资产 ID: {asset_id} (10000 + {spot_index})")

            # 3. 获取当前价格
            current_price = self.get_current_price(symbol)
            if not current_price:
                return {'status': 'error', 'message': f'无法获取 {symbol} 当前价格'}

            # 4. 计算购买数量
            buy_size = usdt_amount / current_price

            # 5. 获取资产精度信息
            asset_info = self.get_asset_info(symbol)
            if asset_info and 'szDecimals' in asset_info:
                decimals = asset_info['szDecimals']
                buy_size = round(buy_size, decimals)
            else:
                buy_size = round(buy_size, 4)  # 默认精度

            print(f"📦 现货买入 {symbol}")
            print(f"   投入金额: ${usdt_amount:.2f}")
            print(f"   当前价格: ${current_price:.2f}")
            print(f"   购买数量: {buy_size} {symbol}")
            print(f"   杠杆倍数: 1x（真现货，无杠杆）")

            # 6. 计算限价（买入时设置稍高价格，确保快速成交）
            limit_price = current_price * (1 + slippage)
            limit_price_str = str(round(limit_price, 2))
            buy_size_str = str(buy_size)

            # 7. 使用真正的现货 order API
            # 注意：这里使用数字资产 ID，不是字符串交易对
            print(f"   正在下单: asset={asset_id}, is_buy=True, size={buy_size_str}, price={limit_price_str}")

            result = self.exchange.order(
                asset=asset_id,  # 数字资产 ID (10000+)
                is_buy=True,
                sz=float(buy_size_str),
                limit_px=float(limit_price_str),
                order_type={"limit": {"tif": "Ioc"}},  # Immediate-or-Cancel 立即成交或取消
                reduce_only=False
            )

            print(f"   订单结果: {result}")

            if result and result.get('status') == 'ok':
                print(f"✅ 现货买入成功（无杠杆，真现货持有）")
                return {
                    'status': 'ok',
                    'message': f'买入 {buy_size} {symbol} 现货',
                    'data': {
                        'symbol': symbol,
                        'asset_id': asset_id,
                        'spot_index': spot_index,
                        'size': buy_size,
                        'price': current_price,
                        'usdt_amount': usdt_amount,
                        'leverage': 1,  # 现货永远是 1x
                        'result': result
                    }
                }
            else:
                error_msg = result.get('response', '未知错误') if result else '未知错误'
                print(f"❌ 现货买入失败: {error_msg}")
                return {'status': 'error', 'message': error_msg, 'result': result}

        except Exception as e:
            print(f"❌ 现货买入异常: {e}")
            import traceback
            traceback.print_exc()
            return {'status': 'error', 'message': str(e)}

    def sell_spot(
        self,
        symbol: str,
        size: Optional[float] = None,
        slippage: float = 0.01
    ) -> Dict[str, Any]:
        """
        卖出现货（真正的无杠杆现货）

        使用 Hyperliquid 真现货 API，资产 ID = 10000 + spotMeta.universe.index

        Args:
            symbol: 币种符号（如 'BTC', 'ETH'）
            size: 卖出数量（None=全部卖出）
            slippage: 滑点容忍度（默认 1%）

        Returns:
            订单结果
        """
        try:
            # 1. 获取现货资产索引
            spot_index = self.get_spot_asset_index(symbol)
            if spot_index is None:
                return {'status': 'error', 'message': f'无法获取 {symbol} 现货资产索引'}

            # 2. 计算现货资产 ID（10000 + index）
            asset_id = 10000 + spot_index
            print(f"💰 现货资产 ID: {asset_id} (10000 + {spot_index})")

            # 3. 如果未指定数量，查询现货余额
            if size is None:
                spot_balances = self.get_spot_balances()
                balance = next((b for b in spot_balances if b['coin'] == symbol), None)
                if not balance:
                    return {'status': 'error', 'message': f'没有 {symbol} 的现货持仓'}

                size = float(balance['total'])
                if size <= 0:
                    return {'status': 'error', 'message': f'{symbol} 余额为 0'}

            # 4. 获取当前价格
            current_price = self.get_current_price(symbol)
            if not current_price:
                return {'status': 'error', 'message': f'无法获取 {symbol} 当前价格'}

            print(f"💰 现货卖出 {symbol}")
            print(f"   卖出数量: {size} {symbol}")
            print(f"   当前价格: ${current_price:.2f}")
            print(f"   预计收益: ${size * current_price:.2f}")

            # 5. 计算限价（卖出时设置稍低价格，确保快速成交）
            limit_price = current_price * (1 - slippage)
            limit_price_str = str(round(limit_price, 2))
            size_str = str(size)

            # 6. 使用真正的现货 order API
            print(f"   正在下单: asset={asset_id}, is_buy=False, size={size_str}, price={limit_price_str}")

            result = self.exchange.order(
                asset=asset_id,  # 数字资产 ID (10000+)
                is_buy=False,
                sz=float(size_str),
                limit_px=float(limit_price_str),
                order_type={"limit": {"tif": "Ioc"}},  # Immediate-or-Cancel
                reduce_only=False
            )

            print(f"   订单结果: {result}")

            if result and result.get('status') == 'ok':
                print(f"✅ 现货卖出成功")
                return {
                    'status': 'ok',
                    'message': f'卖出 {size} {symbol} 现货',
                    'data': {
                        'symbol': symbol,
                        'asset_id': asset_id,
                        'spot_index': spot_index,
                        'size': size,
                        'price': current_price,
                        'result': result
                    }
                }
            else:
                error_msg = result.get('response', '未知错误') if result else '未知错误'
                print(f"❌ 现货卖出失败: {error_msg}")
                return {'status': 'error', 'message': error_msg, 'result': result}

        except Exception as e:
            print(f"❌ 现货卖出异常: {e}")
            import traceback
            traceback.print_exc()
            return {'status': 'error', 'message': str(e)}

    def get_spot_balances(self) -> List[Dict[str, Any]]:
        """
        获取所有现货余额

        Returns:
            现货余额列表 [{'coin': 'BTC', 'total': '0.5', 'hold': '0.1'}, ...]
        """
        try:
            user_state = self.info.user_state(self.address)

            # 提取现货余额
            balances = user_state.get('balances', [])

            # 过滤出有余额的币种
            spot_balances = []
            for balance in balances:
                coin = balance.get('coin', '')
                total = float(balance.get('total', 0))
                hold = float(balance.get('hold', 0))

                if total > 0:
                    spot_balances.append({
                        'coin': coin,
                        'total': total,
                        'hold': hold,
                        'available': total - hold
                    })

            return spot_balances

        except Exception as e:
            print(f"❌ 获取现货余额失败: {e}")
            return []

    def get_spot_balance(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        获取指定币种的现货余额

        Args:
            symbol: 币种符号（如 'BTC', 'ETH'）

        Returns:
            {'coin': str, 'total': float, 'hold': float, 'available': float}
        """
        balances = self.get_spot_balances()
        return next((b for b in balances if b['coin'] == symbol), None)
