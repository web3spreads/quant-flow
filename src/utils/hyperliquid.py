"""
Hyperliquid SDK 工具函数
"""

from hyperliquid.info import Info


def create_info(base_url: str, skip_ws: bool = True) -> Info:
    """
    安全创建 Info 实例。
    """
    return Info(base_url, skip_ws=skip_ws)
