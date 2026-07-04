"""
网格交易管理器 (动态调节版)
支持网格同步、AI 止盈止损、层级循环复用和状态持久化
"""

import json
import math
import os
import shutil
import tempfile
import time
from collections.abc import Callable
from contextlib import suppress
from decimal import Decimal
from typing import Any

from src.trading.grid_barrier import GridBarrierMonitor, TripleBarrierConfig
from src.trading.grid_pnl import GridPnLTracker
from src.trading.order_manager import OrderManager
from src.utils.cloud_logger import get_cloud_logger
from src.utils.grid_math import GridLevel, GridLevelState, extract_order_id
from src.utils.logger import TradingLogger
from src.utils.precision import to_decimal

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

# Hyperliquid 单笔订单最小名义额（USD）。低于此值的下单必被交易所拒绝。
# 含 2% 缓冲，避免价格波动导致名义额贴边后被拒。
# TODO: 该值为全市场经验常量，理想情况下应从交易所元数据按交易对动态获取（不同合约/现货可能不同）。
HL_MIN_NOTIONAL_USD = 10.0
HL_MIN_NOTIONAL_BUFFER = 1.02


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
        barrier_config: TripleBarrierConfig | None = None,
        on_round_trip_close: Callable[[str, float], None] | None = None,
        max_position_notional_usd: float = 0.0,
        trend_flatten_surgical: bool = False,
        inventory_cap_strict: bool = False,
        keep_grid_reconcile: bool = False,
    ):
        self.order_manager = order_manager
        self.logger = logger
        self.notifier = notifier
        self.state_file = state_file
        # 网格库存硬上限（USD 净持仓名义额）：>0 时启用。净持仓名义额达此值后禁止同向加仓。
        self.max_position_notional_usd = max(0.0, self._safe_float(max_position_notional_usd, 0.0))
        # 手术式平逆势库存：只平超出上限的逆势层级，保留网格挂单与层级状态（默认关闭=全量拆网）
        self.trend_flatten_surgical = bool(trend_flatten_surgical)
        # 库存上限严格模式：名义额计入同向未成交挂单，取价/查单失败 fail-closed（默认关闭）
        self.inventory_cap_strict = bool(inventory_cap_strict)
        # KEEP_GRID 周期对账：撤掉交易所上与本地状态无对应的非 reduce_only 残留挂单（默认关闭）
        self.keep_grid_reconcile = bool(keep_grid_reconcile)
        # 每轮 round-trip 平仓回调：把网格逐轮盈亏上报给账户级风控（连亏熔断）。
        # GridManager 不直接依赖 ProtectionManager，仅通过回调解耦上报，main.py 负责接线。
        self.on_round_trip_close = on_round_trip_close
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
        self.barrier_config = barrier_config or TripleBarrierConfig()
        self.state = self._load_state()

        # 层级循环复用：每个 symbol 对应一组 GridLevel
        self.grid_levels: dict[str, list[GridLevel]] = {}
        # PnL 追踪：每个 symbol 对应一个 tracker
        self.pnl_trackers: dict[str, GridPnLTracker] = {}
        # Triple Barrier 监控：每个 symbol 对应一个 monitor
        self.barrier_monitors: dict[str, GridBarrierMonitor] = {}
        # 上次全量重建时间戳（用于重建冷却），从状态恢复以抵御自动重启循环导致的高频重建
        self._last_rebuild_ts: dict[str, float] = {}
        # 从状态文件恢复层级和 PnL
        self._restore_levels_from_state()

    def _restore_levels_from_state(self):
        """从持久化状态中恢复 grid_levels、pnl_trackers 和 barrier_monitors（崩溃恢复）。"""
        for symbol, grid_data in self.state.get("active_grids", {}).items():
            # 恢复层级
            levels_data = grid_data.get("levels")
            if levels_data and isinstance(levels_data, list):
                self.grid_levels[symbol] = [GridLevel.from_dict(ld) for ld in levels_data]
            # 恢复 PnL tracker
            pnl_data = grid_data.get("pnl")
            if pnl_data and isinstance(pnl_data, dict):
                self.pnl_trackers[symbol] = GridPnLTracker.from_dict(pnl_data)
            # 恢复 barrier monitor（使用 last_sync 作为 start_time）
            start_time = grid_data.get("last_sync", time.time())
            self.barrier_monitors[symbol] = GridBarrierMonitor(
                config=self.barrier_config, start_time=start_time
            )
            # 恢复上次重建时间戳：优先 last_rebuild_ts，其次旧状态的 last_sync；
            # 两者都缺失时回退到当前时间（视为“刚重建”）而非 0.0——否则崩溃/自动重启
            # 后冷却判断恒为真，会立即触发全量撤换单抖动（正是重建冷却要规避的）。
            # 安全性触发（挂单不足/参数异常）的重建不受冷却约束，不影响必要保护。
            self._last_rebuild_ts[symbol] = self._safe_float(
                grid_data.get("last_rebuild_ts") or grid_data.get("last_sync"), time.time()
            )

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
        """原子写入状态文件，防止进程中断导致文件截断损坏。"""
        state_dir = os.path.dirname(self.state_file) or "."
        try:
            fd, tmp_path = tempfile.mkstemp(dir=state_dir, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
            shutil.move(tmp_path, self.state_file)
        except Exception:
            # 临时文件写入失败时回退到直接写入
            with suppress(OSError):
                if "tmp_path" in locals():
                    os.unlink(tmp_path)
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

        当已有层级循环数据时，优先使用增量同步；
        仅在结构性变化（层数/类型/方向改变、首次建网格）时执行全撤全建。
        """
        # 周期性清理孤儿 trigger 单（如历史遗留 TPSL），防止主网订单长期累积
        self._cleanup_orphan_trigger_orders(symbol)

        action = ai_config.get("action")

        # 增量同步路由：如果已有层级数据且非重建场景，使用增量同步
        if action == "UPDATE_GRID" and symbol in self.grid_levels and self.grid_levels[symbol]:
            should_rebuild, reason = self._should_rebuild_grid(symbol=symbol, new_config=ai_config)
            if not should_rebuild:
                self.logger.print_info(f"   [Grid] 增量同步模式: {reason}")
                cloud = get_cloud_logger()
                if cloud:
                    cloud.send_grid_event(
                        symbol=symbol,
                        action="incremental_sync",
                        details={"reason": reason},
                    )
                self.sync_grid_incremental(symbol)
                return

        if action != "UPDATE_GRID":
            # AI 不更新网格时，只保底减仓保护单，不再补基础开仓单
            self.logger.print_section(f"🛡️ 减仓保底模式 - {symbol}", style="bold yellow")

            if action == "KEEP_GRID":
                self.logger.print_info(
                    f"{symbol}: AI 返回 KEEP_GRID，本轮仅检查减仓保护单（reduce_only）"
                )
            elif action == "INSUFFICIENT_CAPITAL":
                self.logger.print_error(
                    f"{symbol}: 💸 资金不足以支撑最小网格，本轮拒绝布单。"
                    f"原因: {ai_config.get('reason', 'unknown')}"
                )
            elif action == "ERROR":
                reason = str(ai_config.get("reason", "unknown"))
                self.logger.print_warning(f"{symbol}: AI 决策异常 action=ERROR，reason={reason}")
            elif action is None:
                self.logger.print_warning(
                    f"{symbol}: AI 决策缺少 action 字段，按保守策略仅检查减仓保护单"
                )
            else:
                self.logger.print_warning(
                    f"{symbol}: AI 返回未知 action={action}，按保守策略仅检查减仓保护单"
                )

            # 对账：撤掉交易所上与本地层级/状态无对应的非 reduce_only 残单。
            # 历史缺陷：KEEP_GRID 分支从不清理残单，靠成交后「无对应持仓」事后移除
            # （线上单日 194 次），期间残单可能意外成交产生计划外库存。
            if self.keep_grid_reconcile:
                self._reconcile_orphan_orders(symbol)

            # 网格空转告警：层级已被清空（紧急平仓/熔断后）且无持仓时，网格没有任何
            # 挂单在工作，只能等 AI 下一次 UPDATE_GRID 重建——这段时间是纯空转，
            # 醒目提示避免误以为网格还在运行。
            if not self.grid_levels.get(symbol) and action == "KEEP_GRID":
                if abs(self._get_symbol_position_size(symbol)) <= 0:
                    self.logger.print_warning(
                        f"   [Grid] 💤 {symbol} 网格空转中：无层级、无持仓，"
                        f"等待 AI 返回 UPDATE_GRID 重建"
                    )

            if self.grid_reduce_only_exit_orders_enabled:
                self._ensure_min_orders(symbol=symbol)
            else:
                self.logger.print_info("已关闭分批减仓单补齐，跳过 reduce_only 补齐检查")
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

        # 1. 彻底清理旧订单；若未完全撤净，停止本轮重建，避免新旧订单叠加
        cancel_all_ok = self._cancel_all_orders(symbol)
        if not cancel_all_ok:
            self.logger.print_warning("   [Grid] ⚠️ 旧网格撤单未全部成功，跳过本轮重建")
            remaining_orders = self._get_symbol_open_orders(symbol=symbol)
            if remaining_orders:
                self._sync_local_state_with_orders(symbol, remaining_orders)
            return

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

        # 库存上限预判。宽松模式：挂限价单不改变持仓，两个布尔整轮稳定，一次算好即可。
        # 严格模式：同向挂单计入敞口，而本循环自己挂出的单不会被入口检查看见——
        # 一次性布尔会放行整轮批量挂单，把潜在同向库存推到上限数倍（线上实测：
        # 空头敞口 $14.6 < 上限 $40 放行后，一轮挂出 4×$50 卖单，潜在敞口 $214）。
        # 故严格模式改为额度制：本轮已挂同向名义额计入预算，耗尽即跳过剩余同向单。
        buy_headroom = sell_headroom = None
        if self.inventory_cap_strict and self.max_position_notional_usd > 0:
            buy_headroom = self._inventory_headroom_usd(symbol, is_buy_open=True)
            sell_headroom = self._inventory_headroom_usd(symbol, is_buy_open=False)
            block_buy_open = buy_headroom <= 0
            block_sell_open = sell_headroom <= 0
        else:
            block_buy_open = self._would_exceed_inventory_cap(symbol, is_buy_open=True)
            block_sell_open = self._would_exceed_inventory_cap(symbol, is_buy_open=False)
        if block_buy_open or block_sell_open:
            self.logger.print_warning(
                f"   [Grid] 🚧 库存达上限 ${self.max_position_notional_usd:.0f}，"
                f"本轮跳过{'买' if block_buy_open else ''}{'卖' if block_sell_open else ''}开仓单（防逆势累积）"
            )
        placed_buy_notional = 0.0
        placed_sell_notional = 0.0
        budget_warned_buy = False
        budget_warned_sell = False

        # 3. 重新布置
        for i, p in enumerate(prices):
            if i > 0:
                time.sleep(1.0)  # 防限流

            try:
                if p < current_price:
                    if block_buy_open:
                        continue
                    if buy_headroom is not None and placed_buy_notional + new_amount > buy_headroom:
                        if not budget_warned_buy:
                            self.logger.print_warning(
                                f"   [Grid] 🚧 买开仓额度耗尽（本轮已挂 ${placed_buy_notional:.0f}"
                                f" / 余量 ${buy_headroom:.0f}），剩余买开仓单跳过"
                            )
                            budget_warned_buy = True
                        continue
                    res = self.order_manager.execute_long_limit(
                        symbol,
                        new_amount,
                        p,
                        tp_ratio=tp_ratio,
                        sl_ratio=sl_ratio,
                        with_take_profit=self.grid_limit_order_take_profit_enabled,
                        with_stop_loss=self.grid_limit_order_stop_loss_enabled,
                        amount_is_notional=True,
                    )
                    if res and res.get("success"):
                        oid = self._extract_oid(res["limit_order"])
                        if oid:
                            buy_orders.append({"oid": oid, "px": p})
                            placed_buy_notional += new_amount
                            self.logger.print_info(f"   [Grid] ✅ 买单挂载: ${p}")
                            self.logger.log_trade(
                                symbol=symbol,
                                action="GRID_BUY",
                                amount=new_amount,
                                price=p,
                                order_id=str(oid),
                                status="PLACED",
                            )
                    elif res and not res.get("success"):
                        self.logger.print_warning(
                            f"   [Grid] ⚠️ 买单跳过 @ ${p}: {res.get('message', 'unknown')}"
                        )
                elif p > current_price:
                    if block_sell_open:
                        continue
                    if sell_headroom is not None and placed_sell_notional + new_amount > sell_headroom:
                        if not budget_warned_sell:
                            self.logger.print_warning(
                                f"   [Grid] 🚧 卖开仓额度耗尽（本轮已挂 ${placed_sell_notional:.0f}"
                                f" / 余量 ${sell_headroom:.0f}），剩余卖开仓单跳过"
                            )
                            budget_warned_sell = True
                        continue
                    res = self.order_manager.execute_short_limit(
                        symbol,
                        new_amount,
                        p,
                        tp_ratio=tp_ratio,
                        sl_ratio=sl_ratio,
                        with_take_profit=self.grid_limit_order_take_profit_enabled,
                        with_stop_loss=self.grid_limit_order_stop_loss_enabled,
                        amount_is_notional=True,
                    )
                    if res and res.get("success"):
                        oid = self._extract_oid(res["limit_order"])
                        if oid:
                            sell_orders.append({"oid": oid, "px": p})
                            placed_sell_notional += new_amount
                            self.logger.print_info(f"   [Grid] ✅ 卖单挂载: ${p}")
                            self.logger.log_trade(
                                symbol=symbol,
                                action="GRID_SELL",
                                amount=new_amount,
                                price=p,
                                order_id=str(oid),
                                status="PLACED",
                            )
                    elif res and not res.get("success"):
                        self.logger.print_warning(
                            f"   [Grid] ⚠️ 卖单跳过 @ ${p}: {res.get('message', 'unknown')}"
                        )
            except Exception as e:
                self.logger.print_error(f"   [Grid] 下单异常 @ ${p}: {e}")

        # 4. 初始化层级循环复用数据
        levels = []
        for i, order_info in enumerate(buy_orders):
            level = GridLevel(
                id=f"L{i}",
                price=to_decimal(order_info["px"]),
                amount=to_decimal(new_amount),
                side="LONG",
                state=GridLevelState.OPEN_PENDING,
            )
            level.open_order_id = order_info["oid"]
            levels.append(level)
        for i, order_info in enumerate(sell_orders):
            level = GridLevel(
                id=f"L{len(buy_orders) + i}",
                price=to_decimal(order_info["px"]),
                amount=to_decimal(new_amount),
                side="SHORT",
                state=GridLevelState.OPEN_PENDING,
            )
            level.open_order_id = order_info["oid"]
            levels.append(level)

        self.grid_levels[symbol] = levels

        # 初始化 PnL tracker（全建时重置）
        if symbol not in self.pnl_trackers:
            self.pnl_trackers[symbol] = GridPnLTracker()

        # 初始化/重置 barrier monitor
        self.barrier_monitors[symbol] = GridBarrierMonitor(
            config=self.barrier_config, start_time=time.time()
        )

        # 5. 更新状态（含层级和 PnL 数据）
        rebuild_ts = time.time()
        self._last_rebuild_ts[symbol] = rebuild_ts
        self.state["active_grids"][symbol] = {
            "config": ai_config,
            "buy_orders": buy_orders,
            "sell_orders": sell_orders,
            "levels": [level.to_dict() for level in levels],
            "pnl": self.pnl_trackers[symbol].to_dict(),
            "last_sync": rebuild_ts,
            "last_rebuild_ts": rebuild_ts,
        }
        self._save_state()
        self.logger.print_info(f"✅ {symbol} 网格调整完成。")

        # 记录网格重建事件到云端
        cloud = get_cloud_logger()
        if cloud:
            cloud.send_grid_event(
                symbol=symbol,
                action="rebuild",
                details={
                    "lower_price": new_lower,
                    "upper_price": new_upper,
                    "grid_num": new_num,
                    "amount_per_grid": new_amount,
                    "tp_ratio": tp_ratio,
                    "sl_ratio": sl_ratio,
                    "buy_count": len(buy_orders),
                    "sell_count": len(sell_orders),
                    "current_price": current_price,
                    "reason": ai_config.get("reason", "N/A"),
                },
            )

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

        # 重建冷却：除上述安全性触发（首次/挂单不足/参数异常）外，距上次全量重建不足冷却期一律不重建。
        # 这是抑制高频撤换单的主闸——历史上 84.6% 周期触发全量重建、挂单活不过 5 分钟即源于此处缺失。
        if self.grid_rebuild_cooldown_seconds > 0:
            last_rebuild = self._last_rebuild_ts.get(symbol, 0.0)
            elapsed = time.time() - last_rebuild
            if 0 <= elapsed < self.grid_rebuild_cooldown_seconds:
                remaining = self.grid_rebuild_cooldown_seconds - elapsed
                return (
                    False,
                    f"重建冷却中（剩余 {remaining:.0f}s / 冷却 {self.grid_rebuild_cooldown_seconds}s），维持网格",
                )

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
        hard_timeout_sec: float = 20.0,
    ) -> list[dict[str, Any]]:
        """重建前尽量把残留限价单撤净；超时后返回剩余订单。"""
        remaining_orders = self._get_symbol_open_orders(symbol=symbol)
        if not remaining_orders:
            return []

        start_time = time.monotonic()

        for round_idx in range(1, max_rounds + 1):
            for order in remaining_orders:
                if time.monotonic() - start_time >= hard_timeout_sec:
                    self.logger.print_warning(
                        f"   [Grid] ⏱️ 撤单硬超时 {hard_timeout_sec}s，剩余 {len(remaining_orders)} 单未清"
                    )
                    return remaining_orders
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

    def _reconcile_orphan_orders(self, symbol: str):
        """撤掉交易所上与本地层级/状态无对应的非 reduce_only 残留挂单。

        「孤儿单」来源：全量重建被中断、崩溃恢复后状态漂移、紧急平仓撤单失败残留等。
        reduce_only 单不动（属于 ``_ensure_min_orders`` 的减仓保护单簿记，撤了会互相打架）；
        trigger 单由 ``_cleanup_orphan_trigger_orders`` 单独治理。
        """
        try:
            open_orders = self._get_symbol_open_orders(symbol)
        except Exception as e:
            self.logger.print_error(f"   [Grid] ❌ 对账查单失败: {e}")
            return

        known_oids: set[int] = set()
        for level in self.grid_levels.get(symbol) or []:
            if level.open_order_id is not None:
                known_oids.add(level.open_order_id)
            if level.close_order_id is not None:
                known_oids.add(level.close_order_id)
        grid = self.state["active_grids"].get(symbol) or {}
        for order in list(grid.get("buy_orders") or []) + list(grid.get("sell_orders") or []):
            if isinstance(order, dict) and order.get("oid") is not None:
                known_oids.add(order["oid"])

        canceled = 0
        for order in open_orders:
            oid = order.get("oid")
            if oid is None or oid in known_oids:
                continue
            if bool(order.get("reduceOnly", False)):
                continue  # 减仓保护单不属于层级簿记，跳过
            if self._is_trigger_order(order):
                continue
            if self._cancel_order_with_retry(symbol, oid):
                canceled += 1
        if canceled:
            self.logger.print_warning(
                f"   [Grid] 🧹 对账撤掉无主残单 {canceled} 个（{symbol}）"
            )

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

    def _would_exceed_inventory_cap(self, symbol: str, is_buy_open: bool) -> bool:
        """库存上限守卫：净持仓名义额已达上限时，禁止再往「加剧当前持仓方向」的方向开仓。

        这是单边趋势亏损的根因防线——中性网格在上涨里不断成交上方卖单开空，空头库存
        无任何上限地累积，最终被市价平掉造成大额亏损。启用后：净空头达上限就不再放行卖开仓单
        （但仍放行买开仓单以减仓/反向收敛），反之亦然。

        Args:
            is_buy_open: True=买入开多单（增多头敞口）；False=卖出开空单（增空头敞口）。
        Returns:
            True 表示该开仓单会加剧已超限的同向库存，应跳过。
        """
        cap = self.max_position_notional_usd
        if cap <= 0:
            return False  # 未启用

        if self.inventory_cap_strict:
            try:
                directional_exposure = self._directional_exposure_usd(symbol, is_buy_open)
            except Exception as e:
                # 严格模式 fail-closed：取数失败时拦截加仓。异常行情/接口抖动时恰恰是
                # 逆势库存风险最高的时刻，宽松放行（历史行为）等于风控在最需要时缺位。
                self.logger.print_warning(
                    f"   [Grid] 🚧 库存上限检查取数失败（{e}），严格模式拦截{symbol}开仓单"
                )
                return True
            return directional_exposure >= cap

        # ── 宽松模式：历史行为，只看已成交持仓，不看未成交挂单 ──
        position_size = self._get_symbol_position_size(symbol)
        if abs(position_size) <= 0:
            return False  # 当前空仓即放行
        try:
            price = self.order_manager.client.get_current_price(symbol) or 0.0
        except Exception:
            return False  # 取价失败不拦截，避免误伤正常布单
        pos_notional = abs(position_size) * float(price)
        pos_is_long = position_size > 0
        adding_same_direction = (is_buy_open and pos_is_long) or (
            (not is_buy_open) and (not pos_is_long)
        )
        if pos_notional < cap:
            return False
        return adding_same_direction  # 仅在已达上限时拦截同向加仓

    def _directional_exposure_usd(self, symbol: str, is_buy_open: bool) -> float:
        """严格模式口径的方向化敞口名义额（USD）。

        = 同向持仓名义额（反向持仓为负、先抵扣）+ 同向非 reduce-only 挂单名义额。
        取价/查单失败时抛出异常，由调用方按 fail-closed 语义处理。
        """
        position_size = self._get_symbol_position_size(symbol)
        price = self.order_manager.client.get_current_price(symbol) or 0.0
        if price <= 0:
            raise ValueError(f"无效价格 {price}")

        pos_notional = abs(position_size) * float(price)
        pos_is_long = position_size > 0
        if abs(position_size) <= 0:
            directional_exposure = 0.0
        elif (is_buy_open and pos_is_long) or ((not is_buy_open) and (not pos_is_long)):
            directional_exposure = pos_notional  # 持仓与拟开方向相同
        else:
            directional_exposure = -pos_notional  # 反向持仓：新开单先抵消库存

        open_orders = self._get_symbol_open_orders(symbol)
        side_check = self._is_buy_side if is_buy_open else self._is_sell_side
        for order in open_orders:
            # reduce_only 挂单只减仓不加库存，不计入
            if bool(order.get("reduceOnly", False)):
                continue
            if not side_check(order):
                continue
            px = self._safe_float(order.get("limitPx"), 0.0)
            sz = self._safe_float(order.get("sz"), 0.0)
            directional_exposure += abs(px * sz)
        return directional_exposure

    def _inventory_headroom_usd(self, symbol: str, is_buy_open: bool) -> float:
        """严格模式下拟开方向距库存上限的剩余名义额度（USD，不小于 0）。

        批量重建用它做额度制预算：本轮已挂同向名义额累计扣减，耗尽即跳过。
        取数失败返回 0.0（fail-closed，与 _would_exceed_inventory_cap 一致）。
        """
        cap = self.max_position_notional_usd
        try:
            directional_exposure = self._directional_exposure_usd(symbol, is_buy_open)
        except Exception as e:
            self.logger.print_warning(
                f"   [Grid] 🚧 库存额度计算取数失败（{e}），严格模式按零额度处理（{symbol}）"
            )
            return 0.0
        return max(0.0, cap - directional_exposure)

    def flatten_adverse_inventory(self, symbol: str, trend_dir: int) -> bool:
        """趋势过滤的止血动作：当净持仓方向与趋势相反时，减掉逆势库存。

        trend_dir: +1=上涨趋势, -1=下跌趋势。上涨却持空、或下跌却持多即为「逆势」。

        两种模式（trend_flatten_surgical 开关）：
        - 关闭（历史行为）：``_emergency_close_all`` 全量拆网——撤全部挂单、市价全平、
          删全部层级、重置重建冷却。线上实证 12.5 天拆网 145 次，每次都在摆动极值
          实现亏损并支付 taker 费，然后冷却 900s 重建，与网格「持库存等回归」的
          盈利机制正面对抗，是净值 -39% 的主出血口。
        - 开启（手术式）：只市价平掉「超出库存上限的逆势层级」，保留顺势挂单、
          剩余层级与重建冷却状态，网格继续运转。

        Returns:
            True 表示确实平掉了（全部或部分）逆势库存。
        """
        if trend_dir == 0:
            return False
        position_size = self._get_symbol_position_size(symbol)
        if abs(position_size) <= 0:
            return False
        adverse = (trend_dir > 0 and position_size < 0) or (
            trend_dir < 0 and position_size > 0
        )
        if not adverse:
            return False
        if self.trend_flatten_surgical:
            return self._surgical_reduce_adverse(symbol, trend_dir, position_size)
        self.logger.print_warning(
            f"   [Grid] 🩹 趋势({'涨' if trend_dir > 0 else '跌'})与持仓"
            f"({'空' if position_size < 0 else '多'})相反，市价平掉逆势库存"
        )
        self._emergency_close_all(
            symbol, reason=f"趋势过滤：平逆势库存 (trend_dir={trend_dir})"
        )
        return True

    def _surgical_reduce_adverse(self, symbol: str, trend_dir: int, position_size: float) -> bool:
        """手术式减仓：逐层市价平掉超出库存上限的逆势层级，网格其余部分原样保留。

        与 ``_emergency_close_all`` 的区别：不撤顺势挂单、不删层级数据、不动 PnL tracker
        与 barrier monitor、不重置重建冷却——被平层级 reset 回 IDLE，趋势解除后由增量
        同步自然重新挂单（重新挂单仍受库存上限约束）。

        削减目标：逆势名义额 ≤ max_position_notional_usd；上限未启用（=0）时保留一层
        （给均值回归留出最小仓位，避免整段趋势判定期间空转）。
        按「入场价最差优先」平仓：多头库存平最高买入价、空头库存平最低卖出价。

        Returns:
            True 表示至少平掉了一个层级。
        """
        try:
            current_price = self.order_manager.client.get_current_price(symbol) or 0.0
        except Exception as e:
            self.logger.print_error(f"   [Grid] 手术式减仓取价失败，跳过本轮: {e}")
            return False
        if current_price <= 0:
            return False
        cp = to_decimal(current_price)

        adverse_side = "SHORT" if position_size < 0 else "LONG"
        levels = self.grid_levels.get(symbol) or []
        adverse_levels = [
            level
            for level in levels
            if level.side == adverse_side
            and level.state in (GridLevelState.OPEN_FILLED, GridLevelState.CLOSE_PENDING)
            and level.open_fill_price is not None
            and level.open_fill_amount is not None
        ]

        adverse_notional = sum(
            (level.open_fill_amount * cp for level in adverse_levels), Decimal("0")
        )
        cap = to_decimal(self.max_position_notional_usd)
        if cap <= 0:
            # 上限未启用：保留一层库存
            keep_one = max(adverse_levels, key=lambda lv: lv.open_fill_amount * cp, default=None)
            cap = keep_one.open_fill_amount * cp if keep_one else Decimal("0")
        if adverse_notional <= cap:
            return False  # 逆势库存在允许范围内：不动，让网格自己回归

        # 入场价最差优先：多头平最高入场价，空头平最低入场价
        adverse_levels.sort(
            key=lambda lv: lv.open_fill_price,
            reverse=(adverse_side == "LONG"),
        )

        reduced_count = 0
        total_reduced_pnl = Decimal("0")
        tracker = self.pnl_trackers.get(symbol)
        if tracker is None:
            tracker = GridPnLTracker()
            self.pnl_trackers[symbol] = tracker

        for level in adverse_levels:
            if adverse_notional <= cap:
                break
            # 先撤该层挂着的平仓单，避免市价平仓后 reduce_only 平仓单变孤儿
            if level.state == GridLevelState.CLOSE_PENDING and level.close_order_id:
                self._cancel_order_with_retry(symbol, level.close_order_id)

            close_size = float(level.open_fill_amount)
            try:
                result = self.order_manager.client.close_position(symbol, size=close_size)
            except Exception as e:
                self.logger.print_error(f"   [Grid] {level.id} 手术式减仓下单异常: {e}")
                continue
            if not result or str(result.get("status", "")).lower() != "ok":
                self.logger.print_warning(
                    f"   [Grid] {level.id} 手术式减仓失败: {result}"
                )
                continue

            # 以当前价近似成交价记账（忽略滑点；与紧急平仓同一近似口径），
            # 复用 record_round_trip 保证 realized PnL / 手续费统计口径一致。
            level.close_fill_price = cp
            level.close_fill_amount = level.open_fill_amount
            level.close_fill_time = time.time()
            pnl = tracker.record_round_trip(level)
            total_reduced_pnl += pnl
            adverse_notional -= level.open_fill_amount * cp
            reduced_count += 1

            self.logger.log_trade(
                symbol=symbol,
                action="GRID_FORCED_REDUCE",
                amount=close_size,
                price=float(cp),
                order_id=str(level.close_order_id or ""),
                status="FILLED",
                pnl=float(pnl),
                reason=f"趋势过滤手术式减仓 (trend_dir={trend_dir}, level={level.id})",
            )
            # 强制平仓事件：亏损计入连亏熔断，净盈利不重置计数（见 forced 语义）
            self._report_round_trip_close(symbol, float(pnl), forced=True)
            level.reset()

        if reduced_count:
            self.logger.print_warning(
                f"   [Grid] 🔪 手术式减仓完成: 平掉 {reduced_count} 个逆势层级，"
                f"实现盈亏 {float(total_reduced_pnl):+.4f}，剩余逆势名义额 "
                f"${float(adverse_notional):.2f} ≤ 目标 ${float(cap):.2f}"
            )
            cloud = get_cloud_logger()
            if cloud:
                cloud.send_grid_event(
                    symbol=symbol,
                    action="surgical_reduce",
                    details={
                        "trend_dir": trend_dir,
                        "reduced_levels": reduced_count,
                        "realized_pnl": float(total_reduced_pnl),
                        "remaining_adverse_notional": float(adverse_notional),
                    },
                    level="warn",
                )
            self._save_incremental_state(symbol)
        return reduced_count > 0

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
            # 保留上次重建时间戳，避免残单回写重置重建冷却；两者皆空时回退 0.0 防止 None 入库
            "last_rebuild_ts": grid.get("last_rebuild_ts")
            or self._last_rebuild_ts.get(symbol)
            or 0.0,
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

            if close_with_buy:
                raw_price = current_price * (1 - step)
                side_code = "B"
            else:
                raw_price = current_price * (1 + step)
                side_code = "A"

            limit_price = self.order_manager.client.format_price(symbol, raw_price)
            if not limit_price or limit_price <= 0:
                continue

            # 最小名义额对应的最小下单量：低于 $10 名义额的减仓单必被 HL 拒绝，
            # 历史上这里退化到 size_step（约 $0.17）产生了上万笔灰尘虚单。
            min_notional_size = (HL_MIN_NOTIONAL_USD * HL_MIN_NOTIONAL_BUFFER) / limit_price
            remaining_to_cover = max(required_cover_size - projected_covered, 0.0)
            if remaining_to_cover <= 0:
                break

            target_layers_left = max(min_exit_orders - current_count, 1)
            # 资金有限时动态合并层级：若按 target_layers_left 拆分会使单层低于最小名义额，
            # 则减少层数，确保每层都 ≥ $10 且持仓能被完全覆盖。
            if min_notional_size > 0:
                max_layers_by_notional = max(1, int(remaining_to_cover / min_notional_size))
                target_layers_left = min(target_layers_left, max_layers_by_notional)
            order_size = remaining_to_cover / target_layers_left
            # 抬到最小名义额，但不超过剩余待覆盖持仓
            order_size = max(order_size, min_notional_size)
            order_size = min(order_size, remaining_to_cover)
            # 量化到合约最小步长（向上取整避免低于最小名义额；reduce_only 略微超出由交易所截断）
            if size_step > 0:
                order_size = round(math.ceil(order_size / size_step) * size_step, 10)

            # 若连整笔剩余持仓都凑不到 $10 名义额，则无法下合法减仓单，停止补单
            if order_size <= 0 or order_size * limit_price < HL_MIN_NOTIONAL_USD:
                self.logger.print_warning(
                    f"   [Grid] ⏭️ 剩余待覆盖持仓名义额不足 ${HL_MIN_NOTIONAL_USD:.0f}，"
                    f"跳过减仓补单（剩余 {remaining_to_cover:.6f}）"
                )
                break

            result = self.order_manager.client.place_limit_order(
                symbol=symbol,
                is_buy=close_with_buy,
                size=order_size,
                price=limit_price,
                reduce_only=True,
            )

            # 校验内层 statuses：拒单（外层仍 ok）不得计入覆盖率与已挂单数
            order_ok, order_err = self.order_manager.client.check_order_success(result)
            if order_ok:
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
                # 覆盖率按实际覆盖量累计：order_size 经 ceil 取整可能略超 remaining_to_cover，
                # 但 reduce_only 单实际成交被交易所截断到剩余持仓，若按 order_size 累计会过计、
                # 提前判定覆盖完成而漏挂后续保护单。故按 min(order_size, remaining_to_cover) 计。
                projected_covered += min(order_size, remaining_to_cover)
                placed += 1
                self.logger.print_warning(
                    f"   [Grid] 🛟 补减仓{side_name}单: {order_size:.6f} @ ${limit_price} (reduce_only)"
                )
                self.logger.log_trade(
                    symbol=symbol,
                    action="GRID_REDUCE_BUY" if close_with_buy else "GRID_REDUCE_SELL",
                    amount=order_size,
                    price=float(limit_price),
                    order_id=str(oid) if oid is not None else "",
                    status="PLACED",
                )
            else:
                self.logger.print_warning(
                    f"   [Grid] ⚠️ 减仓{side_name}单被拒 @ ${limit_price}: {order_err}"
                )

        return open_orders

    def _extract_oid(self, limit_order_res: dict[str, Any]) -> int | None:
        return extract_order_id(limit_order_res)

    def _calculate_grid_prices(
        self, lower: float, upper: float, num: int, grid_type: str
    ) -> list[float]:
        """计算网格价格分布，内部使用 Decimal 精确计算。"""
        if num < 2:
            return [float(to_decimal(lower).quantize(Decimal("0.1")))]

        d_lower = to_decimal(lower)
        d_upper = to_decimal(upper)
        tick = Decimal("0.1")

        prices: list[float] = []
        if grid_type == "ARITHMETIC":
            diff = (d_upper - d_lower) / Decimal(str(num - 1))
            for i in range(num):
                p = d_lower + Decimal(str(i)) * diff
                prices.append(float(p.quantize(tick)))
        else:  # GEOMETRIC
            if d_lower <= 0 or d_upper <= 0:
                return [float(d_lower.quantize(tick))]
            # 使用 Decimal 的 ln/exp 实现精确分数幂
            log_ratio = (d_upper / d_lower).ln() / Decimal(str(num - 1))
            for i in range(num):
                p = d_lower * (log_ratio * Decimal(str(i))).exp()
                prices.append(float(p.quantize(tick)))
        return prices

    def _cancel_all_orders(self, symbol: str) -> bool:
        # 优先用交易所真实挂单清理（含 trigger），避免本地 state 漂移导致漏撤单
        all_canceled = True
        canceled_oids = set()
        open_orders = self._get_symbol_open_orders(symbol, include_trigger=True)
        for order in open_orders:
            oid = order.get("oid")
            if oid is None:
                continue
            try:
                if self._cancel_order_with_retry(symbol, oid):
                    canceled_oids.add(oid)
                else:
                    all_canceled = False
            except Exception as e:
                self.logger.print_warning(f"   [Grid] ⚠️ 撤单异常 oid={oid}: {e}")
                all_canceled = False

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
                    else:
                        all_canceled = False

        if symbol in self.state["active_grids"]:
            del self.state["active_grids"][symbol]
            self._save_state()

        return all_canceled

    def cancel_all_orders(self, symbol: str) -> bool:
        """
        撤销指定 symbol 的全部网格挂单（含 trigger）并清理本地网格状态。

        公共入口，供 main.py 在账户级风控熔断（CLOSE_ALL_POSITIONS）时调用，
        避免熔断期间网格挂单成交新增敞口。返回 True 表示全部撤销成功。
        """
        return self._cancel_all_orders(symbol)

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
        """增强版网格摘要：包含层级状态分布和 PnL 报告。"""
        grid = self.state["active_grids"].get(symbol)
        if not grid:
            return "目前无运行中的网格。"

        config = grid["config"]
        params = config.get("parameters", config)

        # 基础信息
        base_info = (
            f"当前正在运行 {symbol} 天地单网格：\n"
            f"- 区间: ${params.get('lower_price', 'N/A')} - ${params.get('upper_price', 'N/A')}\n"
            f"- 止盈比例: {params.get('tp_ratio', 'N/A')}\n"
            f"- 待成交买单: {len(grid.get('buy_orders', []))} 个\n"
            f"- 待成交卖单: {len(grid.get('sell_orders', []))} 个"
        )

        # 层级状态分布
        levels = self.grid_levels.get(symbol)
        if levels:
            state_counts: dict[str, int] = {}
            for level in levels:
                state_counts[level.state.value] = state_counts.get(level.state.value, 0) + 1
            base_info += f"\n- 层级状态: {state_counts}"

        # PnL 报告
        tracker = self.pnl_trackers.get(symbol)
        if tracker and levels:
            try:
                current_price = self.order_manager.client.get_current_price(symbol)
                if current_price and current_price > 0:
                    current_price_d = to_decimal(current_price)
                    # 总投入 = 所有层的 amount 之和
                    total_investment = sum((level.amount for level in levels), Decimal("0"))
                    summary = tracker.get_summary(levels, current_price_d, total_investment)
                    base_info += (
                        f"\n- 已实现 PnL: {summary['realized_pnl']:+.4f} USDT"
                        f"\n- 未实现 PnL: {summary['unrealized_pnl']:+.4f} USDT"
                        f"\n- 净 PnL: {summary['net_pnl']:+.4f} ({summary['net_pnl_pct']:.2%})"
                        f"\n- 完成轮回: {summary['completed_round_trips']} 次"
                        f"\n- 累计手续费: {summary['total_fees']:.4f} USDT"
                    )
            except Exception as e:
                self.logger.print_error(f"   [Grid] 获取网格摘要 PnL 时出错: {e}")

        return base_info

    # ────────────────────────────────────────────────────────────────
    # 层级循环复用：增量同步
    # ────────────────────────────────────────────────────────────────

    def _emergency_close_all(self, symbol: str, reason: str):
        """紧急全部平仓（Triple Barrier 触发时调用）。"""
        self.logger.print_warning(f"   [Grid] 紧急平仓: {reason}")
        cloud = get_cloud_logger()

        # 1. 撤掉所有挂单
        self._cancel_all_orders(symbol)

        # 2. 市价平仓
        close_success = False
        try:
            result = self.order_manager.client.close_position(symbol)
            if result:
                close_success = True
                self.logger.print_info(f"   [Grid] {symbol} 市价平仓完成: {result}")
        except Exception as e:
            self.logger.print_error(f"   [Grid] {symbol} 市价平仓失败: {e}")
            if cloud:
                cloud.send_alert(
                    symbol=symbol,
                    alert_type="emergency_close_failed",
                    severity="extreme",
                    message=f"Triple Barrier 紧急平仓失败: {e}",
                    details={"reason": reason, "error": str(e)},
                )

        # 记录紧急平仓事件到云端
        if cloud:
            cloud.send_grid_event(
                symbol=symbol,
                action="emergency_close",
                details={
                    "reason": reason,
                    "close_success": close_success,
                },
                level="warn",
            )

        # 把被市价平掉的持仓盈亏上报连亏熔断：否则该插件对网格里最大的那些止损/紧急平仓
        # 永远不可见（只数限价 TP 平仓的小赢小输，线上 global_losses 长期为 0 即铁证）。
        # 用平仓前 OPEN_FILLED 层的未实现盈亏近似市价平仓的已实现盈亏（忽略滑点/taker 费，
        # 量级足够驱动连亏判定）。必须在删除层级数据之前计算。
        # forced=True：强平净盈利不得重置连亏计数（线上 145 次强平 0 次熔断的根因）。
        try:
            tracker = self.pnl_trackers.get(symbol)
            levels = self.grid_levels.get(symbol)
            if tracker and levels:
                cp = self.order_manager.client.get_current_price(symbol)
                if cp and cp > 0:
                    closed_pnl = tracker.calculate_unrealized_pnl(levels, to_decimal(cp))
                    if abs(closed_pnl) > 0:
                        # 归因落盘：紧急平仓的已实现盈亏 + 触发原因（barrier/趋势过滤/熔断）
                        self.logger.log_trade(
                            symbol=symbol,
                            action="GRID_EMERGENCY_CLOSE",
                            amount=float(
                                sum(
                                    (
                                        lv.open_fill_amount
                                        for lv in levels
                                        if lv.state
                                        in (
                                            GridLevelState.OPEN_FILLED,
                                            GridLevelState.CLOSE_PENDING,
                                        )
                                        and lv.open_fill_amount is not None
                                    ),
                                    Decimal("0"),
                                )
                            ),
                            price=float(cp),
                            order_id="",
                            status="FILLED",
                            pnl=float(closed_pnl),
                            reason=reason,
                        )
                        self._report_round_trip_close(symbol, float(closed_pnl), forced=True)
        except Exception as e:
            self.logger.print_warning(f"   [Grid] 紧急平仓盈亏上报风控失败: {e}")

        # 3. 清理层级数据
        if symbol in self.grid_levels:
            del self.grid_levels[symbol]
        if symbol in self.barrier_monitors:
            del self.barrier_monitors[symbol]
        if symbol in self.pnl_trackers:
            del self.pnl_trackers[symbol]

        # 通知
        if self.notifier:
            try:
                self.notifier.notify_grid_update(
                    symbol=symbol,
                    lower=0,
                    upper=0,
                    num=0,
                    amount=0,
                    tp=0,
                    sl=0,
                    buy_count=0,
                    sell_count=0,
                    reason=f"Triple Barrier 触发: {reason}",
                )
            except Exception as e:
                self.logger.print_warning(f"   [Grid] 发送 Triple Barrier 触发通知失败: {e}")

    def check_barrier(self, symbol: str) -> bool:
        """Triple Barrier 兜底检查（独立方法，供每个网格周期开头无条件调用）。

        历史问题：barrier 仅在 ``sync_grid_incremental`` 的窄分支里检查，AI 频繁返回
        KEEP_GRID/ERROR 时根本走不到那里，导致 -5%/超时等兜底止损长期形同虚设。提取为
        独立方法后由 ``grid_cycle`` 每轮先调用，不受 AI action 分支影响。触发即紧急平仓。

        Returns:
            True 表示已触发屏障并紧急平仓（本轮应跳过后续布单）。
        """
        levels = self.grid_levels.get(symbol)
        monitor = self.barrier_monitors.get(symbol)
        tracker = self.pnl_trackers.get(symbol)
        if not levels or not monitor or not tracker:
            return False
        try:
            current_price_raw = self.order_manager.client.get_current_price(symbol)
            if not current_price_raw or current_price_raw <= 0:
                return False
            current_price_d = to_decimal(current_price_raw)
            total_investment = sum((level.amount for level in levels), Decimal("0"))
            net_pnl_pct = tracker.get_net_pnl_pct(levels, current_price_d, total_investment)
            trigger = monitor.check(
                current_price=current_price_d,
                net_pnl_pct=net_pnl_pct,
                current_time=time.time(),
            )
            if trigger:
                self.logger.print_warning(f"   [Grid] Triple Barrier 触发: {trigger}")
                cloud = get_cloud_logger()
                if cloud:
                    cloud.send_risk_event(
                        symbol=symbol,
                        risk_type="triple_barrier_triggered",
                        details={
                            "trigger_reason": trigger,
                            "net_pnl_pct": float(net_pnl_pct),
                            "current_price": current_price_raw,
                            "total_investment": float(total_investment),
                            "active_levels": len(levels),
                        },
                        level="error",
                    )
                self._emergency_close_all(symbol, reason=trigger)
                return True
        except Exception as e:
            self.logger.print_error(f"   [Grid] Triple Barrier 检查异常: {e}")
        return False

    def sync_grid_incremental(self, symbol: str):
        """增量同步：只处理需要操作的层级，不全撤全建。"""
        levels = self.grid_levels.get(symbol)
        if not levels:
            self.logger.print_warning(f"   [Grid] {symbol} 无层级数据，跳过增量同步")
            return

        # Triple Barrier 屏障检查（与 grid_cycle 顶层调用同一方法；此处保留以覆盖
        # 非 grid_cycle 路径直接调用 sync_grid 的情况，已平仓时幂等返回 False）
        if self.check_barrier(symbol):
            return

        exchange_orders = self._get_symbol_open_orders(symbol)
        exchange_oids = {o["oid"] for o in exchange_orders if "oid" in o}

        # 统一获取一次成交记录，避免每个层级重复调用 API 触发频率限制
        try:
            user_address = self.order_manager.client.address
            cached_fills = self.order_manager.client.info.user_fills(user_address) or []
        except Exception as e:
            self.logger.print_error(f"   [Grid] 批量查询成交记录失败: {e}")
            cached_fills = []

        for level in levels:
            try:
                if level.state == GridLevelState.IDLE:
                    # 空闲 -> 挂开仓单
                    self._place_open_order(symbol, level)

                elif level.state == GridLevelState.OPEN_PENDING:
                    # 检查开仓单是否还在挂单列表中
                    if level.open_order_id not in exchange_oids:
                        # 不在挂单列表 -> 已成交或被撤
                        if self._confirm_fill(symbol, level, "open", cached_fills):
                            level.state = GridLevelState.OPEN_FILLED
                            self.logger.print_info(
                                f"   [Grid] {level.id} 开仓成交 @ {level.open_fill_price}"
                            )
                            self.logger.log_trade(
                                symbol=symbol,
                                action=f"GRID_OPEN_{'BUY' if level.side == 'LONG' else 'SELL'}",
                                amount=float(level.open_fill_amount or 0),
                                price=float(level.open_fill_price or 0),
                                order_id=str(level.open_order_id or ""),
                                status="FILLED",
                            )
                        else:
                            # 被撤销/失败 -> 回到 IDLE 重新挂
                            level.state = GridLevelState.IDLE

                elif level.state == GridLevelState.OPEN_FILLED:
                    # 开仓已成交 -> 挂平仓单
                    self._place_close_order(symbol, level)

                elif level.state == GridLevelState.CLOSE_PENDING:
                    if level.close_order_id not in exchange_oids:
                        if self._confirm_fill(symbol, level, "close", cached_fills):
                            level.state = GridLevelState.COMPLETED
                            self.logger.print_info(
                                f"   [Grid] {level.id} 平仓成交 @ {level.close_fill_price}"
                            )
                            self.logger.log_trade(
                                symbol=symbol,
                                action=f"GRID_CLOSE_{'BUY' if level.side == 'SHORT' else 'SELL'}",
                                amount=float(level.close_fill_amount or 0),
                                price=float(level.close_fill_price or 0),
                                order_id=str(level.close_order_id or ""),
                                status="FILLED",
                            )
                        else:
                            # 平仓单被撤 -> 回到 OPEN_FILLED 重挂
                            level.state = GridLevelState.OPEN_FILLED

                elif level.state == GridLevelState.COMPLETED:
                    # 完成一轮 -> 记录 PnL -> 重置
                    self._record_round_trip(symbol, level)
                    level.reset()
                    # reset 后变为 IDLE，下一轮 sync 会重新挂单

            except Exception as e:
                self.logger.print_error(f"   [Grid] {level.id} 同步异常: {e}")

        self._save_incremental_state(symbol)

    def _place_open_order(self, symbol: str, level: GridLevel):
        """为层级挂开仓单。"""
        current_price = self.order_manager.client.get_current_price(symbol)
        if not current_price or current_price <= 0:
            return

        is_buy = level.side == "LONG"
        price = float(level.price)

        # 只在价格合理时挂单（买单低于市价，卖单高于市价）
        if is_buy and price >= current_price:
            return
        if not is_buy and price <= current_price:
            return

        # 库存上限：净持仓达上限后不再往同方向加仓（防单边趋势逆势累积，本次最大亏损根因）。
        # 这是主要执行点——增量同步每轮在此重新挂开仓单，超限方向被持续拦截，库存自然收敛。
        if self._would_exceed_inventory_cap(symbol, is_buy_open=is_buy):
            self.logger.print_warning(
                f"   [Grid] 🚧 {level.id} 库存达上限，跳过{'买' if is_buy else '卖'}开仓单（防逆势累积）"
            )
            return

        # 从 state 中获取 tp/sl 配置
        grid_data = self.state["active_grids"].get(symbol, {})
        config = grid_data.get("config", {})
        params = config.get("parameters", config)
        tp_ratio = params.get("tp_ratio")
        sl_ratio = params.get("sl_ratio")

        if is_buy:
            res = self.order_manager.execute_long_limit(
                symbol,
                float(level.amount),
                price,
                tp_ratio=tp_ratio,
                sl_ratio=sl_ratio,
                with_take_profit=self.grid_limit_order_take_profit_enabled,
                with_stop_loss=self.grid_limit_order_stop_loss_enabled,
                amount_is_notional=True,
            )
        else:
            res = self.order_manager.execute_short_limit(
                symbol,
                float(level.amount),
                price,
                tp_ratio=tp_ratio,
                sl_ratio=sl_ratio,
                with_take_profit=self.grid_limit_order_take_profit_enabled,
                with_stop_loss=self.grid_limit_order_stop_loss_enabled,
                amount_is_notional=True,
            )

        if res and res.get("success"):
            oid = self._extract_oid(res.get("limit_order", {}))
            if oid:
                level.open_order_id = oid
                level.state = GridLevelState.OPEN_PENDING
                self.logger.print_info(
                    f"   [Grid] {level.id} 挂开仓单 {'买' if is_buy else '卖'} @ ${price}"
                )
                self.logger.log_trade(
                    symbol=symbol,
                    action=f"GRID_{'BUY' if is_buy else 'SELL'}",
                    amount=float(level.amount),
                    price=price,
                    order_id=str(oid),
                    status="PLACED",
                )

    def _confirm_fill(
        self, symbol: str, level: GridLevel, order_type: str, fills: list | None = None
    ) -> bool:
        """确认订单是否已成交（非被撤销）。

        通过查询交易所成交历史 (user_fills) 判断。
        优先使用外部传入的 fills 缓存，避免重复 API 调用。
        """
        if fills is None:
            try:
                user_address = self.order_manager.client.address
                fills = self.order_manager.client.info.user_fills(user_address) or []
            except Exception as e:
                self.logger.print_error(f"   [Grid] 查询成交记录失败: {e}")
                return False

        order_id = level.open_order_id if order_type == "open" else level.close_order_id

        for fill in fills:
            if fill.get("oid") == order_id:
                price = to_decimal(fill.get("px", "0"))
                amount = to_decimal(fill.get("sz", "0"))
                timestamp = fill.get("time", time.time())

                if order_type == "open":
                    level.open_fill_price = price
                    level.open_fill_amount = amount
                    level.open_fill_time = timestamp
                else:
                    level.close_fill_price = price
                    level.close_fill_amount = amount
                    level.close_fill_time = timestamp
                return True

        return False

    def _place_close_order(self, symbol: str, level: GridLevel):
        """根据开仓实际成交价计算平仓价格并挂平仓单。"""
        if level.open_fill_price is None or level.open_fill_amount is None:
            self.logger.print_warning(f"   [Grid] {level.id} 缺少开仓成交数据，无法挂平仓单")
            return

        # 从 state 获取 tp_ratio
        grid_data = self.state["active_grids"].get(symbol, {})
        config = grid_data.get("config", {})
        params = config.get("parameters", config)
        tp_ratio = to_decimal(params.get("tp_ratio", "0.005"))

        if level.side == "LONG":
            # 做多平仓 = 卖出，价格 = 开仓价 x (1 + tp_ratio)
            close_price = level.open_fill_price * (Decimal("1") + tp_ratio)
            is_buy = False
        else:
            # 做空平仓 = 买入，价格 = 开仓价 x (1 - tp_ratio)
            close_price = level.open_fill_price * (Decimal("1") - tp_ratio)
            is_buy = True

        formatted_price = self.order_manager.client.format_price(symbol, float(close_price))

        result = self.order_manager.client.place_limit_order(
            symbol=symbol,
            is_buy=is_buy,
            size=float(level.open_fill_amount),
            price=formatted_price,
            reduce_only=True,
        )

        # 校验内层 statuses：HL 拒单时外层仍为 status=ok，错误藏在 statuses[].error，
        # 仅判外层会把被拒平仓单误记为 PLACED/CLOSE_PENDING，导致持仓失去对冲裸奔
        order_ok, order_err = self.order_manager.client.check_order_success(result)
        if order_ok:
            oid = self._extract_oid(result)
            if oid:
                level.close_order_id = oid
                level.state = GridLevelState.CLOSE_PENDING
                self.logger.print_info(
                    f"   [Grid] {level.id} 挂平仓单 {'买' if is_buy else '卖'} "
                    f"@ ${formatted_price} (reduce_only)"
                )
                self.logger.log_trade(
                    symbol=symbol,
                    action=f"GRID_CLOSE_{'BUY' if is_buy else 'SELL'}",
                    amount=float(level.open_fill_amount or 0),
                    price=float(formatted_price),
                    order_id=str(oid),
                    status="PLACED",
                )
        else:
            self.logger.print_warning(
                f"   [Grid] {level.id} 平仓单失败/被拒 @ ${formatted_price}: {order_err}"
            )
            cloud = get_cloud_logger()
            if cloud:
                cloud.send_alert(
                    symbol=symbol,
                    alert_type="grid_close_order_failed",
                    severity="high",
                    message=f"层级 {level.id} 平仓单失败 @ ${formatted_price}",
                    details={
                        "level_id": level.id,
                        "side": level.side,
                        "close_price": float(formatted_price),
                        "open_fill_price": float(level.open_fill_price)
                        if level.open_fill_price
                        else 0,
                        "result": str(result),
                    },
                )

    def _report_round_trip_close(self, symbol: str, pnl: float, forced: bool = False):
        """把逐轮盈亏上报给账户级风控（连亏熔断），统一处理异常与签名兼容。

        forced=True 表示风控强制平仓（紧急平仓/手术式减仓），连亏熔断据此区分
        「主动止盈」与「被动强平」语义。失败不得影响网格主流程——风控记账出错
        绝不能拖垮布单/同步，故吞掉异常仅记日志。
        """
        if self.on_round_trip_close is None:
            return
        try:
            try:
                self.on_round_trip_close(symbol, pnl, forced)
            except TypeError:
                # 兼容未升级的两参回调（如自定义接线/旧回测桩）
                self.on_round_trip_close(symbol, pnl)
        except Exception as e:
            self.logger.print_warning(f"   [Grid] round-trip 盈亏上报风控失败: {e}")

    def _record_round_trip(self, symbol: str, level: GridLevel):
        """在层级完成一轮开平仓时调用，记录 PnL。"""
        tracker = self.pnl_trackers.get(symbol)
        if not tracker:
            tracker = GridPnLTracker()
            self.pnl_trackers[symbol] = tracker

        pnl = tracker.record_round_trip(level)
        self.logger.print_info(
            f"   [Grid] {level.id} 完成第 {level.round_trip_count} 轮 | "
            f"PnL: {pnl:+.4f} | 累计: {level.cumulative_pnl:+.4f}"
        )
        # 每轮往返的已实现盈亏落盘（trades jsonl 的 pnl 字段历史上恒为 null，
        # 12.5 天亏 39% 无从归因即源于此）——归因标签 GRID_TP=主动止盈往返。
        self.logger.log_trade(
            symbol=symbol,
            action="GRID_ROUND_TRIP",
            amount=float(level.open_fill_amount or 0),
            price=float(level.close_fill_price or 0),
            order_id=str(level.close_order_id or ""),
            status="FILLED",
            pnl=float(pnl),
            reason="GRID_TP",
        )

        self._report_round_trip_close(symbol, float(pnl), forced=False)

        # 记录轮回完成和 PnL 到云端
        cloud = get_cloud_logger()
        if cloud:
            cloud.send_grid_event(
                symbol=symbol,
                action="round_trip_completed",
                details={
                    "level_id": level.id,
                    "side": level.side,
                    "round_trip_count": level.round_trip_count,
                    "pnl": float(pnl),
                    "cumulative_pnl": float(level.cumulative_pnl),
                    "open_price": float(level.open_fill_price) if level.open_fill_price else 0,
                    "close_price": float(level.close_fill_price) if level.close_fill_price else 0,
                    "amount": float(level.open_fill_amount) if level.open_fill_amount else 0,
                    "realized_pnl": float(tracker.realized_pnl),
                    "total_fees": float(tracker.realized_fees),
                    "total_round_trips": tracker.completed_round_trips,
                },
            )

    def _save_incremental_state(self, symbol: str):
        """保存增量同步后的层级状态和 PnL 数据。"""
        grid_data = self.state["active_grids"].get(symbol, {})
        levels = self.grid_levels.get(symbol, [])
        tracker = self.pnl_trackers.get(symbol)

        grid_data["levels"] = [level.to_dict() for level in levels]
        if tracker:
            grid_data["pnl"] = tracker.to_dict()
        grid_data["last_sync"] = time.time()

        self.state["active_grids"][symbol] = grid_data
        self._save_state()
