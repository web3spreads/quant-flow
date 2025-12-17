"""
复盘 Agent 状态定义

定义 ReviewAgent 在 LangGraph 工作流中使用的状态类型。
"""

from typing import TypedDict, List, Dict, Any, Optional, Annotated
from langchain_core.messages import BaseMessage

from src.agents.common.state.base import add_messages


class ReviewAgentState(TypedDict):
    """
    复盘 Agent 状态

    包含复盘分析所需的所有数据。
    """
    # ===== 消息历史 =====
    messages: Annotated[List[BaseMessage], add_messages]

    # ===== 基本信息 =====
    symbol: str  # 交易对符号
    timestamp: str  # 当前时间戳

    # ===== 输入数据 =====
    decision_records: List[Dict[str, Any]]  # 决策记录列表
    fills_summary: Optional[Dict[str, Any]]  # 成交汇总
    existing_lessons: Optional[List[Dict[str, Any]]]  # 已有经验

    # ===== 处理中间数据 =====
    decision_digest: List[Dict[str, Any]]  # 压缩后的决策摘要
    stats: Dict[str, Any]  # 统计数据
    context_features: Dict[str, Any]  # 上下文特征
    similar_lessons: List[Dict[str, Any]]  # 相似经验

    # ===== Prompt =====
    prompt: str  # 生成的 Prompt

    # ===== 输出数据 =====
    raw_output: str  # LLM 原始输出
    lessons: List[Dict[str, Any]]  # 提取的经验教训
    summary: str  # 复盘摘要
    spot_checks: List[Dict[str, Any]]  # 现货检查建议

    # ===== 工作流控制 =====
    current_step: str  # 当前步骤
    errors: List[str]  # 错误信息列表


class LessonOutput(TypedDict):
    """
    经验输出结构

    标准化的经验输出格式。
    """
    rule: str  # 经验规则
    action: str  # 建议动作
    confidence: float  # 置信度
    support_count: int  # 支持样本数
    evidence: List[str]  # 证据列表
    context_features: Optional[Dict[str, Any]]  # 相关上下文特征


def create_initial_state(
    symbol: str,
    decision_records: List[Dict[str, Any]],
    fills_summary: Optional[Dict[str, Any]] = None,
    existing_lessons: Optional[List[Dict[str, Any]]] = None,
) -> ReviewAgentState:
    """
    创建初始状态

    Args:
        symbol: 交易对符号
        decision_records: 决策记录列表
        fills_summary: 成交汇总
        existing_lessons: 已有经验

    Returns:
        初始化的复盘 Agent 状态
    """
    from datetime import datetime

    return ReviewAgentState(
        messages=[],
        symbol=symbol,
        timestamp=datetime.now().isoformat(),
        decision_records=decision_records,
        fills_summary=fills_summary,
        existing_lessons=existing_lessons,
        decision_digest=[],
        stats={},
        context_features={},
        similar_lessons=[],
        prompt="",
        raw_output="",
        lessons=[],
        summary="",
        spot_checks=[],
        current_step="start",
        errors=[],
    )
