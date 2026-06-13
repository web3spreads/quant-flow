"""
持仓超时保护插件测试
"""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.plugins.protections.base import ProtectionContext
from src.plugins.protections.position_timeout import PositionTimeoutProtection


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def plugin(tmp_dir):
    return PositionTimeoutProtection(
        config={"max_position_hours": 2.0},
        data_dir=tmp_dir,
    )


def make_ctx(timestamp=None):
    return ProtectionContext(
        balance=10000,
        equity=10000,
        unrealized_pnl=0,
        margin_used=0,
        current_positions=[],
        timestamp=timestamp or datetime.now(),
    )


class TestTimeoutDetection:
    """超时检测"""

    def test_no_timeout_for_new_position(self, plugin):
        """刚开的仓不超时"""
        plugin.on_trade_open("BTC", 50000, 0.1, True, 5)
        result = plugin.check(make_ctx())
        assert result.triggered is False

    def test_detects_timeout(self, plugin):
        """超时持仓被检测到"""
        plugin.on_trade_open("BTC", 50000, 0.1, True, 5)
        # 手动修改开仓时间
        plugin._position_records["BTC"]["entry_time"] = (
            datetime.now() - timedelta(hours=3)
        ).isoformat()

        result = plugin.check(make_ctx())
        assert result.triggered is True
        assert "BTC" in result.affected_symbols

    def test_multiple_timeouts(self, plugin):
        """多个超时持仓"""
        plugin.on_trade_open("BTC", 50000, 0.1, True, 5)
        plugin.on_trade_open("ETH", 3000, 1.0, True, 3)

        three_hours_ago = (datetime.now() - timedelta(hours=3)).isoformat()
        plugin._position_records["BTC"]["entry_time"] = three_hours_ago
        plugin._position_records["ETH"]["entry_time"] = three_hours_ago

        result = plugin.check(make_ctx())
        assert result.triggered is True
        assert "BTC" in result.affected_symbols
        assert "ETH" in result.affected_symbols


class TestPositionTracking:
    """持仓记录管理"""

    def test_close_removes_record(self, plugin):
        """平仓后移除记录"""
        plugin.on_trade_open("BTC", 50000, 0.1, True, 5)
        assert "BTC" in plugin._position_records

        plugin.on_trade_close("BTC", 100)
        assert "BTC" not in plugin._position_records

    def test_close_nonexistent_is_safe(self, plugin):
        """平仓不存在的持仓不报错"""
        plugin.on_trade_close("DOGE", 0)  # 不应报错

    def test_on_position_dropped_clears_record(self, plugin):
        """风控强平后 on_position_dropped 清理超时记录"""
        plugin.on_trade_open("BTC", 50000, 0.1, True, 5)
        assert "BTC" in plugin._position_records

        plugin.on_position_dropped("BTC")
        assert "BTC" not in plugin._position_records

    def test_on_position_dropped_nonexistent_is_safe(self, plugin):
        """对不存在的持仓调用 on_position_dropped 不报错"""
        plugin.on_position_dropped("DOGE")  # 不应报错


class TestTimestampHandling:
    """显式时间戳支持（回测确定性）"""

    def test_get_timeout_symbols_uses_passed_timestamp(self, plugin):
        """get_timeout_symbols 使用传入的时间戳而非墙钟"""
        plugin.on_trade_open("BTC", 50000, 0.1, True, 5)
        entry = plugin._position_records["BTC"]["entry_time"]
        entry_dt = datetime.fromisoformat(entry)

        # 传入开仓后 1 小时：未超时（阈值 2h）
        assert plugin.get_timeout_symbols(timestamp=entry_dt + timedelta(hours=1)) == []
        # 传入开仓后 3 小时：已超时
        assert "BTC" in plugin.get_timeout_symbols(timestamp=entry_dt + timedelta(hours=3))

    def test_on_trade_open_records_passed_timestamp(self, plugin):
        """on_trade_open 按传入时间戳记录开仓时间"""
        ts = datetime(2024, 1, 1, 12, 0, 0)
        plugin.on_trade_open("BTC", 50000, 0.1, True, 5, timestamp=ts)
        assert plugin._position_records["BTC"]["entry_time"] == ts.isoformat()


class TestStatePersistence:
    """状态持久化"""

    def test_state_survives_restart(self, tmp_dir):
        """重启后持仓记录恢复"""
        p1 = PositionTimeoutProtection(config={"max_position_hours": 2.0}, data_dir=tmp_dir)
        p1.on_trade_open("BTC", 50000, 0.1, True, 5)

        p2 = PositionTimeoutProtection(config={"max_position_hours": 2.0}, data_dir=tmp_dir)
        assert "BTC" in p2._position_records
        assert p2._position_records["BTC"]["entry_price"] == 50000
