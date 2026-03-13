"""
Prompt 自优化测试（改进5）
"""

from unittest.mock import MagicMock

import pytest

from src.agent.prompt_meta_reflection import PromptMetaReflector


class TestPromptMetaReflector:
    """Prompt 元反思器测试"""

    @pytest.fixture
    def mock_llm_manager(self):
        manager = MagicMock()
        llm = MagicMock()
        llm.invoke.return_value = MagicMock(
            content='{"suggestions": [{"target_step": "趋势确认", "problem": "缺少多周期确认", "suggestion": "增加4H趋势验证"}]}'
        )
        manager.get_client.return_value = llm
        return manager

    @pytest.fixture
    def mock_prompt_manager(self):
        pm = MagicMock()
        pm.format_prompt_meta_review.return_value = "测试 Prompt"
        return pm

    @pytest.fixture
    def reflector(self, mock_llm_manager, mock_prompt_manager, tmp_path):
        return PromptMetaReflector(
            llm_manager=mock_llm_manager,
            prompt_manager=mock_prompt_manager,
            memory_store=MagicMock(),
            output_dir=str(tmp_path / "prompt_opt"),
        )

    def test_evaluate_fincot_completion(self, reflector):
        """测试 FinCoT 完成度评估"""
        records = [
            {
                "reason": "趋势确认：多头。入场信号：MACD金叉。情绪校验：中性。"
                "复盘比对：无匹配。风险计算：盈亏比2.0。最终决策：BUY",
                "action_details": {"output": ""},
            },
            {
                "reason": "趋势确认：空头。入场信号：RSI超卖。最终决策：HOLD",
                "action_details": {"output": ""},
            },
        ]
        result = reflector._evaluate_fincot_completion(records)

        assert result["total_checked"] == 2
        assert result["score"] > 0
        assert "趋势确认" in result["step_rates"]
        assert result["step_rates"]["趋势确认"] == 1.0  # 两条都提到了

    def test_evaluate_lesson_citation(self, reflector):
        """测试经验引用率评估"""
        records = [
            {"reason": "根据复盘经验，RSI > 70 时应观望", "action_details": {}},
            {"reason": "MACD 金叉信号明确", "action_details": {}},
            {"reason": "历史教训表明不宜追高", "action_details": {}},
        ]
        result = reflector._evaluate_lesson_citation(records)

        assert result["total_with_reason"] == 3
        assert result["cited_count"] == 2  # "复盘" 和 "历史教训"
        assert abs(result["score"] - 2 / 3) < 0.01

    def test_evaluate_consistency(self, reflector):
        """测试决策一致性评估"""
        records = [
            {"decision": "BUY", "market_data": {"rsi": 25}},
            {"decision": "BUY", "market_data": {"rsi": 28}},
            {"decision": "BUY", "market_data": {"rsi": 22}},
            {"decision": "SELL_SHORT", "market_data": {"rsi": 75}},
            {"decision": "SELL_SHORT", "market_data": {"rsi": 78}},
        ]
        result = reflector._evaluate_consistency(records)

        assert result["score"] > 0
        assert result["total_groups"] >= 2

    def test_evaluate_calibration(self, reflector):
        """测试置信度校准评估"""
        records = [
            {"decision": "BUY", "action_details": {"confidence": 0.9, "pnl": 10}},
            {"decision": "BUY", "action_details": {"confidence": 0.8, "pnl": 5}},
            {"decision": "BUY", "action_details": {"confidence": 0.3, "pnl": -5}},
            {"decision": "SELL", "action_details": {"confidence": 0.4, "pnl": -3}},
        ]
        result = reflector._evaluate_calibration(records)

        # 高置信度全胜，低置信度全败，校准应该好
        assert result["high_confidence_win_rate"] == 1.0
        assert result["low_confidence_win_rate"] == 0.0
        assert result["score"] == 1.0

    def test_evaluate_prompt_effectiveness(self, reflector):
        """测试综合效果评估"""
        records = [
            {
                "reason": "趋势确认：多头。最终决策：BUY",
                "decision": "BUY",
                "market_data": {"rsi": 55},
                "action_details": {"confidence": 0.7, "pnl": 5},
            },
        ]
        report = reflector.evaluate_prompt_effectiveness(records, [])

        assert "overall_score" in report
        assert "fincot_completion" in report
        assert "lesson_citation_rate" in report
        assert "decision_consistency" in report
        assert "confidence_calibration" in report

    def test_generate_optimization_suggestions(self, reflector):
        """测试优化建议生成"""
        report = {
            "overall_score": 0.6,
            "fincot_completion": {"score": 0.5, "step_rates": {}},
            "lesson_citation_rate": {"score": 0.3},
            "decision_consistency": {"score": 0.8},
            "confidence_calibration": {"score": 0.6},
        }
        suggestions = reflector.generate_optimization_suggestions(report)

        assert len(suggestions) >= 1
        assert "target_step" in suggestions[0]

    def test_save_report(self, reflector, tmp_path):
        """测试报告保存"""
        report = {"overall_score": 0.7}
        suggestions = [{"target_step": "test", "problem": "p", "suggestion": "s"}]

        reflector.save_report(report, suggestions)

        output_dir = tmp_path / "prompt_opt"
        files = list(output_dir.glob("*_report.json"))
        assert len(files) == 1

    def test_get_historical_scores_empty(self, reflector):
        """测试无历史数据时返回空"""
        scores = reflector.get_historical_scores()
        assert scores == []

    def test_evaluate_empty_records(self, reflector):
        """测试空记录的评估"""
        report = reflector.evaluate_prompt_effectiveness([], [])
        # 空记录时各维度默认为 0，但 calibration 默认为 0.5（中性）
        assert report["overall_score"] >= 0
        assert report["fincot_completion"]["score"] == 0
        assert report["lesson_citation_rate"]["score"] == 0
