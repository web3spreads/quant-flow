"""
交易 Agent 模块

使用 LangGraph StateGraph 实现的交易决策 Agent。
负责分析市场数据并做出交易决策。
"""

from src.agents.trading.state import TradingAgentState, create_initial_state

# 延迟导入以避免循环依赖和缺失模块问题
# 使用时请调用 get_trading_agent() 或 get_trading_workflow()


def get_trading_agent():
    """获取 TradingAgent 类（延迟导入）"""
    from src.agents.trading.agent import TradingAgent

    return TradingAgent


def get_trading_workflow():
    """获取 TradingAgentWorkflow 类（延迟导入）"""
    from src.agents.trading.workflow import TradingAgentWorkflow

    return TradingAgentWorkflow


__all__ = [
    "TradingAgentState",
    "create_initial_state",
    "get_trading_agent",
    "get_trading_workflow",
]
