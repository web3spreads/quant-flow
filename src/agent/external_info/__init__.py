"""
外部信息收集 Agent 模块

使用 LangGraph StateGraph 实现的外部信息收集工作流。
负责从外部来源（Exa 搜索等）收集加密货币市场信息。

模块结构：
- state.py: 工作流状态定义 (ResearchState)
- tools.py: Exa 搜索工具（需要 langchain-exa 依赖）
- workflow.py: LangGraph 工作流实现

注意：tools, workflow 模块需要 langchain-exa 依赖，
使用延迟导入以避免在依赖不存在时导入失败。
"""

# 状态定义（无外部依赖，直接导入）
from src.agent.external_info.state import ResearchState, create_initial_state


def __getattr__(name):
    """
    延迟导入：只在真正需要时才导入依赖 langchain-exa 的模块
    """
    # 工具相关
    if name in (
        "search_crypto_market_news",
        "search_crypto_regulatory_news",
        "search_crypto_macro_news",
        "create_period_search_queries",
        "ALL_TOOLS",
    ):
        from src.agent.external_info import tools

        return getattr(tools, name)

    # 工作流
    if name == "ExternalInfoWorkflow":
        from src.agent.external_info.workflow import ExternalInfoWorkflow

        return ExternalInfoWorkflow

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # 状态
    "ResearchState",
    "create_initial_state",
    # 工具（延迟导入）
    "search_crypto_market_news",
    "search_crypto_regulatory_news",
    "search_crypto_macro_news",
    "create_period_search_queries",
    "ALL_TOOLS",
    # 工作流（延迟导入）
    "ExternalInfoWorkflow",
]
