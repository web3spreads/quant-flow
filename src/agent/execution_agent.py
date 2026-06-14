"""
执行 Agent 模块

负责将单币种 Agent 的决策意图转换为实际的工具调用
使用 Pydantic AI 的 structured output 机制来确保正确的工具调用
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from src.llm import LLMClientManager
from src.llm.llm_client import wrap_llm_client

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
2. 识别决策意图（开多、平多、开空、平空、观望）
3. 提取关键参数（交易对、金额、杠杆等）
4. 输出结构化的执行计划

决策类型映射：
- BUY (开多/买入开多) → 调用 buy() 工具
- SELL (平多/卖出平多) → 调用 sell() 工具
- SELL_SHORT (开空/卖空开空) → 调用 sell_short() 工具
- BUY_TO_COVER (平空/买入平空/CLOSE) → 调用 buy_to_cover() 工具
- DO_NOTHING (观望/HOLD/不操作) → 调用 do_nothing() 工具
- BUY_LIMIT (限价开多) → 调用 buy_limit() 工具，需要提供 price 字段
- SELL_SHORT_LIMIT (限价开空) → 调用 sell_short_limit() 工具，需要提供 price 字段
- CANCEL_LIMIT_ORDER (取消限价单) → 调用 cancel_limit_order() 工具，需要提供 order_id 字段

重要规则：
- 从文本中准确提取交易对、金额、杠杆等参数
- 如果文本中没有明确指定金额或杠杆，设置为 null
- 决策理由应简短明确，总结关键要点
- 如果文本中决策不明确，默认为 DO_NOTHING

🚨 输出格式要求（极其重要）：
- 你的输出必须是纯 JSON 对象，符合指定的 JSON Schema
- 决策类型必须映射为 BUY、SELL、SELL_SHORT、BUY_TO_COVER、DO_NOTHING、BUY_LIMIT、SELL_SHORT_LIMIT 或 CANCEL_LIMIT_ORDER 中的一个
"""


class ExecutionAgent:
    """执行 Agent - 负责将 LLM 决策文本解析为结构化的执行计划，并执行相应的工具调用"""

    def __init__(self, llm_manager: LLMClientManager, temperature: float = 0.1):
        """
        初始化执行 Agent

        Args:
            llm_manager: LLM 客户端管理器
            temperature: 温度参数。执行 Agent 仅将决策文本解析为结构化执行计划，
                应使用低温（默认 0.1）以保证结构化输出的确定性与稳定性，避免高温
                引入解析随机性导致执行计划抖动。
        """
        self.llm_manager = llm_manager
        # 获取支持 structured output 的 LLM 客户端（Pydantic AI 兼容 Model）
        self.llm = wrap_llm_client(
            self.llm_manager.get_client(json_mode=True, temperature=temperature)
        )

    def parse_decision(self, decision_text: str, symbol: str, logger=None) -> ExecutionPlan:
        """
        解析单币种 Agent 的决策分析文本，生成结构化的执行计划

        Args:
            decision_text: 决策分析文本
            symbol: 交易对符号
            logger: 日志记录器

        Returns:
            ExecutionPlan 结构化对象
        """
        try:
            # 增强型清洗：去除可能包含的多余前缀/后缀，提取纯文本
            cleaned_text = decision_text.strip()
            prompt = (
                f"以下是交易决策文本，请将其转换为结构化的执行计划：\n\n"
                f"--- 决策文本开始 ---\n"
                f"{cleaned_text}\n"
                f"--- 决策文本结束 ---\n\n"
                f"默认交易对: {symbol}\n"
                f"注意提取可能出现的：\n"
                f"1. 交易对符号\n"
                f"2. 交易金额（如果提到，限价单也需要）\n"
                f"3. 杠杆倍数（如果提到，限价单也需要）\n"
                f"4. 限价价格（如果是限价单，必须提供 price 字段）\n"
                f"5. 订单ID（如果是取消限价单，必须提供 order_id 字段）\n"
                f"6. 决策理由摘要\n"
            )

            # 使用 Pydantic AI Agent 确保返回正确的 ExecutionPlan 架构
            agent = Agent(
                self.llm, system_prompt=EXECUTION_AGENT_SYSTEM_PROMPT, output_type=ExecutionPlan
            )

            result = agent.run_sync(prompt)
            execution_plan = result.output

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
