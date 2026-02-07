"""
交易信号综合评分系统
整合多维度分析生成可靠的交易信号

核心功能：
1. 多因子信号评分
2. 信号确认机制
3. 入场时机优化
4. 信号过滤和验证
5. 历史信号表现追踪
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class SignalType(Enum):
    """信号类型"""

    LONG_ENTRY = "long_entry"  # 做多入场
    LONG_EXIT = "long_exit"  # 做多出场
    SHORT_ENTRY = "short_entry"  # 做空入场
    SHORT_EXIT = "short_exit"  # 做空出场
    NO_SIGNAL = "no_signal"  # 无信号


class SignalQuality(Enum):
    """信号质量"""

    EXCELLENT = "excellent"  # 极佳 (80-100)
    GOOD = "good"  # 良好 (60-79)
    FAIR = "fair"  # 一般 (40-59)
    POOR = "poor"  # 较差 (20-39)
    INVALID = "invalid"  # 无效 (0-19)


@dataclass
class SignalFactor:
    """单个信号因子"""

    name: str
    value: float  # 因子原始值
    score: float  # 标准化得分 (-1 到 1)
    weight: float  # 权重
    contribution: float  # 对总分的贡献
    description: str  # 因子描述


@dataclass
class SignalConfirmation:
    """信号确认信息"""

    confirmed: bool
    confirmation_count: int  # 确认因子数量
    required_confirmations: int  # 需要的确认数量
    confirmations: list[str]  # 确认因子列表
    rejections: list[str]  # 拒绝因子列表
    confidence_boost: float  # 确认带来的置信度提升


@dataclass
class EntryTiming:
    """入场时机分析"""

    is_optimal: bool
    timing_score: float  # 0-1
    price_position: str  # "at_support", "at_resistance", "middle"
    pullback_quality: str  # "good_pullback", "extended", "no_pullback"
    suggested_action: str  # "enter_now", "wait_pullback", "wait_breakout"
    wait_for_price: float | None  # 建议等待的价格
    reasoning: str


@dataclass
class SignalValidation:
    """信号验证结果"""

    is_valid: bool
    validation_score: float  # 0-100
    passed_checks: list[str]
    failed_checks: list[str]
    warnings: list[str]
    risk_adjusted_score: float


@dataclass
class TradingSignal:
    """完整的交易信号"""

    signal_id: str
    timestamp: str
    symbol: str
    signal_type: SignalType
    quality: SignalQuality

    # 评分信息
    raw_score: float  # 原始得分 (-100 到 100)
    normalized_score: float  # 标准化得分 (0 到 100)
    confidence: float  # 置信度 (0 到 1)

    # 因子分析
    factors: list[SignalFactor]
    dominant_factor: str  # 主导因子

    # 确认和验证
    confirmation: SignalConfirmation
    validation: SignalValidation
    timing: EntryTiming

    # 交易建议
    suggested_action: str
    entry_price: float
    stop_loss: float
    take_profit: float
    position_size_pct: float
    risk_reward_ratio: float

    # 附加信息
    reasoning: list[str]
    warnings: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)


class SignalScorer:
    """信号评分器"""

    # 默认因子权重
    DEFAULT_WEIGHTS = {
        "trend": 0.25,  # 趋势
        "momentum": 0.20,  # 动量
        "volume": 0.15,  # 成交量
        "volatility": 0.10,  # 波动性
        "price_action": 0.15,  # 价格行为
        "multi_timeframe": 0.15,  # 多周期一致性
    }

    def __init__(
        self,
        weights: dict[str, float] | None = None,
        min_confirmation_count: int = 3,
        quality_thresholds: dict[str, float] | None = None,
    ):
        """
        初始化信号评分器

        Args:
            weights: 因子权重字典
            min_confirmation_count: 最小确认因子数量
            quality_thresholds: 质量阈值
        """
        self.weights = weights or self.DEFAULT_WEIGHTS
        self.min_confirmations = min_confirmation_count
        self.quality_thresholds = quality_thresholds or {
            "excellent": 80,
            "good": 60,
            "fair": 40,
            "poor": 20,
        }

        self._signal_counter = 0
        self._signal_history: list[TradingSignal] = []

    def score_signal(
        self,
        symbol: str,
        market_data: dict[str, Any],
        trend_analysis: Any,
        momentum_analysis: Any,
        volume_analysis: Any,
        volatility_analysis: Any,
        support_resistance: Any,
        multi_timeframe_trends: dict[str, str] | None = None,
        current_position: dict[str, Any] | None = None,
    ) -> TradingSignal:
        """
        计算综合交易信号

        Args:
            symbol: 交易对符号
            market_data: 市场数据
            trend_analysis: 趋势分析结果
            momentum_analysis: 动量分析结果
            volume_analysis: 成交量分析结果
            volatility_analysis: 波动性分析结果
            support_resistance: 支撑阻力分析
            multi_timeframe_trends: 多周期趋势
            current_position: 当前持仓

        Returns:
            TradingSignal: 完整的交易信号
        """
        current_price = market_data.get("current_price", 0)

        # 计算各因子得分
        factors = []

        # 1. 趋势因子
        trend_score, trend_desc = self._score_trend(trend_analysis)
        factors.append(
            SignalFactor(
                name="trend",
                value=trend_analysis.strength if hasattr(trend_analysis, "strength") else 0,
                score=trend_score,
                weight=self.weights["trend"],
                contribution=trend_score * self.weights["trend"],
                description=trend_desc,
            )
        )

        # 2. 动量因子
        momentum_score, momentum_desc = self._score_momentum(momentum_analysis)
        factors.append(
            SignalFactor(
                name="momentum",
                value=momentum_analysis.rsi_value
                if hasattr(momentum_analysis, "rsi_value")
                else 50,
                score=momentum_score,
                weight=self.weights["momentum"],
                contribution=momentum_score * self.weights["momentum"],
                description=momentum_desc,
            )
        )

        # 3. 成交量因子
        volume_score, volume_desc = self._score_volume(volume_analysis, trend_analysis)
        factors.append(
            SignalFactor(
                name="volume",
                value=volume_analysis.volume_ratio
                if hasattr(volume_analysis, "volume_ratio")
                else 1,
                score=volume_score,
                weight=self.weights["volume"],
                contribution=volume_score * self.weights["volume"],
                description=volume_desc,
            )
        )

        # 4. 波动性因子
        vol_score, vol_desc = self._score_volatility(volatility_analysis)
        factors.append(
            SignalFactor(
                name="volatility",
                value=volatility_analysis.atr_percentile
                if hasattr(volatility_analysis, "atr_percentile")
                else 50,
                score=vol_score,
                weight=self.weights["volatility"],
                contribution=vol_score * self.weights["volatility"],
                description=vol_desc,
            )
        )

        # 5. 价格行为因子
        pa_score, pa_desc = self._score_price_action(
            current_price, support_resistance, trend_analysis
        )
        factors.append(
            SignalFactor(
                name="price_action",
                value=current_price,
                score=pa_score,
                weight=self.weights["price_action"],
                contribution=pa_score * self.weights["price_action"],
                description=pa_desc,
            )
        )

        # 6. 多周期一致性因子
        mtf_score, mtf_desc = self._score_multi_timeframe(multi_timeframe_trends)
        factors.append(
            SignalFactor(
                name="multi_timeframe",
                value=mtf_score,
                score=mtf_score,
                weight=self.weights["multi_timeframe"],
                contribution=mtf_score * self.weights["multi_timeframe"],
                description=mtf_desc,
            )
        )

        # 计算原始得分 (-100 到 100)
        raw_score = sum(f.contribution for f in factors) * 100

        # 确定信号类型和方向
        signal_type, direction = self._determine_signal_type(
            raw_score, current_position, trend_analysis
        )

        # 标准化得分 (0 到 100)
        normalized_score = (raw_score + 100) / 2

        # 信号确认
        confirmation = self._check_confirmations(factors, direction)

        # 应用确认提升
        if confirmation.confirmed:
            normalized_score = min(100, normalized_score + confirmation.confidence_boost * 10)

        # 确定信号质量
        quality = self._determine_quality(normalized_score)

        # 信号验证
        validation = self._validate_signal(
            signal_type, normalized_score, factors, volatility_analysis
        )

        # 入场时机分析
        timing = self._analyze_entry_timing(
            current_price, support_resistance, trend_analysis, direction
        )

        # 计算置信度
        confidence = self._calculate_confidence(normalized_score, confirmation, validation, timing)

        # 计算止损止盈
        stop_loss, take_profit = self._calculate_sl_tp(
            current_price, direction, volatility_analysis, support_resistance
        )

        # 计算风险回报比
        if direction == "long":
            potential_profit = take_profit - current_price
            potential_loss = current_price - stop_loss
        else:
            potential_profit = current_price - take_profit
            potential_loss = stop_loss - current_price

        risk_reward = potential_profit / potential_loss if potential_loss > 0 else 1.0

        # 计算建议仓位比例
        position_size_pct = self._calculate_position_size(
            confidence, volatility_analysis, risk_reward
        )

        # 生成推理说明
        reasoning = self._generate_reasoning(factors, confirmation, timing)

        # 生成警告
        warnings = self._generate_warnings(validation, timing, volatility_analysis)

        # 确定建议操作
        suggested_action = self._determine_action(signal_type, validation, timing, confidence)

        # 找出主导因子
        dominant_factor = max(factors, key=lambda f: abs(f.contribution)).name

        # 生成信号ID
        self._signal_counter += 1
        signal_id = f"{symbol}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{self._signal_counter}"

        signal = TradingSignal(
            signal_id=signal_id,
            timestamp=datetime.now().isoformat(),
            symbol=symbol,
            signal_type=signal_type,
            quality=quality,
            raw_score=raw_score,
            normalized_score=normalized_score,
            confidence=confidence,
            factors=factors,
            dominant_factor=dominant_factor,
            confirmation=confirmation,
            validation=validation,
            timing=timing,
            suggested_action=suggested_action,
            entry_price=current_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            position_size_pct=position_size_pct,
            risk_reward_ratio=risk_reward,
            reasoning=reasoning,
            warnings=warnings,
        )

        # 保存到历史
        self._signal_history.append(signal)
        if len(self._signal_history) > 1000:
            self._signal_history = self._signal_history[-500:]

        return signal

    def _score_trend(self, trend) -> tuple[float, str]:
        """评分趋势因子"""
        if not trend:
            return 0.0, "无趋势数据"

        direction = getattr(trend, "direction", "neutral")
        strength = getattr(trend, "strength", 0)
        ma_alignment = getattr(trend, "ma_alignment", "mixed")

        score = 0.0
        descriptions = []

        if direction == "bullish":
            score = strength
            descriptions.append("看涨趋势")
        elif direction == "bearish":
            score = -strength
            descriptions.append("看跌趋势")
        else:
            descriptions.append("趋势中性")

        if ma_alignment == "aligned_up":
            score += 0.2
            descriptions.append("均线多头排列")
        elif ma_alignment == "aligned_down":
            score -= 0.2
            descriptions.append("均线空头排列")

        return max(-1, min(1, score)), ", ".join(descriptions)

    def _score_momentum(self, momentum) -> tuple[float, str]:
        """评分动量因子"""
        if not momentum:
            return 0.0, "无动量数据"

        rsi = getattr(momentum, "rsi_value", 50)
        rsi_state = getattr(momentum, "rsi_state", "neutral")
        macd_state = getattr(momentum, "macd_state", "neutral")
        macd_crossover = getattr(momentum, "macd_crossover", None)
        rsi_divergence = getattr(momentum, "rsi_divergence", None)

        score = 0.0
        descriptions = []

        # RSI评分
        if rsi_state == "oversold":
            score += 0.3
            descriptions.append(f"RSI超卖({rsi:.1f})")
        elif rsi_state == "overbought":
            score -= 0.3
            descriptions.append(f"RSI超买({rsi:.1f})")
        else:
            # 中性区域根据位置给分
            if rsi > 50:
                score += (rsi - 50) / 100
            else:
                score -= (50 - rsi) / 100

        # MACD评分
        if macd_state == "bullish":
            score += 0.3
            descriptions.append("MACD多头")
        elif macd_state == "bearish":
            score -= 0.3
            descriptions.append("MACD空头")

        # MACD交叉
        if macd_crossover == "golden_cross":
            score += 0.3
            descriptions.append("MACD金叉")
        elif macd_crossover == "death_cross":
            score -= 0.3
            descriptions.append("MACD死叉")

        # RSI背离
        if rsi_divergence == "bullish":
            score += 0.2
            descriptions.append("RSI看涨背离")
        elif rsi_divergence == "bearish":
            score -= 0.2
            descriptions.append("RSI看跌背离")

        return max(-1, min(1, score)), ", ".join(descriptions) if descriptions else "动量中性"

    def _score_volume(self, volume, trend) -> tuple[float, str]:
        """评分成交量因子"""
        if not volume:
            return 0.0, "无成交量数据"

        volume_ratio = getattr(volume, "volume_ratio", 1.0)
        volume_trend = getattr(volume, "volume_trend", "stable")
        volume_confirmation = getattr(volume, "volume_confirmation", False)
        unusual_volume = getattr(volume, "unusual_volume", False)

        trend_direction = getattr(trend, "direction", "neutral") if trend else "neutral"

        score = 0.0
        descriptions = []

        # 量价配合
        if volume_confirmation:
            if trend_direction == "bullish":
                score += 0.4
                descriptions.append("放量上涨")
            elif trend_direction == "bearish":
                score -= 0.4
                descriptions.append("放量下跌")

        # 成交量趋势
        if volume_trend == "increasing":
            if trend_direction == "bullish":
                score += 0.2
            elif trend_direction == "bearish":
                score -= 0.2
            descriptions.append("成交量增加")
        elif volume_trend == "decreasing":
            descriptions.append("成交量减少")

        # 异常成交量
        if unusual_volume:
            descriptions.append("异常成交量")
            # 异常成交量可能是信号，但方向需要结合趋势
            if trend_direction == "bullish":
                score += 0.1
            elif trend_direction == "bearish":
                score -= 0.1

        # 量比评分
        if volume_ratio > 2.0:
            descriptions.append(f"量比{volume_ratio:.1f}")

        return max(-1, min(1, score)), ", ".join(descriptions) if descriptions else "成交量正常"

    def _score_volatility(self, volatility) -> tuple[float, str]:
        """评分波动性因子"""
        if not volatility:
            return 0.0, "无波动性数据"

        vol_state = getattr(volatility, "volatility_state", "normal")

        descriptions = []

        # 波动性不直接影响方向，但影响信号质量
        if vol_state == "low":
            score = 0.3  # 低波动有利于趋势交易
            descriptions.append("低波动性环境")
        elif vol_state == "normal":
            score = 0.0
            descriptions.append("正常波动性")
        elif vol_state == "high":
            score = -0.2  # 高波动增加风险
            descriptions.append("高波动性")
        else:  # extreme
            score = -0.5
            descriptions.append("极端波动性")

        return score, ", ".join(descriptions)

    def _score_price_action(self, price, sr, trend) -> tuple[float, str]:
        """评分价格行为因子"""
        if not sr:
            return 0.0, "无支撑阻力数据"

        price_to_support = getattr(sr, "price_to_support_pct", 5)
        price_to_resistance = getattr(sr, "price_to_resistance_pct", 5)

        trend_direction = getattr(trend, "direction", "neutral") if trend else "neutral"

        score = 0.0
        descriptions = []

        # 价格位置评分
        if price_to_support < 1:  # 接近支撑位
            if trend_direction != "bearish":
                score += 0.3
                descriptions.append("接近支撑位（潜在买入点）")
            else:
                score -= 0.1
                descriptions.append("接近支撑位但趋势向下")
        elif price_to_resistance < 1:  # 接近阻力位
            if trend_direction != "bullish":
                score -= 0.3
                descriptions.append("接近阻力位（潜在卖出点）")
            else:
                score += 0.1
                descriptions.append("接近阻力位可能突破")

        # 空间比
        upside_space = price_to_resistance
        downside_risk = price_to_support

        if upside_space > downside_risk * 2:
            score += 0.2
            descriptions.append("上涨空间大于下跌风险")
        elif downside_risk > upside_space * 2:
            score -= 0.2
            descriptions.append("下跌风险大于上涨空间")

        return max(-1, min(1, score)), ", ".join(descriptions) if descriptions else "价格行为中性"

    def _score_multi_timeframe(self, mtf_trends) -> tuple[float, str]:
        """评分多周期一致性因子"""
        if not mtf_trends:
            return 0.0, "无多周期数据"

        bullish_keywords = ["上涨", "强势", "bullish", "up"]
        bearish_keywords = ["下跌", "弱势", "bearish", "down"]

        bullish_count = 0
        bearish_count = 0
        total = 0

        for _tf, trend in mtf_trends.items():
            if any(kw in trend.lower() for kw in bullish_keywords):
                bullish_count += 1
            elif any(kw in trend.lower() for kw in bearish_keywords):
                bearish_count += 1
            total += 1

        if total == 0:
            return 0.0, "无有效周期数据"

        # 计算一致性得分
        if bullish_count > bearish_count:
            consistency = bullish_count / total
            score = consistency
            desc = f"多周期看涨一致性 {consistency:.0%}"
        elif bearish_count > bullish_count:
            consistency = bearish_count / total
            score = -consistency
            desc = f"多周期看跌一致性 {consistency:.0%}"
        else:
            score = 0.0
            desc = "多周期分歧"

        return score, desc

    def _determine_signal_type(
        self, raw_score: float, current_position: dict | None, trend
    ) -> tuple[SignalType, str]:
        """确定信号类型"""
        # 阈值
        entry_threshold = 30  # 入场阈值
        exit_threshold = 20  # 出场阈值

        has_long = False
        has_short = False

        if current_position:
            size = float(current_position.get("szi", 0))
            if size > 0:
                has_long = True
            elif size < 0:
                has_short = True

        if raw_score > entry_threshold:
            if has_short:
                return SignalType.SHORT_EXIT, "long"
            else:
                return SignalType.LONG_ENTRY, "long"
        elif raw_score < -entry_threshold:
            if has_long:
                return SignalType.LONG_EXIT, "short"
            else:
                return SignalType.SHORT_ENTRY, "short"
        else:
            # 检查是否应该退出现有仓位
            if has_long and raw_score < -exit_threshold:
                return SignalType.LONG_EXIT, "short"
            elif has_short and raw_score > exit_threshold:
                return SignalType.SHORT_EXIT, "long"

            return SignalType.NO_SIGNAL, "neutral"

    def _check_confirmations(
        self, factors: list[SignalFactor], direction: str
    ) -> SignalConfirmation:
        """检查信号确认"""
        confirmations = []
        rejections = []

        for factor in factors:
            if direction == "long":
                if factor.score > 0.2:
                    confirmations.append(f"{factor.name}: {factor.description}")
                elif factor.score < -0.2:
                    rejections.append(f"{factor.name}: {factor.description}")
            elif direction == "short":
                if factor.score < -0.2:
                    confirmations.append(f"{factor.name}: {factor.description}")
                elif factor.score > 0.2:
                    rejections.append(f"{factor.name}: {factor.description}")

        confirmed = len(confirmations) >= self.min_confirmations
        confidence_boost = len(confirmations) * 0.05  # 每个确认增加5%置信度

        return SignalConfirmation(
            confirmed=confirmed,
            confirmation_count=len(confirmations),
            required_confirmations=self.min_confirmations,
            confirmations=confirmations,
            rejections=rejections,
            confidence_boost=min(0.3, confidence_boost),  # 最多增加30%
        )

    def _determine_quality(self, normalized_score: float) -> SignalQuality:
        """确定信号质量"""
        if normalized_score >= self.quality_thresholds["excellent"]:
            return SignalQuality.EXCELLENT
        elif normalized_score >= self.quality_thresholds["good"]:
            return SignalQuality.GOOD
        elif normalized_score >= self.quality_thresholds["fair"]:
            return SignalQuality.FAIR
        elif normalized_score >= self.quality_thresholds["poor"]:
            return SignalQuality.POOR
        else:
            return SignalQuality.INVALID

    def _validate_signal(
        self, signal_type: SignalType, score: float, factors: list[SignalFactor], volatility
    ) -> SignalValidation:
        """验证信号"""
        passed = []
        failed = []
        warnings = []

        # 检查1: 分数阈值
        if score >= 40:
            passed.append("分数超过最低阈值")
        else:
            failed.append(f"分数{score:.1f}低于最低阈值40")

        # 检查2: 至少有一个强因子
        has_strong_factor = any(abs(f.score) > 0.5 for f in factors)
        if has_strong_factor:
            passed.append("存在强信号因子")
        else:
            warnings.append("缺少强信号因子")

        # 检查3: 没有严重矛盾因子
        conflicting = sum(1 for f in factors if f.score * factors[0].score < -0.3)
        if conflicting <= 1:
            passed.append("因子一致性良好")
        else:
            failed.append(f"存在{conflicting}个矛盾因子")

        # 检查4: 波动性检查
        vol_state = getattr(volatility, "volatility_state", "normal") if volatility else "normal"
        if vol_state == "extreme":
            warnings.append("极端波动性环境")
        elif vol_state == "high":
            warnings.append("高波动性环境")
        else:
            passed.append("波动性环境适合交易")

        is_valid = len(failed) == 0 and signal_type != SignalType.NO_SIGNAL
        validation_score = 100 - len(failed) * 25 - len(warnings) * 10
        risk_adjusted = validation_score * (0.8 if vol_state in ["high", "extreme"] else 1.0)

        return SignalValidation(
            is_valid=is_valid,
            validation_score=max(0, validation_score),
            passed_checks=passed,
            failed_checks=failed,
            warnings=warnings,
            risk_adjusted_score=max(0, risk_adjusted),
        )

    def _analyze_entry_timing(self, price: float, sr, trend, direction: str) -> EntryTiming:
        """分析入场时机"""
        if not sr:
            return EntryTiming(
                is_optimal=False,
                timing_score=0.5,
                price_position="unknown",
                pullback_quality="unknown",
                suggested_action="enter_now",
                wait_for_price=None,
                reasoning="缺少支撑阻力数据",
            )

        support = getattr(sr, "nearest_support", price * 0.95)
        resistance = getattr(sr, "nearest_resistance", price * 1.05)
        price_to_support = getattr(sr, "price_to_support_pct", 5)
        price_to_resistance = getattr(sr, "price_to_resistance_pct", 5)

        timing_score = 0.5
        reasoning_parts = []

        if direction == "long":
            # 做多入场时机
            if price_to_support < 2:
                timing_score = 0.9
                price_position = "at_support"
                pullback_quality = "good_pullback"
                suggested_action = "enter_now"
                wait_for_price = None
                reasoning_parts.append("价格接近支撑位，是良好的做多入场点")
            elif price_to_resistance < 2:
                timing_score = 0.3
                price_position = "at_resistance"
                pullback_quality = "extended"
                suggested_action = "wait_pullback"
                wait_for_price = support * 1.005
                reasoning_parts.append("价格接近阻力位，建议等待回调")
            else:
                timing_score = 0.6
                price_position = "middle"
                pullback_quality = "no_pullback"
                suggested_action = "enter_now"
                wait_for_price = None
                reasoning_parts.append("价格位于中间区域")

        elif direction == "short":
            # 做空入场时机
            if price_to_resistance < 2:
                timing_score = 0.9
                price_position = "at_resistance"
                pullback_quality = "good_pullback"
                suggested_action = "enter_now"
                wait_for_price = None
                reasoning_parts.append("价格接近阻力位，是良好的做空入场点")
            elif price_to_support < 2:
                timing_score = 0.3
                price_position = "at_support"
                pullback_quality = "extended"
                suggested_action = "wait_pullback"
                wait_for_price = resistance * 0.995
                reasoning_parts.append("价格接近支撑位，建议等待反弹")
            else:
                timing_score = 0.6
                price_position = "middle"
                pullback_quality = "no_pullback"
                suggested_action = "enter_now"
                wait_for_price = None
                reasoning_parts.append("价格位于中间区域")
        else:
            price_position = "middle"
            pullback_quality = "no_pullback"
            suggested_action = "wait"
            wait_for_price = None

        is_optimal = timing_score >= 0.7

        return EntryTiming(
            is_optimal=is_optimal,
            timing_score=timing_score,
            price_position=price_position,
            pullback_quality=pullback_quality,
            suggested_action=suggested_action,
            wait_for_price=wait_for_price,
            reasoning=", ".join(reasoning_parts) if reasoning_parts else "时机分析完成",
        )

    def _calculate_confidence(
        self,
        score: float,
        confirmation: SignalConfirmation,
        validation: SignalValidation,
        timing: EntryTiming,
    ) -> float:
        """计算置信度"""
        # 基础置信度基于分数
        base_confidence = score / 100

        # 确认加成
        if confirmation.confirmed:
            base_confidence += confirmation.confidence_boost

        # 验证调整
        base_confidence *= validation.risk_adjusted_score / 100

        # 时机调整
        base_confidence *= 0.5 + timing.timing_score * 0.5

        return max(0, min(1, base_confidence))

    def _calculate_sl_tp(self, price: float, direction: str, volatility, sr) -> tuple[float, float]:
        """计算止损止盈"""
        atr = getattr(volatility, "current_atr", price * 0.02) if volatility else price * 0.02
        sl_mult = getattr(volatility, "suggested_sl_multiplier", 1.5) if volatility else 1.5
        tp_mult = getattr(volatility, "suggested_tp_multiplier", 3.0) if volatility else 3.0

        if direction == "long":
            stop_loss = price - atr * sl_mult
            take_profit = price + atr * tp_mult
        else:
            stop_loss = price + atr * sl_mult
            take_profit = price - atr * tp_mult

        return stop_loss, take_profit

    def _calculate_position_size(self, confidence: float, volatility, risk_reward: float) -> float:
        """计算建议仓位比例"""
        base_size = 0.1  # 基础10%

        # 置信度调整
        confidence_factor = confidence

        # 波动性调整
        vol_state = getattr(volatility, "volatility_state", "normal") if volatility else "normal"
        vol_factors = {"low": 1.2, "normal": 1.0, "high": 0.7, "extreme": 0.5}
        vol_factor = vol_factors.get(vol_state, 1.0)

        # 风险回报比调整
        rr_factor = min(1.5, risk_reward / 2)

        return min(0.2, base_size * confidence_factor * vol_factor * rr_factor)

    def _generate_reasoning(
        self, factors: list[SignalFactor], confirmation: SignalConfirmation, timing: EntryTiming
    ) -> list[str]:
        """生成推理说明"""
        reasoning = []

        # 主要因子说明
        for f in sorted(factors, key=lambda x: abs(x.contribution), reverse=True)[:3]:
            if abs(f.score) > 0.1:
                reasoning.append(f"{f.name}: {f.description}")

        # 确认说明
        if confirmation.confirmed:
            reasoning.append(f"信号获得{confirmation.confirmation_count}个因子确认")

        # 时机说明
        reasoning.append(f"入场时机: {timing.reasoning}")

        return reasoning

    def _generate_warnings(
        self, validation: SignalValidation, timing: EntryTiming, volatility
    ) -> list[str]:
        """生成警告"""
        warnings = list(validation.warnings)

        if not timing.is_optimal:
            warnings.append(f"入场时机不理想 (评分: {timing.timing_score:.0%})")

        vol_state = getattr(volatility, "volatility_state", "normal") if volatility else "normal"
        if vol_state == "extreme":
            warnings.append("当前市场波动极大，建议减小仓位或观望")

        return warnings

    def _determine_action(
        self,
        signal_type: SignalType,
        validation: SignalValidation,
        timing: EntryTiming,
        confidence: float,
    ) -> str:
        """确定建议操作"""
        if not validation.is_valid:
            return "hold"

        if confidence < 0.3:
            return "hold"

        if signal_type == SignalType.LONG_ENTRY:
            if timing.suggested_action == "wait_pullback":
                return "wait_pullback_then_buy"
            return "buy"
        elif signal_type == SignalType.SHORT_ENTRY:
            if timing.suggested_action == "wait_pullback":
                return "wait_pullback_then_sell_short"
            return "sell_short"
        elif signal_type == SignalType.LONG_EXIT:
            return "sell"
        elif signal_type == SignalType.SHORT_EXIT:
            return "buy_to_cover"

        return "hold"

    def get_signal_history(self, symbol: str | None = None, limit: int = 10) -> list[TradingSignal]:
        """获取信号历史"""
        history = self._signal_history
        if symbol:
            history = [s for s in history if s.symbol == symbol]
        return history[-limit:]


def format_signal_for_prompt(signal: TradingSignal) -> str:
    """
    将交易信号格式化为可注入Prompt的文本

    Args:
        signal: TradingSignal 交易信号

    Returns:
        格式化的文本
    """
    lines = []
    lines.append("## 🎯 交易信号评分")
    lines.append(f"**信号类型**: {signal.signal_type.value}")
    lines.append(f"**信号质量**: {signal.quality.value}")
    lines.append(f"**综合得分**: {signal.normalized_score:.1f}/100")
    lines.append(f"**置信度**: {signal.confidence:.0%}")
    lines.append(f"**主导因子**: {signal.dominant_factor}")
    lines.append("")

    # 因子分析
    lines.append("### 因子分析")
    for f in sorted(signal.factors, key=lambda x: abs(x.contribution), reverse=True):
        direction = "📈" if f.score > 0 else "📉" if f.score < 0 else "➖"
        lines.append(
            f"- {direction} {f.name}: {f.description} (权重: {f.weight:.0%}, 贡献: {f.contribution:.2f})"
        )
    lines.append("")

    # 确认信息
    lines.append("### 信号确认")
    lines.append(f"- 确认状态: {'✅ 已确认' if signal.confirmation.confirmed else '❌ 未确认'}")
    lines.append(
        f"- 确认因子: {signal.confirmation.confirmation_count}/{signal.confirmation.required_confirmations}"
    )
    if signal.confirmation.confirmations:
        lines.append("- 确认项:")
        for c in signal.confirmation.confirmations:
            lines.append(f"  - ✅ {c}")
    lines.append("")

    # 入场时机
    lines.append("### 入场时机")
    lines.append(f"- 时机评分: {signal.timing.timing_score:.0%}")
    lines.append(f"- 是否最佳: {'是' if signal.timing.is_optimal else '否'}")
    lines.append(f"- 建议: {signal.timing.suggested_action}")
    if signal.timing.wait_for_price:
        lines.append(f"- 建议等待价格: ${signal.timing.wait_for_price:.2f}")
    lines.append(f"- 分析: {signal.timing.reasoning}")
    lines.append("")

    # 交易建议
    lines.append("### 交易建议")
    lines.append(f"- **建议操作**: {signal.suggested_action}")
    lines.append(f"- 入场价: ${signal.entry_price:.2f}")
    lines.append(f"- 止损价: ${signal.stop_loss:.2f}")
    lines.append(f"- 止盈价: ${signal.take_profit:.2f}")
    lines.append(f"- 风险回报比: {signal.risk_reward_ratio:.2f}")
    lines.append(f"- 建议仓位: {signal.position_size_pct:.1%}")
    lines.append("")

    # 推理
    if signal.reasoning:
        lines.append("### 推理逻辑")
        for r in signal.reasoning:
            lines.append(f"- {r}")
        lines.append("")

    # 警告
    if signal.warnings:
        lines.append("### ⚠️ 警告")
        for w in signal.warnings:
            lines.append(f"- {w}")

    return "\n".join(lines)
