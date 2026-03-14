"""
单币种交易 Agent 模块
为每个交易对维护独立的上下文窗口和决策历史
"""

import time
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent

from src.agent.execution_agent import ExecutionAgent
from src.agent.prompts import SYSTEM_PROMPT
from src.agent.tools import TradingTools
from src.agents.common.utils.helpers import send_error_notification
from src.config import FEE_RATE_PER_SIDE, MAKER_FEE_RATE_PER_SIDE
from src.fees import FeeRates
from src.llm import LLMClientManager
from src.prompt_manager import PromptManager
from src.trading.order_manager import OrderManager
from src.utils.logger import TradingLogger


def safe_float(value: Any, default: float = 0.0) -> float:
    """
    安全地将值转换为float，如果转换失败则返回默认值

    Args:
        value: 要转换的值
        default: 转换失败时的默认值

    Returns:
        转换后的float值或默认值
    """
    try:
        if value is None:
            return default
        return float(value)
    except (ValueError, TypeError):
        return default


def safe_leverage(leverage_data: Any, default: int = 1) -> int:
    """
    安全地从leverage数据中提取杠杆倍数

    Hyperliquid API返回的leverage字段格式:
    {
        "type": "cross" | "isolated",
        "value": 10
    }

    Args:
        leverage_data: leverage数据，可能是字典、数字或None
        default: 提取失败时的默认值

    Returns:
        杠杆倍数（整数）
    """
    try:
        if leverage_data is None:
            return default

        # 如果是字典，尝试提取 value 字段
        if isinstance(leverage_data, dict):
            value = leverage_data.get("value", default)
            return int(value)

        # 如果直接是数字，转换为整数
        return int(leverage_data)
    except (ValueError, TypeError, KeyError):
        return default


class SingleSymbolAgent:
    """单币种交易 Agent - 为每个交易对维护独立上下文"""

    MIN_PROFIT_TO_FEE_RATIO = 4.0

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
    ):
        """
        初始化单币种交易 Agent

        Args:
            symbol: 交易对
            order_manager: 订单管理器
            logger: 日志记录器
            llm_manager: LLM 客户端管理器
            temperature: 温度参数
            max_iterations: 最大迭代次数
            trade_amount: 单笔交易金额上限
            max_leverage: 最大杠杆倍数
            take_profit_ratio: 止盈比例
            stop_loss_ratio: 止损比例
            notifier: 通知管理器（可选）
            prompt_manager: Prompt管理器（可选）
        """
        self.symbol = symbol
        self.order_manager = order_manager
        self.logger = logger
        self.llm_manager = llm_manager
        self.trade_amount = trade_amount
        self.max_leverage = max_leverage
        self.take_profit_ratio = take_profit_ratio
        self.stop_loss_ratio = stop_loss_ratio
        self.limit_order_enabled = limit_order_enabled
        self.current_price = 0.0
        self.max_iterations = max_iterations
        self.notifier = notifier
        self.prompt_manager = prompt_manager
        self.fee_rates = fee_rates or FeeRates(
            maker_rate=MAKER_FEE_RATE_PER_SIDE, taker_rate=FEE_RATE_PER_SIDE
        )

        # 用于去重：记录本次决策周期中已执行的工具调用
        self._executed_callbacks = set()

        # 初始化 LLM（从管理器获取）
        self.llm = self.llm_manager.get_client(temperature=temperature)

        # 初始化执行 Agent（用于解析决策文本并执行）
        self.execution_agent = ExecutionAgent(
            llm_manager=llm_manager,
            temperature=0.0,  # 执行 Agent 使用零温度确保确定性
        )

        # 创建工具
        self.tools = self._create_tools()

        # 保存工具回调以供执行 Agent 使用
        self.tools_callbacks = self._get_tool_callbacks()

        self.agent_executor = create_react_agent(model=self.llm, tools=self.tools)

        # 设置系统提示词
        if self.prompt_manager:
            system_prompt_text = self.prompt_manager.get_system_prompt()
        else:
            system_prompt_text = SYSTEM_PROMPT
        self.system_message = SystemMessage(content=system_prompt_text)

    def _create_tools(self) -> list:
        """创建工具集"""

        def buy_callback(
            symbol: str, amount: float | None = None, leverage: int | None = None
        ) -> str:
            """买入开多回调"""
            try:
                # 检查是否允许开新仓（trade_amount > 0 表示允许）
                if self.trade_amount <= 0:
                    return "❌ 当前余额不足，无法开新仓。请专注于管理现有持仓（止盈/止损）。"

                fee_guard_msg = self._check_fee_guard()
                if fee_guard_msg:
                    return fee_guard_msg

                # 使用 AI 指定的金额和杠杆，如果没有指定则使用默认上限
                actual_amount = amount if amount is not None else self.trade_amount
                actual_leverage = leverage if leverage is not None else self.max_leverage

                # 验证参数
                if actual_amount > self.trade_amount:
                    return f"❌ 交易金额 ${actual_amount} 超过上限 ${self.trade_amount}"
                if actual_leverage > self.max_leverage:
                    return f"❌ 杠杆倍数 {actual_leverage}x 超过上限 {self.max_leverage}x"

                self.logger.print_info(
                    f"[{self.symbol}Agent] 执行买入开多 (金额: ${actual_amount}, 杠杆: {actual_leverage}x)"
                )

                if not self.order_manager.check_sufficient_balance(actual_amount):
                    return f"❌ 余额不足，需要 {actual_amount} USDT"

                # 允许多头仓位的加仓，不再检查是否已有多头仓位

                result = self.order_manager.execute_long(
                    symbol=self.symbol,
                    usdt_amount=actual_amount,
                    leverage=actual_leverage,
                    with_tpsl=True,
                )

                if result and result.get("success"):
                    # 获取市场订单信息
                    result.get("market_order", {})
                    entry_price = self.current_price
                    # 使用配置的止盈止损比例（与实际交易一致）
                    tp_price = entry_price * (1 + self.take_profit_ratio)
                    sl_price = entry_price * (1 - self.stop_loss_ratio)
                    quantity = result.get("quantity", 0)
                    leverage_used = actual_leverage

                    # 发送开仓通知
                    if self.notifier:
                        self.notifier.notify_trade_opened(
                            symbol=self.symbol,
                            side="long",
                            quantity=quantity,
                            price=entry_price,
                            leverage=leverage_used,
                            stop_loss=sl_price,
                            take_profit=tp_price,
                            position_value=quantity * entry_price,
                            margin=actual_amount,
                            reason=f"AI 策略分析，多头信号确认 (金额: ${actual_amount}, 杠杆: {actual_leverage}x)",
                            order_hash=result.get("hash", ""),
                        )

                    return (
                        f"✅ 买入开多成功！\n"
                        f"  金额: ${actual_amount} USD\n"
                        f"  杠杆: {actual_leverage}x\n"
                        f"  入场价: ${entry_price:.2f}\n"
                        f"  止盈价: ${tp_price:.2f}\n"
                        f"  止损价: ${sl_price:.2f}"
                    )

                # 开仓失败 - 记录详细信息并发送通知
                error_msg = "❌ 买入开多失败"
                error_details = f"API 返回: {result}"
                self.logger.print_error(f"[{self.symbol}Agent] {error_msg}")
                self.logger.print_error(f"[{self.symbol}Agent] {error_details}")
                self.logger.print_error(
                    f"[{self.symbol}Agent] 交易参数: 金额=${actual_amount}, 杠杆={actual_leverage}x, 价格=${self.current_price}"
                )

                # 发送即时错误通知
                if self.notifier:
                    context = (
                        f"交易对: {self.symbol}\n"
                        f"交易金额: ${actual_amount}\n"
                        f"杠杆倍数: {actual_leverage}x\n"
                        f"当前价: ${self.current_price}\n"
                        f"API响应: {result}"
                    )
                    self.notifier.notify_error(
                        title=f"{self.symbol} 买入开多失败",
                        error_message=error_details,
                        context=context,
                    )

                return f"{error_msg}\n详情: {error_details}"

            except Exception as e:
                import traceback

                error_msg = f"❌ 买入开多异常: {str(e)}"
                stack_trace = traceback.format_exc()

                # 记录完整的异常信息
                self.logger.print_error(f"[{self.symbol}Agent] {error_msg}")
                self.logger.print_error(f"[{self.symbol}Agent] 堆栈跟踪:\n{stack_trace}")
                self.logger.logger.exception(e)

                # 发送即时错误通知
                if self.notifier:
                    context = (
                        f"交易对: {self.symbol}\n"
                        f"当前价: ${self.current_price}\n"
                        f"异常类型: {type(e).__name__}\n"
                        f"堆栈跟踪: {stack_trace[:500]}"
                    )
                    self.notifier.notify_error(
                        title=f"{self.symbol} 买入开多异常", error_message=str(e), context=context
                    )

                return error_msg

        def sell_callback(symbol: str) -> str:
            """卖出平多回调"""
            try:
                self.logger.print_info(f"[{self.symbol}Agent] 执行卖出平多")

                positions = self.order_manager.get_current_positions()
                # 查找多头仓位（szi > 0 表示多头）
                position = next(
                    (
                        p
                        for p in positions
                        if p.get("coin") == self.symbol and safe_float(p.get("szi", 0)) > 0
                    ),
                    None,
                )

                if not position:
                    return f"❌ 未持有 {self.symbol} 的多头仓位"

                # 记录持仓详情以便调试
                self.logger.print_info(
                    f"[{self.symbol}Agent] 持仓详情: 币种={position.get('coin')}, 大小={position.get('szi')}, 入场价={position.get('entryPx')}"
                )

                result = self.order_manager.close_position(
                    symbol=self.symbol,
                    size=None,  # Close entire position
                )

                if result and result.get("status") == "ok":
                    # 发送平仓通知
                    if self.notifier:
                        entry_price = float(position.get("entryPx", 0))
                        # 优先使用实际成交价，回退到当前市场价
                        exit_price = result.get("fill_price", self.current_price)
                        size = abs(float(position.get("szi", 0)))
                        # 根据开仓价、平仓价和数量计算实际盈亏金额
                        pnl = (exit_price - entry_price) * size
                        leverage = safe_leverage(position.get("leverage"), 1)
                        pnl_percent = (
                            (exit_price - entry_price) / entry_price * leverage * 100
                            if entry_price > 0
                            else 0
                        )

                        self.notifier.notify_trade_closed(
                            symbol=self.symbol,
                            side="long",
                            quantity=size,
                            entry_price=entry_price,
                            exit_price=exit_price,
                            pnl=pnl,
                            pnl_percent=pnl_percent,
                            order_hash=result.get("hash", ""),
                        )

                    return "✅ 卖出平多成功！"

                # 平仓失败 - 记录详细信息并发送通知
                error_msg = "❌ 卖出平多失败"
                error_details = f"API 返回: {result}"
                self.logger.print_error(f"[{self.symbol}Agent] {error_msg}")
                self.logger.print_error(f"[{self.symbol}Agent] {error_details}")
                self.logger.print_error(f"[{self.symbol}Agent] 持仓信息: {position}")
                self.logger.print_error(f"[{self.symbol}Agent] 当前价格: ${self.current_price}")

                # 发送即时错误通知
                if self.notifier:
                    context = (
                        f"交易对: {self.symbol}\n"
                        f"持仓大小: {position.get('szi')}\n"
                        f"入场价: ${position.get('entryPx')}\n"
                        f"当前价: ${self.current_price}\n"
                        f"API响应: {result}"
                    )
                    self.notifier.notify_error(
                        title=f"{self.symbol} 卖出平多失败",
                        error_message=error_details,
                        context=context,
                    )

                return f"{error_msg}\n详情: {error_details}"

            except Exception as e:
                import traceback

                error_msg = f"❌ 卖出平多异常: {str(e)}"
                stack_trace = traceback.format_exc()

                # 记录完整的异常信息
                self.logger.print_error(f"[{self.symbol}Agent] {error_msg}")
                self.logger.print_error(f"[{self.symbol}Agent] 堆栈跟踪:\n{stack_trace}")
                self.logger.logger.exception(e)

                # 发送即时错误通知
                if self.notifier:
                    context = (
                        f"交易对: {self.symbol}\n"
                        f"当前价: ${self.current_price}\n"
                        f"异常类型: {type(e).__name__}\n"
                        f"堆栈跟踪: {stack_trace[:500]}"  # 限制长度
                    )
                    self.notifier.notify_error(
                        title=f"{self.symbol} 卖出平多异常", error_message=str(e), context=context
                    )

                return error_msg

        def sell_short_callback(
            symbol: str, amount: float | None = None, leverage: int | None = None
        ) -> str:
            """卖空开空回调"""
            try:
                # 检查是否允许开新仓（trade_amount > 0 表示允许）
                if self.trade_amount <= 0:
                    return "❌ 当前余额不足，无法开新仓。请专注于管理现有持仓（止盈/止损）。"

                fee_guard_msg = self._check_fee_guard()
                if fee_guard_msg:
                    return fee_guard_msg

                # 使用 AI 指定的金额和杠杆，如果没有指定则使用默认上限
                actual_amount = amount if amount is not None else self.trade_amount
                actual_leverage = leverage if leverage is not None else self.max_leverage

                # 验证参数
                if actual_amount > self.trade_amount:
                    return f"❌ 交易金额 ${actual_amount} 超过上限 ${self.trade_amount}"
                if actual_leverage > self.max_leverage:
                    return f"❌ 杠杆倍数 {actual_leverage}x 超过上限 {self.max_leverage}x"

                self.logger.print_info(
                    f"[{self.symbol}Agent] 执行卖空开空 (金额: ${actual_amount}, 杠杆: {actual_leverage}x)"
                )

                if not self.order_manager.check_sufficient_balance(actual_amount):
                    return f"❌ 余额不足，需要 {actual_amount} USDT"

                # 允许空头仓位的加仓，不再检查是否已有空头仓位

                result = self.order_manager.execute_short(
                    symbol=self.symbol,
                    usdt_amount=actual_amount,
                    leverage=actual_leverage,
                    with_tpsl=True,
                )

                if result and result.get("success"):
                    # 获取市场订单信息
                    entry_price = self.current_price
                    # 使用配置的止盈止损比例（做空方向相反）
                    tp_price = entry_price * (1 - self.take_profit_ratio)  # 下跌时止盈
                    sl_price = entry_price * (1 + self.stop_loss_ratio)  # 上涨时止损
                    quantity = result.get("quantity", 0)
                    leverage_used = actual_leverage

                    # 发送开仓通知
                    if self.notifier:
                        self.notifier.notify_trade_opened(
                            symbol=self.symbol,
                            side="short",
                            quantity=quantity,
                            price=entry_price,
                            leverage=leverage_used,
                            stop_loss=sl_price,
                            take_profit=tp_price,
                            position_value=quantity * entry_price,
                            margin=actual_amount,
                            reason=f"AI 策略分析，空头信号确认 (金额: ${actual_amount}, 杠杆: {actual_leverage}x)",
                            order_hash=result.get("hash", ""),
                        )

                    return (
                        f"✅ 卖空开空成功！\n"
                        f"  金额: ${actual_amount} USD\n"
                        f"  杠杆: {actual_leverage}x\n"
                        f"  入场价: ${entry_price:.2f}\n"
                        f"  止盈价: ${tp_price:.2f}\n"
                        f"  止损价: ${sl_price:.2f}"
                    )

                # 开仓失败 - 记录详细信息并发送通知
                error_msg = "❌ 卖空开空失败"
                error_details = f"API 返回: {result}"
                self.logger.print_error(f"[{self.symbol}Agent] {error_msg}")
                self.logger.print_error(f"[{self.symbol}Agent] {error_details}")
                self.logger.print_error(
                    f"[{self.symbol}Agent] 交易参数: 金额=${actual_amount}, 杠杆={actual_leverage}x, 价格=${self.current_price}"
                )

                # 发送即时错误通知
                if self.notifier:
                    context = (
                        f"交易对: {self.symbol}\n"
                        f"交易金额: ${actual_amount}\n"
                        f"杠杆倍数: {actual_leverage}x\n"
                        f"当前价: ${self.current_price}\n"
                        f"API响应: {result}"
                    )
                    self.notifier.notify_error(
                        title=f"{self.symbol} 卖空开空失败",
                        error_message=error_details,
                        context=context,
                    )

                return f"{error_msg}\n详情: {error_details}"

            except Exception as e:
                import traceback

                error_msg = f"❌ 卖空开空异常: {str(e)}"
                stack_trace = traceback.format_exc()

                # 记录完整的异常信息
                self.logger.print_error(f"[{self.symbol}Agent] {error_msg}")
                self.logger.print_error(f"[{self.symbol}Agent] 堆栈跟踪:\n{stack_trace}")
                self.logger.logger.exception(e)

                # 发送即时错误通知
                if self.notifier:
                    context = (
                        f"交易对: {self.symbol}\n"
                        f"当前价: ${self.current_price}\n"
                        f"异常类型: {type(e).__name__}\n"
                        f"堆栈跟踪: {stack_trace[:500]}"
                    )
                    self.notifier.notify_error(
                        title=f"{self.symbol} 卖空开空异常", error_message=str(e), context=context
                    )

                return error_msg

        def buy_to_cover_callback(symbol: str) -> str:
            """买入平空回调"""
            try:
                self.logger.print_info(f"[{self.symbol}Agent] 执行买入平空")

                positions = self.order_manager.get_current_positions()
                # 查找空头仓位（szi < 0 表示空头）
                position = next(
                    (
                        p
                        for p in positions
                        if p.get("coin") == self.symbol and safe_float(p.get("szi", 0)) < 0
                    ),
                    None,
                )

                if not position:
                    return f"❌ 未持有 {self.symbol} 的空头仓位"

                # 记录持仓详情以便调试
                self.logger.print_info(
                    f"[{self.symbol}Agent] 持仓详情: 币种={position.get('coin')}, 大小={position.get('szi')}, 入场价={position.get('entryPx')}"
                )

                result = self.order_manager.close_position(
                    symbol=self.symbol,
                    size=None,  # Close entire position
                )

                if result and result.get("status") == "ok":
                    # 发送平仓通知
                    if self.notifier:
                        entry_price = float(position.get("entryPx", 0))
                        # 优先使用实际成交价，回退到当前市场价
                        exit_price = result.get("fill_price", self.current_price)
                        size = abs(float(position.get("szi", 0)))
                        # 做空盈亏：价格下跌盈利，上涨亏损
                        pnl = (entry_price - exit_price) * size
                        leverage = safe_leverage(position.get("leverage"), 1)
                        pnl_percent = (
                            ((entry_price - exit_price) / entry_price * leverage * 100)
                            if entry_price > 0
                            else 0
                        )

                        self.notifier.notify_trade_closed(
                            symbol=self.symbol,
                            side="short",
                            quantity=size,
                            entry_price=entry_price,
                            exit_price=exit_price,
                            pnl=pnl,
                            pnl_percent=pnl_percent,
                            order_hash=result.get("hash", ""),
                        )

                    return "✅ 买入平空成功！"

                # 平仓失败 - 记录详细信息并发送通知
                error_msg = "❌ 买入平空失败"
                error_details = f"API 返回: {result}"
                self.logger.print_error(f"[{self.symbol}Agent] {error_msg}")
                self.logger.print_error(f"[{self.symbol}Agent] {error_details}")
                self.logger.print_error(f"[{self.symbol}Agent] 持仓信息: {position}")
                self.logger.print_error(f"[{self.symbol}Agent] 当前价格: ${self.current_price}")

                # 发送即时错误通知
                if self.notifier:
                    context = (
                        f"交易对: {self.symbol}\n"
                        f"持仓大小: {position.get('szi')}\n"
                        f"入场价: ${position.get('entryPx')}\n"
                        f"当前价: ${self.current_price}\n"
                        f"API响应: {result}"
                    )
                    self.notifier.notify_error(
                        title=f"{self.symbol} 买入平空失败",
                        error_message=error_details,
                        context=context,
                    )

                return f"{error_msg}\n详情: {error_details}"

            except Exception as e:
                import traceback

                error_msg = f"❌ 买入平空异常: {str(e)}"
                stack_trace = traceback.format_exc()

                # 记录完整的异常信息
                self.logger.print_error(f"[{self.symbol}Agent] {error_msg}")
                self.logger.print_error(f"[{self.symbol}Agent] 堆栈跟踪:\n{stack_trace}")
                self.logger.logger.exception(e)

                # 发送即时错误通知
                if self.notifier:
                    context = (
                        f"交易对: {self.symbol}\n"
                        f"当前价: ${self.current_price}\n"
                        f"异常类型: {type(e).__name__}\n"
                        f"堆栈跟踪: {stack_trace[:500]}"  # 限制长度
                    )
                    self.notifier.notify_error(
                        title=f"{self.symbol} 买入平空异常", error_message=str(e), context=context
                    )

                return error_msg

        def do_nothing_callback(reason: str) -> str:
            """不操作回调"""
            # 去重：避免在同一决策周期中重复执行相同的回调
            callback_key = f"do_nothing:{reason}"
            if callback_key not in self._executed_callbacks:
                self.logger.print_info(f"[{self.symbol}Agent] 不操作 - {reason}")
                self._executed_callbacks.add(callback_key)
            return f"⏸️  确认：不执行操作。原因：{reason}"

        def buy_spot_callback(symbol: str, amount: float | None = None) -> str:
            """现货定投推荐回调（仅推荐，不直接执行）"""
            # 检查是否允许开新仓
            if self.trade_amount <= 0:
                return "❌ 当前余额不足，无法进行现货定投。"

            actual_amount = amount if amount is not None else self.trade_amount
            if actual_amount > self.trade_amount:
                return f"❌ 定投金额 ${actual_amount} 超过上限 ${self.trade_amount}"
            self.logger.print_info(
                f"[{self.symbol}Agent] 推荐现货定投 (建议金额: ${actual_amount})，将交给现货 Agent 评估"
            )
            return f"📝 已推荐 {symbol} 现货定投 (建议金额: ${actual_amount})，等待现货 Agent 评估"

        # 限价单回调（仅在启用时创建）
        buy_limit_callback = None
        sell_short_limit_callback = None
        cancel_limit_order_callback = None

        if self.limit_order_enabled:

            def buy_limit_callback(
                symbol: str,
                amount: float | None = None,
                leverage: int | None = None,
                price: float = 0.0,
            ) -> str:
                """限价开多回调"""
                try:
                    if self.trade_amount <= 0:
                        return "❌ 当前余额不足，无法开新仓。"

                    if price <= 0:
                        return "❌ 限价价格必须大于0"

                    fee_guard_msg = self._check_fee_guard()
                    if fee_guard_msg:
                        return fee_guard_msg

                    actual_amount = amount if amount is not None else self.trade_amount
                    actual_leverage = leverage if leverage is not None else self.max_leverage

                    if actual_amount > self.trade_amount:
                        return f"❌ 交易金额 ${actual_amount} 超过上限 ${self.trade_amount}"
                    if actual_leverage > self.max_leverage:
                        return f"❌ 杠杆倍数 {actual_leverage}x 超过上限 {self.max_leverage}x"

                    self.logger.print_info(
                        f"[{self.symbol}Agent] 执行限价开多 (金额: ${actual_amount}, 杠杆: {actual_leverage}x, 限价: ${price:.2f})"
                    )

                    if not self.order_manager.check_sufficient_balance(actual_amount):
                        return f"❌ 余额不足，需要 {actual_amount} USDT"

                    result = self.order_manager.execute_long_limit(
                        symbol=self.symbol,
                        usdt_amount=actual_amount,
                        limit_price=price,
                        leverage=actual_leverage,
                    )

                    if result and result.get("success"):
                        tp_price = result.get("take_profit_price", 0)
                        sl_price = result.get("stop_loss_price", 0)

                        return (
                            f"✅ 限价开多订单已提交！\n"
                            f"  币种: {symbol}\n"
                            f"  限价: ${price:.2f}\n"
                            f"  投入: ${actual_amount:.2f}\n"
                            f"  杠杆: {actual_leverage}x\n"
                            f"  数量: {result.get('quantity', 0):.6f}\n"
                            f"  止盈价: ${tp_price:.2f} (成交后设置)\n"
                            f"  止损价: ${sl_price:.2f} (成交后设置)\n"
                            f"  ⏳ 等待价格回调到限价成交"
                        )
                    else:
                        return f"❌ 限价开多失败: {result.get('message', '未知错误') if result else '订单提交失败'}"

                except Exception as e:
                    error_msg = f"限价开多异常: {str(e)}"
                    self.logger.print_error(f"[{self.symbol}Agent] {error_msg}")
                    return f"❌ {error_msg}"

            def sell_short_limit_callback(
                symbol: str,
                amount: float | None = None,
                leverage: int | None = None,
                price: float = 0.0,
            ) -> str:
                """限价开空回调"""
                try:
                    if self.trade_amount <= 0:
                        return "❌ 当前余额不足，无法开新仓。"

                    if price <= 0:
                        return "❌ 限价价格必须大于0"

                    fee_guard_msg = self._check_fee_guard()
                    if fee_guard_msg:
                        return fee_guard_msg

                    actual_amount = amount if amount is not None else self.trade_amount
                    actual_leverage = leverage if leverage is not None else self.max_leverage

                    if actual_amount > self.trade_amount:
                        return f"❌ 交易金额 ${actual_amount} 超过上限 ${self.trade_amount}"
                    if actual_leverage > self.max_leverage:
                        return f"❌ 杠杆倍数 {actual_leverage}x 超过上限 {self.max_leverage}x"

                    self.logger.print_info(
                        f"[{self.symbol}Agent] 执行限价开空 (金额: ${actual_amount}, 杠杆: {actual_leverage}x, 限价: ${price:.2f})"
                    )

                    if not self.order_manager.check_sufficient_balance(actual_amount):
                        return f"❌ 余额不足，需要 {actual_amount} USDT"

                    result = self.order_manager.execute_short_limit(
                        symbol=self.symbol,
                        usdt_amount=actual_amount,
                        limit_price=price,
                        leverage=actual_leverage,
                    )

                    if result and result.get("success"):
                        tp_price = result.get("take_profit_price", 0)
                        sl_price = result.get("stop_loss_price", 0)

                        return (
                            f"✅ 限价开空订单已提交！\n"
                            f"  币种: {symbol}\n"
                            f"  限价: ${price:.2f}\n"
                            f"  投入: ${actual_amount:.2f}\n"
                            f"  杠杆: {actual_leverage}x\n"
                            f"  数量: {result.get('quantity', 0):.6f}\n"
                            f"  止盈价: ${tp_price:.2f} (成交后设置)\n"
                            f"  止损价: ${sl_price:.2f} (成交后设置)\n"
                            f"  ⏳ 等待价格回调到限价成交"
                        )
                    else:
                        return f"❌ 限价开空失败: {result.get('message', '未知错误') if result else '订单提交失败'}"

                except Exception as e:
                    error_msg = f"限价开空异常: {str(e)}"
                    self.logger.print_error(f"[{self.symbol}Agent] {error_msg}")
                    return f"❌ {error_msg}"

            def cancel_limit_order_callback(symbol: str, order_id: int = 0) -> str:
                """取消限价单回调"""
                try:
                    if order_id <= 0:
                        return f"❌ 订单ID无效: {order_id}"

                    self.logger.print_info(f"[{self.symbol}Agent] 取消限价单 (订单ID: {order_id})")

                    result = self.order_manager.cancel_limit_order(symbol=symbol, order_id=order_id)

                    if result and result.get("success"):
                        return f"✅ 限价单 {order_id} 已成功取消"
                    else:
                        return f"❌ 取消限价单失败: {result.get('message', '未知错误') if result else '取消失败'}"

                except Exception as e:
                    error_msg = f"取消限价单异常: {str(e)}"
                    self.logger.print_error(f"[{self.symbol}Agent] {error_msg}")
                    return f"❌ {error_msg}"

        trading_tools = TradingTools(
            buy_callback,
            sell_callback,
            sell_short_callback,
            buy_to_cover_callback,
            do_nothing_callback,
            buy_spot_callback,
            buy_limit_callback if self.limit_order_enabled else None,
            sell_short_limit_callback if self.limit_order_enabled else None,
            cancel_limit_order_callback if self.limit_order_enabled else None,
        )

        # 保存回调函数引用以供后续使用
        self._buy_callback = buy_callback
        self._sell_callback = sell_callback
        self._sell_short_callback = sell_short_callback
        self._buy_to_cover_callback = buy_to_cover_callback
        self._do_nothing_callback = do_nothing_callback
        self._buy_spot_callback = buy_spot_callback
        if self.limit_order_enabled:
            self._buy_limit_callback = buy_limit_callback
            self._sell_short_limit_callback = sell_short_limit_callback
            self._cancel_limit_order_callback = cancel_limit_order_callback
        else:
            self._buy_limit_callback = None
            self._sell_short_limit_callback = None
            self._cancel_limit_order_callback = None

        return trading_tools.get_all_tools()

    def _get_tool_callbacks(self) -> dict[str, Any]:
        """
        获取工具回调函数字典

        Returns:
            工具回调函数字典
        """
        callbacks = {
            "buy": self._buy_callback,
            "sell": self._sell_callback,
            "sell_short": self._sell_short_callback,
            "buy_to_cover": self._buy_to_cover_callback,
            "do_nothing": self._do_nothing_callback,
            "buy_spot": self._buy_spot_callback,
        }

        # 如果限价单功能启用，添加限价单回调
        if self.limit_order_enabled:
            callbacks["buy_limit"] = self._buy_limit_callback
            callbacks["sell_short_limit"] = self._sell_short_limit_callback
            callbacks["cancel_limit_order"] = self._cancel_limit_order_callback

        return callbacks

    def _check_fee_guard(self) -> str | None:
        """
        确保当前止盈目标足以覆盖手续费，避免因为手续费导致的小额亏损
        """
        total_fee_rate = self.fee_rates.taker_rate * 2

        profit_to_fee_ratio = self.take_profit_ratio / total_fee_rate
        if profit_to_fee_ratio < self.MIN_PROFIT_TO_FEE_RATIO:
            return (
                f"❌ 当前止盈比例 {self.take_profit_ratio:.2%} "
                f"仅为手续费的 {profit_to_fee_ratio:.1f} 倍，低于最低要求 {self.MIN_PROFIT_TO_FEE_RATIO}x"
            )
        return None

    def make_decision(
        self,
        market_data: dict[str, Any],
        multi_timeframe_trends: dict[str, str],
        current_positions: list,
        max_positions: int,
        historical_summary: str | None = None,
        enriched_data: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """
        做出交易决策

        Args:
            market_data: 市场数据
            multi_timeframe_trends: 多时间周期趋势
            current_positions: 当前持仓
            max_positions: 最大持仓数
            historical_summary: 历史决策汇总（可选）

        Returns:
            (决策类型, 决策详情)
        """
        try:
            # 重置去重状态（每次新的决策周期开始时）
            self._executed_callbacks.clear()

            # 更新当前价格
            self.current_price = market_data.get("current_price", 0)

            # 获取实时余额信息
            balance_info = self.order_manager.get_available_balance_info()
            balance_dict = None
            if balance_info.get("status") == "ok":
                balance_dict = {
                    "total": balance_info["total"],
                    "occupied": balance_info["occupied"],
                    "available": balance_info["available"],
                }

            # 创建 Prompt
            if not self.prompt_manager:
                raise ValueError("PromptManager 是必需的，但未提供")

            # 获取待处理的限价单（如果限价单功能启用）
            open_limit_orders = []
            if self.limit_order_enabled:
                try:
                    open_limit_orders = self.order_manager.get_open_limit_orders(symbol=self.symbol)
                except Exception as e:
                    self.logger.print_warning(f"[{self.symbol}Agent] 获取限价单列表失败: {e}")

            prompt = self.prompt_manager.format_trading_prompt(
                symbol=self.symbol,
                market_data=market_data,
                multi_timeframe_trends=multi_timeframe_trends,
                current_positions=current_positions,
                max_positions=max_positions,
                max_trade_amount=self.trade_amount,
                max_leverage=self.max_leverage,
                take_profit_ratio=self.take_profit_ratio,
                stop_loss_ratio=self.stop_loss_ratio,
                historical_summary=historical_summary,
                balance_info=balance_dict,
                enriched_data=enriched_data,
                limit_order_enabled=self.limit_order_enabled,
                open_limit_orders=open_limit_orders,
            )

            # 显示 Prompt
            self.logger.print_section(f"[{self.symbol}Agent] 独立决策分析", style="bold magenta")
            self.logger.print_prompt(prompt)

            # 调用 Agent（含自动重试 1 次机制）
            messages = [self.system_message, HumanMessage(content=prompt)]

            # 使用 config 参数限制最大迭代次数
            # recursion_limit 控制图的最大递归深度，防止无限循环
            config = {"recursion_limit": self.max_iterations * 2}

            all_events, agent_output = self._invoke_agent_with_retry(messages, config, prompt)

            # 解析结果
            decision_type = self._parse_decision_from_events(all_events)
            decision_details = {
                "output": agent_output,
                "events": all_events,
                "prompt": prompt,
                "symbol": self.symbol,
            }

            return decision_type, decision_details

        except Exception as e:
            self.logger.print_error(f"[{self.symbol}Agent] 决策异常: {e}")
            self.logger.logger.exception(e)
            send_error_notification(
                notifier=self.notifier,
                exception=e,
                title=f"{self.symbol} Agent 决策异常",
                context_details={
                    "交易对": self.symbol,
                    "当前价": f"${self.current_price}",
                    "阶段": "LLM 决策分析",
                    "说明": "LLM API 调用异常，本轮决策将降级为 ERROR",
                },
            )
            return "ERROR", {"error": str(e)}

    def _invoke_agent_with_retry(
        self,
        messages: list,
        config: dict,
        prompt: str,
        max_retries: int = 1,
    ) -> tuple[list, str]:
        """
        调用 Agent 并在失败时自动重试，重试成功或失败均发送通知

        Args:
            messages: LLM 消息列表
            config: LangGraph 配置
            prompt: 原始 prompt（用于过滤输出）
            max_retries: 最大重试次数（默认 1 次）

        Returns:
            (事件列表, agent 输出文本)
        """
        for attempt in range(1, max_retries + 2):  # 首次 + 重试次数
            try:
                all_events = []
                agent_output = ""
                last_printed_content = ""

                for event in self.agent_executor.stream(
                    {"messages": messages}, stream_mode="values", config=config
                ):
                    all_events.append(event)
                    if "messages" in event and len(event["messages"]) > 0:
                        last_message = event["messages"][-1]
                        if hasattr(last_message, "content"):
                            content = last_message.content
                            if content and content != prompt:
                                agent_output = content

                            if (
                                content
                                and content != prompt
                                and len(content) > len(last_printed_content)
                            ):
                                self.logger.print_ai_response(
                                    content, f"🎯 {self.symbol} Agent 分析中..."
                                )
                                last_printed_content = content

                # 调用成功，如果是重试成功则记录日志
                if attempt > 1:
                    self.logger.print_info(
                        f"[{self.symbol}Agent] LLM API 重试成功（第 {attempt} 次尝试）"
                    )

                return all_events, agent_output

            except Exception as e:
                if attempt <= max_retries:
                    # 还有重试机会
                    self.logger.print_warning(
                        f"[{self.symbol}Agent] LLM API 调用失败（第 {attempt} 次），"
                        f"{2**attempt}s 后重试: {e}"
                    )
                    time.sleep(2**attempt)
                else:
                    # 所有重试均失败，发送通知并抛出异常
                    self.logger.print_error(
                        f"[{self.symbol}Agent] LLM API 调用失败，"
                        f"已重试 {max_retries} 次仍未恢复: {e}"
                    )
                    send_error_notification(
                        notifier=self.notifier,
                        exception=e,
                        title=f"{self.symbol} LLM API 重试失败",
                        context_details={
                            "交易对": self.symbol,
                            "当前价": f"${self.current_price}",
                            "阶段": "LLM 决策分析",
                            "尝试次数": f"{attempt} 次（含首次 + {max_retries} 次重试）",
                            "说明": "LLM API 调用重试后仍然失败",
                        },
                    )
                    raise

    def _parse_decision_from_events(self, events: list) -> str:
        """
        从事件中解析决策类型

        优先从工具调用中解析，如果没有则使用 ExecutionAgent 解析文本并执行

        Args:
            events: LangGraph 事件列表

        Returns:
            决策类型
        """
        try:
            # 首先尝试从正式的工具调用中解析
            for event in reversed(events):
                if "messages" not in event:
                    continue

                for message in reversed(event["messages"]):
                    if hasattr(message, "tool_calls") and message.tool_calls:
                        for tool_call in message.tool_calls:
                            tool_name = tool_call.get("name", "")
                            if tool_name == "buy":
                                return "BUY"
                            elif tool_name == "sell":
                                return "SELL"
                            elif tool_name == "sell_short":
                                return "SELL_SHORT"
                            elif tool_name == "buy_to_cover":
                                return "BUY_TO_COVER"
                            elif tool_name == "buy_spot":
                                return "BUY_SPOT_RECOMMEND"
                            elif tool_name == "do_nothing":
                                return "DO_NOTHING"

                    if hasattr(message, "name"):
                        if message.name == "buy":
                            return "BUY"
                        elif message.name == "sell":
                            return "SELL"
                        elif message.name == "sell_short":
                            return "SELL_SHORT"
                        elif message.name == "buy_to_cover":
                            return "BUY_TO_COVER"
                        elif message.name == "buy_spot":
                            return "BUY_SPOT_RECOMMEND"
                        elif message.name == "do_nothing":
                            return "DO_NOTHING"

            # 后备方案：使用 ExecutionAgent 解析文本并执行
            # 提取 Agent 的决策文本（只提取 AI 消息，不包括用户 prompt）
            decision_text = ""
            for event in reversed(events):
                if "messages" not in event:
                    continue
                for message in reversed(event["messages"]):
                    # 只提取 AI 的响应消息
                    if (
                        hasattr(message, "content")
                        and isinstance(message.content, str)
                        and hasattr(message, "type")
                        and message.type == "ai"
                    ):
                        decision_text = message.content
                        break
                if decision_text:
                    break

            if decision_text:
                self.logger.print_info(
                    f"[{self.symbol}Agent] 未检测到工具调用，使用 ExecutionAgent 解析决策文本"
                )
                self.logger.print_info(
                    f"[{self.symbol}Agent] 决策文本长度: {len(decision_text)} 字符"
                )
                self.logger.print_info(
                    f"[{self.symbol}Agent] 决策文本预览: {decision_text[:300]}{'...' if len(decision_text) > 300 else ''}"
                )

                # 使用 ExecutionAgent 解析决策
                execution_plan = self.execution_agent.parse_decision(
                    decision_text=decision_text, symbol=self.symbol, logger=self.logger
                )

                # 执行计划
                result = self.execution_agent.execute_plan(
                    execution_plan=execution_plan,
                    tools_callbacks=self.tools_callbacks,
                    logger=self.logger,
                )

                self.logger.print_info(f"[ExecutionAgent] 执行结果: {result}")

                # 返回决策类型
                decision_map = {
                    "BUY": "BUY",
                    "SELL": "SELL",
                    "SELL_SHORT": "SELL_SHORT",
                    "BUY_TO_COVER": "BUY_TO_COVER",
                    "BUY_SPOT": "BUY_SPOT_RECOMMEND",
                    "DO_NOTHING": "DO_NOTHING",
                }
                return decision_map.get(execution_plan.decision.value, "DO_NOTHING")

            return "DO_NOTHING"

        except Exception as e:
            self.logger.logger.error(f"解析决策失败: {e}")
            send_error_notification(
                notifier=self.notifier,
                exception=e,
                title=f"{self.symbol} 决策解析失败",
                context_details={
                    "交易对": self.symbol,
                    "阶段": "SingleSymbolAgent 决策解析",
                    "说明": "LLM 决策解析异常，本轮决策将降级为 ERROR",
                },
            )
            return "ERROR"
