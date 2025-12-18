"""
外部信息收集工具模块
使用 LangChain 和 Exa 集成的最佳实践

注意：此模块已迁移到 src.agents.external_info.tools
此文件保留用于向后兼容，请使用新位置。
"""

# 从新位置导入（兼容层）
from src.agents.external_info.tools import (
    search_crypto_market_news,
    search_crypto_regulatory_news,
    search_crypto_macro_news,
    create_period_search_queries,
    ALL_TOOLS,
)

__all__ = [
    "search_crypto_market_news",
    "search_crypto_regulatory_news",
    "search_crypto_macro_news",
    "create_period_search_queries",
    "ALL_TOOLS",
]
