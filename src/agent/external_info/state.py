"""
LangGraph 状态定义
用于外部信息收集工作流

注意：此模块已迁移到 src.agents.external_info.state
此文件保留用于向后兼容，请使用新位置。
"""

# 从新位置导入（兼容层）
from src.agents.external_info.state import ResearchState, create_initial_state

__all__ = ["ResearchState", "create_initial_state"]
