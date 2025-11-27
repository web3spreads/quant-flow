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
        min_confidence: float = 0.35
    ):
        self.logger = logger
        self.prompt_manager = prompt_manager
        self.lookback_decisions = lookback_decisions
        self.memory_store = memory_store
        self.min_confidence = min_confidence

        self.llm = ChatOpenAI(
            base_url=openai_api_base,
            api_key=openai_api_key,
            model=model,
            temperature=temperature,
        )

        system_prompt = self.prompt_manager.get_review_system_prompt()
        self.system_message = SystemMessage(content=system_prompt)

    def review(
        self,
        symbol: str,
        decision_records: List[Dict[str, Any]],
        fills_summary: Optional[Dict[str, Any]] = None,
        existing_lessons: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """执行复盘"""
        if not decision_records:
            return {"lessons": [], "summary": "", "spot_checks": []}

        records = decision_records[-self.lookback_decisions :]
        digest = self._build_decision_digest(records)
        stats = self._calculate_stats(records)

        if existing_lessons is None and self.memory_store:
            existing_lessons = self.memory_store.get_lessons(symbol)

        prompt = self.prompt_manager.format_review_prompt(
            symbol=symbol,
            decision_digest=digest,
            stats=stats,
            existing_lessons=(existing_lessons or [])[:5],
            fills_summary=fills_summary or {"total_fills": 0, "total_pnl": 0.0}
        )

        self.logger.print_section(f"🧠 {symbol} 复盘 Agent 输入", style="bold white")
        self.logger.print_prompt(prompt)

        response = self.llm.invoke([
            self.system_message,
            HumanMessage(content=prompt)
        ])

        raw_text = response.content if isinstance(response.content, str) else str(response.content)
        parsed = self._parse_response(raw_text)
        lessons = parsed.get("lessons", [])

        filtered_lessons = [
            lesson for lesson in lessons
            if (lesson.get("rule") and lesson.get("action") and float(lesson.get("confidence", 0)) >= self.min_confidence)
        ]

        result = {
            "summary": parsed.get("summary", ""),
            "lessons": filtered_lessons,
            "spot_checks": parsed.get("spot_checks", []),
            "raw_output": raw_text,
            "prompt": prompt
        }

        return result

    def _build_decision_digest(self, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """压缩决策历史为短摘要，控制 token"""
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
                "reason": self._shorten(reason)
            })
        return digest

    def _calculate_stats(self, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """统计基本指标，避免让 LLM 自己计算"""
        prices = [float(r.get("market_data", {}).get("current_price") or 0.0) for r in records]
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
            "average_price": avg_price
        }

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
        """解析 LLM 响应"""
        json_block = self._extract_json_block(text)
        if not json_block:
            return {"summary": text[:200], "lessons": [], "spot_checks": []}

        try:
            data = json.loads(json_block)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
        return {"summary": text[:200], "lessons": [], "spot_checks": []}
