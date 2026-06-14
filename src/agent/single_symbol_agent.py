"""
单币种交易 Agent 模块
为每个交易对维护独立的上下文窗口和决策历史
"""

import time
from typing import Any

from pydantic_ai import Agent, RunContext
from pydantic_ai.usage import UsageLimitExceeded, UsageLimits

from src.agent.execution_agent import ExecutionAgent
from src.agent.helpers import send_error_notification
from src.agent.prompts import SYSTEM_PROMPT
from src.config import FEE_RATE_PER_SIDE, MAKER_FEE_RATE_PER_SIDE
from src.fees import FeeRates
from src.llm import LLMClientManager
from src.llm.llm_client import wrap_llm_client
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

        # 工具回调执行后写回的交易事件，供 make_decision 合并到 details，
        # 用于风控插件等下游消费实际成交参数（避免回调结果只通过文本回流到 LLM）
        self._current_trade_event: dict[str, Any] = {}

        # 用于保存执行决策的类型
        self._current_decision_type: str | None = None

        # 初始化 LLM（从管理器获取）
        self.llm = wrap_llm_client(self.llm_manager.get_client(temperature=temperature))

        # 初始化执行 Agent（用于解析决策文本并执行）
        self.execution_agent = ExecutionAgent(
            llm_manager=llm_manager,
            temperature=0.0,  # 执行 Agent 使用零温度确保确定性
        )

        # 创建回调并保存
        self._create_tools()
        self.tools_callbacks = self._get_tool_callbacks()

        # 设置系统提示词与 Pydantic AI Agent
        if self.prompt_manager:
            system_prompt_text = self.prompt_manager.get_system_prompt()
        else:
            system_prompt_text = SYSTEM_PROMPT
        self.system_prompt = system_prompt_text

        # 初始化 Pydantic AI Agent
        self.pydantic_agent = Agent(
            self.llm, deps_type=SingleSymbolAgent, system_prompt=system_prompt_text
        )
        self._register_pydantic_tools()

    def _register_pydantic_tools(self):
        """注册 Pydantic AI 的工具"""

        @self.pydantic_agent.tool
        def buy(
            ctx: RunContext[SingleSymbolAgent],
            symbol: str,
            amount: float | None = None,
            leverage: int | None = None,
        ) -> str:
            """执行买入开多操作。当市场出现明确的做多信号时使用此工具。

            使用条件:
            - 未持有该币种的多头仓位
            - 未达到最大持仓数量
            - 技术指标显示看涨信号

            系统会自动设置止盈单（价格上涨 5%）和止损单（价格下跌 2%）。
            """
            ctx.deps._current_decision_type = "BUY"
            return ctx.deps._buy_callback(symbol, amount, leverage)

        @self.pydantic_agent.tool
        def sell(ctx: RunContext[SingleSymbolAgent], symbol: str) -> str:
            """执行卖出平多操作。当已持有该币种的多头仓位且出现卖出信号时使用此工具。"""
            ctx.deps._current_decision_type = "SELL"
            return ctx.deps._sell_callback(symbol)

        @self.pydantic_agent.tool
        def sell_short(
            ctx: RunContext[SingleSymbolAgent],
            symbol: str,
            amount: float | None = None,
            leverage: int | None = None,
        ) -> str:
            """执行卖空开空操作。当市场出现明确的做空信号时使用此工具。

            使用条件:
            - 未持有该币种的空头仓位
            - 未达到最大持仓数量
            - 技术指标显示看跌信号

            系统会自动设置止盈单（价格下跌 5%）和止损单（价格上涨 2%）。
            """
            ctx.deps._current_decision_type = "SELL_SHORT"
            return ctx.deps._sell_short_callback(symbol, amount, leverage)

        @self.pydantic_agent.tool
        def buy_to_cover(ctx: RunContext[SingleSymbolAgent], symbol: str) -> str:
            """执行买入平空操作。当已持有该币种的空头仓位且出现平仓信号时使用此工具。"""
            ctx.deps._current_decision_type = "BUY_TO_COVER"
            return ctx.deps._buy_to_cover_callback(symbol)

        @self.pydantic_agent.tool
        def do_nothing(ctx: RunContext[SingleSymbolAgent], reason: str) -> str:
            """不执行任何交易操作。当市场信号不明确或不满足交易条件时使用此工具。

            参数:
            - reason: 不操作的原因（必须提供）
            """
            ctx.deps._current_decision_type = "DO_NOTHING"
            return ctx.deps._do_nothing_callback(reason)

        if self.limit_order_enabled:

            @self.pydantic_agent.tool
            def buy_limit(
                ctx: RunContext[SingleSymbolAgent],
                symbol: str,
                amount: float | None = None,
                leverage: int | None = None,
                price: float = 0.0,
            ) -> str:
                """执行限价开多操作。当市场出现做多信号但希望以更好的价格成交时使用此工具。"""
                ctx.deps._current_decision_type = "BUY_LIMIT"
                return ctx.deps._buy_limit_callback(symbol, amount, leverage, price)

            @self.pydantic_agent.tool
            def sell_short_limit(
                ctx: RunContext[SingleSymbolAgent],
                symbol: str,
                amount: float | None = None,
                leverage: int | None = None,
                price: float = 0.0,
            ) -> str:
                """执行限价开空操作。当市场出现做空信号但希望以更好的价格成交时使用此工具。"""
                ctx.deps._current_decision_type = "SELL_SHORT_LIMIT"
                return ctx.deps._sell_short_limit_callback(symbol, amount, leverage, price)

            @self.pydantic_agent.tool
            def cancel_limit_order(
                ctx: RunContext[SingleSymbolAgent], symbol: str, order_id: int
            ) -> str:
                """取消待处理的限价单。"""
                ctx.deps._current_decision_type = "CANCEL_LIMIT_ORDER"
                return ctx.deps._cancel_limit_order_callback(symbol, order_id)

    def _create_tools(self) -> list:
        """创建工具集"""

        def buy_callback(
            symbol: str, amount: float | None = None, leverage: int | None = None
        ) -> str:
            """买入开多回调"""
            try:
                # 幂等守卫：本轮已下过单则拒绝（防止 run_sync 重跑导致重复下单）
                dup_msg = self._guard_duplicate_action("BUY")
                if dup_msg:
                    return dup_msg
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

                    # 写回执行后的真实成交参数（供 main.py 风控插件等下游使用）
                    self._current_trade_event = {
                        "action": "BUY",
                        "entry_price": float(entry_price),
                        "size": float(quantity),
                        "amount": float(actual_amount),
                        "leverage": int(actual_leverage),
                        "fill_price": float(result.get("fill_price", entry_price)),
                        "is_long": True,
                    }

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
                # 幂等守卫：本轮已平过仓则拒绝（防止 run_sync 重跑导致重复平仓）
                dup_msg = self._guard_duplicate_action("SELL")
                if dup_msg:
                    return dup_msg
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
                    # 计算执行结果（独立于通知发送，供风控插件等下游消费）
                    entry_price = float(position.get("entryPx", 0))
                    exit_price = result.get("fill_price", self.current_price)
                    size = abs(float(position.get("szi", 0)))
                    pnl = (exit_price - entry_price) * size
                    leverage = safe_leverage(position.get("leverage"), 1)
                    pnl_percent = (
                        (exit_price - entry_price) / entry_price * leverage * 100
                        if entry_price > 0
                        else 0
                    )

                    # 写回执行后的真实成交参数
                    self._current_trade_event = {
                        "action": "SELL",
                        "entry_price": entry_price,
                        "exit_price": float(exit_price),
                        "fill_price": float(exit_price),
                        "size": size,
                        "pnl": float(pnl),
                        "pnl_percent": float(pnl_percent),
                        "leverage": int(leverage),
                    }

                    # 发送平仓通知
                    if self.notifier:
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
                # 幂等守卫：本轮已下过单则拒绝（防止 run_sync 重跑导致重复下单）
                dup_msg = self._guard_duplicate_action("SELL_SHORT")
                if dup_msg:
                    return dup_msg
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

                    # 写回执行后的真实成交参数（供 main.py 风控插件等下游使用）
                    self._current_trade_event = {
                        "action": "SELL_SHORT",
                        "entry_price": float(entry_price),
                        "size": float(quantity),
                        "amount": float(actual_amount),
                        "leverage": int(actual_leverage),
                        "fill_price": float(result.get("fill_price", entry_price)),
                        "is_long": False,
                    }

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
                # 幂等守卫：本轮已平过仓则拒绝（防止 run_sync 重跑导致重复平仓）
                dup_msg = self._guard_duplicate_action("BUY_TO_COVER")
                if dup_msg:
                    return dup_msg
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
                    # 计算执行结果（独立于通知发送，供风控插件等下游消费）
                    entry_price = float(position.get("entryPx", 0))
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

                    # 写回执行后的真实成交参数
                    self._current_trade_event = {
                        "action": "BUY_TO_COVER",
                        "entry_price": entry_price,
                        "exit_price": float(exit_price),
                        "fill_price": float(exit_price),
                        "size": size,
                        "pnl": float(pnl),
                        "pnl_percent": float(pnl_percent),
                        "leverage": int(leverage),
                    }

                    # 发送平仓通知
                    if self.notifier:
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
                    # 幂等守卫：本轮已下过限价单则拒绝（防止重复挂单造成双重敞口）
                    dup_msg = self._guard_duplicate_action("BUY_LIMIT")
                    if dup_msg:
                        return dup_msg
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
                    # 幂等守卫：本轮已下过限价单则拒绝（防止重复挂单造成双重敞口）
                    dup_msg = self._guard_duplicate_action("SELL_SHORT_LIMIT")
                    if dup_msg:
                        return dup_msg
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

        # 保存回调函数引用以供后续使用
        self._buy_callback = buy_callback
        self._sell_callback = sell_callback
        self._sell_short_callback = sell_short_callback
        self._buy_to_cover_callback = buy_to_cover_callback
        self._do_nothing_callback = do_nothing_callback
        if self.limit_order_enabled:
            self._buy_limit_callback = buy_limit_callback
            self._sell_short_limit_callback = sell_short_limit_callback
            self._cancel_limit_order_callback = cancel_limit_order_callback
        else:
            self._buy_limit_callback = None
            self._sell_short_limit_callback = None
            self._cancel_limit_order_callback = None

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
        }

        # 如果限价单功能启用，添加限价单回调
        if self.limit_order_enabled:
            callbacks["buy_limit"] = self._buy_limit_callback
            callbacks["sell_short_limit"] = self._sell_short_limit_callback
            callbacks["cancel_limit_order"] = self._cancel_limit_order_callback

        return callbacks

    def _guard_duplicate_action(self, action: str) -> str | None:
        """
        幂等守卫：同一决策周期内，每个真实下单/平仓动作最多执行一次。

        pydantic-ai 的 ``run_sync`` 在一次运行内可被模型多次调用工具，且上层
        ``_invoke_agent_with_retry`` 在 ``run_sync`` 抛错时会整轮重跑。若放任重复
        调用，已成交的真实订单会在重跑中再次下单 → 重复开仓/平仓（真实资金损失）。
        因此每个下单回调入口先调用本方法：本轮已执行过则返回拒绝信息，调用方直接返回不下单。

        去重集合 ``_executed_callbacks`` 在每次 ``make_decision`` 开始时清空，
        故幂等边界 = 单次决策周期（含其内的应用层重试），跨周期不受影响。

        Args:
            action: 动作标识，如 "BUY" / "SELL" / "SELL_SHORT" / "BUY_TO_COVER" /
                "BUY_LIMIT" / "SELL_SHORT_LIMIT"

        Returns:
            None 表示本轮尚未执行、允许执行（并已登记）；非空字符串表示拒绝原因，
            调用方应将其直接返回给模型，不再下单。
        """
        key = f"action:{action}"
        if key in self._executed_callbacks:
            msg = f"❌ 本轮决策已执行过 {action}，拒绝重复下单（幂等保护）"
            self.logger.print_warning(f"[{self.symbol}Agent] {msg}")
            return msg
        self._executed_callbacks.add(key)
        return None

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

            # 重置工具回调写回的交易事件
            self._current_trade_event = {}

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
            self._current_decision_type = None
            agent_output = self._invoke_agent_with_retry(prompt)

            # 解析结果
            if self._current_decision_type:
                decision_type = self._current_decision_type
            else:
                decision_type = self._parse_decision_from_text(agent_output)

            decision_details = {
                "output": agent_output,
                "prompt": prompt,
                "symbol": self.symbol,
            }

            # 合入工具回调写回的真实成交参数
            # （open/close 事件传递给风控插件、即时反思、决策录制等）
            if self._current_trade_event:
                decision_details.update(self._current_trade_event)

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
        prompt: str,
        max_retries: int = 1,
    ) -> str:
        """
        调用 Agent 并在失败时自动重试，重试成功或失败均发送通知
        """
        # 工具调用 / 模型请求上限（接入 agent_max_iterations 配置）。
        # 迁移到 pydantic-ai 后该配置一度失效，模型可在一轮内无限循环调用工具
        # （放大重复下单面）；此处用 UsageLimits 重新施加约束。
        usage_limits = UsageLimits(
            tool_calls_limit=max(1, self.max_iterations),
            request_limit=max(5, self.max_iterations * 2),
        )

        for attempt in range(1, max_retries + 2):  # 首次 + 重试次数
            try:
                # 调用 Pydantic AI agent
                result = self.pydantic_agent.run_sync(prompt, deps=self, usage_limits=usage_limits)
                agent_output = (
                    result.output if isinstance(result.output, str) else str(result.output)
                )

                # 调用成功，如果是重试成功则记录日志
                if attempt > 1:
                    self.logger.print_info(
                        f"[{self.symbol}Agent] LLM API 重试成功（第 {attempt} 次尝试）"
                    )

                return agent_output

            except UsageLimitExceeded as e:
                # 用量超限（工具调用/模型请求超过 max_iterations）：模型在单轮内失控，
                # 重试必然再次触顶，故直接失败、不做无意义的指数退避重试
                self.logger.print_warning(
                    f"[{self.symbol}Agent] 决策循环超限（工具/请求超过 max_iterations="
                    f"{self.max_iterations}）: {e}"
                )
                send_error_notification(
                    notifier=self.notifier,
                    exception=e,
                    title=f"{self.symbol} 决策循环超限",
                    context_details={
                        "交易对": self.symbol,
                        "当前价": f"${self.current_price}",
                        "阶段": "LLM 决策分析",
                        "说明": (
                            f"模型在一轮决策内工具调用/请求超过上限 "
                            f"{self.max_iterations}，已拒绝继续"
                        ),
                    },
                )
                raise

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

    def _parse_decision_from_text(self, decision_text: str) -> str:
        """
        后备方案：使用 ExecutionAgent 解析文本并执行
        """
        try:
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
                    "DO_NOTHING": "DO_NOTHING",
                    "BUY_LIMIT": "BUY_LIMIT",
                    "SELL_SHORT_LIMIT": "SELL_SHORT_LIMIT",
                    "CANCEL_LIMIT_ORDER": "CANCEL_LIMIT_ORDER",
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
