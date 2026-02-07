"""
复盘 Agent 状态定义

定义 ReviewAgent 在 LangGraph 工作流中使用的状态类型。
"""

from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage

from src.agents.common.state.base import add_messages


class ReviewAgentState(TypedDict):
    """
    复盘 Agent 状态

    包含复盘分析所需的所有数据。
    """

    # ===== 消息历史 =====
    messages: Annotated[list[BaseMessage], add_messages]

    # ===== 基本信息 =====
    symbol: str  # 交易对符号
    timestamp: str  # 当前时间戳

    # ===== 输入数据 =====
    decision_records: list[dict[str, Any]]  # 决策记录列表
    fills_summary: dict[str, Any] | None  # 成交汇总
    existing_lessons: list[dict[str, Any]] | None  # 已有经验

    # ===== 处理中间数据 =====
    decision_digest: list[dict[str, Any]]  # 压缩后的决策摘要
    stats: dict[str, Any]  # 统计数据
    context_features: dict[str, Any]  # 上下文特征
    similar_lessons: list[dict[str, Any]]  # 相似经验

    # ===== Prompt =====
    prompt: str  # 生成的 Prompt

    # ===== 输出数据 =====
    raw_output: str  # LLM 原始输出
    lessons: list[dict[str, Any]]  # 提取的经验教训
    summary: str  # 复盘摘要
    spot_checks: list[dict[str, Any]]  # 现货检查建议

    # ===== 工作流控制 =====
    current_step: str  # 当前步骤
    errors: list[str]  # 错误信息列表


class LessonOutput(TypedDict):
    """
    经验输出结构

    标准化的经验输出格式。
    """

    rule: str  # 经验规则
    action: str  # 建议动作
    confidence: float  # 置信度
    support_count: int  # 支持样本数
    evidence: list[str]  # 证据列表
    context_features: dict[str, Any] | None  # 相关上下文特征


def create_initial_state(
    symbol: str,
    decision_records: list[dict[str, Any]],
    fills_summary: dict[str, Any] | None = None,
    existing_lessons: list[dict[str, Any]] | None = None,
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
