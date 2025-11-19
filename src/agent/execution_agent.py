"""
执行 Agent 模块

负责将单币种 Agent 的决策意图转换为实际的工具调用
使用 LangChain 的 structured output 机制来确保正确的工具调用
"""

from typing import Dict, Any, Optional
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field
from enum import Enum


class DecisionType(str, Enum):
    """决策类型枚举"""
    BUY = "BUY"
    SELL = "SELL"
    SELL_SHORT = "SELL_SHORT"
    BUY_TO_COVER = "BUY_TO_COVER"
    DO_NOTHING = "DO_NOTHING"
    BUY_SPOT = "BUY_SPOT"


class ExecutionPlan(BaseModel):
    """执行计划 - structured output 格式"""
    decision: DecisionType = Field(description="决策类型")
    symbol: str = Field(description="交易对符号")
    amount: Optional[float] = Field(default=None, description="交易金额（仅开仓时需要）")
    leverage: Optional[int] = Field(default=None, description="杠杆倍数（仅开仓时需要）")
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

重要规则：
- 从文本中准确提取交易对、金额、杠杆等参数
- 如果文本中没有明确指定金额或杠杆，设置为 None（使用默认值）
- 决策理由应简短明确，总结关键要点
- 如果文本中决策不明确，默认为 DO_NOTHING
"""


class ExecutionAgent:
    """执行 Agent - 负责将决策意图转换为工具调用"""

    def __init__(
        self,
        openai_api_base: str,
        openai_api_key: str,
        openai_model: str = "gpt-4",
        temperature: float = 0.0
    ):
        """
        初始化执行 Agent

        Args:
            openai_api_base: OpenAI API Base URL
            openai_api_key: OpenAI API Key
            openai_model: 模型名称
            temperature: 温度参数（建议使用 0 以确保确定性输出）
        """
        self.llm = ChatOpenAI(
            base_url=openai_api_base,
            api_key=openai_api_key,
            model=openai_model,
            temperature=temperature,
        )

        # 使用 structured output
        self.structured_llm = self.llm.with_structured_output(ExecutionPlan)

    def parse_decision(
        self,
        decision_text: str,
        symbol: str,
        logger=None
    ) -> ExecutionPlan:
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
                HumanMessage(content=prompt)
            ]

            execution_plan = self.structured_llm.invoke(messages)

            if logger:
                amount_str = f"{execution_plan.amount}" if execution_plan.amount is not None else "默认"
                leverage_str = f"{execution_plan.leverage}x" if execution_plan.leverage is not None else "默认"
                logger.print_info(
                    f"[ExecutionAgent] 解析决策: {execution_plan.decision.value} "
                    f"(金额: ${amount_str}, 杠杆: {leverage_str})"
                )

            return execution_plan

        except Exception as e:
            if logger:
                logger.print_error(f"[ExecutionAgent] 解析决策失败: {e}")

            # 返回默认的观望决策
            return ExecutionPlan(
                decision=DecisionType.DO_NOTHING,
                symbol=symbol,
                reason=f"解析失败: {str(e)}"
            )

    def execute_plan(
        self,
        execution_plan: ExecutionPlan,
        tools_callbacks: Dict[str, Any],
        logger=None
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
                callback = tools_callbacks.get('buy')
                if callback:
                    return callback(
                        symbol=symbol,
                        amount=execution_plan.amount,
                        leverage=execution_plan.leverage
                    )
                else:
                    return f"❌ 未找到 BUY 工具回调"

            elif decision == DecisionType.SELL:
                callback = tools_callbacks.get('sell')
                if callback:
                    return callback(symbol=symbol)
                else:
                    return f"❌ 未找到 SELL 工具回调"

            elif decision == DecisionType.SELL_SHORT:
                callback = tools_callbacks.get('sell_short')
                if callback:
                    return callback(
                        symbol=symbol,
                        amount=execution_plan.amount,
                        leverage=execution_plan.leverage
                    )
                else:
                    return f"❌ 未找到 SELL_SHORT 工具回调"

            elif decision == DecisionType.BUY_TO_COVER:
                callback = tools_callbacks.get('buy_to_cover')
                if callback:
                    return callback(symbol=symbol)
                else:
                    return f"❌ 未找到 BUY_TO_COVER 工具回调"

            elif decision == DecisionType.BUY_SPOT:
                callback = tools_callbacks.get('buy_spot')
                if callback:
                    return callback(
                        symbol=symbol,
                        amount=execution_plan.amount
                    )
                else:
                    return f"❌ 未找到 BUY_SPOT 工具回调"

            elif decision == DecisionType.DO_NOTHING:
                callback = tools_callbacks.get('do_nothing')
                if callback:
                    return callback(reason=execution_plan.reason)
                else:
                    return f"❌ 未找到 DO_NOTHING 工具回调"

            return f"❌ 未识别的决策类型: {decision}"

        except Exception as e:
            error_msg = f"❌ 执行计划失败: {str(e)}"
            if logger:
                logger.print_error(f"[ExecutionAgent] {error_msg}")
            return error_msg
