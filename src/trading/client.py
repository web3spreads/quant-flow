"""
Hyperliquid 永续合约客户端
基于官方 hyperliquid-python-sdk
"""

import time
import traceback
from decimal import ROUND_HALF_UP, Decimal
from math import floor, log10
from typing import Any

import eth_account
from eth_account.signers.local import LocalAccount
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants

from src.fees import FeeRates, calculate_fee_rates
from src.utils.cloud_logger import get_cloud_logger
from src.utils.hyperliquid import create_info, safe_spot_meta


class HyperliquidClient:
    """
    Hyperliquid 永续合约交易客户端

    提供统一的交易接口，使用 Hyperliquid 官方 Python SDK
    """

    def __init__(
        self,
        private_key: str,
        account_address: str | None = None,
        testnet: bool = False,
        api_urls: list[str] | None = None,
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
            api_urls: 备用 API 地址列表
        """
        self.testnet = testnet

        # 处理 API 地址列表
        default_url = constants.TESTNET_API_URL if testnet else constants.MAINNET_API_URL
        self.api_urls = api_urls if api_urls else [default_url]
        self.current_url_index = 0
        self.base_url = self.api_urls[self.current_url_index]

        # 从私钥创建账户（用于签名）
        if not private_key.startswith("0x"):
            private_key = "0x" + private_key
        self.account: LocalAccount = eth_account.Account.from_key(private_key)

        # 判断是否为 API 钱包模式
        self.is_api_wallet_mode = (
            account_address is not None and account_address.lower() != self.account.address.lower()
        )

        # 设置账户地址（余额和持仓查询的地址）
        if account_address:
            self.address = account_address
        else:
            self.address = self.account.address

        # 显示连接信息
        print(f"🔗 连接 Hyperliquid {'测试网' if testnet else '主网'}")
        if self.is_api_wallet_mode:
            print("🤖 模式: API 钱包代理")
            print(f"📍 主钱包地址: {self.address}")
            print(f"🔑 API 钱包地址: {self.account.address}")
        else:
            print("👤 模式: 单钱包")
            print(f"📍 钱包地址: {self.address}")

        # 预先获取安全的 spot_meta，Info 和 Exchange 共享，避免重复请求和越界崩溃
        self._spot_meta = safe_spot_meta(self.base_url)

        # 初始化 Info API（市场数据查询）
        self.info = create_info(self.base_url, skip_ws=True)

        # 初始化 Exchange API（交易执行）
        # 在 API 钱包模式下，account_address 参数告诉 Exchange 代理哪个主钱包
        self.exchange = Exchange(
            self.account,
            self.base_url,
            account_address=self.address if self.is_api_wallet_mode else None,
            spot_meta=self._spot_meta,
        )

    def _rotate_api(self):
        """轮转到下一个 API 地址"""
        if len(self.api_urls) <= 1:
            return False

        self.current_url_index = (self.current_url_index + 1) % len(self.api_urls)
        self.base_url = self.api_urls[self.current_url_index]
        print(f"⚠️ API 请求失败，正在切换至备用节点: {self.base_url}")

        # 重新实例化 SDK 组件
        self._spot_meta = safe_spot_meta(self.base_url)
        self.info = create_info(self.base_url, skip_ws=True)
        self.exchange = Exchange(
            self.account,
            self.base_url,
            account_address=self.address if self.is_api_wallet_mode else None,
            spot_meta=self._spot_meta,
        )
        return True

    def _request_with_fallback(self, func_name: str, *args, is_exchange: bool = False, **kwargs):
        """执行带 Fallback 机制的请求"""
        attempts = 0
        max_attempts = len(self.api_urls)

        last_exception = None

        while attempts < max_attempts:
            try:
                # 获取当前要调用的对象 (info 或 exchange)
                target = self.exchange if is_exchange else self.info
                # 获取对应的方法
                func = getattr(target, func_name)
                # 执行调用
                return func(*args, **kwargs)
            except Exception as e:
                last_exception = e
                attempts += 1
                print(f"❌ API 调用异常 ({self.base_url}): {e}")

                if attempts < max_attempts:
                    if not self._rotate_api():
                        break
                else:
                    break

        # 如果所有尝试都失败，抛出异常或返回 None
        print(f"🚨 所有 API 节点均已尝试，请求最终失败: {last_exception}")
        raise last_exception

    def fetch_user_fee_rates(
        self,
        *,
        is_aligned_quote_token: bool = False,
        deployer_fee_scale: float = 0.0,
        growth_mode: bool = False,
    ) -> FeeRates:
        """
        获取用户当前的实际费率（maker/taker），按 Hyperliquid 官方公式计算。

        Args:
            is_aligned_quote_token: 是否为 aligned quote 资产
            deployer_fee_scale: HIP-3 部署者费率缩放（0-3，非 HIP-3=0）
            growth_mode: HIP-3 growth mode

        Returns:
            FeeRates(maker_rate, taker_rate) 小数形式（0.00045 = 0.045%）
        """
        user_fees = self.info.user_fees(self.address)
        maker_rate = float(user_fees.get("userAddRate", 0))
        taker_rate = float(user_fees.get("userCrossRate", 0))
        active_referral_discount = float(user_fees.get("activeReferralDiscount", 0))

        return calculate_fee_rates(
            FeeRates(maker_rate=maker_rate, taker_rate=taker_rate),
            active_referral_discount=active_referral_discount,
            is_aligned_quote_token=is_aligned_quote_token,
            deployer_fee_scale=deployer_fee_scale,
            growth_mode=growth_mode,
        )

    def _is_unified_account(self) -> bool | None:
        """判定当前地址是否为保证金合一账户（unifiedAccount/portfolioMargin）。

        经官方 userAbstraction 接口判定，结果缓存（账户模式极少变更），
        经典账户后续调用零额外开销。查询失败返回 None（未知，交由调用方降级）。
        """
        cached = getattr(self, "_abstraction_mode", None)
        if cached is None:
            try:
                cached = self._request_with_fallback(
                    "query_user_abstraction_state", self.address
                )
            except Exception as e:
                print(f"⚠️ 账户模式查询失败（userAbstraction）: {e}")
                return None
            self._abstraction_mode = cached
        return cached in ("unifiedAccount", "portfolioMargin")

    def get_balance(self) -> dict[str, Any] | None:
        """
        获取账户余额信息

        Returns:
            {
                'accountValue': float,  # 账户总价值（USD，包含未实现盈亏）
                'totalMarginUsed': float,  # 已用保证金
                'totalRawUsd': float,  # 原始 USD 余额（现金部分，不包含未实现盈亏）
                'withdrawable': str,  # 可提取余额
            }

        注意：可用余额应该计算为 accountValue - totalMarginUsed，而不是直接使用 totalRawUsd
        """
        try:
            user_state = self._request_with_fallback("user_state", self.address)
            margin_summary = user_state.get("marginSummary", {})
            account_value = float(margin_summary.get("accountValue", 0))
            total_margin_used = float(margin_summary.get("totalMarginUsed", 0))
            withdrawable = user_state.get("withdrawable", "0")

            # 统一账户（unified account）兼容：新式账户 spot/perp 保证金合一，
            # 总净值以 spot 视图为准（USDC total=总抵押，hold=持仓/挂单占用），
            # perp marginSummary 无持仓时恒 0、有持仓时仅等于被占用的那部分抵押
            # （实测：$11 账户开仓后 perp accountValue 只剩 $2.28 == spot hold），
            # 两种形态都会严重低估净值。官方判定接口 userAbstraction 返回
            # "unifiedAccount"/"portfolioMargin"（合一模式）或 "default"（经典），
            # 结果缓存；判定失败时退回启发式（perp 视图无资产才尝试 spot）。
            unified = self._is_unified_account()
            if unified or (unified is None and account_value <= 0):
                try:
                    spot_state = self._request_with_fallback("spot_user_state", self.address)
                    for balance in spot_state.get("balances", []) or []:
                        if balance.get("coin") != "USDC":
                            continue
                        usdc_total = float(balance.get("total", 0))
                        if usdc_total > 0:
                            account_value = usdc_total
                            total_margin_used = float(balance.get("hold", 0))
                            withdrawable = str(account_value - total_margin_used)
                        break
                except Exception as spot_err:
                    # spot 查询失败时维持 perp 视图结果，不因兼容路径引入新故障
                    print(f"⚠️ 统一账户 spot 余额回退查询失败: {spot_err}")

            return {
                "accountValue": account_value,
                "totalMarginUsed": total_margin_used,
                "totalRawUsd": float(margin_summary.get("totalRawUsd", 0)),
                "available": account_value - total_margin_used,
                "withdrawable": withdrawable,
            }
        except Exception as e:
            print(f"❌ 获取余额失败: {e}")
            return None

    def get_positions(self) -> list[dict[str, Any]]:
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
            user_state = self._request_with_fallback("user_state", self.address)
            positions = []
            for asset_position in user_state.get("assetPositions", []):
                position = asset_position.get("position", {})
                if float(position.get("szi", 0)) != 0:  # 只返回有持仓的
                    positions.append(position)
            return positions
        except Exception as e:
            print(f"❌ 获取持仓失败: {e}")
            return []

    def get_open_orders(self, include_trigger: bool = False) -> list[dict[str, Any]]:
        """
        获取待处理的订单列表

        Args:
            include_trigger: 是否包含 trigger 触发单（TP/SL）

        Returns:
            List of open orders:
            [{
                'oid': int,  # 订单ID
                'coin': str,  # 交易对符号
                'side': str,  # 'B'=买入, 'A'=卖出
                'sz': str,  # 订单数量
                'limitPx': str,  # 限价价格
                'orderType': dict,  # 订单类型信息
                ...
            }]
        """
        try:
            # 优先使用 frontend_open_orders：包含 Trigger 单（Stop Market / TP Market）
            # 仅使用 user_state.openOrders 会在部分账户模式下漏掉触发单，导致清理逻辑失效。
            raw_orders = self._request_with_fallback("frontend_open_orders", self.address)
            if not isinstance(raw_orders, list):
                user_state = self._request_with_fallback("user_state", self.address)
                raw_orders = (
                    user_state.get("openOrders", []) if isinstance(user_state, dict) else []
                )

            normalized_orders = [
                self._normalize_open_order(order) for order in raw_orders if isinstance(order, dict)
            ]
            if include_trigger:
                return normalized_orders

            return [o for o in normalized_orders if not self._is_trigger_like_order(o)]
        except Exception as e:
            print(f"❌ 获取待处理订单失败: {e}")
            return []

    @staticmethod
    def _is_trigger_like_order(order: dict[str, Any]) -> bool:
        order_type = order.get("orderType")
        if isinstance(order_type, dict):
            return "trigger" in order_type

        if bool(order.get("isTrigger")):
            return True

        if isinstance(order_type, str):
            lowered = order_type.lower()
            return "stop" in lowered or "take profit" in lowered or "trigger" in lowered

        trigger_condition = str(order.get("triggerCondition", "")).strip()
        return trigger_condition not in {"", "N/A"}

    @staticmethod
    def _normalize_open_order(order: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(order)
        order_type = normalized.get("orderType")
        if isinstance(order_type, dict):
            return normalized

        if HyperliquidClient._is_trigger_like_order(order):
            normalized["orderType"] = {
                "trigger": {
                    "triggerPx": normalized.get("triggerPx"),
                    "triggerCondition": normalized.get("triggerCondition"),
                    "kind": order_type,
                }
            }
        else:
            normalized["orderType"] = {"limit": {"tif": normalized.get("tif") or "Gtc"}}

        return normalized

    def get_asset_info(self, symbol: str) -> dict[str, Any] | None:
        """
        获取交易对的元数据信息

        Args:
            symbol: 交易对符号（如 'ETH'）

        Returns:
            交易对元数据，包括精度信息
        """
        try:
            meta = self._request_with_fallback("meta")
            universe = meta.get("universe", [])
            for asset in universe:
                if asset.get("name") == symbol:
                    return asset
            print(f"⚠️ 找不到交易对 {symbol} 的元数据")
            return None
        except Exception as e:
            print(f"❌ 获取交易对信息失败: {e}")
            return None

    def get_current_price(self, symbol: str) -> float | None:
        """
        获取当前价格

        Args:
            symbol: 交易对符号（如 'ETH'）

        Returns:
            当前价格
        """
        try:
            # 获取所有市场元数据
            all_mids = self._request_with_fallback("all_mids")
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
        根据 Hyperliquid 的价格精度要求格式化价格

        Hyperliquid 价格要求:
        1. 最多5位有效数字
        2. 最多 MAX_DECIMALS - szDecimals 位小数（永续合约 MAX_DECIMALS=6）
        3. 必须是 tick size 的整数倍

        参考: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/tick-and-lot-size

        Args:
            symbol: 交易对符号
            price: 原始价格

        Returns:
            格式化后的价格
        """
        try:
            # 1. 首先限制到5位有效数字
            # 例如: 94283.7 -> 94284 (5位有效数字)
            if price > 0:
                # 计算数量级
                magnitude = floor(log10(abs(price)))
                # 保留5位有效数字
                sig_figs = 5
                # 计算需要保留的小数位数
                decimal_places = sig_figs - magnitude - 1
                if decimal_places < 0:
                    # 价格很大，需要四舍五入到整数位
                    factor = 10 ** (-decimal_places)
                    formatted = round(price / factor) * factor
                else:
                    formatted = round(price, decimal_places)
            else:
                formatted = price

            # 2. 确保是 tick size (0.1) 的整数倍
            # 使用 Decimal 避免浮点数精度问题
            tick_size = Decimal("0.1")
            price_decimal = Decimal(str(formatted))

            # 转换为 tick 单位，四舍五入，再转回价格
            ticks = (price_decimal / tick_size).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            formatted = float(ticks * tick_size)

            # 3. 获取 szDecimals，计算最大小数位数
            # 永续合约: max_decimals = 6 - szDecimals
            asset_info = self.get_asset_info(symbol)
            if asset_info:
                sz_decimals = asset_info.get("szDecimals", 0)
                max_price_decimals = 6 - sz_decimals
                # 限制小数位数（使用 Decimal 确保精度）
                formatted_decimal = Decimal(str(formatted))
                quantize_str = (
                    "0." + "0" * max(0, max_price_decimals) if max_price_decimals > 0 else "1"
                )
                formatted = float(
                    formatted_decimal.quantize(Decimal(quantize_str), rounding=ROUND_HALF_UP)
                )

            return float(formatted)

        except Exception as e:
            print(f"❌ 格式化价格失败: {e}")
            traceback.print_exc()
            # 回退：四舍五入到整数（最安全）
            return float(round(price))

    @staticmethod
    def check_order_success(order_result: dict[str, Any]) -> tuple[bool, str | None]:
        """
        检查订单是否成功

        【增强版】：
        - 检查订单是否真正成交（filled）
        - 区分订单提交成功和实际成交
        - 处理部分成交情况

        Args:
            order_result: 订单结果字典

        Returns:
            (是否成功, 错误信息)
        """
        if not order_result:
            return False, "订单结果为空"

        # 检查顶层 status
        if order_result.get("status") != "ok":
            error_msg = order_result.get("message", order_result.get("response", "未知错误"))
            return False, f"订单请求失败: {error_msg}"

        # 检查 response 中的详细状态
        response = order_result.get("response", {})
        response_type = response.get("type")

        # 对于订单类型的响应，检查 statuses
        if response_type == "order":
            data = response.get("data", {})
            statuses = data.get("statuses", [])

            if not statuses:
                return False, "没有返回订单状态"

            # 检查每个状态
            errors = []
            filled_count = 0
            resting_count = 0  # 挂单中

            for status in statuses:
                if "error" in status:
                    errors.append(status["error"])
                elif "filled" in status:
                    # 订单已成交
                    filled_count += 1
                elif "resting" in status:
                    # 订单挂单中（限价单正常状态）
                    resting_count += 1

            if errors:
                return False, "; ".join(errors)

            # 有成交或挂单都算成功
            if filled_count > 0 or resting_count > 0:
                return True, None

            # 检查是否有其他成功状态
            for status in statuses:
                if isinstance(status, dict) and not status.get("error"):
                    return True, None

            return False, f"未知订单状态: {statuses}"

        # 对于其他类型的响应，默认认为成功
        return True, None

    @staticmethod
    def get_order_fill_info(order_result: dict[str, Any]) -> dict[str, Any]:
        """
        从订单结果中提取成交信息

        Args:
            order_result: 订单结果字典

        Returns:
            {
                'is_filled': bool,
                'fill_price': float or None,
                'fill_size': float or None,
                'order_id': int or None,
                'is_resting': bool  # 是否挂单中
            }
        """
        result = {
            "is_filled": False,
            "fill_price": None,
            "fill_size": None,
            "order_id": None,
            "is_resting": False,
        }

        if not order_result or order_result.get("status") != "ok":
            return result

        response = order_result.get("response", {})
        if response.get("type") != "order":
            return result

        data = response.get("data", {})
        statuses = data.get("statuses", [])

        for status in statuses:
            if isinstance(status, dict):
                if "filled" in status:
                    filled_info = status["filled"]
                    result["is_filled"] = True
                    result["fill_price"] = float(filled_info.get("avgPx", 0))
                    result["fill_size"] = float(filled_info.get("totalSz", 0))
                    result["order_id"] = filled_info.get("oid")
                    break
                elif "resting" in status:
                    resting_info = status["resting"]
                    result["is_resting"] = True
                    result["order_id"] = resting_info.get("oid")

        return result

    def place_market_order(
        self,
        symbol: str,
        is_buy: bool,
        size: float,
        reduce_only: bool = False,
        slippage: float = 0.01,
    ) -> dict[str, Any] | None:
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
            if asset_info and "szDecimals" in asset_info:
                decimals = asset_info["szDecimals"]
                size = round(size, decimals)
            else:
                size = round(size, 3)

            # 使用官方的 market_open 方法
            # 注意：market_open 不直接支持 reduce_only 参数
            # 如果需要 reduce_only，应该在调用前验证持仓
            order_result = self._request_with_fallback(
                "market_open",
                symbol,
                is_buy,
                size,
                None,  # px=None 表示使用当前市价
                slippage,  # 滑点容忍度
                is_exchange=True,
            )

            return order_result

        except Exception as e:
            print(f"❌ 下单失败: {e}")
            return {"status": "error", "message": str(e)}

    def place_limit_order(
        self, symbol: str, is_buy: bool, size: float, price: float, reduce_only: bool = False
    ) -> dict[str, Any] | None:
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
            if asset_info and "szDecimals" in asset_info:
                decimals = asset_info["szDecimals"]
                size = round(size, decimals)
            else:
                size = round(size, 3)

            # 格式化价格
            price = self.format_price(symbol, price)

            order_result = self._request_with_fallback(
                "order",
                symbol,
                is_buy,
                size,
                price,
                {"limit": {"tif": "Gtc"}},  # Good-til-Cancel
                reduce_only=reduce_only,
                is_exchange=True,
            )
            return order_result
        except Exception as e:
            print(f"❌ 下单失败: {e}")
            return {"status": "error", "message": str(e)}

    def place_order_with_tpsl(
        self,
        symbol: str,
        is_buy: bool,
        size: float,
        take_profit_price: float | None = None,
        stop_loss_price: float | None = None,
        slippage: float = 0.01,
        require_stop_loss: bool = True,
    ) -> dict[str, Any]:
        """
        下带止盈止损的市价单（使用官方 market_open 方法）

        【重要安全机制】：
        - 如果止损单设置失败，会自动平仓以避免裸仓风险
        - 止盈单失败不会导致平仓，但会记录错误

        Args:
            symbol: 交易对符号
            is_buy: True=买入，False=卖出
            size: 下单数量
            take_profit_price: 止盈价格
            stop_loss_price: 止损价格
            slippage: 滑点容忍度（默认 1%，官方推荐）
            require_stop_loss: 是否强制要求止损单成功（默认True，失败则平仓）

        Returns:
            {
                'success': bool,
                'market_order': dict,
                'take_profit_order': dict,
                'stop_loss_order': dict,
                'errors': list,
                'rollback_executed': bool  # 是否执行了回滚平仓
            }
        """
        result = {
            "success": False,
            "market_order": None,
            "take_profit_order": None,
            "stop_loss_order": None,
            "errors": [],
            "rollback_executed": False,
        }

        try:
            # 1. 下市价单
            market_order = self.place_market_order(symbol, is_buy, size, slippage=slippage)
            result["market_order"] = market_order

            market_success, market_error = self.check_order_success(market_order)
            if not market_success:
                result["errors"].append(f"市价单失败: {market_error}")
                return result

            # 2. 下止损单（优先，因为止损是保护机制）
            sl_success = True
            if stop_loss_price:
                sl_result = self.place_tpsl_order(
                    symbol=symbol,
                    trigger_price=stop_loss_price,
                    is_buy=not is_buy,  # 平仓方向相反
                    size=size,
                    is_tp=False,
                )
                result["stop_loss_order"] = sl_result
                sl_success, sl_error = self.check_order_success(sl_result)
                if not sl_success:
                    error_msg = f"止损单失败: {sl_error}"
                    if "request_params" in sl_result:
                        error_msg += f"\n请求参数: {sl_result['request_params']}"
                    result["errors"].append(error_msg)

                    # 【关键安全机制】止损单失败，立即平仓避免裸仓（带退避重试 + 全平兜底 + 失败告警）
                    if require_stop_loss:
                        print("⚠️ 【安全机制】止损单设置失败，立即平仓避免裸仓风险")
                        cloud = get_cloud_logger()
                        if cloud:
                            cloud.send_risk_event(
                                symbol=symbol,
                                risk_type="stop_loss_failed_rollback",
                                details={
                                    "is_buy": is_buy,
                                    "size": size,
                                    "stop_loss_price": stop_loss_price,
                                    "sl_error": sl_error,
                                },
                                level="error",
                            )
                        rb_ok, rb_result = self._emergency_close_with_retry(
                            symbol, size, reason="止损单失败"
                        )
                        result["rollback_executed"] = True
                        result["rollback_result"] = rb_result
                        result["rollback_final_success"] = rb_ok
                        if rb_ok:
                            result["errors"].append("已自动平仓，避免裸仓风险")
                        else:
                            result["errors"].append(
                                "严重：自动平仓在重试+全平兜底后仍失败，请立即手动处理！"
                            )

                        return result

            # 3. 下止盈单（如果提供）
            tp_success = True
            if take_profit_price:
                tp_result = self.place_tpsl_order(
                    symbol=symbol,
                    trigger_price=take_profit_price,
                    is_buy=not is_buy,  # 平仓方向相反
                    size=size,
                    is_tp=True,
                )
                result["take_profit_order"] = tp_result
                tp_success, tp_error = self.check_order_success(tp_result)
                if not tp_success:
                    error_msg = f"止盈单失败: {tp_error}"
                    if "request_params" in tp_result:
                        error_msg += f"\n请求参数: {tp_result['request_params']}"
                    result["errors"].append(error_msg)
                    # 止盈单失败不平仓，但记录警告
                    print("⚠️ 止盈单设置失败，仓位仍有止损保护")

            # 判断整体成功
            # 止损必须成功（如果需要），止盈失败可以接受
            result["success"] = market_success and sl_success

            return result

        except Exception as e:
            result["errors"].append(f"异常: {str(e)}")
            # 发生异常时也尝试平仓（带退避重试 + 全平兜底 + 失败告警，与止损失败路径一致，
            # 修复原异常路径“重试后不校验是否成功、无 critical 告警”导致的静默裸仓）
            if result["market_order"] and self.check_order_success(result["market_order"])[0]:
                print("⚠️ 【安全机制】发生异常，尝试平仓避免裸仓风险")
                try:
                    rb_ok, rb_result = self._emergency_close_with_retry(
                        symbol, size, reason="下单异常"
                    )
                    result["rollback_executed"] = True
                    result["rollback_result"] = rb_result
                    result["rollback_final_success"] = rb_ok
                    if not rb_ok:
                        result["errors"].append(
                            "严重：异常平仓在重试+全平兜底后仍失败，请立即手动处理！"
                        )
                except Exception as rollback_e:
                    result["errors"].append(f"平仓异常: {rollback_e}")
                    result["rollback_final_success"] = False
                    print(f"⚠️ 异常平仓再异常：{rollback_e}")
            return result

    def place_tpsl_order(
        self,
        symbol: str,
        trigger_price: float,
        is_buy: bool,
        size: float,
        is_tp: bool = True,
        sl_slippage: float = 0.10,  # 止损滑点提高到10%确保成交
    ) -> dict[str, Any]:
        """
        下止盈或止损单

        【重要改进】：
        - 止损单使用更大滑点(10%)确保在极端行情下能成交
        - 止盈单使用较小滑点(1%)获取更好价格

        Args:
            symbol: 交易对符号
            trigger_price: 触发价格
            is_buy: True=买入，False=卖出
            size: 数量
            is_tp: True=止盈单，False=止损单
            sl_slippage: 止损滑点比例（默认10%）

        Returns:
            订单结果
        """
        try:
            # 确保 trigger_price 是 float 类型
            trigger_price = float(trigger_price)

            # 格式化触发价格，避免精度问题
            trigger_price = self.format_price(symbol, trigger_price)

            # 格式化数量，根据交易对的 szDecimals 精度要求
            asset_info = self.get_asset_info(symbol)
            if asset_info and "szDecimals" in asset_info:
                decimals = asset_info["szDecimals"]
                size = float(round(size, decimals))
            else:
                size = float(round(size, 3))

            # 确保 trigger_price 是 float 类型
            trigger_price_float = float(trigger_price)

            # 构造触发单类型
            order_type = {
                "trigger": {
                    "isMarket": True,
                    "triggerPx": trigger_price_float,
                    "tpsl": "tp" if is_tp else "sl",
                }
            }

            # 计算限价（止盈止损单的备用限价）
            #
            # 【关键改进】止损单需要更大滑点确保在极端行情下成交
            # - 止盈(TP)：1% 滑点，追求更好价格
            # - 止损(SL)：10% 滑点，确保一定能成交（防止穿仓）
            #
            # 限价方向说明：
            # - 止损卖出：limit < trigger（接受更低价格）
            # - 止损买入：limit > trigger（接受更高价格）
            # - 止盈卖出：limit > trigger（要求更高价格）
            # - 止盈买入：limit < trigger（要求更低价格）

            if is_tp:
                slippage = 0.01  # 止盈：1% 滑点
            else:
                slippage = sl_slippage  # 止损：使用配置的滑点（默认10%）

            # 根据订单类型和平仓方向计算限价
            if is_tp:
                # 止盈：限价朝有利方向（要求更好的价格）
                if not is_buy:
                    # 卖出平仓（做多止盈）：限价高于触发价
                    limit_price = trigger_price_float * (1 + slippage)
                else:
                    # 买入平仓（做空止盈）：限价低于触发价
                    limit_price = trigger_price_float * (1 - slippage)
            else:
                # 止损：限价朝不利方向（接受更差的价格以确保成交）
                if not is_buy:
                    # 卖出平仓（做多止损）：限价远低于触发价
                    limit_price = trigger_price_float * (1 - slippage)
                else:
                    # 买入平仓（做空止损）：限价远高于触发价
                    limit_price = trigger_price_float * (1 + slippage)

            # 格式化限价，避免精度问题
            limit_price = self.format_price(symbol, limit_price)

            # 下单
            order_result = self._request_with_fallback(
                "order",
                symbol,
                is_buy,
                size,
                limit_price,
                order_type,
                reduce_only=True,
                is_exchange=True,
            )

            # 如果失败，添加请求参数到结果中便于调试
            if order_result and order_result.get("status") != "ok":
                order_result["request_params"] = {
                    "symbol": symbol,
                    "is_buy": is_buy,
                    "size": size,
                    "limit_price": limit_price,
                    "trigger_price": trigger_price_float,
                    "order_type": order_type,
                    "reduce_only": True,
                    "is_tp": is_tp,
                    "slippage": slippage,
                }

            return order_result

        except Exception as e:
            print(f"❌ 下止盈止损单失败: {e}")
            traceback.print_exc()
            return {
                "status": "error",
                "message": str(e),
                "request_params": {
                    "symbol": symbol,
                    "trigger_price": trigger_price,
                    "is_buy": is_buy,
                    "size": size,
                    "is_tp": is_tp,
                },
            }

    def cancel_order(self, symbol: str, oid: int) -> dict[str, Any]:
        """
        取消订单

        Args:
            symbol: 交易对符号
            oid: 订单ID

        Returns:
            取消结果
        """
        try:
            cancel_result = self._request_with_fallback("cancel", symbol, oid, is_exchange=True)
            return cancel_result
        except Exception as e:
            print(f"❌ 取消订单失败: {e}")
            return {"status": "error", "message": str(e)}

    def update_leverage(self, symbol: str, leverage: int, is_cross: bool = True) -> dict[str, Any]:
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
                max_leverage = asset_info.get("maxLeverage", 50)
                if leverage > max_leverage:
                    error_msg = f"杠杆 {leverage}x 超过 {symbol} 的最大杠杆 {max_leverage}x"
                    print(f"❌ {error_msg}")
                    return {"status": "error", "message": error_msg}

            # 如果是逐仓模式，检查当前持仓的杠杆
            if not is_cross:
                positions = self.get_positions()
                for position in positions:
                    if position.get("coin") == symbol:
                        # 获取当前持仓的杠杆
                        leverage_info = position.get("leverage", {})
                        if isinstance(leverage_info, dict):
                            current_leverage = int(leverage_info.get("value", 1))
                        else:
                            current_leverage = int(leverage_info) if leverage_info else 1

                        # 如果目标杠杆低于当前杠杆，可能需要更多保证金
                        if leverage < current_leverage:
                            print(f"⚠️ 当前持仓杠杆: {current_leverage}x, 目标杠杆: {leverage}x")
                            print("⚠️ 降低杠杆可能需要增加保证金，如果失败将使用当前杠杆")

                            # 尝试设置杠杆
                            result = self._request_with_fallback(
                                "update_leverage",
                                leverage,
                                symbol,
                                is_cross=is_cross,
                                is_exchange=True,
                            )

                            # 如果失败，返回特殊状态，允许使用当前杠杆
                            if result.get("status") == "err":
                                error_msg = result.get("response", "未知错误")
                                if (
                                    "sufficient margin" in error_msg.lower()
                                    or "decrease leverage" in error_msg.lower()
                                ):
                                    print(
                                        f"⚠️ 降低杠杆失败（保证金不足），将使用当前杠杆 {current_leverage}x"
                                    )
                                    return {
                                        "status": "warning",
                                        "message": f"无法降低杠杆（需要更多保证金），使用当前杠杆 {current_leverage}x",
                                        "current_leverage": current_leverage,
                                        "target_leverage": leverage,
                                    }
                                else:
                                    print(f"❌ 杠杆设置失败: {error_msg}")
                                    return {"status": "error", "message": error_msg}

                            return result
                        # 如果目标杠杆高于或等于当前杠杆，直接设置
                        elif leverage > current_leverage:
                            print(f"ℹ️ 当前持仓杠杆: {current_leverage}x, 提高至: {leverage}x")
                        else:
                            print(f"ℹ️ 当前持仓杠杆: {current_leverage}x, 保持不变")
                            return {"status": "ok", "message": f"杠杆已为 {leverage}x，无需更改"}

            # 设置杠杆
            result = self._request_with_fallback(
                "update_leverage", leverage, symbol, is_cross=is_cross, is_exchange=True
            )

            # 验证结果
            if result.get("status") == "err":
                error_msg = result.get("response", "未知错误")
                print(f"❌ 杠杆设置失败: {error_msg}")
                return {"status": "error", "message": error_msg}

            return result
        except Exception as e:
            print(f"❌ 更新杠杆失败: {e}")
            return {"status": "error", "message": str(e)}

    def get_candles(
        self,
        symbol: str,
        interval: str = "15m",
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[dict[str, Any]] | None:
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
            candles = self._request_with_fallback(
                "candles_snapshot",
                coin=symbol,
                interval=interval,
                startTime=start_time,
                endTime=end_time,
            )
            return candles
        except Exception as e:
            print(f"❌ 获取K线数据失败: {e}")
            return None

    def check_api_wallet_authorization(self) -> dict[str, Any]:
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
            "is_api_wallet_mode": self.is_api_wallet_mode,
            "is_authorized": False,
            "main_wallet": self.address,
            "api_wallet": self.account.address,
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
            print("\n🔍 检查 API 钱包授权状态")
            print(f"   主钱包: {self.address}")
            print(f"   API 钱包: {self.account.address}")

            # 由于 Hyperliquid API 的授权检查方式可能因版本而异
            # 这里提供一个基本的检查逻辑
            # 实际的授权检查可能需要根据 API 文档调整

            # 尝试获取授权列表（如果 API 支持）
            if "authorizedAgents" in user_state:
                agents = user_state.get("authorizedAgents", [])
                is_authorized = self.account.address.lower() in [a.lower() for a in agents]
                result["is_authorized"] = is_authorized
                print(f"   授权状态: {'✅ 已授权' if is_authorized else '❌ 未授权'}")
                return result

            # 如果没有明确的授权列表，尝试通过测试交易来判断
            # 但这里我们只做查询，不做实际交易
            print("   ⚠️  无法直接查询授权状态")
            print("   💡 建议: 在主钱包中授权 API 钱包，或进行测试交易验证")

        except Exception as e:
            print(f"❌ 检查授权失败: {e}")

        return result

    def _emergency_close_with_retry(
        self,
        symbol: str,
        size: float,
        *,
        reason: str,
        max_retries: int = 3,
    ) -> tuple[bool, dict[str, Any] | None]:
        """紧急平仓（止损单失败/下单异常后避免裸仓的统一兜底）。

        策略：
        1. 按 size 重试部分平仓，尝试间指数退避（0.5s/1s/2s），避免无间隔连发
           被交易所限流/判重，反而降低成功率；
        2. 多次失败后退一步用 market_close 全平兜底——部分平仓依赖 get_positions
           查询持仓方向，行情/网络抖动导致查询为空会一直失败，全平不依赖该查询；
        3. 仍失败则发 critical 风控告警，由人工介入，绝不静默放过裸仓。

        Args:
            symbol: 交易对符号
            size: 期望平掉的数量
            reason: 触发原因（用于日志/告警）
            max_retries: 部分平仓最大重试次数

        Returns:
            (是否最终平仓成功, 最后一次平仓结果)
        """
        last_result: dict[str, Any] | None = None
        last_error: str | None = None

        for attempt in range(1, max_retries + 1):
            if attempt > 1:
                # 0.5s, 1s, 2s... 指数退避（上限 2s）
                time.sleep(min(2.0, 0.5 * (2 ** (attempt - 2))))
            print(f"➡️ 紧急平仓第 {attempt}/{max_retries} 次尝试（{reason}）")
            last_result = self.close_position(symbol, size)
            ok, last_error = self.check_order_success(last_result)
            if ok:
                print(f"✅ 紧急平仓成功（第 {attempt} 次，{reason}）")
                return True, last_result
            print(f"⚠️ 紧急平仓尝试失败（第 {attempt} 次）：{last_error}")

        # 全平兜底：按 size 多次失败（常因 get_positions 查不到持仓），改用市价全平
        print(f"➡️ 紧急平仓改用市价全平兜底（{reason}）")
        last_result = self.close_position(symbol, None)
        ok, last_error = self.check_order_success(last_result)
        if ok:
            print(f"✅ 市价全平兜底成功（{reason}）")
            return True, last_result

        critical_msg = (
            f"紧急平仓在 {max_retries} 次重试+全平兜底后仍失败: {last_error}，请立即手动处理！"
        )
        print(f"❌ 【严重】{critical_msg}")
        cloud = get_cloud_logger()
        if cloud:
            cloud.send_risk_event(
                symbol=symbol,
                risk_type="emergency_close_failed_critical",
                details={
                    "size": size,
                    "reason": reason,
                    "last_error": str(last_error),
                    "message": critical_msg,
                },
                level="error",
            )
        return False, last_result

    def close_position(self, symbol: str, size: float | None = None) -> dict[str, Any]:
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
                result = self._request_with_fallback("market_close", symbol, is_exchange=True)
                return result
            else:
                # 部分平仓 - 需要判断持仓方向
                positions = self.get_positions()
                position = next((p for p in positions if p["coin"] == symbol), None)

                if not position:
                    return {"status": "error", "message": f"没有 {symbol} 的持仓"}

                # 获取持仓数量和方向
                position_size = float(position["szi"])
                if position_size == 0:
                    return {"status": "error", "message": f"{symbol} 仓位为 0"}

                # 判断平仓方向：持仓为正(多仓)则卖出平仓，持仓为负(空仓)则买入平仓
                is_buy = position_size < 0
                close_size = abs(float(size))

                print(f"🔴 市价部分平仓 {symbol}: {'买入' if is_buy else '卖出'} {close_size}")

                # 使用官方 market_open 方法，配合 1% 滑点
                result = self._request_with_fallback(
                    "market_open",
                    symbol,
                    is_buy,
                    close_size,
                    None,  # px=None 使用市价
                    0.01,  # 1% 滑点（官方推荐，比原来的5%更合理）
                    is_exchange=True,
                )

                return result

        except Exception as e:
            print(f"❌ 平仓失败: {e}")
            return {"status": "error", "message": str(e)}
