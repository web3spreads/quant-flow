"""
复盘 Agent
负责读取近期决策，生成结构化的经验规则

v2.0 增强版：
- 增加决策效果评估，分析历史决策的实际盈亏
- 根据市场状态分类提取经验
- 动态调整经验权重（时间衰减、有效性验证）
- 增加反馈循环验证经验效果

v2.1 抗过拟合增强：
- 市场状态多样性检查
- 经验泛化机制（将具体价格转为相对值）
- 交叉验证机制
"""

import json
import re
from datetime import datetime
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from src.agent.context_extractor import ContextExtractor
from src.agent.review_daily_logger import ReviewDailyLogger
from src.agent.review_memory import ReviewMemoryStore
from src.agent.similarity_scorer import SimilarityScorer
from src.llm import LLMClientManager
from src.prompt_manager import PromptManager
from src.utils.logger import TradingLogger

# 抗过拟合泛化模块（可选依赖）
try:
    from src.agents.review.generalization import enhance_lessons_with_generalization

    HAS_GENERALIZATION = True
except ImportError:
    HAS_GENERALIZATION = False


class DecisionEffectivenessAnalyzer:
    """决策效果分析器"""

    @staticmethod
    def analyze(records: list[dict[str, Any]]) -> dict[str, Any]:
        """
        分析决策记录的效果

        Args:
            records: 决策记录列表

        Returns:
            效果分析结果
        """
        result = {
            "total_decisions": len(records),
            "active_decisions": 0,  # 非观望决策数
            "profitable_decisions": 0,
            "losing_decisions": 0,
            "neutral_decisions": 0,
            "win_rate": 0.0,
            "avg_profit": 0.0,
            "avg_loss": 0.0,
            "profit_factor": 1.0,
            "max_consecutive_wins": 0,
            "max_consecutive_losses": 0,
            "decision_patterns": [],  # 决策模式分析
            "market_condition_performance": {},  # 不同市况下的表现
        }

        if not records:
            return result

        profits = []
        losses = []
        consecutive_wins = 0
        consecutive_losses = 0
        max_consecutive_wins = 0
        max_consecutive_losses = 0

        # 按市况分类的表现
        market_performance: dict[str, dict[str, Any]] = {}

        for record in records:
            decision = record.get("decision", "").upper()
            action_details = record.get("action_details", {}) or {}
            market_data = record.get("market_data", {}) or {}

            # 跳过观望决策
            if "DO_NOTHING" in decision or "HOLD" in decision:
                result["neutral_decisions"] += 1
                continue

            result["active_decisions"] += 1

            # 提取盈亏信息
            pnl = 0
            if action_details:
                pnl = action_details.get("pnl", 0) or action_details.get("closed_pnl", 0) or 0

            # 提取市场状态
            rsi = market_data.get("rsi", 50)
            trend = "neutral"
            if rsi > 60:
                trend = "bullish"
            elif rsi < 40:
                trend = "bearish"

            # 初始化市场状态统计
            if trend not in market_performance:
                market_performance[trend] = {
                    "total": 0,
                    "profitable": 0,
                    "losing": 0,
                    "total_pnl": 0,
                }

            market_performance[trend]["total"] += 1
            market_performance[trend]["total_pnl"] += pnl

            if pnl > 0:
                result["profitable_decisions"] += 1
                profits.append(pnl)
                market_performance[trend]["profitable"] += 1
                consecutive_wins += 1
                consecutive_losses = 0
                max_consecutive_wins = max(max_consecutive_wins, consecutive_wins)
            elif pnl < 0:
                result["losing_decisions"] += 1
                losses.append(abs(pnl))
                market_performance[trend]["losing"] += 1
                consecutive_losses += 1
                consecutive_wins = 0
                max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)

        # 计算统计指标
        total_closed = result["profitable_decisions"] + result["losing_decisions"]
        if total_closed > 0:
            result["win_rate"] = result["profitable_decisions"] / total_closed

        if profits:
            result["avg_profit"] = sum(profits) / len(profits)
        if losses:
            result["avg_loss"] = sum(losses) / len(losses)

        # 盈利因子
        total_profit = sum(profits) if profits else 0
        total_loss = sum(losses) if losses else 1
        result["profit_factor"] = total_profit / total_loss if total_loss > 0 else total_profit

        result["max_consecutive_wins"] = max_consecutive_wins
        result["max_consecutive_losses"] = max_consecutive_losses
        result["market_condition_performance"] = market_performance

        # 识别决策模式
        result["decision_patterns"] = DecisionEffectivenessAnalyzer._identify_patterns(records)

        return result

    @staticmethod
    def _identify_patterns(records: list[dict[str, Any]]) -> list[str]:
        """识别决策模式"""
        patterns = []

        if not records:
            return patterns

        # 统计各类决策
        decision_counts: dict[str, int] = {}
        for r in records:
            d = r.get("decision", "UNKNOWN").upper()
            decision_counts[d] = decision_counts.get(d, 0) + 1

        total = len(records)

        # 识别主导决策模式
        for decision, count in decision_counts.items():
            ratio = count / total
            if ratio > 0.5:
                patterns.append(f"主导决策: {decision} ({ratio:.0%})")
            elif ratio > 0.3:
                patterns.append(f"频繁决策: {decision} ({ratio:.0%})")

        # 识别连续决策模式
        consecutive_same = 1
        max_consecutive = 1
        prev_decision = None
        for r in records:
            d = r.get("decision", "")
            if d == prev_decision:
                consecutive_same += 1
                max_consecutive = max(max_consecutive, consecutive_same)
            else:
                consecutive_same = 1
            prev_decision = d

        if max_consecutive >= 3:
            patterns.append(f"存在连续{max_consecutive}次相同决策")

        return patterns


class LessonValidator:
    """经验验证器 - 验证历史经验的有效性"""

    @staticmethod
    def validate_lesson(
        lesson: dict[str, Any],
        recent_records: list[dict[str, Any]],
        similarity_scorer: "SimilarityScorer",
    ) -> dict[str, Any]:
        """
        验证经验在最近交易中的有效性

        Args:
            lesson: 经验规则
            recent_records: 最近的交易记录
            similarity_scorer: 相似度计算器

        Returns:
            验证结果
        """
        result = {
            "is_valid": True,
            "effectiveness_score": 0.5,
            "matching_records": 0,
            "successful_applications": 0,
            "failed_applications": 0,
            "recommendation": "maintain",  # maintain, boost, demote, remove
        }

        if not recent_records or not lesson:
            return result

        lesson_context = lesson.get("context_features", {})
        lesson_action = lesson.get("action", "").upper()

        matching_records = []

        # 找出与经验上下文相似的记录
        for record in recent_records:
            record_context = {}
            market_data = record.get("market_data", {})
            if market_data:
                record_context = {
                    "rsi": market_data.get("rsi", 50),
                    "trend_direction": "up" if market_data.get("price_change", 0) > 0 else "down",
                    "volatility_level": "normal",
                }

            if lesson_context:
                similarity = similarity_scorer.compute(lesson_context, record_context)
                if similarity >= 0.6:  # 相似度阈值
                    matching_records.append({"record": record, "similarity": similarity})

        result["matching_records"] = len(matching_records)

        if not matching_records:
            result["recommendation"] = "maintain"
            return result

        # 分析匹配记录中的决策效果
        for match in matching_records:
            record = match["record"]
            decision = record.get("decision", "").upper()
            action_details = record.get("action_details", {}) or {}
            pnl = action_details.get("pnl", 0) or 0

            # 检查决策是否符合经验建议
            action_matched = (
                ("BUY" in lesson_action and "BUY" in decision)
                or ("SELL" in lesson_action and "SELL" in decision)
                or ("HOLD" in lesson_action and "DO_NOTHING" in decision)
            )

            if action_matched:
                if pnl > 0:
                    result["successful_applications"] += 1
                elif pnl < 0:
                    result["failed_applications"] += 1

        # 计算有效性得分
        total_applications = result["successful_applications"] + result["failed_applications"]
        if total_applications > 0:
            result["effectiveness_score"] = result["successful_applications"] / total_applications
        else:
            result["effectiveness_score"] = 0.5  # 无数据时保持中性

        # 确定建议（需要最少 5 个匹配样本才有统计意义）
        min_samples = 5
        if total_applications < min_samples:
            # 样本不足，不做调整，避免少量噪声驱动的过拟合
            result["recommendation"] = "maintain"
            result["insufficient_samples"] = True
        elif result["effectiveness_score"] >= 0.7:
            result["recommendation"] = "boost"
            result["is_valid"] = True
        elif result["effectiveness_score"] >= 0.4:
            result["recommendation"] = "maintain"
            result["is_valid"] = True
        elif result["effectiveness_score"] >= 0.2:
            result["recommendation"] = "demote"
            result["is_valid"] = True
        else:
            result["recommendation"] = "remove"
            result["is_valid"] = False

        return result


class ReviewAgent:
    """复盘 Agent：提炼经验并输出结构化 JSON（增强版）"""

    def __init__(
        self,
        logger: TradingLogger,
        prompt_manager: PromptManager,
        llm_manager: LLMClientManager,
        temperature: float = 0.05,
        lookback_decisions: int = 12,
        memory_store: ReviewMemoryStore | None = None,
        min_confidence: float = 0.35,
        similarity_threshold: float = 0.5,
        similarity_weights: dict[str, float] | None = None,
        confidence_decay_factor: float = 0.6,
        similarity_method: str = "cosine",
        notifier=None,
        daily_logger: ReviewDailyLogger | None = None,
        enable_lesson_validation: bool = True,
        time_decay_days: int = 30,
    ):
        self.logger = logger
        self.prompt_manager = prompt_manager
        self.llm_manager = llm_manager
        self.lookback_decisions = lookback_decisions
        self.memory_store = memory_store
        self.min_confidence = min_confidence
        self.similarity_threshold = similarity_threshold
        self.confidence_decay_factor = confidence_decay_factor
        self.notifier = notifier
        self.daily_logger = daily_logger
        self.enable_lesson_validation = enable_lesson_validation
        self.time_decay_days = time_decay_days

        self.llm = self.llm_manager.get_client(json_mode=True, temperature=temperature)

        system_prompt = self.prompt_manager.get_review_system_prompt()
        self.system_message = SystemMessage(content=system_prompt)
        self.context_extractor = ContextExtractor()
        self.similarity_scorer = SimilarityScorer(
            weights=similarity_weights, method=similarity_method
        )

        # 增强版组件
        self.effectiveness_analyzer = DecisionEffectivenessAnalyzer()
        self.lesson_validator = LessonValidator()

    def review(
        self,
        symbol: str,
        decision_records: list[dict[str, Any]],
        fills_summary: dict[str, Any] | None = None,
        existing_lessons: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """执行复盘（增强版）"""
        if not decision_records:
            return {"lessons": [], "summary": "", "spot_checks": [], "effectiveness": {}}

        records = decision_records[-self.lookback_decisions :]
        digest = self._build_decision_digest(records)
        stats = self._calculate_stats(records)

        # 增强：分析决策效果
        effectiveness = self.effectiveness_analyzer.analyze(records)

        current_context = self.context_extractor.extract(
            records[-1].get("market_data", {}),
            decision_records=records,
        )

        # 获取相似经验
        similar_lessons: list[dict[str, Any]] = []
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

        # 增强：验证现有经验的有效性
        validated_lessons = []
        if self.enable_lesson_validation and existing_lessons:
            validated_lessons = self._validate_existing_lessons(existing_lessons, records)

        # 增强：添加效果分析到prompt
        enhanced_stats = {
            **stats,
            "win_rate": effectiveness.get("win_rate", 0),
            "profit_factor": effectiveness.get("profit_factor", 1.0),
            "max_consecutive_losses": effectiveness.get("max_consecutive_losses", 0),
            "decision_patterns": effectiveness.get("decision_patterns", []),
            "market_performance": effectiveness.get("market_condition_performance", {}),
        }

        prompt = self.prompt_manager.format_review_prompt(
            symbol=symbol,
            decision_digest=digest,
            stats=enhanced_stats,
            existing_lessons=(validated_lessons or existing_lessons or [])[:5],
            fills_summary=fills_summary or {"total_fills": 0, "total_pnl": 0.0},
            context_features=current_context,
        )

        self.logger.print_section(f"🧠 {symbol} 复盘 Agent 输入", style="bold white")
        self.logger.print_prompt(prompt)

        # 显示效果分析
        if effectiveness.get("active_decisions", 0) > 0:
            self.logger.print_info(
                f"决策效果: 胜率 {effectiveness.get('win_rate', 0):.1%}, "
                f"盈利因子 {effectiveness.get('profit_factor', 1.0):.2f}, "
                f"最大连亏 {effectiveness.get('max_consecutive_losses', 0)}次"
            )

        response = self.llm.invoke([self.system_message, HumanMessage(content=prompt)])

        raw_text = response.content if isinstance(response.content, str) else str(response.content)
        parsed = self._parse_response(raw_text)
        lessons = parsed.get("lessons", [])

        # 增强：基于效果分析调整经验置信度
        adjusted_lessons = self._adjust_lessons_by_effectiveness(lessons, effectiveness)

        filtered_lessons = self._enrich_lessons(
            lessons=adjusted_lessons,
            context_features=current_context,
        )

        # 增强：应用时间衰减
        filtered_lessons = self._apply_time_decay(filtered_lessons)

        # 增强：抗过拟合泛化处理（将具体数值转为相对表达，按市场多样性调整置信度）
        if HAS_GENERALIZATION and filtered_lessons:
            try:
                filtered_lessons = enhance_lessons_with_generalization(
                    lessons=filtered_lessons,
                    records=records,
                )
            except Exception as e:
                self.logger.print_warning(f"经验泛化处理失败，跳过: {e}")

        # 发送通知（如果有新经验且通知器可用）
        if filtered_lessons and self.notifier:
            try:
                self.notifier.notify_review_lesson(
                    symbol=symbol, lessons=filtered_lessons, summary=parsed.get("summary", "")
                )
            except Exception as e:
                self.logger.print_warning(f"发送复盘通知失败: {e}")

        # 记录到每日日志（用于 LoRA 训练）
        if self.daily_logger:
            try:
                self.daily_logger.log_review(
                    symbol=symbol,
                    prompt=prompt,
                    raw_output=raw_text,
                    lessons=filtered_lessons,
                    summary=parsed.get("summary", ""),
                    context_features=current_context,
                    decision_digest=digest,
                    stats=enhanced_stats,
                    fills_summary=fills_summary,
                    existing_lessons=existing_lessons,
                    spot_checks=parsed.get("spot_checks", []),
                )
            except Exception as e:
                self.logger.print_warning(f"记录每日日志失败: {e}")

        result = {
            "summary": parsed.get("summary", ""),
            "lessons": filtered_lessons,
            "spot_checks": parsed.get("spot_checks", []),
            "raw_output": raw_text,
            "prompt": prompt,
            "context_features": current_context,
            "effectiveness": effectiveness,
            "validated_lessons": validated_lessons,
        }

        return result

    def _validate_existing_lessons(
        self, lessons: list[dict[str, Any]], recent_records: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """验证现有经验的有效性并调整"""
        validated = []

        for lesson in lessons:
            validation_result = self.lesson_validator.validate_lesson(
                lesson, recent_records, self.similarity_scorer
            )

            # 根据验证结果调整置信度
            adjusted_lesson = lesson.copy()
            recommendation = validation_result.get("recommendation", "maintain")

            original_confidence = lesson.get("confidence", 0.5)

            if recommendation == "boost":
                # 提升有效经验的置信度
                adjusted_lesson["confidence"] = min(1.0, original_confidence * 1.2)
                adjusted_lesson["validation_status"] = "verified_effective"
            elif recommendation == "demote":
                # 降低效果不佳经验的置信度
                adjusted_lesson["confidence"] = original_confidence * 0.7
                adjusted_lesson["validation_status"] = "needs_review"
            elif recommendation == "remove":
                # 标记无效经验（但不直接删除，留给存储层决定）
                adjusted_lesson["confidence"] = original_confidence * 0.3
                adjusted_lesson["validation_status"] = "ineffective"
            else:
                adjusted_lesson["validation_status"] = "maintained"

            adjusted_lesson["effectiveness_score"] = validation_result.get(
                "effectiveness_score", 0.5
            )
            adjusted_lesson["matching_records"] = validation_result.get("matching_records", 0)

            validated.append(adjusted_lesson)

        # 按调整后的置信度排序
        validated.sort(key=lambda x: x.get("confidence", 0), reverse=True)
        return validated

    def _adjust_lessons_by_effectiveness(
        self, lessons: list[dict[str, Any]], effectiveness: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """根据整体决策效果调整新经验的置信度"""
        if not lessons:
            return lessons

        win_rate = effectiveness.get("win_rate", 0.5)
        profit_factor = effectiveness.get("profit_factor", 1.0)
        max_consecutive_losses = effectiveness.get("max_consecutive_losses", 0)

        adjusted = []
        for lesson in lessons:
            adjusted_lesson = lesson.copy()
            original_confidence = lesson.get("confidence", 0.5)

            # 整体效果调整因子
            effect_factor = 1.0

            # 基于胜率调整
            if win_rate >= 0.6:
                effect_factor *= 1.1  # 高胜率时略微提升
            elif win_rate <= 0.3:
                effect_factor *= 0.8  # 低胜率时降低

            # 基于盈利因子调整
            if profit_factor >= 1.5:
                effect_factor *= 1.1
            elif profit_factor < 0.8:
                effect_factor *= 0.85

            # 基于连续亏损调整
            if max_consecutive_losses >= 5:
                effect_factor *= 0.9  # 连续亏损过多时更谨慎

            # 置信度上限 0.85 防止过度自信（全量测试需大量样本才能接近 1.0）
            adjusted_lesson["confidence"] = min(0.85, max(0.1, original_confidence * effect_factor))
            adjusted_lesson["effect_adjustment_factor"] = effect_factor

            adjusted.append(adjusted_lesson)

        return adjusted

    def _apply_time_decay(self, lessons: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        应用指数时间衰减到经验置信度

        使用半衰期模型：每过 time_decay_days 天，置信度衰减到原来的一半。
        比线性衰减更符合信息老化规律 — 近期变化影响大，远期衰减趋于稳定。
        最低保留 30% 权重（旧版 50% 过于宽松）。
        """
        if not lessons or self.time_decay_days <= 0:
            return lessons

        now = datetime.now()
        # 指数衰减系数：半衰期 = time_decay_days → λ = ln(2) / half_life
        import math
        decay_lambda = math.log(2) / self.time_decay_days

        decayed = []
        for lesson in lessons:
            decayed_lesson = lesson.copy()

            # 优先使用 last_seen（最近被验证的时间），回退到 created_at
            time_ref = lesson.get("last_seen") or lesson.get("created_at")
            if time_ref:
                try:
                    if isinstance(time_ref, str):
                        ref_time = datetime.fromisoformat(time_ref.replace("Z", "+00:00"))
                    elif isinstance(time_ref, datetime):
                        ref_time = time_ref
                    else:
                        ref_time = now

                    days_elapsed = (now - ref_time).days
                    if days_elapsed > 0:
                        # 指数衰减：e^(-λt)，最低保留 30%
                        decay_factor = max(0.3, math.exp(-decay_lambda * days_elapsed))
                        original_confidence = decayed_lesson.get("confidence", 0.5)
                        decayed_lesson["confidence"] = round(
                            original_confidence * decay_factor, 3
                        )
                        decayed_lesson["time_decay_applied"] = True
                        decayed_lesson["decay_factor"] = round(decay_factor, 3)
                except (ValueError, TypeError, OverflowError):
                    pass  # 时间解析失败时不应用衰减

            decayed.append(decayed_lesson)

        return decayed

    def _build_decision_digest(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
                    "result": action_details.get("status") or action_details.get("decision", "N/A"),
                    "reason": self._shorten(reason),
                }
            )
        return digest

    def _calculate_stats(self, records: list[dict[str, Any]]) -> dict[str, Any]:
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
            "average_price": avg_price,
        }

    def _enrich_lessons(
        self,
        lessons: list[dict[str, Any]],
        context_features: dict[str, Any],
        negative_confidence_boost: float = 1.15,
    ) -> list[dict[str, Any]]:
        """为经验打上相似度、置信区间并按阈值过滤，同时推断 lesson_type 和 source_type"""
        if not lessons:
            return []

        enriched: list[dict[str, Any]] = []
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
            adjusted_confidence = round(base_confidence * env_match_factor, 3)
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

            # 改进3: 推断 lesson_type（如果 LLM 未返回）
            lesson_type = lesson.get("lesson_type", "unknown")
            if lesson_type == "unknown" or not lesson_type:
                lesson_type = self._infer_lesson_type(action)

            # 改进3: negative 经验置信度加成
            if lesson_type == "negative":
                adjusted_confidence = min(1.0, round(
                    adjusted_confidence * negative_confidence_boost, 3
                ))

            # 改进4: 推断 source_type（如果 LLM 未返回）
            source_type = lesson.get("source_type", "mixed")
            if source_type == "mixed" or not source_type:
                source_type = self._infer_source_type(rule, action)

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
                    "lesson_type": lesson_type,
                    "source_type": source_type,
                }
            )

        enriched.sort(key=lambda item: item.get("confidence", 0), reverse=True)
        return enriched

    @staticmethod
    def _infer_lesson_type(action: str) -> str:
        """
        改进3: 从 action 文本推断经验类型

        negative 关键词: 避免/不要/谨慎/不宜/禁止
        """
        negative_keywords = ["避免", "不要", "谨慎", "不宜", "禁止", "警惕", "减少"]
        for kw in negative_keywords:
            if kw in action:
                return "negative"
        return "positive"

    @staticmethod
    def _infer_source_type(rule: str, action: str) -> str:
        """
        改进4: 从 rule + action 文本推断信号来源类型

        factual: 可量化的技术指标和链上数据（RSI, MACD, 资金费率, MVRV 等）
        subjective: 主观判断和情绪类信号（市场情绪, 新闻, 舆论, 辩论等）
        """
        text = f"{rule} {action}"
        factual_keywords = [
            "RSI", "MACD", "EMA", "ATR", "成交量", "布林带", "支撑位", "阻力位",
            "链上", "MVRV", "SOPR", "K线", "均线", "MA", "BB", "rsi", "macd",
            "资金费率", "持仓量", "OI", "换手率", "清算",
        ]
        subjective_keywords = [
            "情绪", "新闻", "市场氛围", "叙事", "舆论", "辩论",
            "恐慌", "FOMO", "狂热", "共识", "预期",
        ]

        has_factual = any(kw in text for kw in factual_keywords)
        has_subjective = any(kw in text for kw in subjective_keywords)

        if has_factual and has_subjective:
            return "mixed"
        if has_factual:
            return "factual"
        if has_subjective:
            return "subjective"
        return "mixed"

    def _environment_match_factor(self, similarity_score: float) -> float:
        """
        根据相似度计算环境匹配度，低相似度时衰减置信度

        使用二次衰减而非线性，使高相似度区域更敏感（0.9 vs 0.8 差异更大），
        同时低相似度区域惩罚更严格，降低环境外推风险。
        最低保留 0.1 权重（而非 0.2），进一步限制不匹配环境的影响。
        """
        return max(0.1, similarity_score ** 2)

    def _calculate_confidence_interval(
        self,
        base_confidence: float,
        adjusted_confidence: float,
        support_count: int,
        similarity_score: float,
    ) -> list[float]:
        """
        简易置信区间估算：方差基于原始置信度，含小样本修正和相似度宽度调整

        小样本修正：support < 5 时额外放宽区间（模拟 t 分布比正态分布更宽的尾部）
        """
        support = max(1, support_count)
        base_confidence = max(0.0, min(base_confidence, 1.0))
        variance = base_confidence * (1 - base_confidence)
        std_error = (variance / support) ** 0.5
        # 小样本修正：样本不足 5 时放宽区间
        small_sample_factor = 1.0 + max(0, (5 - support)) * 0.15
        widen = 1 + (1 - similarity_score)  # 相似度低时放宽
        margin = std_error * widen * small_sample_factor
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
    def _extract_json_block(text: str) -> str | None:
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

    def _parse_response(self, text: str) -> dict[str, Any]:
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
