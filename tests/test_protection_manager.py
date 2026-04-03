"""
保护插件管理器测试
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.plugins.protections import ProtectionManager
from src.plugins.protections.base import (
    ProtectionAction,
    ProtectionContext,
    ProtectionReturn,
)


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


def make_ctx(equity=10000):
    return ProtectionContext(
        balance=equity,
        equity=equity,
        unrealized_pnl=0,
        margin_used=0,
        current_positions=[],
    )


class TestEmptyConfig:
    """空配置"""

    def test_empty_list_no_plugins(self, tmp_dir):
        """空列表等同于关闭所有风控"""
        manager = ProtectionManager(protections_config=[], data_dir=tmp_dir)
        assert len(manager.plugins) == 0
        results = manager.check_all(make_ctx())
        assert results == []

    def test_no_side_effects(self, tmp_dir):
        """空配置下事件分发不报错"""
        manager = ProtectionManager(protections_config=[], data_dir=tmp_dir)
        manager.on_trade_open("BTC", 50000, 0.1, True, 5)
        manager.on_trade_close("BTC", 100)


class TestPluginLoading:
    """插件加载"""

    def test_loads_known_plugins(self, tmp_dir):
        """已知插件正确加载"""
        config = [
            {"name": "max_drawdown", "max_drawdown_pct": 0.10},
            {"name": "daily_loss", "max_daily_loss_pct": 0.05},
        ]
        manager = ProtectionManager(protections_config=config, data_dir=tmp_dir)
        assert len(manager.plugins) == 2

    def test_skips_disabled_plugins(self, tmp_dir):
        """disabled 插件被跳过"""
        config = [
            {"name": "max_drawdown", "enabled": False},
            {"name": "daily_loss", "max_daily_loss_pct": 0.05},
        ]
        manager = ProtectionManager(protections_config=config, data_dir=tmp_dir)
        assert len(manager.plugins) == 1

    def test_skips_unknown_plugins(self, tmp_dir):
        """未知插件名被跳过"""
        config = [{"name": "nonexistent_plugin"}]
        manager = ProtectionManager(protections_config=config, data_dir=tmp_dir)
        assert len(manager.plugins) == 0


class TestCheckAll:
    """check_all 执行"""

    def test_returns_triggered_results(self, tmp_dir):
        """只返回已触发的结果"""
        config = [
            {"name": "max_drawdown", "max_drawdown_pct": 0.10},
        ]
        manager = ProtectionManager(protections_config=config, data_dir=tmp_dir)

        # 设置峰值
        manager.check_all(make_ctx(10000))
        # 触发回撤
        results = manager.check_all(make_ctx(8500))
        assert len(results) == 1
        assert results[0].triggered is True

    def test_callback_on_trigger(self, tmp_dir):
        """触发时调用回调"""
        callback = MagicMock()
        config = [{"name": "max_drawdown", "max_drawdown_pct": 0.10}]
        manager = ProtectionManager(
            protections_config=config, data_dir=tmp_dir, on_protection_triggered=callback
        )

        manager.check_all(make_ctx(10000))
        manager.check_all(make_ctx(8500))

        callback.assert_called_once()


class TestMostSevereAction:
    """动作优先级"""

    def test_close_all_is_most_severe(self):
        """CLOSE_ALL_POSITIONS 优先级最高"""
        results = [
            ProtectionReturn(triggered=True, action=ProtectionAction.PAUSE_NEW_TRADES, reason="a"),
            ProtectionReturn(
                triggered=True, action=ProtectionAction.CLOSE_ALL_POSITIONS, reason="b"
            ),
        ]
        action = ProtectionManager.get_most_severe_action(results)
        assert action == ProtectionAction.CLOSE_ALL_POSITIONS

    def test_empty_results_returns_none(self):
        """空结果返回 NONE"""
        action = ProtectionManager.get_most_severe_action([])
        assert action == ProtectionAction.NONE


class TestSymbolLocking:
    """交易对锁定查询"""

    def test_locks_via_consecutive_loss(self, tmp_dir):
        """通过连续亏损插件锁定交易对"""
        config = [
            {"name": "consecutive_loss", "max_consecutive_losses": 2, "per_symbol": True,
             "pause_hours": 1.0},
        ]
        manager = ProtectionManager(protections_config=config, data_dir=tmp_dir)

        manager.on_trade_close("BTC", -100)
        manager.on_trade_close("BTC", -100)

        locked, reason = manager.is_symbol_locked("BTC")
        assert locked is True
        assert "BTC" in reason

        locked_eth, _ = manager.is_symbol_locked("ETH")
        assert locked_eth is False


class TestEventDispatch:
    """事件分发"""

    def test_trade_open_dispatched_to_all(self, tmp_dir):
        """开仓事件分发到所有插件"""
        config = [
            {"name": "position_timeout", "max_position_hours": 48},
            {"name": "consecutive_loss", "max_consecutive_losses": 5},
        ]
        manager = ProtectionManager(protections_config=config, data_dir=tmp_dir)
        manager.on_trade_open("BTC", 50000, 0.1, True, 5)

        # position_timeout 应记录持仓
        timeout_plugin = manager.plugins[0]
        assert "BTC" in timeout_plugin._position_records

    def test_trade_close_dispatched_to_all(self, tmp_dir):
        """平仓事件分发到所有插件"""
        config = [
            {"name": "position_timeout", "max_position_hours": 48},
            {"name": "consecutive_loss", "max_consecutive_losses": 5},
        ]
        manager = ProtectionManager(protections_config=config, data_dir=tmp_dir)
        manager.on_trade_open("BTC", 50000, 0.1, True, 5)
        manager.on_trade_close("BTC", -100)

        # position_timeout 应移除持仓
        timeout_plugin = manager.plugins[0]
        assert "BTC" not in timeout_plugin._position_records

        # consecutive_loss 应记录亏损
        loss_plugin = manager.plugins[1]
        assert loss_plugin._global_losses == 1
