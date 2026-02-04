"""
执行 Agent LangGraph 工作流

使用 StateGraph 实现的决策执行工作流。
工作流程：
1. parse_decision: 解析决策文本
2. execute_plan: 执行计划
"""

from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph

from src.agents.common.utils.helpers import extract_json_from_text
from src.agents.common.utils.llm import LLMConfig, create_json_llm
from src.agents.execution.state import (
    EXECUTION_AGENT_SYSTEM_PROMPT,
    DecisionType,
    ExecutionAgentState,
    ExecutionPlan,
    create_initial_state,
)
from src.llm import LLMClientManager


class ExecutionAgentWorkflow:
    """
    执行 Agent 工作流

    基于 LangGraph StateGraph 的决策执行工作流。
    将决策文本解析为结构化执行计划并执行。
    """

    def __init__(
        self,
        tools_callbacks: dict[str, Any],
        logger=None,
        llm_config: LLMConfig | None = None,
        llm_manager: LLMClientManager | None = None,
        temperature: float | None = 0.0,
    ):
        """
        初始化工作流

        Args:
            tools_callbacks: 工具回调函数字典
            logger: 日志记录器
            llm_config: LLM 配置（旧版，与 llm_manager 二选一）
            llm_manager: LLM 客户端管理器（新版，推荐使用）
            temperature: 温度参数覆盖（默认 0.0 以确保确定性）
        """
        self.tools_callbacks = tools_callbacks
        self.logger = logger
        self.llm_manager = llm_manager
        self.llm_config = llm_config

        # 初始化 LLM（优先使用 llm_manager）
        if llm_manager:
            self.llm = llm_manager.get_client(json_mode=True, temperature=temperature)
        elif llm_config:
            self.llm = create_json_llm(llm_config, temperature=temperature)
        else:
            raise ValueError("必须提供 llm_config 或 llm_manager")

        # 创建支持 structured output 的 LLM
        self.structured_llm = self.llm.with_structured_output(ExecutionPlan)

        # 构建工作流
        self.app = self._build_workflow()

    def _build_workflow(self) -> StateGraph:
        """
        构建 LangGraph 工作流

        工作流结构：
        start -> parse_decision -> execute_plan -> END
        """
        workflow = StateGraph(ExecutionAgentState)

        # 添加节点
        workflow.add_node("parse_decision", self._parse_decision_node)
        workflow.add_node("execute_plan", self._execute_plan_node)

        # 设置入口点
        workflow.set_entry_point("parse_decision")

        # 添加边
        workflow.add_conditional_edges(
            "parse_decision",
            self._route_after_parse,
            {
                "execute": "execute_plan",
                "end": END,
            },
        )
        workflow.add_edge("execute_plan", END)

        return workflow.compile()

    def _parse_decision_node(self, state: ExecutionAgentState) -> dict[str, Any]:
        """
        解析决策文本

        使用 LLM 将决策文本转换为结构化的执行计划。
        """
        decision_text = state["decision_text"]
        symbol = state["symbol"]

        if self.logger:
            self.logger.print_info(
                f"[ExecutionAgent] 开始解析决策文本（长度: {len(decision_text)} 字符）"
            )

        # 预检查
        if not decision_text or not decision_text.strip():
            return {
                "parsed_decision": DecisionType.DO_NOTHING.value,
                "execution_plan": {
                    "decision": DecisionType.DO_NOTHING.value,
                    "symbol": symbol,
                    "amount": None,
                    "leverage": None,
                    "reason": "决策文本为空",
                },
                "current_step": "parse_decision",
                "errors": state.get("errors", []) + ["决策文本为空"],
            }

        try:
            # 构建 Prompt
            prompt = f"""
请分析以下交易决策文本，提取关键信息并生成执行计划。

交易对: {symbol}

决策文本:
{decision_text}

请识别：
1. 决策类型（BUY/SELL/SELL_SHORT/BUY_TO_COVER/DO_NOTHING/BUY_SPOT）
2. 交易金额（如果提到）
3. 杠杆倍数（如果提到）
4. 决策理由摘要

注意：
- 仔细区分"开多/买入"(BUY)和"平空/买入平空/CLOSE"(BUY_TO_COVER)
- 仔细区分"平多/卖出"(SELL)和"开空/卖空"(SELL_SHORT)
- 如果文本中说"决策: CLOSE"或"平空"，应该是 BUY_TO_COVER
- 如果金额或杠杆没有明确提到，设置为 null
"""

            messages = [
                SystemMessage(content=EXECUTION_AGENT_SYSTEM_PROMPT),
                HumanMessage(content=prompt),
            ]

            # 尝试使用 structured output
            try:
                execution_plan = self.structured_llm.invoke(messages)
                plan_dict = execution_plan.model_dump()

                if self.logger:
                    self.logger.print_info(
                        f"[ExecutionAgent] 解析成功: {execution_plan.decision.value}"
                    )

                return {
                    "parsed_decision": execution_plan.decision.value,
                    "execution_plan": plan_dict,
                    "current_step": "parse_decision",
                }

            except Exception as structured_error:
                # 后备方案：手动解析 JSON
                if self.logger:
                    self.logger.print_warning(
                        f"[ExecutionAgent] Structured output 失败，使用后备方案: {structured_error}"
                    )

                response = self.llm.invoke(messages)
                response_content = (
                    response.content if hasattr(response, "content") else str(response)
                )

                parsed_data = extract_json_from_text(response_content)

                if parsed_data:
                    # 验证并补全必需字段
                    if "decision" not in parsed_data:
                        parsed_data["decision"] = DecisionType.DO_NOTHING.value
                    if "symbol" not in parsed_data:
                        parsed_data["symbol"] = symbol
                    if "reason" not in parsed_data:
                        parsed_data["reason"] = "AI 决策解析"

                    try:
                        execution_plan = ExecutionPlan(**parsed_data)
                        plan_dict = execution_plan.model_dump()

                        if self.logger:
                            self.logger.print_info(
                                f"[ExecutionAgent] 后备方案成功: {execution_plan.decision.value}"
                            )

                        return {
                            "parsed_decision": execution_plan.decision.value,
                            "execution_plan": plan_dict,
                            "current_step": "parse_decision",
                        }
                    except Exception as validation_error:
                        if self.logger:
                            self.logger.print_warning(
                                f"[ExecutionAgent] 字段验证失败: {validation_error}"
                            )

                # 无法解析，返回默认决策
                return {
                    "parsed_decision": DecisionType.DO_NOTHING.value,
                    "execution_plan": {
                        "decision": DecisionType.DO_NOTHING.value,
                        "symbol": symbol,
                        "amount": None,
                        "leverage": None,
                        "reason": "无法解析决策文本",
                    },
                    "current_step": "parse_decision",
                    "errors": state.get("errors", []) + ["无法解析决策文本"],
                }

        except Exception as e:
            error_msg = f"决策解析异常: {str(e)}"
            if self.logger:
                self.logger.print_error(f"[ExecutionAgent] {error_msg}")

            return {
                "parsed_decision": DecisionType.DO_NOTHING.value,
                "execution_plan": {
                    "decision": DecisionType.DO_NOTHING.value,
                    "symbol": symbol,
                    "amount": None,
                    "leverage": None,
                    "reason": error_msg,
                },
                "current_step": "error",
                "errors": state.get("errors", []) + [error_msg],
            }

    def _execute_plan_node(self, state: ExecutionAgentState) -> dict[str, Any]:
        """
        执行计划

        调用相应的工具回调函数执行交易。
        """
        plan = state.get("execution_plan", {})
        decision = plan.get("decision", DecisionType.DO_NOTHING.value)
        symbol = plan.get("symbol", state["symbol"])
        amount = plan.get("amount")
        leverage = plan.get("leverage")
        reason = plan.get("reason", "")

        if self.logger:
            self.logger.print_info(
                f"[ExecutionAgent] 执行计划: {decision} (金额: {amount}, 杠杆: {leverage})"
            )

        try:
            result = ""

            if decision == DecisionType.BUY.value:
                callback = self.tools_callbacks.get("buy")
                if callback:
                    result = callback(symbol=symbol, amount=amount, leverage=leverage)
                else:
                    result = "❌ 未找到 BUY 工具回调"

            elif decision == DecisionType.SELL.value:
                callback = self.tools_callbacks.get("sell")
                if callback:
                    result = callback(symbol=symbol)
                else:
                    result = "❌ 未找到 SELL 工具回调"

            elif decision == DecisionType.SELL_SHORT.value:
                callback = self.tools_callbacks.get("sell_short")
                if callback:
                    result = callback(symbol=symbol, amount=amount, leverage=leverage)
                else:
                    result = "❌ 未找到 SELL_SHORT 工具回调"

            elif decision == DecisionType.BUY_TO_COVER.value:
                callback = self.tools_callbacks.get("buy_to_cover")
                if callback:
                    result = callback(symbol=symbol)
                else:
                    result = "❌ 未找到 BUY_TO_COVER 工具回调"

            elif decision == DecisionType.BUY_SPOT.value:
                callback = self.tools_callbacks.get("buy_spot")
                if callback:
                    result = callback(symbol=symbol, amount=amount)
                else:
                    result = "❌ 未找到 BUY_SPOT 工具回调"

            elif decision == DecisionType.DO_NOTHING.value:
                callback = self.tools_callbacks.get("do_nothing")
                if callback:
                    result = callback(reason=reason)
                else:
                    result = f"⏸️ 不执行操作: {reason}"

            else:
                result = f"❌ 未识别的决策类型: {decision}"

            success = "✅" in result or "⏸️" in result

            if self.logger:
                self.logger.print_info(f"[ExecutionAgent] 执行结果: {result}")

            return {
                "execution_result": result,
                "success": success,
                "current_step": "execute_plan",
            }

        except Exception as e:
            error_msg = f"执行计划异常: {str(e)}"
            if self.logger:
                self.logger.print_error(f"[ExecutionAgent] {error_msg}")

            return {
                "execution_result": f"❌ {error_msg}",
                "success": False,
                "current_step": "error",
                "errors": state.get("errors", []) + [error_msg],
            }

    def _route_after_parse(self, state: ExecutionAgentState) -> Literal["execute", "end"]:
        """
        条件路由：决定是否执行计划
        """
        if state.get("current_step") == "error":
            return "end"

        # 有执行计划则执行
        if state.get("execution_plan"):
            return "execute"

        return "end"

    def run(
        self,
        decision_text: str,
        symbol: str,
    ) -> dict[str, Any]:
        """
        运行工作流

        Args:
            decision_text: 待解析的决策文本
            symbol: 交易对符号

        Returns:
            包含执行结果的字典
        """
        initial_state = create_initial_state(
            decision_text=decision_text,
            symbol=symbol,
        )

        final_state = self.app.invoke(initial_state)

        return {
            "decision_type": final_state.get("parsed_decision", "DO_NOTHING"),
            "execution_plan": final_state.get("execution_plan"),
            "execution_result": final_state.get("execution_result", ""),
            "success": final_state.get("success", False),
            "errors": final_state.get("errors", []),
        }
