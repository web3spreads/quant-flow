"""
环境特征提取模块
从市场数据和决策记录中提取用于相似度计算的特征
"""

import math
from datetime import datetime
from typing import Any


class ContextExtractor:
    """将原始市场数据转换为轻量的环境特征向量"""

    def __init__(self, volatility_window: int = 10):
        # 使用最近的决策窗口估计波动率，避免短期噪声
        self.volatility_window = volatility_window

    def extract(
        self,
        market_data: dict[str, Any],
        decision_records: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """提取当前环境特征"""
        md = market_data or {}
        price = float(md.get("current_price") or md.get("close") or 0.0)

        features = {
            "rsi": float(md.get("rsi", 50.0)),
            "macd_signal": self._macd_state(md),
            "trend_direction": self._trend_direction(md),
            "volatility_level": self._volatility_level(md, price, decision_records),
            "volume_ratio": self._volume_ratio(md),
            "price_position": self._price_position(md),
            "time_of_day": self._time_of_day(md.get("timestamp")),
            "ema_trend": self._ema_trend(md),
        }
        return features

    def _macd_state(self, market_data: dict[str, Any]) -> str:
        hist = float(market_data.get("macd_hist", 0) or 0)
        if hist > 0:
            return "bullish"
        if hist < 0:
            return "bearish"
        return "neutral"

    def _trend_direction(self, market_data: dict[str, Any]) -> str:
        ma_short = market_data.get("ma_7")
        ma_mid = market_data.get("ma_25")
        ma_long = market_data.get("ma_99")
        price = market_data.get("current_price")

        if ma_short and ma_mid:
            if ma_short > ma_mid * 1.005:
                return "up"
            if ma_short < ma_mid * 0.995:
                return "down"
        if price and ma_long:
            if price > ma_long * 1.01:
                return "up"
            if price < ma_long * 0.99:
                return "down"
        return "sideways"

    def _ema_trend(self, market_data: dict[str, Any]) -> str:
        ema_fast = market_data.get("ema_20") or market_data.get("ma_7")
        ema_slow = market_data.get("ema_50") or market_data.get("ma_25")

        if ema_fast is None or ema_slow is None:
            return "mixed"

        if ema_fast > ema_slow * 1.003:
            return "bullish"
        if ema_fast < ema_slow * 0.997:
            return "bearish"
        return "mixed"

    def _volatility_level(
        self,
        market_data: dict[str, Any],
        price: float,
        decision_records: list[dict[str, Any]] | None,
    ) -> str:
        if price <= 0:
            price = 1.0

        atr = market_data.get("atr_14") or market_data.get("atr14")
        if atr:
            ratio = abs(float(atr)) / price
        elif market_data.get("bb_upper") and market_data.get("bb_lower"):
            bb_range = float(market_data["bb_upper"]) - float(market_data["bb_lower"])
            ratio = bb_range / price if price else 0.0
        else:
            ratio = self._volatility_from_history(decision_records, price)

        if ratio < 0.005:
            return "low"
        if ratio < 0.02:
            return "medium"
        return "high"

    def _volatility_from_history(
        self, decision_records: list[dict[str, Any]] | None, price: float
    ) -> float:
        if not decision_records:
            return 0.01

        recent = decision_records[-self.volatility_window :]
        prices = [
            float(item.get("market_data", {}).get("current_price") or 0.0)
            for item in recent
            if item.get("market_data")
        ]
        if not prices:
            return 0.01

        mean_price = sum(prices) / len(prices)
        if mean_price == 0:
            return 0.01

        variance = sum((p - mean_price) ** 2 for p in prices) / len(prices)
        std_dev = math.sqrt(variance)
        return std_dev / mean_price

    def _volume_ratio(self, market_data: dict[str, Any]) -> float:
        volume = float(market_data.get("volume") or market_data.get("current_volume") or 0)
        volume_ma = float(market_data.get("volume_ma_20") or market_data.get("avg_volume") or 0)
        if volume_ma <= 0:
            return 1.0 if volume > 0 else 0.0
        ratio = volume / volume_ma
        # 防止极端值影响相似度
        return max(0.0, min(ratio, 5.0))

    def _price_position(self, market_data: dict[str, Any]) -> float:
        bb_pos = market_data.get("bb_position")
        if bb_pos is not None:
            try:
                return max(0.0, min(float(bb_pos), 1.0))
            except (TypeError, ValueError):
                pass

        price = float(market_data.get("current_price") or 0.0)
        high = float(market_data.get("high") or 0.0)
        low = float(market_data.get("low") or 0.0)
        if high > low and price > 0:
            return max(0.0, min((price - low) / (high - low), 1.0))

        # 默认中性位置
        return 0.5

    def _time_of_day(self, timestamp_value: Any) -> str:
        try:
            if isinstance(timestamp_value, datetime):
                hour = timestamp_value.hour
            elif isinstance(timestamp_value, str):
                parsed = datetime.fromisoformat(timestamp_value)
                hour = parsed.hour
            else:
                hour = datetime.utcnow().hour
        except Exception:
            hour = datetime.utcnow().hour

        if 6 <= hour < 12:
            return "morning"
        if 12 <= hour < 17:
            return "afternoon"
        if 17 <= hour < 22:
            return "evening"
        return "night"
