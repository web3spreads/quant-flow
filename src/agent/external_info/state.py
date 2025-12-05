"""
LangGraph 状态定义
用于外部信息收集工作流
"""

from typing import List, Dict, Any, Optional, TypedDict



class ResearchState(TypedDict):
    """研究工作流状态"""
    # 基本信息
    period: str  # 时间周期: daily, weekly, biweekly, monthly
    symbols: List[str]  # 关注的币种列表
    
    # 搜索相关
    search_queries: Dict[str, List[Dict[str, Any]]]  # 搜索查询列表
    search_results: Dict[str, List[str]]  # 搜索结果，按主题分类
    
    # 报告生成
    formatted_results: str  # 格式化的搜索结果文本
    report: Optional[Dict[str, Any]]  # 生成的结构化报告
    
    # 错误处理
    errors: List[str]  # 错误信息列表
