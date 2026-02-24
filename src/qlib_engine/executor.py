"""
QLib 决策执行器

桥接 QLib 的 TradeDecision 对象和现有的 OrderManager 执行层。
将 QLib 量化决策转换为实际的交易操作。
"""

from __future__ import annotations

import threading
from typing import Any

from src.qlib_engine.strategy.signal_strategy import TradeDecision
from src.trading.order_manager import OrderManager
from src.utils.logger import get_logger


class QLibDecisionExecutor:
    """QLib 决策执行器 — 将 TradeDecision 映射为 OrderManager 操作"""

    # QLib action → OrderManager 方法映射
    _ACTION_MAP = {
        "buy": "execute_long",
        "sell": "close_position",
        "sell_short": "execute_short",
        "buy_to_cover": "close_position",
        "hold": None,
    }

    def __init__(
        self,
        order_manager: OrderManager,
        max_trade_amount: float = 100.0,
        max_leverage: int = 10,
        min_trade_amount: float = 10.0,
        trading_lock: threading.Lock | None = None,
    ):
        """
        初始化 QLib 决策执行器

        Args:
            order_manager: 订单管理器实例
            max_trade_amount: 单笔最大交易金额（USD）
            max_leverage: 最大杠杆倍数
            min_trade_amount: 最小交易金额（USD）
            trading_lock: 交易锁（用于线程安全地临时覆盖 TP/SL）
        """
        self.order_manager = order_manager
        self.max_trade_amount = max_trade_amount
        self.max_leverage = max_leverage
        self.min_trade_amount = min_trade_amount
        self._trading_lock = trading_lock
        self.logger = get_logger()

    def execute(
        self,
        decision: TradeDecision,
        account_balance: float = 0,
        leverage: int | None = None,
    ) -> dict[str, Any]:
        """
        执行 QLib 交易决策

        Args:
            decision: QLib 生成的 TradeDecision 对象
            account_balance: 当前可用余额（用于计算实际金额）
            leverage: 杠杆倍数（None 使用默认值）

        Returns:
            兼容 decision_history 格式的结果字典
        """
        result = {
            "source": "qlib",
            "symbol": decision.symbol,
            "action": decision.action,
            "direction": decision.direction,
            "signal_strength": decision.signal_strength,
            "confidence": decision.confidence,
            "reasoning": decision.reasoning,
            "warnings": decision.warnings,
            "blockers": decision.blockers,
            "model_type": decision.model_type,
            "timestamp": decision.timestamp,
            "executed": False,
            "order_result": None,
            "output": "",
        }

        # hold 或不应交易的决策直接返回
        if decision.action == "hold" or not decision.should_trade:
            reason = "；".join(decision.reasoning) if decision.reasoning else "QLib 建议持有/观望"
            result["output"] = reason
            return result

        # 检查是否有阻止原因
        if decision.blockers:
            reason = "QLib 决策被阻止: " + "；".join(decision.blockers)
            result["output"] = reason
            return result

        # 计算实际交易金额
        usdt_amount = self._calculate_trade_amount(decision, account_balance)
        if usdt_amount < self.min_trade_amount:
            result["output"] = (
                f"计算金额 ${usdt_amount:.2f} 低于最小交易金额 ${self.min_trade_amount}"
            )
            return result

        # 确定杠杆
        lev = min(leverage or self.order_manager.default_leverage, self.max_leverage)

        # 执行交易
        order_result = self._execute_action(decision, usdt_amount, lev)
        if order_result:
            result["executed"] = True
            result["order_result"] = order_result
            result["output"] = (
                f"QLib {decision.action} {decision.symbol} "
                f"${usdt_amount:.2f} @ {lev}x 杠杆, "
                f"信号强度={decision.signal_strength:.2f}, "
                f"置信度={decision.confidence:.2f}"
            )
        else:
            result["output"] = f"QLib 决策执行失败: {decision.action} {decision.symbol}"

        return result

    def _calculate_trade_amount(
        self,
        decision: TradeDecision,
        account_balance: float,
    ) -> float:
        """
        将 suggested_size_pct 转换为实际 USD 金额

        Args:
            decision: 交易决策
            account_balance: 可用余额

        Returns:
            实际交易金额（USD）
        """
        if account_balance <= 0:
            return 0.0

        # 基于百分比计算
        raw_amount = account_balance * decision.suggested_size_pct

        # 上下限裁剪
        amount = max(self.min_trade_amount, min(raw_amount, self.max_trade_amount))
        return round(amount, 2)

    def _execute_action(
        self,
        decision: TradeDecision,
        usdt_amount: float,
        leverage: int,
    ) -> dict[str, Any] | None:
        """
        根据 action 类型调用 OrderManager 对应方法

        Args:
            decision: 交易决策
            usdt_amount: 交易金额
            leverage: 杠杆倍数

        Returns:
            订单执行结果
        """
        action = decision.action
        symbol = decision.symbol

        # 临时覆盖 OrderManager 的 TP/SL 比例
        original_tp = self.order_manager.take_profit_ratio
        original_sl = self.order_manager.stop_loss_ratio

        try:
            # 使用 QLib 决策中的止盈止损比例
            if decision.take_profit_pct > 0:
                self.order_manager.take_profit_ratio = decision.take_profit_pct
            if decision.stop_loss_pct > 0:
                self.order_manager.stop_loss_ratio = decision.stop_loss_pct

            if action == "buy":
                return self.order_manager.execute_long(
                    symbol=symbol,
                    usdt_amount=usdt_amount,
                    leverage=leverage,
                )
            elif action == "sell_short":
                return self.order_manager.execute_short(
                    symbol=symbol,
                    usdt_amount=usdt_amount,
                    leverage=leverage,
                )
            elif action in ("sell", "buy_to_cover"):
                return self.order_manager.close_position(symbol=symbol)
            else:
                self.logger.print_warning(f"未知的 QLib action: {action}")
                return None
        finally:
            # 恢复原始 TP/SL 比例
            self.order_manager.take_profit_ratio = original_tp
            self.order_manager.stop_loss_ratio = original_sl

    def decision_to_history_format(
        self,
        decision: TradeDecision,
        exec_result: dict[str, Any],
    ) -> dict[str, Any]:
        """
        将执行结果转换为 decision_history 兼容格式

        Args:
            decision: QLib 交易决策
            exec_result: execute() 的返回值

        Returns:
            兼容 DecisionHistory.add_decision() 的字典
        """
        # 映射 action 到 decision_history 的决策字符串
        action_to_decision = {
            "buy": "OPEN_LONG",
            "sell": "CLOSE_LONG",
            "sell_short": "OPEN_SHORT",
            "buy_to_cover": "CLOSE_SHORT",
            "hold": "HOLD",
        }

        decision_str = action_to_decision.get(decision.action, "HOLD")

        return {
            "decision": decision_str,
            "reason": exec_result.get("output", ""),
            "action_details": exec_result,
        }
