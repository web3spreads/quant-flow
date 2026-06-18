"""
最大回撤保护插件测试
"""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.plugins.protections.base import ProtectionAction, ProtectionContext
from src.plugins.protections.drawdown import MaxDrawdownProtection


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def plugin(tmp_dir):
    return MaxDrawdownProtection(
        config={"max_drawdown_pct": 0.10, "pause_hours": 1.0},
        data_dir=tmp_dir,
    )


def make_ctx(equity, balance=None, **kwargs):
    """创建测试上下文"""
    return ProtectionContext(
        balance=balance or equity,
        equity=equity,
        unrealized_pnl=kwargs.get("unrealized_pnl", 0),
        margin_used=kwargs.get("margin_used", 0),
        current_positions=kwargs.get("positions", []),
        timestamp=kwargs.get("timestamp", datetime.now()),
    )


class TestInvalidEquityGuard:
    """净值非法守卫"""

    def test_skips_on_nonpositive_equity(self, plugin):
        """equity<=0 时跳过：不触发 CLOSE_ALL 误平、不污染峰值"""
        plugin.check(make_ctx(10000))  # 建立峰值
        # 行情/接口抖动给出 0 / 负净值，不应算出 ~100% 回撤而误触发全平
        assert plugin.check(make_ctx(0)).triggered is False
        assert plugin.check(make_ctx(-1000)).triggered is False
        assert plugin._peak_equity == 10000  # 峰值未被坏值污染


class TestPeakEquityTracking:
    """峰值净值追踪"""

    def test_peak_updates_on_higher_equity(self, plugin):
        """净值上升时更新峰值"""
        plugin.check(make_ctx(10000))
        plugin.check(make_ctx(11000))
        assert plugin._peak_equity == 11000

    def test_peak_not_updated_on_lower_equity(self, plugin):
        """净值下降时不更新峰值"""
        plugin.check(make_ctx(10000))
        plugin.check(make_ctx(9500))
        assert plugin._peak_equity == 10000


class TestDrawdownDetection:
    """回撤检测"""

    def test_no_trigger_below_threshold(self, plugin):
        """回撤未达阈值时不触发"""
        plugin.check(make_ctx(10000))
        result = plugin.check(make_ctx(9500))  # 5% 回撤
        assert result.triggered is False

    def test_triggers_at_threshold(self, plugin):
        """回撤达到 10% 时触发"""
        plugin.check(make_ctx(10000))
        result = plugin.check(make_ctx(9000))  # 10% 回撤
        assert result.triggered is True
        assert result.action == ProtectionAction.CLOSE_ALL_POSITIONS
        assert result.should_pause is True

    def test_triggers_above_threshold(self, plugin):
        """回撤超过 10% 时触发"""
        plugin.check(make_ctx(10000))
        result = plugin.check(make_ctx(8500))  # 15% 回撤
        assert result.triggered is True
        assert result.details["drawdown_pct"] >= 0.10


class TestPausePeriod:
    """暂停期管理"""

    def test_remains_paused_during_pause_period(self, plugin):
        """暂停期内持续返回暂停"""
        plugin.check(make_ctx(10000))
        plugin.check(make_ctx(8500))  # 触发

        # 暂停期内，即使净值恢复也仍然暂停
        result = plugin.check(make_ctx(10000))
        assert result.triggered is True
        assert result.should_pause is True

    def test_resumes_after_pause_period(self, plugin):
        """暂停期过后恢复"""
        now = datetime.now()
        plugin.check(make_ctx(10000, timestamp=now))
        plugin.check(make_ctx(8500, timestamp=now))  # 触发

        # 2 小时后（超过 1 小时暂停期）
        later = now + timedelta(hours=2)
        result = plugin.check(make_ctx(10000, timestamp=later))
        assert result.triggered is False


class TestStatePersistence:
    """状态持久化"""

    def test_state_survives_restart(self, tmp_dir):
        """重启后峰值恢复"""
        p1 = MaxDrawdownProtection(config={"max_drawdown_pct": 0.10}, data_dir=tmp_dir)
        p1.check(make_ctx(10000))
        p1.check(make_ctx(11000))

        p2 = MaxDrawdownProtection(config={"max_drawdown_pct": 0.10}, data_dir=tmp_dir)
        assert p2._peak_equity == 11000


class TestCloudEvent:
    """云端事件上报"""

    @patch("src.utils.cloud_logger.get_cloud_logger")
    def test_sends_risk_event_on_trigger(self, mock_get_cloud, tmp_dir):
        """触发时上报风控事件"""
        mock_cloud = MagicMock()
        mock_get_cloud.return_value = mock_cloud

        plugin = MaxDrawdownProtection(config={"max_drawdown_pct": 0.10}, data_dir=tmp_dir)
        plugin.check(make_ctx(10000))
        plugin.check(make_ctx(8500))

        mock_cloud.send_risk_event.assert_called_once()
