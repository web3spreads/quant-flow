"""
即时反思模块（改进1a）

每笔交易平仓后的即时反思（纯规则，无 LLM 调用）。
参考论文：Adaptive Multi-Agent Bitcoin Trading (arXiv:2510.08068) — 双粒度反思

功能：
1. 计算交易结果与预期偏差
2. 提取当前 context_features
3. 查找匹配的现有经验
4. 根据交易结果更新匹配经验的 support_count 和 confidence
5. 输出结构化反思记录
"""

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


class InstantReflector:
    """每笔交易平仓后的即时反思（纯规则，无 LLM 调用）"""

    def __init__(
        self,
        memory_store,
        similarity_scorer,
        context_extractor,
        logger_instance=None,
        similarity_threshold: float = 0.6,
        min_support_for_update: int = 3,
        confidence_upper_bound: float = 0.85,
    ):
        """
        初始化即时反思器

        Args:
            memory_store: ReviewMemoryStore 实例
            similarity_scorer: SimilarityScorer 实例
            context_extractor: ContextExtractor 实例
            logger_instance: TradingLogger 实例（可选）
            similarity_threshold: 经验匹配的最小相似度阈值
            min_support_for_update: 经验最少需要多少次验证后才允许调整置信度
            confidence_upper_bound: 置信度上限，防止过度自信
        """
        self.memory_store = memory_store
        self.similarity_scorer = similarity_scorer
        self.context_extractor = context_extractor
        self._logger = logger_instance
        self.similarity_threshold = similarity_threshold
        self.min_support_for_update = min_support_for_update
        self.confidence_upper_bound = confidence_upper_bound

    def reflect_on_close(
        self,
        symbol: str,
        decision_record: dict[str, Any],
        trade_result: dict[str, Any],
        market_data: dict[str, Any],
    ) -> dict[str, Any]:
        """
        核心方法：平仓后执行即时反思

        Args:
            symbol: 交易对符号
            decision_record: 决策记录（包含 decision, reason, market_data 等）
            trade_result: 交易执行结果（包含 pnl, status 等）
            market_data: 当前市场数据

        Returns:
            结构化反思记录
        """
        now_text = datetime.utcnow().isoformat(timespec="seconds")

        # 1. 计算交易结果与预期偏差
        deviation = self._calculate_deviation(decision_record, trade_result)

        # 2. 提取当前 context_features
        context_features = self.context_extractor.extract(market_data)

        # 3. 判断交易是否盈利
        pnl = float(trade_result.get("pnl", 0) or trade_result.get("closed_pnl", 0) or 0)
        trade_profitable = pnl > 0

        # 4. 更新匹配的现有经验
        updated_count = self._update_matching_lessons(symbol, context_features, trade_profitable)

        # 5. 构建反思记录
        reflection = {
            "symbol": symbol,
            "timestamp": now_text,
            "decision": decision_record.get("decision", "UNKNOWN"),
            "pnl": pnl,
            "trade_profitable": trade_profitable,
            "deviation": deviation,
            "context_features": context_features,
            "updated_lessons_count": updated_count,
        }

        if self._logger:
            direction = "盈利" if trade_profitable else "亏损"
            self._logger.print_info(
                f"[{symbol}] 即时反思完成 | {direction} ${pnl:+.2f} | 更新 {updated_count} 条经验"
            )

        return reflection

    def _calculate_deviation(
        self, decision_record: dict[str, Any], trade_result: dict[str, Any]
    ) -> dict[str, Any]:
        """
        计算偏差：预期盈亏比 vs 实际盈亏比、预期持仓时间 vs 实际持仓时间

        Args:
            decision_record: 决策记录
            trade_result: 交易结果

        Returns:
            偏差分析结果
        """
        deviation = {
            "pnl_deviation": 0.0,
            "holding_time_deviation": 0.0,
            "expected_pnl": 0.0,
            "actual_pnl": 0.0,
        }

        # 实际盈亏
        actual_pnl = float(trade_result.get("pnl", 0) or trade_result.get("closed_pnl", 0) or 0)
        deviation["actual_pnl"] = actual_pnl

        # 预期盈亏（从决策记录中提取，如果有的话）
        action_details = decision_record.get("action_details", {}) or {}
        expected_tp = float(action_details.get("take_profit_ratio", 0) or 0)
        expected_sl = float(action_details.get("stop_loss_ratio", 0) or 0)
        trade_amount = float(action_details.get("amount", 0) or action_details.get("size", 0) or 0)

        if expected_tp > 0 and trade_amount > 0:
            deviation["expected_pnl"] = trade_amount * expected_tp
            if deviation["expected_pnl"] != 0:
                deviation["pnl_deviation"] = actual_pnl / deviation["expected_pnl"] - 1.0
        elif expected_sl > 0 and actual_pnl < 0 and trade_amount > 0:
            expected_loss = -trade_amount * expected_sl
            if expected_loss != 0:
                deviation["pnl_deviation"] = actual_pnl / expected_loss - 1.0

        # 持仓时间偏差（如果有记录）
        entry_time = decision_record.get("timestamp")
        exit_time = trade_result.get("timestamp") or trade_result.get("close_time")
        if entry_time and exit_time:
            try:
                if isinstance(entry_time, str):
                    entry_dt = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
                else:
                    entry_dt = entry_time
                if isinstance(exit_time, str):
                    exit_dt = datetime.fromisoformat(exit_time.replace("Z", "+00:00"))
                else:
                    exit_dt = exit_time
                deviation["holding_time_deviation"] = (exit_dt - entry_dt).total_seconds() / 3600
            except (ValueError, TypeError):
                pass

        return deviation

    def _update_matching_lessons(
        self, symbol: str, context_features: dict[str, Any], trade_profitable: bool
    ) -> int:
        """
        更新匹配经验的 support_count 和 confidence

        Args:
            symbol: 交易对符号
            context_features: 当前环境特征
            trade_profitable: 交易是否盈利

        Returns:
            更新的经验数量
        """
        if not self.memory_store:
            return 0

        lessons = self.memory_store.get_lessons(symbol)
        if not lessons:
            return 0

        updated_count = 0
        for lesson in lessons:
            lesson_context = lesson.get("context_features", {})
            if not lesson_context:
                continue

            sim = self.similarity_scorer.compute(context_features, lesson_context)
            if sim < self.similarity_threshold:
                continue

            # 更新 support_count
            support_count = lesson.get("support_count", 1)
            lesson["support_count"] = support_count + 1

            # 贝叶斯更新置信度：新证据与历史按样本量加权平均
            # 仅当积累足够样本后才调整，避免单笔交易噪声驱动置信度漂移
            if support_count >= self.min_support_for_update:
                current_confidence = lesson.get("confidence", 0.5)
                # 单笔结果信号：盈利 +1，亏损 -1，映射到 [0, 1]
                event_signal = 1.0 if trade_profitable else 0.0
                # 贝叶斯加权：历史权重 = support_count / (support_count + 1)
                new_confidence = (support_count * current_confidence + event_signal) / (
                    support_count + 1
                )
                lesson["confidence"] = round(
                    max(0.1, min(self.confidence_upper_bound, new_confidence)), 3
                )

            lesson["last_seen"] = datetime.utcnow().isoformat(timespec="seconds")
            updated_count += 1

        if updated_count > 0:
            self.memory_store.save()

        return updated_count
