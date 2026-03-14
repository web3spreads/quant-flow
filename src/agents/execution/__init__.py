"""
执行 Agent 模块

使用 LangGraph StateGraph 实现的决策执行 Agent。
负责将交易决策文本转换为结构化的执行计划并执行。
"""

from src.agents.execution.state import (
    DecisionType,
    ExecutionAgentState,
    ExecutionPlan,
    create_initial_state,
)

# 延迟导入以避免循环依赖和缺失模块问题


def get_execution_workflow():
    """获取 ExecutionAgentWorkflow 类（延迟导入）"""
    from src.agents.execution.workflow import ExecutionAgentWorkflow

    return ExecutionAgentWorkflow


__all__ = [
    "ExecutionAgentState",
    "ExecutionPlan",
    "DecisionType",
    "create_initial_state",
    "get_execution_workflow",
]
