"""技术指标测试：指标计算、强趋势检测与迟滞确认器。"""

from conftest import make_ohlcv

from src.data.indicators import TechnicalIndicators, TrendConfirmTracker, detect_strong_trend


class TestIndicators:
    def test_calculate_all_indicators_adds_columns(self):
        df = TechnicalIndicators.calculate_all_indicators(make_ohlcv())
        for col in ("ma_7", "ma_25", "rsi", "macd", "macd_hist", "bb_upper", "bb_lower"):
            assert col in df.columns, f"缺少指标列 {col}"

    def test_get_latest_indicators_fields(self):
        df = TechnicalIndicators.calculate_all_indicators(make_ohlcv())
        latest = TechnicalIndicators.get_latest_indicators(df)
        for key in ("current_price", "rsi", "macd_hist", "bb_upper", "bb_lower", "volume_change"):
            assert key in latest, f"缺少字段 {key}"
        # 上行序列的 RSI 应偏强
        assert latest["rsi"] > 50

    def test_empty_df_returns_empty(self):
        import pandas as pd

        assert TechnicalIndicators.get_latest_indicators(pd.DataFrame()) == {}


class TestDetectStrongTrend:
    TRENDS_UP = {"15分钟": "强势上涨", "1小时": "强势上涨", "4小时": "强势上涨", "日线": "震荡整理"}

    def test_up_votes_reach_threshold(self):
        assert detect_strong_trend(self.TRENDS_UP, min_votes=3) == 1

    def test_votes_below_threshold(self):
        assert detect_strong_trend(self.TRENDS_UP, min_votes=4) == 0

    def test_down_trend(self):
        trends = {"15分钟": "强势下跌", "1小时": "强势下跌", "4小时": "强势下跌"}
        assert detect_strong_trend(trends, min_votes=3) == -1

    def test_timeframe_whitelist_excludes_noise(self):
        trends = {"1分钟": "强势上涨", "15分钟": "强势上涨", "1小时": "强势上涨"}
        # 白名单排除 1m 后只剩 2 票，不达阈值
        assert detect_strong_trend(trends, min_votes=3, allowed_timeframes=["15m", "1h"]) == 0

    def test_empty_trends(self):
        assert detect_strong_trend(None, min_votes=1) == 0
        assert detect_strong_trend({}, min_votes=1) == 0


class TestTrendConfirmTracker:
    def test_hysteresis_confirmation(self):
        tracker = TrendConfirmTracker(confirm_cycles=2, flatten_min_cycles=3)
        assert tracker.update(1) == (0, False)  # 第 1 周期：未确认
        assert tracker.update(1) == (1, False)  # 第 2 周期：暂停生效
        assert tracker.update(1) == (1, True)  # 第 3 周期：允许平逆势库存

    def test_direction_flip_resets(self):
        tracker = TrendConfirmTracker(confirm_cycles=2, flatten_min_cycles=2)
        tracker.update(1)
        assert tracker.update(-1) == (0, False)  # 方向翻转，计数归零重来
        assert tracker.update(-1) == (-1, True)

    def test_zero_resets(self):
        tracker = TrendConfirmTracker(confirm_cycles=2, flatten_min_cycles=2)
        tracker.update(1)
        tracker.update(0)
        assert tracker.update(1) == (0, False)  # 信号消失后需重新累计

    def test_flatten_never_below_confirm(self):
        tracker = TrendConfirmTracker(confirm_cycles=3, flatten_min_cycles=1)
        # flatten_min_cycles 会被抬到 confirm_cycles
        assert tracker.flatten_min_cycles == 3
