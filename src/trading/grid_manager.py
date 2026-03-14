"""
网格交易管理器 (动态调节版)
支持网格同步、AI 止盈止损和状态持久化
"""

import time
import json
import os
from typing import Dict, Any, List, Optional
from src.trading.order_manager import OrderManager
from src.utils.logger import TradingLogger


DEFAULT_MIN_ORDERS = 4
DEFAULT_AMOUNT_PER_ORDER = 10.0
DEFAULT_GRID_NUM = 10
DEFAULT_GRID_TYPE = "GEOMETRIC"

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
    ):
        self.order_manager = order_manager
        self.logger = logger
        self.notifier = notifier
        self.state_file = state_file
        self.state = self._load_state()

    def _load_state(self) -> Dict[str, Any]:
        if os.path.exists(self.state_file):
            with open(self.state_file, "r", encoding="utf-8") as f:
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
        market_data: Dict[str, Any],
        agent: Any,
        trends: Dict[str, str] = None,
    ):
        """
        统一入口：获取状态 -> AI 决策 -> 同步网格
        """
        summary = self.get_grid_summary(symbol)
        ai_config = agent.make_decision(market_data, trends, summary)
        self.sync_grid(symbol, ai_config)

    def sync_grid(self, symbol: str, ai_config: Dict[str, Any]):
        """
        核心逻辑：根据 AI 最新的决策，同步现实中的网格状态。
        """
        action = ai_config.get("action")
        if action != "UPDATE_GRID":
            # AI 不更新网格时，只保底减仓保护单，不再补基础开仓单
            self.logger.print_section(f"🛡️ 减仓保底模式 - {symbol}", style="bold yellow")
            self.logger.print_info("AI 未触发 UPDATE_GRID，本轮仅检查减仓保护单（reduce_only）")
            self._ensure_min_orders(symbol=symbol)
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
        except:
            new_num = DEFAULT_GRID_NUM
            
        new_amount = params.get("amount_per_grid")
        tp_ratio = params.get("tp_ratio")
        sl_ratio = params.get("sl_ratio")

        if new_lower is None or new_upper is None or new_amount is None:
            self.logger.print_error(f"   [Grid] ❌ 配置缺失: lower={new_lower}, upper={new_upper}, amount={new_amount}")
            return

        # 防止 AI 抽风输出 -1
        if new_upper <= 0 or new_lower <= 0:
            self.logger.print_error(f"   [Grid] ❌ 非法价格区间: ${new_lower} - ${new_upper}")
            return

        self.logger.print_section(f"🔄 动态调整 {symbol} 网格", style="bold cyan")
        self.logger.print_info(f"AI 新区间: ${new_lower} - ${new_upper} | TP: {tp_ratio} SL: {sl_ratio}")

        # 1. 彻底清理旧订单
        self._cancel_all_orders(symbol)

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
            if i > 0: time.sleep(1.0) # 防限流

            try:
                if p < current_price:
                    res = self.order_manager.execute_long_limit(symbol, new_amount, p, tp_ratio=tp_ratio, sl_ratio=sl_ratio)
                    if res and res.get('success'):
                        oid = self._extract_oid(res['limit_order'])
                        if oid:
                            buy_orders.append({"oid": oid, "px": p})
                            self.logger.print_info(f"   [Grid] ✅ 买单挂载: ${p}")
                    elif res and not res.get('success'):
                        self.logger.print_warning(f"   [Grid] ⚠️ 买单跳过 @ ${p}: {res.get('message', 'unknown')}")
                elif p > current_price:
                    res = self.order_manager.execute_short_limit(symbol, new_amount, p, tp_ratio=tp_ratio, sl_ratio=sl_ratio)
                    if res and res.get('success'):
                        oid = self._extract_oid(res['limit_order'])
                        if oid:
                            sell_orders.append({"oid": oid, "px": p})
                            self.logger.print_info(f"   [Grid] ✅ 卖单挂载: ${p}")
                    elif res and not res.get('success'):
                        self.logger.print_warning(f"   [Grid] ⚠️ 卖单跳过 @ ${p}: {res.get('message', 'unknown')}")
            except Exception as e:
                self.logger.print_error(f"   [Grid] 下单异常 @ ${p}: {e}")

        # 4. 更新状态
        self.state["active_grids"][symbol] = {
            "config": ai_config,
            "buy_orders": buy_orders,
            "sell_orders": sell_orders,
            "last_sync": time.time()
        }
        self._save_state()
        self.logger.print_info(f"✅ {symbol} 网格调整完成。")


        # 无论 AI 如何，始终检查减仓保护单（不强制补基础开仓单）
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
                reason=ai_config.get("reason", "N/A")
            )

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _is_buy_side(order: Dict[str, Any]) -> bool:
        side = str(order.get("side", "")).strip().upper()
        return side in {"B", "BUY", "BID"}

    @staticmethod
    def _is_sell_side(order: Dict[str, Any]) -> bool:
        side = str(order.get("side", "")).strip().upper()
        return side in {"A", "ASK", "SELL"}

    def _get_symbol_open_orders(self, symbol: str) -> List[Dict[str, Any]]:
        try:
            orders = self.order_manager.client.get_open_orders() or []
            return [o for o in orders if o.get("coin") == symbol]
        except Exception as e:
            self.logger.print_error(f"   [Grid] ❌ 查询 {symbol} 挂单失败: {e}")
            return []

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

    def _build_order_snapshot(self, order: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        oid = order.get("oid")
        if oid is None:
            return None
        return {
            "oid": oid,
            "px": self._safe_float(order.get("limitPx"), 0.0),
        }

    def _sync_local_state_with_orders(self, symbol: str, open_orders: List[Dict[str, Any]]):
        grid = self.state["active_grids"].get(symbol) or {}
        buy_orders: List[Dict[str, Any]] = []
        sell_orders: List[Dict[str, Any]] = []

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
        open_orders: List[Dict[str, Any]],
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

    def _resync_state_with_exchange(self, symbol: str, open_orders: List[Dict[str, Any]]):
        """以交易所真实挂单为准刷新本地状态。"""
        refreshed_open_orders = self._get_symbol_open_orders(symbol)
        if refreshed_open_orders:
            open_orders = refreshed_open_orders
        self._sync_local_state_with_orders(symbol, open_orders)

    def _ensure_position_exit_orders(
        self,
        symbol: str,
        current_price: float,
        open_orders: List[Dict[str, Any]],
        min_exit_orders: int = EXIT_MIN_ORDERS,
        max_exit_orders: int = EXIT_MAX_ORDERS,
        target_coverage_ratio: float = EXIT_TARGET_COVERAGE_RATIO,
    ) -> List[Dict[str, Any]]:
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

    def _extract_oid(self, limit_order_res: Dict[str, Any]) -> Optional[int]:
        try:
            # 兼容 SDK 原始返回格式
            if 'response' in limit_order_res:
                return limit_order_res['response']['data']['statuses'][0]['resting']['oid']
            return None
        except Exception:
            return None

    def _calculate_grid_prices(self, lower: float, upper: float, num: int, grid_type: str) -> List[float]:
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
                prices.append(round(lower * (ratio ** i), 1))
        return prices

    def _cancel_all_orders(self, symbol: str):
        # 优先用交易所真实挂单清理，避免本地 state 漂移导致漏撤单
        canceled_oids = set()
        open_orders = self._get_symbol_open_orders(symbol)
        for order in open_orders:
            oid = order.get("oid")
            if oid is None:
                continue
            try:
                self.order_manager.client.cancel_order(symbol, oid)
                canceled_oids.add(oid)
            except Exception as e:
                self.logger.print_warning(f"   [Grid] ⚠️ 撤单失败 oid={oid}: {e}")

        # 回退：补撤 state 中仍记录但交易所列表里未返回的 oid
        grid = self.state["active_grids"].get(symbol)
        if grid:
            local_oids = [o.get("oid") for o in grid.get("buy_orders", []) if isinstance(o, dict)] + \
                        [o.get("oid") for o in grid.get("sell_orders", []) if isinstance(o, dict)]
            for oid in local_oids:
                if oid is None or oid in canceled_oids:
                    continue
                try:
                    self.order_manager.client.cancel_order(symbol, oid)
                except Exception:
                    pass

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
        if not grid: return "目前无运行中的网格。"
        
        config = grid['config']
        params = config.get("parameters", config)
        return (f"当前正在运行 {symbol} 天地单网格：\n"
                f"- 区间: ${params.get('lower_price', 'N/A')} - ${params.get('upper_price', 'N/A')}\n"
                f"- 止盈比例: {params.get('tp_ratio', 'N/A')}\n"
                f"- 待成交买单: {len(grid['buy_orders'])} 个\n"
                f"- 待成交卖单: {len(grid['sell_orders'])} 个")
