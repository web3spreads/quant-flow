"""
工具函数模块

提供所有 Agent 共享的工具函数。
"""

from src.agents.common.utils.helpers import (
    safe_float,
    safe_leverage,
    safe_int,
    shorten_text,
    extract_json_from_text,
)
from src.agents.common.utils.llm import (
    create_llm,
    create_json_llm,
    LLMConfig,
)
from src.agents.common.utils.similarity_scorer import (
    SimilarityScorer,
    DEFAULT_WEIGHTS,
)
from src.agents.common.utils.context_extractor import ContextExtractor
from src.agents.common.utils.review_daily_logger import ReviewDailyLogger
from src.agents.common.utils.market_info_store import (
    MarketInfoStore,
    RiskSeverity,
    get_market_info_store,
)

__all__ = [
    # helpers
    "safe_float",
    "safe_leverage",
    "safe_int",
    "shorten_text",
    "extract_json_from_text",
    # llm
    "create_llm",
    "create_json_llm",
    "LLMConfig",
    # similarity
    "SimilarityScorer",
    "DEFAULT_WEIGHTS",
    # context
    "ContextExtractor",
    # review logger
    "ReviewDailyLogger",
    # market info store
    "MarketInfoStore",
    "RiskSeverity",
    "get_market_info_store",
]
