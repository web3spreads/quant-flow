"""
复盘经验持久化存储
用于在不同运行之间保留复盘 Agent 的经验规则
"""

import json
import math
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from src.agent.similarity_scorer import SimilarityScorer


class ReviewMemoryStore:
    """复盘经验存储，支持简单的基于文件的持久化（线程安全）"""

    def __init__(self, path: str, max_lessons: int = 30):
        self.path = Path(path)
        self.max_lessons = max_lessons
        self._lock = threading.RLock()
        self.lessons: dict[str, list[dict[str, Any]]] = {}
        self.load()

    def load(self):
        """
        从磁盘加载经验规则到内存。

        文件格式期望为 JSON，包含 {"lessons": {symbol: [rules]}} 结构。
        如果文件不存在或解析失败，会重置为空字典以避免阻塞主流程。
        兼容旧格式（列表格式）并自动转换为新格式。
        """
        with self._lock:
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
            except (json.JSONDecodeError, OSError) as e:
                # 若解析失败，重置为空以避免阻塞主流程
                import logging

                logging.getLogger(__name__).warning(f"加载经验文件失败: {e}")
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
                # 改进2: Regime 感知记忆
                item.setdefault("source_regime", "unknown")
                # 改进3: 确认偏差防护
                item.setdefault("lesson_type", "unknown")
                # 改进4: 事实-主观分离
                item.setdefault("source_type", "mixed")

    def save(self):
        """保存经验到磁盘"""
        with self._lock:
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
        with self._lock:
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
        current_regime: str | None = None,
        regime_mismatch_factor: float = 0.4,
    ) -> list[dict[str, Any]]:
        """
        根据相似度筛选经验规则

        Args:
            symbol: 交易对符号
            context_features: 当前环境特征
            scorer: 相似度计算器
            similarity_threshold: 最小相似度阈值
            limit: 返回数量上限
            current_regime: 当前市场 Regime（trending/ranging/volatile），用于 Regime 感知过滤
            regime_mismatch_factor: Regime 不匹配时的相似度降权因子
        """
        lessons = self.get_lessons(symbol)
        scored: list[dict[str, Any]] = []
        for lesson in lessons:
            sim = scorer.compute(context_features, lesson.get("context_features", {}))

            # 改进2: Regime 感知 — Regime 不匹配时按兼容性矩阵降权
            if current_regime:
                lesson_regime = lesson.get("source_regime", "unknown")
                if lesson_regime != "unknown" and lesson_regime != current_regime:
                    compatibility = self._regime_compatibility(
                        lesson_regime, current_regime, regime_mismatch_factor
                    )
                    sim *= compatibility

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

    def get_verbal_finetuning_section(
        self,
        symbol: str,
        limit: int = 5,
        current_regime: str | None = None,
        trending_subjective_boost: float = 1.3,
        ranging_factual_boost: float = 1.3,
    ) -> str:
        """
        生成结构化的 Verbal Fine-tuning 注入段落（参考 arXiv:2510.08068）。

        与 get_lessons_summary 的区别：
        - 高优先级标记，要求 LLM 优先参考
        - 按置信度+证据数量综合排序，突出最可靠规则
        - 区分「高置信」和「待验证」规则，减少噪声干扰
        - 改进2: 标注 Regime 来源
        - 改进3: 标注 negative 经验（[避免]前缀）
        - 改进4: 根据 Regime 调整事实/主观经验权重

        Args:
            symbol: 交易对符号
            limit: 返回经验数量上限
            current_regime: 当前市场 Regime，用于调整排序权重
            trending_subjective_boost: 趋势市中主观经验的权重提升
            ranging_factual_boost: 震荡/高波动市中事实经验的权重提升

        Returns:
            格式化的 Markdown 文本，用于直接注入 Prompt 决策上下文。
        """
        lessons = self.get_lessons(symbol)
        if not lessons:
            return ""

        # 综合评分 = 置信度 * log(1 + 证据数)，平衡置信度和可重复性
        def score(lesson: dict) -> float:
            conf = lesson.get("confidence", 0)
            support = lesson.get("support_count", 1)

            # 低样本量惩罚：仅验证 1 次的经验降权，避免噪声经验排序过高
            if support <= 1:
                conf *= 0.7
            elif support <= 2:
                conf *= 0.85

            base_score = conf * math.log1p(support)

            # 改进4: 根据当前 Regime 调整事实/主观权重
            # 但 negative 经验不做 regime boost，避免错误强化"避免"型经验
            if current_regime and lesson.get("lesson_type") != "negative":
                source_type = lesson.get("source_type", "mixed")
                if current_regime == "trending" and source_type == "subjective":
                    base_score *= trending_subjective_boost
                elif current_regime in ("ranging", "volatile") and source_type == "factual":
                    base_score *= ranging_factual_boost

            return base_score

        lessons_sorted = sorted(lessons, key=score, reverse=True)[:limit]

        high_conf = [lesson for lesson in lessons_sorted if lesson.get("confidence", 0) >= 0.6]
        low_conf = [lesson for lesson in lessons_sorted if lesson.get("confidence", 0) < 0.6]

        lines = [f"## 🧠 {symbol} 复盘经验（优先参考，在做决策前必须逐条对照）\n"]

        if high_conf:
            lines.append("**高置信规则（已被多次验证）：**")
            for idx, lesson in enumerate(high_conf):
                prefix = self._lesson_prefix(lesson)
                lines.append(
                    f"  {idx + 1}. {prefix}当 {lesson.get('rule')} → 应 {lesson.get('action')} "
                    f"（置信度 {lesson.get('confidence', 0):.2f}，验证 {lesson.get('support_count', 1)} 次）"
                )

        if low_conf:
            lines.append("\n**待验证规则（参考但不强制）：**")
            for idx, lesson in enumerate(low_conf):
                prefix = self._lesson_prefix(lesson)
                lines.append(
                    f"  {idx + 1}. {prefix}当 {lesson.get('rule')} → 应 {lesson.get('action')} "
                    f"（置信度 {lesson.get('confidence', 0):.2f}）"
                )

        return "\n".join(lines)

    def _lesson_prefix(self, lesson: dict) -> str:
        """生成经验前缀标注（Regime 来源 + 类型标注）"""
        parts = []
        # 改进3: negative 经验标注
        lesson_type = lesson.get("lesson_type", "unknown")
        if lesson_type == "negative":
            parts.append("[避免]")

        # 改进2: Regime 来源标注
        regime = lesson.get("source_regime", "unknown")
        regime_labels = {
            "trending": "[趋势市经验]",
            "ranging": "[震荡市经验]",
            "volatile": "[高波动市经验]",
        }
        if regime in regime_labels:
            parts.append(regime_labels[regime])

        # 改进4: 事实/主观标注
        source_type = lesson.get("source_type", "mixed")
        type_labels = {"factual": "[事实型]", "subjective": "[主观型]"}
        if source_type in type_labels:
            parts.append(type_labels[source_type])

        return "".join(parts) if parts else ""

    @staticmethod
    def _regime_compatibility(
        source_regime: str, current_regime: str, default_factor: float = 0.4
    ) -> float:
        """
        计算两个 Regime 之间的兼容性因子

        不同 Regime 组合的经验外推风险不同：
        - trending ↔ ranging：中等风险（部分策略可复用）
        - trending ↔ volatile：高风险（趋势策略在高波动中容易止损）
        - ranging ↔ volatile：较高风险
        """
        # 兼容性矩阵：(source, current) → 因子
        # 值越低 = 越不兼容 = 降权越大
        compatibility_matrix = {
            ("trending", "ranging"): 0.4,
            ("ranging", "trending"): 0.4,
            ("trending", "volatile"): 0.2,
            ("volatile", "trending"): 0.2,
            ("ranging", "volatile"): 0.3,
            ("volatile", "ranging"): 0.3,
        }
        return compatibility_matrix.get((source_regime, current_regime), default_factor)

    def get_lesson_type_stats(self, symbol: str) -> dict[str, Any]:
        """
        返回指定交易对经验的类型统计

        Args:
            symbol: 交易对符号

        Returns:
            包含各类型数量和比例的字典
        """
        lessons = self.get_lessons(symbol)
        total = len(lessons)
        if total == 0:
            return {
                "total": 0,
                "positive": 0,
                "negative": 0,
                "unknown": 0,
                "positive_ratio": 0.0,
                "negative_ratio": 0.0,
            }

        positive = sum(1 for ls in lessons if ls.get("lesson_type") == "positive")
        negative = sum(1 for ls in lessons if ls.get("lesson_type") == "negative")
        unknown = total - positive - negative

        return {
            "total": total,
            "positive": positive,
            "negative": negative,
            "unknown": unknown,
            "positive_ratio": positive / total,
            "negative_ratio": negative / total,
        }

    def add_lessons(
        self,
        symbol: str,
        lessons: list[dict[str, Any]],
        min_confidence: float = 0.35,
        current_regime: str | None = None,
        max_positive_ratio: float = 0.7,
    ) -> list[dict[str, Any]]:
        """
        添加新的经验规则，并自动合并/去重

        Args:
            symbol: 交易对符号
            lessons: 经验列表
            min_confidence: 最小置信度阈值
            current_regime: 当前市场 Regime，写入经验（改进2）
            max_positive_ratio: 最大正面经验比例，用于确认偏差防护（改进3）

        Returns:
            被采纳的经验列表
        """
        if not lessons:
            return []

        with self._lock:
            return self._add_lessons_locked(
                symbol, lessons, min_confidence, current_regime, max_positive_ratio
            )

    def _add_lessons_locked(
        self,
        symbol: str,
        lessons: list[dict[str, Any]],
        min_confidence: float,
        current_regime: str | None,
        max_positive_ratio: float,
    ) -> list[dict[str, Any]]:
        """add_lessons 的内部实现（已持有锁）"""
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
                # 改进2: Regime 感知
                "source_regime": item.get("source_regime") or current_regime or "unknown",
                # 改进3: 确认偏差防护
                "lesson_type": item.get("lesson_type", "unknown"),
                # 改进4: 事实-主观分离
                "source_type": item.get("source_type", "mixed"),
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
                # 更新 regime/type 字段（如果新值非默认）
                if normalized["source_regime"] != "unknown":
                    found["source_regime"] = normalized["source_regime"]
                if normalized["lesson_type"] != "unknown":
                    found["lesson_type"] = normalized["lesson_type"]
                if normalized["source_type"] != "mixed":
                    found["source_type"] = normalized["source_type"]
                found["last_seen"] = now_text
                accepted.append(found)
            else:
                normalized["last_seen"] = now_text
                bucket.append(normalized)
                accepted.append(normalized)

        # 控制每个 symbol 的经验数量（改进3: 偏差防护淘汰策略）
        if len(bucket) > self.max_lessons:
            bucket = self._evict_with_bias_protection(bucket, max_positive_ratio)
            self.lessons[symbol] = bucket

        if accepted:
            self.save()

        return accepted

    def _evict_with_bias_protection(
        self, bucket: list[dict[str, Any]], max_positive_ratio: float
    ) -> list[dict[str, Any]]:
        """
        淘汰超限经验，同时保护 negative 经验不被过度淘汰（改进3）

        淘汰策略综合考量：
        1. 样本量（support_count）：多次验证的经验更值得保留
        2. 置信度：高置信度优先保留
        3. 时效性：最近使用的经验优先保留
        4. 类型保护：当 negative 比例不足时，优先淘汰 positive 中低分经验
        """
        if len(bucket) <= self.max_lessons:
            return bucket

        # 统计类型分布（基于淘汰后目标容量判断，而非当前总量）
        negative_count = sum(1 for item in bucket if item.get("lesson_type") == "negative")
        min_negative_ratio = 1.0 - max_positive_ratio
        min_negative_needed = max(1, int(self.max_lessons * min_negative_ratio))
        # 如果淘汰可能导致 negative 低于最低保留数，触发保护
        protect_negative = negative_count <= min_negative_needed or (
            negative_count > 0 and negative_count / len(bucket) < min_negative_ratio + 0.1
        )

        def eviction_score(lesson: dict) -> float:
            """综合评分：高分的经验优先保留（后排的先被淘汰）"""
            conf = lesson.get("confidence", 0)
            support = lesson.get("support_count", 1)
            # 样本量贡献：log1p(support) 平滑，避免单次验证经验得分过高
            support_score = math.log1p(support) * 0.1
            # 综合分 = 置信度 + 样本量贡献
            score = conf + support_score
            # 当 negative 比例不足时，positive 经验的淘汰优先级更高（分数不加保护）
            if protect_negative and lesson.get("lesson_type") == "negative":
                score += 0.15  # 保护分，但低于旧版 0.2，因为现在有 support_score 补充
            return score

        bucket.sort(key=eviction_score, reverse=True)
        return bucket[: self.max_lessons]
