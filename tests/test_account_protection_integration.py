"""
保护系统集成测试
测试 ProtectionManager 接入 main.py 主循环后的各项功能。
注意：本测试不启动真实交易循环，而是单独测试各集成点的逻辑。
"""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.plugins.protections import (
    ProtectionAction,
    ProtectionContext,
    ProtectionManager,
)

# ──────────────────────────────── Fixtures ────────────────────────────────


@pytest.fixture
def tmp_dir():
    """创建临时数据目录"""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def full_config():
    """完整的保护配置列表"""
    return [
        {"name": "max_drawdown", "max_drawdown_pct": 0.10, "pause_hours": 1.0},
        {"name": "daily_loss", "max_daily_loss_pct": 0.05, "pause_hours": 1.0},
        {"name": "consecutive_loss", "max_consecutive_losses": 3, "per_symbol": True,
         "pause_hours": 1.0},
        {"name": "position_timeout", "max_position_hours": 2.0},
    ]


@pytest.fixture
def manager(tmp_dir, full_config):
    """创建 ProtectionManager 实例"""
    return ProtectionManager(
        protections_config=full_config,
        data_dir=tmp_dir,
    )


@pytest.fixture
def manager_with_callback(tmp_dir, full_config):
    """创建带回调的 ProtectionManager 实例"""
    callback = MagicMock()
    m = ProtectionManager(
        protections_config=full_config,
        data_dir=tmp_dir,
        on_protection_triggered=callback,
    )
    return m, callback


def make_ctx(equity, balance=None, positions=None):
    return ProtectionContext(
        balance=balance or equity,
        equity=equity,
        unrealized_pnl=0,
        margin_used=0,
        current_positions=positions or [],
    )


# ──────────────── 测试1: disabled 时无副作用 ────────────────


class TestProtectionDisabled:
    """protections 为空列表时，manager 不加载任何插件"""

    def test_empty_config_no_plugins(self, tmp_dir):
        """空配置 = 无风控"""
        manager = ProtectionManager(protections_config=[], data_dir=tmp_dir)
        assert len(manager.plugins) == 0

    def test_none_manager_skips_all_checks(self):
        """模拟 main.py 中 protection_manager 为 None 的逻辑"""
        protection_manager = None
        can_open_new_positions = True

        if protection_manager:
            can_open_new_positions = False

        assert can_open_new_positions is True


# ──────────────── 测试2: 最大回撤触发全部平仓 ────────────────


class TestDrawdownProtection:
    """最大回撤超阈值 → CLOSE_ALL_POSITIONS"""

    def test_drawdown_triggers_close_all(self, manager):
        """净值回撤超过 10% 时触发全部平仓"""
        manager.check_all(make_ctx(10000))
        results = manager.check_all(make_ctx(8500))

        action = ProtectionManager.get_most_severe_action(results)
        assert action == ProtectionAction.CLOSE_ALL_POSITIONS

    def test_close_all_calls_close_position(self, manager):
        """回撤触发时模拟 main.py 中的平仓逻辑"""
        manager.check_all(make_ctx(10000))
        results = manager.check_all(make_ctx(8500))
        action = ProtectionManager.get_most_severe_action(results)

        mock_order_manager = MagicMock()
        current_positions = [
            {"symbol": "BTC", "size": 0.1},
            {"symbol": "ETH", "size": 1.0},
        ]

        if action == ProtectionAction.CLOSE_ALL_POSITIONS:
            for pos in current_positions:
                sym = pos.get("symbol", "")
                if sym:
                    mock_order_manager.close_position(sym)

        assert mock_order_manager.close_position.call_count == 2


# ──────────────── 测试3: 单日亏损暂停新开仓 ────────────────


class TestDailyLossProtection:
    """单日亏损超阈值 → PAUSE_NEW_TRADES"""

    def test_daily_loss_pauses_new_trades(self, manager):
        """当日亏损超 5% 时暂停新开仓"""
        manager.check_all(make_ctx(10000))
        results = manager.check_all(make_ctx(9400))

        paused = any(r.should_pause for r in results)
        assert paused is True


# ──────────────── 测试4: 持仓超时自动平仓 ────────────────


class TestPositionTimeout:
    """持仓超时 → affected_symbols 包含超时持仓"""

    def test_timeout_detected(self, manager):
        """超时持仓被正确检测"""
        manager.on_trade_open("BTC", 50000, 0.1, True, 5)

        # 找到 position_timeout 插件并修改开仓时间
        for plugin in manager.plugins:
            if plugin.name == "position_timeout":
                plugin._position_records["BTC"]["entry_time"] = (
                    datetime.now() - timedelta(hours=3)
                ).isoformat()

        results = manager.check_all(make_ctx(10000))
        timeout_results = [r for r in results if r.affected_symbols and "BTC" in r.affected_symbols]
        assert len(timeout_results) > 0

    def test_non_timeout_position_not_detected(self, manager):
        """未超时持仓不应被检测到"""
        manager.on_trade_open("ETH", 3000, 1.0, True, 3)
        results = manager.check_all(make_ctx(10000))
        timeout_results = [r for r in results if r.affected_symbols and "ETH" in r.affected_symbols]
        assert len(timeout_results) == 0


# ──────────────── 测试5: 连续亏损暂停交易 ────────────────


class TestConsecutiveLossProtection:
    """连续亏损 → per-symbol 锁定"""

    def test_consecutive_losses_lock_symbol(self, manager):
        """连续 3 次亏损后锁定该交易对"""
        for _ in range(3):
            manager.on_trade_close("BTC", -100)

        locked, reason = manager.is_symbol_locked("BTC")
        assert locked is True
        assert "BTC" in reason

    def test_profit_resets_consecutive_losses(self, manager):
        """盈利后重置连续亏损计数"""
        manager.on_trade_close("BTC", -100)
        manager.on_trade_close("BTC", -100)
        manager.on_trade_close("BTC", 200)  # 盈利

        locked, _ = manager.is_symbol_locked("BTC")
        assert locked is False


# ──────────────── 测试6: 回调通知 ────────────────


class TestNotificationIntegration:
    """保护触发时回调函数应被调用"""

    def test_callback_called_on_protection(self, manager_with_callback):
        """保护触发时回调函数被调用"""
        manager, callback = manager_with_callback

        manager.check_all(make_ctx(10000))
        manager.check_all(make_ctx(8500))  # 触发回撤保护

        callback.assert_called()
        reason = callback.call_args[0][0]
        assert isinstance(reason, str)
        assert len(reason) > 0


# ──────────────── 测试7: CloudLogger 上报 ────────────────


class TestCloudLoggerIntegration:
    """保护触发时 CloudLogger 应上报风控事件"""

    @patch("src.utils.cloud_logger.get_cloud_logger")
    def test_cloud_risk_event_sent(self, mock_get_cloud, tmp_dir, full_config):
        """保护触发时 send_risk_event 被调用"""
        mock_cloud = MagicMock()
        mock_get_cloud.return_value = mock_cloud

        manager = ProtectionManager(
            protections_config=full_config,
            data_dir=tmp_dir,
        )
        manager.check_all(make_ctx(10000))
        manager.check_all(make_ctx(8500))

        mock_cloud.send_risk_event.assert_called()


# ──────────────── 测试8: 事件分发 ────────────────


class TestEventDispatch:
    """on_trade_open/close 正确分发到所有插件"""

    def test_trade_open_recorded(self, manager):
        """开仓事件分发到 position_timeout"""
        manager.on_trade_open("BTC", 50000, 0.1, True, 5)

        for plugin in manager.plugins:
            if plugin.name == "position_timeout":
                assert "BTC" in plugin._position_records

    def test_trade_close_recorded(self, manager):
        """平仓事件分发到 consecutive_loss 和 position_timeout"""
        manager.on_trade_open("BTC", 50000, 0.1, True, 5)
        manager.on_trade_close("BTC", -100)

        for plugin in manager.plugins:
            if plugin.name == "position_timeout":
                assert "BTC" not in plugin._position_records
            if plugin.name == "consecutive_loss":
                assert plugin._global_losses == 1

    def test_state_persistence(self, tmp_dir, full_config):
        """重启后插件状态恢复"""
        m1 = ProtectionManager(protections_config=full_config, data_dir=tmp_dir)
        m1.on_trade_close("BTC", -100)
        m1.on_trade_close("ETH", -200)
        m1.on_trade_open("SOL", 150, 10, True, 3)

        m2 = ProtectionManager(protections_config=full_config, data_dir=tmp_dir)
        for plugin in m2.plugins:
            if plugin.name == "consecutive_loss":
                assert plugin._global_losses == 2
            if plugin.name == "position_timeout":
                assert "SOL" in plugin._position_records
