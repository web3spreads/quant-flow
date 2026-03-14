"""
市场信息存储模块
负责存储和读取外部市场信息,供交易决策 Agent 使用

注意:此模块已迁移到 src.agents.common.utils.market_info_store
此文件保留用于向后兼容,请使用新位置。
"""

# 从新位置导入(兼容层)
from src.agents.common.utils.market_info_store import (
    MarketInfoStore,
    RiskSeverity,
    get_market_info_store,
)

__all__ = ["MarketInfoStore", "RiskSeverity", "get_market_info_store"]
