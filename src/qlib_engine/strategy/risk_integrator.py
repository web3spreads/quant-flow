"""
风控集成器

将 QLib 信号策略与现有的风控模块（DecisionValidator、PositionSizer、RiskManager）对接。
确保 QLib 驱动的交易决策通过完整的风控流程。
"""

import logging

from .signal_strategy import TradeDecision

logger = logging.getLogger("QuantFlow.QLib")


class RiskIntegrator:
    """
    风控集成器

    将 QLib 信号策略生成的交易决策与现有风控模块集成：
    - DecisionValidator: 多维度验证
    - PositionSizer: 凯利公式动态仓位
    - RiskManager: ATR 止盈止损
    - AccountProtector: 账户保护

    集成方式：
    QLib 决策先经过信号策略生成初步决策，
    再由本模块调用现有风控模块进行二次验证和调整。
    """

    def __init__(
        self,
        decision_validator=None,
        position_sizer=None,
        risk_manager=None,
        account_protector=None,
        qlib_weight: float = 0.7,
    ):
        """
        初始化风控集成器

        Args:
            decision_validator: 决策验证器（可选，来自 src.trading.decision_validator）
            position_sizer: 仓位计算器（可选，来自 src.trading.position_sizer）
            risk_manager: 风险管理器（可选，来自 src.trading.risk_manager）
            account_protector: 账户保护器（可选，来自 src.trading.account_protector）
            qlib_weight: QLib 信号权重（0-1），剩余权重给传统风控
        """
        self.decision_validator = decision_validator
        self.position_sizer = position_sizer
        self.risk_manager = risk_manager
        self.account_protector = account_protector
        self.qlib_weight = qlib_weight

    def apply_risk_controls(
        self,
        decision: TradeDecision,
        market_data: dict | None = None,
        account_info: dict | None = None,
    ) -> TradeDecision:
        """
        对 QLib 决策应用风控规则

        Args:
            decision: QLib 策略生成的初步交易决策
            market_data: 市场数据（用于风控计算）
                {
                    "current_price": float,
                    "atr": float,
                    "volatility": float,
                    "df": DataFrame,  # 原始 K 线数据
                }
            account_info: 账户信息
                {
                    "balance": float,
                    "equity": float,
                    "positions": list[dict],
                    "daily_pnl": float,
                }

        Returns:
            经过风控调整后的交易决策
        """
        if not decision.should_trade:
            return decision

        # 1. 账户保护检查
        if self.account_protector and account_info:
            protection_result = self._check_account_protection(decision, account_info)
            if protection_result:
                return protection_result

        # 2. 决策验证（多维度验证）
        if self.decision_validator and market_data:
            validation_result = self._validate_decision(decision, market_data)
            if validation_result:
                decision.warnings.extend(validation_result.get("warnings", []))
                if validation_result.get("blocked"):
                    decision.should_trade = False
                    decision.blockers.extend(validation_result.get("blockers", []))
                    return decision

        # 3. 仓位调整
        if self.position_sizer and account_info:
            adjusted_size = self._adjust_position_size(
                decision, account_info, market_data
            )
            if adjusted_size is not None:
                decision.reasoning.append(
                    f"风控仓位调整: {decision.suggested_size_pct:.1%} → {adjusted_size:.1%}"
                )
                decision.suggested_size_pct = adjusted_size

        # 4. 止盈止损调整
        if self.risk_manager and market_data:
            adjusted_sl, adjusted_tp = self._adjust_stop_levels(
                decision, market_data
            )
            if adjusted_sl is not None:
                decision.reasoning.append(
                    f"风控止损调整: {decision.stop_loss_pct:.2%} → {adjusted_sl:.2%}"
                )
                decision.stop_loss_pct = adjusted_sl
            if adjusted_tp is not None:
                decision.reasoning.append(
                    f"风控止盈调整: {decision.take_profit_pct:.2%} → {adjusted_tp:.2%}"
                )
                decision.take_profit_pct = adjusted_tp

        return decision

    def _check_account_protection(
        self,
        decision: TradeDecision,
        account_info: dict,
    ) -> TradeDecision | None:
        """
        检查账户保护规则

        Returns:
            如果需要阻止交易，返回修改后的决策；否则返回 None
        """
        try:
            # 检查账户保护器的状态
            if hasattr(self.account_protector, "check_protection"):
                protection = self.account_protector.check_protection(
                    equity=account_info.get("equity", 0),
                    initial_equity=account_info.get("balance", 0),
                    daily_pnl=account_info.get("daily_pnl", 0),
                    positions=account_info.get("positions", []),
                )
                # 如果保护器建议暂停交易
                if protection and protection.get("action") in (
                    "PAUSE_NEW_TRADES", "CLOSE_ALL_POSITIONS"
                ):
                    decision.should_trade = False
                    decision.blockers.append(
                        f"账户保护触发: {protection.get('reason', '未知原因')}"
                    )
                    return decision
        except Exception as e:
            logger.warning(f"账户保护检查失败: {e}")

        return None

    def _validate_decision(
        self,
        decision: TradeDecision,
        market_data: dict,
    ) -> dict | None:
        """
        调用决策验证器进行多维度验证

        Returns:
            验证结果字典 {"warnings": [], "blockers": [], "blocked": bool}
        """
        try:
            if hasattr(self.decision_validator, "validate"):
                result = self.decision_validator.validate(
                    action=decision.action,
                    signal_strength=decision.signal_strength,
                    market_data=market_data,
                )
                return result
        except Exception as e:
            logger.warning(f"决策验证失败: {e}")

        return None

    def _adjust_position_size(
        self,
        decision: TradeDecision,
        account_info: dict,
        market_data: dict | None,
    ) -> float | None:
        """
        使用现有 PositionSizer 调整仓位

        采用加权融合：
        最终仓位 = QLib建议 × qlib_weight + 风控建议 × (1 - qlib_weight)

        Returns:
            调整后的仓位比例
        """
        try:
            if hasattr(self.position_sizer, "calculate_position_size"):
                risk_size = self.position_sizer.calculate_position_size(
                    balance=account_info.get("balance", 0),
                    signal_strength=decision.signal_strength,
                    market_data=market_data,
                )
                if risk_size is not None:
                    # 加权融合
                    blended = (
                        decision.suggested_size_pct * self.qlib_weight
                        + risk_size * (1 - self.qlib_weight)
                    )
                    return blended
        except Exception as e:
            logger.warning(f"仓位调整失败: {e}")

        return None

    def _adjust_stop_levels(
        self,
        decision: TradeDecision,
        market_data: dict,
    ) -> tuple[float | None, float | None]:
        """
        使用现有 RiskManager 调整止盈止损

        Returns:
            (调整后的止损比例, 调整后的止盈比例)
        """
        try:
            if hasattr(self.risk_manager, "calculate_stop_levels"):
                levels = self.risk_manager.calculate_stop_levels(
                    current_price=market_data.get("current_price", 0),
                    atr=market_data.get("atr", 0),
                    direction=decision.direction,
                )
                if levels:
                    return levels.get("stop_loss_pct"), levels.get("take_profit_pct")
        except Exception as e:
            logger.warning(f"止盈止损调整失败: {e}")

        return None, None
