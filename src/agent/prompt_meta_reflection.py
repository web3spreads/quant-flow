"""
Prompt 自优化模块（改进5）

评估 Prompt 效果并生成优化建议。
参考论文：ATLAS Adaptive-OPRO (arXiv:2510.15949) — Prompt 自优化

功能：
1. 评估 FinCoT 6步完成度
2. 评估经验引用率
3. 评估决策一致性
4. 评估置信度校准
5. 生成 Prompt 微调建议（需人工审核后手动应用）
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from src.llm.llm_client import wrap_llm_client

logger = logging.getLogger(__name__)


class PromptMetaReflector:
    """Prompt 效果评估与优化建议生成"""

    def __init__(
        self,
        llm_manager,
        prompt_manager,
        memory_store,
        logger_instance=None,
        output_dir: str = "logs/prompt_optimization",
        temperature: float = 0.1,
    ):
        """
        初始化 Prompt 元反思器

        Args:
            llm_manager: LLMClientManager 实例
            prompt_manager: PromptManager 实例
            memory_store: ReviewMemoryStore 实例
            logger_instance: TradingLogger 实例（可选）
            output_dir: 优化报告输出目录
            temperature: LLM 温度参数
        """
        self.llm_manager = llm_manager
        self.prompt_manager = prompt_manager
        self.memory_store = memory_store
        self._logger = logger_instance
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.temperature = temperature

        self.llm = wrap_llm_client(
            self.llm_manager.get_client(json_mode=True, temperature=temperature)
        )

    def evaluate_prompt_effectiveness(
        self,
        weekly_records: list[dict[str, Any]],
        lessons: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """
        评估 Prompt 效果

        评估维度：
        - fincot_completion: FinCoT 6步完成度
        - lesson_citation_rate: 决策理由中引用复盘经验的比例
        - decision_consistency: 相似环境下决策一致性
        - confidence_calibration: 高置信度决策是否真的更准确

        Args:
            weekly_records: 一周内的决策记录
            lessons: 复盘经验列表

        Returns:
            PromptEffectivenessReport
        """
        report = {
            "timestamp": datetime.now().isoformat(),
            "total_records": len(weekly_records),
            "fincot_completion": self._evaluate_fincot_completion(weekly_records),
            "lesson_citation_rate": self._evaluate_lesson_citation(weekly_records),
            "decision_consistency": self._evaluate_consistency(weekly_records),
            "confidence_calibration": self._evaluate_calibration(weekly_records),
            "overall_score": 0.0,
        }

        # 计算综合评分
        scores = [
            report["fincot_completion"].get("score", 0),
            report["lesson_citation_rate"].get("score", 0),
            report["decision_consistency"].get("score", 0),
            report["confidence_calibration"].get("score", 0),
        ]
        valid_scores = [s for s in scores if s > 0]
        report["overall_score"] = sum(valid_scores) / len(valid_scores) if valid_scores else 0.0

        return report

    def _evaluate_fincot_completion(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        """
        评估 FinCoT 6步完成度

        检查决策输出中各步骤是否有实质内容
        """
        fincot_steps = ["趋势确认", "入场信号", "情绪校验", "复盘比对", "风险计算", "最终决策"]
        step_counts = dict.fromkeys(fincot_steps, 0)
        total_checked = 0

        for record in records:
            reason = record.get("reason", "") or ""
            ai_response = (record.get("action_details", {}) or {}).get("output", "") or ""
            combined_text = f"{reason} {ai_response}"

            if not combined_text.strip():
                continue

            total_checked += 1
            for step in fincot_steps:
                if step in combined_text:
                    step_counts[step] += 1

        if total_checked == 0:
            return {"score": 0, "step_rates": {}, "total_checked": 0}

        step_rates = {step: count / total_checked for step, count in step_counts.items()}
        avg_completion = sum(step_rates.values()) / len(step_rates) if step_rates else 0

        return {
            "score": round(avg_completion, 3),
            "step_rates": {k: round(v, 3) for k, v in step_rates.items()},
            "total_checked": total_checked,
        }

    def _evaluate_lesson_citation(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        """评估决策理由中引用复盘经验的比例"""
        citation_keywords = ["复盘", "经验", "规则", "历史教训", "之前", "上次"]
        cited_count = 0
        total_with_reason = 0

        for record in records:
            reason = record.get("reason", "") or ""
            ai_response = (record.get("action_details", {}) or {}).get("output", "") or ""
            combined = f"{reason} {ai_response}"

            if not combined.strip():
                continue

            total_with_reason += 1
            if any(kw in combined for kw in citation_keywords):
                cited_count += 1

        rate = cited_count / total_with_reason if total_with_reason > 0 else 0

        return {
            "score": round(rate, 3),
            "cited_count": cited_count,
            "total_with_reason": total_with_reason,
        }

    def _evaluate_consistency(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        """评估相似环境下决策一致性"""
        if len(records) < 3:
            return {"score": 0, "inconsistent_pairs": 0, "total_pairs": 0}

        # 按 RSI 区间分组，检查同一区间内决策是否一致
        rsi_groups: dict[str, list[str]] = {}
        for record in records:
            market_data = record.get("market_data", {}) or {}
            rsi = float(market_data.get("rsi", 50) or 50)
            decision = record.get("decision", "UNKNOWN")

            if decision in ("DO_NOTHING", "HOLD"):
                continue

            # RSI 分为 5 个区间
            if rsi < 30:
                group = "oversold"
            elif rsi < 45:
                group = "low"
            elif rsi < 55:
                group = "neutral"
            elif rsi < 70:
                group = "high"
            else:
                group = "overbought"

            rsi_groups.setdefault(group, []).append(decision)

        # 计算每个组内的一致性
        total_groups = 0
        consistent_groups = 0
        for decisions in rsi_groups.values():
            if len(decisions) < 2:
                continue
            total_groups += 1
            # 一致性 = 最多的决策类型占比
            most_common = max(set(decisions), key=decisions.count)
            consistency = decisions.count(most_common) / len(decisions)
            if consistency >= 0.6:
                consistent_groups += 1

        score = consistent_groups / total_groups if total_groups > 0 else 0

        return {
            "score": round(score, 3),
            "consistent_groups": consistent_groups,
            "total_groups": total_groups,
        }

    def _evaluate_calibration(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        """评估置信度校准：高置信度决策是否真的更准确"""
        high_conf_wins = 0
        high_conf_total = 0
        low_conf_wins = 0
        low_conf_total = 0

        for record in records:
            action_details = record.get("action_details", {}) or {}
            confidence = float(action_details.get("confidence", 0.5) or 0.5)
            pnl = float(action_details.get("pnl", 0) or 0)

            decision = record.get("decision", "")
            if decision in ("DO_NOTHING", "HOLD"):
                continue

            if confidence >= 0.7:
                high_conf_total += 1
                if pnl > 0:
                    high_conf_wins += 1
            elif confidence < 0.5:
                low_conf_total += 1
                if pnl > 0:
                    low_conf_wins += 1

        high_win_rate = high_conf_wins / high_conf_total if high_conf_total > 0 else 0
        low_win_rate = low_conf_wins / low_conf_total if low_conf_total > 0 else 0

        # 校准度 = 高置信胜率 - 低置信胜率（应该为正）
        calibration = (
            high_win_rate - low_win_rate if high_conf_total > 0 and low_conf_total > 0 else 0
        )
        # 归一化到 [0, 1]
        score = max(0, min(1, 0.5 + calibration))

        return {
            "score": round(score, 3),
            "high_confidence_win_rate": round(high_win_rate, 3),
            "low_confidence_win_rate": round(low_win_rate, 3),
            "high_confidence_total": high_conf_total,
            "low_confidence_total": low_conf_total,
        }

    def generate_optimization_suggestions(self, report: dict[str, Any]) -> list[dict[str, Any]]:
        """
        调用 LLM 生成 Prompt 微调建议

        Args:
            report: evaluate_prompt_effectiveness 的输出

        Returns:
            建议列表，每条: {target_step, problem, suggestion}
        """
        try:
            prompt = self.prompt_manager.format_prompt_meta_review(
                effectiveness_report=report,
                historical_trend=self.get_historical_scores(),
            )

            system_prompt = (
                "你是一名 Prompt 工程优化专家。根据 Prompt 效果评估报告，"
                "生成具体的 Prompt 微调建议。\n"
                "输出 JSON 格式：\n"
                '{"suggestions": [{"target_step": "步骤名", "problem": "问题描述", '
                '"suggestion": "优化建议"}]}'
            )

            from pydantic_ai import Agent

            agent = Agent(self.llm, system_prompt=system_prompt)
            res = agent.run_sync(prompt)
            raw_text = res.output if isinstance(res.output, str) else str(res.output)

            try:
                data = json.loads(raw_text)
                return data.get("suggestions", [])
            except json.JSONDecodeError:
                return [
                    {
                        "target_step": "解析",
                        "problem": "LLM 输出格式异常",
                        "suggestion": raw_text[:200],
                    }
                ]

        except Exception as e:
            if self._logger:
                self._logger.print_warning(f"Prompt 优化建议生成失败: {e}")
            return []

    def save_report(self, report: dict[str, Any], suggestions: list[dict[str, Any]]):
        """保存评估报告和优化建议"""
        week_str = datetime.now().strftime("%Y-W%W")
        file_path = self.output_dir / f"{week_str}_report.json"
        data = {
            "report": report,
            "suggestions": suggestions,
            "saved_at": datetime.now().isoformat(),
        }
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        if self._logger:
            self._logger.print_info(f"Prompt 优化报告已保存: {file_path}")

    def get_historical_scores(self, weeks: int = 8) -> list[dict[str, Any]]:
        """读取历史评分用于趋势追踪"""
        scores = []
        if not self.output_dir.exists():
            return scores

        files = sorted(self.output_dir.glob("*_report.json"), reverse=True)[:weeks]
        for f in files:
            try:
                with open(f, encoding="utf-8") as fp:
                    data = json.load(fp)
                    report = data.get("report", {})
                    scores.append(
                        {
                            "week": f.stem.replace("_report", ""),
                            "overall_score": report.get("overall_score", 0),
                            "fincot": report.get("fincot_completion", {}).get("score", 0),
                            "citation": report.get("lesson_citation_rate", {}).get("score", 0),
                        }
                    )
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"读取历史评分文件失败: {e}")
                continue

        return list(reversed(scores))
