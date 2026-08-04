"""
每周系统性反思模块（改进1b）

每周策略级深度复盘（调用 LLM），识别系统性偏差和反复错误。
参考论文：Adaptive Multi-Agent Bitcoin Trading (arXiv:2510.08068) — 双粒度反思

功能：
1. 聚合一周内所有交易对的数据
2. 检测系统性偏差（连续同方向交易、特定时段表现差异等）
3. 检测反复错误（相同条件下反复犯的错误）
4. 调用 LLM 生成策略级调整建议
5. 保存到 logs/weekly_reflection/YYYY-WXX.json
"""

import json
import logging
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from src.llm.llm_client import wrap_llm_client

logger = logging.getLogger(__name__)


class WeeklyReflector:
    """每周策略级深度复盘（调用 LLM）"""

    def __init__(
        self,
        llm_manager,
        prompt_manager,
        memory_store,
        logger_instance=None,
        notifier=None,
        output_dir: str = "logs/weekly_reflection",
        temperature: float = 0.1,
        weekly_day: int = 0,
        weekly_hour: int = 8,
    ):
        """
        初始化每周反思器

        Args:
            llm_manager: LLMClientManager 实例
            prompt_manager: PromptManager 实例
            memory_store: ReviewMemoryStore 实例
            logger_instance: TradingLogger 实例（可选）
            notifier: Notifier 实例（可选）
            output_dir: 反思报告输出目录
            temperature: LLM 温度参数
            weekly_day: 每周运行的星期几（0=周一）
            weekly_hour: 每周运行的小时
        """
        self.llm_manager = llm_manager
        self.prompt_manager = prompt_manager
        self.memory_store = memory_store
        self._logger = logger_instance
        self.notifier = notifier
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.temperature = temperature
        self.weekly_day = weekly_day
        self.weekly_hour = weekly_hour

        self.llm = wrap_llm_client(
            self.llm_manager.get_client(json_mode=True, temperature=temperature)
        )

    def should_run(self, last_run_time: datetime | None = None) -> bool:
        """
        检查是否到了每周运行时间

        Args:
            last_run_time: 上次运行时间，None 表示从未运行

        Returns:
            是否应该运行
        """
        now = datetime.now()

        # 检查是否是指定的星期几和小时
        if now.weekday() != self.weekly_day:
            return False
        if now.hour < self.weekly_hour:
            return False

        # 如果从未运行过，应该运行
        if last_run_time is None:
            return True

        # 检查上次运行是否在本周之前
        days_since_last = (now - last_run_time).days
        return days_since_last >= 6

    def run_weekly_reflection(
        self,
        symbols: list[str],
        decision_history,
        fills_data: dict[str, list[dict]] | None = None,
    ) -> dict[str, Any]:
        """
        核心方法：执行每周策略级深度复盘

        Args:
            symbols: 交易对列表
            decision_history: DecisionHistory 实例
            fills_data: 各交易对的成交数据（可选）

        Returns:
            每周反思报告
        """
        if self._logger:
            self._logger.print_section("📊 运行每周策略级复盘", style="bold white")

        # 1. 聚合一周内所有交易对的数据
        all_records = []
        per_symbol_stats = {}
        for symbol in symbols:
            records = decision_history.get_recent_decisions(symbol, limit=100)
            # 筛选最近 7 天的记录
            week_records = self._filter_recent_week(records)
            all_records.extend(week_records)
            per_symbol_stats[symbol] = self._calculate_symbol_stats(week_records)

        if not all_records:
            if self._logger:
                self._logger.print_info("本周无交易记录，跳过每周复盘")
            return {"status": "skipped", "reason": "无交易记录"}

        # 2. 检测系统性偏差
        systematic_biases = self._detect_systematic_biases(all_records)

        # 3. 检测反复错误
        recurring_errors = self._detect_recurring_errors(all_records)

        # 4. 聚合各交易对的经验
        all_lessons = {}
        for symbol in symbols:
            if self.memory_store:
                all_lessons[symbol] = self.memory_store.get_lessons(symbol)[:5]

        # 5. 调用 LLM 生成策略级调整建议
        weekly_stats = {
            "total_records": len(all_records),
            "per_symbol": per_symbol_stats,
        }

        try:
            prompt = self.prompt_manager.format_weekly_review_prompt(
                weekly_stats=weekly_stats,
                systematic_biases=systematic_biases,
                recurring_errors=recurring_errors,
                all_symbols_summary=all_lessons,
            )

            system_prompt = self.prompt_manager.get_weekly_review_system_prompt()
            from pydantic_ai import Agent

            agent = Agent(self.llm, system_prompt=system_prompt)
            res = agent.run_sync(prompt)
            raw_text = res.output if isinstance(res.output, str) else str(res.output)

            # 解析响应
            try:
                analysis = json.loads(raw_text)
            except json.JSONDecodeError:
                analysis = {"summary": raw_text[:500], "suggestions": []}

        except Exception as e:
            if self._logger:
                self._logger.print_warning(f"每周复盘 LLM 调用失败: {e}")
            analysis = {"summary": f"LLM 调用失败: {str(e)}", "suggestions": []}

        # 6. 构建报告
        report = {
            "timestamp": datetime.now().isoformat(),
            "week": datetime.now().strftime("%Y-W%W"),
            "weekly_stats": weekly_stats,
            "systematic_biases": systematic_biases,
            "recurring_errors": recurring_errors,
            "analysis": analysis,
            "status": "completed",
        }

        # 7. 保存报告
        self._save_report(report)

        # 8. 发送通知
        if self.notifier:
            try:
                summary = analysis.get("summary", "每周复盘完成")
                self.notifier.send(f"📊 每周策略复盘\n{summary}")
            except Exception as e:
                if self._logger:
                    self._logger.print_warning(f"每周复盘通知发送失败: {e}")

        if self._logger:
            bias_count = len(systematic_biases)
            error_count = len(recurring_errors)
            self._logger.print_info(
                f"每周复盘完成 | 记录 {len(all_records)} 条 | "
                f"偏差 {bias_count} 项 | 反复错误 {error_count} 项"
            )

        return report

    def _filter_recent_week(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """筛选最近 7 天的记录"""
        now = datetime.now()
        result = []
        for record in records:
            ts = record.get("timestamp")
            if not ts:
                continue
            try:
                if isinstance(ts, str):
                    record_time = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                elif isinstance(ts, datetime):
                    record_time = ts
                else:
                    continue
                if (now - record_time).days <= 7:
                    result.append(record)
            except (ValueError, TypeError):
                continue
        return result

    def _calculate_symbol_stats(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        """计算单个交易对的统计数据"""
        if not records:
            return {"total": 0, "decisions": {}, "pnl": 0.0}

        decisions = Counter(r.get("decision", "UNKNOWN") for r in records)
        total_pnl = sum(
            float((r.get("action_details", {}) or {}).get("pnl", 0) or 0) for r in records
        )

        return {
            "total": len(records),
            "decisions": dict(decisions),
            "pnl": round(total_pnl, 2),
        }

    def _detect_systematic_biases(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        识别系统性偏差

        检测：连续同方向交易、特定时段表现差异、某币种持续亏损等
        """
        biases = []

        if not records:
            return biases

        # 1. 检测连续同方向交易
        consecutive_same = 1
        max_consecutive = 1
        prev_decision = None
        dominant_decision = None
        for r in records:
            d = r.get("decision", "")
            if d == prev_decision and d not in ("DO_NOTHING", "HOLD"):
                consecutive_same += 1
                if consecutive_same > max_consecutive:
                    max_consecutive = consecutive_same
                    dominant_decision = d
            else:
                consecutive_same = 1
            prev_decision = d

        if max_consecutive >= 4:
            biases.append(
                {
                    "type": "directional_bias",
                    "description": f"连续 {max_consecutive} 次 {dominant_decision} 决策",
                    "severity": "high" if max_consecutive >= 6 else "medium",
                }
            )

        # 2. 检测决策分布偏差
        decision_counts = Counter(
            r.get("decision", "UNKNOWN")
            for r in records
            if r.get("decision") not in ("DO_NOTHING", "HOLD")
        )
        total_active = sum(decision_counts.values())
        if total_active > 0:
            for decision, count in decision_counts.items():
                ratio = count / total_active
                if ratio > 0.7:
                    biases.append(
                        {
                            "type": "decision_distribution_bias",
                            "description": f"{decision} 占比 {ratio:.0%}，过于单一",
                            "severity": "medium",
                        }
                    )

        # 3. 检测按交易对分组的持续亏损
        symbol_pnl = defaultdict(float)
        for r in records:
            symbol = r.get("symbol") or r.get("market_data", {}).get("symbol", "UNKNOWN")
            pnl = float((r.get("action_details", {}) or {}).get("pnl", 0) or 0)
            symbol_pnl[symbol] += pnl

        for symbol, pnl in symbol_pnl.items():
            if pnl < -50:  # 超过 $50 亏损
                biases.append(
                    {
                        "type": "symbol_persistent_loss",
                        "description": f"{symbol} 本周累计亏损 ${pnl:.2f}",
                        "severity": "high" if pnl < -100 else "medium",
                    }
                )

        return biases

    def _detect_recurring_errors(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        识别反复错误

        检测：相同条件下反复犯的错误（如在高 RSI 时连续做多亏损）
        """
        errors = []

        if not records:
            return errors

        # 按市场状态分组统计亏损
        condition_losses = defaultdict(lambda: {"count": 0, "total_loss": 0.0})

        for r in records:
            pnl = float((r.get("action_details", {}) or {}).get("pnl", 0) or 0)
            if pnl >= 0:
                continue

            market_data = r.get("market_data", {}) or {}
            rsi = float(market_data.get("rsi", 50) or 50)
            decision = r.get("decision", "UNKNOWN")

            # 识别条件
            if rsi > 70 and decision in ("BUY", "SELL_SHORT"):
                key = f"RSI>70时{decision}"
            elif rsi < 30 and decision in ("SELL", "SELL_SHORT"):
                key = f"RSI<30时{decision}"
            else:
                key = f"{decision}_亏损"

            condition_losses[key]["count"] += 1
            condition_losses[key]["total_loss"] += abs(pnl)

        for condition, stats in condition_losses.items():
            if stats["count"] >= 3:
                errors.append(
                    {
                        "condition": condition,
                        "count": stats["count"],
                        "total_loss": round(stats["total_loss"], 2),
                        "suggestion": f"在 {condition} 条件下已反复亏损 {stats['count']} 次，建议调整策略",
                    }
                )

        return errors

    def _save_report(self, report: dict[str, Any]):
        """保存报告到 JSON 文件"""
        week_str = report.get("week", datetime.now().strftime("%Y-W%W"))
        file_path = self.output_dir / f"{week_str}.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        if self._logger:
            self._logger.print_info(f"每周复盘报告已保存: {file_path}")
