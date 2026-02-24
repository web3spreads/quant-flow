"""
基于 QLib 信号的交易策略

将 QLib 模型预测信号转化为具体的交易决策，
并与现有的风控模块集成。
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime

from ..model.predictor import SignalDirection, TradingSignal

logger = logging.getLogger("QuantFlow.QLib")


@dataclass
class TradeDecision:
    """
    交易决策数据结构

    QLib 信号驱动的交易决策，包含方向、仓位、止盈止损等信息。
    与现有 OrderManager 兼容。
    """

    symbol: str
    action: str  # buy/sell/sell_short/buy_to_cover/hold
    should_trade: bool  # 是否应该执行交易
    direction: str  # long/short/neutral
    signal_strength: float  # 信号强度 [0, 1]
    confidence: float  # 置信度 [0, 1]
    suggested_size_pct: float  # 建议仓位比例 [0, 1]
    stop_loss_pct: float  # 建议止损百分比
    take_profit_pct: float  # 建议止盈百分比
    reasoning: list[str] = field(default_factory=list)  # 决策理由
    warnings: list[str] = field(default_factory=list)  # 警告信息
    blockers: list[str] = field(default_factory=list)  # 阻止原因
    timestamp: str = ""  # 决策时间
    model_type: str = ""  # 使用的模型

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "symbol": self.symbol,
            "action": self.action,
            "should_trade": self.should_trade,
            "direction": self.direction,
            "signal_strength": self.signal_strength,
            "confidence": self.confidence,
            "suggested_size_pct": self.suggested_size_pct,
            "stop_loss_pct": self.stop_loss_pct,
            "take_profit_pct": self.take_profit_pct,
            "reasoning": self.reasoning,
            "warnings": self.warnings,
            "blockers": self.blockers,
            "model_type": self.model_type,
        }


class QLibSignalStrategy:
    """
    基于 QLib 模型信号的交易策略

    核心逻辑：
    1. 接收 QLib 模型预测信号
    2. 结合市场上下文（当前持仓、账户余额等）
    3. 应用风控规则
    4. 生成最终交易决策
    """

    def __init__(
        self,
        signal_threshold: float = 0.3,
        strong_signal_threshold: float = 0.7,
        max_position_pct: float = 0.3,
        default_stop_loss_pct: float = 0.02,
        default_take_profit_pct: float = 0.06,
        min_confidence: float = 0.3,
    ):
        """
        初始化策略

        Args:
            signal_threshold: 最低信号阈值
            strong_signal_threshold: 强信号阈值
            max_position_pct: 最大单笔仓位占比
            default_stop_loss_pct: 默认止损百分比
            default_take_profit_pct: 默认止盈百分比
            min_confidence: 最低置信度要求
        """
        self.signal_threshold = signal_threshold
        self.strong_signal_threshold = strong_signal_threshold
        self.max_position_pct = max_position_pct
        self.default_stop_loss_pct = default_stop_loss_pct
        self.default_take_profit_pct = default_take_profit_pct
        self.min_confidence = min_confidence

    def generate_decision(
        self,
        signal: TradingSignal,
        current_position: dict | None = None,
        account_balance: float = 0,
        market_context: dict | None = None,
    ) -> TradeDecision:
        """
        根据 QLib 信号生成交易决策

        Args:
            signal: QLib 模型交易信号
            current_position: 当前持仓信息
                {
                    "side": "long"/"short"/None,
                    "size": float,
                    "entry_price": float,
                    "unrealized_pnl": float,
                }
            account_balance: 账户余额
            market_context: 市场上下文信息（可选）

        Returns:
            交易决策
        """
        reasoning = []
        warnings = []
        blockers = []

        # --- 信号分析 ---
        reasoning.append(
            f"QLib 模型信号: {signal.direction.value}, "
            f"强度={signal.strength:.3f}, 置信度={signal.confidence:.3f}"
        )

        # --- 前置检查 ---

        # 检查信号是否可执行
        if not signal.is_actionable:
            return self._hold_decision(signal, reasoning=["信号强度不足，保持观望"])

        # 检查置信度
        if signal.confidence < self.min_confidence:
            warnings.append(f"置信度较低: {signal.confidence:.3f} < {self.min_confidence}")
            if signal.confidence < self.min_confidence * 0.5:
                return self._hold_decision(
                    signal,
                    reasoning=[f"置信度过低 ({signal.confidence:.3f})，不执行交易"],
                )

        # --- 确定交易动作 ---
        has_position = current_position is not None and current_position.get("size", 0) > 0
        position_side = current_position.get("side") if has_position else None

        action, direction = self._determine_action(signal, has_position, position_side, reasoning)

        if action == "hold":
            return self._hold_decision(signal, reasoning=reasoning)

        # --- 计算仓位大小 ---
        size_pct = self._calculate_position_size(signal, account_balance, market_context)
        reasoning.append(f"建议仓位: {size_pct:.1%}")

        # --- 计算止盈止损 ---
        stop_loss_pct, take_profit_pct = self._calculate_stop_levels(signal, market_context)
        reasoning.append(
            f"止损: {stop_loss_pct:.2%}, 止盈: {take_profit_pct:.2%}, "
            f"风险回报比: {take_profit_pct / (stop_loss_pct + 1e-12):.1f}"
        )

        return TradeDecision(
            symbol=signal.symbol,
            action=action,
            should_trade=len(blockers) == 0,
            direction=direction,
            signal_strength=signal.strength,
            confidence=signal.confidence,
            suggested_size_pct=size_pct,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            reasoning=reasoning,
            warnings=warnings,
            blockers=blockers,
            model_type=signal.model_type,
        )

    def _determine_action(
        self,
        signal: TradingSignal,
        has_position: bool,
        position_side: str | None,
        reasoning: list[str],
    ) -> tuple[str, str]:
        """
        确定交易动作

        Returns:
            (action, direction) 元组
        """
        if signal.is_long:
            if has_position and position_side == "short":
                reasoning.append("信号做多，当前持有空仓 → 平空仓")
                return "buy_to_cover", "long"
            elif has_position and position_side == "long":
                reasoning.append("信号做多，当前已持有多仓 → 保持")
                return "hold", "long"
            else:
                reasoning.append("信号做多，无持仓 → 开多仓")
                return "buy", "long"

        elif signal.is_short:
            if has_position and position_side == "long":
                reasoning.append("信号做空，当前持有多仓 → 平多仓")
                return "sell", "short"
            elif has_position and position_side == "short":
                reasoning.append("信号做空，当前已持有空仓 → 保持")
                return "hold", "short"
            else:
                reasoning.append("信号做空，无持仓 → 开空仓")
                return "sell_short", "short"

        else:
            reasoning.append("信号中性 → 保持")
            return "hold", "neutral"

    def _calculate_position_size(
        self,
        signal: TradingSignal,
        account_balance: float,
        market_context: dict | None,
    ) -> float:
        """
        根据信号强度和置信度计算建议仓位大小

        使用简化的凯利公式思想：
        仓位 = 最大仓位 × 信号强度 × 置信度

        Args:
            signal: 交易信号
            account_balance: 账户余额
            market_context: 市场上下文

        Returns:
            建议仓位占比 [0, max_position_pct]
        """
        # 基础仓位 = 最大仓位 × 信号强度
        base_size = self.max_position_pct * signal.strength

        # 置信度调整
        confidence_factor = signal.confidence

        # 强信号加成
        strength_bonus = 1.0
        if signal.direction in (SignalDirection.STRONG_LONG, SignalDirection.STRONG_SHORT):
            strength_bonus = 1.2

        final_size = base_size * confidence_factor * strength_bonus
        return min(final_size, self.max_position_pct)

    def _calculate_stop_levels(
        self,
        signal: TradingSignal,
        market_context: dict | None,
    ) -> tuple[float, float]:
        """
        计算止盈止损水平

        信号越强，止损越紧、止盈越远（更激进）
        信号越弱，止损越宽、止盈越近（更保守）

        Returns:
            (止损百分比, 止盈百分比)
        """
        # 基础止损和止盈
        base_sl = self.default_stop_loss_pct
        base_tp = self.default_take_profit_pct

        # 根据信号强度调整
        if signal.strength >= 0.7:
            # 强信号：止损紧一点，止盈远一点
            stop_loss = base_sl * 0.8
            take_profit = base_tp * 1.5
        elif signal.strength >= 0.5:
            # 中等信号：使用默认值
            stop_loss = base_sl
            take_profit = base_tp
        else:
            # 弱信号：止损宽一点，止盈近一点
            stop_loss = base_sl * 1.3
            take_profit = base_tp * 0.7

        # 确保最小风险回报比 >= 1.5
        min_rr = 1.5
        if take_profit / (stop_loss + 1e-12) < min_rr:
            take_profit = stop_loss * min_rr

        return stop_loss, take_profit

    def _hold_decision(
        self,
        signal: TradingSignal,
        reasoning: list[str] | None = None,
    ) -> TradeDecision:
        """生成保持观望的决策"""
        return TradeDecision(
            symbol=signal.symbol,
            action="hold",
            should_trade=False,
            direction="neutral",
            signal_strength=signal.strength,
            confidence=signal.confidence,
            suggested_size_pct=0,
            stop_loss_pct=0,
            take_profit_pct=0,
            reasoning=reasoning or ["保持观望"],
            model_type=signal.model_type,
        )
