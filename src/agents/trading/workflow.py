"""
交易 Agent LangGraph 工作流

使用 StateGraph 实现的交易决策工作流。
工作流程：
1. prepare_prompt: 准备交易决策 Prompt
2. analyze_market: 调用 LLM 分析市场并决策
3. parse_decision: 解析决策结果
4. execute_trade: 执行交易（如果检测到工具调用）
5. fallback_execution: 后备执行（使用 ExecutionAgent）
"""

from typing import Any, Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import create_react_agent

from src.agents.common.utils.llm import LLMConfig, create_llm
from src.agents.trading.state import TradingAgentState
from src.llm import LLMClientManager
from src.prompt_manager import PromptManager


class TradingAgentWorkflow:
    """
    交易 Agent 工作流

    基于 LangGraph StateGraph 的交易决策工作流。
    采用条件分支处理不同的执行路径。
    """

    def __init__(
        self,
        prompt_manager: PromptManager,
        tools: list,
        tools_callbacks: dict[str, Any],
        logger=None,
        max_iterations: int = 5,
        llm_config: LLMConfig | None = None,
        llm_manager: LLMClientManager | None = None,
        temperature: float | None = None,
        notifier=None,
    ):
        """
        初始化工作流

        Args:
            prompt_manager: Prompt 管理器
            tools: 工具列表
            tools_callbacks: 工具回调函数字典
            logger: 日志记录器
            max_iterations: 最大迭代次数
            llm_config: LLM 配置（旧版，与 llm_manager 二选一）
            llm_manager: LLM 客户端管理器（新版，推荐使用）
            temperature: 温度参数覆盖
            notifier: 通知管理器（可选）
        """
        self.prompt_manager = prompt_manager
        self.tools = tools
        self.tools_callbacks = tools_callbacks
        self.logger = logger
        self.max_iterations = max_iterations
        self.llm_manager = llm_manager
        self.llm_config = llm_config
        self.notifier = notifier

        # 初始化 LLM（优先使用 llm_manager）
        if llm_manager:
            self.llm = llm_manager.get_client(temperature=temperature)
        elif llm_config:
            self.llm = create_llm(llm_config, temperature=temperature)
        else:
            raise ValueError("必须提供 llm_config 或 llm_manager")

        # 创建 ReAct Agent（用于工具调用）
        self.react_agent = create_react_agent(model=self.llm, tools=tools)

        # 获取系统 Prompt
        self.system_prompt = prompt_manager.get_system_prompt()

        # 构建工作流
        self.app = self._build_workflow()

    def _build_workflow(self) -> StateGraph:
        """
        构建 LangGraph 工作流

        工作流结构：
        start -> prepare_prompt -> analyze_market -> parse_decision
                                                        |
                                    +-------------------+-------------------+
                                    |                   |                   |
                                    v                   v                   v
                              tool_detected      no_tool_detected     error
                                    |                   |                   |
                                    v                   v                   v
                              execute_trade    fallback_execution        END
                                    |                   |
                                    +-------------------+
                                            |
                                            v
                                          END
        """
        workflow = StateGraph(TradingAgentState)

        # 添加节点
        workflow.add_node("prepare_prompt", self._prepare_prompt_node)
        workflow.add_node("analyze_market", self._analyze_market_node)
        workflow.add_node("parse_decision", self._parse_decision_node)
        workflow.add_node("execute_trade", self._execute_trade_node)
        workflow.add_node("fallback_execution", self._fallback_execution_node)

        # 设置入口点
        workflow.set_entry_point("prepare_prompt")

        # 添加边
        workflow.add_edge("prepare_prompt", "analyze_market")
        workflow.add_edge("analyze_market", "parse_decision")

        # 条件边：根据决策解析结果选择下一步
        workflow.add_conditional_edges(
            "parse_decision",
            self._route_after_parse,
            {
                "execute": "execute_trade",
                "fallback": "fallback_execution",
                "end": END,
            },
        )

        workflow.add_edge("execute_trade", END)
        workflow.add_edge("fallback_execution", END)

        return workflow.compile()

    def _prepare_prompt_node(self, state: TradingAgentState) -> dict[str, Any]:
        """
        准备交易决策 Prompt

        使用 PromptManager 生成完整的交易分析 Prompt。
        """
        if self.logger:
            self.logger.print_info(f"[{state['symbol']}] 准备交易决策 Prompt...")

        try:
            prompt = self.prompt_manager.format_trading_prompt(
                symbol=state["symbol"],
                market_data=state["market_data"],
                multi_timeframe_trends=state["multi_timeframe_trends"],
                current_positions=state["current_positions"],
                max_positions=state["max_positions"],
                max_trade_amount=state["trade_amount"],
                max_leverage=state["max_leverage"],
                take_profit_ratio=state["take_profit_ratio"],
                stop_loss_ratio=state["stop_loss_ratio"],
                historical_summary=state["historical_summary"],
                balance_info=state["balance_info"],
                enriched_data=state["enriched_data"],
            )

            if self.logger:
                self.logger.print_section(
                    f"[{state['symbol']}Agent] 独立决策分析", style="bold magenta"
                )
                self.logger.print_prompt(prompt)

            return {
                "prompt": prompt,
                "current_step": "prepare_prompt",
            }

        except Exception as e:
            error_msg = f"Prompt 准备失败: {str(e)}"
            if self.logger:
                self.logger.print_error(f"[{state['symbol']}] {error_msg}")
            if self.notifier:
                self.notifier.notify_error(
                    title=f"{state['symbol']} Prompt 准备失败",
                    error_message=str(e),
                    context=f"交易对: {state['symbol']}\n异常类型: {type(e).__name__}",
                )
            return {
                "errors": state.get("errors", []) + [error_msg],
                "current_step": "error",
            }

    def _analyze_market_node(self, state: TradingAgentState) -> dict[str, Any]:
        """
        分析市场并做出决策

        使用 ReAct Agent 进行市场分析，支持工具调用。
        """
        if self.logger:
            self.logger.print_info(f"[{state['symbol']}] 使用 ReAct Agent 分析市场...")

        try:
            # 构建消息
            messages = [
                SystemMessage(content=self.system_prompt),
                HumanMessage(content=state["prompt"]),
            ]

            # 配置递归限制
            config = {"recursion_limit": self.max_iterations * 2}

            # 收集所有事件
            all_events = []
            agent_output = ""
            last_printed_content = ""

            for event in self.react_agent.stream(
                {"messages": messages}, stream_mode="values", config=config
            ):
                all_events.append(event)
                if "messages" in event and len(event["messages"]) > 0:
                    last_message = event["messages"][-1]
                    if hasattr(last_message, "content"):
                        content = last_message.content
                        if content and content != state["prompt"]:
                            agent_output = content

                        # 流式输出（避免重复打印）
                        if (
                            content
                            and content != state["prompt"]
                            and len(content) > len(last_printed_content)
                        ):
                            if self.logger:
                                self.logger.print_ai_response(
                                    content, f"🎯 {state['symbol']} Agent 分析中..."
                                )
                            last_printed_content = content

            return {
                "messages": [AIMessage(content=agent_output)],
                "decision_details": {
                    "output": agent_output,
                    "events": all_events,
                    "prompt": state["prompt"],
                    "symbol": state["symbol"],
                },
                "current_step": "analyze_market",
            }

        except Exception as e:
            error_msg = f"市场分析失败: {str(e)}"
            if self.logger:
                self.logger.print_error(f"[{state['symbol']}] {error_msg}")
            if self.notifier:
                self.notifier.notify_error(
                    title=f"{state['symbol']} LLM 市场分析失败",
                    error_message=str(e),
                    context=(
                        f"交易对: {state['symbol']}\n"
                        f"异常类型: {type(e).__name__}\n"
                        f"阶段: ReAct Agent 市场分析\n"
                        f"说明: LLM API 调用异常，本轮决策将降级为观望"
                    ),
                )
            return {
                "errors": state.get("errors", []) + [error_msg],
                "current_step": "error",
            }

    def _parse_decision_node(self, state: TradingAgentState) -> dict[str, Any]:
        """
        解析决策结果

        从 ReAct Agent 的输出中提取决策类型。
        优先从工具调用中解析，如果没有则标记需要后备执行。
        """
        if self.logger:
            self.logger.print_info(f"[{state['symbol']}] 解析决策结果...")

        try:
            events = state["decision_details"].get("events", [])
            decision_type = self._parse_decision_from_events(events)

            if decision_type and decision_type != "DO_NOTHING":
                # 从工具调用中成功解析出决策
                return {
                    "decision_type": decision_type,
                    "should_use_execution_agent": False,
                    "current_step": "parse_decision",
                }
            else:
                # 需要使用后备执行 Agent
                return {
                    "decision_type": "PENDING",
                    "should_use_execution_agent": True,
                    "current_step": "parse_decision",
                }

        except Exception as e:
            error_msg = f"决策解析失败: {str(e)}"
            if self.logger:
                self.logger.print_error(f"[{state['symbol']}] {error_msg}")
            if self.notifier:
                self.notifier.notify_error(
                    title=f"{state['symbol']} 决策解析失败",
                    error_message=str(e),
                    context=(
                        f"交易对: {state['symbol']}\n"
                        f"异常类型: {type(e).__name__}\n"
                        f"阶段: 决策结果解析"
                    ),
                )
            return {
                "decision_type": "ERROR",
                "errors": state.get("errors", []) + [error_msg],
                "current_step": "error",
            }

    def _execute_trade_node(self, state: TradingAgentState) -> dict[str, Any]:
        """
        执行交易

        工具已经在 ReAct Agent 中被调用，这里记录执行结果。
        """
        if self.logger:
            self.logger.print_info(f"[{state['symbol']}] 交易已执行: {state['decision_type']}")

        return {
            "execution_result": f"交易决策 {state['decision_type']} 已通过工具调用执行",
            "current_step": "execute_trade",
        }

    def _fallback_execution_node(self, state: TradingAgentState) -> dict[str, Any]:
        """
        后备执行节点

        当 ReAct Agent 未能直接调用工具时，
        使用 ExecutionAgent 解析决策文本并执行。
        """
        if self.logger:
            self.logger.print_info(f"[{state['symbol']}] 使用后备执行 Agent...")

        try:
            # 导入 ExecutionAgent（延迟导入避免循环依赖）
            from src.agents.common.utils.llm import LLMConfig
            from src.agents.execution import ExecutionAgentWorkflow

            # 获取 AI 输出文本
            decision_text = state["decision_details"].get("output", "")
            if not decision_text:
                return {
                    "decision_type": "DO_NOTHING",
                    "execution_result": "未检测到有效的决策文本",
                    "current_step": "fallback_execution",
                }

            # 创建执行 Agent 工作流
            # 优先使用 llm_manager，否则使用 llm_config
            if self.llm_manager:
                execution_workflow = ExecutionAgentWorkflow(
                    tools_callbacks=self.tools_callbacks,
                    logger=self.logger,
                    llm_manager=self.llm_manager,
                    temperature=0.0,  # 执行 Agent 使用零温度
                    notifier=self.notifier,
                )
            elif self.llm_config:
                execution_config = LLMConfig(
                    api_base=self.llm_config.api_base,
                    api_key=self.llm_config.api_key,
                    model=self.llm_config.model,
                    temperature=0.0,  # 执行 Agent 使用零温度
                )
                execution_workflow = ExecutionAgentWorkflow(
                    llm_config=execution_config,
                    tools_callbacks=self.tools_callbacks,
                    logger=self.logger,
                    notifier=self.notifier,
                )
            else:
                raise ValueError("无法创建执行 Agent：缺少 LLM 配置")

            # 执行
            result = execution_workflow.run(
                decision_text=decision_text,
                symbol=state["symbol"],
            )

            decision_type = result.get("decision_type", "DO_NOTHING")
            execution_result = result.get("execution_result", "")

            if self.logger:
                self.logger.print_info(f"[{state['symbol']}] 后备执行结果: {decision_type}")

            return {
                "decision_type": decision_type,
                "execution_result": execution_result,
                "current_step": "fallback_execution",
            }

        except Exception as e:
            error_msg = f"后备执行失败: {str(e)}"
            if self.logger:
                self.logger.print_error(f"[{state['symbol']}] {error_msg}")
            if self.notifier:
                self.notifier.notify_error(
                    title=f"{state['symbol']} 后备执行失败",
                    error_message=str(e),
                    context=(
                        f"交易对: {state['symbol']}\n"
                        f"异常类型: {type(e).__name__}\n"
                        f"阶段: ExecutionAgent 后备执行\n"
                        f"说明: LLM 决策执行异常，本轮决策将降级为观望"
                    ),
                )
            return {
                "decision_type": "ERROR",
                "errors": state.get("errors", []) + [error_msg],
                "current_step": "error",
            }

    def _route_after_parse(self, state: TradingAgentState) -> Literal["execute", "fallback", "end"]:
        """
        条件路由：根据决策解析结果选择下一步

        Returns:
            "execute": 已检测到工具调用，记录执行结果
            "fallback": 需要使用后备执行 Agent
            "end": 出错或无需进一步处理
        """
        if state.get("current_step") == "error":
            return "end"

        if state.get("should_use_execution_agent", False):
            return "fallback"

        # 已经通过工具调用执行
        return "execute"

    def _parse_decision_from_events(self, events: list) -> str | None:
        """
        从事件中解析决策类型

        优先从工具调用中解析。

        Args:
            events: LangGraph 事件列表

        Returns:
            决策类型字符串或 None
        """
        tool_decision_map = {
            "buy": "BUY",
            "sell": "SELL",
            "sell_short": "SELL_SHORT",
            "buy_to_cover": "BUY_TO_COVER",
            "buy_spot": "BUY_SPOT_RECOMMEND",
            "do_nothing": "DO_NOTHING",
        }

        for event in reversed(events):
            if "messages" not in event:
                continue

            for message in reversed(event["messages"]):
                # 检查 tool_calls 属性
                if hasattr(message, "tool_calls") and message.tool_calls:
                    for tool_call in message.tool_calls:
                        tool_name = tool_call.get("name", "")
                        if tool_name in tool_decision_map:
                            return tool_decision_map[tool_name]

                # 检查 name 属性（工具消息）
                if hasattr(message, "name") and message.name in tool_decision_map:
                    return tool_decision_map[message.name]

        return None

    def run(self, initial_state: TradingAgentState) -> dict[str, Any]:
        """
        运行工作流

        Args:
            initial_state: 初始状态

        Returns:
            最终状态
        """
        final_state = self.app.invoke(initial_state)
        return final_state
