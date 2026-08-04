"""
调度引擎：组件装配 + 主循环。

职责边界：
- 装配所有组件（交易客户端、行情、LLM、保护链、两种策略）；
- 驱动永续周期（K 线节拍：对齐 K 线收盘后 offset 秒触发）与
  网格周期（固定间隔线程）；
- 两条周期共享一把交易锁——并发操作同一账户会踩乱持仓/挂单状态，
  冲突时后来者跳过本轮；
- 把策略执行结果接线到保护链（开平仓事件、逐轮盈亏）。

决策与执行逻辑不在这里：决策在 strategy 层，执行在 trading 层。
"""

import threading
import time
from typing import Any

from src.config import Config
from src.data.indicators import TechnicalIndicators
from src.data.market_data import MarketDataFetcher
from src.llm import LLMClient
from src.plugins.protections import ProtectionAction, ProtectionContext, ProtectionManager
from src.strategy.grid import GridStrategy
from src.strategy.grid_agent import GridAgent
from src.strategy.perp import PerpStrategy
from src.trading.client import HyperliquidClient
from src.trading.grid_barrier import TripleBarrierConfig
from src.trading.grid_manager import GridManager
from src.trading.order_manager import OrderManager
from src.utils.candle_align import next_candle_close_ts
from src.utils.logger import get_logger


class Engine:
    """交易引擎：装配组件并驱动两种策略的周期循环。"""

    def __init__(self, config: Config):
        self.config = config
        self.logger = get_logger()
        self.is_running = False
        # 永续与网格共享的交易锁：冲突时后来者跳过本轮
        self._trading_lock = threading.Lock()
        self._grid_thread: threading.Thread | None = None

        self._build_components()

    # ── 组件装配 ──────────────────────────────────────────────────────────

    def _build_components(self) -> None:
        cfg = self.config
        self.logger.print_section("🔧 初始化组件")

        self.market_fetcher = MarketDataFetcher(testnet=cfg.exchange.testnet)
        self.client = HyperliquidClient(
            private_key=cfg.exchange.private_key,
            account_address=cfg.exchange.account_address,
            testnet=cfg.exchange.testnet,
        )
        self.order_manager = OrderManager(
            client=self.client,
            take_profit_ratio=cfg.trading.take_profit_ratio,
            stop_loss_ratio=cfg.trading.stop_loss_ratio,
            default_leverage=cfg.trading.max_leverage,
        )
        self.llm = LLMClient(
            base_url=cfg.llm.base_url,
            api_key=cfg.llm.api_key,
            model=cfg.llm.model,
            temperature=cfg.llm.temperature,
            timeout=cfg.llm.timeout,
        )
        self.logger.print_info(f"✅ LLM: {cfg.llm.model} @ {cfg.llm.base_url}")

        self.protection_manager: ProtectionManager | None = None
        if cfg.protections:
            self.protection_manager = ProtectionManager(
                protections_config=cfg.protections,
                on_protection_triggered=lambda reason: self.logger.print_warning(
                    f"[风控] 保护触发: {reason}"
                ),
            )
            names = ", ".join(p.name for p in self.protection_manager.plugins)
            self.logger.print_info(f"✅ 保护链: {names or '（无有效插件）'}")
        else:
            self.logger.print_warning("⚠️ protections 为空，账户风控已全部关闭")

        # 永续策略：每个交易对一个实例
        self.perp_strategies: dict[str, PerpStrategy] = {}
        if cfg.trading.perp_enabled:
            for symbol in cfg.trading.symbols:
                self.perp_strategies[symbol] = PerpStrategy(
                    symbol=symbol,
                    order_manager=self.order_manager,
                    llm=self.llm,
                    logger=self.logger,
                    trading=cfg.trading,
                )
            self.logger.print_info(f"✅ 永续策略: {', '.join(self.perp_strategies)}")

        # 网格策略：单交易对（symbols[0]）
        self.grid_strategy: GridStrategy | None = None
        if cfg.trading.grid_enabled:
            symbol = cfg.trading.symbols[0]
            grid_manager = GridManager(
                self.order_manager,
                self.logger,
                state_file="data/grid_state.json",
                barrier_config=TripleBarrierConfig.from_config(cfg.grid.barrier),
                on_round_trip_close=self._on_grid_round_trip_close,
                max_position_notional_usd=cfg.grid.max_position_notional_usd,
                trend_flatten_surgical=True,
                inventory_cap_strict=True,
                keep_grid_reconcile=True,
                netting_attribution_enabled=True,
            )
            grid_agent = GridAgent(
                symbol=symbol,
                order_manager=self.order_manager,
                logger=self.logger,
                llm=self.llm,
                trade_amount=cfg.trading.max_trade_amount,
                width_pct_min=cfg.grid.width_min_pct,
                width_pct_max=cfg.grid.width_max_pct,
                width_pct_fallback=cfg.grid.width_fallback_pct,
                ai_width_blend_weight=cfg.grid.ai_blend_weight,
                force_neutral_mode=cfg.grid.force_neutral,
                max_leverage=cfg.trading.max_leverage,
                min_grid_num=cfg.grid.min_grid_num,
            )
            self.grid_strategy = GridStrategy(
                symbol=symbol,
                grid_agent=grid_agent,
                grid_manager=grid_manager,
                order_manager=self.order_manager,
                market_fetcher=self.market_fetcher,
                logger=self.logger,
                grid_config=cfg.grid,
                timeframe=cfg.trading.timeframe,
                protection_manager=self.protection_manager,
            )
            self.logger.print_info(f"✅ 网格策略: {symbol}")

        self._log_startup_balance()

    def _on_grid_round_trip_close(self, symbol: str, pnl: float, forced: bool = False) -> None:
        """网格逐轮平仓回调：把盈亏喂给连亏熔断插件（网格风控的逐轮通路）。"""
        if not self.protection_manager:
            return
        try:
            self.protection_manager.on_trade_close(symbol=symbol, pnl=float(pnl), forced=forced)
        except Exception as e:
            self.logger.print_warning(f"[网格风控] round-trip 盈亏上报失败: {e}")

    def _log_startup_balance(self) -> None:
        info = self.order_manager.get_available_balance_info()
        if info.get("status") == "ok":
            self.logger.print_info(
                f"💰 账户余额: 总值 ${info.get('total', 0):.2f} | "
                f"可用 ${info.get('available', 0):.2f}"
            )
        else:
            self.logger.print_warning(f"⚠️ 无法获取账户余额: {info.get('message')}")

    # ── 主循环 ────────────────────────────────────────────────────────────

    def run(self) -> None:
        """启动引擎（阻塞直到 stop）。"""
        cfg = self.config.trading
        if not cfg.perp_enabled and not cfg.grid_enabled:
            self.logger.print_error("perp_enabled 与 grid_enabled 均为 false，无策略可运行")
            return

        self.is_running = True
        try:
            if self.grid_strategy:
                self._grid_thread = threading.Thread(
                    target=self._grid_loop, daemon=True, name="grid-loop"
                )
                self._grid_thread.start()
                self.logger.print_info(
                    f"网格循环已启动 | 间隔: {self.config.grid.interval_minutes} 分钟"
                )

            if self.perp_strategies:
                self.logger.print_section(
                    f"K 线节拍驱动已启动 | 周期: {cfg.timeframe} | 偏移: {cfg.timeframe_offset}s"
                )
                if cfg.run_immediately:
                    self.perp_cycle()
                while self.is_running:
                    self._wait_next_candle()
                    if self.is_running:
                        self.perp_cycle()
            else:
                self.logger.print_section("纯网格模式运行中")
                while self.is_running:
                    time.sleep(1)
        finally:
            self.stop("收到停止信号或异常退出")

    def stop(self, reason: str = "手动停止") -> None:
        """优雅停机：等待进行中的网格周期完成，避免腰斩撤单/布单序列留下裸仓。"""
        if not self.is_running and self._grid_thread is None:
            return
        self.is_running = False
        self.logger.print_section(f"🛑 停止引擎: {reason}")
        if self._grid_thread and self._grid_thread.is_alive():
            self.logger.print_info("等待进行中的网格周期完成（最多 30s）...")
            self._grid_thread.join(timeout=30)
        self._grid_thread = None
        self.order_manager.shutdown()
        self.logger.print_info("引擎已停止")

    def _wait_next_candle(self) -> None:
        """等待到下一根 K 线收盘后 offset 秒（分段 sleep 以便快速响应停止信号）。"""
        cfg = self.config.trading
        target = next_candle_close_ts(cfg.timeframe) + cfg.timeframe_offset
        sleep_duration = max(target - time.time(), cfg.min_throttle_secs)
        target_str = time.strftime("%H:%M:%S", time.gmtime(target))
        self.logger.print_info(
            f"[节拍] 等待下一根 {cfg.timeframe} K 线 | 目标: {target_str} UTC | "
            f"等待: {sleep_duration:.0f}s"
        )
        end_time = time.time() + sleep_duration
        while time.time() < end_time and self.is_running:
            time.sleep(max(0.0, min(1.0, end_time - time.time())))

    # ── 永续周期 ──────────────────────────────────────────────────────────

    def perp_cycle(self) -> None:
        """执行一轮永续决策周期（所有交易对）。"""
        if not self._trading_lock.acquire(blocking=False):
            self.logger.print_warning("⏭️ 上一个交易周期仍在运行，跳过本次调度")
            return
        try:
            self._perp_cycle_inner()
        except Exception as e:
            self.logger.print_error(f"交易周期异常: {e}")
            self.logger.logger.exception(e)
        finally:
            self._trading_lock.release()

    def _perp_cycle_inner(self) -> None:
        cfg = self.config.trading
        self.logger.print_header(f"🔄 永续交易周期开始 - {time.strftime('%Y-%m-%d %H:%M:%S')}")

        positions = self.order_manager.get_current_positions()
        balance_info = self.order_manager.get_available_balance_info()
        if balance_info.get("status") != "ok":
            self.logger.print_error(f"❌ {balance_info.get('message')}，跳过本次交易周期")
            return
        available = float(balance_info.get("available", 0))
        self.logger.print_info(f"可用余额: ${available:.2f} | 持仓数: {len(positions)}")

        # 余额判定：不足以开新仓时仅管理现有持仓；无持仓则整轮跳过
        suggestion = self.order_manager.calculate_suggested_trade_amount(
            desired_amount=cfg.max_trade_amount, balance_info=balance_info
        )
        open_allowed = bool(suggestion.get("can_trade"))
        if not open_allowed:
            self.logger.print_warning(f"⚠️ {suggestion.get('reason')}")
            if not positions:
                self.logger.print_warning("无持仓且余额不足，跳过本次交易周期")
                return

        # 账户级风控
        open_allowed = self._enforce_protections(balance_info, positions) and open_allowed

        for symbol in cfg.symbols:
            try:
                self._run_symbol_cycle(symbol, positions, available, open_allowed)
            except Exception as e:
                self.logger.print_error(f"{symbol} 决策异常: {e}")
                self.logger.logger.exception(e)

        self.logger.print_header("✅ 永续交易周期完成")

    def _enforce_protections(
        self, balance_info: dict[str, Any], positions: list[dict[str, Any]]
    ) -> bool:
        """执行保护链检查，返回本轮是否允许开新仓。

        - ``CLOSE_ALL_POSITIONS``：平掉全部持仓（回撤熔断）；
        - ``PAUSE_NEW_TRADES``：禁止开新仓，仅管理持仓；
        - ``position_timeout`` 命中的交易对：直接平仓。

        风控强平不向连亏插件上报 pnl（避免虚假连亏计数），仅清理持仓记录。
        """
        if not self.protection_manager:
            return True

        context = ProtectionContext(
            balance=balance_info.get("available", 0),
            equity=balance_info.get("total", 0),
            unrealized_pnl=balance_info.get("unrealized_pnl", 0),
            margin_used=balance_info.get("occupied", 0),
            current_positions=positions or [],
        )
        results = self.protection_manager.check_all(context)
        action = ProtectionManager.get_most_severe_action(results)
        for r in results:
            self.logger.print_warning(f"[风控]{r.reason}")

        if action == ProtectionAction.CLOSE_ALL_POSITIONS:
            self.logger.print_warning("[风控]回撤保护触发，执行全部平仓")
            for pos in positions or []:
                sym = pos.get("coin", "")
                if not sym:
                    continue
                try:
                    self.order_manager.close_position(sym)
                    self.protection_manager.on_position_dropped(sym)
                    self.logger.print_info(f"[风控]已平仓: {sym}")
                except Exception as e:
                    self.logger.print_error(f"[风控]平仓失败 {sym}: {e}")
            return False

        # 超时持仓直接平仓（从 check_all 结果取，避免重复扫描）
        for r in results:
            if r.plugin_name == "position_timeout" and r.affected_symbols:
                for sym in r.affected_symbols:
                    self.logger.print_warning(f"[风控]持仓超时: {sym}，执行平仓")
                    try:
                        self.order_manager.close_position(sym)
                        self.protection_manager.on_position_dropped(sym)
                    except Exception as e:
                        self.logger.print_error(f"[风控]超时平仓失败 {sym}: {e}")

        if action == ProtectionAction.PAUSE_NEW_TRADES:
            self.logger.print_warning("[风控]保护插件已暂停新开仓，仅管理现有持仓")
            return False
        return True

    def _run_symbol_cycle(
        self,
        symbol: str,
        positions: list[dict[str, Any]],
        available: float,
        open_allowed: bool,
    ) -> None:
        """单交易对：行情 → 策略决策执行 → 风控事件接线 → 决策日志。"""
        self.logger.print_section(f"📊 {symbol} 决策")

        # 交易对级锁定（连亏 per-symbol 锁定）
        if self.protection_manager:
            locked, lock_reason = self.protection_manager.is_symbol_locked(symbol)
            if locked:
                self.logger.print_warning(f"[风控]{symbol} 已锁定: {lock_reason}")
                open_allowed = False

        cfg = self.config.trading
        df = self.market_fetcher.fetch_ohlcv(
            symbol=symbol, timeframe=cfg.timeframe, limit=cfg.candles_limit
        )
        if df is None or df.empty:
            self.logger.print_warning(f"无法获取 {symbol} 的市场数据，跳过")
            return
        df = TechnicalIndicators.calculate_all_indicators(df)
        market_data = TechnicalIndicators.get_latest_indicators(df)
        trends = TechnicalIndicators.get_multi_timeframe_trend(
            self.market_fetcher, symbol, cached_ohlcv={cfg.timeframe: df}
        )
        self.logger.print_market_data(symbol, market_data)
        self.logger.print_info(
            "多周期趋势: " + " | ".join(f"{tf}: {t}" for tf, t in trends.items())
        )

        record = self.perp_strategies[symbol].run_cycle(
            market_data=market_data,
            trends=trends,
            positions=positions,
            available_balance=available,
            open_allowed=open_allowed,
        )
        action = record.get("action", "HOLD")
        self.logger.print_info(
            f"[{symbol}] 决策: {action} | 置信度 {record.get('confidence', 0):.2f} | "
            f"{record.get('reason', '')}"
        )

        # 保护插件事件接线：仅在真实执行成功时上报
        if self.protection_manager and record.get("executed"):
            try:
                if action in ("BUY", "SELL_SHORT"):
                    self.protection_manager.on_trade_open(
                        symbol=symbol,
                        entry_price=float(record.get("entry_price", 0) or 0),
                        size=float(record.get("size", 0) or 0),
                        is_long=bool(record.get("is_long")),
                        leverage=int(record.get("leverage", 1) or 1),
                    )
                elif action == "CLOSE":
                    self.protection_manager.on_trade_close(
                        symbol=symbol, pnl=float(record.get("pnl", 0) or 0)
                    )
            except Exception as e:
                self.logger.print_warning(f"[{symbol}] 保护插件记录失败: {e}")

        self.logger.log_decision(
            symbol=symbol,
            market_data=market_data,
            prompt=record.get("prompt", ""),
            ai_response=record.get("raw_response", ""),
            decision=action,
            action_details={
                k: record.get(k)
                for k in ("executed", "size", "entry_price", "leverage", "pnl", "reason")
            },
            status="SUCCESS" if record.get("llm_ok", True) else "ERROR",
            error_message=None if record.get("llm_ok", True) else record.get("reason"),
            confidence=float(record.get("confidence", 0.0)),
        )

    # ── 网格周期线程 ──────────────────────────────────────────────────────

    def _grid_loop(self) -> None:
        """网格周期循环：固定间隔执行，与永续周期共锁互斥。"""
        interval = max(60, self.config.grid.interval_minutes * 60)
        if not self.config.trading.run_immediately:
            self._sleep_responsive(interval)
        while self.is_running:
            if self._trading_lock.acquire(blocking=False):
                try:
                    self.logger.print_header(
                        f"🔄 网格周期开始 - {time.strftime('%Y-%m-%d %H:%M:%S')}"
                    )
                    self.grid_strategy.run_cycle()
                    self.logger.print_header("✅ 网格周期完成")
                finally:
                    self._trading_lock.release()
            else:
                self.logger.print_warning("⏭️ 交易周期运行中，跳过本次网格周期")
            self._sleep_responsive(interval)

    def _sleep_responsive(self, seconds: float) -> None:
        """分段 sleep，以便快速响应停止信号。"""
        end_time = time.time() + seconds
        while time.time() < end_time and self.is_running:
            time.sleep(min(1.0, max(0.0, end_time - time.time())))
