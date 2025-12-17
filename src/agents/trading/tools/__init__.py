"""
交易 Agent 工具模块

交易 Agent 特有的工具定义。
通用工具请使用 src.agents.common.tools。
"""

# 交易 Agent 使用通用交易工具
from src.agents.common.tools.trading import (
    TradingToolFactory,
    BuyInput,
    SellShortInput,
    BuySpotInput,
    create_mock_callbacks,
)

__all__ = [
    "TradingToolFactory",
    "BuyInput",
    "SellShortInput",
    "BuySpotInput",
    "create_mock_callbacks",
]
