"""
外部信息 Agent 模块

使用 LangGraph StateGraph 实现的外部信息收集 Agent。
负责从外部来源（Exa 搜索等）收集市场信息。

注意：此模块是对现有 src/agent/external_info 的封装，
保持向后兼容的同时提供新的目录结构。
"""

# 从现有实现导入
from src.agent.external_info.state import ResearchState
from src.agent.external_info.workflow import ExternalInfoWorkflow
from src.agent.external_info.tools import (
    search_crypto_market_news,
    search_crypto_regulatory_news,
    search_crypto_macro_news,
    create_period_search_queries,
)
from src.agent.external_info_agent import ExternalInfoAgent

__all__ = [
    "ResearchState",
    "ExternalInfoWorkflow",
    "ExternalInfoAgent",
    "search_crypto_market_news",
    "search_crypto_regulatory_news",
    "search_crypto_macro_news",
    "create_period_search_queries",
]
