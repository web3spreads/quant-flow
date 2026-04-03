"""
单日亏损保护插件测试
"""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.plugins.protections.base import ProtectionAction, ProtectionContext
from src.plugins.protections.daily_loss import DailyLossProtection


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def plugin(tmp_dir):
    return DailyLossProtection(
        config={"max_daily_loss_pct": 0.05, "pause_hours": 1.0},
        data_dir=tmp_dir,
    )


def make_ctx(equity, timestamp=None):
    return ProtectionContext(
        balance=equity,
        equity=equity,
        unrealized_pnl=0,
        margin_used=0,
        current_positions=[],
        timestamp=timestamp or datetime.now(),
    )


class TestDailyLossDetection:
    """日亏损检测"""

    def test_no_trigger_below_threshold(self, plugin):
        """日亏损未达阈值时不触发"""
        plugin.check(make_ctx(10000))
        result = plugin.check(make_ctx(9700))  # 3% 亏损
        assert result.triggered is False

    def test_triggers_at_threshold(self, plugin):
        """日亏损达 5% 时触发"""
        plugin.check(make_ctx(10000))
        result = plugin.check(make_ctx(9500))  # 5% 亏损
        assert result.triggered is True
        assert result.action == ProtectionAction.PAUSE_NEW_TRADES
        assert result.should_pause is True


class TestDailyReset:
    """日期重置"""

    def test_resets_on_new_day(self, plugin):
        """新的一天重置日亏损基准"""
        today = datetime(2026, 4, 3, 10, 0)
        plugin.check(make_ctx(10000, timestamp=today))
        # 当日亏损
        plugin.check(make_ctx(9400, timestamp=today))  # 触发

        # 第二天，重置
        tomorrow = datetime(2026, 4, 4, 10, 0)
        result = plugin.check(make_ctx(9400, timestamp=tomorrow))
        # 新一天基准为 9400，没有新亏损，不触发
        assert result.triggered is False


class TestPausePeriod:
    """暂停期管理"""

    def test_resumes_after_pause(self, plugin):
        """暂停期过后且净值恢复时不再触发"""
        now = datetime.now()
        plugin.check(make_ctx(10000, timestamp=now))
        plugin.check(make_ctx(9400, timestamp=now))  # 触发（日亏损 6%）

        # 2 小时后，净值恢复到基准附近（日亏损 < 5%）
        later = now + timedelta(hours=2)
        result = plugin.check(make_ctx(9600, timestamp=later))
        # 暂停期已过且日亏损回到 4%（< 5%），不再暂停
        assert result.should_pause is False


class TestStatePersistence:
    """状态持久化"""

    def test_state_survives_restart(self, tmp_dir):
        """重启后日亏损状态恢复"""
        p1 = DailyLossProtection(config={"max_daily_loss_pct": 0.05}, data_dir=tmp_dir)
        p1.check(make_ctx(10000))
        assert p1._daily_start_equity == 10000

        p2 = DailyLossProtection(config={"max_daily_loss_pct": 0.05}, data_dir=tmp_dir)
        assert p2._daily_start_equity == 10000
