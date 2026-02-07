"""
决策验证器
在执行交易前进行多维度验证，确保交易决策的质量

验证维度：
1. 多周期趋势共振 - 确保不同时间周期趋势一致
2. 信号质量验证 - 确保信号强度达到阈值
3. 风险回报验证 - 确保风险回报比合理
4. 市场环境验证 - 避开不适合交易的市场状态
5. 入场时机验证 - 等待更好的入场点
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import pandas as pd


class ValidationResult(StrEnum):
    """验证结果"""
    PASS = "pass"  # 通过验证
    WARN = "warn"  # 警告但允许
    BLOCK = "block"  # 阻止交易


class TrendDirection(StrEnum):
    """趋势方向"""
    STRONG_UP = "strong_up"
    UP = "up"
    NEUTRAL = "neutral"
    DOWN = "down"
    STRONG_DOWN = "strong_down"
    UNKNOWN = "unknown"


class MarketRegime(StrEnum):
    """市场状态"""
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"
    VOLATILE = "volatile"
    BREAKOUT = "breakout"
    UNKNOWN = "unknown"


@dataclass
class TrendAnalysis:
    """趋势分析结果"""
    direction: TrendDirection
    strength: float  # 0-1
    consistency: float  # 趋势一致性 0-1
    momentum: float  # 动量 -1 到 1


@dataclass
class ValidationCheck:
    """单项验证检查结果"""
    name: str
    result: ValidationResult
    score: float  # 0-1
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class DecisionValidation:
    """决策验证结果"""
    is_valid: bool
    overall_score: float  # 0-1 综合评分
    decision: str  # 原始决策
    validated_decision: str  # 验证后的决策
    checks: list[ValidationCheck] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)

    # 优化后的参数
    suggested_size_multiplier: float = 1.0  # 建议仓位调整系数
    suggested_entry_price: float | None = None  # 建议入场价格
    wait_for_pullback: bool = False  # 是否等待回调

    def get_summary(self) -> str:
        """获取验证摘要"""
        status = "✅ 通过" if self.is_valid else "❌ 阻止"
        parts = [f"{status} (评分: {self.overall_score:.2f})"]

        if self.blockers:
            parts.append(f"阻止原因: {', '.join(self.blockers[:2])}")
        if self.warnings:
            parts.append(f"警告: {', '.join(self.warnings[:2])}")
        if self.suggestions:
            parts.append(f"建议: {', '.join(self.suggestions[:1])}")

        return " | ".join(parts)


class DecisionValidator:
    """
    决策验证器

    执行多维度验证确保交易决策质量
    """

    def __init__(
        self,
        # 趋势共振配置
        require_trend_alignment: bool = True,
        min_aligned_timeframes: int = 2,  # 至少2个周期趋势一致
        trend_alignment_weight: float = 0.25,

        # 信号质量配置
        min_signal_score: float = 0.4,
        signal_quality_weight: float = 0.25,

        # 风险回报配置
        min_risk_reward_ratio: float = 1.5,
        risk_reward_weight: float = 0.20,

        # 市场环境配置
        avoid_high_volatility: bool = True,
        volatility_threshold: float = 2.5,  # ATR 倍数
        market_regime_weight: float = 0.15,

        # 入场时机配置
        prefer_pullback_entry: bool = True,
        max_chase_percent: float = 0.02,  # 追高/追低阈值
        entry_timing_weight: float = 0.15
    ):
        # 趋势共振
        self.require_trend_alignment = require_trend_alignment
        self.min_aligned_timeframes = min_aligned_timeframes
        self.trend_alignment_weight = trend_alignment_weight

        # 信号质量
        self.min_signal_score = min_signal_score
        self.signal_quality_weight = signal_quality_weight

        # 风险回报
        self.min_risk_reward_ratio = min_risk_reward_ratio
        self.risk_reward_weight = risk_reward_weight

        # 市场环境
        self.avoid_high_volatility = avoid_high_volatility
        self.volatility_threshold = volatility_threshold
        self.market_regime_weight = market_regime_weight

        # 入场时机
        self.prefer_pullback_entry = prefer_pullback_entry
        self.max_chase_percent = max_chase_percent
        self.entry_timing_weight = entry_timing_weight

    def validate_decision(
        self,
        decision: str,
        symbol: str,
        current_price: float,
        indicators: dict[str, Any],
        multi_timeframe_trends: dict[str, str],
        take_profit_ratio: float,
        stop_loss_ratio: float,
        leverage: int = 1,
        df: pd.DataFrame | None = None,
        signal_score: float | None = None
    ) -> DecisionValidation:
        """
        验证交易决策

        Args:
            decision: 原始决策 ("BUY", "SELL_SHORT", "DO_NOTHING" 等)
            symbol: 交易对符号
            current_price: 当前价格
            indicators: 技术指标字典
            multi_timeframe_trends: 多周期趋势 {"15分钟": "上涨", "1小时": "下跌", ...}
            take_profit_ratio: 止盈比例
            stop_loss_ratio: 止损比例
            leverage: 杠杆倍数
            df: OHLCV DataFrame (可选)
            signal_score: 信号评分 (可选)

        Returns:
            DecisionValidation 验证结果
        """
        checks = []
        blockers = []
        warnings = []
        suggestions = []

        # 如果不是开仓决策，直接通过
        if decision not in ["BUY", "SELL_SHORT"]:
            return DecisionValidation(
                is_valid=True,
                overall_score=1.0,
                decision=decision,
                validated_decision=decision,
                checks=[],
                blockers=[],
                warnings=[],
                suggestions=[]
            )

        is_long = decision == "BUY"

        # 1. 多周期趋势共振验证
        trend_check = self._check_trend_alignment(
            multi_timeframe_trends, is_long
        )
        checks.append(trend_check)
        if trend_check.result == ValidationResult.BLOCK:
            blockers.append(trend_check.message)
        elif trend_check.result == ValidationResult.WARN:
            warnings.append(trend_check.message)

        # 2. 信号质量验证
        signal_check = self._check_signal_quality(
            indicators, signal_score, is_long
        )
        checks.append(signal_check)
        if signal_check.result == ValidationResult.BLOCK:
            blockers.append(signal_check.message)
        elif signal_check.result == ValidationResult.WARN:
            warnings.append(signal_check.message)

        # 3. 风险回报验证
        rr_check = self._check_risk_reward(
            take_profit_ratio, stop_loss_ratio, leverage
        )
        checks.append(rr_check)
        if rr_check.result == ValidationResult.BLOCK:
            blockers.append(rr_check.message)
        elif rr_check.result == ValidationResult.WARN:
            warnings.append(rr_check.message)

        # 4. 市场环境验证
        market_check = self._check_market_regime(
            indicators, df, is_long
        )
        checks.append(market_check)
        if market_check.result == ValidationResult.BLOCK:
            blockers.append(market_check.message)
        elif market_check.result == ValidationResult.WARN:
            warnings.append(market_check.message)

        # 5. 入场时机验证
        entry_check = self._check_entry_timing(
            current_price, indicators, df, is_long
        )
        checks.append(entry_check)
        if entry_check.result == ValidationResult.BLOCK:
            blockers.append(entry_check.message)
        elif entry_check.result == ValidationResult.WARN:
            warnings.append(entry_check.message)

        # 计算综合评分
        weights = [
            self.trend_alignment_weight,
            self.signal_quality_weight,
            self.risk_reward_weight,
            self.market_regime_weight,
            self.entry_timing_weight
        ]
        scores = [check.score for check in checks]
        overall_score = sum(w * s for w, s in zip(weights, scores))

        # 决定是否通过
        is_valid = len(blockers) == 0 and overall_score >= 0.5
        validated_decision = decision if is_valid else "DO_NOTHING"

        # 生成建议
        if not is_valid:
            suggestions.append("建议等待更好的入场时机")

        # 计算仓位调整系数
        size_multiplier = self._calculate_size_multiplier(checks, overall_score)

        # 计算建议入场价格
        suggested_entry = self._calculate_suggested_entry(
            current_price, indicators, df, is_long
        )

        # 是否等待回调
        wait_for_pullback = entry_check.details.get('should_wait', False)

        return DecisionValidation(
            is_valid=is_valid,
            overall_score=overall_score,
            decision=decision,
            validated_decision=validated_decision,
            checks=checks,
            blockers=blockers,
            warnings=warnings,
            suggestions=suggestions,
            suggested_size_multiplier=size_multiplier,
            suggested_entry_price=suggested_entry,
            wait_for_pullback=wait_for_pullback
        )

    def _check_trend_alignment(
        self,
        multi_timeframe_trends: dict[str, str],
        is_long: bool
    ) -> ValidationCheck:
        """
        检查多周期趋势共振

        要求多个时间周期趋势方向一致
        """
        # 趋势方向映射
        bullish_keywords = ["上涨", "强势上涨", "多头", "看涨", "向上"]
        bearish_keywords = ["下跌", "强势下跌", "空头", "看跌", "向下"]

        aligned_count = 0
        opposite_count = 0
        total_count = 0

        # 时间周期权重 (长周期权重更高)
        timeframe_weights = {
            "1分钟": 0.5,
            "5分钟": 0.7,
            "15分钟": 0.8,
            "1小时": 1.0,
            "4小时": 1.2,
            "日线": 1.5
        }

        weighted_alignment = 0.0
        total_weight = 0.0

        for tf, trend in multi_timeframe_trends.items():
            if not trend or trend in ["无数据", "获取失败", "数据不足"]:
                continue

            total_count += 1
            weight = timeframe_weights.get(tf, 1.0)
            total_weight += weight

            trend_lower = trend.lower()
            is_bullish = any(kw in trend_lower for kw in bullish_keywords)
            is_bearish = any(kw in trend_lower for kw in bearish_keywords)

            if is_long:
                if is_bullish:
                    aligned_count += 1
                    weighted_alignment += weight
                elif is_bearish:
                    opposite_count += 1
                    weighted_alignment -= weight * 0.5
            else:  # 做空
                if is_bearish:
                    aligned_count += 1
                    weighted_alignment += weight
                elif is_bullish:
                    opposite_count += 1
                    weighted_alignment -= weight * 0.5

        # 计算得分
        if total_weight > 0:
            score = max(0, min(1, (weighted_alignment / total_weight + 1) / 2))
        else:
            score = 0.5

        # 判断结果
        direction_text = "做多" if is_long else "做空"

        if aligned_count >= self.min_aligned_timeframes and opposite_count == 0:
            result = ValidationResult.PASS
            message = f"{aligned_count}个周期趋势与{direction_text}方向一致"
        elif aligned_count >= self.min_aligned_timeframes:
            result = ValidationResult.WARN
            message = f"{aligned_count}个周期一致，但{opposite_count}个周期相反"
        elif self.require_trend_alignment:
            result = ValidationResult.BLOCK
            message = f"趋势不一致: 仅{aligned_count}个周期支持{direction_text}"
        else:
            result = ValidationResult.WARN
            message = f"趋势较弱: {aligned_count}个周期支持{direction_text}"

        return ValidationCheck(
            name="trend_alignment",
            result=result,
            score=score,
            message=message,
            details={
                "aligned_count": aligned_count,
                "opposite_count": opposite_count,
                "total_count": total_count,
                "weighted_alignment": weighted_alignment
            }
        )

    def _check_signal_quality(
        self,
        indicators: dict[str, Any],
        signal_score: float | None,
        is_long: bool
    ) -> ValidationCheck:
        """
        检查信号质量

        综合评估各项技术指标的信号强度
        """
        scores = []
        details = {}

        # RSI 信号
        rsi = indicators.get('rsi')
        if rsi is not None and indicators.get('rsi_available', True):
            if is_long:
                # 做多：RSI 在 30-50 较好（超卖区域），70以上危险
                if rsi < 30:
                    rsi_score = 0.9  # 超卖，好的做多机会
                elif rsi < 50:
                    rsi_score = 0.7
                elif rsi < 70:
                    rsi_score = 0.5
                else:
                    rsi_score = 0.2  # 超买，不适合做多
            else:
                # 做空：RSI 在 50-70 较好（超买区域），30以下危险
                if rsi > 70:
                    rsi_score = 0.9  # 超买，好的做空机会
                elif rsi > 50:
                    rsi_score = 0.7
                elif rsi > 30:
                    rsi_score = 0.5
                else:
                    rsi_score = 0.2  # 超卖，不适合做空

            scores.append(('rsi', rsi_score, 0.3))
            details['rsi'] = {'value': rsi, 'score': rsi_score}

        # MACD 信号
        macd = indicators.get('macd')
        macd_signal = indicators.get('macd_signal')
        macd_hist = indicators.get('macd_hist')

        if all(v is not None for v in [macd, macd_signal, macd_hist]) and indicators.get('macd_available', True):
            if is_long:
                # 做多：MACD > 信号线，且柱状图为正
                if macd > macd_signal and macd_hist > 0:
                    macd_score = 0.9
                elif macd > macd_signal:
                    macd_score = 0.7
                elif macd_hist > 0:
                    macd_score = 0.5
                else:
                    macd_score = 0.3
            else:
                # 做空：MACD < 信号线，且柱状图为负
                if macd < macd_signal and macd_hist < 0:
                    macd_score = 0.9
                elif macd < macd_signal:
                    macd_score = 0.7
                elif macd_hist < 0:
                    macd_score = 0.5
                else:
                    macd_score = 0.3

            scores.append(('macd', macd_score, 0.3))
            details['macd'] = {'macd': macd, 'signal': macd_signal, 'hist': macd_hist, 'score': macd_score}

        # 布林带位置
        bb_position = indicators.get('bb_position')
        if bb_position is not None and indicators.get('bb_available', True):
            if is_long:
                # 做多：接近下轨较好
                if bb_position < 0.2:
                    bb_score = 0.9
                elif bb_position < 0.4:
                    bb_score = 0.7
                elif bb_position < 0.6:
                    bb_score = 0.5
                else:
                    bb_score = 0.3
            else:
                # 做空：接近上轨较好
                if bb_position > 0.8:
                    bb_score = 0.9
                elif bb_position > 0.6:
                    bb_score = 0.7
                elif bb_position > 0.4:
                    bb_score = 0.5
                else:
                    bb_score = 0.3

            scores.append(('bb', bb_score, 0.2))
            details['bb_position'] = {'value': bb_position, 'score': bb_score}

        # 均线排列
        ema_20 = indicators.get('ema_20')
        current_price = indicators.get('current_price', 0)

        if ema_20 is not None and current_price > 0:
            if is_long:
                # 做多：价格在均线上方
                if current_price > ema_20:
                    ma_score = 0.8
                else:
                    ma_score = 0.4
            else:
                # 做空：价格在均线下方
                if current_price < ema_20:
                    ma_score = 0.8
                else:
                    ma_score = 0.4

            scores.append(('ma', ma_score, 0.2))
            details['ma'] = {'ema_20': ema_20, 'price': current_price, 'score': ma_score}

        # 使用外部信号评分（如果提供）
        if signal_score is not None:
            scores.append(('external', signal_score, 0.3))
            details['external_score'] = signal_score

        # 计算加权平均分
        if scores:
            total_weight = sum(w for _, _, w in scores)
            weighted_score = sum(s * w for _, s, w in scores) / total_weight
        else:
            weighted_score = 0.5

        # 判断结果
        if weighted_score >= 0.7:
            result = ValidationResult.PASS
            message = f"信号质量优秀 ({weighted_score:.2f})"
        elif weighted_score >= self.min_signal_score:
            result = ValidationResult.PASS
            message = f"信号质量合格 ({weighted_score:.2f})"
        elif weighted_score >= self.min_signal_score * 0.8:
            result = ValidationResult.WARN
            message = f"信号质量偏弱 ({weighted_score:.2f})"
        else:
            result = ValidationResult.BLOCK
            message = f"信号质量不足 ({weighted_score:.2f} < {self.min_signal_score})"

        return ValidationCheck(
            name="signal_quality",
            result=result,
            score=weighted_score,
            message=message,
            details=details
        )

    def _check_risk_reward(
        self,
        take_profit_ratio: float,
        stop_loss_ratio: float,
        leverage: int
    ) -> ValidationCheck:
        """
        检查风险回报比

        考虑杠杆对实际风险的影响
        """
        # 计算实际风险回报比
        if stop_loss_ratio > 0:
            risk_reward_ratio = take_profit_ratio / stop_loss_ratio
        else:
            risk_reward_ratio = 0

        # 考虑杠杆的影响
        # 高杠杆需要更高的风险回报比来覆盖更大的风险
        adjusted_min_rr = self.min_risk_reward_ratio * (1 + (leverage - 1) * 0.1)

        # 计算得分
        if risk_reward_ratio >= adjusted_min_rr * 1.5:
            score = 1.0
        elif risk_reward_ratio >= adjusted_min_rr:
            score = 0.8
        elif risk_reward_ratio >= adjusted_min_rr * 0.8:
            score = 0.5
        else:
            score = 0.3

        # 判断结果
        if risk_reward_ratio >= adjusted_min_rr:
            result = ValidationResult.PASS
            message = f"风险回报比 {risk_reward_ratio:.2f}:1 合格"
        elif risk_reward_ratio >= adjusted_min_rr * 0.8:
            result = ValidationResult.WARN
            message = f"风险回报比 {risk_reward_ratio:.2f}:1 偏低"
        else:
            result = ValidationResult.BLOCK
            message = f"风险回报比 {risk_reward_ratio:.2f}:1 不足 (需要 {adjusted_min_rr:.2f}:1)"

        return ValidationCheck(
            name="risk_reward",
            result=result,
            score=score,
            message=message,
            details={
                "risk_reward_ratio": risk_reward_ratio,
                "min_required": adjusted_min_rr,
                "leverage": leverage,
                "take_profit_ratio": take_profit_ratio,
                "stop_loss_ratio": stop_loss_ratio
            }
        )

    def _check_market_regime(
        self,
        indicators: dict[str, Any],
        df: pd.DataFrame | None,
        is_long: bool
    ) -> ValidationCheck:
        """
        检查市场环境

        识别不适合交易的市场状态
        """
        details = {}
        warnings = []

        # 检查波动性
        atr_14 = indicators.get('atr_14')
        current_price = indicators.get('current_price', 0)

        volatility_score = 1.0
        if atr_14 is not None and current_price > 0:
            volatility_pct = atr_14 / current_price
            details['volatility_pct'] = volatility_pct

            # 正常波动率约 1-2%，超过 3% 算高波动
            if volatility_pct > 0.03:
                volatility_score = 0.3
                warnings.append("极高波动率")
            elif volatility_pct > 0.02:
                volatility_score = 0.5
                warnings.append("高波动率")
            elif volatility_pct > 0.015:
                volatility_score = 0.7
            else:
                volatility_score = 1.0

        # 检查成交量
        volume = indicators.get('volume')
        volume_ma = indicators.get('volume_ma_20')

        volume_score = 1.0
        if volume is not None and volume_ma is not None and volume_ma > 0:
            volume_ratio = volume / volume_ma
            details['volume_ratio'] = volume_ratio

            if volume_ratio < 0.5:
                volume_score = 0.5
                warnings.append("成交量过低")
            elif volume_ratio > 3.0:
                volume_score = 0.7
                warnings.append("成交量异常放大")
            else:
                volume_score = 1.0

        # 检查价格相对位置
        bb_upper = indicators.get('bb_upper')
        bb_lower = indicators.get('bb_lower')

        price_position_score = 1.0
        if bb_upper is not None and bb_lower is not None and current_price > 0:
            bb_width = (bb_upper - bb_lower) / current_price
            details['bb_width'] = bb_width

            # 布林带过窄可能预示突破
            if bb_width < 0.02:
                price_position_score = 0.6
                warnings.append("布林带收窄，可能突破")

        # 使用 DataFrame 进行更深入分析
        regime_score = 1.0
        if df is not None and len(df) >= 20:
            # 检查近期价格走势
            recent_returns = df['close'].pct_change().tail(20)

            # 检查是否有大幅跳空或异常走势
            max_return = recent_returns.abs().max()
            if max_return > 0.05:
                regime_score = 0.5
                warnings.append(f"近期有{max_return*100:.1f}%的异常波动")

            # 检查趋势强度
            close_20 = df['close'].tail(20)
            trend_strength = (close_20.iloc[-1] - close_20.iloc[0]) / close_20.iloc[0]
            details['trend_strength'] = trend_strength

            # 极端趋势可能面临回调风险
            if abs(trend_strength) > 0.1:
                if (is_long and trend_strength > 0.1) or (not is_long and trend_strength < -0.1):
                    regime_score = min(regime_score, 0.6)
                    warnings.append("趋势过于延伸，回调风险高")

        # 综合得分
        overall_score = (volatility_score * 0.3 + volume_score * 0.2 +
                        price_position_score * 0.2 + regime_score * 0.3)

        # 判断结果
        if overall_score >= 0.7:
            result = ValidationResult.PASS
            message = "市场环境适合交易"
        elif overall_score >= 0.5:
            result = ValidationResult.WARN
            message = f"市场环境一般: {', '.join(warnings[:2])}"
        else:
            if self.avoid_high_volatility:
                result = ValidationResult.BLOCK
                message = f"市场环境不佳: {', '.join(warnings[:2])}"
            else:
                result = ValidationResult.WARN
                message = f"市场环境风险: {', '.join(warnings[:2])}"

        details['warnings'] = warnings

        return ValidationCheck(
            name="market_regime",
            result=result,
            score=overall_score,
            message=message,
            details=details
        )

    def _check_entry_timing(
        self,
        current_price: float,
        indicators: dict[str, Any],
        df: pd.DataFrame | None,
        is_long: bool
    ) -> ValidationCheck:
        """
        检查入场时机

        避免追高/追低，等待回调入场
        """
        details = {}
        should_wait = False

        # 检查价格相对于短期高低点的位置
        entry_score = 1.0

        if df is not None and len(df) >= 10:
            recent_high = df['high'].tail(10).max()
            recent_low = df['low'].tail(10).min()
            recent_range = recent_high - recent_low

            details['recent_high'] = recent_high
            details['recent_low'] = recent_low

            if recent_range > 0:
                price_position = (current_price - recent_low) / recent_range
                details['price_position_in_range'] = price_position

                if is_long:
                    # 做多：如果价格接近区间顶部，不是好的入场点
                    if price_position > 0.9:
                        entry_score = 0.3
                        should_wait = True
                        details['issue'] = "价格接近区间顶部"
                    elif price_position > 0.7:
                        entry_score = 0.5
                        details['issue'] = "价格偏高"
                    elif price_position < 0.3:
                        entry_score = 1.0
                        details['advantage'] = "价格接近区间底部"
                    else:
                        entry_score = 0.7
                else:
                    # 做空：如果价格接近区间底部，不是好的入场点
                    if price_position < 0.1:
                        entry_score = 0.3
                        should_wait = True
                        details['issue'] = "价格接近区间底部"
                    elif price_position < 0.3:
                        entry_score = 0.5
                        details['issue'] = "价格偏低"
                    elif price_position > 0.7:
                        entry_score = 1.0
                        details['advantage'] = "价格接近区间顶部"
                    else:
                        entry_score = 0.7

        # 检查是否在追高/追低
        ema_20 = indicators.get('ema_20')
        if ema_20 is not None and current_price > 0:
            deviation = (current_price - ema_20) / ema_20
            details['deviation_from_ema'] = deviation

            if is_long and deviation > self.max_chase_percent or not is_long and deviation < -self.max_chase_percent:
                entry_score = min(entry_score, 0.4)
                should_wait = True
                details['chasing'] = True

        # RSI 极端值检查
        rsi = indicators.get('rsi')
        if rsi is not None and (is_long and rsi > 75 or not is_long and rsi < 25):
            entry_score = min(entry_score, 0.3)
            should_wait = True
            details['rsi_extreme'] = True

        details['should_wait'] = should_wait

        # 判断结果
        if entry_score >= 0.7:
            result = ValidationResult.PASS
            message = "入场时机良好"
        elif entry_score >= 0.5:
            result = ValidationResult.WARN
            message = "入场时机一般，可考虑等待回调"
        else:
            if self.prefer_pullback_entry:
                result = ValidationResult.WARN
                message = "建议等待回调再入场"
            else:
                result = ValidationResult.WARN
                message = "入场时机不佳"

        return ValidationCheck(
            name="entry_timing",
            result=result,
            score=entry_score,
            message=message,
            details=details
        )

    def _calculate_size_multiplier(
        self,
        checks: list[ValidationCheck],
        overall_score: float
    ) -> float:
        """
        根据验证结果计算仓位调整系数

        信号越强，仓位可以越大；信号越弱，仓位越小
        """
        if overall_score >= 0.8:
            return 1.0  # 满仓
        elif overall_score >= 0.7:
            return 0.8  # 8成仓
        elif overall_score >= 0.6:
            return 0.6  # 6成仓
        elif overall_score >= 0.5:
            return 0.4  # 4成仓
        else:
            return 0.2  # 2成仓（最小仓位）

    def _calculate_suggested_entry(
        self,
        current_price: float,
        indicators: dict[str, Any],
        df: pd.DataFrame | None,
        is_long: bool
    ) -> float | None:
        """
        计算建议的入场价格

        如果当前价格不理想，给出更好的入场价格建议
        """
        # 使用 EMA 和支撑/阻力计算建议价格
        ema_20 = indicators.get('ema_20')
        bb_lower = indicators.get('bb_lower')
        bb_upper = indicators.get('bb_upper')

        if is_long:
            # 做多：建议在 EMA 或布林带下轨附近入场
            candidates = []
            if ema_20 is not None and ema_20 < current_price:
                candidates.append(ema_20)
            if bb_lower is not None and bb_lower < current_price:
                candidates.append(bb_lower * 1.01)  # 略高于下轨

            if candidates:
                return max(candidates)  # 取最高的作为入场价
        else:
            # 做空：建议在 EMA 或布林带上轨附近入场
            candidates = []
            if ema_20 is not None and ema_20 > current_price:
                candidates.append(ema_20)
            if bb_upper is not None and bb_upper > current_price:
                candidates.append(bb_upper * 0.99)  # 略低于上轨

            if candidates:
                return min(candidates)  # 取最低的作为入场价

        return None


def create_validator_from_config(config: dict[str, Any]) -> DecisionValidator:
    """
    从配置创建决策验证器

    Args:
        config: 配置字典

    Returns:
        DecisionValidator 实例
    """
    enhanced_config = config.get('enhanced_analysis', {})

    return DecisionValidator(
        require_trend_alignment=enhanced_config.get('require_trend_alignment', True),
        min_aligned_timeframes=enhanced_config.get('min_aligned_timeframes', 2),
        min_signal_score=enhanced_config.get('min_confidence', 0.4),
        min_risk_reward_ratio=config.get('account_protection', {}).get('min_risk_reward_ratio', 1.5),
        avoid_high_volatility=enhanced_config.get('avoid_high_volatility', True),
        prefer_pullback_entry=enhanced_config.get('prefer_pullback_entry', True),
        max_chase_percent=enhanced_config.get('max_chase_percent', 0.02)
    )
