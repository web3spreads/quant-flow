"""
QLib 数据层

负责从 Hyperliquid 收集数据并转换为 QLib 可用格式，
包含加密货币专用的 DataHandler 和 24/7 交易日历。
"""

from .calendar import CryptoCalendar
from .handler import CryptoAlpha158
from .perpetual import PERPETUAL_FEATURE_CONFIG

__all__ = [
    "CryptoCalendar",
    "HyperliquidDataCollector",
    "CryptoAlpha158",
    "PERPETUAL_FEATURE_CONFIG",
]


def __getattr__(name):
    """延迟导入依赖外部 SDK 的模块"""
    if name == "HyperliquidDataCollector":
        from .collector import HyperliquidDataCollector
        return HyperliquidDataCollector
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
