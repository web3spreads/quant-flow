"""
环境特征相似度计算
支持欧氏距离与余弦相似度，并允许为不同特征设置权重
"""

from typing import Any, Dict, Optional
import math


DEFAULT_WEIGHTS: Dict[str, float] = {
    "rsi": 1.2,
    "macd_signal": 1.0,
    "ema_trend": 1.0,
    "trend_direction": 1.0,
    "volatility_level": 0.8,
    "volume_ratio": 0.6,
    "price_position": 0.7,
    "time_of_day": 0.3,
}


class SimilarityScorer:
    """按特征权重计算相似度（0-1）"""

    def __init__(
        self,
        weights: Optional[Dict[str, float]] = None,
        method: str = "cosine",
    ):
        self.weights = {**DEFAULT_WEIGHTS, **(weights or {})}
        self.method = method

    def compute(
        self,
        current: Dict[str, Any],
        target: Dict[str, Any],
        method: Optional[str] = None,
    ) -> float:
        """计算两个特征向量的相似度（0-1）"""
        m = (method or self.method or "cosine").lower()
        features = self._align_features(current, target)
        if not features:
            return 0.0

        if m == "euclidean":
            return self._euclidean_similarity(features)
        if m == "hybrid":
            # 混合：余弦与欧氏的平均
            return (self._cosine_similarity(features) + self._euclidean_similarity(features)) / 2
        return self._cosine_similarity(features)

    def _align_features(
        self, current: Dict[str, Any], target: Dict[str, Any]
    ) -> Dict[str, Dict[str, float]]:
        features: Dict[str, Dict[str, float]] = {}
        for key in set(current.keys()).intersection(target.keys()):
            cur_val = self._normalize_feature(key, current.get(key))
            tgt_val = self._normalize_feature(key, target.get(key))
            if cur_val is None or tgt_val is None:
                continue
            features[key] = {"current": cur_val, "target": tgt_val}
        return features

    def _normalize_feature(self, key: str, value: Any) -> Optional[float]:
        if value is None:
            return None

        categorical_maps: Dict[str, Dict[str, float]] = {
            "macd_signal": {"bullish": 1.0, "bearish": -1.0, "neutral": 0.0},
            "trend_direction": {"up": 1.0, "down": -1.0, "sideways": 0.0},
            "volatility_level": {"low": 0.0, "medium": 0.5, "high": 1.0},
            "time_of_day": {
                "morning": 0.25,
                "afternoon": 0.5,
                "evening": 0.75,
                "night": 0.1,
            },
            "ema_trend": {"bullish": 1.0, "bearish": -1.0, "mixed": 0.0},
        }

        if isinstance(value, str):
            value = value.lower()
            if key in categorical_maps and value in categorical_maps[key]:
                return categorical_maps[key][value]
            try:
                value = float(value)
            except ValueError:
                return None

        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None

        if key == "rsi":
            return max(0.0, min(numeric, 100.0)) / 100.0
        if key == "volume_ratio":
            return max(0.0, min(numeric, 5.0)) / 5.0
        if key == "price_position":
            return max(0.0, min(numeric, 1.0))
        # 默认将数值平滑压缩到 [-1, 1]，避免大尺度特征主导相似度
        return numeric / (1 + abs(numeric))

    def _cosine_similarity(self, features: Dict[str, Dict[str, float]]) -> float:
        dot = 0.0
        norm_a = 0.0
        norm_b = 0.0
        for key, pair in features.items():
            weight = self.weights.get(key, 1.0)
            dot += weight * pair["current"] * pair["target"]
            norm_a += weight * pair["current"] ** 2
            norm_b += weight * pair["target"] ** 2

        if norm_a <= 0 or norm_b <= 0:
            return 0.0

        cosine = dot / (math.sqrt(norm_a) * math.sqrt(norm_b))
        # 将 [-1,1] 映射到 [0,1]
        return max(0.0, min((cosine + 1) / 2, 1.0))

    def _euclidean_similarity(self, features: Dict[str, Dict[str, float]]) -> float:
        distance_sq = 0.0
        for key, pair in features.items():
            weight = self.weights.get(key, 1.0)
            distance_sq += weight * (pair["current"] - pair["target"]) ** 2
        distance = math.sqrt(distance_sq)
        # 距离越大，相似度越低；1/(1+d) 映射到 0-1
        return 1 / (1 + distance)
