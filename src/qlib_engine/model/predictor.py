"""
信号预测器

基于训练好的模型对最新市场数据生成交易信号。
将模型的原始预测分数转换为标准化的交易信号。
"""

import logging
from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd

logger = logging.getLogger("QuantFlow.QLib")


class SignalDirection(Enum):
    """交易信号方向"""
    STRONG_LONG = "强烈做多"
    LONG = "做多"
    WEAK_LONG = "弱做多"
    NEUTRAL = "中性"
    WEAK_SHORT = "弱做空"
    SHORT = "做空"
    STRONG_SHORT = "强烈做空"


@dataclass
class TradingSignal:
    """
    交易信号数据结构

    包含模型预测的原始分数和经过处理的交易信号。
    """
    symbol: str
    raw_score: float         # 模型原始预测分数
    normalized_score: float  # 标准化后的分数 [-1, 1]
    direction: SignalDirection  # 信号方向
    strength: float          # 信号强度 [0, 1]
    confidence: float        # 置信度 [0, 1]
    percentile: float        # 历史分位数 [0, 1]
    model_type: str          # 使用的模型类型
    feature_count: int       # 使用的特征数量

    @property
    def is_actionable(self) -> bool:
        """信号是否可执行（非中性且强度足够）"""
        return self.direction != SignalDirection.NEUTRAL and self.strength >= 0.3

    @property
    def is_long(self) -> bool:
        """是否为做多信号"""
        return self.direction in (
            SignalDirection.STRONG_LONG,
            SignalDirection.LONG,
            SignalDirection.WEAK_LONG,
        )

    @property
    def is_short(self) -> bool:
        """是否为做空信号"""
        return self.direction in (
            SignalDirection.STRONG_SHORT,
            SignalDirection.SHORT,
            SignalDirection.WEAK_SHORT,
        )

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "symbol": self.symbol,
            "raw_score": self.raw_score,
            "normalized_score": self.normalized_score,
            "direction": self.direction.value,
            "strength": self.strength,
            "confidence": self.confidence,
            "percentile": self.percentile,
            "model_type": self.model_type,
            "is_actionable": self.is_actionable,
        }


class SignalPredictor:
    """
    信号预测器

    将模型的原始预测分数转换为可操作的交易信号。

    处理流程：
    1. 模型预测 → 原始分数
    2. 分数标准化 → [-1, 1] 范围
    3. 方向判定 → 做多/做空/中性
    4. 强度计算 → 信号强弱
    5. 置信度估计 → 基于历史分布
    """

    def __init__(
        self,
        signal_threshold: float = 0.3,
        strong_threshold: float = 0.7,
        history_window: int = 500,
    ):
        """
        初始化信号预测器

        Args:
            signal_threshold: 信号阈值（低于此值视为中性）
            strong_threshold: 强信号阈值
            history_window: 历史分数窗口（用于分位数计算）
        """
        self.signal_threshold = signal_threshold
        self.strong_threshold = strong_threshold
        self.history_window = history_window
        self._score_history: dict[str, list[float]] = {}  # 每个交易对的历史分数

    def predict(
        self,
        model,
        features: pd.DataFrame,
        symbol: str,
        model_type: str = "unknown",
    ) -> TradingSignal:
        """
        生成交易信号

        Args:
            model: 训练好的模型
            features: 最新的特征 DataFrame（一行或多行）
            symbol: 交易对
            model_type: 模型类型

        Returns:
            交易信号
        """
        # 清理输入
        features_clean = features.replace([np.inf, -np.inf], np.nan).fillna(0)

        # 模型预测
        if hasattr(model, "predict"):
            raw_scores = model.predict(features_clean)
        else:
            raise ValueError("模型没有 predict 方法")

        # 取最新一个预测值
        raw_score = float(raw_scores[-1]) if len(raw_scores) > 0 else 0.0

        # 更新历史记录
        if symbol not in self._score_history:
            self._score_history[symbol] = []
        self._score_history[symbol].append(raw_score)
        # 限制历史窗口
        if len(self._score_history[symbol]) > self.history_window:
            self._score_history[symbol] = self._score_history[symbol][-self.history_window:]

        # 标准化分数
        normalized_score = self._normalize_score(raw_score, symbol)

        # 判定方向
        direction = self._determine_direction(normalized_score)

        # 计算强度
        strength = abs(normalized_score)

        # 计算置信度
        confidence = self._estimate_confidence(raw_score, symbol)

        # 计算历史分位数
        percentile = self._calculate_percentile(raw_score, symbol)

        signal = TradingSignal(
            symbol=symbol,
            raw_score=raw_score,
            normalized_score=normalized_score,
            direction=direction,
            strength=strength,
            confidence=confidence,
            percentile=percentile,
            model_type=model_type,
            feature_count=len(features.columns),
        )

        logger.info(
            f"[{symbol}] 信号生成: {direction.value}, "
            f"强度={strength:.3f}, 置信度={confidence:.3f}, "
            f"原始分数={raw_score:.6f}"
        )

        return signal

    def _normalize_score(self, score: float, symbol: str) -> float:
        """
        标准化分数到 [-1, 1] 范围

        使用历史分数的均值和标准差进行 Z-Score 标准化，
        然后通过 tanh 压缩到 [-1, 1]。

        Args:
            score: 原始分数
            symbol: 交易对

        Returns:
            标准化后的分数
        """
        history = self._score_history.get(symbol, [])

        if len(history) < 5:
            # 历史数据不足，使用简单的 tanh 标准化
            return float(np.tanh(score * 100))

        mean = np.mean(history)
        std = np.std(history)
        if std < 1e-12:
            return 0.0

        z_score = (score - mean) / std
        return float(np.tanh(z_score))

    def _determine_direction(self, normalized_score: float) -> SignalDirection:
        """
        根据标准化分数判定交易方向

        Args:
            normalized_score: 标准化分数 [-1, 1]

        Returns:
            信号方向
        """
        abs_score = abs(normalized_score)

        if abs_score < self.signal_threshold:
            return SignalDirection.NEUTRAL

        if normalized_score > 0:
            if abs_score >= self.strong_threshold:
                return SignalDirection.STRONG_LONG
            elif abs_score >= (self.signal_threshold + self.strong_threshold) / 2:
                return SignalDirection.LONG
            else:
                return SignalDirection.WEAK_LONG
        else:
            if abs_score >= self.strong_threshold:
                return SignalDirection.STRONG_SHORT
            elif abs_score >= (self.signal_threshold + self.strong_threshold) / 2:
                return SignalDirection.SHORT
            else:
                return SignalDirection.WEAK_SHORT

    def _estimate_confidence(self, score: float, symbol: str) -> float:
        """
        估计预测的置信度

        基于历史预测分数的分布来估计当前预测的可靠性。
        分数越偏离历史均值且历史样本越多，置信度越高。

        Args:
            score: 原始分数
            symbol: 交易对

        Returns:
            置信度 [0, 1]
        """
        history = self._score_history.get(symbol, [])

        if len(history) < 10:
            return 0.3  # 历史数据不足，低置信度

        # 基于样本量的基础置信度
        sample_confidence = min(len(history) / 100, 1.0) * 0.5

        # 基于信号一致性的置信度（最近 N 个预测方向是否一致）
        recent = history[-10:]
        direction_consistency = abs(sum(1 if s > 0 else -1 for s in recent)) / len(recent)

        confidence = sample_confidence + direction_consistency * 0.5
        return min(confidence, 1.0)

    def _calculate_percentile(self, score: float, symbol: str) -> float:
        """
        计算当前分数在历史中的分位数

        Args:
            score: 原始分数
            symbol: 交易对

        Returns:
            分位数 [0, 1]
        """
        history = self._score_history.get(symbol, [])

        if len(history) < 5:
            return 0.5

        return float(np.mean([1 if s <= score else 0 for s in history]))
