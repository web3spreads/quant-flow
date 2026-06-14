"""
经验泛化器模块

提供抗过拟合机制：
1. 将具体价格转换为相对变化（百分比/ATR倍数）
2. 检查经验覆盖的市场状态多样性
3. 收集经验失效的反例
4. 交叉验证机制
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class MarketState(StrEnum):
    """市场状态分类"""

    TRENDING_UP = "trending_up"  # 上升趋势
    TRENDING_DOWN = "trending_down"  # 下降趋势
    RANGING = "ranging"  # 横盘震荡
    HIGH_VOLATILITY = "high_volatility"  # 高波动
    LOW_VOLATILITY = "low_volatility"  # 低波动


@dataclass
class DiversityScore:
    """市场状态多样性评分"""

    total_states_covered: int
    state_coverage: dict[str, int]  # 每个状态覆盖的记录数
    diversity_ratio: float  # 0-1，越高表示越多样
    missing_states: list[str]  # 缺失的市场状态
    recommendation: str  # 建议


class MarketStateClassifier:
    """市场状态分类器"""

    @staticmethod
    def classify(market_data: dict[str, Any]) -> MarketState:
        """
        根据市场数据判断当前市场状态

        Args:
            market_data: 包含 rsi, atr, price_change 等指标的市场数据

        Returns:
            MarketState 枚举值
        """
        # 防御 None 值：指标不可用时 key 存在但值为 None
        rsi = market_data.get("rsi") or 50
        atr_percent = market_data.get("atr_percent") or 2.0  # ATR 占价格百分比
        price_change = market_data.get("price_change") or 0
        price_change_1h = market_data.get("price_change_1h") or 0

        # 波动率判断
        if atr_percent > 4.0:
            return MarketState.HIGH_VOLATILITY
        elif atr_percent < 1.0:
            return MarketState.LOW_VOLATILITY

        # 趋势判断
        if rsi > 60 and price_change > 0 and price_change_1h > 0.5:
            return MarketState.TRENDING_UP
        elif rsi < 40 and price_change < 0 and price_change_1h < -0.5:
            return MarketState.TRENDING_DOWN

        # 默认为震荡
        return MarketState.RANGING

    @staticmethod
    def classify_records(records: list[dict[str, Any]]) -> dict[MarketState, list[dict]]:
        """将记录按市场状态分类"""
        classified: dict[MarketState, list[dict]] = {state: [] for state in MarketState}

        for record in records:
            market_data = record.get("market_data", {})
            state = MarketStateClassifier.classify(market_data)
            classified[state].append(record)

        return classified


class LessonGeneralizer:
    """
    经验泛化器

    功能：
    1. 抽象化：将具体价格转换为百分比/ATR倍数
    2. 模式识别：提取可复用的市场模式
    3. 反例收集：记录经验失效的场景
    """

    def __init__(self, min_diversity_ratio: float = 0.5):
        """
        Args:
            min_diversity_ratio: 最低多样性比例，低于此值会触发警告
        """
        self.min_diversity_ratio = min_diversity_ratio

    def generalize_lesson(self, lesson: dict[str, Any]) -> dict[str, Any]:
        """
        泛化单条经验

        将具体数值转换为相对表达，提高可复用性

        Args:
            lesson: 原始经验规则

        Returns:
            泛化后的经验规则
        """
        generalized = lesson.copy()
        context = lesson.get("context_features", {})

        if not context:
            return generalized

        # 泛化上下文特征
        generalized_context = {}

        # 1. RSI 区间化
        if "rsi" in context:
            rsi = context["rsi"]
            if rsi > 70:
                generalized_context["rsi_zone"] = "overbought"
            elif rsi > 60:
                generalized_context["rsi_zone"] = "bullish"
            elif rsi < 30:
                generalized_context["rsi_zone"] = "oversold"
            elif rsi < 40:
                generalized_context["rsi_zone"] = "bearish"
            else:
                generalized_context["rsi_zone"] = "neutral"

        # 2. 价格变化转为强度
        if "price_change" in context:
            change = context["price_change"]
            if abs(change) < 0.5:
                generalized_context["price_momentum"] = "weak"
            elif abs(change) < 2.0:
                generalized_context["price_momentum"] = "moderate"
            else:
                generalized_context["price_momentum"] = "strong"
            generalized_context["price_direction"] = "up" if change > 0 else "down"

        # 3. 波动率等级
        if "atr_percent" in context:
            atr = context["atr_percent"]
            if atr < 1.5:
                generalized_context["volatility_level"] = "low"
            elif atr < 3.0:
                generalized_context["volatility_level"] = "normal"
            else:
                generalized_context["volatility_level"] = "high"

        # 4. 趋势强度
        if "trend_strength" in context:
            strength = context["trend_strength"]
            if strength < 0.3:
                generalized_context["trend_clarity"] = "unclear"
            elif strength < 0.6:
                generalized_context["trend_clarity"] = "moderate"
            else:
                generalized_context["trend_clarity"] = "strong"

        # 保留原始数值用于精确匹配
        generalized_context["_original"] = context
        generalized["generalized_context"] = generalized_context
        generalized["is_generalized"] = True

        return generalized

    def check_market_diversity(
        self, lessons: list[dict[str, Any]], records: list[dict[str, Any]]
    ) -> DiversityScore:
        """
        检查经验覆盖的市场状态多样性

        Args:
            lessons: 经验规则列表
            records: 用于生成经验的决策记录

        Returns:
            DiversityScore 评分结果
        """
        # 分类记录
        classified = MarketStateClassifier.classify_records(records)

        # 统计覆盖
        state_coverage = {
            state.value: len(records_list) for state, records_list in classified.items()
        }

        # 计算覆盖的状态数
        covered_states = sum(1 for count in state_coverage.values() if count > 0)
        total_states = len(MarketState)

        # 计算多样性比例
        diversity_ratio = covered_states / total_states

        # 找出缺失状态
        missing_states = [
            state.value for state, records_list in classified.items() if len(records_list) == 0
        ]

        # 生成建议
        if diversity_ratio >= 0.8:
            recommendation = "excellent"
        elif diversity_ratio >= self.min_diversity_ratio:
            recommendation = "acceptable"
        else:
            recommendation = "insufficient_diversity"

        return DiversityScore(
            total_states_covered=covered_states,
            state_coverage=state_coverage,
            diversity_ratio=diversity_ratio,
            missing_states=missing_states,
            recommendation=recommendation,
        )

    def adjust_confidence_by_diversity(
        self, lesson: dict[str, Any], diversity_score: DiversityScore
    ) -> dict[str, Any]:
        """
        根据多样性评分调整经验置信度

        低多样性环境下学习的经验，置信度应该降低

        Args:
            lesson: 经验规则
            diversity_score: 多样性评分

        Returns:
            调整后的经验规则
        """
        adjusted = lesson.copy()
        original_confidence = lesson.get("confidence", 0.5)

        # 多样性惩罚因子
        if diversity_score.recommendation == "excellent":
            diversity_factor = 1.0
        elif diversity_score.recommendation == "acceptable":
            # 多样性一般，适度惩罚（根据实际覆盖率线性插值）
            diversity_factor = 0.75 + (diversity_score.diversity_ratio * 0.2)
        else:
            # 低多样性，显著降低置信度
            diversity_factor = 0.5 + (diversity_score.diversity_ratio * 0.3)

        adjusted["confidence"] = round(original_confidence * diversity_factor, 3)
        adjusted["diversity_factor"] = diversity_factor
        adjusted["diversity_warning"] = diversity_score.recommendation == "insufficient_diversity"

        if diversity_score.missing_states:
            adjusted["untested_market_states"] = diversity_score.missing_states

        return adjusted


def enhance_lessons_with_generalization(
    lessons: list[dict[str, Any]], records: list[dict[str, Any]], min_diversity_ratio: float = 0.5
) -> list[dict[str, Any]]:
    """
    便捷函数：增强经验列表，添加泛化和多样性调整

    Args:
        lessons: 原始经验列表
        records: 决策记录
        min_diversity_ratio: 最低多样性比例

    Returns:
        增强后的经验列表
    """
    generalizer = LessonGeneralizer(min_diversity_ratio=min_diversity_ratio)

    # 检查多样性
    diversity_score = generalizer.check_market_diversity(lessons, records)

    enhanced_lessons = []
    for lesson in lessons:
        # 泛化
        generalized = generalizer.generalize_lesson(lesson)
        # 根据多样性调整置信度
        adjusted = generalizer.adjust_confidence_by_diversity(generalized, diversity_score)
        enhanced_lessons.append(adjusted)

    return enhanced_lessons
