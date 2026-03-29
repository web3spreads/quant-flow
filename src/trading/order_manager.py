"""
Hyperliquid 订单管理器
管理永续合约的订单创建、监控和执行，包括止盈止损逻辑
"""

import threading
from collections.abc import Callable
from datetime import datetime
from typing import Any

from src.trading.client import HyperliquidClient
from src.utils.cloud_logger import get_cloud_logger
from src.utils.grid_math import extract_order_id


class LimitOrderMonitor:
    """
    限价单成交监控器

    监控限价单成交状态，成交后自动设置止盈止损
    解决限价单成交后裸仓风险问题
    """

    def __init__(
        self,
        client: HyperliquidClient,
        check_interval: float = 5.0,
        max_check_duration: float = 3600.0,  # 最长监控1小时
    ):
        """
        初始化限价单监控器

        Args:
            client: Hyperliquid 客户端
            check_interval: 检查间隔（秒）
            max_check_duration: 最长监控时长（秒）
        """
        self.client = client
        self.check_interval = check_interval
        self.max_check_duration = max_check_duration

        # 待监控的限价单列表
        # {order_id: {symbol, is_buy, size, tp_price, sl_price, created_at, ...}}
        self._pending_orders: dict[int, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._monitor_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def add_order(
        self,
        order_id: int,
        symbol: str,
        is_buy: bool,
        size: float,
        entry_price: float,
        take_profit_price: float | None,
        stop_loss_price: float | None,
        on_tpsl_set: Callable | None = None,
    ) -> None:
        """
        添加限价单到监控列表

        Args:
            order_id: 订单ID
            symbol: 交易对
            is_buy: 是否做多
            size: 仓位大小
            entry_price: 入场价格
            take_profit_price: 止盈价格（可选）
            stop_loss_price: 止损价格（可选）
            on_tpsl_set: 止盈止损设置成功后的回调
        """
        with self._lock:
            self._pending_orders[order_id] = {
                "symbol": symbol,
                "is_buy": is_buy,
                "size": size,
                "entry_price": entry_price,
                "take_profit_price": take_profit_price,
                "stop_loss_price": stop_loss_price,
                "created_at": datetime.now(),
                "on_tpsl_set": on_tpsl_set,
                "tpsl_attempts": 0,
            }
            print(f"📋 限价单 {order_id} 已加入监控队列")

        # 确保监控线程运行
        self._ensure_monitor_running()

    def remove_order(self, order_id: int) -> None:
        """从监控列表移除订单"""
        with self._lock:
            if order_id in self._pending_orders:
                del self._pending_orders[order_id]
                print(f"📋 限价单 {order_id} 已从监控队列移除")

    def _ensure_monitor_running(self) -> None:
        """确保监控线程运行"""
        if self._monitor_thread is None or not self._monitor_thread.is_alive():
            self._stop_event.clear()
            self._monitor_thread = threading.Thread(
                target=self._monitor_loop, daemon=True, name="LimitOrderMonitor"
            )
            self._monitor_thread.start()
            print("🔄 限价单监控线程已启动")

    def stop(self) -> None:
        """停止监控"""
        self._stop_event.set()
        if self._monitor_thread:
            self._monitor_thread.join(timeout=10)
            print("🛑 限价单监控线程已停止")

    def _monitor_loop(self) -> None:
        """监控循环"""
        while not self._stop_event.is_set():
            try:
                self._check_orders()
            except Exception as e:
                print(f"❌ 限价单监控异常: {e}")

            # 如果没有待监控订单，退出循环
            with self._lock:
                if not self._pending_orders:
                    print("📋 无待监控限价单，监控线程休眠")
                    break

            # 等待下一次检查
            self._stop_event.wait(self.check_interval)

    def _check_orders(self) -> None:
        """检查所有待监控的限价单"""
        with self._lock:
            orders_to_check = list(self._pending_orders.items())

        # 获取当前挂单
        open_orders = self.client.get_open_orders()
        open_order_ids = {o.get("oid") for o in open_orders}

        # 批量获取持仓，减少 API 调用；设置 TPSL 后置空以触发刷新
        position_map: dict[str, Any] | None = None

        for order_id, order_info in orders_to_check:
            symbol = order_info["symbol"]
            created_at = order_info["created_at"]

            # 检查是否超时
            elapsed = (datetime.now() - created_at).total_seconds()
            if elapsed > self.max_check_duration:
                print(f"⏰ 限价单 {order_id} 监控超时，移除")
                self.remove_order(order_id)
                continue

            # 检查订单是否还在挂单中
            if order_id in open_order_ids:
                # 订单仍未成交，继续监控
                continue

            # 订单不在挂单中，说明已成交或已取消
            # 用订单记录的原始 size（而非总持仓），避免多单同时成交时超额设置止盈止损
            order_size = order_info["size"]
            is_buy = order_info["is_buy"]

            # 验证确实有对应方向的持仓（防止订单被取消后误操作）
            # 在循环内获取最新持仓，确保数据准确（持仓可能因前一个订单的 TPSL 设置而变化）
            if position_map is None:
                positions = self.client.get_positions()
                position_map = {p["coin"]: p for p in positions}
            position = position_map.get(symbol)

            if position:
                position_size = float(position.get("szi", 0))
                if (is_buy and position_size > 0) or (not is_buy and position_size < 0):
                    # 限价单成交，用订单原始 size 设置止盈止损
                    print(f"✅ 限价单 {order_id} 已成交，正在设置止盈止损 (size={order_size})")
                    self._set_tpsl_for_order(order_id, order_info, order_size)
                    # TPSL 设置可能影响持仓状态，下次循环需要刷新
                    position_map = None
                else:
                    # 持仓方向不匹配，订单可能被取消
                    print(f"⚠️ 限价单 {order_id} 持仓方向不匹配，可能已取消")
                    self.remove_order(order_id)
            else:
                # 无持仓，订单可能被取消或已平仓
                print(f"⚠️ 限价单 {order_id} 无对应持仓，移除监控")
                self.remove_order(order_id)

    def _set_tpsl_for_order(
        self, order_id: int, order_info: dict[str, Any], actual_size: float
    ) -> None:
        """为成交的限价单设置止盈止损"""
        symbol = order_info["symbol"]
        is_buy = order_info["is_buy"]
        tp_price = order_info["take_profit_price"]
        sl_price = order_info["stop_loss_price"]

        order_info["tpsl_attempts"] += 1
        max_attempts = 3

        try:
            # 先设置止损（更重要）
            sl_success = True
            if sl_price:
                sl_result = self.client.place_tpsl_order(
                    symbol=symbol,
                    trigger_price=sl_price,
                    is_buy=not is_buy,
                    size=actual_size,
                    is_tp=False,
                )
                sl_success, sl_error = self.client.check_order_success(sl_result)
                if not sl_success:
                    print(f"❌ 限价单 {order_id} 止损设置失败: {sl_error}")

                    # 重试或紧急平仓
                    if order_info["tpsl_attempts"] >= max_attempts:
                        print("⚠️ 【安全机制】止损设置多次失败，紧急平仓")
                        cloud = get_cloud_logger()
                        if cloud:
                            cloud.send_risk_event(
                                symbol=symbol,
                                risk_type="tpsl_failed_emergency_close",
                                details={
                                    "order_id": order_id,
                                    "attempts": max_attempts,
                                    "sl_price": sl_price,
                                    "actual_size": actual_size,
                                },
                                level="error",
                            )
                        self.client.close_position(symbol)
                        self.remove_order(order_id)
                        return
                    else:
                        # 稍后重试
                        return
                else:
                    print(f"✅ 限价单 {order_id} 止损已设置: ${sl_price}")

            # 设置止盈
            tp_success = True
            if tp_price:
                tp_result = self.client.place_tpsl_order(
                    symbol=symbol,
                    trigger_price=tp_price,
                    is_buy=not is_buy,
                    size=actual_size,
                    is_tp=True,
                )
                tp_success, tp_error = self.client.check_order_success(tp_result)
                if not tp_success:
                    print(f"⚠️ 限价单 {order_id} 止盈设置失败: {tp_error}")
                else:
                    print(f"✅ 限价单 {order_id} 止盈已设置: ${tp_price}")

            # 调用回调
            if sl_success:
                callback = order_info.get("on_tpsl_set")
                if callback:
                    try:
                        callback(order_id, sl_success and tp_success)
                    except Exception as e:
                        print(f"⚠️ 止盈止损回调异常: {e}")

                # 移除已处理的订单
                self.remove_order(order_id)

        except Exception as e:
            print(f"❌ 设置止盈止损异常: {e}")
            if order_info["tpsl_attempts"] >= max_attempts:
                print("⚠️ 【安全机制】异常次数过多，紧急平仓")
                try:
                    self.client.close_position(symbol)
                except Exception as close_err:
                    print(f"⚠️ 紧急平仓失败: {close_err}")
                self.remove_order(order_id)


class OrderManager:
    """Hyperliquid 订单管理器"""

    def __init__(
        self,
        client: HyperliquidClient,
        take_profit_ratio: float = 0.05,
        stop_loss_ratio: float = 0.02,
        default_leverage: int = 10,
        min_risk_reward_ratio: float = 1.5,  # 最小风险回报比
        enable_limit_order_monitor: bool = True,
    ):
        """
        初始化订单管理器

        Args:
            client: Hyperliquid 客户端
            take_profit_ratio: 止盈比例（默认 5%）
            stop_loss_ratio: 止损比例（默认 2%）
            default_leverage: 默认杠杆倍数（默认 10倍）
            min_risk_reward_ratio: 最小风险回报比（默认 1.5）
            enable_limit_order_monitor: 是否启用限价单监控（默认 True）
        """
        self.client = client
        self.take_profit_ratio = take_profit_ratio
        self.stop_loss_ratio = stop_loss_ratio
        self.default_leverage = default_leverage
        self.min_risk_reward_ratio = min_risk_reward_ratio

        # 初始化限价单监控器
        self.limit_order_monitor: LimitOrderMonitor | None = None
        if enable_limit_order_monitor:
            self.limit_order_monitor = LimitOrderMonitor(client)

        print("✅ 订单管理器初始化完成")
        print(f"   止盈比例: {take_profit_ratio * 100}%")
        print(f"   止损比例: {stop_loss_ratio * 100}%")
        print(f"   默认杠杆: {default_leverage}x")
        print(f"   最小风险回报比: {min_risk_reward_ratio}")
        print(f"   限价单监控: {'启用' if enable_limit_order_monitor else '禁用'}")

    def validate_risk_reward(
        self,
        take_profit_ratio: float,
        stop_loss_ratio: float,
        leverage: int,
        fee_rate: float = 0.0005,
    ) -> dict[str, Any]:
        """
        验证风险回报比是否合理（考虑杠杆）

        【重要】这是防止亏损的关键检查
        - 杠杆会放大止损的实际损失
        - 手续费会侵蚀利润

        Args:
            take_profit_ratio: 止盈比例（如 0.05 = 5%）
            stop_loss_ratio: 止损比例（如 0.02 = 2%）
            leverage: 杠杆倍数
            fee_rate: 单边手续费率（如 0.0005 = 0.05%）

        Returns:
            {
                'is_valid': bool,
                'risk_reward_ratio': float,  # 实际风险回报比
                'profit_after_fees': float,  # 扣除手续费后的实际利润率
                'loss_with_leverage': float,  # 考虑杠杆后的实际损失率
                'message': str
            }
        """
        # 计算双边手续费（开仓+平仓）
        total_fee = fee_rate * 2 * leverage  # 手续费也被杠杆放大

        # 止盈时的实际收益（扣除手续费）
        profit_after_fees = (take_profit_ratio * leverage) - total_fee

        # 止损时的实际损失（考虑杠杆）
        loss_with_leverage = (stop_loss_ratio * leverage) + total_fee

        # 计算实际风险回报比
        if loss_with_leverage > 0:
            risk_reward_ratio = profit_after_fees / loss_with_leverage
        else:
            risk_reward_ratio = float("inf")

        # 判断是否合理
        is_valid = risk_reward_ratio >= self.min_risk_reward_ratio and profit_after_fees > 0

        if not is_valid:
            if profit_after_fees <= 0:
                message = f"止盈不足以覆盖手续费！利润率: {profit_after_fees * 100:.2f}%"
            else:
                message = f"风险回报比过低: {risk_reward_ratio:.2f} < {self.min_risk_reward_ratio}"
        else:
            message = f"风险回报比: {risk_reward_ratio:.2f}, 实际利润: {profit_after_fees * 100:.2f}%, 实际损失: {loss_with_leverage * 100:.2f}%"

        return {
            "is_valid": is_valid,
            "risk_reward_ratio": risk_reward_ratio,
            "profit_after_fees": profit_after_fees,
            "loss_with_leverage": loss_with_leverage,
            "message": message,
        }

    def calculate_safe_tpsl(
        self, leverage: int, fee_rate: float = 0.0005, target_risk_reward: float = 2.0
    ) -> dict[str, float]:
        """
        根据杠杆计算安全的止盈止损比例

        Args:
            leverage: 杠杆倍数
            fee_rate: 单边手续费率
            target_risk_reward: 目标风险回报比

        Returns:
            {'take_profit_ratio': float, 'stop_loss_ratio': float}
        """
        # 双边手续费
        total_fee = fee_rate * 2

        # 为了确保利润覆盖手续费，止盈至少需要 > 手续费/杠杆
        min_tp_ratio = total_fee * 2 / leverage  # 2倍手续费作为最小利润

        # 根据目标风险回报比计算止损
        # risk_reward = (tp * leverage - fee) / (sl * leverage + fee)
        # 假设 sl = tp / (target_risk_reward * 2)，简化计算

        # 使用保守的计算方式
        suggested_tp = max(self.take_profit_ratio, min_tp_ratio)

        # 根据风险回报比反推止损
        # profit = suggested_tp * leverage - total_fee
        # loss = suggested_sl * leverage + total_fee
        # ratio = profit / loss = target_risk_reward
        # suggested_sl = (profit / target_risk_reward - total_fee) / leverage

        profit = suggested_tp * leverage - total_fee * leverage
        suggested_sl = (profit / target_risk_reward - total_fee * leverage) / leverage

        # 确保止损在合理范围内
        suggested_sl = max(0.005, min(suggested_sl, 0.05))  # 0.5% - 5%

        return {"take_profit_ratio": suggested_tp, "stop_loss_ratio": suggested_sl}

    def shutdown(self) -> None:
        """关闭订单管理器，停止所有后台任务"""
        if self.limit_order_monitor:
            self.limit_order_monitor.stop()

    def get_available_balance(self) -> float:
        """
        获取可用余额（USD）

        Returns:
            可用余额 = 账户总价值 - 已占用保证金
        """
        balance = self.client.get_balance()
        if balance:
            total = balance["accountValue"]
            occupied = balance["totalMarginUsed"]
            # 可用余额 = 账户总价值 - 已占用保证金
            return total - occupied
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

    def get_available_balance_info(self) -> dict[str, Any]:
        """
        获取详细的余额信息

        Returns:
            {
                'status': 'ok' | 'error',
                'total': float,  # 总价值
                'occupied': float,  # 已占用
                'available': float,  # 可用
                'unrealized_pnl': float,  # 未实现盈亏
                'message': str
            }
        """
        try:
            balance = self.client.get_balance()
            if not balance:
                return {
                    "status": "error",
                    "message": "无法获取余额信息",
                    "total": 0,
                    "occupied": 0,
                    "available": 0,
                    "unrealized_pnl": 0,
                }

            total = balance["accountValue"]
            occupied = balance["totalMarginUsed"]
            # 可用余额 = 账户总价值 - 已占用保证金
            available = total - occupied

            # 计算未实现盈亏（从所有持仓汇总）
            unrealized_pnl = 0
            positions = self.client.get_positions()
            for position in positions:
                unrealized_pnl += float(position.get("unrealizedPnl", 0))

            return {
                "status": "ok",
                "total": total,
                "occupied": occupied,
                "available": available,
                "unrealized_pnl": unrealized_pnl,
                "message": f"总价值: ${total:.2f}, 可用: ${available:.2f}, 未实现盈亏: ${unrealized_pnl:+.2f}",
            }

        except Exception as e:
            return {
                "status": "error",
                "message": f"获取余额失败: {e}",
                "total": 0,
                "occupied": 0,
                "available": 0,
                "unrealized_pnl": 0,
            }

    def get_current_positions(self) -> list[dict[str, Any]]:
        """
        获取当前持仓列表

        Returns:
            持仓列表
        """
        return self.client.get_positions()

    def _get_latest_fill_info(self, symbol: str | None = None) -> dict[str, Any]:
        """
        获取最近一笔成交的交易哈希和成交价

        Args:
            symbol: 交易对符号（可选，用于筛选匹配的 fill）

        Returns:
            {"hash": str | None, "fill_price": float | None}
        """
        result: dict[str, Any] = {"hash": None, "fill_price": None}
        try:
            import time

            # 等待一小段时间确保订单已成交
            time.sleep(0.5)

            # 获取最近的fills
            user_address = self.client.address
            fills = self.client.info.user_fills(user_address)

            if fills:
                # 优先查找匹配 symbol 的最近 fill
                target_fill = None
                if symbol:
                    target_fill = next((f for f in fills if f.get("coin") == symbol), None)
                # 回退到最新的 fill
                if not target_fill:
                    target_fill = fills[0]

                result["hash"] = target_fill.get("hash")
                px = target_fill.get("px")
                if px is not None:
                    result["fill_price"] = float(px)

        except Exception as e:
            print(f"⚠️ 获取交易成交信息失败: {e}")

        return result

    def calculate_position_size(
        self, symbol: str, usdt_amount: float, leverage: int | None = None
    ) -> float | None:
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

            # 获取交易对的精度信息
            asset_info = self.client.get_asset_info(symbol)
            if asset_info and "szDecimals" in asset_info:
                # 根据交易对的精度要求格式化数量
                decimals = asset_info["szDecimals"]
                size = round(size, decimals)
                print(f"   数量精度: {decimals} 位小数 -> {size}")
            else:
                # 如果无法获取精度，默认保留 3 位小数
                size = round(size, 3)
                print(f"   数量精度: 默认 3 位小数 -> {size}")

            return size

        except Exception as e:
            print(f"❌ 计算仓位大小失败: {e}")
            return None

    def execute_long(
        self, symbol: str, usdt_amount: float, leverage: int | None = None, with_tpsl: bool = True
    ) -> dict[str, Any] | None:
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

            # 3. 设置杠杆（仅在无持仓时设置，避免降杠杆保证金不足的问题）
            lev = leverage if leverage else self.default_leverage

            # 检查是否已有该币种的持仓
            current_positions = self.get_current_positions()
            has_position = any(pos.get("coin") == symbol for pos in current_positions)

            if has_position:
                print(f"   ⚠️  检测到已有 {symbol} 持仓，跳过杠杆设置（使用现有杠杆）")
            else:
                print(f"   设置杠杆: {lev}x (逐仓模式)")
                leverage_result = self.client.update_leverage(symbol, lev, is_cross=False)

                # 检查杠杆设置结果
                if leverage_result.get("status") == "error":
                    print(f"❌ 杠杆设置失败: {leverage_result.get('message')}")
                    print("❌ 无法继续下单")
                    return None
                elif leverage_result.get("status") == "warning":
                    # 无法降低杠杆，但可以使用当前杠杆继续
                    current_lev = leverage_result.get("current_leverage", lev)
                    print(f"⚠️ {leverage_result.get('message')}")
                    print(f"   使用当前杠杆 {current_lev}x 继续下单")
                    lev = current_lev

            # 4. 计算止盈止损价格
            if with_tpsl:
                tp_price = current_price * (1 + self.take_profit_ratio)
                sl_price = current_price * (1 - self.stop_loss_ratio)

                # 格式化价格，避免精度问题
                tp_price = self.client.format_price(symbol, tp_price)
                sl_price = self.client.format_price(symbol, sl_price)

                print(f"   止盈价: ${tp_price:.2f} (+{self.take_profit_ratio * 100}%)")
                print(f"   止损价: ${sl_price:.2f} (-{self.stop_loss_ratio * 100}%)")

                # 下带 TP/SL 的订单
                result = self.client.place_order_with_tpsl(
                    symbol=symbol,
                    is_buy=True,
                    size=size,
                    take_profit_price=tp_price,
                    stop_loss_price=sl_price,
                )
            else:
                # 只下市价单
                market_order = self.client.place_market_order(symbol=symbol, is_buy=True, size=size)
                result = {
                    "success": market_order.get("status") == "ok",
                    "market_order": market_order,
                    "take_profit_order": None,
                    "stop_loss_order": None,
                    "errors": [] if market_order.get("status") == "ok" else [market_order],
                }

            # 添加交易信息到返回结果
            if result:
                result["quantity"] = size
                result["price"] = current_price
                result["leverage"] = lev
                # 获取交易哈希：下单后查询最近的fills
                fill_info = self._get_latest_fill_info(symbol)
                result["hash"] = fill_info["hash"] or ""

            return result

        except Exception as e:
            print(f"❌ 执行做多失败: {e}")
            return None

    def execute_short(
        self, symbol: str, usdt_amount: float, leverage: int | None = None, with_tpsl: bool = True
    ) -> dict[str, Any] | None:
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

            # 3. 设置杠杆（仅在无持仓时设置，避免降杠杆保证金不足的问题）
            lev = leverage if leverage else self.default_leverage

            # 检查是否已有该币种的持仓
            current_positions = self.get_current_positions()
            has_position = any(pos.get("coin") == symbol for pos in current_positions)

            if has_position:
                print(f"   ⚠️  检测到已有 {symbol} 持仓，跳过杠杆设置（使用现有杠杆）")
            else:
                print(f"   设置杠杆: {lev}x (逐仓模式)")
                leverage_result = self.client.update_leverage(symbol, lev, is_cross=False)

                # 检查杠杆设置结果
                if leverage_result.get("status") == "error":
                    print(f"❌ 杠杆设置失败: {leverage_result.get('message')}")
                    print("❌ 无法继续下单")
                    return None
                elif leverage_result.get("status") == "warning":
                    # 无法降低杠杆，但可以使用当前杠杆继续
                    current_lev = leverage_result.get("current_leverage", lev)
                    print(f"⚠️ {leverage_result.get('message')}")
                    print(f"   使用当前杠杆 {current_lev}x 继续下单")
                    lev = current_lev

            # 4. 计算止盈止损价格（做空时方向相反）
            if with_tpsl:
                # Use abs() to ensure price is always positive, even if ratio is misconfigured
                tp_price = current_price * abs(1 - self.take_profit_ratio)  # 下跌时止盈
                sl_price = current_price * (1 + self.stop_loss_ratio)  # 上涨时止损

                # 格式化价格，避免精度问题
                tp_price = self.client.format_price(symbol, tp_price)
                sl_price = self.client.format_price(symbol, sl_price)

                print(f"   止盈价: ${tp_price:.2f} (-{self.take_profit_ratio * 100}%)")
                print(f"   止损价: ${sl_price:.2f} (+{self.stop_loss_ratio * 100}%)")

                # 下带 TP/SL 的订单
                result = self.client.place_order_with_tpsl(
                    symbol=symbol,
                    is_buy=False,
                    size=size,
                    take_profit_price=tp_price,
                    stop_loss_price=sl_price,
                )
            else:
                # 只下市价单
                market_order = self.client.place_market_order(
                    symbol=symbol, is_buy=False, size=size
                )
                result = {
                    "success": market_order.get("status") == "ok",
                    "market_order": market_order,
                    "take_profit_order": None,
                    "stop_loss_order": None,
                    "errors": [] if market_order.get("status") == "ok" else [market_order],
                }

            # 添加交易信息到返回结果
            if result:
                result["quantity"] = size
                result["price"] = current_price
                result["leverage"] = lev
                # 获取交易哈希：下单后查询最近的fills
                fill_info = self._get_latest_fill_info(symbol)
                result["hash"] = fill_info["hash"] or ""

            return result

        except Exception as e:
            print(f"❌ 执行做空失败: {e}")
            return None

    def close_position(self, symbol: str, size: float | None = None) -> dict[str, Any] | None:
        """
        平仓操作

        Args:
            symbol: 交易对符号
            size: 平仓数量（None=全平）

        Returns:
            平仓结果（包含交易哈希和实际成交价）
        """
        try:
            result = self.client.close_position(symbol, size)

            # 如果平仓成功，获取交易哈希和实际成交价
            if result and result.get("status") == "ok":
                fill_info = self._get_latest_fill_info(symbol)
                result["hash"] = fill_info["hash"] or ""
                if fill_info["fill_price"] is not None:
                    result["fill_price"] = fill_info["fill_price"]

            return result
        except Exception as e:
            print(f"❌ 平仓失败: {e}")
            return None

    def calculate_suggested_trade_amount(
        self,
        desired_amount: float,
        min_trade_amount: float = 10.0,
        balance_info: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
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

            if balance_info["status"] != "ok":
                return {
                    "can_trade": False,
                    "suggested_amount": 0,
                    "reason": balance_info["message"],
                }

            available = balance_info["available"]

            # 检查是否有足够余额
            if available < min_trade_amount:
                return {
                    "can_trade": False,
                    "suggested_amount": 0,
                    "reason": f"可用余额 ${available:.2f} 低于最小交易金额 ${min_trade_amount:.2f}",
                }

            # 如果期望金额小于等于可用余额，直接使用
            if desired_amount <= available:
                return {
                    "can_trade": True,
                    "suggested_amount": desired_amount,
                    "reason": f"使用配置的交易金额 ${desired_amount:.2f}",
                }
            else:
                # 使用可用余额的一部分（留一些余量）
                suggested = available * 0.8  # 使用 80% 的可用余额
                if suggested >= min_trade_amount:
                    return {
                        "can_trade": True,
                        "suggested_amount": suggested,
                        "reason": f"可用余额不足，调整为 ${suggested:.2f} (可用余额的 80%)",
                    }
                else:
                    return {
                        "can_trade": False,
                        "suggested_amount": 0,
                        "reason": "可用余额不足，无法交易",
                    }

        except Exception as e:
            return {"can_trade": False, "suggested_amount": 0, "reason": f"计算建议金额失败: {e}"}

    def execute_long_limit(
        self,
        symbol: str,
        usdt_amount: float,
        limit_price: float,
        leverage: int | None = None,
        tp_ratio: float | None = None,
        sl_ratio: float | None = None,
        with_take_profit: bool = True,
        with_stop_loss: bool = True,
    ) -> dict[str, Any] | None:
        """
        执行限价开多操作（带止盈止损计算）

        Args:
            symbol: 交易对符号
            usdt_amount: 投入金额
            limit_price: 限价价格
            leverage: 杠杆倍数
            tp_ratio: 自定义止盈比例（覆盖默认值）
            sl_ratio: 自定义止损比例（覆盖默认值）
            with_take_profit: 是否启用止盈触发单
            with_stop_loss: 是否启用止损触发单

        Returns:
            订单信息（包含止盈止损价格）
        """
        try:
            # 1. 计算仓位大小
            lev = leverage if leverage else self.default_leverage

            # 合约数量 = (投入金额 * 杠杆) / 限价
            size = (usdt_amount * lev) / limit_price

            # 获取交易对的精度信息
            asset_info = self.client.get_asset_info(symbol)
            if asset_info and "szDecimals" in asset_info:
                decimals = asset_info["szDecimals"]
                size = round(size, decimals)
            else:
                size = round(size, 3)

            print(f"📈 限价开多 {symbol}: {size} 张合约 @ ${limit_price:.2f}")

            # 2. 仅在无持仓时设置杠杆（避免逐仓模式下有持仓时修改杠杆导致保证金问题）
            current_positions = self.get_current_positions()
            has_position = any(pos.get("coin") == symbol for pos in current_positions)

            if has_position:
                print(f"   ⚠️  检测到已有 {symbol} 持仓，跳过杠杆设置（使用现有杠杆）")
            else:
                print(f"   设置杠杆: {lev}x (逐仓模式)")
                leverage_result = self.client.update_leverage(symbol, lev, is_cross=False)
                if leverage_result.get("status") == "error":
                    print(f"❌ 杠杆设置失败: {leverage_result.get('message')}")
                    return None

            # 3. 计算止盈止损价格（基于限价单价格按百分比计算）
            tp_price = None
            sl_price = None
            actual_tp_ratio = tp_ratio if tp_ratio is not None else self.take_profit_ratio
            actual_sl_ratio = sl_ratio if sl_ratio is not None else self.stop_loss_ratio
            if with_take_profit:
                tp_price = self.client.format_price(symbol, limit_price * (1 + actual_tp_ratio))
                print(f"   止盈价: ${tp_price:.2f} (+{actual_tp_ratio * 100:.3f}%)")
            if with_stop_loss:
                sl_price = self.client.format_price(symbol, limit_price * (1 - actual_sl_ratio))
                print(f"   止损价: ${sl_price:.2f} (-{actual_sl_ratio * 100:.3f}%)")

            # 4. 下限价单
            limit_order = self.client.place_limit_order(
                symbol=symbol, is_buy=True, size=size, price=limit_price
            )

            if limit_order.get("status") == "ok":
                result = {
                    "success": True,
                    "limit_order": limit_order,
                    "quantity": size,
                    "price": limit_price,
                    "leverage": lev,
                    "take_profit_price": tp_price,
                    "stop_loss_price": sl_price,
                    "message": "限价单已提交，成交后将按配置自动设置风控单",
                }

                # 5. 注册到 LimitOrderMonitor，成交后自动设置止盈止损
                if self.limit_order_monitor and (with_take_profit or with_stop_loss):
                    order_id = extract_order_id(limit_order)
                    if order_id:
                        self.limit_order_monitor.add_order(
                            order_id=order_id,
                            symbol=symbol,
                            is_buy=True,
                            size=size,
                            entry_price=limit_price,
                            take_profit_price=tp_price,
                            stop_loss_price=sl_price,
                        )
                    else:
                        print("⚠️ 无法提取订单 ID，限价单监控未注册（限价单可能已立即成交）")

                return result
            else:
                print(f"❌ 限价单失败: {limit_order.get('message')}")
                return None

        except Exception as e:
            print(f"❌ 执行限价开多失败: {e}")
            return None

    def execute_short_limit(
        self,
        symbol: str,
        usdt_amount: float,
        limit_price: float,
        leverage: int | None = None,
        tp_ratio: float | None = None,
        sl_ratio: float | None = None,
        with_take_profit: bool = True,
        with_stop_loss: bool = True,
    ) -> dict[str, Any] | None:
        """
        执行限价开空操作（带止盈止损计算）

        Args:
            symbol: 交易对符号
            usdt_amount: 投入金额
            limit_price: 限价价格
            leverage: 杠杆倍数
            tp_ratio: 自定义止盈比例（覆盖默认值）
            sl_ratio: 自定义止损比例（覆盖默认值）
            with_take_profit: 是否启用止盈触发单
            with_stop_loss: 是否启用止损触发单

        Returns:
            订单信息（包含止盈止损价格）
        """
        try:
            # 1. 计算仓位大小
            lev = leverage if leverage else self.default_leverage

            # 合约数量 = (投入金额 * 杠杆) / 限价
            size = (usdt_amount * lev) / limit_price

            # 获取交易对的精度信息
            asset_info = self.client.get_asset_info(symbol)
            if asset_info and "szDecimals" in asset_info:
                decimals = asset_info["szDecimals"]
                size = round(size, decimals)
            else:
                size = round(size, 3)

            print(f"📉 限价开空 {symbol}: {size} 张合约 @ ${limit_price:.2f}")

            # 2. 仅在无持仓时设置杠杆（避免逐仓模式下有持仓时修改杠杆导致保证金问题）
            current_positions = self.get_current_positions()
            has_position = any(pos.get("coin") == symbol for pos in current_positions)

            if has_position:
                print(f"   ⚠️  检测到已有 {symbol} 持仓，跳过杠杆设置（使用现有杠杆）")
            else:
                print(f"   设置杠杆: {lev}x (逐仓模式)")
                leverage_result = self.client.update_leverage(symbol, lev, is_cross=False)
                if leverage_result.get("status") == "error":
                    print(f"❌ 杠杆设置失败: {leverage_result.get('message')}")
                    return None

            # 3. 计算止盈止损价格（基于限价单价格按百分比计算）
            # 做空：止盈价 = 限价 * (1 - take_profit_ratio)，止损价 = 限价 * (1 + stop_loss_ratio)
            tp_price = None
            sl_price = None
            actual_tp_ratio = tp_ratio if tp_ratio is not None else self.take_profit_ratio
            actual_sl_ratio = sl_ratio if sl_ratio is not None else self.stop_loss_ratio
            if with_take_profit:
                tp_price = self.client.format_price(symbol, limit_price * (1 - actual_tp_ratio))
                print(f"   止盈价: ${tp_price:.2f} (-{actual_tp_ratio * 100:.3f}%)")
            if with_stop_loss:
                sl_price = self.client.format_price(symbol, limit_price * (1 + actual_sl_ratio))
                print(f"   止损价: ${sl_price:.2f} (+{actual_sl_ratio * 100:.3f}%)")

            # 4. 下限价单
            limit_order = self.client.place_limit_order(
                symbol=symbol, is_buy=False, size=size, price=limit_price
            )

            if limit_order.get("status") == "ok":
                result = {
                    "success": True,
                    "limit_order": limit_order,
                    "quantity": size,
                    "price": limit_price,
                    "leverage": lev,
                    "take_profit_price": tp_price,
                    "stop_loss_price": sl_price,
                    "message": "限价单已提交，成交后将按配置自动设置风控单",
                }

                # 5. 注册到 LimitOrderMonitor，成交后自动设置止盈止损
                if self.limit_order_monitor and (with_take_profit or with_stop_loss):
                    order_id = extract_order_id(limit_order)
                    if order_id:
                        self.limit_order_monitor.add_order(
                            order_id=order_id,
                            symbol=symbol,
                            is_buy=False,
                            size=size,
                            entry_price=limit_price,
                            take_profit_price=tp_price,
                            stop_loss_price=sl_price,
                        )
                    else:
                        print("⚠️ 无法提取订单 ID，限价单监控未注册（限价单可能已立即成交）")

                return result
            else:
                print(f"❌ 限价单失败: {limit_order.get('message')}")
                return None

        except Exception as e:
            print(f"❌ 执行限价开空失败: {e}")
            return None

    def get_open_limit_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """
        获取待处理的限价单列表

        Args:
            symbol: 交易对符号（可选，如果提供则只返回该交易对的限价单）

        Returns:
            格式化的限价单列表:
            [{
                'order_id': int,  # 订单ID
                'symbol': str,  # 交易对
                'side': str,  # 'buy' 或 'sell'
                'limit_price': float,  # 限价
                'size': float,  # 数量
                'current_price': float,  # 当前价格
                'price_diff_percent': float,  # 与当前价格的差距百分比
            }]
        """
        try:
            open_orders = self.client.get_open_orders()
            current_price_map = {}

            # 格式化限价单
            formatted_orders = []
            for order in open_orders:
                order_symbol = order.get("coin", "")

                # 如果指定了symbol，只返回该交易对的订单
                if symbol and order_symbol != symbol:
                    continue

                # 获取当前价格（缓存）
                if order_symbol not in current_price_map:
                    current_price = self.client.get_current_price(order_symbol)
                    current_price_map[order_symbol] = current_price
                else:
                    current_price = current_price_map[order_symbol]

                # 解析订单信息
                order_id = order.get("oid")
                limit_price = float(order.get("limitPx", 0))
                size = float(order.get("sz", 0))
                side = "buy" if order.get("side") == "B" else "sell"

                # 计算与当前价格的差距
                price_diff_percent = 0.0
                if current_price:
                    if side == "buy":
                        price_diff_percent = ((limit_price - current_price) / current_price) * 100
                    else:
                        price_diff_percent = ((current_price - limit_price) / current_price) * 100

                formatted_orders.append(
                    {
                        "order_id": order_id,
                        "symbol": order_symbol,
                        "side": side,
                        "limit_price": limit_price,
                        "size": size,
                        "current_price": current_price,
                        "price_diff_percent": price_diff_percent,
                    }
                )

            return formatted_orders

        except Exception as e:
            print(f"❌ 获取限价单列表失败: {e}")
            return []

    def cancel_limit_order(self, symbol: str, order_id: int) -> dict[str, Any]:
        """
        取消限价单

        Args:
            symbol: 交易对符号
            order_id: 订单ID

        Returns:
            取消结果
        """
        try:
            result = self.client.cancel_order(symbol, order_id)
            if result.get("status") == "ok":
                print(f"✅ 限价单 {order_id} 已取消")
                return {
                    "success": True,
                    "message": f"限价单 {order_id} 已成功取消",
                    "order_id": order_id,
                    "symbol": symbol,
                }
            else:
                error_msg = result.get("message", "未知错误")
                print(f"❌ 取消限价单失败: {error_msg}")
                return {
                    "success": False,
                    "message": f"取消限价单失败: {error_msg}",
                    "order_id": order_id,
                    "symbol": symbol,
                }
        except Exception as e:
            error_msg = f"取消限价单异常: {str(e)}"
            print(f"❌ {error_msg}")
            return {"success": False, "message": error_msg, "order_id": order_id, "symbol": symbol}
