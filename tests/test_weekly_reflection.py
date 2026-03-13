"""
每周反思模块测试（改进1b）
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.agent.weekly_reflection import WeeklyReflector


class MockDecisionHistory:
    """模拟决策历史"""

    def __init__(self, records=None):
        self._records = records or []

    def get_recent_decisions(self, symbol, limit=100):
        return [r for r in self._records if r.get("symbol") == symbol][:limit]


class TestWeeklyReflector:
    """每周反思器测试"""

    @pytest.fixture
    def mock_llm_manager(self):
        manager = MagicMock()
        llm = MagicMock()
        llm.invoke.return_value = MagicMock(
            content='{"summary": "本周表现稳定", "key_findings": [], "strategy_adjustments": [], "risk_warnings": []}'
        )
        manager.get_client.return_value = llm
        return manager

    @pytest.fixture
    def mock_prompt_manager(self):
        pm = MagicMock()
        pm.format_weekly_review_prompt.return_value = "测试 Prompt"
        pm.get_weekly_review_system_prompt.return_value = "你是分析师"
        return pm

    @pytest.fixture
    def reflector(self, mock_llm_manager, mock_prompt_manager, tmp_path):
        return WeeklyReflector(
            llm_manager=mock_llm_manager,
            prompt_manager=mock_prompt_manager,
            memory_store=MagicMock(),
            output_dir=str(tmp_path / "weekly"),
            weekly_day=datetime.now().weekday(),  # 设为今天方便测试
            weekly_hour=0,
        )

    def test_should_run_never_ran(self, reflector):
        """测试从未运行时应该运行"""
        assert reflector.should_run(None) is True

    def test_should_run_recently_ran(self, reflector):
        """测试最近运行过时不应该运行"""
        assert reflector.should_run(datetime.now() - timedelta(days=1)) is False

    def test_should_run_week_ago(self, reflector):
        """测试一周前运行过时应该运行"""
        assert reflector.should_run(datetime.now() - timedelta(days=7)) is True

    def test_should_run_wrong_day(self):
        """测试非指定日不应该运行"""
        reflector = WeeklyReflector(
            llm_manager=MagicMock(),
            prompt_manager=MagicMock(),
            memory_store=MagicMock(),
            weekly_day=(datetime.now().weekday() + 3) % 7,  # 不同日
        )
        assert reflector.should_run(None) is False

    def test_run_weekly_reflection_empty(self, reflector):
        """测试无记录时跳过"""
        history = MockDecisionHistory([])
        result = reflector.run_weekly_reflection(["BTC"], history)
        assert result["status"] == "skipped"

    def test_run_weekly_reflection_with_data(self, reflector):
        """测试有记录时正常执行"""
        now = datetime.now()
        records = [
            {
                "symbol": "BTC",
                "decision": "BUY",
                "timestamp": now.isoformat(),
                "market_data": {"rsi": 55},
                "action_details": {"pnl": 10.0},
            },
            {
                "symbol": "BTC",
                "decision": "SELL",
                "timestamp": now.isoformat(),
                "market_data": {"rsi": 75},
                "action_details": {"pnl": -5.0},
            },
        ]
        history = MockDecisionHistory(records)
        result = reflector.run_weekly_reflection(["BTC"], history)

        assert result["status"] == "completed"
        assert "weekly_stats" in result
        assert "systematic_biases" in result
        assert "recurring_errors" in result

    def test_detect_systematic_biases(self, reflector):
        """测试系统性偏差检测"""
        records = [
            {"decision": "BUY", "action_details": {"pnl": 0}},
            {"decision": "BUY", "action_details": {"pnl": 0}},
            {"decision": "BUY", "action_details": {"pnl": 0}},
            {"decision": "BUY", "action_details": {"pnl": 0}},
            {"decision": "BUY", "action_details": {"pnl": 0}},
        ]
        biases = reflector._detect_systematic_biases(records)

        # 应该检测到连续同方向偏差和分布偏差
        assert len(biases) >= 1
        types = [b["type"] for b in biases]
        assert "directional_bias" in types or "decision_distribution_bias" in types

    def test_detect_recurring_errors(self, reflector):
        """测试反复错误检测"""
        records = [
            {"decision": "BUY", "market_data": {"rsi": 80}, "action_details": {"pnl": -10}},
            {"decision": "BUY", "market_data": {"rsi": 75}, "action_details": {"pnl": -8}},
            {"decision": "BUY", "market_data": {"rsi": 78}, "action_details": {"pnl": -12}},
        ]
        errors = reflector._detect_recurring_errors(records)

        # 应该检测到 RSI>70 时 BUY 的反复错误
        assert len(errors) >= 1

    def test_save_report(self, reflector, tmp_path):
        """测试报告保存"""
        report = {
            "week": "2026-W11",
            "status": "completed",
            "timestamp": datetime.now().isoformat(),
        }
        reflector._save_report(report)

        import os
        output_dir = tmp_path / "weekly"
        files = list(output_dir.glob("*.json"))
        assert len(files) == 1
