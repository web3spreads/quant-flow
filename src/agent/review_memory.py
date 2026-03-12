"""
复盘经验持久化存储
用于在不同运行之间保留复盘 Agent 的经验规则
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from src.agent.similarity_scorer import SimilarityScorer


class ReviewMemoryStore:
    """复盘经验存储，支持简单的基于文件的持久化"""

    def __init__(self, path: str, max_lessons: int = 30):
        self.path = Path(path)
        self.max_lessons = max_lessons
        self.lessons: dict[str, list[dict[str, Any]]] = {}
        self.load()

    def load(self):
        """
        从磁盘加载经验规则到内存。

        文件格式期望为 JSON，包含 {"lessons": {symbol: [rules]}} 结构。
        如果文件不存在或解析失败，会重置为空字典以避免阻塞主流程。
        兼容旧格式（列表格式）并自动转换为新格式。
        """
        try:
            if self.path.exists():
                with open(self.path, encoding="utf-8") as f:
                    data = json.load(f)

                if isinstance(data, dict) and isinstance(data.get("lessons"), dict):
                    self.lessons = data["lessons"]
                elif isinstance(data, list):
                    # 兼容旧格式
                    converted: dict[str, list[dict[str, Any]]] = {}
                    for item in data:
                        symbol = item.get("symbol", "GLOBAL")
                        converted.setdefault(symbol, []).append(item)
                    self.lessons = converted
                else:
                    self.lessons = {}
        except Exception:
            # 若解析失败，重置为空以避免阻塞主流程
            self.lessons = {}

        self._ensure_context_defaults()

    def _ensure_context_defaults(self):
        """为旧记录补充 context_features 等新字段"""
        for _symbol, items in self.lessons.items():
            for item in items:
                item.setdefault("context_features", {})
                item.setdefault("original_confidence", item.get("confidence", 0))
                item.setdefault("similarity_score", 0.0)
                item.setdefault("confidence_interval", [])

    def save(self):
        """保存经验到磁盘"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"lessons": self.lessons, "updated_at": datetime.utcnow().isoformat()}
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def get_lessons(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """
        获取指定交易对的经验列表。

        Args:
            symbol: 交易对符号（如 "BTC"）。如果为 None，返回所有交易对的经验。

        Returns:
            经验规则列表，按 last_seen 时间倒序排列（最新的在前）。
        """
        if symbol:
            lessons = list(self.lessons.get(symbol, []))
            return sorted(lessons, key=lambda x: x.get("last_seen", ""), reverse=True)

        # 全量（用于 Prompt 展示）
        aggregated: list[dict[str, Any]] = []
        for symbol_lessons in self.lessons.values():
            aggregated.extend(symbol_lessons)
        return sorted(aggregated, key=lambda x: x.get("last_seen", ""), reverse=True)

    def get_similar_lessons(
        self,
        symbol: str,
        context_features: dict[str, Any],
        scorer: SimilarityScorer,
        similarity_threshold: float = 0.5,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """
        根据相似度筛选经验规则

        Args:
            symbol: 交易对符号
            context_features: 当前环境特征
            scorer: 相似度计算器
            similarity_threshold: 最小相似度阈值
            limit: 返回数量上限
        """
        lessons = self.get_lessons(symbol)
        scored: list[dict[str, Any]] = []
        for lesson in lessons:
            sim = scorer.compute(context_features, lesson.get("context_features", {}))
            if sim < similarity_threshold:
                continue
            lesson_with_score = dict(lesson)
            lesson_with_score["similarity_score"] = sim
            scored.append(lesson_with_score)

        scored.sort(key=lambda item: item.get("similarity_score", 0), reverse=True)
        return scored[:limit]

    def get_lessons_summary(self, symbol: str, limit: int = 5) -> str:
        """
        将经验格式化为可嵌入 Prompt 的文本。

        Args:
            symbol: 交易对符号
            limit: 最多返回的经验条数，默认 5 条

        Returns:
            格式化的文本字符串，包含标题和编号列表。
            如果没有经验，返回空字符串。
        """
        lessons = self.get_lessons(symbol)[:limit]
        if not lessons:
            return ""

        lines = [
            f"{idx + 1}. {lesson.get('rule')} => {lesson.get('action')} "
            f"(置信度 {lesson.get('confidence', 0):.2f}, 证据 {lesson.get('support_count', 1)})"
            for idx, lesson in enumerate(lessons)
        ]
        return "### ♻️ 复盘经验\n" + "\n".join(lines)

    def get_verbal_finetuning_section(self, symbol: str, limit: int = 5) -> str:
        """
        生成结构化的 Verbal Fine-tuning 注入段落（参考 arXiv:2510.08068）。

        与 get_lessons_summary 的区别：
        - 高优先级标记，要求 LLM 优先参考
        - 按置信度+证据数量综合排序，突出最可靠规则
        - 区分「高置信」和「待验证」规则，减少噪声干扰

        Returns:
            格式化的 Markdown 文本，用于直接注入 Prompt 决策上下文。
        """
        lessons = self.get_lessons(symbol)
        if not lessons:
            return ""

        # 综合评分 = 置信度 * log(1 + 证据数)，平衡置信度和可重复性
        import math

        def score(lesson: dict) -> float:
            conf = lesson.get("confidence", 0)
            support = lesson.get("support_count", 1)
            return conf * math.log1p(support)

        lessons_sorted = sorted(lessons, key=score, reverse=True)[:limit]

        high_conf = [l for l in lessons_sorted if l.get("confidence", 0) >= 0.6]
        low_conf = [l for l in lessons_sorted if l.get("confidence", 0) < 0.6]

        lines = [f"## 🧠 {symbol} 复盘经验（优先参考，在做决策前必须逐条对照）\n"]

        if high_conf:
            lines.append("**高置信规则（已被多次验证）：**")
            for idx, lesson in enumerate(high_conf):
                lines.append(
                    f"  {idx + 1}. 当 {lesson.get('rule')} → 应 {lesson.get('action')} "
                    f"（置信度 {lesson.get('confidence', 0):.2f}，验证 {lesson.get('support_count', 1)} 次）"
                )

        if low_conf:
            lines.append("\n**待验证规则（参考但不强制）：**")
            for idx, lesson in enumerate(low_conf):
                lines.append(
                    f"  {idx + 1}. 当 {lesson.get('rule')} → 应 {lesson.get('action')} "
                    f"（置信度 {lesson.get('confidence', 0):.2f}）"
                )

        return "\n".join(lines)

    def add_lessons(
        self, symbol: str, lessons: list[dict[str, Any]], min_confidence: float = 0.35
    ) -> list[dict[str, Any]]:
        """
        添加新的经验规则，并自动合并/去重

        Returns:
            被采纳的经验列表
        """
        if not lessons:
            return []

        accepted: list[dict[str, Any]] = []
        bucket = self.lessons.setdefault(symbol, [])
        now_text = datetime.utcnow().isoformat(timespec="seconds")

        for item in lessons:
            rule = (item.get("rule") or "").strip()
            action = (item.get("action") or "").strip()
            confidence = float(item.get("confidence", 0) or 0)

            if not rule or not action or confidence < min_confidence:
                continue

            normalized = {
                "rule": rule[:80],
                "action": action[:80],
                "conditions": (item.get("conditions") or [])[:4],
                "confidence": round(confidence, 3),
                "original_confidence": float(item.get("original_confidence", confidence)),
                "evidence": (item.get("evidence") or [])[:5],
                "last_seen": item.get("last_seen", now_text),
                "support_count": int(item.get("support_count", 1)),
                "similarity_score": float(item.get("similarity_score", 0)),
                "confidence_interval": item.get("confidence_interval", []),
                "context_features": item.get("context_features") or {},
                "symbol": symbol,
            }

            found = next(
                (entry for entry in bucket if entry.get("rule") == normalized["rule"]),
                None,
            )

            if found:
                # 合并已有规则
                found["support_count"] = found.get("support_count", 1) + 1
                found["confidence"] = round((found.get("confidence", 0.5) + confidence) / 2, 3)
                found["original_confidence"] = round(
                    (
                        found.get("original_confidence", found["confidence"])
                        + normalized["original_confidence"]
                    )
                    / 2,
                    3,
                )
                found["conditions"] = normalized["conditions"] or found.get("conditions", [])
                found["evidence"] = normalized["evidence"] or found.get("evidence", [])
                found["similarity_score"] = max(
                    normalized.get("similarity_score", 0),
                    found.get("similarity_score", 0),
                )
                if normalized.get("confidence_interval"):
                    found["confidence_interval"] = normalized["confidence_interval"]
                if normalized.get("context_features"):
                    found["context_features"] = normalized["context_features"]
                found["last_seen"] = now_text
                accepted.append(found)
            else:
                normalized["last_seen"] = now_text
                bucket.append(normalized)
                accepted.append(normalized)

        # 控制每个 symbol 的经验数量
        if len(bucket) > self.max_lessons:
            bucket.sort(key=lambda x: x.get("last_seen", ""), reverse=True)
            self.lessons[symbol] = bucket[: self.max_lessons]

        if accepted:
            self.save()

        return accepted
