"""保护插件链测试：回撤熔断、单日亏损、连亏锁定与事件分发。"""

from datetime import datetime, timedelta

import pytest

from src.plugins.protections import ProtectionAction, ProtectionContext, ProtectionManager


def make_context(equity: float, timestamp: datetime | None = None) -> ProtectionContext:
    ctx = ProtectionContext(
        balance=equity,
        equity=equity,
        unrealized_pnl=0.0,
        margin_used=0.0,
        current_positions=[],
    )
    if timestamp:
        ctx.timestamp = timestamp
    return ctx


def make_manager(config: list[dict], tmp_path) -> ProtectionManager:
    return ProtectionManager(protections_config=config, data_dir=tmp_path / "protection")


class TestMaxDrawdown:
    def test_drawdown_triggers_close_all(self, tmp_path):
        manager = make_manager([{"name": "max_drawdown", "max_drawdown_pct": 0.10}], tmp_path)
        assert manager.check_all(make_context(1000.0)) == []  # 建立峰值
        results = manager.check_all(make_context(850.0))  # 回撤 15%
        assert ProtectionManager.get_most_severe_action(results) == (
            ProtectionAction.CLOSE_ALL_POSITIONS
        )

    def test_small_drawdown_not_triggered(self, tmp_path):
        manager = make_manager([{"name": "max_drawdown", "max_drawdown_pct": 0.10}], tmp_path)
        manager.check_all(make_context(1000.0))
        assert manager.check_all(make_context(950.0)) == []  # 回撤 5%，未达阈值


class TestDailyLoss:
    def test_daily_loss_pauses_new_trades(self, tmp_path):
        manager = make_manager([{"name": "daily_loss", "max_daily_loss_pct": 0.05}], tmp_path)
        manager.check_all(make_context(1000.0))  # 建立当日起点
        results = manager.check_all(make_context(940.0))  # 当日 -6%
        assert ProtectionManager.get_most_severe_action(results) == (
            ProtectionAction.PAUSE_NEW_TRADES
        )


class TestConsecutiveLoss:
    CONFIG = [
        {
            "name": "consecutive_loss",
            "max_consecutive_losses": 3,
            "per_symbol": True,
            "pause_hours": 4,
        }
    ]

    def test_per_symbol_lock_after_streak(self, tmp_path):
        manager = make_manager(self.CONFIG, tmp_path)
        for _ in range(3):
            manager.on_trade_close(symbol="BTC", pnl=-5.0)
        locked, reason = manager.is_symbol_locked("BTC")
        assert locked is True
        assert "BTC" in reason or "连续亏损" in reason
        # 其他交易对不受影响
        assert manager.is_symbol_locked("ETH")[0] is False

    def test_profit_resets_streak(self, tmp_path):
        manager = make_manager(self.CONFIG, tmp_path)
        manager.on_trade_close(symbol="BTC", pnl=-5.0)
        manager.on_trade_close(symbol="BTC", pnl=-5.0)
        manager.on_trade_close(symbol="BTC", pnl=+8.0)  # 盈利重置
        manager.on_trade_close(symbol="BTC", pnl=-5.0)
        assert manager.is_symbol_locked("BTC")[0] is False

    def test_forced_profit_does_not_reset_when_configured(self, tmp_path):
        config = [dict(self.CONFIG[0], forced_close_no_reset=True)]
        manager = make_manager(config, tmp_path)
        manager.on_trade_close(symbol="BTC", pnl=-5.0)
        manager.on_trade_close(symbol="BTC", pnl=-5.0)
        # 风控强平的浮盈了结不算「打破连亏」
        manager.on_trade_close(symbol="BTC", pnl=+3.0, forced=True)
        manager.on_trade_close(symbol="BTC", pnl=-5.0)
        assert manager.is_symbol_locked("BTC")[0] is True

    def test_lock_expires(self, tmp_path):
        manager = make_manager(self.CONFIG, tmp_path)
        for _ in range(3):
            manager.on_trade_close(symbol="BTC", pnl=-5.0)
        future = datetime.now() + timedelta(hours=5)
        assert manager.is_symbol_locked("BTC", timestamp=future)[0] is False


class TestManagerBehavior:
    def test_unknown_plugin_ignored(self, tmp_path):
        manager = make_manager([{"name": "not_exists"}], tmp_path)
        assert manager.plugins == []

    def test_disabled_plugin_ignored(self, tmp_path):
        manager = make_manager([{"name": "max_drawdown", "enabled": False}], tmp_path)
        assert manager.plugins == []

    def test_most_severe_action_ordering(self):
        from src.plugins.protections.base import ProtectionReturn

        results = [
            ProtectionReturn(triggered=True, action=ProtectionAction.PAUSE_NEW_TRADES),
            ProtectionReturn(triggered=True, action=ProtectionAction.CLOSE_ALL_POSITIONS),
        ]
        assert ProtectionManager.get_most_severe_action(results) == (
            ProtectionAction.CLOSE_ALL_POSITIONS
        )
        assert ProtectionManager.get_most_severe_action([]) == ProtectionAction.NONE

    def test_trigger_callback_invoked(self, tmp_path):
        triggered: list[str] = []
        manager = ProtectionManager(
            protections_config=[{"name": "max_drawdown", "max_drawdown_pct": 0.10}],
            data_dir=tmp_path / "protection",
            on_protection_triggered=triggered.append,
        )
        manager.check_all(make_context(1000.0))
        manager.check_all(make_context(800.0))
        assert len(triggered) == 1
        assert "回撤" in triggered[0]


@pytest.fixture(autouse=True)
def _isolate_cwd_data(monkeypatch, tmp_path):
    """防止插件把状态文件写进仓库的 data/ 目录。"""
    monkeypatch.chdir(tmp_path)


class TestConsecutiveLossPauseExpiry:
    def test_global_counter_resets_after_pause_expires(self, tmp_path):
        # 历史缺陷：暂停到期只清 _is_paused 不清计数，空仓时下一次 check
        # 立即因计数仍达阈值重新触发，每个冷却期循环一次=永久锁死
        manager = make_manager(
            [
                {
                    "name": "consecutive_loss",
                    "max_consecutive_losses": 2,
                    "per_symbol": False,
                    "pause_hours": 1,
                }
            ],
            tmp_path,
        )
        t0 = datetime(2026, 8, 5, 10, 0, 0)
        manager.on_trade_close("BTC", pnl=-1.0, timestamp=t0)
        manager.on_trade_close("BTC", pnl=-1.0, timestamp=t0)
        results = manager.check_all(make_context(1000.0, timestamp=t0))
        assert results and results[0].action == ProtectionAction.PAUSE_NEW_TRADES

        # 暂停期后（无任何平仓事件）：必须恢复且不再重复触发
        t1 = t0 + timedelta(hours=1, minutes=1)
        assert manager.check_all(make_context(1000.0, timestamp=t1)) == []
        t2 = t1 + timedelta(minutes=5)
        assert manager.check_all(make_context(1000.0, timestamp=t2)) == []


class TestDrawdownSuspectSample:
    def test_single_collapse_sample_not_trusted(self, tmp_path):
        # 单次骤降逾 50%（统一账户接口降级形态）：首个样本只告警不熔断
        manager = make_manager([{"name": "max_drawdown", "max_drawdown_pct": 0.10}], tmp_path)
        assert manager.check_all(make_context(1000.0)) == []  # 峰值 1000
        results = manager.check_all(make_context(200.0))  # 骤降 80%
        assert results == []  # 等待确认，不触发

    def test_recovered_sample_clears_suspicion(self, tmp_path):
        manager = make_manager([{"name": "max_drawdown", "max_drawdown_pct": 0.10}], tmp_path)
        manager.check_all(make_context(1000.0))
        manager.check_all(make_context(200.0))  # 坏采样
        assert manager.check_all(make_context(995.0)) == []  # 恢复，虚惊一场

    def test_persistent_collapse_confirmed_and_triggers(self, tmp_path):
        # 连续两周期骤降=真实回撤，第二个样本必须正常触发 CLOSE_ALL
        manager = make_manager([{"name": "max_drawdown", "max_drawdown_pct": 0.10}], tmp_path)
        manager.check_all(make_context(1000.0))
        manager.check_all(make_context(200.0))
        results = manager.check_all(make_context(210.0))
        assert results and results[0].action == ProtectionAction.CLOSE_ALL_POSITIONS

    def test_normal_drawdown_unaffected_by_guard(self, tmp_path):
        # 未过坏采样阈值的正常回撤照常即时触发
        manager = make_manager([{"name": "max_drawdown", "max_drawdown_pct": 0.10}], tmp_path)
        manager.check_all(make_context(1000.0))
        results = manager.check_all(make_context(850.0))  # 回撤 15%，跌幅未过 50%
        assert results and results[0].action == ProtectionAction.CLOSE_ALL_POSITIONS
