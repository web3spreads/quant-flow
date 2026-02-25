"""
增强型交易引擎
整合市场状态分析、风险管理和信号评分系统，提供更智能的交易决策

核心功能：
1. 智能市场分析
2. 风险控制集成
3. 信号质量评估
4. 决策验证和过滤
5. 自适应参数调整
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd

from src.data.market_state import MarketAnalysisResult, MarketState, MarketStateAnalyzer
from src.data.signal_scorer import SignalQuality, SignalScorer, SignalType, TradingSignal
from src.trading.risk_manager import (
    PositionSizeResult,
    RiskAssessment,
    RiskManager,
    RiskParameters,
    StopLossResult,
    TakeProfitResult,
)

logger = logging.getLogger("QuantFlow.EnhancedEngine")


@dataclass
class EnhancedDecision:
    """增强型交易决策"""

    # 决策信息
    decision_id: str
    timestamp: str
    symbol: str

    # 建议操作
    action: str  # "buy", "sell", "sell_short", "buy_to_cover", "hold"
    should_trade: bool

    # 分析结果
    market_analysis: MarketAnalysisResult
    trading_signal: TradingSignal
    risk_assessment: RiskAssessment

    # 交易参数
    entry_price: float
    stop_loss: StopLossResult
    take_profit: TakeProfitResult
    position_size: PositionSizeResult

    # 置信度和质量
    overall_confidence: float
    decision_quality: str

    # 推理和警告
    reasoning: list[str]
    warnings: list[str]
    blockers: list[str]  # 阻止交易的原因

    # Prompt注入文本
    prompt_injection: str

    # 元数据
    metadata: dict[str, Any] = field(default_factory=dict)


class EnhancedTradingEngine:
    """增强型交易引擎"""

    # QLib 强信号判定阈值
    QLIB_STRONG_STRENGTH_THRESHOLD = 0.5
    QLIB_STRONG_CONFIDENCE_THRESHOLD = 0.3

    # 综合置信度权重（有 QLib 信号时）
    CONFIDENCE_WEIGHT_QLIB = 0.40
    CONFIDENCE_WEIGHT_SIGNAL_WITH_QLIB = 0.30
    CONFIDENCE_WEIGHT_RISK_WITH_QLIB = 0.20
    CONFIDENCE_WEIGHT_MTF_WITH_QLIB = 0.10

    # 综合置信度权重（无 QLib 信号时）
    CONFIDENCE_WEIGHT_SIGNAL = 0.50
    CONFIDENCE_WEIGHT_RISK = 0.30
    CONFIDENCE_WEIGHT_MTF = 0.20

    def __init__(
        self,
        risk_params: RiskParameters | None = None,
        signal_weights: dict[str, float] | None = None,
        min_signal_quality: SignalQuality = SignalQuality.FAIR,
        min_confidence: float = 0.4,
        enable_risk_filter: bool = True,
        enable_timing_filter: bool = True,
    ):
        """
        初始化增强型交易引擎

        Args:
            risk_params: 风险参数配置
            signal_weights: 信号因子权重
            min_signal_quality: 最低信号质量要求
            min_confidence: 最低置信度要求
            enable_risk_filter: 是否启用风险过滤
            enable_timing_filter: 是否启用时机过滤
        """
        self.market_analyzer = MarketStateAnalyzer()
        self.signal_scorer = SignalScorer(weights=signal_weights)
        self.risk_manager = RiskManager(risk_params=risk_params)

        self.min_signal_quality = min_signal_quality
        self.min_confidence = min_confidence
        self.enable_risk_filter = enable_risk_filter
        self.enable_timing_filter = enable_timing_filter

        self._decision_counter = 0
        self._decision_history: list[EnhancedDecision] = []

    def analyze_and_decide(
        self,
        symbol: str,
        df: pd.DataFrame,
        current_price: float,
        account_balance: float,
        current_positions: list[dict[str, Any]],
        multi_timeframe_trends: dict[str, str] | None = None,
        leverage: int = 3,
        qlib_signal: dict | None = None,
    ) -> EnhancedDecision:
        """
        执行完整的分析和决策流程

        Args:
            symbol: 交易对符号
            df: OHLCV DataFrame (已计算技术指标)
            current_price: 当前价格
            account_balance: 账户余额
            current_positions: 当前持仓列表
            multi_timeframe_trends: 多周期趋势
            leverage: 杠杆倍数

        Returns:
            EnhancedDecision: 增强型交易决策
        """
        timestamp = datetime.now()
        self._decision_counter += 1
        decision_id = f"{symbol}_{timestamp.strftime('%Y%m%d%H%M%S')}_{self._decision_counter}"

        reasoning = []
        warnings = []
        blockers = []

        # 第一步：市场状态分析
        market_analysis = self.market_analyzer.analyze(
            df=df, current_price=current_price, multi_timeframe_trends=multi_timeframe_trends
        )
        reasoning.append(f"市场状态: {market_analysis.state.value}")

        # 第二步：风险评估
        current_position = self._find_position(symbol, current_positions)
        risk_assessment = self.risk_manager.assess_risk(
            account_balance=account_balance,
            current_positions=current_positions,
            market_volatility=market_analysis.volatility.volatility_state,
        )

        if risk_assessment.risk_level.value >= 5:  # VERY_HIGH or EXTREME
            warnings.append(f"风险级别较高: {risk_assessment.risk_level.name}")

        # 第三步：信号评分
        trading_signal = self.signal_scorer.score_signal(
            symbol=symbol,
            market_data={"current_price": current_price},
            trend_analysis=market_analysis.trend,
            momentum_analysis=market_analysis.momentum,
            volume_analysis=market_analysis.volume,
            volatility_analysis=market_analysis.volatility,
            support_resistance=market_analysis.support_resistance,
            multi_timeframe_trends=multi_timeframe_trends,
            current_position=current_position,
        )

        reasoning.append(f"信号质量: {trading_signal.quality.value}")
        reasoning.append(f"信号置信度: {trading_signal.confidence:.0%}")

        # 第四步：计算止损止盈
        is_long = trading_signal.signal_type in [SignalType.LONG_ENTRY, SignalType.SHORT_EXIT]

        stop_loss_result = self.risk_manager.calculate_dynamic_stop_loss(
            entry_price=current_price,
            is_long=is_long,
            current_atr=market_analysis.volatility.current_atr,
            volatility_state=market_analysis.volatility.volatility_state,
            support_level=market_analysis.support_resistance.nearest_support,
            resistance_level=market_analysis.support_resistance.nearest_resistance,
        )

        take_profit_result = self.risk_manager.calculate_dynamic_take_profit(
            entry_price=current_price,
            stop_loss_price=stop_loss_result.stop_loss_price,
            is_long=is_long,
            current_atr=market_analysis.volatility.current_atr,
            resistance_level=market_analysis.support_resistance.nearest_resistance,
            support_level=market_analysis.support_resistance.nearest_support,
        )

        # 第五步：计算仓位大小
        position_size_result = self.risk_manager.calculate_position_size(
            account_balance=account_balance,
            entry_price=current_price,
            stop_loss_price=stop_loss_result.stop_loss_price,
            leverage=leverage,
            current_atr=market_analysis.volatility.current_atr,
            volatility_state=market_analysis.volatility.volatility_state,
        )

        # 第六步：决策过滤和验证
        should_trade, action, filter_blockers = self._apply_filters(
            trading_signal=trading_signal,
            risk_assessment=risk_assessment,
            market_analysis=market_analysis,
            current_position=current_position,
            qlib_signal=qlib_signal,
        )

        blockers.extend(filter_blockers)

        # 第七步：计算综合置信度和质量
        overall_confidence = self._calculate_overall_confidence(
            signal_confidence=trading_signal.confidence,
            risk_assessment=risk_assessment,
            market_analysis=market_analysis,
            qlib_signal=qlib_signal,
        )

        decision_quality = self._determine_decision_quality(
            trading_signal=trading_signal, overall_confidence=overall_confidence, blockers=blockers
        )

        # 添加警告
        warnings.extend(trading_signal.warnings)
        warnings.extend(risk_assessment.warnings)

        # 生成Prompt注入文本
        prompt_injection = self._generate_prompt_injection(
            market_analysis=market_analysis,
            trading_signal=trading_signal,
            risk_assessment=risk_assessment,
            stop_loss=stop_loss_result,
            take_profit=take_profit_result,
            position_size=position_size_result,
            decision_quality=decision_quality,
        )

        # 创建决策对象
        decision = EnhancedDecision(
            decision_id=decision_id,
            timestamp=timestamp.isoformat(),
            symbol=symbol,
            action=action,
            should_trade=should_trade,
            market_analysis=market_analysis,
            trading_signal=trading_signal,
            risk_assessment=risk_assessment,
            entry_price=current_price,
            stop_loss=stop_loss_result,
            take_profit=take_profit_result,
            position_size=position_size_result,
            overall_confidence=overall_confidence,
            decision_quality=decision_quality,
            reasoning=reasoning,
            warnings=warnings,
            blockers=blockers,
            prompt_injection=prompt_injection,
        )

        # 保存到历史
        self._decision_history.append(decision)
        if len(self._decision_history) > 500:
            self._decision_history = self._decision_history[-250:]

        return decision

    def _find_position(self, symbol: str, positions: list[dict[str, Any]]) -> dict[str, Any] | None:
        """查找指定符号的持仓"""
        for pos in positions:
            if pos.get("coin") == symbol:
                return pos
        return None

    def _apply_filters(
        self,
        trading_signal: TradingSignal,
        risk_assessment: RiskAssessment,
        market_analysis: MarketAnalysisResult,
        current_position: dict[str, Any] | None,
        qlib_signal: dict | None = None,
    ) -> tuple[bool, str, list[str]]:
        """
        应用过滤器决定是否交易（融合 QLib 信号）

        Returns:
            (should_trade, action, blockers)
        """
        blockers = []

        # 解析 QLib 信号
        qlib_strength = 0.0
        qlib_confidence = 0.0
        qlib_direction = None
        if qlib_signal:
            qlib_strength = qlib_signal.get("strength", 0)
            qlib_confidence = qlib_signal.get("confidence", 0)
            qlib_direction = qlib_signal.get("direction", "")
        qlib_is_strong = (
            qlib_strength > self.QLIB_STRONG_STRENGTH_THRESHOLD
            and qlib_confidence > self.QLIB_STRONG_CONFIDENCE_THRESHOLD
        )

        # 检查是否应该平仓（优先级最高，高于 QLib）
        if current_position:
            size = float(current_position.get("szi", 0))
            if size > 0 and trading_signal.signal_type == SignalType.LONG_EXIT:
                return True, "sell", []
            elif size < 0 and trading_signal.signal_type == SignalType.SHORT_EXIT:
                return True, "buy_to_cover", []

        # 检查信号类型 —— QLib 可覆盖 NO_SIGNAL
        if trading_signal.signal_type == SignalType.NO_SIGNAL:
            if qlib_is_strong:
                # QLib 强信号覆盖 NO_SIGNAL
                if "做多" in qlib_direction or "LONG" in qlib_direction.upper():
                    action = "buy"
                    logger.info(
                        f"QLib 信号覆盖 NO_SIGNAL → 做多 "
                        f"(强度={qlib_strength:.3f}, 置信度={qlib_confidence:.3f})"
                    )
                elif "做空" in qlib_direction or "SHORT" in qlib_direction.upper():
                    action = "sell_short"
                    logger.info(
                        f"QLib 信号覆盖 NO_SIGNAL → 做空 "
                        f"(强度={qlib_strength:.3f}, 置信度={qlib_confidence:.3f})"
                    )
                else:
                    blockers.append("无有效交易信号")
                    return False, "hold", blockers

                # QLib 覆盖后仍检查风险
                if self.enable_risk_filter:
                    if not risk_assessment.can_trade:
                        blockers.append("风险评估不通过")
                    if risk_assessment.risk_level.value >= 6:
                        blockers.append(f"风险过高: {risk_assessment.risk_level.name}")

                should_trade = len(blockers) == 0
                if not should_trade:
                    action = "hold"
                return should_trade, action, blockers
            else:
                blockers.append("无有效交易信号")
                return False, "hold", blockers

        # 映射信号类型到操作
        signal_to_action = {
            SignalType.LONG_ENTRY: "buy",
            SignalType.LONG_EXIT: "sell",
            SignalType.SHORT_ENTRY: "sell_short",
            SignalType.SHORT_EXIT: "buy_to_cover",
        }
        action = signal_to_action.get(trading_signal.signal_type, "hold")

        # 过滤1: 信号质量检查
        quality_order = [
            SignalQuality.INVALID,
            SignalQuality.POOR,
            SignalQuality.FAIR,
            SignalQuality.GOOD,
            SignalQuality.EXCELLENT,
        ]

        signal_quality_index = quality_order.index(trading_signal.quality)
        min_quality_index = quality_order.index(self.min_signal_quality)

        if signal_quality_index < min_quality_index:
            if qlib_is_strong:
                logger.info("QLib 强信号放松信号质量过滤")
            else:
                blockers.append(
                    f"信号质量不足: {trading_signal.quality.value} < {self.min_signal_quality.value}"
                )

        # 过滤2: 置信度检查
        if trading_signal.confidence < self.min_confidence:
            if qlib_is_strong:
                logger.info("QLib 强信号放松置信度过滤")
            else:
                blockers.append(
                    f"置信度不足: {trading_signal.confidence:.0%} < {self.min_confidence:.0%}"
                )

        # 过滤3: 风险检查
        if self.enable_risk_filter:
            if not risk_assessment.can_trade:
                blockers.append("风险评估不通过")

            if risk_assessment.risk_level.value >= 6:  # EXTREME
                blockers.append(f"风险过高: {risk_assessment.risk_level.name}")

        # 过滤4: 时机检查
        if self.enable_timing_filter:
            if not trading_signal.timing.is_optimal and trading_signal.timing.timing_score < 0.4:
                blockers.append(f"入场时机不佳: {trading_signal.timing.timing_score:.0%}")

        # 过滤5: 信号验证
        if not trading_signal.validation.is_valid:
            blockers.append("信号验证失败")

        # 过滤6: 市场状态检查
        dangerous_states = [MarketState.UNKNOWN]
        if market_analysis.state in dangerous_states:
            blockers.append(f"市场状态不确定: {market_analysis.state.value}")

        should_trade = len(blockers) == 0
        if not should_trade:
            action = "hold"

        return should_trade, action, blockers

    def _calculate_overall_confidence(
        self,
        signal_confidence: float,
        risk_assessment: RiskAssessment,
        market_analysis: MarketAnalysisResult,
        qlib_signal: dict | None = None,
    ) -> float:
        """计算综合置信度（融合 QLib 信号）"""
        risk_factor = 1 - (risk_assessment.risk_score / 100) * 0.5
        mtf_factor = market_analysis.multi_timeframe_alignment

        if qlib_signal and qlib_signal.get("confidence", 0) > 0:
            # 有 QLib 信号时融合置信度
            qlib_conf = qlib_signal.get("confidence", 0)
            qlib_str = qlib_signal.get("strength", 0)
            confidence = (
                (qlib_conf * qlib_str) * self.CONFIDENCE_WEIGHT_QLIB
                + signal_confidence * self.CONFIDENCE_WEIGHT_SIGNAL_WITH_QLIB
                + risk_factor * self.CONFIDENCE_WEIGHT_RISK_WITH_QLIB
                + mtf_factor * self.CONFIDENCE_WEIGHT_MTF_WITH_QLIB
            )
        else:
            # 无 QLib 信号时保持原有权重
            confidence = (
                signal_confidence * self.CONFIDENCE_WEIGHT_SIGNAL
                + risk_factor * self.CONFIDENCE_WEIGHT_RISK
                + mtf_factor * self.CONFIDENCE_WEIGHT_MTF
            )

        return max(0, min(1, confidence))

    def _determine_decision_quality(
        self, trading_signal: TradingSignal, overall_confidence: float, blockers: list[str]
    ) -> str:
        """确定决策质量"""
        if blockers:
            return "blocked"

        if overall_confidence >= 0.8 and trading_signal.quality == SignalQuality.EXCELLENT:
            return "excellent"
        elif overall_confidence >= 0.6 and trading_signal.quality in [
            SignalQuality.EXCELLENT,
            SignalQuality.GOOD,
        ]:
            return "good"
        elif overall_confidence >= 0.4:
            return "fair"
        else:
            return "poor"

    def _generate_prompt_injection(
        self,
        market_analysis: MarketAnalysisResult,
        trading_signal: TradingSignal,
        risk_assessment: RiskAssessment,
        stop_loss: StopLossResult,
        take_profit: TakeProfitResult,
        position_size: PositionSizeResult,
        decision_quality: str,
    ) -> str:
        """生成用于注入Prompt的综合分析文本"""
        lines = []

        # 综合评估摘要
        lines.append("# 🔬 智能分析系统评估")
        lines.append("")
        lines.append(f"**决策质量**: {decision_quality}")
        lines.append(f"**市场状态**: {market_analysis.state.value}")
        lines.append(f"**信号类型**: {trading_signal.signal_type.value}")
        lines.append(f"**信号质量**: {trading_signal.quality.value}")
        lines.append(f"**综合置信度**: {trading_signal.confidence:.0%}")
        lines.append(f"**风险级别**: {risk_assessment.risk_level.name}")
        lines.append("")

        # 市场分析摘要
        lines.append("## 市场分析")
        lines.append(f"- 趋势方向: {market_analysis.trend.direction}")
        lines.append(f"- 趋势强度: {market_analysis.trend.strength:.2f}")
        lines.append(f"- 均线排列: {market_analysis.trend.ma_alignment}")
        lines.append(f"- 波动状态: {market_analysis.volatility.volatility_state}")
        lines.append(
            f"- RSI状态: {market_analysis.momentum.rsi_state} ({market_analysis.momentum.rsi_value:.1f})"
        )
        lines.append(f"- MACD状态: {market_analysis.momentum.macd_state}")
        if market_analysis.momentum.macd_crossover:
            lines.append(f"- MACD交叉: {market_analysis.momentum.macd_crossover}")
        lines.append(f"- 多周期一致性: {market_analysis.multi_timeframe_alignment:.0%}")
        lines.append("")

        # 支撑阻力
        sr = market_analysis.support_resistance
        lines.append("## 关键价位")
        lines.append(
            f"- 最近支撑: ${sr.nearest_support:.2f} (距离: {sr.price_to_support_pct:.1f}%)"
        )
        lines.append(
            f"- 最近阻力: ${sr.nearest_resistance:.2f} (距离: {sr.price_to_resistance_pct:.1f}%)"
        )
        lines.append("")

        # 信号因子
        lines.append("## 信号因子分析")
        for f in sorted(trading_signal.factors, key=lambda x: abs(x.contribution), reverse=True)[
            :4
        ]:
            direction = "📈" if f.score > 0 else "📉" if f.score < 0 else "➖"
            lines.append(f"- {direction} **{f.name}**: {f.description}")
        lines.append("")

        # 交易建议
        lines.append("## 交易建议")
        lines.append(f"- **建议操作**: {trading_signal.suggested_action}")
        lines.append(f"- 建议止损: ${stop_loss.stop_loss_price:.2f} ({stop_loss.method})")
        lines.append(f"- 建议止盈: ${take_profit.take_profit_price:.2f} ({take_profit.method})")
        lines.append(f"- 风险回报比: {take_profit.risk_reward_ratio:.2f}")
        lines.append(f"- 建议仓位: ${position_size.position_size:.2f} ({position_size.method})")
        if position_size.adjustments:
            for adj in position_size.adjustments:
                lines.append(f"  - {adj}")
        lines.append("")

        # 入场时机
        lines.append("## 入场时机")
        timing = trading_signal.timing
        lines.append(f"- 时机评分: {timing.timing_score:.0%}")
        lines.append(f"- 是否最佳: {'✅ 是' if timing.is_optimal else '❌ 否'}")
        lines.append(f"- 分析: {timing.reasoning}")
        if timing.wait_for_price:
            lines.append(f"- 建议等待价格: ${timing.wait_for_price:.2f}")
        lines.append("")

        # 确认和警告
        if trading_signal.confirmation.confirmations:
            lines.append("## ✅ 信号确认")
            for c in trading_signal.confirmation.confirmations[:3]:
                lines.append(f"- {c}")
            lines.append("")

        all_warnings = list(trading_signal.warnings) + list(risk_assessment.warnings)
        if all_warnings:
            lines.append("## ⚠️ 警告")
            for w in all_warnings[:5]:
                lines.append(f"- {w}")
            lines.append("")

        # 风险提示
        if risk_assessment.recommendations:
            lines.append("## 💡 风险建议")
            for r in risk_assessment.recommendations[:3]:
                lines.append(f"- {r}")

        return "\n".join(lines)

    def get_analysis_summary(self, decision: EnhancedDecision) -> str:
        """获取决策的简短摘要"""
        return (
            f"[{decision.symbol}] "
            f"状态: {decision.market_analysis.state.value}, "
            f"信号: {decision.trading_signal.signal_type.value}, "
            f"质量: {decision.decision_quality}, "
            f"操作: {decision.action}, "
            f"置信度: {decision.overall_confidence:.0%}"
        )

    def get_decision_history(
        self, symbol: str | None = None, limit: int = 10
    ) -> list[EnhancedDecision]:
        """获取决策历史"""
        history = self._decision_history
        if symbol:
            history = [d for d in history if d.symbol == symbol]
        return history[-limit:]

    def update_risk_parameters(self, params: RiskParameters):
        """更新风险参数"""
        self.risk_manager = RiskManager(risk_params=params)

    def update_signal_weights(self, weights: dict[str, float]):
        """更新信号权重"""
        self.signal_scorer = SignalScorer(weights=weights)


def create_enhanced_engine_from_config(config: dict[str, Any]) -> EnhancedTradingEngine:
    """
    从配置字典创建增强型交易引擎

    Args:
        config: 配置字典

    Returns:
        EnhancedTradingEngine 实例
    """
    # 解析风险参数
    risk_config = config.get("risk", {})
    risk_params = RiskParameters(
        max_risk_per_trade=risk_config.get("max_risk_per_trade", 0.02),
        max_total_exposure=risk_config.get("max_total_exposure", 0.5),
        max_single_position=risk_config.get("max_single_position", 0.2),
        default_stop_loss_pct=risk_config.get("stop_loss_ratio", 0.02),
        default_take_profit_pct=risk_config.get("take_profit_ratio", 0.05),
        min_risk_reward_ratio=risk_config.get("min_risk_reward", 1.5),
        atr_stop_loss_multiplier=risk_config.get("atr_sl_multiplier", 1.5),
        atr_take_profit_multiplier=risk_config.get("atr_tp_multiplier", 3.0),
        trailing_stop_enabled=risk_config.get("trailing_stop_enabled", True),
        trailing_stop_distance_pct=risk_config.get("trailing_stop_distance", 0.015),
        volatility_adjustment_enabled=risk_config.get("volatility_adjustment", True),
    )

    # 解析信号权重
    signal_config = config.get("signal", {})
    signal_weights = signal_config.get("weights", None)

    # 解析过滤器配置
    filter_config = config.get("filter", {})
    min_quality_str = filter_config.get("min_signal_quality", "fair")
    quality_map = {
        "excellent": SignalQuality.EXCELLENT,
        "good": SignalQuality.GOOD,
        "fair": SignalQuality.FAIR,
        "poor": SignalQuality.POOR,
        "invalid": SignalQuality.INVALID,
    }
    min_quality = quality_map.get(min_quality_str.lower(), SignalQuality.FAIR)

    return EnhancedTradingEngine(
        risk_params=risk_params,
        signal_weights=signal_weights,
        min_signal_quality=min_quality,
        min_confidence=filter_config.get("min_confidence", 0.4),
        enable_risk_filter=filter_config.get("enable_risk_filter", True),
        enable_timing_filter=filter_config.get("enable_timing_filter", True),
    )
