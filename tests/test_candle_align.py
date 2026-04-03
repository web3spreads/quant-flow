"""
K 线节拍对齐工具测试
测试 timeframe_to_seconds 和 next_candle_close_ts 的核心逻辑。
"""

import pytest

from src.utils.candle_align import next_candle_close_ts, timeframe_to_seconds


class TestTimeframeToSeconds:
    """timeframe 字符串解析"""

    def test_minutes(self):
        """分钟级别"""
        assert timeframe_to_seconds("1m") == 60
        assert timeframe_to_seconds("5m") == 300
        assert timeframe_to_seconds("15m") == 900
        assert timeframe_to_seconds("30m") == 1800

    def test_hours(self):
        """小时级别"""
        assert timeframe_to_seconds("1h") == 3600
        assert timeframe_to_seconds("4h") == 14400

    def test_days(self):
        """日级别"""
        assert timeframe_to_seconds("1d") == 86400

    def test_case_insensitive(self):
        """大小写不敏感"""
        assert timeframe_to_seconds("1H") == 3600
        assert timeframe_to_seconds("15M") == 900

    def test_invalid_format(self):
        """非法格式抛出 ValueError"""
        with pytest.raises(ValueError):
            timeframe_to_seconds("abc")
        with pytest.raises(ValueError):
            timeframe_to_seconds("1w")
        with pytest.raises(ValueError):
            timeframe_to_seconds("")


class TestNextCandleCloseTs:
    """下一根 K 线收盘时间计算"""

    def test_15m_alignment(self):
        """15 分钟 K 线对齐到 900 的整数倍"""
        # 假设当前 now_ts = 1000（距离下一个 900 倍数是 1800）
        result = next_candle_close_ts("15m", now_ts=1000.0)
        assert result == 1800.0
        assert result % 900 == 0

    def test_1h_alignment(self):
        """1 小时 K 线对齐到 3600 的整数倍"""
        # now_ts = 3601 -> 下一个边界 7200
        result = next_candle_close_ts("1h", now_ts=3601.0)
        assert result == 7200.0
        assert result % 3600 == 0

    def test_4h_alignment(self):
        """4 小时 K 线对齐到 14400 的整数倍"""
        result = next_candle_close_ts("4h", now_ts=14400.0)
        # 恰好在边界上，应返回下一个
        assert result == 28800.0

    def test_boundary_returns_next(self):
        """恰好在 K 线收盘时刻，应返回下一根而非当前"""
        # now_ts = 900（恰好是 15m 的边界）
        result = next_candle_close_ts("15m", now_ts=900.0)
        assert result == 1800.0

    def test_result_always_greater_than_now(self):
        """结果始终大于 now_ts"""
        for now in [0.0, 100.0, 899.0, 900.0, 901.0, 10000.0]:
            result = next_candle_close_ts("15m", now_ts=now)
            assert result > now

    def test_default_now(self):
        """不传 now_ts 时使用 time.time()"""
        import time

        before = time.time()
        result = next_candle_close_ts("1m")
        assert result > before
        # 1 分钟 K 线，下一个收盘最多 60 秒后
        assert result <= before + 60
