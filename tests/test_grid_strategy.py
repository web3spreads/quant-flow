"""网格策略编排测试：趋势过滤、Triple Barrier 短路、空转自愈与 LLM 健康跟踪。"""

from conftest import FakeOrderManager, make_ohlcv

from src.config import GridConfig
from src.strategy.grid import GridStrategy


class StubGridManager:
    """GridManager 桩：记录 sync/flatten 调用，行为可配置。"""

    def __init__(self):
        self.synced: list[dict] = []
        self.flatten_calls: list[int] = []
        self.barrier_triggered = False
        self.idle = True

    def reconcile_netting_closes(self, symbol):
        pass

    def check_barrier(self, symbol):
        return self.barrier_triggered

    def get_grid_summary(self, symbol):
        return "无网格"

    def sync_grid(self, symbol, decision):
        self.synced.append(decision)

    def flatten_adverse_inventory(self, symbol, trend_dir):
        self.flatten_calls.append(trend_dir)

    def cancel_all_orders(self, symbol):
        pass

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
