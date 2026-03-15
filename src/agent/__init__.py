"""
Agent 模块

使用延迟导入避免循环依赖问题。
"""


def __getattr__(name):
    """
    延迟导入：只在真正需要时才导入模块
    """
    # Trading agents (需要 eth_account 等依赖)
    if name == "SingleSymbolAgent":
        from src.agent.single_symbol_agent import SingleSymbolAgent

        return SingleSymbolAgent

    if name == "EnhancedSingleSymbolAgent":
        from src.agent.enhanced_single_symbol_agent import EnhancedSingleSymbolAgent

        return EnhancedSingleSymbolAgent

    if name == "create_enhanced_agent":
        from src.agent.enhanced_single_symbol_agent import create_enhanced_agent

        return create_enhanced_agent

    # Summary agent
    if name in ("SummaryAgentV2", "DecisionHistory"):
        from src.agent import summary_agent_v2

        return getattr(summary_agent_v2, name)

    # Trading tools
    if name == "TradingTools":
        from src.agent.tools import TradingTools

        return TradingTools

    # Prompts
    if name == "SYSTEM_PROMPT":
        from src.agent.prompts import SYSTEM_PROMPT

        return SYSTEM_PROMPT

    # Review agent
    if name == "ReviewAgent":
        from src.agent.review_agent import ReviewAgent

        return ReviewAgent

    if name == "ReviewMemoryStore":
        from src.agent.review_memory import ReviewMemoryStore

        return ReviewMemoryStore

    # Utils
    if name == "ContextExtractor":
        from src.agent.context_extractor import ContextExtractor

        return ContextExtractor

    if name == "SimilarityScorer":
        from src.agent.similarity_scorer import SimilarityScorer

        return SimilarityScorer

    # External info
    if name in ("ExternalInfoAgent", "ExternalInfoScheduler", "get_external_info_agent"):
        from src.agent import external_info_agent

        return getattr(external_info_agent, name)

    # Market info store
    if name in ("MarketInfoStore", "get_market_info_store"):
        from src.agent import market_info_store

        return getattr(market_info_store, name)

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "SingleSymbolAgent",
    "EnhancedSingleSymbolAgent",
    "create_enhanced_agent",
    "SummaryAgentV2",
    "DecisionHistory",
    "TradingTools",
    "SYSTEM_PROMPT",
    "ReviewAgent",
    "ReviewMemoryStore",
    "ContextExtractor",
    "SimilarityScorer",
    "ExternalInfoAgent",
    "ExternalInfoScheduler",
    "get_external_info_agent",
    "MarketInfoStore",
    "get_market_info_store",
]
