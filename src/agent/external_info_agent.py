"""
外部信息收集 Agent
负责使用 Exa 搜索 API 收集市场信息，并汇总生成报告

注意：此模块已迁移到 src.agents.external_info.agent
此文件保留用于向后兼容，请使用新位置。
"""

# 从新位置导入（兼容层）
from src.agents.external_info.agent import (
    ExternalInfoAgent,
    ExternalInfoScheduler,
    get_external_info_agent,
)

__all__ = [
    "ExternalInfoAgent",
    "ExternalInfoScheduler",
    "get_external_info_agent",
]
