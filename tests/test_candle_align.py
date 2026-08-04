"""K 线节拍对齐测试。"""

import pytest

from src.utils.candle_align import next_candle_close_ts, timeframe_to_seconds


class TestTimeframeToSeconds:
    def test_common_timeframes(self):
        assert timeframe_to_seconds("1m") == 60
        assert timeframe_to_seconds("15m") == 900
        assert timeframe_to_seconds("1h") == 3600
        assert timeframe_to_seconds("4h") == 14400
        assert timeframe_to_seconds("1d") == 86400

    def test_case_and_whitespace(self):
        assert timeframe_to_seconds(" 1H ") == 3600

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            timeframe_to_seconds("abc")
        with pytest.raises(ValueError):
            timeframe_to_seconds("1w")


class TestNextCandleClose:
    def test_alignment(self):
        # 12:34:56 UTC 的下一根 1h K 线收盘应是 13:00:00
        now = 12 * 3600 + 34 * 60 + 56
        assert next_candle_close_ts("1h", now_ts=now) == 13 * 3600

    def test_exact_boundary_moves_to_next(self):
        # 恰好在边界上时返回下一个边界（当前 K 线刚开始）
        assert next_candle_close_ts("1h", now_ts=3600) == 7200

    def test_15m_alignment(self):
        assert next_candle_close_ts("15m", now_ts=901) == 1800
