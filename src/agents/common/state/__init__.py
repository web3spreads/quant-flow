"""
状态定义模块

提供所有 Agent 使用的状态类型定义。
使用 TypedDict 和 Pydantic 进行类型验证。
"""

from src.agents.common.state.base import (
    BaseAgentState,
    MessageState,
    add_messages,
)

__all__ = [
    "BaseAgentState",
    "MessageState",
    "add_messages",
]
