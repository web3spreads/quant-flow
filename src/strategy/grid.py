"""
网格策略：一个完整网格周期的编排。

周期内的检查顺序（每一步都有真实事故背书，勿随意调换）：

1. 净额对冲平仓归因——先补记上一轮漏掉的平仓盈亏，本轮风控才能看到；
2. 账户级熔断（回撤/单日亏损）——触发则平仓撤单、跳过布单；
3. 净值快照 + 停机线——净值过低且无持仓时整轮短路（不拉行情、不调 LLM）；
4. Triple Barrier——独立于 AI 决策的全局兜底止损，每轮必查；
5. 趋势过滤——多周期一致强势时暂停加仓（迟滞确认去抖），可选平逆势库存；
6. AI 决策 → LLM 健康跟踪 → 空转自愈 → GridManager 布单同步。
"""

from typing import Any

from src.config import GridConfig
from src.data.indicators import TechnicalIndicators, TrendConfirmTracker, detect_strong_trend
from src.data.market_data import MarketDataFetcher
from src.plugins.protections import ProtectionAction, ProtectionContext, ProtectionManager
from src.strategy.grid_agent import GridAgent
from src.trading.grid_manager import GridManager
from src.trading.order_manager import OrderManager
from src.utils.logger import TradingLogger


class GridStrategy:
    """单交易对网格策略（编排层，决策在 GridAgent、执行在 GridManager）。"""

    def __init__(
        self,
        symbol: str,
        grid_agent: GridAgent,
        grid_manager: GridManager,
        order_manager: OrderManager,
        market_fetcher: MarketDataFetcher,
        logger: TradingLogger,
        grid_config: GridConfig,
        timeframe: str = "1h",
        protection_manager: ProtectionManager | None = None,
    ):
        """
        Args:
            symbol: 交易对符号
            grid_agent: AI 决策引擎
            grid_manager: 网格执行管理器
            order_manager: 订单管理器（余额/持仓查询）
            market_fetcher: 行情获取器
            logger: 日志器
            grid_config: 网格配置
            timeframe: 决策 K 线周期
            protection_manager: 账户保护链（None=不做账户级熔断）
        """
        self.symbol = symbol
        self.grid_agent = grid_agent
        self.grid_manager = grid_manager
        self.order_manager = order_manager
        self.market_fetcher = market_fetcher
        self.logger = logger
        self.config = grid_config
        self.timeframe = timeframe
        self.protection_manager = protection_manager

        # 趋势过滤迟滞确认器：连续 N 周期同向强趋势才动作（单周期瞬时误判
        # 是线上 12.5 天 145 次强平的主要来源）
        self._trend_tracker = TrendConfirmTracker(
            confirm_cycles=grid_config.trend_confirm_cycles,
            flatten_min_cycles=grid_config.flatten_min_cycles,
        )
        # LLM 连续故障计数 / 空转连续计数 / 本轮故障是否已告警
        self._llm_failure_streak = 0
        self._idle_streak = 0
        self._llm_alert_sent = False

    # ── 主流程 ────────────────────────────────────────────────────────────

    def run_cycle(self) -> None:
        """执行一个网格周期（内部兜住所有异常，不向调度层抛出）。"""
        try:
            self._run_cycle_inner()
        except Exception as e:
            self.logger.print_error(f"网格周期执行异常: {e}")
            self.logger.logger.exception(e)

    def _run_cycle_inner(self) -> None:
        symbol = self.symbol

        # 1. 净额对冲平仓归因：以链上成交补记层级状态机漏掉的平仓盈亏，
        #    先归因再风控——本轮新增亏损当轮就能被连亏熔断看到。
        #    归因出错绝不能拖垮交易周期，故兜住异常仅记日志。
        try:
            self.grid_manager.reconcile_netting_closes(symbol)
        except Exception as e:
            self.logger.print_warning(f"[Grid] 净额归因异常（不影响主流程）: {e}")

        # 2. 账户级风控熔断（回撤/单日亏损，按权益判定）
        if self._account_protection_triggered():
            return

        # 3. 净值快照 + 停机线短路
        if self._snapshot_and_halted():
            return

        # 4. Triple Barrier 每轮必查：独立于 AI action 分支，KEEP_GRID/ERROR
        #    周期也兜底止损。触发即已紧急平仓，本轮跳过布单。
        if self.grid_manager.check_barrier(symbol):
            self.logger.print_warning("[网格风控] Triple Barrier 触发，已紧急平仓，跳过本轮布单")
            return

        # 5. 行情与指标
        df = self.market_fetcher.fetch_ohlcv(symbol=symbol, timeframe=self.timeframe, limit=100)
        if df is None or df.empty:
            self.logger.print_error("无法获取市场数据，跳过本轮网格周期")
            return
        df = TechnicalIndicators.calculate_all_indicators(df)
        market_data = TechnicalIndicators.get_latest_indicators(df)
        self.logger.print_market_data(symbol, market_data)
        trends = TechnicalIndicators.get_multi_timeframe_trend(
            self.market_fetcher, symbol, cached_ohlcv={self.timeframe: df}
        )

        # 6. 趋势过滤 → AI 决策 → 健康跟踪 → 空转自愈
        ai_decision = self._decide(market_data, trends)
        self._track_llm_health(ai_decision)
        ai_decision = self._maybe_fallback_rebuild(ai_decision, market_data)

        # 7. 记录决策并同步网格
        action = ai_decision.get("action", "UNKNOWN")
        reason = ai_decision.get("reason", "")
        decision_ok = action in ("UPDATE_GRID", "KEEP_GRID")
        self.logger.log_decision(
            symbol=symbol,
            market_data=market_data,
            prompt="[GridAgent]",
            ai_response=reason,
            decision=action,
            action_details=ai_decision,
            status="SUCCESS" if decision_ok else "ERROR",
            error_message=None if decision_ok else reason,
            confidence=float(ai_decision.get("confidence", 0.0)),
        )
        self.grid_manager.sync_grid(symbol, ai_decision)

    # ── 决策与趋势过滤 ────────────────────────────────────────────────────

    def _decide(self, market_data: dict[str, Any], trends: dict[str, str]) -> dict[str, Any]:
        """趋势过滤优先：一致强势时合成 KEEP_GRID（暂停加仓），否则调 AI。"""
        raw_dir = (
            detect_strong_trend(
                trends,
                min_votes=self.config.trend_filter_min_votes,
                allowed_timeframes=list(self.config.trend_filter_timeframes),
            )
            if self.config.trend_filter_enabled
            else 0
        )
        trend_dir, flatten_allowed = self._trend_tracker.update(raw_dir)

        if trend_dir != 0:
            arrow = "上涨" if trend_dir > 0 else "下跌"
            _, streak = self._trend_tracker.streak
            self.logger.print_warning(
                f"[网格风控] 检测到强趋势（{arrow}，连续 {streak} 周期确认），"
                f"本轮暂停网格加仓，仅维持减仓保护单"
            )
            # 「暂停加仓」先行、「平逆势库存」靠后（需要更多连续确认）
            if self.config.flatten_adverse and flatten_allowed:
                self.grid_manager.flatten_adverse_inventory(self.symbol, trend_dir)
            return {
                "action": "KEEP_GRID",
                "mode": "NEUTRAL",
                "confidence": 0.0,
                "reason": f"强趋势({arrow})暂停加仓",
                # llm_ok：本轮压根没调 LLM，不计入 LLM 故障；
                # trend_paused：主动暂停不算空转，不得触发兜底重建
                "llm_ok": True,
                "trend_paused": True,
            }

        if raw_dir != 0:
            _, streak = self._trend_tracker.streak
            self.logger.print_info(
                f"[网格风控] 检测到强趋势信号但未达连续确认周期 "
                f"({streak}/{self.config.trend_confirm_cycles})，本轮暂不动作"
            )
        summary = self.grid_manager.get_grid_summary(self.symbol)
        return self.grid_agent.make_decision(market_data, trends, summary)

    # ── 账户级熔断 ────────────────────────────────────────────────────────

    def _account_protection_triggered(self) -> bool:
        """账户级熔断检查（回撤/单日亏损）。

        - ``CLOSE_ALL_POSITIONS``：平全部持仓 + 撤本交易对网格挂单，跳过本轮；
        - ``PAUSE_NEW_TRADES``：仅跳过本轮布单。

        余额/持仓查询失败时不误熔断（返回 False），避免网络抖动把网格误停。
        连亏熔断走另一条逐轮通路（GridManager 的 round-trip 回调），不在此处。
        """
        if not self.protection_manager:
            return False

        try:
            balance_info = self.order_manager.get_available_balance_info()
        except Exception as e:
            self.logger.print_warning(f"[网格风控] 获取余额失败，跳过风控检查: {e}")
            return False
        if balance_info.get("status") != "ok":
            self.logger.print_warning(
                f"[网格风控] 余额查询异常({balance_info.get('message')})，跳过风控检查"
            )
            return False
        try:
            positions = self.order_manager.get_current_positions()
        except Exception as e:
            self.logger.print_warning(f"[网格风控] 获取持仓失败，跳过风控检查: {e}")
            return False

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
            self.logger.print_warning(f"[网格风控]{r.reason}")

        if action == ProtectionAction.CLOSE_ALL_POSITIONS:
            self.logger.print_warning("[网格风控]账户熔断触发，平掉全部持仓并撤销网格挂单")
            for pos in positions or []:
                sym = pos.get("coin", "")
                if not sym:
                    continue
                try:
                    self.order_manager.close_position(sym)
                    # 强平属风控主动行为：清理持仓记录，不向连亏插件上报虚假 pnl
                    self.protection_manager.on_position_dropped(sym)
                except Exception as e:
                    self.logger.print_error(f"[网格风控]平仓失败 {sym}: {e}")
            # 撤销全部网格挂单（含 trigger）并清理本地状态，避免熔断期间挂单成交新增敞口
            try:
                self.grid_manager.cancel_all_orders(self.symbol)
                self.logger.print_info(f"[网格风控]已撤销 {self.symbol} 的全部网格挂单")
            except Exception as e:
                self.logger.print_error(f"[网格风控]撤销网格挂单失败: {e}")
            return True

        if action == ProtectionAction.PAUSE_NEW_TRADES:
            self.logger.print_warning("[网格风控]账户风控暂停新开仓，本轮跳过网格布单")
            return True
        return False

    # ── 净值快照与停机线 ──────────────────────────────────────────────────

    def _snapshot_and_halted(self) -> bool:
        """记录净值快照；净值低于停机线且无持仓时短路整轮（省行情与 LLM 开销）。"""
        try:
            balance_info = self.order_manager.get_available_balance_info()
        except Exception as e:
            self.logger.print_warning(f"[Grid] 获取余额失败，跳过快照/停机检查: {e}")
            return False
        if balance_info.get("status") != "ok":
            return False

        equity = float(balance_info.get("equity", balance_info.get("total", 0)) or 0)
        position_notional = 0.0
        has_position = False
        try:
            for pos in self.order_manager.get_current_positions() or []:
                if pos.get("coin") == self.symbol:
                    has_position = True
                    position_notional = abs(float(pos.get("positionValue", 0) or 0))
                    break
        except Exception as e:
            self.logger.print_warning(f"[Grid] 快照取持仓失败: {e}")
            has_position = True  # 查询失败按有持仓处理，不敢停机

        self.logger.log_equity_snapshot(
            equity=equity,
            available=float(balance_info.get("available", 0) or 0),
            unrealized_pnl=float(balance_info.get("unrealized_pnl", 0) or 0),
            position_notional=position_notional,
            symbol=self.symbol,
        )

        halt_line = self.config.halt_below_usd
        if 0 < equity < halt_line and not has_position:
            self.logger.print_warning(
                f"[Grid] 💤 净值 ${equity:.2f} 低于停机线 ${halt_line:.2f} 且无持仓，"
                f"跳过本轮网格周期（不拉行情/不调 LLM）"
            )
            return True
        return False

    # ── LLM 健康跟踪与空转自愈 ────────────────────────────────────────────

    def _track_llm_health(self, ai_decision: dict[str, Any]) -> None:
        """跟踪 LLM 连续故障，达阈值时升级告警（故障期间只告警一次）。

        依据 GridAgent 打的 ``llm_ok`` 标——这类兜底与 AI 真实 KEEP_GRID 同形，
        不做标记就无从分辨（历史教训：模型下线后 13 小时无告警）。
        """
        threshold = self.config.llm_failure_alert_cycles
        if threshold <= 0:
            return

        if ai_decision.get("llm_ok", True):
            if self._llm_failure_streak:
                self.logger.print_info(
                    f"[Grid] ✅ {self.symbol} LLM 决策已恢复正常"
                    f"（此前连续失败 {self._llm_failure_streak} 周期）"
                )
            self._llm_failure_streak = 0
            self._llm_alert_sent = False
            return

        self._llm_failure_streak += 1
        if self._llm_failure_streak < threshold or self._llm_alert_sent:
            return

        reason = str(ai_decision.get("reason", ""))[:300]
        self.logger.print_error(
            f"🚨 {self.symbol} 网格 LLM 决策连续 {self._llm_failure_streak} 个周期失败，"
            f"网格已停止更新，仅维持减仓保护单。最近一次原因: {reason}。"
            f"请检查 LLM 供应商模型名是否已下线、API 余额与网络连通性"
        )
        self._llm_alert_sent = True

    def _maybe_fallback_rebuild(
        self, ai_decision: dict[str, Any], market_data: dict[str, Any]
    ) -> dict[str, Any]:
        """LLM 持续不可用把网格拖成空转时，用纯市场数据兜底重建。

        空转死锁：层级被清空后只有 UPDATE_GRID 能重建，而 LLM 故障期每轮只产出
        ERROR 或兜底 KEEP_GRID——「维持现有网格」在无层级时等于维持一片空白。
        趋势过滤主动暂停的周期不算空转：那时候不建网格正是趋势过滤的目的。
        """
        cycles = self.config.llm_fallback_rebuild_cycles
        if cycles <= 0:
            return ai_decision

        if (
            ai_decision.get("action") == "UPDATE_GRID"
            or ai_decision.get("trend_paused")
            or not self.grid_manager.is_grid_idle(self.symbol)
        ):
            self._idle_streak = 0
            return ai_decision

        self._idle_streak += 1
        if self._idle_streak < cycles:
            self.logger.print_warning(
                f"[Grid] 💤 {self.symbol} 网格空转 {self._idle_streak}/{cycles} 周期，"
                f"达阈值后将按市场数据兜底重建"
            )
            return ai_decision

        # 无论兜底成功与否都清零：失败时也要重新攒够 cycles 周期才再试，
        # 避免资金不足等持续性原因导致每轮都重试刷屏。
        self._idle_streak = 0
        try:
            fallback = self.grid_agent.build_fallback_config(market_data)
        except Exception as e:
            self.logger.print_error(f"[Grid] 兜底重建网格失败: {e}")
            return ai_decision
        return fallback
