"""
单币种交易 Agent 模块
为每个交易对维护独立的上下文窗口和决策历史
"""

from typing import Dict, Any, Tuple, Optional
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent

from src.agent.tools import TradingTools
from src.agent.prompts import SYSTEM_PROMPT
from src.trading.order_manager import OrderManager
from src.utils.logger import TradingLogger
from src.prompt_manager import PromptManager


class SingleSymbolAgent:
    """单币种交易 Agent - 为每个交易对维护独立上下文"""

    def __init__(
        self,
        symbol: str,
        order_manager: OrderManager,
        logger: TradingLogger,
        openai_api_base: str,
        openai_api_key: str,
        openai_model: str,
        temperature: float = 0.1,
        max_iterations: int = 5,
        trade_amount: float = 100.0,
        max_leverage: int = 10,
        take_profit_ratio: float = 0.05,
        stop_loss_ratio: float = 0.02,
        notifier=None,
        prompt_manager: Optional[PromptManager] = None
    ):
        """
        初始化单币种交易 Agent

        Args:
            symbol: 交易对
            order_manager: 订单管理器
            logger: 日志记录器
            openai_api_base: OpenAI API Base URL
            openai_api_key: OpenAI API Key
            openai_model: 模型名称
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
        self.trade_amount = trade_amount
        self.max_leverage = max_leverage
        self.take_profit_ratio = take_profit_ratio
        self.stop_loss_ratio = stop_loss_ratio
        self.current_price = 0.0
        self.max_iterations = max_iterations
        self.notifier = notifier
        self.prompt_manager = prompt_manager

        # 初始化 LLM
        self.llm = ChatOpenAI(
            base_url=openai_api_base,
            api_key=openai_api_key,
            model=openai_model,
            temperature=temperature,
        )

        # 创建工具
        self.tools = self._create_tools()

        self.agent_executor = create_react_agent(
            model=self.llm,
            tools=self.tools
        )

        # 设置系统提示词
        if self.prompt_manager:
            system_prompt_text = self.prompt_manager.get_system_prompt()
        else:
            system_prompt_text = SYSTEM_PROMPT
        self.system_message = SystemMessage(content=system_prompt_text)

    def _create_tools(self) -> list:
        """创建工具集"""

        def buy_callback(symbol: str, amount: Optional[float] = None, leverage: Optional[int] = None) -> str:
            """买入开多回调"""
            try:
                # 使用 AI 指定的金额和杠杆，如果没有指定则使用默认上限
                actual_amount = amount if amount is not None else self.trade_amount
                actual_leverage = leverage if leverage is not None else self.max_leverage

                # 验证参数
                if actual_amount > self.trade_amount:
                    return f"❌ 交易金额 ${actual_amount} 超过上限 ${self.trade_amount}"
                if actual_leverage > self.max_leverage:
                    return f"❌ 杠杆倍数 {actual_leverage}x 超过上限 {self.max_leverage}x"

                self.logger.print_info(f"[{self.symbol}Agent] 执行买入开多 (金额: ${actual_amount}, 杠杆: {actual_leverage}x)")

                if not self.order_manager.check_sufficient_balance(actual_amount):
                    return f"❌ 余额不足，需要 {actual_amount} USDT"

                positions = self.order_manager.get_current_positions()
                has_long = any(p.get('coin') == self.symbol and p.get('side', 'long') == 'long' for p in positions)
                if has_long:
                    return f"❌ 已持有 {self.symbol} 的多头仓位"

                result = self.order_manager.execute_long(
                    symbol=self.symbol,
                    usdt_amount=actual_amount,
                    leverage=actual_leverage,
                    with_tpsl=True
                )

                if result and result.get('success'):
                    # 获取市场订单信息
                    market_order = result.get('market_order', {})
                    entry_price = self.current_price
                    tp_price = entry_price * 1.05  # 5% take profit
                    sl_price = entry_price * 0.98  # 2% stop loss
                    quantity = result.get('quantity', 0)
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
                            order_hash=result.get('hash', '')
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
                self.logger.print_error(f"[{self.symbol}Agent] 交易参数: 金额=${actual_amount}, 杠杆={actual_leverage}x, 价格=${self.current_price}")

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
                        context=context
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
                        title=f"{self.symbol} 买入开多异常",
                        error_message=str(e),
                        context=context
                    )

                return error_msg

        def sell_callback(symbol: str) -> str:
            """卖出平多回调"""
            try:
                self.logger.print_info(f"[{self.symbol}Agent] 执行卖出平多")

                positions = self.order_manager.get_current_positions()
                position = next((p for p in positions if p.get('coin') == self.symbol and p.get('side', 'long') == 'long'), None)

                if not position:
                    return f"❌ 未持有 {self.symbol} 的多头仓位"

                # 记录持仓详情以便调试
                self.logger.print_info(f"[{self.symbol}Agent] 持仓详情: 币种={position.get('coin')}, 大小={position.get('szi')}, 入场价={position.get('entryPx')}")

                result = self.order_manager.close_position(
                    symbol=self.symbol,
                    size=None  # Close entire position
                )

                if result and result.get('status') == 'ok':
                    # 发送平仓通知
                    if self.notifier:
                        entry_price = float(position.get('entryPx', 0))
                        exit_price = self.current_price
                        size = abs(float(position.get('szi', 0)))
                        pnl = result.get('pnl', 0)
                        pnl_percent = (exit_price - entry_price) / entry_price * 100 if entry_price > 0 else 0

                        self.notifier.notify_trade_closed(
                            symbol=self.symbol,
                            side="long",
                            quantity=size,
                            entry_price=entry_price,
                            exit_price=exit_price,
                            pnl=pnl,
                            pnl_percent=pnl_percent,
                            order_hash=result.get('hash', '')
                        )

                    return f"✅ 卖出平多成功！"

                # 平仓失败 - 记录详细信息并发送通知
                error_msg = f"❌ 卖出平多失败"
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
                        context=context
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
                        title=f"{self.symbol} 卖出平多异常",
                        error_message=str(e),
                        context=context
                    )

                return error_msg

        def sell_short_callback(symbol: str, amount: Optional[float] = None, leverage: Optional[int] = None) -> str:
            """卖空开空回调"""
            try:
                # 使用 AI 指定的金额和杠杆，如果没有指定则使用默认上限
                actual_amount = amount if amount is not None else self.trade_amount
                actual_leverage = leverage if leverage is not None else self.max_leverage

                # 验证参数
                if actual_amount > self.trade_amount:
                    return f"❌ 交易金额 ${actual_amount} 超过上限 ${self.trade_amount}"
                if actual_leverage > self.max_leverage:
                    return f"❌ 杠杆倍数 {actual_leverage}x 超过上限 {self.max_leverage}x"

                self.logger.print_info(f"[{self.symbol}Agent] 执行卖空开空 (金额: ${actual_amount}, 杠杆: {actual_leverage}x)")

                if not self.order_manager.check_sufficient_balance(actual_amount):
                    return f"❌ 余额不足，需要 {actual_amount} USDT"

                positions = self.order_manager.get_current_positions()
                has_short = any(p.get('coin') == self.symbol and p.get('side') == 'short' for p in positions)
                if has_short:
                    return f"❌ 已持有 {self.symbol} 的空头仓位"

                result = self.order_manager.execute_short(
                    symbol=self.symbol,
                    usdt_amount=actual_amount,
                    leverage=actual_leverage,
                    with_tpsl=True
                )

                if result and result.get('success'):
                    # 获取市场订单信息
                    entry_price = self.current_price
                    tp_price = entry_price * 0.95  # 5% take profit (下跌)
                    sl_price = entry_price * 1.02  # 2% stop loss (上涨)
                    quantity = result.get('quantity', 0)
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
                            order_hash=result.get('hash', '')
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
                self.logger.print_error(f"[{self.symbol}Agent] 交易参数: 金额=${actual_amount}, 杠杆={actual_leverage}x, 价格=${self.current_price}")

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
                        context=context
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
                        title=f"{self.symbol} 卖空开空异常",
                        error_message=str(e),
                        context=context
                    )

                return error_msg

        def buy_to_cover_callback(symbol: str) -> str:
            """买入平空回调"""
            try:
                self.logger.print_info(f"[{self.symbol}Agent] 执行买入平空")

                positions = self.order_manager.get_current_positions()
                position = next((p for p in positions if p.get('coin') == self.symbol and p.get('side') == 'short'), None)

                if not position:
                    return f"❌ 未持有 {self.symbol} 的空头仓位"

                # 记录持仓详情以便调试
                self.logger.print_info(f"[{self.symbol}Agent] 持仓详情: 币种={position.get('coin')}, 大小={position.get('szi')}, 入场价={position.get('entryPx')}")

                result = self.order_manager.close_position(
                    symbol=self.symbol,
                    size=None  # Close entire position
                )

                if result and result.get('status') == 'ok':
                    # 发送平仓通知
                    if self.notifier:
                        entry_price = float(position.get('entryPx', 0))
                        exit_price = self.current_price
                        size = abs(float(position.get('szi', 0)))
                        pnl = result.get('pnl', 0)
                        leverage = position.get('leverage', 1)
                        pnl_percent = ((entry_price - exit_price) / entry_price * leverage * 100) if entry_price > 0 else 0

                        self.notifier.notify_trade_closed(
                            symbol=self.symbol,
                            side="short",
                            quantity=size,
                            entry_price=entry_price,
                            exit_price=exit_price,
                            pnl=pnl,
                            pnl_percent=pnl_percent,
                            order_hash=result.get('hash', '')
                        )

                    return f"✅ 买入平空成功！"

                # 平仓失败 - 记录详细信息并发送通知
                error_msg = f"❌ 买入平空失败"
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
                        context=context
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
                        title=f"{self.symbol} 买入平空异常",
                        error_message=str(e),
                        context=context
                    )

                return error_msg

        def do_nothing_callback(reason: str) -> str:
            """不操作回调"""
            self.logger.print_info(f"[{self.symbol}Agent] 不操作 - {reason}")
            return f"⏸️  确认：不执行操作。原因：{reason}"

        def buy_spot_callback(symbol: str, amount: Optional[float] = None) -> str:
            """现货定投推荐回调（仅推荐，不直接执行）"""
            actual_amount = amount if amount is not None else self.trade_amount
            if actual_amount > self.trade_amount:
                return f"❌ 定投金额 ${actual_amount} 超过上限 ${self.trade_amount}"
            self.logger.print_info(f"[{self.symbol}Agent] 推荐现货定投 (建议金额: ${actual_amount})，将交给现货 Agent 评估")
            return f"📝 已推荐 {symbol} 现货定投 (建议金额: ${actual_amount})，等待现货 Agent 评估"

        trading_tools = TradingTools(
            buy_callback,
            sell_callback,
            sell_short_callback,
            buy_to_cover_callback,
            do_nothing_callback,
            buy_spot_callback
        )
        return trading_tools.get_all_tools()

    def make_decision(
        self,
        market_data: Dict[str, Any],
        multi_timeframe_trends: Dict[str, str],
        current_positions: list,
        max_positions: int,
        historical_summary: Optional[str] = None
    ) -> Tuple[str, Dict[str, Any]]:
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
            # 更新当前价格
            self.current_price = market_data.get('current_price', 0)

            # 获取实时余额信息
            balance_info = self.order_manager.get_available_balance_info()
            balance_dict = None
            if balance_info.get('status') == 'ok':
                balance_dict = {
                    'total': balance_info['total'],
                    'occupied': balance_info['occupied'],
                    'available': balance_info['available']
                }

            # 创建 Prompt
            if not self.prompt_manager:
                raise ValueError("PromptManager 是必需的，但未提供")

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
                balance_info=balance_dict
            )

            # 显示 Prompt
            self.logger.print_section(f"[{self.symbol}Agent] 独立决策分析", style="bold magenta")
            self.logger.print_prompt(prompt)

            # 调用 Agent
            messages = [
                self.system_message,
                HumanMessage(content=prompt)
            ]

            # 收集所有输出
            all_events = []
            agent_output = ""

            for event in self.agent_executor.stream(
                {"messages": messages},
                stream_mode="values"
            ):
                all_events.append(event)
                if "messages" in event and len(event["messages"]) > 0:
                    last_message = event["messages"][-1]
                    if hasattr(last_message, 'content'):
                        content = last_message.content
                        if content and content != prompt and content != agent_output:
                            # 使用新的 AI 响应渲染方法（支持 Markdown）
                            self.logger.print_ai_response(content, f"🎯 {self.symbol} Agent 分析中...")
                            agent_output = content

            # 解析结果
            decision_type = self._parse_decision_from_events(all_events)
            decision_details = {
                "output": agent_output,
                "events": all_events,
                "prompt": prompt,
                "symbol": self.symbol
            }

            return decision_type, decision_details

        except Exception as e:
            self.logger.print_error(f"[{self.symbol}Agent] 决策异常: {e}")
            self.logger.logger.exception(e)
            return "ERROR", {"error": str(e)}

    def _parse_decision_from_events(self, events: list) -> str:
        """
        从事件中解析决策类型

        Args:
            events: LangGraph 事件列表

        Returns:
            决策类型
        """
        try:
            for event in reversed(events):
                if "messages" not in event:
                    continue

                for message in reversed(event["messages"]):
                    if hasattr(message, 'tool_calls') and message.tool_calls:
                        for tool_call in message.tool_calls:
                            tool_name = tool_call.get('name', '')
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

                    if hasattr(message, 'name'):
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

            return "DO_NOTHING"

        except Exception as e:
            self.logger.logger.error(f"解析决策失败: {e}")
            return "ERROR"
