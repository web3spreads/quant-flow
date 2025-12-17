"""
复盘 Agent 模块

使用 LangGraph StateGraph 实现的复盘经验学习 Agent。
负责分析历史决策，提取经验教训。
"""

from src.agents.review.state import ReviewAgentState, create_initial_state

# 延迟导入以避免循环依赖和缺失模块问题


def get_review_workflow():
    """获取 ReviewAgentWorkflow 类（延迟导入）"""
    from src.agents.review.workflow import ReviewAgentWorkflow
    return ReviewAgentWorkflow


def get_review_agent():
    """获取 ReviewAgent 类（延迟导入）"""
    from src.agents.review.agent import ReviewAgent
    return ReviewAgent


__all__ = [
    "ReviewAgentState",
    "create_initial_state",
    "get_review_workflow",
    "get_review_agent",
]
