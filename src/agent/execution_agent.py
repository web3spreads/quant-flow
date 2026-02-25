"""
执行 Agent 模块

负责将单币种 Agent 的决策意图转换为实际的工具调用
使用 LangChain 的 structured output 机制来确保正确的工具调用
"""

import json
from enum import StrEnum
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from src.llm import LLMClientManager

# ExecutionPlan 字段名常量（避免硬编码字符串）
FIELD_DECISION = "decision"
FIELD_SYMBOL = "symbol"
FIELD_REASON = "reason"
FIELD_AMOUNT = "amount"
FIELD_LEVERAGE = "leverage"
FIELD_PRICE = "price"
FIELD_ORDER_ID = "order_id"


class DecisionType(StrEnum):
    """决策类型枚举"""

    BUY = "BUY"
    SELL = "SELL"
    SELL_SHORT = "SELL_SHORT"
    BUY_TO_COVER = "BUY_TO_COVER"
    DO_NOTHING = "DO_NOTHING"
    BUY_SPOT = "BUY_SPOT"
    BUY_LIMIT = "BUY_LIMIT"
    SELL_SHORT_LIMIT = "SELL_SHORT_LIMIT"
    CANCEL_LIMIT_ORDER = "CANCEL_LIMIT_ORDER"


class ExecutionPlan(BaseModel):
    """执行计划 - structured output 格式"""

    decision: DecisionType = Field(description="决策类型")
    symbol: str = Field(description="交易对符号")
    amount: float | None = Field(default=None, description="交易金额（仅开仓时需要）")
    leverage: int | None = Field(default=None, description="杠杆倍数（仅开仓时需要）")
    price: float | None = Field(default=None, description="限价单价格（仅限价单时需要）")
    order_id: int | None = Field(default=None, description="订单ID（仅取消限价单时需要）")
    reason: str = Field(description="决策理由的简短摘要")


EXECUTION_AGENT_SYSTEM_PROMPT = """你是一个交易执行专家，负责解析交易决策并制定执行计划。

你的任务：
1. 读取交易 Agent 的决策分析文本
2. 识别决策意图（开多、平多、开空、平空、现货买入、观望）
3. 提取关键参数（交易对、金额、杠杆等）
4. 输出结构化的执行计划

决策类型映射：
- BUY (开多/买入开多) → 调用 buy() 工具
- SELL (平多/卖出平多) → 调用 sell() 工具
- SELL_SHORT (开空/卖空开空) → 调用 sell_short() 工具
- BUY_TO_COVER (平空/买入平空/CLOSE) → 调用 buy_to_cover() 工具
- DO_NOTHING (观望/HOLD/不操作) → 调用 do_nothing() 工具
- BUY_SPOT (现货买入/定投) → 调用 buy_spot() 工具
- BUY_LIMIT (限价开多) → 调用 buy_limit() 工具，需要提供 price 字段
- SELL_SHORT_LIMIT (限价开空) → 调用 sell_short_limit() 工具，需要提供 price 字段
- CANCEL_LIMIT_ORDER (取消限价单) → 调用 cancel_limit_order() 工具，需要提供 order_id 字段

重要规则：
- 从文本中准确提取交易对、金额、杠杆等参数
- 如果文本中没有明确指定金额或杠杆，设置为 null
- 决策理由应简短明确，总结关键要点
- 如果文本中决策不明确，默认为 DO_NOTHING

🚨 输出格式要求（极其重要）：
- 你的输出必须是纯 JSON 对象
- 直接以 { 开始，以 } 结束
- 不要添加任何前缀或后缀文字
- 不要使用 Markdown 代码块（```json```）

JSON 输出格式示例：
{
  "decision": "BUY",
  "symbol": "DOGE",
  "amount": 100.0,
  "leverage": 5,
  "reason": "技术指标显示多头信号强烈"
}

或者（观望示例）：
{
  "decision": "DO_NOTHING",
  "symbol": "DOGE",
  "amount": null,
  "leverage": null,
  "reason": "市场趋势不明确，等待更好的入场点"
}
"""


def _extract_json_from_text(text: str) -> dict | None:
    """
    从文本中提取 JSON 对象（模块级辅助函数）

    支持以下格式：
    1. Markdown 代码块: ```json {...} ```
    2. 纯 JSON: {...}
    3. 带有文本的混合内容（即 JSON 对象嵌入在其他文本中）

    Args:
        text: 包含 JSON 对象的文本

    Returns:
        提取的 JSON 对象（字典），如果提取失败则返回 None

    Examples:
        >>> _extract_json_from_text('```json\\n{"key": "value"}\\n```')
        {'key': 'value'}
        >>> _extract_json_from_text('Some text {"key": "value"} more text')
        {'key': 'value'}
    """
    # 方法 1: 尝试提取 markdown 代码块中的 JSON（使用字符串查找，不使用正则）
    code_block_markers = [
        ("```json", "```"),
        ("```", "```"),
    ]
    for start_marker, end_marker in code_block_markers:
        start_idx = text.find(start_marker)
        if start_idx != -1:
            start_idx += len(start_marker)
            end_idx = text.find(end_marker, start_idx)
            if end_idx != -1:
                code_content = text[start_idx:end_idx].strip()
                try:
                    return json.loads(code_content)
                except json.JSONDecodeError:
                    continue

    # 方法 2: 提取第一个平衡的 JSON 对象
    start = text.find("{")
    if start != -1:
        stack = []
        for i in range(start, len(text)):
            if text[i] == "{":
                stack.append("{")
            elif text[i] == "}":
                if stack:
                    stack.pop()
                if not stack:
                    json_str = text[start : i + 1]
                    try:
                        return json.loads(json_str)
                    except json.JSONDecodeError:
                        break

    return None


class ExecutionAgent:
    """执行 Agent - 负责将决策意图转换为工具调用"""

    def __init__(self, llm_manager: LLMClientManager, temperature: float = 0.0):
        """
        初始化执行 Agent

        Args:
            llm_manager: LLM 客户端管理器
            temperature: 温度参数（建议使用 0 以确保确定性输出）
        """
        self.llm_manager = llm_manager
        self.temperature = temperature

        # 获取启用 JSON Mode 的 LLM 客户端
        # JSON Mode 可以显著提高 structured output 的成功率
        self.llm = self.llm_manager.get_client(json_mode=True, temperature=temperature)

        # 使用 structured output
        self.structured_llm = self.llm_manager.get_structured_client(
            output_schema=ExecutionPlan, temperature=temperature
        )

    def parse_decision(self, decision_text: str, symbol: str, logger=None) -> ExecutionPlan:
        """
        解析决策文本并生成执行计划

        Args:
            decision_text: Agent 的决策文本
            symbol: 交易对
            logger: 日志记录器（可选）

        Returns:
            ExecutionPlan: 结构化的执行计划
        """
        try:
            # 预检查：确保决策文本不为空
            if not decision_text or not decision_text.strip():
                error_msg = "决策文本为空，无法解析"
                if logger:
                    logger.print_error(f"[ExecutionAgent] {error_msg}")
                return ExecutionPlan(
                    decision=DecisionType.DO_NOTHING, symbol=symbol, reason=error_msg
                )

            if logger:
                logger.print_info(
                    f"[ExecutionAgent] 开始解析决策文本（长度: {len(decision_text)} 字符）"
                )

            prompt = f"""
请分析以下交易决策文本，提取关键信息并生成执行计划。

交易对: {symbol}

决策文本:
{decision_text}

请识别：
1. 决策类型（BUY/SELL/SELL_SHORT/BUY_TO_COVER/DO_NOTHING/BUY_SPOT/BUY_LIMIT/SELL_SHORT_LIMIT/CANCEL_LIMIT_ORDER）
2. 交易金额（如果提到，限价单也需要）
3. 杠杆倍数（如果提到，限价单也需要）
4. 限价价格（如果是限价单，必须提供 price 字段）
5. 订单ID（如果是取消限价单，必须提供 order_id 字段）
6. 决策理由摘要

注意：
- 仔细区分"开多/买入"(BUY)和"平空/买入平空/CLOSE"(BUY_TO_COVER)
- 仔细区分"平多/卖出"(SELL)和"开空/卖空"(SELL_SHORT)
- 如果文本中说"决策: CLOSE"或"平空"，应该是 BUY_TO_COVER
- 限价单（BUY_LIMIT/SELL_SHORT_LIMIT）必须提供 price 字段
- 取消限价单（CANCEL_LIMIT_ORDER）必须提供 order_id 字段
- 如果金额、杠杆、价格或订单ID没有明确提到，设置为 null
"""

            messages = [
                SystemMessage(content=EXECUTION_AGENT_SYSTEM_PROMPT),
                HumanMessage(content=prompt),
            ]

            # 增强错误处理：捕获 structured output 调用并记录详细信息
            try:
                execution_plan = self.structured_llm.invoke(messages)
            except Exception as structured_error:
                # 使用 warning 级别，因为后备方案能处理
                if logger:
                    logger.print_warning(
                        f"⚠️  [ExecutionAgent] Structured output 需要后备方案: {structured_error}"
                    )
                    logger.print_info(f"[ExecutionAgent] 决策文本长度: {len(decision_text)} 字符")

                # 尝试使用普通 LLM 调用作为后备方案
                if logger:
                    logger.print_info("[ExecutionAgent] 使用兼容模式解析决策")

                try:
                    response = self.llm.invoke(messages)
                    response_content = (
                        response.content if hasattr(response, "content") else str(response)
                    )

                    if logger:
                        max_log_len = 2000
                        truncated = "..." if len(response_content) > max_log_len else ""
                        logger.print_info(
                            f"[ExecutionAgent] LLM 原始响应（完整）: {response_content[:max_log_len]}{truncated} (总长度: {len(response_content)} 字符)"
                        )

                    # 尝试提取并解析 JSON（使用模块级辅助函数）
                    parsed_data = _extract_json_from_text(response_content)

                    if parsed_data:
                        # 验证并补全必需字段（使用字段名常量避免硬编码）
                        if FIELD_DECISION not in parsed_data:
                            if logger:
                                logger.print_warning(
                                    f"[ExecutionAgent] 响应中缺少 '{FIELD_DECISION}' 字段，使用默认值 DO_NOTHING"
                                )
                            parsed_data[FIELD_DECISION] = DecisionType.DO_NOTHING.value

                        if FIELD_SYMBOL not in parsed_data:
                            if logger:
                                logger.print_warning(
                                    f"[ExecutionAgent] 响应中缺少 '{FIELD_SYMBOL}' 字段，使用传入的 symbol: {symbol}"
                                )
                            parsed_data[FIELD_SYMBOL] = symbol

                        if FIELD_REASON not in parsed_data:
                            if logger:
                                logger.print_warning(
                                    f"[ExecutionAgent] 响应中缺少 '{FIELD_REASON}' 字段，使用默认值"
                                )
                            parsed_data[FIELD_REASON] = "AI 决策解析不完整"

                        try:
                            execution_plan = ExecutionPlan(**parsed_data)
                            if logger:
                                logger.print_info("[ExecutionAgent] 后备方案成功：手动解析 JSON")
                        except Exception as validation_error:
                            if logger:
                                logger.print_warning(
                                    f"[ExecutionAgent] JSON 解析后字段验证失败，返回默认决策: {validation_error}"
                                )
                            execution_plan = ExecutionPlan(
                                decision=DecisionType.DO_NOTHING,
                                symbol=symbol,
                                reason="字段验证失败，无法解析 AI 响应格式",
                            )
                    else:
                        # 无法提取 JSON，返回默认的 DO_NOTHING 决策
                        if logger:
                            logger.print_warning(
                                "[ExecutionAgent] 无法从响应中提取有效的 JSON，返回默认决策"
                            )
                        execution_plan = ExecutionPlan(
                            decision=DecisionType.DO_NOTHING,
                            symbol=symbol,
                            reason=f"无法解析 AI 响应格式 (响应长度: {len(response_content)}，预览: {response_content[:200]}{'...' if len(response_content) > 200 else ''})",
                        )

                except Exception as fallback_error:
                    if logger:
                        logger.print_error(f"[ExecutionAgent] 后备方案也失败: {fallback_error}")
                        import traceback

                        logger.print_error(
                            f"[ExecutionAgent] 后备异常堆栈:\n{traceback.format_exc()}"
                        )

                    # 返回默认的 DO_NOTHING 决策，而不是重新抛出异常
                    execution_plan = ExecutionPlan(
                        decision=DecisionType.DO_NOTHING,
                        symbol=symbol,
                        reason=f"后备方案失败: {str(fallback_error)}",
                    )

            if logger:
                amount_str = (
                    f"{execution_plan.amount}" if execution_plan.amount is not None else "默认"
                )
                leverage_str = (
                    f"{execution_plan.leverage}x" if execution_plan.leverage is not None else "默认"
                )
                logger.print_info(
                    f"[ExecutionAgent] 解析决策: {execution_plan.decision.value} "
                    f"(金额: ${amount_str}, 杠杆: {leverage_str})"
                )

            return execution_plan

        except Exception as e:
            if logger:
                logger.print_error(f"[ExecutionAgent] 解析决策失败: {e}")
                # 记录完整的异常堆栈
                import traceback

                logger.print_error(f"[ExecutionAgent] 异常堆栈:\n{traceback.format_exc()}")

            # 返回默认的观望决策
            return ExecutionPlan(
                decision=DecisionType.DO_NOTHING, symbol=symbol, reason=f"解析失败: {str(e)}"
            )

    def execute_plan(
        self, execution_plan: ExecutionPlan, tools_callbacks: dict[str, Any], logger=None
    ) -> str:
        """
        执行计划 - 调用相应的工具函数

        Args:
            execution_plan: 执行计划
            tools_callbacks: 工具回调函数字典
            logger: 日志记录器

        Returns:
            执行结果描述
        """
        try:
            decision = execution_plan.decision
            symbol = execution_plan.symbol

            if decision == DecisionType.BUY:
                callback = tools_callbacks.get("buy")
                if callback:
                    return callback(
                        symbol=symbol,
                        amount=execution_plan.amount,
                        leverage=execution_plan.leverage,
                    )
                else:
                    return "❌ 未找到 BUY 工具回调"

            elif decision == DecisionType.SELL:
                callback = tools_callbacks.get("sell")
                if callback:
                    return callback(symbol=symbol)
                else:
                    return "❌ 未找到 SELL 工具回调"

            elif decision == DecisionType.SELL_SHORT:
                callback = tools_callbacks.get("sell_short")
                if callback:
                    return callback(
                        symbol=symbol,
                        amount=execution_plan.amount,
                        leverage=execution_plan.leverage,
                    )
                else:
                    return "❌ 未找到 SELL_SHORT 工具回调"

            elif decision == DecisionType.BUY_TO_COVER:
                callback = tools_callbacks.get("buy_to_cover")
                if callback:
                    return callback(symbol=symbol)
                else:
                    return "❌ 未找到 BUY_TO_COVER 工具回调"

            elif decision == DecisionType.BUY_SPOT:
                callback = tools_callbacks.get("buy_spot")
                if callback:
                    return callback(symbol=symbol, amount=execution_plan.amount)
                else:
                    return "❌ 未找到 BUY_SPOT 工具回调"

            elif decision == DecisionType.DO_NOTHING:
                callback = tools_callbacks.get("do_nothing")
                if callback:
                    return callback(reason=execution_plan.reason)
                else:
                    return "❌ 未找到 DO_NOTHING 工具回调"

            elif decision == DecisionType.BUY_LIMIT:
                callback = tools_callbacks.get("buy_limit")
                if callback:
                    return callback(
                        symbol=symbol,
                        amount=execution_plan.amount,
                        leverage=execution_plan.leverage,
                        price=execution_plan.price,
                    )
                else:
                    return "❌ 未找到 BUY_LIMIT 工具回调"

            elif decision == DecisionType.SELL_SHORT_LIMIT:
                callback = tools_callbacks.get("sell_short_limit")
                if callback:
                    return callback(
                        symbol=symbol,
                        amount=execution_plan.amount,
                        leverage=execution_plan.leverage,
                        price=execution_plan.price,
                    )
                else:
                    return "❌ 未找到 SELL_SHORT_LIMIT 工具回调"

            elif decision == DecisionType.CANCEL_LIMIT_ORDER:
                callback = tools_callbacks.get("cancel_limit_order")
                if callback:
                    return callback(symbol=symbol, order_id=execution_plan.order_id)
                else:
                    return "❌ 未找到 CANCEL_LIMIT_ORDER 工具回调"

            return f"❌ 未识别的决策类型: {decision}"

        except Exception as e:
            error_msg = f"❌ 执行计划失败: {str(e)}"
            if logger:
                logger.print_error(f"[ExecutionAgent] {error_msg}")
            return error_msg
