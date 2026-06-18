"""
外部信息收集工作流状态定义

使用 TypedDict 定义 LangGraph StateGraph 工作流状态。
"""

from datetime import datetime
from typing import Any, TypedDict


class ResearchState(TypedDict):
    """
    研究工作流状态

    定义外部信息收集工作流中各节点共享的状态结构。
    """

    # ===== 基本配置 =====
    interval_hours: float  # 时间间隔（小时）
    symbols: list[str]  # 关注的币种列表
    start_time: datetime  # 开始时间
    end_time: datetime  # 结束时间

    # ===== 搜索相关 =====
    search_queries: dict[str, list[dict[str, Any]]]  # 搜索查询列表，按主题分类
    search_results: dict[str, list[str]]  # 搜索结果，按主题分类

    # ===== 报告生成 =====
    formatted_results: str  # 格式化的搜索结果文本
    report: dict[str, Any] | None  # 生成的结构化报告

    # ===== 错误处理 =====
    errors: list[str]  # 错误信息列表
