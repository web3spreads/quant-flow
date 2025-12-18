"""
LangGraph 工作流定义
用于外部信息收集和报告生成

注意：此模块已迁移到 src.agents.external_info.workflow
此文件保留用于向后兼容，请使用新位置。
"""

# 从新位置导入（兼容层）
from src.agents.external_info.workflow import ExternalInfoWorkflow

__all__ = ["ExternalInfoWorkflow"]
