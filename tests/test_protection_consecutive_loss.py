"""
连续亏损保护插件测试
"""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.plugins.protections.base import ProtectionAction, ProtectionContext
from src.plugins.protections.consecutive_loss import ConsecutiveLossProtection


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


def make_ctx(equity=10000, timestamp=None):
    return ProtectionContext(
        balance=equity,
        equity=equity,
        unrealized_pnl=0,
        margin_used=0,
        current_positions=[],
        timestamp=timestamp or datetime.now(),
    )


class TestGlobalMode:
    """全局模式（per_symbol=False）"""

    def test_no_trigger_below_threshold(self, tmp_dir):
        """未达阈值不触发"""
        plugin = ConsecutiveLossProtection(
            config={"max_consecutive_losses": 3, "per_symbol": False},
            data_dir=tmp_dir,
        )
        plugin.on_trade_close("BTC", -100)
        plugin.on_trade_close("ETH", -50)
        result = plugin.check(make_ctx())
        assert result.triggered is False

    def test_triggers_at_threshold(self, tmp_dir):
        """连续 3 次亏损触发"""
        plugin = ConsecutiveLossProtection(
            config={"max_consecutive_losses": 3, "per_symbol": False, "pause_hours": 1.0},
            data_dir=tmp_dir,
        )
        plugin.on_trade_close("BTC", -100)
        plugin.on_trade_close("ETH", -50)
        plugin.on_trade_close("BTC", -80)

        result = plugin.check(make_ctx())
        assert result.triggered is True
        assert result.action == ProtectionAction.PAUSE_NEW_TRADES

    def test_profit_resets_counter(self, tmp_dir):
        """盈利重置连续亏损计数"""
        plugin = ConsecutiveLossProtection(
            config={"max_consecutive_losses": 3, "per_symbol": False},
            data_dir=tmp_dir,
        )
        plugin.on_trade_close("BTC", -100)
        plugin.on_trade_close("BTC", -100)
        plugin.on_trade_close("BTC", 200)  # 盈利，重置

        assert plugin._global_losses == 0
        result = plugin.check(make_ctx())
        assert result.triggered is False


class TestPerSymbolMode:
    """交易对级模式（per_symbol=True）"""

    def test_only_locks_affected_symbol(self, tmp_dir):
        """BTC 连续亏损只锁 BTC，ETH 不受影响"""
        plugin = ConsecutiveLossProtection(
            config={"max_consecutive_losses": 2, "per_symbol": True, "pause_hours": 1.0},
            data_dir=tmp_dir,
        )
        plugin.on_trade_close("BTC", -100)
        plugin.on_trade_close("BTC", -100)  # BTC 达阈值

        locked_btc, reason_btc = plugin.is_symbol_locked("BTC")
        locked_eth, reason_eth = plugin.is_symbol_locked("ETH")

        assert locked_btc is True
        assert "BTC" in reason_btc
        assert locked_eth is False

    def test_lock_expires_after_pause_hours(self, tmp_dir):
        """锁定过期后自动解锁"""
        plugin = ConsecutiveLossProtection(
            config={"max_consecutive_losses": 2, "per_symbol": True, "pause_hours": 1.0},
            data_dir=tmp_dir,
        )
        plugin.on_trade_close("BTC", -100)
        plugin.on_trade_close("BTC", -100)

        # 手动将锁定截止时间设为过去
        past = (datetime.now() - timedelta(hours=2)).isoformat()
        plugin._locked_symbols["BTC"] = past

        locked, _ = plugin.is_symbol_locked("BTC")
        assert locked is False

    def test_profit_resets_symbol_counter(self, tmp_dir):
        """盈利重置该交易对的亏损计数"""
        plugin = ConsecutiveLossProtection(
            config={"max_consecutive_losses": 3, "per_symbol": True},
            data_dir=tmp_dir,
        )
        plugin.on_trade_close("BTC", -100)
        plugin.on_trade_close("BTC", -100)
        plugin.on_trade_close("BTC", 200)  # 盈利

        assert plugin._symbol_losses.get("BTC", 0) == 0


class TestStatePersistence:
    """状态持久化"""

    def test_state_survives_restart(self, tmp_dir):
        """重启后连续亏损计数恢复"""
        p1 = ConsecutiveLossProtection(
            config={"max_consecutive_losses": 5, "per_symbol": True},
            data_dir=tmp_dir,
        )
        p1.on_trade_close("BTC", -100)
        p1.on_trade_close("BTC", -200)

        p2 = ConsecutiveLossProtection(
            config={"max_consecutive_losses": 5, "per_symbol": True},
            data_dir=tmp_dir,
        )
        assert p2._global_losses == 2
        assert p2._symbol_losses.get("BTC") == 2
