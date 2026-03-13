"""
多空辩论模块测试
测试 Bull/Bear debate 的上下文构建、辩论执行和结果格式化
"""

from unittest.mock import MagicMock

import pytest

from src.agent.debate import (
    BEAR_SYSTEM_PROMPT,
    BULL_SYSTEM_PROMPT,
    build_debate_context,
    run_bull_bear_debate,
)


class TestDebatePrompts:
    """辩论 Prompt 测试"""

    def test_bull_prompt_contains_key_instructions(self):
        """测试多头 Prompt 包含关键指令"""
        assert "多头" in BULL_SYSTEM_PROMPT
        assert "看多" in BULL_SYSTEM_PROMPT
        assert "论点" in BULL_SYSTEM_PROMPT
        assert "置信度" in BULL_SYSTEM_PROMPT

    def test_bear_prompt_contains_key_instructions(self):
        """测试空头 Prompt 包含关键指令"""
        assert "空头" in BEAR_SYSTEM_PROMPT
        assert "看空" in BEAR_SYSTEM_PROMPT or "观望" in BEAR_SYSTEM_PROMPT
        assert "论点" in BEAR_SYSTEM_PROMPT
        assert "置信度" in BEAR_SYSTEM_PROMPT

    def test_prompts_have_structured_output_format(self):
        """测试 Prompt 要求结构化输出"""
        for prompt in [BULL_SYSTEM_PROMPT, BEAR_SYSTEM_PROMPT]:
            assert "论点1" in prompt
            assert "论点2" in prompt
            assert "论点3" in prompt


class TestBuildDebateContext:
    """辩论上下文构建测试"""

    @pytest.fixture
    def basic_market_data(self):
        """基础市场数据"""
        return {
            "current_price": 50000.0,
            "rsi": 55.0,
            "macd": 0.0012,
            "macd_signal": 0.001,
        }

    @pytest.fixture
    def enriched_data(self):
        """增强数据"""
        return {
            "funding_rate_signal": "资金费率中性",
            "fear_greed_sentiment": "恐惧(35)",
            "current_ema20": 49800.0,
            "ema_20_4h": 49500.0,
            "ema_50_4h": 49000.0,
            "composite_signal": "MACD看多, 价格在EMA上方",
            "h4_trend_analysis": "多头排列",
            "volume_analysis": "正常(1.1倍)",
        }

    def test_basic_context_output(self, basic_market_data):
        """测试基本上下文生成"""
        context = build_debate_context("BTC", basic_market_data)

        assert "BTC" in context
        assert "50000" in context
        assert "RSI=" in context
        assert "MACD=" in context

    def test_context_with_enriched_data(self, basic_market_data, enriched_data):
        """测试包含增强数据的上下文"""
        context = build_debate_context("BTC", basic_market_data, enriched_data=enriched_data)

        assert "资金费率" in context
        assert "恐惧" in context
        assert "综合信号" in context

    def test_context_with_multi_timeframe(self, basic_market_data):
        """测试包含多周期趋势的上下文"""
        trends = {"15m": "上涨", "1h": "震荡", "4h": "上涨"}
        context = build_debate_context("ETH", basic_market_data, multi_timeframe_trends=trends)

        assert "ETH" in context
        assert "15m:上涨" in context
        assert "1h:震荡" in context

    def test_context_without_optional_data(self, basic_market_data):
        """测试不含可选数据时的上下文"""
        context = build_debate_context("SOL", basic_market_data)

        assert "SOL" in context
        # 没有 enriched_data 时使用默认值
        assert "价格=" in context or "$" in context

    def test_context_handles_zero_ema(self, basic_market_data):
        """测试 EMA 为零时不会除零错误"""
        enriched = {"current_ema20": 0}
        context = build_debate_context("BTC", basic_market_data, enriched_data=enriched)

        # 不应该抛异常，偏离率应该为 0
        assert "0.00%" in context

    def test_context_handles_missing_fields(self):
        """测试市场数据缺少字段时的兜底"""
        context = build_debate_context("BTC", {})

        assert "BTC" in context
        assert "RSI=" in context  # 应使用默认值


class TestRunBullBearDebate:
    """辩论执行测试"""

    @pytest.fixture
    def mock_llm(self):
        """模拟 LLM 客户端"""
        llm = MagicMock()

        # 模拟多头响应
        bull_response = MagicMock()
        bull_response.content = (
            "论点1: RSI=55 处于中性偏强区间\n"
            "论点2: 价格站上 EMA20\n"
            "论点3: MACD 多头发散\n"
            "多头置信度: 0.7"
        )

        # 模拟空头响应
        bear_response = MagicMock()
        bear_response.content = (
            "论点1: 上方存在强阻力\n论点2: 成交量萎缩\n论点3: 恐惧贪婪指数偏低\n空头置信度: 0.4"
        )

        llm.invoke.side_effect = [bull_response, bear_response]
        return llm

    @pytest.fixture
    def basic_market_data(self):
        return {"current_price": 50000.0, "rsi": 55.0, "macd": 0.0012, "macd_signal": 0.001}

    def test_debate_returns_formatted_summary(self, mock_llm, basic_market_data):
        """测试辩论返回格式化的摘要"""
        result = run_bull_bear_debate(mock_llm, "BTC", basic_market_data)

        assert "多空辩论" in result
        assert "多头研究员" in result
        assert "空头研究员" in result
        assert "RSI=55" in result  # 多头论点
        assert "成交量萎缩" in result  # 空头论点

    def test_debate_calls_llm_twice(self, mock_llm, basic_market_data):
        """测试辩论执行两次 LLM 调用"""
        run_bull_bear_debate(mock_llm, "BTC", basic_market_data)

        assert mock_llm.invoke.call_count == 2

    def test_debate_passes_correct_system_prompts(self, mock_llm, basic_market_data):
        """测试辩论传递正确的系统提示"""
        run_bull_bear_debate(mock_llm, "BTC", basic_market_data)

        calls = mock_llm.invoke.call_args_list

        # 第一次调用应使用多头 Prompt
        first_call_messages = calls[0][0][0]
        assert first_call_messages[0].content == BULL_SYSTEM_PROMPT

        # 第二次调用应使用空头 Prompt
        second_call_messages = calls[1][0][0]
        assert second_call_messages[0].content == BEAR_SYSTEM_PROMPT

    def test_debate_contains_synthesis_instruction(self, mock_llm, basic_market_data):
        """测试辩论结果包含综合指引"""
        result = run_bull_bear_debate(mock_llm, "BTC", basic_market_data)

        assert "综合双方论点" in result

    def test_debate_handles_llm_failure(self, basic_market_data):
        """测试 LLM 调用失败时返回空字符串"""
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = Exception("API 超时")

        result = run_bull_bear_debate(mock_llm, "BTC", basic_market_data)

        assert result == ""

    def test_debate_handles_empty_content(self, basic_market_data):
        """测试 LLM 返回空内容时的兜底"""
        mock_llm = MagicMock()

        bull_response = MagicMock()
        bull_response.content = ""  # 空内容
        bear_response = MagicMock()
        bear_response.content = None  # None

        mock_llm.invoke.side_effect = [bull_response, bear_response]

        result = run_bull_bear_debate(mock_llm, "BTC", basic_market_data)

        assert "多头分析失败" in result
        assert "空头分析失败" in result

    def test_debate_with_enriched_and_trends(self, mock_llm, basic_market_data):
        """测试传递增强数据和多周期趋势"""
        enriched = {"funding_rate_signal": "看多", "fear_greed_sentiment": "贪婪(70)"}
        trends = {"15m": "上涨", "4h": "震荡"}

        result = run_bull_bear_debate(
            mock_llm,
            "ETH",
            basic_market_data,
            enriched_data=enriched,
            multi_timeframe_trends=trends,
        )

        assert "多空辩论" in result
        # 确认 LLM 收到了包含 ETH 的上下文
        call_args = mock_llm.invoke.call_args_list[0][0][0]
        context_msg = call_args[1].content
        assert "ETH" in context_msg
