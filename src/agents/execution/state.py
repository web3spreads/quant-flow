"""
执行 Agent 状态定义

定义 ExecutionAgent 在 LangGraph 工作流中使用的状态类型。
"""

from enum import StrEnum
from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage
from pydantic import BaseModel, Field

from src.agents.common.state.base import add_messages


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


# ExecutionPlan 字段名常量（避免硬编码字符串）
FIELD_DECISION = "decision"
FIELD_SYMBOL = "symbol"
FIELD_REASON = "reason"
FIELD_AMOUNT = "amount"
FIELD_LEVERAGE = "leverage"
FIELD_PRICE = "price"
FIELD_ORDER_ID = "order_id"


class ExecutionPlan(BaseModel):
    """
    执行计划

    使用 Pydantic 模型定义，支持 LangChain 的 structured output。
    """

    decision: DecisionType = Field(description="决策类型")
    symbol: str = Field(description="交易对符号")
    amount: float | None = Field(default=None, description="交易金额（仅开仓时需要）")
    leverage: int | None = Field(default=None, description="杠杆倍数（仅开仓时需要）")
    price: float | None = Field(default=None, description="限价单价格（仅限价单时需要）")
    order_id: int | None = Field(default=None, description="订单ID（仅取消限价单时需要）")
    reason: str = Field(description="决策理由的简短摘要")


class ExecutionAgentState(TypedDict):
    """
    执行 Agent 状态

    包含决策解析和执行所需的所有数据。
    """

    # ===== 消息历史 =====
    messages: Annotated[list[BaseMessage], add_messages]

    # ===== 输入数据 =====
    decision_text: str  # 待解析的决策文本
    symbol: str  # 交易对符号

    # ===== 解析结果 =====
    execution_plan: dict[str, Any] | None  # 解析后的执行计划
    parsed_decision: str  # 解析出的决策类型

    # ===== 执行结果 =====
    execution_result: str  # 执行结果描述
    success: bool  # 是否执行成功

    # ===== 工作流控制 =====
    current_step: str  # 当前步骤
    errors: list[str]  # 错误信息列表


# 执行 Agent 系统 Prompt
EXECUTION_AGENT_SYSTEM_PROMPT = """你是一个交易执行专家，负责解析交易决策并制定执行计划。

你的任务：
1. 读取交易 Agent 的决策分析文本
2. 识别决策意图（开多、平多、开空、平空、现货买入、限价单、观望）
3. 提取关键参数（交易对、金额、杠杆、限价价格等）
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
- 限价单（BUY_LIMIT/SELL_SHORT_LIMIT）必须提供 price 字段
- 取消限价单（CANCEL_LIMIT_ORDER）必须提供 order_id 字段
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


def create_initial_state(
    decision_text: str,
    symbol: str,
) -> ExecutionAgentState:
    """
    创建初始状态

    Args:
        decision_text: 待解析的决策文本
        symbol: 交易对符号

    Returns:
        初始化的执行 Agent 状态
    """
    return ExecutionAgentState(
        messages=[],
        decision_text=decision_text,
        symbol=symbol,
        execution_plan=None,
        parsed_decision="",
        execution_result="",
        success=False,
        current_step="start",
        errors=[],
    )
