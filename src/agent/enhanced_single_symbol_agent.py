"""
增强型单币种交易 Agent
整合智能分析系统，提供更可靠的交易决策

继承自原有的 SingleSymbolAgent，增加：
1. 增强型交易引擎集成
2. 智能市场状态分析
3. 动态风险管理
4. 信号质量评估
5. 决策前多维度验证
6. 多周期趋势共振确认
7. 动态仓位管理
"""

from typing import Any

import pandas as pd

from src.agent.single_symbol_agent import SingleSymbolAgent
from src.agents.common.utils.helpers import send_error_notification
from src.data.signal_scorer import SignalQuality
from src.fees import FeeRates
from src.llm import LLMClientManager
from src.prompt_manager import PromptManager
from src.trading.decision_validator import DecisionValidation, DecisionValidator
from src.trading.enhanced_engine import EnhancedDecision, EnhancedTradingEngine
from src.trading.order_manager import OrderManager
from src.trading.risk_manager import RiskParameters
from src.utils.logger import TradingLogger


class EnhancedSingleSymbolAgent(SingleSymbolAgent):
    """增强型单币种交易 Agent"""

    def __init__(
        self,
        symbol: str,
        order_manager: OrderManager,
        logger: TradingLogger,
        llm_manager: LLMClientManager,
        temperature: float = 0.1,
        max_iterations: int = 5,
        trade_amount: float = 100.0,
        max_leverage: int = 10,
        take_profit_ratio: float = 0.05,
        stop_loss_ratio: float = 0.02,
        notifier=None,
        prompt_manager: PromptManager | None = None,
        fee_rates: FeeRates | None = None,
        limit_order_enabled: bool = False,
        # 增强参数
        enable_enhanced_analysis: bool = True,
        min_signal_quality: str = "fair",
        min_confidence: float = 0.4,
        enable_risk_filter: bool = True,
        enable_timing_filter: bool = True,
        risk_params: RiskParameters | None = None,
    ):
        """
        初始化增强型单币种交易 Agent

        新增参数:
            enable_enhanced_analysis: 是否启用增强分析
            min_signal_quality: 最低信号质量要求
            min_confidence: 最低置信度要求
            enable_risk_filter: 是否启用风险过滤
            enable_timing_filter: 是否启用时机过滤
            risk_params: 风险参数配置
        """
        # 调用父类初始化
        super().__init__(
            symbol=symbol,
            order_manager=order_manager,
            logger=logger,
            llm_manager=llm_manager,
            temperature=temperature,
            max_iterations=max_iterations,
            trade_amount=trade_amount,
            max_leverage=max_leverage,
            take_profit_ratio=take_profit_ratio,
            stop_loss_ratio=stop_loss_ratio,
            notifier=notifier,
            prompt_manager=prompt_manager,
            fee_rates=fee_rates,
            limit_order_enabled=limit_order_enabled,
        )

        # 增强分析配置
        self.enable_enhanced_analysis = enable_enhanced_analysis

        # 创建风险参数
        if risk_params is None:
            risk_params = RiskParameters(
                default_stop_loss_pct=stop_loss_ratio,
                default_take_profit_pct=take_profit_ratio,
                atr_stop_loss_multiplier=1.5,
                atr_take_profit_multiplier=3.0,
            )

        # 解析信号质量
        quality_map = {
            "excellent": SignalQuality.EXCELLENT,
            "good": SignalQuality.GOOD,
            "fair": SignalQuality.FAIR,
            "poor": SignalQuality.POOR,
        }
        min_quality = quality_map.get(min_signal_quality.lower(), SignalQuality.FAIR)

        # 初始化增强型交易引擎
        if self.enable_enhanced_analysis:
            self.enhanced_engine = EnhancedTradingEngine(
                risk_params=risk_params,
                min_signal_quality=min_quality,
                min_confidence=min_confidence,
                enable_risk_filter=enable_risk_filter,
                enable_timing_filter=enable_timing_filter,
            )
        else:
            self.enhanced_engine = None

        # 缓存最近的增强分析结果
        self._last_enhanced_decision: EnhancedDecision | None = None

        # 初始化决策验证器
        self.decision_validator = DecisionValidator(
            require_trend_alignment=True,
            min_aligned_timeframes=2,
            min_signal_score=min_confidence,
            min_risk_reward_ratio=1.5,  # 将从配置读取
            avoid_high_volatility=True,
            prefer_pullback_entry=True,
        )

        # 缓存最近的验证结果
        self._last_validation: DecisionValidation | None = None

    def analyze_with_enhanced_engine(
        self,
        df: pd.DataFrame,
        current_price: float,
        account_balance: float,
        current_positions: list,
        multi_timeframe_trends: dict[str, str] | None = None,
    ) -> EnhancedDecision | None:
        """
        使用增强引擎进行分析

        Args:
            df: OHLCV DataFrame (已计算技术指标)
            current_price: 当前价格
            account_balance: 账户余额
            current_positions: 当前持仓列表
            multi_timeframe_trends: 多周期趋势

        Returns:
            EnhancedDecision 或 None
        """
        if not self.enable_enhanced_analysis or not self.enhanced_engine:
            return None

        try:
            decision = self.enhanced_engine.analyze_and_decide(
                symbol=self.symbol,
                df=df,
                current_price=current_price,
                account_balance=account_balance,
                current_positions=current_positions,
                multi_timeframe_trends=multi_timeframe_trends,
                leverage=self.max_leverage,
            )

            self._last_enhanced_decision = decision

            # 记录分析摘要
            summary = self.enhanced_engine.get_analysis_summary(decision)
            self.logger.print_info(f"[{self.symbol}] 增强分析: {summary}")

            return decision

        except Exception as e:
            self.logger.print_warning(f"[{self.symbol}] 增强分析失败: {e}")
            send_error_notification(
                notifier=self.notifier,
                exception=e,
                title=f"{self.symbol} 增强分析失败",
                context_details={
                    "交易对": self.symbol,
                    "当前价": f"${current_price}",
                    "阶段": "增强型交易引擎分析",
                    "说明": "增强分析异常，将回退到基础 LLM 决策",
                },
            )
            return None

    def get_enhanced_prompt_injection(self) -> str:
        """
        获取增强分析的Prompt注入文本

        Returns:
            可注入到主Prompt的分析文本
        """
        if self._last_enhanced_decision:
            return self._last_enhanced_decision.prompt_injection
        return ""

    def should_override_decision(self, llm_decision: str) -> tuple[bool, str, str]:
        """
        检查是否应该覆盖LLM的决策

        基于增强分析的结果，判断LLM的决策是否安全

        Args:
            llm_decision: LLM给出的决策 ("BUY", "SELL", "SELL_SHORT", "BUY_TO_COVER", "DO_NOTHING")

        Returns:
            (should_override, new_decision, reason)
        """
        if not self._last_enhanced_decision:
            return False, llm_decision, ""

        decision = self._last_enhanced_decision

        # 如果增强分析有阻止因素
        if decision.blockers:
            # 阻止开仓操作
            if llm_decision in ["BUY", "SELL_SHORT"]:
                return True, "DO_NOTHING", f"增强分析阻止: {', '.join(decision.blockers[:2])}"

        # 如果风险过高
        if decision.risk_assessment.risk_level.value >= 5:  # VERY_HIGH or higher
            if llm_decision in ["BUY", "SELL_SHORT"]:
                return True, "DO_NOTHING", f"风险过高: {decision.risk_assessment.risk_level.name}"

        # 如果信号质量太差
        if decision.trading_signal.quality == SignalQuality.INVALID:
            if llm_decision in ["BUY", "SELL_SHORT"]:
                return True, "DO_NOTHING", "信号质量无效"

        # 如果增强分析建议的方向与LLM相反
        enhanced_action = decision.action
        if enhanced_action == "hold" and llm_decision in ["BUY", "SELL_SHORT"]:
            # 增强分析建议观望，但LLM要开仓
            if decision.overall_confidence < 0.3:
                return True, "DO_NOTHING", "增强分析置信度过低"

        return False, llm_decision, ""

    def get_dynamic_stop_loss(self) -> float | None:
        """获取动态止损价格"""
        if self._last_enhanced_decision:
            return self._last_enhanced_decision.stop_loss.stop_loss_price
        return None

    def get_dynamic_take_profit(self) -> float | None:
        """获取动态止盈价格"""
        if self._last_enhanced_decision:
            return self._last_enhanced_decision.take_profit.take_profit_price
        return None

    def get_suggested_position_size(self) -> float | None:
        """获取建议仓位大小"""
        if self._last_enhanced_decision:
            return self._last_enhanced_decision.position_size.position_size
        return None

    def get_last_enhanced_decision(self) -> EnhancedDecision | None:
        """获取最近的增强分析决策"""
        return self._last_enhanced_decision

    def get_last_validation(self) -> DecisionValidation | None:
        """获取最近的验证结果"""
        return self._last_validation

    def validate_decision(
        self,
        decision: str,
        current_price: float,
        indicators: dict[str, Any],
        multi_timeframe_trends: dict[str, str],
        df: pd.DataFrame | None = None,
        signal_score: float | None = None,
    ) -> DecisionValidation:
        """
        验证交易决策

        在执行交易前进行多维度验证

        Args:
            decision: 原始决策 ("BUY", "SELL_SHORT", "DO_NOTHING" 等)
            current_price: 当前价格
            indicators: 技术指标字典
            multi_timeframe_trends: 多周期趋势
            df: OHLCV DataFrame (可选)
            signal_score: 信号评分 (可选)

        Returns:
            DecisionValidation 验证结果
        """
        # 如果有增强分析结果，使用其信号评分
        if signal_score is None and self._last_enhanced_decision:
            signal_score = self._last_enhanced_decision.overall_confidence

        validation = self.decision_validator.validate_decision(
            decision=decision,
            symbol=self.symbol,
            current_price=current_price,
            indicators=indicators,
            multi_timeframe_trends=multi_timeframe_trends,
            take_profit_ratio=self.take_profit_ratio,
            stop_loss_ratio=self.stop_loss_ratio,
            leverage=self.max_leverage,
            df=df,
            signal_score=signal_score,
        )

        self._last_validation = validation

        # 记录验证结果
        self.logger.print_info(f"[{self.symbol}] 决策验证: {validation.get_summary()}")

        return validation

    def make_decision_with_enhanced_analysis(
        self,
        market_data: dict[str, Any],
        multi_timeframe_trends: dict[str, str],
        current_positions: list,
        max_positions: int,
        historical_summary: str | None = None,
        enriched_data: dict[str, Any] | None = None,
        df: pd.DataFrame | None = None,
        account_balance: float | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """
        使用增强分析进行决策

        与原有 make_decision 兼容，但在有 df 和 account_balance 时会先执行增强分析

        Args:
            market_data: 市场数据
            multi_timeframe_trends: 多周期趋势
            current_positions: 当前持仓
            max_positions: 最大持仓数
            historical_summary: 历史决策汇总（可选）
            enriched_data: 增强数据（可选）
            df: OHLCV DataFrame（可选，用于增强分析）
            account_balance: 账户余额（可选，用于增强分析）

        Returns:
            (决策类型, 决策详情)
        """
        # 如果提供了必要数据，先执行增强分析
        if df is not None and account_balance is not None and self.enable_enhanced_analysis:
            current_price = market_data.get("current_price", 0)

            enhanced_decision = self.analyze_with_enhanced_engine(
                df=df,
                current_price=current_price,
                account_balance=account_balance,
                current_positions=current_positions,
                multi_timeframe_trends=multi_timeframe_trends,
            )

            if enhanced_decision:
                # 将增强分析结果注入到 enriched_data
                if enriched_data is None:
                    enriched_data = {}
                enriched_data["enhanced_analysis"] = enhanced_decision.prompt_injection

                # 如果增强分析建议不交易，可以直接返回
                if not enhanced_decision.should_trade and enhanced_decision.blockers:
                    self.logger.print_info(
                        f"[{self.symbol}] 增强分析建议观望: {', '.join(enhanced_decision.blockers[:2])}"
                    )
                    # 仍然调用 LLM 但会在结果中包含增强分析的警告

        # 调用原有的决策逻辑
        decision, details = self.make_decision(
            market_data=market_data,
            multi_timeframe_trends=multi_timeframe_trends,
            current_positions=current_positions,
            max_positions=max_positions,
            historical_summary=historical_summary,
            enriched_data=enriched_data,
        )

        # 检查是否需要覆盖决策
        should_override, new_decision, reason = self.should_override_decision(decision)
        if should_override:
            self.logger.print_warning(
                f"[{self.symbol}] 决策被覆盖: {decision} -> {new_decision}, 原因: {reason}"
            )
            decision = new_decision
            details["override_reason"] = reason
            details["original_decision"] = decision

        # 执行决策验证（针对开仓决策）
        if decision in ["BUY", "SELL_SHORT"] and df is not None:
            from src.data.indicators import TechnicalIndicators

            indicators = TechnicalIndicators.get_latest_indicators(df)
            current_price = market_data.get("current_price", 0)

            validation = self.validate_decision(
                decision=decision,
                current_price=current_price,
                indicators=indicators,
                multi_timeframe_trends=multi_timeframe_trends,
                df=df,
            )

            details["validation"] = {
                "is_valid": validation.is_valid,
                "score": validation.overall_score,
                "blockers": validation.blockers,
                "warnings": validation.warnings,
                "suggestions": validation.suggestions,
                "size_multiplier": validation.suggested_size_multiplier,
                "wait_for_pullback": validation.wait_for_pullback,
            }

            # 如果验证不通过，覆盖决策
            if not validation.is_valid:
                self.logger.print_warning(
                    f"[{self.symbol}] 决策验证未通过: {decision} -> DO_NOTHING"
                )
                self.logger.print_warning(
                    f"[{self.symbol}] 阻止原因: {', '.join(validation.blockers)}"
                )
                details["validation_override"] = True
                details["original_decision_before_validation"] = decision
                decision = "DO_NOTHING"
            else:
                # 验证通过，但可能需要调整仓位
                if validation.suggested_size_multiplier < 1.0:
                    self.logger.print_info(
                        f"[{self.symbol}] 建议仓位调整: {validation.suggested_size_multiplier:.0%}"
                    )
                    details["adjusted_size_multiplier"] = validation.suggested_size_multiplier

                # 如果建议等待回调
                if validation.wait_for_pullback and validation.suggested_entry_price:
                    self.logger.print_info(
                        f"[{self.symbol}] 建议等待回调至 ${validation.suggested_entry_price:.2f}"
                    )
                    details["suggested_entry_price"] = validation.suggested_entry_price

        # 添加增强分析信息到详情
        if self._last_enhanced_decision:
            details["enhanced_decision"] = {
                "action": self._last_enhanced_decision.action,
                "should_trade": self._last_enhanced_decision.should_trade,
                "confidence": self._last_enhanced_decision.overall_confidence,
                "quality": self._last_enhanced_decision.decision_quality,
                "signal_type": self._last_enhanced_decision.trading_signal.signal_type.value,
                "market_state": self._last_enhanced_decision.market_analysis.state.value,
                "risk_level": self._last_enhanced_decision.risk_assessment.risk_level.name,
                "blockers": self._last_enhanced_decision.blockers,
                "warnings": self._last_enhanced_decision.warnings,
            }

        return decision, details


def create_enhanced_agent(
    symbol: str,
    order_manager: OrderManager,
    logger: TradingLogger,
    llm_manager: LLMClientManager,
    config: dict[str, Any],
    notifier=None,
    prompt_manager=None,
    fee_rates=None,
) -> EnhancedSingleSymbolAgent:
    """
    从配置创建增强型 Agent

    Args:
        symbol: 交易对符号
        order_manager: 订单管理器
        logger: 日志记录器
        llm_manager: LLM客户端管理器
        config: 配置字典
        notifier: 通知器
        prompt_manager: Prompt管理器
        fee_rates: 费率

    Returns:
        EnhancedSingleSymbolAgent 实例
    """
    # 基础配置
    temperature = config.get("agent_temperature", 0.1)
    max_iterations = config.get("agent_max_iterations", 5)
    trade_amount = config.get("max_trade_amount", 100.0)
    max_leverage = config.get("max_leverage", 10)
    take_profit_ratio = config.get("take_profit_ratio", 0.05)
    stop_loss_ratio = config.get("stop_loss_ratio", 0.02)
    limit_order_enabled = config.get("limit_order_enabled", False)

    # 增强配置
    enhanced_config = config.get("enhanced_analysis", {})
    enable_enhanced = enhanced_config.get("enabled", True)
    min_signal_quality = enhanced_config.get("min_signal_quality", "fair")
    min_confidence = enhanced_config.get("min_confidence", 0.4)
    enable_risk_filter = enhanced_config.get("enable_risk_filter", True)
    enable_timing_filter = enhanced_config.get("enable_timing_filter", True)

    # 风险配置
    risk_config = enhanced_config.get("risk", {})
    risk_params = RiskParameters(
        max_risk_per_trade=risk_config.get("max_risk_per_trade", 0.02),
        max_total_exposure=risk_config.get("max_total_exposure", 0.5),
        default_stop_loss_pct=stop_loss_ratio,
        default_take_profit_pct=take_profit_ratio,
        atr_stop_loss_multiplier=risk_config.get("atr_sl_multiplier", 1.5),
        atr_take_profit_multiplier=risk_config.get("atr_tp_multiplier", 3.0),
        trailing_stop_enabled=risk_config.get("trailing_stop_enabled", True),
        volatility_adjustment_enabled=risk_config.get("volatility_adjustment", True),
    )

    return EnhancedSingleSymbolAgent(
        symbol=symbol,
        order_manager=order_manager,
        logger=logger,
        llm_manager=llm_manager,
        temperature=temperature,
        max_iterations=max_iterations,
        trade_amount=trade_amount,
        max_leverage=max_leverage,
        take_profit_ratio=take_profit_ratio,
        stop_loss_ratio=stop_loss_ratio,
        notifier=notifier,
        prompt_manager=prompt_manager,
        fee_rates=fee_rates,
        limit_order_enabled=limit_order_enabled,
        enable_enhanced_analysis=enable_enhanced,
        min_signal_quality=min_signal_quality,
        min_confidence=min_confidence,
        enable_risk_filter=enable_risk_filter,
        enable_timing_filter=enable_timing_filter,
        risk_params=risk_params,
    )
