"""
复盘 Agent 主类

提供与原有 ReviewAgent 兼容的接口，
内部使用 LangGraph 工作流实现。
"""

from typing import Any

from src.agents.common.utils.llm import LLMConfig
from src.agents.review.workflow import ReviewAgentWorkflow
from src.prompt_manager import PromptManager
from src.utils.logger import TradingLogger


class ReviewAgent:
    """
    复盘 Agent

    负责读取近期决策，生成结构化的经验规则。
    内部使用 LangGraph StateGraph 实现工作流。

    与原有 ReviewAgent 保持接口兼容。
    """

    def __init__(
        self,
        logger: TradingLogger,
        prompt_manager: PromptManager,
        openai_api_base: str,
        openai_api_key: str,
        model: str,
        temperature: float = 0.05,
        lookback_decisions: int = 12,
        memory_store=None,
        min_confidence: float = 0.35,
        similarity_threshold: float = 0.5,
        similarity_weights: dict[str, float] | None = None,
        confidence_decay_factor: float = 0.6,
        similarity_method: str = "cosine",
        notifier=None,
        daily_logger=None,
    ):
        """
        初始化复盘 Agent

        Args:
            logger: 日志记录器
            prompt_manager: Prompt 管理器
            openai_api_base: OpenAI API Base URL
            openai_api_key: OpenAI API Key
            model: 模型名称
            temperature: 温度参数
            lookback_decisions: 回溯决策数
            memory_store: 经验存储
            min_confidence: 最小置信度
            similarity_threshold: 相似度阈值
            similarity_weights: 相似度权重
            confidence_decay_factor: 置信度衰减因子
            similarity_method: 相似度计算方法
            notifier: 通知器
            daily_logger: 每日日志记录器
        """
        self.logger = logger
        self.prompt_manager = prompt_manager
        self.lookback_decisions = lookback_decisions
        self.memory_store = memory_store
        self.min_confidence = min_confidence
        self.similarity_threshold = similarity_threshold
        self.confidence_decay_factor = confidence_decay_factor
        self.notifier = notifier
        self.daily_logger = daily_logger

        # 创建 LLM 配置
        self.llm_config = LLMConfig(
            api_base=openai_api_base,
            api_key=openai_api_key,
            model=model,
            temperature=temperature,
        )

        # 导入依赖组件（延迟导入避免循环依赖）
        from src.agent.context_extractor import ContextExtractor
        from src.agent.similarity_scorer import SimilarityScorer

        self.context_extractor = ContextExtractor()
        self.similarity_scorer = SimilarityScorer(
            weights=similarity_weights, method=similarity_method
        )

        # 创建工作流
        self.workflow = ReviewAgentWorkflow(
            llm_config=self.llm_config,
            prompt_manager=prompt_manager,
            context_extractor=self.context_extractor,
            similarity_scorer=self.similarity_scorer,
            memory_store=memory_store,
            daily_logger=daily_logger,
            notifier=notifier,
            logger=logger,
            lookback_decisions=lookback_decisions,
            min_confidence=min_confidence,
            similarity_threshold=similarity_threshold,
            confidence_decay_factor=confidence_decay_factor,
        )

    def review(
        self,
        symbol: str,
        decision_records: list[dict[str, Any]],
        fills_summary: dict[str, Any] | None = None,
        existing_lessons: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """
        执行复盘

        Args:
            symbol: 交易对符号
            decision_records: 决策记录列表
            fills_summary: 成交汇总
            existing_lessons: 已有经验

        Returns:
            复盘结果，包含：
            - summary: 复盘摘要
            - lessons: 提取的经验教训
            - spot_checks: 现货检查建议
            - raw_output: LLM 原始输出
            - prompt: 使用的 Prompt
            - context_features: 上下文特征
        """
        return self.workflow.run(
            symbol=symbol,
            decision_records=decision_records,
            fills_summary=fills_summary,
            existing_lessons=existing_lessons,
        )
