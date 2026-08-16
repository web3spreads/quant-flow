"""网格策略编排测试：趋势过滤、Triple Barrier 短路、空转自愈与 LLM 健康跟踪。"""

from conftest import FakeOrderManager, make_ohlcv

from src.config import GridConfig
from src.plugins.protections import ProtectionAction
from src.strategy.grid import GridStrategy


class StubGridManager:
    """GridManager 桩：记录 sync/flatten 调用，行为可配置。"""

    def __init__(self):
        self.synced: list[dict] = []
        self.flatten_calls: list[int] = []
        self.barrier_triggered = False
        self.idle = True
        self.barrier_checks = 0
        self.maintain_calls = 0
        self.emergency_close_calls: list[str] = []
        self.emergency_close_ok = True
        self.retry_pending_calls = 0

    def reconcile_netting_closes(self, symbol):
        pass

    def retry_pending_emergency_close(self, symbol):
        self.retry_pending_calls += 1

    def check_barrier(self, symbol):
        self.barrier_checks += 1
        return self.barrier_triggered

    def get_grid_summary(self, symbol):
        return "无网格"

    def sync_grid(self, symbol, decision):
        self.synced.append(decision)

    def flatten_adverse_inventory(self, symbol, trend_dir):
        self.flatten_calls.append(trend_dir)

    def cancel_all_orders(self, symbol):
        pass

    def emergency_close_symbol(self, symbol, reason):
        self.emergency_close_calls.append(reason)
        return self.emergency_close_ok

    def maintain_protective_orders(self, symbol):
        self.maintain_calls += 1

    def is_grid_idle(self, symbol):
        return self.idle


class StubGridAgent:
    """GridAgent 桩：返回预置决策序列。"""

    def __init__(self, decisions, fallback=None):
        self.decisions = list(decisions)
        self.fallback = fallback or {
            "action": "UPDATE_GRID",
            "mode": "NEUTRAL",
            "confidence": 0.0,
            "reason": "兜底重建",
            "llm_ok": False,
            "fallback": True,
        }
        self.fallback_calls = 0

    def make_decision(self, market_data, trends, summary):
        return self.decisions.pop(0) if len(self.decisions) > 1 else self.decisions[0]

    def build_fallback_config(self, market_data):
        self.fallback_calls += 1
        return dict(self.fallback)


class StubFetcher:
    def fetch_ohlcv(self, symbol=None, timeframe=None, limit=100):
        return make_ohlcv()


DEGRADED_KEEP = {
    "action": "KEEP_GRID",
    "mode": "NEUTRAL",
    "confidence": 0.0,
    "reason": "LLM 故障兜底",
    "llm_ok": False,
}


def make_strategy(
    agent, manager=None, grid_config=None, test_logger=None
) -> tuple[GridStrategy, StubGridManager]:
    manager = manager or StubGridManager()
    strategy = GridStrategy(
        symbol="ETH",
        grid_agent=agent,
        grid_manager=manager,
        order_manager=FakeOrderManager(available=500.0),
        market_fetcher=StubFetcher(),
        logger=test_logger,
        grid_config=grid_config or GridConfig(trend_filter_enabled=False),
        timeframe="1h",
        protection_manager=None,
    )
    return strategy, manager


class TestBarrierShortCircuit:
    def test_barrier_triggered_skips_cycle(self, test_logger):
        manager = StubGridManager()
        manager.barrier_triggered = True
        strategy, manager = make_strategy(
            StubGridAgent([DEGRADED_KEEP]), manager=manager, test_logger=test_logger
        )
        strategy.run_cycle()
        assert manager.synced == []  # 触发屏障后本轮不布单


class TestFallbackRebuild:
    def test_idle_streak_triggers_fallback(self, test_logger):
        agent = StubGridAgent([DEGRADED_KEEP])
        config = GridConfig(trend_filter_enabled=False, llm_fallback_rebuild_cycles=2)
        strategy, manager = make_strategy(agent, grid_config=config, test_logger=test_logger)

        strategy.run_cycle()  # 空转第 1 周期：未达阈值
        assert manager.synced[-1]["action"] == "KEEP_GRID"
        assert agent.fallback_calls == 0

        strategy.run_cycle()  # 空转第 2 周期：触发兜底重建
        assert agent.fallback_calls == 1
        assert manager.synced[-1]["action"] == "UPDATE_GRID"
        assert manager.synced[-1]["fallback"] is True

    def test_active_grid_not_counted_as_idle(self, test_logger):
        agent = StubGridAgent([DEGRADED_KEEP])
        manager = StubGridManager()
        manager.idle = False  # 交易所上仍有活跃网格挂单
        config = GridConfig(trend_filter_enabled=False, llm_fallback_rebuild_cycles=1)
        strategy, manager = make_strategy(
            agent, manager=manager, grid_config=config, test_logger=test_logger
        )
        strategy.run_cycle()
        assert agent.fallback_calls == 0

    def test_disabled_by_zero_cycles(self, test_logger):
        agent = StubGridAgent([DEGRADED_KEEP])
        config = GridConfig(trend_filter_enabled=False, llm_fallback_rebuild_cycles=0)
        strategy, _ = make_strategy(agent, grid_config=config, test_logger=test_logger)
        for _ in range(5):
            strategy.run_cycle()
        assert agent.fallback_calls == 0


class TestTrendFilter:
    def _strong_trend_config(self, **overrides) -> GridConfig:
        defaults = {
            "trend_filter_enabled": True,
            "trend_filter_min_votes": 1,
            "trend_confirm_cycles": 1,
            "flatten_min_cycles": 2,
            "flatten_adverse": True,
        }
        defaults.update(overrides)
        return GridConfig(**defaults)

    def test_strong_trend_pauses_grid(self, test_logger, monkeypatch):
        monkeypatch.setattr("src.strategy.grid.detect_strong_trend", lambda *a, **kw: 1)
        agent = StubGridAgent([{"action": "UPDATE_GRID", "llm_ok": True, "reason": "不该被调用"}])
        strategy, manager = make_strategy(
            agent, grid_config=self._strong_trend_config(), test_logger=test_logger
        )
        strategy.run_cycle()
        decision = manager.synced[-1]
        assert decision["action"] == "KEEP_GRID"
        assert decision["trend_paused"] is True
        assert decision["llm_ok"] is True  # 未调 LLM，不计入故障

    def test_flatten_waits_for_more_confirmation(self, test_logger, monkeypatch):
        monkeypatch.setattr("src.strategy.grid.detect_strong_trend", lambda *a, **kw: 1)
        agent = StubGridAgent([DEGRADED_KEEP])
        strategy, manager = make_strategy(
            agent, grid_config=self._strong_trend_config(), test_logger=test_logger
        )
        strategy.run_cycle()  # 第 1 周期：暂停生效，但平仓需 2 周期确认
        assert manager.flatten_calls == []
        strategy.run_cycle()  # 第 2 周期：允许平逆势库存
        assert manager.flatten_calls == [1]

    def test_trend_pause_not_counted_as_idle(self, test_logger, monkeypatch):
        monkeypatch.setattr("src.strategy.grid.detect_strong_trend", lambda *a, **kw: 1)
        agent = StubGridAgent([DEGRADED_KEEP])
        config = self._strong_trend_config(llm_fallback_rebuild_cycles=1)
        strategy, _ = make_strategy(agent, grid_config=config, test_logger=test_logger)
        for _ in range(3):
            strategy.run_cycle()
        assert agent.fallback_calls == 0  # 主动暂停不算空转


class TestLLMHealthTracking:
    def test_streak_counts_and_resets(self, test_logger):
        agent = StubGridAgent([DEGRADED_KEEP])
        config = GridConfig(trend_filter_enabled=False, llm_failure_alert_cycles=2)
        strategy, _ = make_strategy(agent, grid_config=config, test_logger=test_logger)

        strategy.run_cycle()
        assert strategy._llm_failure_streak == 1
        strategy.run_cycle()
        assert strategy._llm_failure_streak == 2
        assert strategy._llm_alert_sent is True

        # LLM 恢复后计数与告警标记复位
        agent.decisions = [{"action": "KEEP_GRID", "llm_ok": True, "reason": "恢复"}]
        strategy.run_cycle()
        assert strategy._llm_failure_streak == 0
        assert strategy._llm_alert_sent is False


class TestHaltLine:
    def test_low_equity_without_position_halts(self, test_logger):
        agent = StubGridAgent([{"action": "KEEP_GRID", "llm_ok": True, "reason": "不该被调用"}])
        config = GridConfig(trend_filter_enabled=False, halt_below_usd=1000.0)
        strategy, manager = make_strategy(agent, grid_config=config, test_logger=test_logger)
        # FakeOrderManager 净值 500 < 停机线 1000 且无持仓 → 整轮短路
        strategy.run_cycle()
        assert manager.synced == []


class StubProtectionManager:
    """保护链桩：动作可配置，记录 on_position_dropped 调用。"""

    def __init__(self, action):
        self.action = action
        self.dropped: list[str] = []
        self.locked_symbols: set[str] = set()

    def check_all(self, context):
        from src.plugins.protections import ProtectionReturn

        if self.action == ProtectionAction.NONE:
            return []
        return [ProtectionReturn(triggered=True, action=self.action, reason="桩触发")]

    def on_position_dropped(self, symbol):
        self.dropped.append(symbol)

    def is_symbol_locked(self, symbol, timestamp=None):
        if symbol in self.locked_symbols:
            return True, "连亏锁定桩"
        return False, ""


def make_protected_strategy(agent, pm, manager=None, positions=None, test_logger=None):
    manager = manager or StubGridManager()
    om = FakeOrderManager(available=500.0, positions=positions or [])
    strategy = GridStrategy(
        symbol="ETH",
        grid_agent=agent,
        grid_manager=manager,
        order_manager=om,
        market_fetcher=StubFetcher(),
        logger=test_logger,
        grid_config=GridConfig(trend_filter_enabled=False),
        timeframe="1h",
        protection_manager=pm,
    )
    return strategy, manager, om


class TestAccountProtectionOrdering:
    """账户级保护与 Triple Barrier / 保护单维护的先后关系（历史缺陷回归）。"""

    def test_pause_still_checks_barrier_and_maintains(self, test_logger):
        # PAUSE_NEW_TRADES 只暂停新开仓：Barrier 必须照查、保护单必须照维护，
        # 且不得调用 LLM/布单
        pm = StubProtectionManager(ProtectionAction.PAUSE_NEW_TRADES)
        agent = StubGridAgent([{"action": "UPDATE_GRID", "llm_ok": True, "reason": "不该被调用"}])
        strategy, manager, _ = make_protected_strategy(agent, pm, test_logger=test_logger)
        strategy.run_cycle()
        assert manager.barrier_checks == 1
        assert manager.maintain_calls == 1
        assert manager.synced == []

    def test_pause_barrier_trigger_takes_priority(self, test_logger):
        # 暂停期内屏障触发：紧急平仓优先，本轮不再做保护单维护
        pm = StubProtectionManager(ProtectionAction.PAUSE_NEW_TRADES)
        manager = StubGridManager()
        manager.barrier_triggered = True
        agent = StubGridAgent([DEGRADED_KEEP])
        strategy, manager, _ = make_protected_strategy(
            agent, pm, manager=manager, test_logger=test_logger
        )
        strategy.run_cycle()
        assert manager.barrier_checks == 1
        assert manager.maintain_calls == 0
        assert manager.synced == []

    def test_close_all_uses_verified_grid_close(self, test_logger):
        # CLOSE_ALL：网格交易对走 emergency_close_symbol；成功才 on_position_dropped
        pm = StubProtectionManager(ProtectionAction.CLOSE_ALL_POSITIONS)
        agent = StubGridAgent([DEGRADED_KEEP])
        eth_pos = {"coin": "ETH", "szi": "0.5", "entryPx": "100"}
        strategy, manager, _ = make_protected_strategy(
            agent, pm, positions=[eth_pos], test_logger=test_logger
        )
        strategy.run_cycle()
        assert manager.emergency_close_calls == ["账户熔断强平"]
        assert pm.dropped == ["ETH"]
        assert manager.synced == []

    def test_close_all_failure_keeps_protection_records(self, test_logger):
        # 平仓失败：不得清理保护插件的持仓记录（超时强平等保护须继续有效）
        pm = StubProtectionManager(ProtectionAction.CLOSE_ALL_POSITIONS)
        manager = StubGridManager()
        manager.emergency_close_ok = False
        agent = StubGridAgent([DEGRADED_KEEP])
        eth_pos = {"coin": "ETH", "szi": "0.5", "entryPx": "100"}
        strategy, manager, _ = make_protected_strategy(
            agent, pm, manager=manager, positions=[eth_pos], test_logger=test_logger
        )
        strategy.run_cycle()
        assert manager.emergency_close_calls == ["账户熔断强平"]
        assert pm.dropped == []

    def test_pending_emergency_close_retried_each_cycle(self, test_logger):
        pm = StubProtectionManager(ProtectionAction.NONE)
        agent = StubGridAgent([DEGRADED_KEEP])
        strategy, manager, _ = make_protected_strategy(agent, pm, test_logger=test_logger)
        strategy.run_cycle()
        strategy.run_cycle()
        assert manager.retry_pending_calls == 2


class TestSymbolLockDowngrade:
    def test_update_grid_downgraded_when_locked(self, test_logger):
        # 连亏 per-symbol 锁定期内 UPDATE_GRID 必须降级 KEEP_GRID（网格路径消费锁）
        pm = StubProtectionManager(ProtectionAction.NONE)
        pm.locked_symbols.add("ETH")
        agent = StubGridAgent(
            [{"action": "UPDATE_GRID", "llm_ok": True, "confidence": 0.9, "reason": "扩建"}]
        )
        strategy, manager, _ = make_protected_strategy(agent, pm, test_logger=test_logger)
        strategy.run_cycle()
        assert manager.synced[-1]["action"] == "KEEP_GRID"
        assert "锁定" in manager.synced[-1]["reason"]


class TestFallbackOnlyForLLMFailure:
    def test_healthy_keep_grid_never_triggers_fallback(self, test_logger):
        # LLM 健康时的连续 KEEP_GRID 是 AI 决策，兜底重建不得覆盖
        agent = StubGridAgent([{"action": "KEEP_GRID", "llm_ok": True, "reason": "AI 主动维持"}])
        config = GridConfig(trend_filter_enabled=False, llm_fallback_rebuild_cycles=1)
        strategy, _ = make_strategy(agent, grid_config=config, test_logger=test_logger)
        for _ in range(3):
            strategy.run_cycle()
        assert agent.fallback_calls == 0
