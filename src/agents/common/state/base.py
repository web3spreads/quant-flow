"""
基础状态定义

提供 LangGraph 状态图的基础类型定义。
遵循 LangGraph 最佳实践：
- 状态对象保持小巧、显式、类型化
- 使用 reducer 辅助函数进行消息累积
- 瞬态值通过函数作用域传递，不存储在状态中
"""

from typing import TypedDict, List, Dict, Any, Optional, Annotated, Sequence
from langchain_core.messages import BaseMessage


def add_messages(
    left: Sequence[BaseMessage],
    right: Sequence[BaseMessage]
) -> List[BaseMessage]:
    """
    消息累积 reducer 函数

    用于在 LangGraph 状态中累积消息，避免覆盖历史消息。

    Args:
        left: 现有消息列表
        right: 新消息列表

    Returns:
        合并后的消息列表
    """
    return list(left) + list(right)


class MessageState(TypedDict):
    """
    消息状态 - 用于需要消息累积的 Agent

    使用 Annotated 类型配合 add_messages reducer，
    确保消息正确累积而非覆盖。
    """
    messages: Annotated[List[BaseMessage], add_messages]


class BaseAgentState(TypedDict):
    """
    基础 Agent 状态

    所有 Agent 状态的基类，包含通用字段。
    子类应扩展此类添加特定字段。

    设计原则：
    - 保持状态最小化，只存储必要数据
    - 使用类型注解确保类型安全
    - 瞬态数据不应存储在状态中
    """
    # 消息历史（使用 reducer 累积）
    messages: Annotated[List[BaseMessage], add_messages]

    # 交易对符号
    symbol: str

    # 时间戳
    timestamp: str

    # 错误信息列表
    errors: List[str]

    # 当前步骤/节点名称
    current_step: str


class MarketDataState(TypedDict):
    """
    市场数据状态

    存储市场相关的数据，供交易决策使用。
    """
    # 当前价格
    current_price: float

    # 多时间周期趋势
    multi_timeframe_trends: Dict[str, str]

    # 技术指标
    indicators: Dict[str, Any]

    # 市场情绪
    sentiment: Optional[str]


class PositionState(TypedDict):
    """
    持仓状态

    存储当前持仓信息。
    """
    # 当前持仓列表
    positions: List[Dict[str, Any]]

    # 最大持仓数量
    max_positions: int

    # 账户余额信息
    balance_info: Optional[Dict[str, float]]


class DecisionState(TypedDict):
    """
    决策状态

    存储交易决策相关信息。
    """
    # 决策类型
    decision_type: str

    # 决策详情
    decision_details: Dict[str, Any]

    # 决策理由
    reason: str

    # 置信度 (0-1)
    confidence: float


class ExecutionState(TypedDict):
    """
    执行状态

    存储工具执行相关信息。
    """
    # 执行计划
    execution_plan: Optional[Dict[str, Any]]

    # 执行结果
    execution_result: Optional[str]

    # 是否执行成功
    success: bool


class ReviewState(TypedDict):
    """
    复盘状态

    存储复盘分析相关信息。
    """
    # 决策记录
    decision_records: List[Dict[str, Any]]

    # 提取的经验教训
    lessons: List[Dict[str, Any]]

    # 复盘摘要
    summary: str

    # 上下文特征
    context_features: Dict[str, Any]
