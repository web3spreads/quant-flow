"""
通用工具模块

提供所有 Agent 共享的工具定义。
"""

from src.agents.common.tools.base import (
    BaseTool,
    ToolError,
    ToolResult,
)
from src.agents.common.tools.trading import (
    BuyInput,
    SellShortInput,
    TradingToolFactory,
)

__all__ = [
    "BaseTool",
    "ToolResult",
    "ToolError",
    "TradingToolFactory",
    "BuyInput",
    "SellShortInput",
]
