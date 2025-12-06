"""
复盘 Agent
负责读取近期决策，生成结构化的经验规则
"""

import json
import re
from typing import List, Dict, Any, Optional
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from src.prompt_manager import PromptManager
from src.utils.logger import TradingLogger
from src.agent.review_memory import ReviewMemoryStore
from src.agent.context_extractor import ContextExtractor
from src.agent.similarity_scorer import SimilarityScorer


class ReviewAgent:
    """复盘 Agent：提炼经验并输出结构化 JSON"""

    def __init__(
        self,
        logger: TradingLogger,
        prompt_manager: PromptManager,
        openai_api_base: str,
        openai_api_key: str,
        model: str,
        temperature: float = 0.05,
        lookback_decisions: int = 12,
        memory_store: Optional[ReviewMemoryStore] = None,
        min_confidence: float = 0.35,
        similarity_threshold: float = 0.5,
        similarity_weights: Optional[Dict[str, float]] = None,
        confidence_decay_factor: float = 0.6,
        similarity_method: str = "cosine",
        notifier=None,
    ):
        self.logger = logger
        self.prompt_manager = prompt_manager
        self.lookback_decisions = lookback_decisions
        self.memory_store = memory_store
        self.min_confidence = min_confidence
        self.similarity_threshold = similarity_threshold
        self.confidence_decay_factor = confidence_decay_factor
        self.notifier = notifier

        self.llm = ChatOpenAI(
            base_url=openai_api_base,
            api_key=openai_api_key,
            model=model,
            temperature=temperature,
            model_kwargs={"response_format": {"type": "json_object"}},
        )

        system_prompt = self.prompt_manager.get_review_system_prompt()
        self.system_message = SystemMessage(content=system_prompt)
        self.context_extractor = ContextExtractor()
        self.similarity_scorer = SimilarityScorer(
            weights=similarity_weights, method=similarity_method
        )

    def review(
        self,
        symbol: str,
        decision_records: List[Dict[str, Any]],
        fills_summary: Optional[Dict[str, Any]] = None,
        existing_lessons: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """执行复盘"""
        if not decision_records:
            return {"lessons": [], "summary": "", "spot_checks": []}

        records = decision_records[-self.lookback_decisions :]
        digest = self._build_decision_digest(records)
        stats = self._calculate_stats(records)
        current_context = self.context_extractor.extract(
            records[-1].get("market_data", {}),
            decision_records=records,
        )

        similar_lessons: List[Dict[str, Any]] = []
        if self.memory_store:
            similar_lessons = self.memory_store.get_similar_lessons(
                symbol=symbol,
                context_features=current_context,
                scorer=self.similarity_scorer,
                similarity_threshold=self.similarity_threshold,
                limit=5,
            )

        if existing_lessons is None and self.memory_store:
            existing_lessons = (
                similar_lessons if similar_lessons else self.memory_store.get_lessons(symbol)
            )
        elif similar_lessons:
            existing_lessons = similar_lessons

        prompt = self.prompt_manager.format_review_prompt(
            symbol=symbol,
            decision_digest=digest,
            stats=stats,
            existing_lessons=(existing_lessons or [])[:5],
            fills_summary=fills_summary or {"total_fills": 0, "total_pnl": 0.0},
            context_features=current_context,
        )

        self.logger.print_section(f"🧠 {symbol} 复盘 Agent 输入", style="bold white")
        self.logger.print_prompt(prompt)

        response = self.llm.invoke([self.system_message, HumanMessage(content=prompt)])

        raw_text = (
            response.content
            if isinstance(response.content, str)
            else str(response.content)
        )
        parsed = self._parse_response(raw_text)
        lessons = parsed.get("lessons", [])
        filtered_lessons = self._enrich_lessons(
            lessons=lessons,
            context_features=current_context,
        )

        # 发送通知（如果有新经验且通知器可用）
        if filtered_lessons and self.notifier:
            try:
                self.notifier.notify_review_lesson(
                    symbol=symbol,
                    lessons=filtered_lessons,
                    summary=parsed.get("summary", "")
                )
            except Exception as e:
                self.logger.print_warning(f"发送复盘通知失败: {e}")

        result = {
            "summary": parsed.get("summary", ""),
            "lessons": filtered_lessons,
            "spot_checks": parsed.get("spot_checks", []),
            "raw_output": raw_text,
            "prompt": prompt,
            "context_features": current_context,
        }

        return result

    def _build_decision_digest(
        self, records: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        压缩决策历史为短摘要，控制 token 消耗。

        Args:
            records: 决策记录列表，每条记录包含 market_data、action_details 等字段

        Returns:
            压缩后的摘要列表，每条包含: timestamp, decision, price, result, reason
        """
        digest = []
        for record in records:
            market = record.get("market_data", {})
            action_details = record.get("action_details", {})
            reason = record.get("reason") or action_details.get("output", "")
            digest.append(
                {
                    "timestamp": record.get("timestamp", ""),
                    "decision": record.get("decision", "UNKNOWN"),
                    "price": float(market.get("current_price") or 0.0),
                    "result": action_details.get("status")
                    or action_details.get("decision", "N/A"),
                    "reason": self._shorten(reason),
                }
            )
        return digest

    def _calculate_stats(self, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        统计基本指标，避免让 LLM 自己计算。

        Args:
            records: 决策记录列表

        Returns:
            统计字典，包含:
            - total_decisions: 总决策数
            - buy_count, sell_count, sell_short_count, buy_to_cover_count, idle_count: 各类型决策计数
            - close_count: 平仓总数 (sell + buy_to_cover)
            - min_price, max_price, average_price: 价格统计
        """
        prices = [
            float(r.get("market_data", {}).get("current_price") or 0.0) for r in records
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

    def _enrich_lessons(
        self, lessons: List[Dict[str, Any]], context_features: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """为经验打上相似度、置信区间并按阈值过滤"""
        if not lessons:
            return []

        enriched: List[Dict[str, Any]] = []
        for lesson in lessons:
            rule = (lesson.get("rule") or "").strip()
            action = (lesson.get("action") or "").strip()
            if not rule or not action:
                continue

            base_confidence = float(lesson.get("confidence", 0) or 0)

            # 仅在响应包含 context_features 时使用；否则视为新规则并绑定当前环境
            if "context_features" in lesson and lesson.get("context_features"):
                lesson_context = lesson.get("context_features")
            else:
                lesson_context = context_features

            similarity_score = self.similarity_scorer.compute(
                context_features, lesson_context or {}
            )
            env_match_factor = self._environment_match_factor(similarity_score)
            adjusted_confidence = round(
                base_confidence * env_match_factor, 3
            )
            support_count = int(lesson.get("support_count", 1) or 1)
            ci_low, ci_high = self._calculate_confidence_interval(
                base_confidence,
                adjusted_confidence,
                support_count,
                similarity_score,
            )

            if adjusted_confidence < self.min_confidence:
                continue
            if similarity_score < self.similarity_threshold:
                continue

            enriched.append(
                {
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
                }
            )

        enriched.sort(key=lambda item: item.get("confidence", 0), reverse=True)
        return enriched

    def _environment_match_factor(self, similarity_score: float) -> float:
        """
        根据相似度计算环境匹配度，低相似度时衰减置信度

        逻辑：相似度越低，惩罚越大，但保留最低 0.2 的权重
        """
        penalty = (1 - similarity_score) * self.confidence_decay_factor
        return max(0.2, 1 - penalty)

    def _calculate_confidence_interval(
        self,
        base_confidence: float,
        adjusted_confidence: float,
        support_count: int,
        similarity_score: float,
    ) -> List[float]:
        """
        简易置信区间估算：方差基于原始置信度，相似度只影响区间宽度一次
        """
        support = max(1, support_count)
        base_confidence = max(0.0, min(base_confidence, 1.0))
        variance = base_confidence * (1 - base_confidence)
        std_error = (variance / support) ** 0.5
        widen = 1 + (1 - similarity_score)  # 相似度低时放宽
        margin = std_error * widen
        lower = max(0.0, adjusted_confidence - margin)
        upper = min(1.0, adjusted_confidence + margin)
        return [round(lower, 3), round(upper, 3)]

    @staticmethod
    def _shorten(text: str, limit: int = 140) -> str:
        """裁剪长文本，减少 token"""
        if not text:
            return ""
        clean = re.sub(r"\s+", " ", text).strip()
        return clean if len(clean) <= limit else clean[: limit - 3] + "..."

    @staticmethod
    def _extract_json_block(text: str) -> Optional[str]:
        """尝试从文本中提取 JSON 块"""
        try:
            json.loads(text)
            return text
        except json.JSONDecodeError:
            pass

        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            candidate = match.group(0)
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                return None
        return None

    def _parse_response(self, text: str) -> Dict[str, Any]:
        """
        解析 LLM 响应为结构化数据。

        Args:
            text: LLM 返回的原始文本，期望为 JSON 格式

        Returns:
            解析后的字典，包含 summary, lessons, spot_checks 字段。
            如果解析失败，返回默认结构: {"summary": text[:200], "lessons": [], "spot_checks": []}
        """
        json_block = self._extract_json_block(text)
        if not json_block:
            return {"summary": text[:200], "lessons": [], "spot_checks": []}

        try:
            data = json.loads(json_block)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            # If JSON parsing fails, fall back to default summary/lessons/spot_checks.
            pass
        return {"summary": text[:200], "lessons": [], "spot_checks": []}
