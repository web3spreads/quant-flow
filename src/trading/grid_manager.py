"""
网格交易管理器 (动态调节版)
支持网格同步、AI 止盈止损和状态持久化
"""

import json
import os
import time
from contextlib import suppress
from typing import Any

from src.trading.order_manager import OrderManager
from src.utils.grid_math import extract_order_id
from src.utils.logger import TradingLogger

DEFAULT_MIN_ORDERS = 4
DEFAULT_AMOUNT_PER_ORDER = 10.0
DEFAULT_GRID_NUM = 10
DEFAULT_GRID_TYPE = "GEOMETRIC"
DEFAULT_GRID_REBUILD_COOLDOWN_SECONDS = 900
DEFAULT_GRID_REBUILD_MIN_PRICE_CHANGE_RATIO = 0.004
DEFAULT_GRID_REBUILD_MIN_OPEN_ORDERS = 2

EXIT_MIN_ORDERS = 3
EXIT_MAX_ORDERS = 8
EXIT_TARGET_COVERAGE_RATIO = 1.0
EXIT_PRICE_STEPS = [0.004, 0.008, 0.012, 0.016, 0.024, 0.032, 0.040, 0.050]


class GridManager:
    """管理网格订单的动态同步"""

    def __init__(
        self,
        order_manager: OrderManager,
        logger: TradingLogger,
        state_file: str = "grid_state.json",
        notifier=None,
        grid_limit_order_take_profit_enabled: bool = True,
        grid_limit_order_stop_loss_enabled: bool = True,
        grid_reduce_only_exit_orders_enabled: bool = True,
        grid_rebuild_cooldown_seconds: int = DEFAULT_GRID_REBUILD_COOLDOWN_SECONDS,
        grid_rebuild_min_price_change_ratio: float = DEFAULT_GRID_REBUILD_MIN_PRICE_CHANGE_RATIO,
    ):
        self.order_manager = order_manager
        self.logger = logger
        self.notifier = notifier
        self.state_file = state_file
        self.grid_limit_order_take_profit_enabled = bool(grid_limit_order_take_profit_enabled)
        self.grid_limit_order_stop_loss_enabled = bool(grid_limit_order_stop_loss_enabled)
        self.grid_reduce_only_exit_orders_enabled = bool(grid_reduce_only_exit_orders_enabled)
        self.grid_rebuild_cooldown_seconds = max(
            0,
            int(
                self._safe_float(
                    grid_rebuild_cooldown_seconds, DEFAULT_GRID_REBUILD_COOLDOWN_SECONDS
                )
            ),
        )
        self.grid_rebuild_min_price_change_ratio = max(
            0.0,
            self._safe_float(
                grid_rebuild_min_price_change_ratio,
                DEFAULT_GRID_REBUILD_MIN_PRICE_CHANGE_RATIO,
            ),
        )
        self.state = self._load_state()

    def _load_state(self) -> dict[str, Any]:
        if os.path.exists(self.state_file):
            with open(self.state_file, encoding="utf-8") as f:
                try:
                    data = json.load(f)
                    if "active_grids" not in data:
                        data = {"active_grids": {}}
                    return data
                except Exception:
                    pass
        return {"active_grids": {}}

    def _save_state(self):
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2)

    def update_grid(
        self,
        symbol: str,
        market_data: dict[str, Any],
        agent: Any,
        trends: dict[str, str] = None,
    ):
        """
        统一入口：获取状态 -> AI 决策 -> 同步网格
        """
        summary = self.get_grid_summary(symbol)
        ai_config = agent.make_decision(market_data, trends, summary)
        self.sync_grid(symbol, ai_config)

    def sync_grid(self, symbol: str, ai_config: dict[str, Any]):
        """
        核心逻辑：根据 AI 最新的决策，同步现实中的网格状态。
        """
        # 周期性清理孤儿 trigger 单（如历史遗留 TPSL），防止主网订单长期累积
        self._cleanup_orphan_trigger_orders(symbol)

        action = ai_config.get("action")
        if action != "UPDATE_GRID":
            # AI 不更新网格时，只保底减仓保护单，不再补基础开仓单
            self.logger.print_section(f"🛡️ 减仓保底模式 - {symbol}", style="bold yellow")
            if self.grid_reduce_only_exit_orders_enabled:
                self.logger.print_info("AI 未触发 UPDATE_GRID，本轮仅检查减仓保护单（reduce_only）")
                self._ensure_min_orders(symbol=symbol)
            else:
                self.logger.print_info("AI 未触发 UPDATE_GRID，且已关闭分批减仓单补齐")
            return

        # 兼容两种格式：参数在根目录或在 parameters 下
        params = ai_config.get("parameters", ai_config)
        new_lower = params.get("lower_price")
        new_upper = params.get("upper_price")
        new_num = params.get("grid_num", DEFAULT_GRID_NUM)
        # 增加安全检查
        try:
            new_num = int(new_num)
            if new_num <= 0:
                new_num = DEFAULT_GRID_NUM
        except (TypeError, ValueError):
            new_num = DEFAULT_GRID_NUM

        new_amount = params.get("amount_per_grid")
        tp_ratio = params.get("tp_ratio")
        sl_ratio = params.get("sl_ratio")

        if new_lower is None or new_upper is None or new_amount is None:
            self.logger.print_error(
                f"   [Grid] ❌ 配置缺失: lower={new_lower}, upper={new_upper}, amount={new_amount}"
            )
            return

        # 防止 AI 抽风输出 -1
        if new_upper <= 0 or new_lower <= 0:
            self.logger.print_error(f"   [Grid] ❌ 非法价格区间: ${new_lower} - ${new_upper}")
            return

        self.logger.print_section(f"🔄 动态调整 {symbol} 网格", style="bold cyan")
        self.logger.print_info(
            f"AI 新区间: ${new_lower} - ${new_upper} | TP: {tp_ratio} SL: {sl_ratio}"
        )

        should_rebuild, skip_reason = self._should_rebuild_grid(symbol=symbol, new_config=ai_config)
        if not should_rebuild:
            self.logger.print_info(f"   [Grid] ⏸️ 跳过重建: {skip_reason}")
            if self.grid_reduce_only_exit_orders_enabled:
                self._ensure_min_orders(symbol=symbol)
            return

        # 1. 彻底清理旧订单
        self._cancel_all_orders(symbol)

        # 撤单后轮询确认挂单清空，若仍残留则停止本轮重建，避免新旧订单叠加
        remaining_orders = self._drain_open_orders_before_rebuild(symbol=symbol)
        if remaining_orders:
            self.logger.print_warning(
                f"   [Grid] ⚠️ 撤单后仍有 {len(remaining_orders)} 个挂单残留，跳过本轮重建"
            )
            self._sync_local_state_with_orders(symbol, remaining_orders)
            return

        # 2. 计算新价格分布
        prices = self._calculate_grid_prices(
            new_lower,
            new_upper,
            new_num,
            ai_config.get("grid_type", DEFAULT_GRID_TYPE),
        )
        current_price = self.order_manager.client.get_current_price(symbol)

        buy_orders = []
        sell_orders = []

        # 3. 重新布置
        for i, p in enumerate(prices):
            if i > 0:
                time.sleep(1.0)  # 防限流

            try:
                if p < current_price:
                    res = self.order_manager.execute_long_limit(
                        symbol,
                        new_amount,
                        p,
                        tp_ratio=tp_ratio,
                        sl_ratio=sl_ratio,
                        with_take_profit=self.grid_limit_order_take_profit_enabled,
                        with_stop_loss=self.grid_limit_order_stop_loss_enabled,
                    )
                    if res and res.get("success"):
                        oid = self._extract_oid(res["limit_order"])
                        if oid:
                            buy_orders.append({"oid": oid, "px": p})
                            self.logger.print_info(f"   [Grid] ✅ 买单挂载: ${p}")
                    elif res and not res.get("success"):
                        self.logger.print_warning(
                            f"   [Grid] ⚠️ 买单跳过 @ ${p}: {res.get('message', 'unknown')}"
                        )
                elif p > current_price:
                    res = self.order_manager.execute_short_limit(
                        symbol,
                        new_amount,
                        p,
                        tp_ratio=tp_ratio,
                        sl_ratio=sl_ratio,
                        with_take_profit=self.grid_limit_order_take_profit_enabled,
                        with_stop_loss=self.grid_limit_order_stop_loss_enabled,
                    )
                    if res and res.get("success"):
                        oid = self._extract_oid(res["limit_order"])
                        if oid:
                            sell_orders.append({"oid": oid, "px": p})
                            self.logger.print_info(f"   [Grid] ✅ 卖单挂载: ${p}")
                    elif res and not res.get("success"):
                        self.logger.print_warning(
                            f"   [Grid] ⚠️ 卖单跳过 @ ${p}: {res.get('message', 'unknown')}"
                        )
            except Exception as e:
                self.logger.print_error(f"   [Grid] 下单异常 @ ${p}: {e}")

        # 4. 更新状态
        self.state["active_grids"][symbol] = {
            "config": ai_config,
            "buy_orders": buy_orders,
            "sell_orders": sell_orders,
            "last_sync": time.time(),
        }
        self._save_state()
        self.logger.print_info(f"✅ {symbol} 网格调整完成。")

        # 无论 AI 如何，始终检查减仓保护单（不强制补基础开仓单）
        if self.grid_reduce_only_exit_orders_enabled:
            self._ensure_min_orders(symbol=symbol)

        # 发送通知
        if self.notifier:
            self.notifier.notify_grid_update(
                symbol=symbol,
                lower=new_lower,
                upper=new_upper,
                num=new_num,
                amount=new_amount,
                tp=tp_ratio,
                sl=sl_ratio,
                buy_count=len(buy_orders),
                sell_count=len(sell_orders),
                reason=ai_config.get("reason", "N/A"),
            )

    def _extract_grid_params(self, config: dict[str, Any]) -> dict[str, Any]:
        payload = config or {}
        params = payload.get("parameters", payload)
        grid_type = str(
            payload.get("grid_type", params.get("grid_type", DEFAULT_GRID_TYPE))
        ).upper()
        mode = str(payload.get("mode", params.get("mode", "NEUTRAL"))).upper()
        return {
            "lower_price": self._safe_float(params.get("lower_price"), 0.0),
            "upper_price": self._safe_float(params.get("upper_price"), 0.0),
            "grid_num": int(
                self._safe_float(params.get("grid_num", DEFAULT_GRID_NUM), DEFAULT_GRID_NUM)
            ),
            "amount_per_grid": self._safe_float(params.get("amount_per_grid"), 0.0),
            "grid_type": grid_type,
            "mode": mode,
        }

    def _has_sufficient_open_orders(self, symbol: str, min_orders: int) -> bool:
        open_orders = self._get_symbol_open_orders(symbol=symbol)
        return len(open_orders) >= max(min_orders, 0)

    def _should_rebuild_grid(self, symbol: str, new_config: dict[str, Any]) -> tuple[bool, str]:
        current_grid = self.state["active_grids"].get(symbol)
        if not current_grid:
            return True, "首次建网格"

        if not self._has_sufficient_open_orders(symbol, DEFAULT_GRID_REBUILD_MIN_OPEN_ORDERS):
            return True, "当前挂单数量不足，允许重建补网格"

        old_params = self._extract_grid_params(current_grid.get("config", {}))
        new_params = self._extract_grid_params(new_config)
        old_lower = old_params["lower_price"]
        old_upper = old_params["upper_price"]
        new_lower = new_params["lower_price"]
        new_upper = new_params["upper_price"]
        old_amount = old_params["amount_per_grid"]
        new_amount = new_params["amount_per_grid"]

        if min(old_lower, old_upper, new_lower, new_upper) <= 0:
            return True, "网格参数异常，强制重建"

        if (
            old_params["grid_num"] != new_params["grid_num"]
            or old_params["grid_type"] != new_params["grid_type"]
            or old_params["mode"] != new_params["mode"]
        ):
            return True, "网格结构变化（层数/类型/方向），需要重建"

        lower_change = abs(new_lower - old_lower) / max(abs(old_lower), 1e-9)
        upper_change = abs(new_upper - old_upper) / max(abs(old_upper), 1e-9)
        price_change = max(lower_change, upper_change)

        amount_change = 0.0
        if old_amount > 0 and new_amount > 0:
            amount_change = abs(new_amount - old_amount) / max(abs(old_amount), 1e-9)

        if price_change < self.grid_rebuild_min_price_change_ratio and amount_change < 0.20:
            return (
                False,
                f"区间变化 {price_change * 100:.3f}% / 单格资金变化 {amount_change * 100:.2f}% 低于阈值",
            )

        return True, "满足重建条件"

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _is_buy_side(order: dict[str, Any]) -> bool:
        side = str(order.get("side", "")).strip().upper()
        return side in {"B", "BUY", "BID"}

    @staticmethod
    def _is_sell_side(order: dict[str, Any]) -> bool:
        side = str(order.get("side", "")).strip().upper()
        return side in {"A", "ASK", "SELL"}

    @staticmethod
    def _is_trigger_order(order: dict[str, Any]) -> bool:
        order_type = order.get("orderType") or {}
        return isinstance(order_type, dict) and "trigger" in order_type

    def _get_symbol_open_orders(
        self,
        symbol: str,
        include_trigger: bool = False,
    ) -> list[dict[str, Any]]:
        try:
            try:
                orders = (
                    self.order_manager.client.get_open_orders(include_trigger=include_trigger) or []
                )
            except TypeError:
                # 向后兼容不支持 include_trigger 参数的客户端
                orders = self.order_manager.client.get_open_orders() or []
            return [o for o in orders if o.get("coin") == symbol]
        except Exception as e:
            self.logger.print_error(f"   [Grid] ❌ 查询 {symbol} 挂单失败: {e}")
            return []

    def _cancel_order_with_retry(
        self,
        symbol: str,
        oid: int,
        max_retries: int = 3,
        retry_delay_sec: float = 0.2,
    ) -> bool:
        for attempt in range(1, max_retries + 1):
            result = self.order_manager.client.cancel_order(symbol, oid)
            status = ""
            if isinstance(result, dict):
                status = str(result.get("status", "")).strip().lower()

            if status == "ok":
                return True

            self.logger.print_warning(
                f"   [Grid] ⚠️ 撤单失败 oid={oid} (第 {attempt}/{max_retries} 次): {result}"
            )
            if attempt < max_retries:
                time.sleep(retry_delay_sec)

        return False

    def _drain_open_orders_before_rebuild(
        self,
        symbol: str,
        max_rounds: int = 5,
        round_sleep_sec: float = 0.4,
    ) -> list[dict[str, Any]]:
        """重建前尽量把残留限价单撤净；超时后返回剩余订单。"""
        remaining_orders = self._get_symbol_open_orders(symbol=symbol)
        if not remaining_orders:
            return []

        for round_idx in range(1, max_rounds + 1):
            for order in remaining_orders:
                oid = order.get("oid")
                if oid is None:
                    continue
                self._cancel_order_with_retry(symbol, oid)

            if round_idx < max_rounds:
                time.sleep(round_sleep_sec)
            remaining_orders = self._get_symbol_open_orders(symbol=symbol)
            if not remaining_orders:
                if round_idx > 1:
                    self.logger.print_info(f"   [Grid] ✅ 残留挂单已清空（重试 {round_idx} 轮）")
                return []

        return remaining_orders

    def _cleanup_orphan_trigger_orders(self, symbol: str):
        """清理与当前持仓不匹配的 trigger 单（无仓或方向错误）。"""
        try:
            open_orders = self._get_symbol_open_orders(symbol, include_trigger=True)
            trigger_orders = [o for o in open_orders if self._is_trigger_order(o)]
            if not trigger_orders:
                return

            position_size = self._get_symbol_position_size(symbol)
            has_position = abs(position_size) > 0
            close_with_buy = position_size < 0  # 空仓平仓要买；多仓平仓要卖
            canceled = 0

            for order in trigger_orders:
                oid = order.get("oid")
                if oid is None:
                    continue

                should_cancel = (not has_position) or (self._is_buy_side(order) != close_with_buy)
                if not should_cancel:
                    continue

                if self._cancel_order_with_retry(symbol, oid):
                    canceled += 1

            if canceled:
                self.logger.print_warning(
                    f"   [Grid] 🧹 已清理孤儿 trigger 单 {canceled} 个（{symbol}）"
                )
        except Exception as e:
            self.logger.print_error(f"   [Grid] ❌ 清理孤儿 trigger 单失败: {e}")

    def _get_symbol_position_size(self, symbol: str) -> float:
        try:
            positions = self.order_manager.get_current_positions() or []
        except Exception as e:
            self.logger.print_error(f"   [Grid] ❌ 查询 {symbol} 持仓失败: {e}")
            return 0.0

        for position in positions:
            if position.get("coin") == symbol:
                return self._safe_float(position.get("szi"), 0.0)
        return 0.0

    def _get_size_step(self, symbol: str) -> float:
        try:
            asset_info = self.order_manager.client.get_asset_info(symbol) or {}
            sz_decimals = int(asset_info.get("szDecimals", 3))
            if sz_decimals < 0:
                sz_decimals = 3
            return 10 ** (-sz_decimals)
        except Exception:
            return 0.001

    def _build_order_snapshot(self, order: dict[str, Any]) -> dict[str, Any] | None:
        oid = order.get("oid")
        if oid is None:
            return None
        return {
            "oid": oid,
            "px": self._safe_float(order.get("limitPx"), 0.0),
        }

    def _sync_local_state_with_orders(self, symbol: str, open_orders: list[dict[str, Any]]):
        grid = self.state["active_grids"].get(symbol) or {}
        buy_orders: list[dict[str, Any]] = []
        sell_orders: list[dict[str, Any]] = []

        for order in open_orders:
            snapshot = self._build_order_snapshot(order)
            if not snapshot:
                continue
            if self._is_buy_side(order):
                buy_orders.append(snapshot)
            elif self._is_sell_side(order):
                sell_orders.append(snapshot)

        self.state["active_grids"][symbol] = {
            "config": grid.get("config", {}),
            "buy_orders": buy_orders,
            "sell_orders": sell_orders,
            "last_sync": time.time(),
        }
        self._save_state()

    @staticmethod
    def _append_order_cache(
        open_orders: list[dict[str, Any]],
        symbol: str,
        oid: int,
        side_code: str,
        limit_price: float,
        size: float = 0.0,
    ):
        open_orders.append(
            {
                "oid": oid,
                "coin": symbol,
                "side": side_code,
                "limitPx": str(limit_price),
                "sz": str(size),
            }
        )

    def _resync_state_with_exchange(self, symbol: str, open_orders: list[dict[str, Any]]):
        """以交易所真实挂单为准刷新本地状态。"""
        refreshed_open_orders = self._get_symbol_open_orders(symbol)
        if refreshed_open_orders:
            open_orders = refreshed_open_orders
        self._sync_local_state_with_orders(symbol, open_orders)

    def _ensure_position_exit_orders(
        self,
        symbol: str,
        current_price: float,
        open_orders: list[dict[str, Any]],
        min_exit_orders: int = EXIT_MIN_ORDERS,
        max_exit_orders: int = EXIT_MAX_ORDERS,
        target_coverage_ratio: float = EXIT_TARGET_COVERAGE_RATIO,
    ) -> list[dict[str, Any]]:
        """有持仓时，强制确保存在 reduce_only 减仓挂单。"""
        if not current_price or current_price <= 0:
            return open_orders

        position_size = self._get_symbol_position_size(symbol)
        if abs(position_size) <= 0:
            return open_orders

        close_with_buy = position_size < 0  # 空仓需要买单减仓；多仓需要卖单减仓
        if close_with_buy:
            exit_orders = [o for o in open_orders if self._is_buy_side(o)]
            side_name = "买"
        else:
            exit_orders = [o for o in open_orders if self._is_sell_side(o)]
            side_name = "卖"

        existing_count = len(exit_orders)
        if existing_count >= max_exit_orders:
            return open_orders

        covered_size = 0.0
        size_fields_found = False
        for order in exit_orders:
            sz = self._safe_float(order.get("sz"), 0.0)
            if sz > 0:
                size_fields_found = True
                covered_size += abs(sz)

        abs_position = abs(position_size)
        required_cover_size = abs_position * max(target_coverage_ratio, 0.0)
        size_step = self._get_size_step(symbol)
        if size_fields_found:
            coverage_ok = covered_size >= max(0.0, required_cover_size - size_step)
            if existing_count >= min_exit_orders and coverage_ok:
                return open_orders
        else:
            # 如果交易所返回里没有 sz 字段，只退化到“至少有分层减仓单”。
            if existing_count >= min_exit_orders:
                return open_orders

        if existing_count >= max_exit_orders:
            return open_orders

        placed = 0
        projected_covered = covered_size

        for step in EXIT_PRICE_STEPS:
            current_count = existing_count + placed
            count_ok = current_count >= min_exit_orders
            coverage_ok = projected_covered >= max(0.0, required_cover_size - size_step)
            if count_ok and coverage_ok:
                break

            if current_count >= max_exit_orders:
                break

            size_needed = max(required_cover_size - projected_covered, size_step)
            target_layers_left = max(min_exit_orders - current_count, 1)
            order_size = max(size_needed / target_layers_left, size_step)
            if order_size < size_step:
                break

            if close_with_buy:
                raw_price = current_price * (1 - step)
                side_code = "B"
            else:
                raw_price = current_price * (1 + step)
                side_code = "A"

            limit_price = self.order_manager.client.format_price(symbol, raw_price)
            result = self.order_manager.client.place_limit_order(
                symbol=symbol,
                is_buy=close_with_buy,
                size=order_size,
                price=limit_price,
                reduce_only=True,
            )

            if isinstance(result, dict) and result.get("status") == "ok":
                oid = self._extract_oid(result)
                if oid is not None:
                    self._append_order_cache(
                        open_orders=open_orders,
                        symbol=symbol,
                        oid=oid,
                        side_code=side_code,
                        limit_price=limit_price,
                        size=order_size,
                    )
                projected_covered += order_size
                placed += 1
                self.logger.print_warning(
                    f"   [Grid] 🛟 补减仓{side_name}单: {order_size:.6f} @ ${limit_price} (reduce_only)"
                )
            else:
                self.logger.print_warning(
                    f"   [Grid] ⚠️ 减仓{side_name}单失败 @ ${limit_price}: {result}"
                )

        return open_orders

    def _extract_oid(self, limit_order_res: dict[str, Any]) -> int | None:
        return extract_order_id(limit_order_res)

    def _calculate_grid_prices(
        self, lower: float, upper: float, num: int, grid_type: str
    ) -> list[float]:
        if num < 2:
            return [lower]
        # 确保输入是实数而非复数
        try:
            if hasattr(lower, "real"):
                lower = float(lower.real)
            if hasattr(upper, "real"):
                upper = float(upper.real)
        except Exception:
            pass

        prices = []
        if grid_type == "ARITHMETIC":
            diff = (upper - lower) / (num - 1)
            for i in range(num):
                prices.append(round(lower + i * diff, 1))
        else:  # GEOMETRIC
            # 增加安全检查
            if lower <= 0 or upper <= 0:
                return [lower]
            ratio = (upper / lower) ** (1 / (num - 1))
            for i in range(num):
                prices.append(round(lower * (ratio**i), 1))
        return prices

    def _cancel_all_orders(self, symbol: str):
        # 优先用交易所真实挂单清理（含 trigger），避免本地 state 漂移导致漏撤单
        canceled_oids = set()
        open_orders = self._get_symbol_open_orders(symbol, include_trigger=True)
        for order in open_orders:
            oid = order.get("oid")
            if oid is None:
                continue
            try:
                if self._cancel_order_with_retry(symbol, oid):
                    canceled_oids.add(oid)
            except Exception as e:
                self.logger.print_warning(f"   [Grid] ⚠️ 撤单异常 oid={oid}: {e}")

        # 回退：补撤 state 中仍记录但交易所列表里未返回的 oid
        grid = self.state["active_grids"].get(symbol)
        if grid:
            local_oids = [
                o.get("oid") for o in grid.get("buy_orders", []) if isinstance(o, dict)
            ] + [o.get("oid") for o in grid.get("sell_orders", []) if isinstance(o, dict)]
            for oid in local_oids:
                if oid is None or oid in canceled_oids:
                    continue
                with suppress(Exception):
                    if self._cancel_order_with_retry(symbol, oid):
                        canceled_oids.add(oid)

        if symbol in self.state["active_grids"]:
            del self.state["active_grids"][symbol]
            self._save_state()

    def _ensure_min_orders(
        self,
        symbol: str,
        min_orders: int = DEFAULT_MIN_ORDERS,
        amount_per_order: float = DEFAULT_AMOUNT_PER_ORDER,
    ):
        """保底逻辑：仅检查并补齐 reduce_only 减仓保护单。

        参数 min_orders/amount_per_order 保留仅为兼容旧调用，不再用于补基础开仓单。
        """
        try:
            _ = (min_orders, amount_per_order)  # 兼容保留参数，避免误删调用方
            current_price = self.order_manager.client.get_current_price(symbol)
            if not current_price or current_price <= 0:
                self.logger.print_warning(f"   [Grid] ⚠️ 无法获取 {symbol} 当前价格，跳过补单")
                return

            open_orders = self._get_symbol_open_orders(symbol)
            open_orders = self._ensure_position_exit_orders(
                symbol=symbol,
                current_price=current_price,
                open_orders=open_orders,
                min_exit_orders=EXIT_MIN_ORDERS,
                max_exit_orders=EXIT_MAX_ORDERS,
                target_coverage_ratio=EXIT_TARGET_COVERAGE_RATIO,
            )
            self._resync_state_with_exchange(symbol, open_orders)
        except Exception as e:
            self.logger.print_error(f"   [Grid] ❌ ensure_min_orders 失败: {e}")

    def get_grid_summary(self, symbol: str) -> str:
        grid = self.state["active_grids"].get(symbol)
        if not grid:
            return "目前无运行中的网格。"

        config = grid["config"]
        params = config.get("parameters", config)
        return (
            f"当前正在运行 {symbol} 天地单网格：\n"
            f"- 区间: ${params.get('lower_price', 'N/A')} - ${params.get('upper_price', 'N/A')}\n"
            f"- 止盈比例: {params.get('tp_ratio', 'N/A')}\n"
            f"- 待成交买单: {len(grid['buy_orders'])} 个\n"
            f"- 待成交卖单: {len(grid['sell_orders'])} 个"
        )
