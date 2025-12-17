"""
通用工具模块

提供所有 Agent 共享的工具定义。
"""

from src.agents.common.tools.base import (
    BaseTool,
    ToolResult,
    ToolError,
)
from src.agents.common.tools.trading import (
    TradingToolFactory,
    BuyInput,
    SellShortInput,
    BuySpotInput,
)

__all__ = [
    "BaseTool",
    "ToolResult",
    "ToolError",
    "TradingToolFactory",
    "BuyInput",
    "SellShortInput",
    "BuySpotInput",
]
