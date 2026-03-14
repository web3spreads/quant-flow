"""
外部信息收集模块初始化文件

注意：此模块已迁移到 src.agents.external_info
此文件保留用于向后兼容，请使用新位置。
"""


def __getattr__(name):
    """
    延迟导入：从新位置导入（兼容层）
    """
    if name in (
        "search_crypto_market_news",
        "search_crypto_regulatory_news",
        "search_crypto_macro_news",
        "create_period_search_queries",
        "ALL_TOOLS",
    ):
        from src.agents.external_info import tools

        return getattr(tools, name)

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "search_crypto_market_news",
    "search_crypto_regulatory_news",
    "search_crypto_macro_news",
    "create_period_search_queries",
    "ALL_TOOLS",
]
