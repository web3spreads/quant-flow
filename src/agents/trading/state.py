"""
交易 Agent 状态定义

定义 TradingAgent 在 LangGraph 工作流中使用的状态类型。
"""

from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import BaseMessage

from src.agents.common.state.base import add_messages


class TradingAgentState(TypedDict):
    """
    交易 Agent 状态

    包含交易决策所需的所有数据。
    使用 TypedDict 确保类型安全和 IDE 支持。
    """

    # ===== 消息历史（使用 reducer 累积）=====
    messages: Annotated[list[BaseMessage], add_messages]

    # ===== 基本信息 =====
    symbol: str  # 交易对符号
    timestamp: str  # 当前时间戳

    # ===== 市场数据 =====
    market_data: dict[str, Any]  # 市场数据（价格、指标等）
    current_price: float  # 当前价格
    multi_timeframe_trends: dict[str, str]  # 多时间周期趋势
    enriched_data: dict[str, Any] | None  # 增强数据（如外部信息）

    # ===== 持仓信息 =====
    current_positions: list[dict[str, Any]]  # 当前持仓列表
    max_positions: int  # 最大持仓数
    balance_info: dict[str, float] | None  # 账户余额信息

    # ===== 交易参数 =====
    trade_amount: float  # 单笔交易金额上限
    max_leverage: int  # 最大杠杆倍数
    take_profit_ratio: float  # 止盈比例
    stop_loss_ratio: float  # 止损比例

    # ===== 历史上下文 =====
    historical_summary: str | None  # 历史决策汇总

    # ===== 决策相关 =====
    prompt: str  # 生成的 Prompt
    decision_type: str  # 决策类型 (BUY, SELL, SELL_SHORT, BUY_TO_COVER, DO_NOTHING)
    decision_details: dict[str, Any]  # 决策详情
    execution_result: str | None  # 执行结果

    # ===== 工作流控制 =====
    current_step: str  # 当前步骤
    errors: list[str]  # 错误信息列表
    should_use_execution_agent: bool  # 是否需要使用执行 Agent


class TradingDecision(TypedDict):
    """
    交易决策结构

    标准化的决策输出格式。
    """

    decision_type: Literal["BUY", "SELL", "SELL_SHORT", "BUY_TO_COVER", "DO_NOTHING", "ERROR"]
    symbol: str
    amount: float | None
    leverage: int | None
    reason: str
    confidence: float
    timestamp: str


def create_initial_state(
    symbol: str,
    market_data: dict[str, Any],
    multi_timeframe_trends: dict[str, str],
    current_positions: list[dict[str, Any]],
    max_positions: int,
    trade_amount: float,
    max_leverage: int,
    take_profit_ratio: float,
    stop_loss_ratio: float,
    historical_summary: str | None = None,
    balance_info: dict[str, float] | None = None,
    enriched_data: dict[str, Any] | None = None,
) -> TradingAgentState:
    """
    创建初始状态

    Args:
        symbol: 交易对符号
        market_data: 市场数据
        multi_timeframe_trends: 多时间周期趋势
        current_positions: 当前持仓
        max_positions: 最大持仓数
        trade_amount: 单笔交易金额上限
        max_leverage: 最大杠杆倍数
        take_profit_ratio: 止盈比例
        stop_loss_ratio: 止损比例
        historical_summary: 历史决策汇总
        balance_info: 账户余额信息
        enriched_data: 增强数据

    Returns:
        初始化的交易 Agent 状态
    """
    from datetime import datetime

    return TradingAgentState(
        messages=[],
        symbol=symbol,
        timestamp=datetime.now().isoformat(),
        market_data=market_data,
        current_price=market_data.get("current_price") or 0.0,
        multi_timeframe_trends=multi_timeframe_trends,
        enriched_data=enriched_data,
        current_positions=current_positions,
        max_positions=max_positions,
        balance_info=balance_info,
        trade_amount=trade_amount,
        max_leverage=max_leverage,
        take_profit_ratio=take_profit_ratio,
        stop_loss_ratio=stop_loss_ratio,
        historical_summary=historical_summary,
        prompt="",
        decision_type="",
        decision_details={},
        execution_result=None,
        current_step="start",
        errors=[],
        should_use_execution_agent=False,
    )
