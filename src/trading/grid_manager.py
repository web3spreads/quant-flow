"""
网格交易管理器 (动态调节版)
支持网格同步、AI 止盈止损和状态持久化
"""

import json
import os
import time
from typing import Any

from src.trading.order_manager import OrderManager
from src.utils.logger import TradingLogger


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

    def _load_state(self) -> dict[str, Any]:
        if os.path.exists(self.state_file):
            with open(self.state_file) as f:
                try:
                    data = json.load(f)
                    if "active_grids" not in data:
                        data = {"active_grids": {}}
                    return data
                except Exception as e:
                    self.logger.print_error(f"加载网格状态文件 {self.state_file} 失败: {e}")
        return {"active_grids": {}}

    def _save_state(self):
        with open(self.state_file, "w") as f:
            json.dump(self.state, f, indent=2)

    def sync_grid(self, symbol: str, ai_config: dict[str, Any]):
        """
        核心逻辑：根据 AI 最新的决策，同步现实中的网格状态。
        """
        action = ai_config.get("action")
        if action != "UPDATE_GRID":
            # 去掉'等待'状态：即使 AI 不更新网格，也进入保底挂单模式
            self.logger.print_section(f"🛡️ 保底挂单模式 - {symbol}", style="bold yellow")
            self.logger.print_info("AI 未触发 UPDATE_GRID，本轮改为执行保底挂单（至少 4 单）")
            self._ensure_min_orders(symbol=symbol, min_orders=4, amount_per_order=10.0)
            return

        # 兼容两种格式：参数在根目录或在 parameters 下
        params = ai_config.get("parameters", ai_config)
        new_lower = params.get("lower_price")
        new_upper = params.get("upper_price")
        new_num = params.get("grid_num", 10)
        # 增加安全检查
        try:
            new_num = int(new_num)
            if new_num <= 0:
                new_num = 10
        except (ValueError, TypeError):
            new_num = 10

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

        # 1. 先获取当前价格（在取消旧订单前获取，避免取消过程中价格漂移）
        current_price = self.order_manager.client.get_current_price(symbol)
        if not current_price or current_price <= 0:
            self.logger.print_error(f"   [Grid] ❌ 无法获取 {symbol} 当前价格，终止同步")
            return

        # 2. 彻底清理旧订单；取消失败则终止，防止新旧订单并存且旧订单被遗忘
        cancel_ok = self._cancel_all_orders(symbol)
        if not cancel_ok:
            self.logger.print_error(
                f"   [Grid] ❌ 旧订单取消未完全成功，本轮不重建网格，等待下次重试"
            )
            return

        # 3. 计算新价格分布
        prices = self._calculate_grid_prices(
            new_lower, new_upper, new_num, ai_config.get("grid_type", "GEOMETRIC")
        )

        buy_orders = []
        sell_orders = []

        # 4. 重新布置
        for i, p in enumerate(prices):
            if i > 0:
                time.sleep(1.0)  # 防限流

            try:
                if p < current_price:
                    res = self.order_manager.execute_long_limit(
                        symbol, new_amount, p, tp_ratio=tp_ratio, sl_ratio=sl_ratio
                    )
                    if res and res.get("success"):
                        oid = self._extract_oid(res["limit_order"])
                        if oid:
                            buy_orders.append({"oid": oid, "px": p})
                            self.logger.print_info(f"   [Grid] ✅ 买单挂载: ${p}")
                        else:
                            self.logger.print_warning(
                                f"   [Grid] ⚠️ 买单提交成功但无法提取 OID @ ${p}（可能已立即成交）"
                            )
                    elif res and not res.get("success"):
                        self.logger.print_warning(
                            f"   [Grid] ⚠️ 买单跳过 @ ${p}: {res.get('message', 'unknown')}"
                        )
                    else:
                        self.logger.print_warning(f"   [Grid] ⚠️ 买单返回空结果 @ ${p}")
                elif p > current_price:
                    res = self.order_manager.execute_short_limit(
                        symbol, new_amount, p, tp_ratio=tp_ratio, sl_ratio=sl_ratio
                    )
                    if res and res.get("success"):
                        oid = self._extract_oid(res["limit_order"])
                        if oid:
                            sell_orders.append({"oid": oid, "px": p})
                            self.logger.print_info(f"   [Grid] ✅ 卖单挂载: ${p}")
                        else:
                            self.logger.print_warning(
                                f"   [Grid] ⚠️ 卖单提交成功但无法提取 OID @ ${p}（可能已立即成交）"
                            )
                    elif res and not res.get("success"):
                        self.logger.print_warning(
                            f"   [Grid] ⚠️ 卖单跳过 @ ${p}: {res.get('message', 'unknown')}"
                        )
                    else:
                        self.logger.print_warning(f"   [Grid] ⚠️ 卖单返回空结果 @ ${p}")
            except Exception as e:
                self.logger.print_error(f"   [Grid] 下单异常 @ ${p}: {e}")

        # 5. 更新状态
        self.state["active_grids"][symbol] = {
            "config": ai_config,
            "buy_orders": buy_orders,
            "sell_orders": sell_orders,
            "last_sync": time.time(),
        }
        self._save_state()
        self.logger.print_info(
            f"✅ {symbol} 网格调整完成，买单 {len(buy_orders)} 个，卖单 {len(sell_orders)} 个。"
        )

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

    def _extract_oid(self, limit_order_res: dict[str, Any]) -> int | None:
        """
        从下单响应中提取订单 ID，兼容 resting（挂单中）和 filled（立即成交）两种状态。
        """
        try:
            statuses = (
                limit_order_res.get("response", {}).get("data", {}).get("statuses", [])
            )
            if not statuses:
                return None
            status = statuses[0]
            if "resting" in status:
                return status["resting"]["oid"]
            if "filled" in status:
                return status["filled"]["oid"]
            return None
        except (KeyError, IndexError, TypeError, AttributeError):
            return None

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
        except Exception as e:
            self.logger.print_warning(f"计算网格价格时发生意外转换错误: {e}")

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

    def _cancel_all_orders(self, symbol: str) -> bool:
        """
        取消该交易对在交易所的所有挂单（结合 state 记录和 API 真实挂单双重覆盖）。

        Returns:
            True 表示全部取消成功，False 表示有订单取消失败
        """
        # 1. 收集 state 中记录的 oid（可能包含已成交或已取消的，取消时会报错忽略）
        grid = self.state["active_grids"].get(symbol)
        state_oids: set[int] = set()
        if grid:
            for o in grid.get("buy_orders", []):
                if isinstance(o, dict) and "oid" in o:
                    state_oids.add(o["oid"])
            for o in grid.get("sell_orders", []):
                if isinstance(o, dict) and "oid" in o:
                    state_oids.add(o["oid"])

        # 2. 从交易所 API 获取真实挂单（防止 state 与实际不同步时留有遗漏）
        api_oids: set[int] = set()
        try:
            open_orders = self.order_manager.client.get_open_orders()
            for o in open_orders:
                if o.get("coin") == symbol:
                    oid = o.get("oid")
                    if oid is not None:
                        api_oids.add(oid)
        except Exception as e:
            self.logger.print_warning(f"   [Grid] ⚠️ 获取交易所挂单失败: {e}，仅依赖 state 记录")

        all_oids = state_oids | api_oids
        if not all_oids:
            # 既无 state 记录也无 API 挂单，直接视为清空成功
            if symbol in self.state["active_grids"]:
                del self.state["active_grids"][symbol]
                self._save_state()
            return True

        self.logger.print_info(
            f"   [Grid] 准备取消 {symbol} 挂单 {len(all_oids)} 个"
            f"（state: {len(state_oids)}，API额外发现: {len(api_oids - state_oids)}）"
        )

        failed = []
        for oid in all_oids:
            try:
                self.order_manager.client.cancel_order(symbol, oid)
            except Exception as e:
                self.logger.print_warning(f"   [Grid] ⚠️ 取消订单 {oid} 失败: {e}")
                failed.append(oid)

        if failed:
            self.logger.print_error(
                f"   [Grid] ❌ {len(failed)} 个订单取消失败: {failed}，保留 state 以便下次重试"
            )
            return False

        # 全部取消成功，清除 state
        if symbol in self.state["active_grids"]:
            del self.state["active_grids"][symbol]
            self._save_state()
        return True

    def _ensure_min_orders(self, symbol: str, min_orders: int = 4, amount_per_order: float = 10.0):
        """确保至少有 min_orders 个挂单（用于主网/测试网在 AI 观望时也能铺基础档位）。

        通过交易所 API 查询真实挂单数，而非依赖本地 state，避免已成交/已取消订单被误计。
        """
        try:
            grid = self.state["active_grids"].get(symbol) or {
                "buy_orders": [],
                "sell_orders": [],
                "config": {},
            }
            buy_orders = list(grid.get("buy_orders", []))
            sell_orders = list(grid.get("sell_orders", []))

            # 从交易所 API 获取真实挂单数（防止 state 中的"僵尸"记录干扰判断）
            try:
                open_orders = self.order_manager.client.get_open_orders()
                existing = sum(1 for o in open_orders if o.get("coin") == symbol)
                self.logger.print_info(f"   [Grid] 交易所实际挂单数: {existing}")
            except Exception as e:
                self.logger.print_warning(f"   [Grid] ⚠️ 获取实际挂单数失败: {e}，回退到 state 统计")
                existing = len(buy_orders) + len(sell_orders)

            if existing >= min_orders:
                return

            current_price = self.order_manager.client.get_current_price(symbol)
            if not current_price or current_price <= 0:
                self.logger.print_warning(f"   [Grid] ⚠️ 无法获取 {symbol} 当前价格，跳过补单")
                return

            need = min_orders - existing
            self.logger.print_warning(
                f"   [Grid] ⚠️ {symbol} 当前挂单 {existing} 个，补到至少 {min_orders} 个（补 {need} 个）"
            )

            # 价格档位：按当前价上下各两档（尽量均衡），步进约 0.8% / 1.6%
            steps = [0.008, 0.016, 0.024, 0.032]
            prices = []
            for s in steps:
                prices.append(round(current_price * (1 - s), 1))
                prices.append(round(current_price * (1 + s), 1))

            placed = 0
            for p in prices:
                if placed >= need:
                    break
                try:
                    if p < current_price:
                        res = self.order_manager.execute_long_limit(symbol, amount_per_order, p)
                        if res and res.get("success"):
                            oid = self._extract_oid(res["limit_order"])
                            if oid:
                                buy_orders.append({"oid": oid, "px": p})
                                self.logger.print_info(f"   [Grid] ✅ 补买单: ${p}")
                                placed += 1
                        elif res and not res.get("success"):
                            self.logger.print_warning(
                                f"   [Grid] ⚠️ 补买单跳过 @ ${p}: {res.get('message', 'unknown')}"
                            )
                    elif p > current_price:
                        res = self.order_manager.execute_short_limit(symbol, amount_per_order, p)
                        if res and res.get("success"):
                            oid = self._extract_oid(res["limit_order"])
                            if oid:
                                sell_orders.append({"oid": oid, "px": p})
                                self.logger.print_info(f"   [Grid] ✅ 补卖单: ${p}")
                                placed += 1
                        elif res and not res.get("success"):
                            self.logger.print_warning(
                                f"   [Grid] ⚠️ 补卖单跳过 @ ${p}: {res.get('message', 'unknown')}"
                            )
                except Exception as e:
                    self.logger.print_error(f"   [Grid] 补单异常 @ ${p}: {e}")

            # 更新 state
            self.state["active_grids"][symbol] = {
                "config": grid.get("config", {}),
                "buy_orders": buy_orders,
                "sell_orders": sell_orders,
                "last_sync": time.time(),
            }
            self._save_state()
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
