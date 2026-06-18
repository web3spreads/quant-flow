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


class TestCrossDayPause:
    """跨天暂停冷却"""

    def test_pause_persists_across_midnight_within_cooldown(self, plugin):
        """23:30 触发暂停(冷却1h)，00:00 跨天但冷却未到期，应保持暂停而非提前恢复"""
        t1 = datetime(2026, 4, 3, 23, 30)
        plugin.check(make_ctx(10000, timestamp=t1))
        r = plugin.check(make_ctx(9400, timestamp=t1))  # 6% 亏损触发
        assert r.should_pause is True

        t2 = datetime(2026, 4, 4, 0, 0)  # 跨天，距触发仅 30 分钟 < 1h
        r2 = plugin.check(make_ctx(9400, timestamp=t2))
        assert r2.should_pause is True  # 未因跨天提前解除暂停

    def test_pause_clears_after_cooldown_across_day(self, plugin):
        """跨天且冷却已过(1.5h>1h)，应恢复交易"""
        t1 = datetime(2026, 4, 3, 23, 30)
        plugin.check(make_ctx(10000, timestamp=t1))
        plugin.check(make_ctx(9400, timestamp=t1))  # 触发

        t2 = datetime(2026, 4, 4, 1, 0)  # 距触发 1.5h > 1h 冷却
        r = plugin.check(make_ctx(9900, timestamp=t2))
        assert r.should_pause is False


class TestInvalidEquityGuard:
    """净值非法守卫"""

    def test_skips_on_nonpositive_equity(self, plugin):
        """equity<=0(行情/接口抖动)时跳过，不触发误报、不污染基准"""
        plugin.check(make_ctx(10000))
        assert plugin.check(make_ctx(0)).triggered is False
        assert plugin.check(make_ctx(-500)).triggered is False
        # 基准未被坏值污染
        assert plugin._daily_start_equity == 10000


class TestStatePersistence:
    """状态持久化"""

    def test_state_survives_restart(self, tmp_dir):
        """重启后日亏损状态恢复"""
        p1 = DailyLossProtection(config={"max_daily_loss_pct": 0.05}, data_dir=tmp_dir)
        p1.check(make_ctx(10000))
        assert p1._daily_start_equity == 10000

        p2 = DailyLossProtection(config={"max_daily_loss_pct": 0.05}, data_dir=tmp_dir)
        assert p2._daily_start_equity == 10000
