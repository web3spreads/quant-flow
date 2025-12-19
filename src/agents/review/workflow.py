"""
复盘 Agent LangGraph 工作流

使用 StateGraph 实现的复盘分析工作流。
工作流程：
1. prepare_data: 准备数据（压缩决策历史、计算统计、提取特征）
2. find_similar_lessons: 查找相似经验
3. generate_review: 生成复盘分析
4. enrich_lessons: 丰富和过滤经验
5. log_results: 记录结果
"""

from typing import Dict, Any, List, Optional

from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage, SystemMessage

from src.agents.review.state import ReviewAgentState, create_initial_state
from src.agents.common.utils.llm import LLMConfig, create_json_llm
from src.agents.common.utils.helpers import shorten_text, extract_json_from_text
from src.prompt_manager import PromptManager


class ReviewAgentWorkflow:
    """
    复盘 Agent 工作流

    基于 LangGraph StateGraph 的复盘分析工作流。
    """

    def __init__(
        self,
        llm_config: LLMConfig,
        prompt_manager: PromptManager,
        context_extractor=None,
        similarity_scorer=None,
        memory_store=None,
        daily_logger=None,
        notifier=None,
        logger=None,
        lookback_decisions: int = 12,
        min_confidence: float = 0.35,
        similarity_threshold: float = 0.5,
        confidence_decay_factor: float = 0.6,
    ):
        """
        初始化工作流

        Args:
            llm_config: LLM 配置
            prompt_manager: Prompt 管理器
            context_extractor: 上下文特征提取器
            similarity_scorer: 相似度计算器
            memory_store: 经验存储
            daily_logger: 每日日志记录器
            notifier: 通知器
            logger: 日志记录器
            lookback_decisions: 回溯决策数
            min_confidence: 最小置信度
            similarity_threshold: 相似度阈值
            confidence_decay_factor: 置信度衰减因子
        """
        self.llm_config = llm_config
        self.prompt_manager = prompt_manager
        self.context_extractor = context_extractor
        self.similarity_scorer = similarity_scorer
        self.memory_store = memory_store
        self.daily_logger = daily_logger
        self.notifier = notifier
        self.logger = logger
        self.lookback_decisions = lookback_decisions
        self.min_confidence = min_confidence
        self.similarity_threshold = similarity_threshold
        self.confidence_decay_factor = confidence_decay_factor

        # 创建 JSON Mode LLM
        self.llm = create_json_llm(llm_config, temperature=0.05)

        # 获取系统 Prompt
        self.system_prompt = prompt_manager.get_review_system_prompt()

        # 构建工作流
        self.app = self._build_workflow()

    def _build_workflow(self) -> StateGraph:
        """构建 LangGraph 工作流"""
        workflow = StateGraph(ReviewAgentState)

        # 添加节点
        workflow.add_node("prepare_data", self._prepare_data_node)
        workflow.add_node("find_similar_lessons", self._find_similar_lessons_node)
        workflow.add_node("generate_review", self._generate_review_node)
        workflow.add_node("enrich_lessons", self._enrich_lessons_node)
        workflow.add_node("log_results", self._log_results_node)

        # 设置入口点
        workflow.set_entry_point("prepare_data")

        # 添加边
        workflow.add_edge("prepare_data", "find_similar_lessons")
        workflow.add_edge("find_similar_lessons", "generate_review")
        workflow.add_edge("generate_review", "enrich_lessons")
        workflow.add_edge("enrich_lessons", "log_results")
        workflow.add_edge("log_results", END)

        return workflow.compile()

    def _prepare_data_node(self, state: ReviewAgentState) -> Dict[str, Any]:
        """
        准备数据节点

        压缩决策历史、计算统计数据、提取上下文特征。
        """
        if self.logger:
            self.logger.print_info(f"[{state['symbol']}] 准备复盘数据...")

        records = state['decision_records'][-self.lookback_decisions:]

        if not records:
            return {
                "decision_digest": [],
                "stats": {},
                "context_features": {},
                "current_step": "prepare_data",
                "errors": state.get('errors', []) + ["无决策记录"],
            }

        # 压缩决策历史
        digest = self._build_decision_digest(records)

        # 计算统计数据
        stats = self._calculate_stats(records)

        # 提取上下文特征
        context_features = {}
        if self.context_extractor:
            context_features = self.context_extractor.extract(
                records[-1].get("market_data", {}),
                decision_records=records,
            )

        return {
            "decision_digest": digest,
            "stats": stats,
            "context_features": context_features,
            "current_step": "prepare_data",
        }

    def _find_similar_lessons_node(self, state: ReviewAgentState) -> Dict[str, Any]:
        """
        查找相似经验节点
        """
        if self.logger:
            self.logger.print_info(f"[{state['symbol']}] 查找相似经验...")

        similar_lessons = []
        existing_lessons = state.get('existing_lessons')

        if self.memory_store and self.similarity_scorer:
            similar_lessons = self.memory_store.get_similar_lessons(
                symbol=state['symbol'],
                context_features=state['context_features'],
                scorer=self.similarity_scorer,
                similarity_threshold=self.similarity_threshold,
                limit=5,
            )

        # 更新 existing_lessons
        if existing_lessons is None and self.memory_store:
            existing_lessons = (
                similar_lessons if similar_lessons
                else self.memory_store.get_lessons(state['symbol'])
            )
        elif similar_lessons:
            existing_lessons = similar_lessons

        return {
            "similar_lessons": similar_lessons,
            "existing_lessons": existing_lessons,
            "current_step": "find_similar_lessons",
        }

    def _generate_review_node(self, state: ReviewAgentState) -> Dict[str, Any]:
        """
        生成复盘分析节点
        """
        if self.logger:
            self.logger.print_info(f"[{state['symbol']}] 生成复盘分析...")

        try:
            # 生成 Prompt
            prompt = self.prompt_manager.format_review_prompt(
                symbol=state['symbol'],
                decision_digest=state['decision_digest'],
                stats=state['stats'],
                existing_lessons=(state.get('existing_lessons') or [])[:5],
                fills_summary=state.get('fills_summary') or {"total_fills": 0, "total_pnl": 0.0},
                context_features=state['context_features'],
            )

            if self.logger:
                self.logger.print_section(
                    f"🧠 {state['symbol']} 复盘 Agent 输入",
                    style="bold white"
                )
                self.logger.print_prompt(prompt)

            # 调用 LLM
            messages = [
                SystemMessage(content=self.system_prompt),
                HumanMessage(content=prompt)
            ]

            response = self.llm.invoke(messages)
            raw_text = (
                response.content if isinstance(response.content, str)
                else str(response.content)
            )

            # 解析响应
            parsed = self._parse_response(raw_text)

            return {
                "prompt": prompt,
                "raw_output": raw_text,
                "lessons": parsed.get("lessons", []),
                "summary": parsed.get("summary", ""),
                "spot_checks": parsed.get("spot_checks", []),
                "current_step": "generate_review",
            }

        except Exception as e:
            error_msg = f"复盘生成失败: {str(e)}"
            if self.logger:
                self.logger.print_error(f"[{state['symbol']}] {error_msg}")

            return {
                "current_step": "error",
                "errors": state.get('errors', []) + [error_msg],
            }

    def _enrich_lessons_node(self, state: ReviewAgentState) -> Dict[str, Any]:
        """
        丰富和过滤经验节点
        """
        if self.logger:
            self.logger.print_info(f"[{state['symbol']}] 丰富经验数据...")

        lessons = state.get('lessons', [])
        context_features = state.get('context_features', {})

        if not lessons:
            return {"lessons": [], "current_step": "enrich_lessons"}

        enriched = []
        for lesson in lessons:
            rule = (lesson.get("rule") or "").strip()
            action = (lesson.get("action") or "").strip()
            if not rule or not action:
                continue

            base_confidence = float(lesson.get("confidence", 0) or 0)

            # 获取经验的上下文特征
            if "context_features" in lesson and lesson.get("context_features"):
                lesson_context = lesson.get("context_features")
            else:
                lesson_context = context_features

            # 计算相似度
            similarity_score = 1.0
            if self.similarity_scorer and context_features and lesson_context:
                similarity_score = self.similarity_scorer.compute(
                    context_features, lesson_context
                )

            # 计算环境匹配因子
            env_match_factor = self._environment_match_factor(similarity_score)
            adjusted_confidence = round(base_confidence * env_match_factor, 3)

            # 计算置信区间
            support_count = int(lesson.get("support_count", 1) or 1)
            ci_low, ci_high = self._calculate_confidence_interval(
                base_confidence, adjusted_confidence, support_count, similarity_score
            )

            # 过滤低置信度和低相似度的经验
            if adjusted_confidence < self.min_confidence:
                continue
            if similarity_score < self.similarity_threshold:
                continue

            enriched.append({
                **lesson,
                "rule": rule,
                "action": action,
                "original_confidence": base_confidence,
                "confidence": adjusted_confidence,
                "adjusted_confidence": adjusted_confidence,
                "similarity_score": similarity_score,
                "environment_match_factor": env_match_factor,
                "confidence_interval": [ci_low, ci_high],
                "context_features": lesson_context,
                "support_count": support_count,
            })

        # 按置信度排序
        enriched.sort(key=lambda x: x.get("confidence", 0), reverse=True)

        return {"lessons": enriched, "current_step": "enrich_lessons"}

    def _log_results_node(self, state: ReviewAgentState) -> Dict[str, Any]:
        """
        记录结果节点
        """
        if self.logger:
            self.logger.print_info(f"[{state['symbol']}] 记录复盘结果...")

        lessons = state.get('lessons', [])
        summary = state.get('summary', '')

        # 发送通知
        if lessons and self.notifier:
            try:
                self.notifier.notify_review_lesson(
                    symbol=state['symbol'],
                    lessons=lessons,
                    summary=summary
                )
            except Exception as e:
                if self.logger:
                    self.logger.print_warning(f"发送复盘通知失败: {e}")

        # 记录到每日日志
        if self.daily_logger:
            try:
                self.daily_logger.log_review(
                    symbol=state['symbol'],
                    prompt=state.get('prompt', ''),
                    raw_output=state.get('raw_output', ''),
                    lessons=lessons,
                    summary=summary,
                    context_features=state.get('context_features', {}),
                    decision_digest=state.get('decision_digest', []),
                    stats=state.get('stats', {}),
                    fills_summary=state.get('fills_summary'),
                    existing_lessons=state.get('existing_lessons'),
                    spot_checks=state.get('spot_checks', []),
                )
            except Exception as e:
                if self.logger:
                    self.logger.print_warning(f"记录每日日志失败: {e}")

        return {"current_step": "log_results"}

    def _build_decision_digest(
        self,
        records: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """压缩决策历史为短摘要"""
        digest = []
        for record in records:
            market = record.get("market_data", {})
            action_details = record.get("action_details", {})
            reason = record.get("reason") or action_details.get("output", "")
            digest.append({
                "timestamp": record.get("timestamp", ""),
                "decision": record.get("decision", "UNKNOWN"),
                "price": float(market.get("current_price") or 0.0),
                "result": action_details.get("status") or action_details.get("decision", "N/A"),
                "reason": shorten_text(reason, 140),
            })
        return digest

    def _calculate_stats(self, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """统计基本指标"""
        prices = [
            float(r.get("market_data", {}).get("current_price") or 0.0)
            for r in records
        ]
        avg_price = sum(prices) / len(prices) if prices else 0.0

        def count(decision_type: str) -> int:
            return sum(1 for r in records if r.get("decision") == decision_type)

        return {
            "total_decisions": len(records),
            "buy_count": count("BUY"),
            "sell_count": count("SELL"),
            "sell_short_count": count("SELL_SHORT"),
            "buy_to_cover_count": count("BUY_TO_COVER"),
            "idle_count": count("DO_NOTHING"),
            "close_count": count("SELL") + count("BUY_TO_COVER"),
            "min_price": min(prices) if prices else 0.0,
            "max_price": max(prices) if prices else 0.0,
            "average_price": avg_price,
        }

    def _environment_match_factor(self, similarity_score: float) -> float:
        """计算环境匹配因子"""
        penalty = (1 - similarity_score) * self.confidence_decay_factor
        return max(0.2, 1 - penalty)

    def _calculate_confidence_interval(
        self,
        base_confidence: float,
        adjusted_confidence: float,
        support_count: int,
        similarity_score: float,
    ) -> List[float]:
        """计算置信区间"""
        support = max(1, support_count)
        base_confidence = max(0.0, min(base_confidence, 1.0))
        variance = base_confidence * (1 - base_confidence)
        std_error = (variance / support) ** 0.5
        widen = 1 + (1 - similarity_score)
        margin = std_error * widen
        lower = max(0.0, adjusted_confidence - margin)
        upper = min(1.0, adjusted_confidence + margin)
        return [round(lower, 3), round(upper, 3)]

    def _parse_response(self, text: str) -> Dict[str, Any]:
        """解析 LLM 响应"""
        json_data = extract_json_from_text(text)
        if json_data and isinstance(json_data, dict):
            return json_data
        return {"summary": text[:200], "lessons": [], "spot_checks": []}

    def run(
        self,
        symbol: str,
        decision_records: List[Dict[str, Any]],
        fills_summary: Optional[Dict[str, Any]] = None,
        existing_lessons: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        运行工作流

        Args:
            symbol: 交易对符号
            decision_records: 决策记录列表
            fills_summary: 成交汇总
            existing_lessons: 已有经验

        Returns:
            复盘结果
        """
        if not decision_records:
            return {"lessons": [], "summary": "", "spot_checks": []}

        initial_state = create_initial_state(
            symbol=symbol,
            decision_records=decision_records,
            fills_summary=fills_summary,
            existing_lessons=existing_lessons,
        )

        final_state = self.app.invoke(initial_state)

        return {
            "summary": final_state.get("summary", ""),
            "lessons": final_state.get("lessons", []),
            "spot_checks": final_state.get("spot_checks", []),
            "raw_output": final_state.get("raw_output", ""),
            "prompt": final_state.get("prompt", ""),
            "context_features": final_state.get("context_features", {}),
        }
