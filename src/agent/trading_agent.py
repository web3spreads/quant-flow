"""
交易 Agent 核心模块
使用 LangGraph 和最新的 LangChain 1.0+ API
"""

from typing import Dict, Any, Tuple, List
from datetime import datetime
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent

from src.agent.tools import TradingTools
from src.agent.prompts import create_trading_prompt, create_batch_trading_prompt, SYSTEM_PROMPT
from src.trading.order_manager import OrderManager
from src.utils.logger import TradingLogger


class TradingAgent:
    """AI 交易 Agent - 使用 LangGraph"""

    def __init__(
        self,
        order_manager: OrderManager,
        logger: TradingLogger,
        openai_api_base: str,
        openai_api_key: str,
        openai_model: str,
        temperature: float = 0.1,
        max_iterations: int = 5,
        max_token_limit: int = 2000,
        trade_amount: float = 100.0,
        current_symbol: str = "BTC/USDT"
    ):
        """
        初始化交易 Agent

        Args:
            order_manager: 订单管理器
            logger: 日志记录器
            openai_api_base: OpenAI API Base URL
            openai_api_key: OpenAI API Key
            openai_model: 模型名称
            temperature: 温度参数
            max_iterations: 最大迭代次数
            max_token_limit: 记忆最大 token 限制
            trade_amount: 交易金额（USDT）
            current_symbol: 当前交易对
        """
        self.order_manager = order_manager
        self.logger = logger
        self.trade_amount = trade_amount
        self.current_symbol = current_symbol
        self.current_price = 0.0
        self.max_iterations = max_iterations

        # 批量决策时的价格映射表 {symbol: price}
        self.price_map = {}

        # 初始化 LLM
        self.llm = ChatOpenAI(
            base_url=openai_api_base,
            api_key=openai_api_key,
            model=openai_model,
            temperature=temperature,
        )

        # 创建工具
        self.tools = self._create_tools()

        # 使用 LangGraph 创建 ReAct Agent
        # 注意：在 LangGraph 1.0+ 中，系统提示词通过消息历史传递
        self.agent_executor = create_react_agent(
            model=self.llm,
            tools=self.tools
        )

        # 系统提示词作为第一条消息
        self.system_message = SystemMessage(content=SYSTEM_PROMPT)

        # 用于跟踪对话历史
        self.conversation_history = []

    def _create_tools(self) -> list:
        """创建工具集"""

        def buy_callback(symbol: str) -> str:
            """买入开多回调"""
            try:
                self.logger.print_info(f"执行买入开多: {symbol}")

                # 检查余额
                if not self.order_manager.check_sufficient_balance(self.trade_amount):
                    return f"❌ 余额不足，需要 {self.trade_amount} USDT"

                # 检查是否已持有多头仓位
                positions = self.order_manager.get_current_positions()
                has_long = any(p['symbol'] == symbol and p.get('side') == 'long' for p in positions)
                if has_long:
                    return f"❌ 已持有 {symbol} 的多头仓位，无法重复开多"

                # 获取当前价格：优先使用 price_map，否则使用 self.current_price
                current_price = self.price_map.get(symbol, self.current_price)
                if current_price <= 0:
                    return f"❌ 无法获取 {symbol} 的当前价格"

                # 执行买入
                order_info = self.order_manager.execute_buy_with_protection(
                    symbol=symbol,
                    usdt_amount=self.trade_amount,
                    current_price=current_price
                )

                if order_info:
                    return (
                        f"✅ 买入开多成功！\n"
                        f"  订单ID: {order_info['buy_order']['id']}\n"
                        f"  入场价: ${order_info['entry_price']:.2f}\n"
                        f"  止盈价: ${order_info['take_profit_price']:.2f}\n"
                        f"  止损价: ${order_info['stop_loss_price']:.2f}"
                    )
                else:
                    return "❌ 买入开多失败，请检查日志"

            except Exception as e:
                return f"❌ 买入开多异常: {str(e)}"

        def sell_callback(symbol: str) -> str:
            """卖出平多回调"""
            try:
                self.logger.print_info(f"执行卖出平多: {symbol}")

                # 获取多头持仓
                positions = self.order_manager.get_current_positions()
                position = next((p for p in positions if p['symbol'] == symbol and p.get('side', 'long') == 'long'), None)

                if not position:
                    return f"❌ 未持有 {symbol} 的多头仓位，无法卖出平多"

                # 执行卖出
                sell_order = self.order_manager.execute_sell(
                    symbol=symbol,
                    amount=position['amount']
                )

                if sell_order:
                    return (
                        f"✅ 卖出平多成功！\n"
                        f"  订单ID: {sell_order['id']}\n"
                        f"  卖出数量: {position['amount']:.6f}"
                    )
                else:
                    return "❌ 卖出平多失败，请检查日志"

            except Exception as e:
                return f"❌ 卖出平多异常: {str(e)}"

        def sell_short_callback(symbol: str) -> str:
            """卖空开空回调"""
            try:
                self.logger.print_info(f"执行卖空开空: {symbol}")

                # 检查余额
                if not self.order_manager.check_sufficient_balance(self.trade_amount):
                    return f"❌ 余额不足，需要 {self.trade_amount} USDT"

                # 检查是否已持有空头仓位
                positions = self.order_manager.get_current_positions()
                has_short = any(p['symbol'] == symbol and p.get('side') == 'short' for p in positions)
                if has_short:
                    return f"❌ 已持有 {symbol} 的空头仓位，无法重复开空"

                # 检查是否已持有多头仓位（避免同时持有多空）
                has_long = any(p['symbol'] == symbol and p.get('side') == 'long' for p in positions)
                if has_long:
                    return f"❌ 已持有 {symbol} 的多头仓位，不能同时做空"

                # 获取当前价格：优先使用 price_map，否则使用 self.current_price
                current_price = self.price_map.get(symbol, self.current_price)
                if current_price <= 0:
                    return f"❌ 无法获取 {symbol} 的当前价格"

                # 执行卖空
                order_info = self.order_manager.execute_sell_short_with_protection(
                    symbol=symbol,
                    usdt_amount=self.trade_amount,
                    current_price=current_price
                )

                if order_info:
                    return (
                        f"✅ 卖空开空成功！\n"
                        f"  {'模拟' if order_info.get('simulated') else '实际'}空头仓位\n"
                        f"  入场价: ${order_info['entry_price']:.2f}\n"
                        f"  止盈价: ${order_info['take_profit_price']:.2f}\n"
                        f"  止损价: ${order_info['stop_loss_price']:.2f}"
                    )
                else:
                    return "❌ 卖空开空失败，请检查日志"

            except Exception as e:
                return f"❌ 卖空开空异常: {str(e)}"

        def buy_to_cover_callback(symbol: str) -> str:
            """买入平空回调"""
            try:
                self.logger.print_info(f"执行买入平空: {symbol}")

                # 获取空头持仓
                positions = self.order_manager.get_current_positions()
                position = next((p for p in positions if p['symbol'] == symbol and p.get('side') == 'short'), None)

                if not position:
                    return f"❌ 未持有 {symbol} 的空头仓位，无法买入平空"

                # 执行买入平空
                cover_order = self.order_manager.execute_buy_to_cover(
                    symbol=symbol,
                    amount=position['amount']
                )

                if cover_order:
                    return (
                        f"✅ 买入平空成功！\n"
                        f"  {'模拟' if cover_order.get('simulated') else '实际'}平仓\n"
                        f"  平仓数量: {position['amount']:.6f}"
                    )
                else:
                    return "❌ 买入平空失败，请检查日志"

            except Exception as e:
                return f"❌ 买入平空异常: {str(e)}"

        def do_nothing_callback(reason: str) -> str:
            """不操作回调"""
            self.logger.print_info(f"决策：不操作 - {reason}")
            return f"⏸️  确认：不执行操作。原因：{reason}"

        def buy_spot_callback(symbol: str) -> str:
            """现货买入回调（用于长期持有策略）"""
            try:
                self.logger.print_info(f"执行现货定投: {symbol}")

                # 检查余额
                balance_info = self.order_manager.get_available_balance_info()
                if balance_info['status'] != 'ok':
                    return f"❌ {balance_info['message']}"

                available = balance_info['available']
                if available < self.trade_amount:
                    return f"❌ 可用余额不足。需要: ${self.trade_amount:.2f}, 可用: ${available:.2f}"

                # 执行现货买入
                result = self.order_manager.buy_spot_for_dca(
                    symbol=symbol,
                    usdt_amount=self.trade_amount
                )

                if result and result.get('success'):
                    return (
                        f"✅ 现货定投成功！\n"
                        f"  币种: {symbol}\n"
                        f"  投入: ${self.trade_amount:.2f}\n"
                        f"  价格: ${result['price']:.2f}\n"
                        f"  📦 长期持有，无止盈止损"
                    )
                else:
                    return "❌ 现货定投失败，请检查日志"

            except Exception as e:
                return f"❌ 现货定投异常: {str(e)}"

        trading_tools = TradingTools(
            buy_callback,
            sell_callback,
            sell_short_callback,
            buy_to_cover_callback,
            do_nothing_callback,
            buy_spot_callback  # 添加现货买入回调
        )
        return trading_tools.get_all_tools()

    def make_decision(
        self,
        symbol: str,
        market_data: Dict[str, Any],
        current_positions: list,
        max_positions: int
    ) -> Tuple[str, Dict[str, Any]]:
        """
        做出交易决策

        Args:
            symbol: 交易对
            market_data: 市场数据
            current_positions: 当前持仓
            max_positions: 最大持仓数

        Returns:
            (决策类型, 决策详情)
        """
        try:
            # 更新当前价格和交易对
            self.current_price = market_data.get('current_price', 0)
            self.current_symbol = symbol

            # 创建 Prompt
            prompt = create_trading_prompt(
                symbol=symbol,
                market_data=market_data,
                current_positions=current_positions,
                max_positions=max_positions
            )

            # 显示 Prompt
            self.logger.print_prompt(prompt)

            # 调用 Agent
            self.logger.print_section("AI Agent 思考过程", style="bold magenta")

            # 使用 LangGraph 的流式输出
            # 将系统消息作为第一条消息
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
                # 打印中间步骤
                if "messages" in event and len(event["messages"]) > 0:
                    last_message = event["messages"][-1]
                    if hasattr(last_message, 'content'):
                        content = last_message.content
                        if content and content != prompt and content != agent_output:
                            # 使用新的 AI 响应渲染方法（支持 Markdown）
                            self.logger.print_ai_response(content, "💭 AI 思考中...")
                            agent_output = content

            # 解析结果
            decision_type = self._parse_decision_from_events(all_events)
            decision_details = {
                "output": agent_output,
                "events": all_events,
                "prompt": prompt
            }

            return decision_type, decision_details

        except Exception as e:
            self.logger.print_error(f"Agent 决策异常: {e}")
            self.logger.logger.exception(e)
            return "ERROR", {"error": str(e)}

    def _parse_decision_from_events(self, events: list) -> str:
        """
        从 LangGraph 事件中解析决策类型

        Args:
            events: LangGraph 事件列表

        Returns:
            决策类型 (BUY, SELL, SELL_SHORT, BUY_TO_COVER, DO_NOTHING, ERROR)
        """
        try:
            # 遍历所有事件，查找工具调用
            for event in reversed(events):
                if "messages" not in event:
                    continue

                for message in reversed(event["messages"]):
                    # 检查是否是工具调用消息
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
                            elif tool_name == "do_nothing":
                                return "DO_NOTHING"

                    # 检查工具响应消息
                    if hasattr(message, 'name'):
                        if message.name == "buy":
                            return "BUY"
                        elif message.name == "sell":
                            return "SELL"
                        elif message.name == "sell_short":
                            return "SELL_SHORT"
                        elif message.name == "buy_to_cover":
                            return "BUY_TO_COVER"
                        elif message.name == "do_nothing":
                            return "DO_NOTHING"

            # 如果没有找到明确的工具调用，尝试从内容中判断
            for event in reversed(events):
                if "messages" not in event:
                    continue

                for message in reversed(event["messages"]):
                    if hasattr(message, 'content'):
                        content = str(message.content).lower()
                        if "sell_short" in content or "卖空" in content:
                            return "SELL_SHORT"
                        elif "buy_to_cover" in content or "平空" in content:
                            return "BUY_TO_COVER"
                        elif "buy" in content or "买入" in content:
                            return "BUY"
                        elif "sell" in content or "卖出" in content:
                            return "SELL"

            return "DO_NOTHING"

        except Exception as e:
            self.logger.logger.error(f"解析决策失败: {e}")
            return "ERROR"

    def make_batch_decision(
        self,
        symbols_data: List[Dict[str, Any]],
        current_positions: list,
        max_positions: int
    ) -> List[Tuple[str, str, Dict[str, Any]]]:
        """
        批量做出交易决策（一次性分析多个交易对）

        Args:
            symbols_data: 包含所有交易对数据的列表，每项包含 symbol, market_data, multi_timeframe_trends
            current_positions: 当前持仓
            max_positions: 最大持仓数

        Returns:
            决策列表，每项为 (symbol, decision_type, decision_details)
        """
        try:
            # 📝 添加详细日志：验证输入数据
            self.logger.logger.info(f"批量决策输入验证 - symbols_data 数量: {len(symbols_data)}")
            for i, data in enumerate(symbols_data):
                self.logger.logger.info(f"  数据 #{i}: type={type(data)}, keys={list(data.keys()) if isinstance(data, dict) else 'N/A'}")
                if isinstance(data, dict):
                    if 'symbol' in data:
                        self.logger.logger.info(f"    symbol={data['symbol']}")
                    else:
                        self.logger.logger.error(f"    ❌ 缺少 'symbol' 键!")
                    if 'market_data' in data:
                        market_data = data['market_data']
                        self.logger.logger.info(f"    market_data keys={list(market_data.keys()) if isinstance(market_data, dict) else 'N/A'}")
                    else:
                        self.logger.logger.error(f"    ❌ 缺少 'market_data' 键!")

            # 构建价格映射表，供 callback 使用
            self.price_map = {
                data['symbol']: data['market_data'].get('current_price', 0)
                for data in symbols_data
            }

            # 创建批量 Prompt
            prompt = create_batch_trading_prompt(
                symbols_data=symbols_data,
                current_positions=current_positions,
                max_positions=max_positions,
                current_time=datetime.now()
            )

            # 显示 Prompt
            self.logger.print_prompt(prompt)

            # 调用 Agent
            self.logger.print_section("AI Agent 批量分析", style="bold magenta")

            # 使用 LangGraph 的流式输出
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
                # 打印中间步骤
                if "messages" in event and len(event["messages"]) > 0:
                    last_message = event["messages"][-1]
                    if hasattr(last_message, 'content'):
                        content = last_message.content
                        if content and content != prompt and content != agent_output:
                            # 使用新的 AI 响应渲染方法（支持 Markdown）
                            self.logger.print_ai_response(content, "📊 批量分析中...")
                            agent_output = content

            # 解析每个交易对的决策
            decisions = self._parse_batch_decisions_from_events(all_events, symbols_data)

            return decisions

        except Exception as e:
            import traceback
            self.logger.print_error(f"批量决策异常: {e}")
            self.logger.print_error(f"异常类型: {type(e).__name__}")
            self.logger.logger.error(f"完整堆栈跟踪:\n{traceback.format_exc()}")
            self.logger.logger.exception(e)

            # 安全地返回 ERROR 决策（避免再次触发 KeyError）
            try:
                return [(data.get('symbol', 'UNKNOWN'), "ERROR", {"error": str(e)}) for data in symbols_data]
            except Exception as e2:
                self.logger.logger.error(f"构建错误响应失败: {e2}")
                return [("UNKNOWN", "ERROR", {"error": f"双重异常: {str(e)}, {str(e2)}"})]

    def _parse_batch_decisions_from_events(
        self,
        events: list,
        symbols_data: List[Dict[str, Any]]
    ) -> List[Tuple[str, str, Dict[str, Any]]]:
        """
        从批量决策的事件中解析每个交易对的决策

        Args:
            events: LangGraph 事件列表
            symbols_data: 交易对数据列表

        Returns:
            决策列表 [(symbol, decision_type, decision_details), ...]
        """
        try:
            # 收集所有工具调用
            tool_calls_by_symbol = {}

            for event in events:
                if "messages" not in event:
                    continue

                for message in event["messages"]:
                    # 检查工具调用消息
                    if hasattr(message, 'tool_calls') and message.tool_calls:
                        for tool_call in message.tool_calls:
                            tool_name = tool_call.get('name', '')
                            args = tool_call.get('args', {})

                            # 尝试从参数中获取 symbol
                            symbol = None
                            if 'symbol' in args:
                                symbol = args['symbol']
                            elif '__arg1' in args:
                                # do_nothing 工具可能将参数放在 __arg1 中
                                arg1 = args['__arg1']
                                # 从 reason 中尝试提取交易对
                                for data in symbols_data:
                                    if data['symbol'] in str(arg1):
                                        symbol = data['symbol']
                                        break

                            if symbol:
                                if tool_name == "buy":
                                    tool_calls_by_symbol[symbol] = "BUY"
                                elif tool_name == "sell":
                                    tool_calls_by_symbol[symbol] = "SELL"
                                elif tool_name == "sell_short":
                                    tool_calls_by_symbol[symbol] = "SELL_SHORT"
                                elif tool_name == "buy_to_cover":
                                    tool_calls_by_symbol[symbol] = "BUY_TO_COVER"
                                elif tool_name == "do_nothing":
                                    tool_calls_by_symbol[symbol] = "DO_NOTHING"

            # 构建决策列表
            decisions = []
            for data in symbols_data:
                symbol = data['symbol']
                decision_type = tool_calls_by_symbol.get(symbol, "DO_NOTHING")
                decision_details = {
                    "output": f"批量决策：{decision_type}",
                    "events": events,
                    "symbol": symbol
                }
                decisions.append((symbol, decision_type, decision_details))

            return decisions

        except Exception as e:
            self.logger.logger.error(f"解析批量决策失败: {e}")
            # 返回所有交易对的 DO_NOTHING 决策
            return [(data['symbol'], "DO_NOTHING", {"error": str(e)}) for data in symbols_data]
        