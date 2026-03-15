"""
LangGraph Agent 迁移测试

测试新的 LangGraph 架构是否正常工作。
"""

import pytest

from src.agents.common.state.base import add_messages
from src.agents.common.tools.trading import (
    BuyInput,
    TradingToolFactory,
    create_mock_callbacks,
)

# 导入通用模块
from src.agents.common.utils.helpers import (
    extract_json_from_text,
    safe_float,
    safe_int,
    safe_leverage,
    shorten_text,
)
from src.agents.common.utils.llm import LLMConfig


class TestHelperFunctions:
    """测试辅助函数"""

    def test_safe_float_with_valid_input(self):
        """测试有效输入的 safe_float"""
        assert safe_float("123.45") == 123.45
        assert safe_float(123.45) == 123.45
        assert safe_float(123) == 123.0

    def test_safe_float_with_invalid_input(self):
        """测试无效输入的 safe_float"""
        assert safe_float(None) == 0.0
        assert safe_float("invalid") == 0.0
        assert safe_float(None, default=-1.0) == -1.0

    def test_safe_leverage_with_dict(self):
        """测试字典输入的 safe_leverage"""
        assert safe_leverage({"type": "cross", "value": 10}) == 10
        assert safe_leverage({"value": 5}) == 5

    def test_safe_leverage_with_number(self):
        """测试数字输入的 safe_leverage"""
        assert safe_leverage(10) == 10
        assert safe_leverage(5.5) == 5

    def test_safe_leverage_with_invalid_input(self):
        """测试无效输入的 safe_leverage"""
        assert safe_leverage(None) == 1
        assert safe_leverage("invalid") == 1

    def test_safe_int(self):
        """测试 safe_int"""
        assert safe_int("123") == 123
        assert safe_int(45.6) == 45
        assert safe_int(None) == 0
        assert safe_int("invalid", default=-1) == -1

    def test_shorten_text(self):
        """测试文本裁剪"""
        assert shorten_text("Hello World", limit=20) == "Hello World"
        assert shorten_text("Hello World", limit=5) == "He..."
        assert shorten_text("") == ""
        assert shorten_text(None) == ""

    def test_extract_json_from_text_markdown(self):
        """测试从 Markdown 代码块提取 JSON"""
        text = '```json\n{"key": "value"}\n```'
        result = extract_json_from_text(text)
        assert result == {"key": "value"}

    def test_extract_json_from_text_plain(self):
        """测试从纯文本提取 JSON"""
        text = 'Some text {"key": "value"} more text'
        result = extract_json_from_text(text)
        assert result == {"key": "value"}

    def test_extract_json_from_text_invalid(self):
        """测试无效 JSON"""
        text = "No JSON here"
        result = extract_json_from_text(text)
        assert result is None


class TestLLMConfig:
    """测试 LLM 配置"""

    def test_config_creation(self):
        """测试配置创建"""
        config = LLMConfig(
            api_base="http://localhost:8000",
            api_key="test-key",
            model="test-model",
            temperature=0.5,
        )
        assert config.api_base == "http://localhost:8000"
        assert config.api_key == "test-key"
        assert config.model == "test-model"
        assert config.temperature == 0.5

    def test_config_to_dict(self):
        """测试配置转字典"""
        config = LLMConfig(
            api_base="http://localhost:8000",
            api_key="test-key",
            model="test-model",
        )
        result = config.to_dict()
        assert result["base_url"] == "http://localhost:8000"
        assert result["api_key"] == "test-key"
        assert result["model"] == "test-model"


class TestTradingToolFactory:
    """测试交易工具工厂"""

    def test_create_mock_callbacks(self):
        """测试创建模拟回调"""
        callbacks = create_mock_callbacks()
        assert len(callbacks) == 8  # 永续基础 + 限价单回调

        (
            buy_cb,
            sell_cb,
            sell_short_cb,
            buy_to_cover_cb,
            nothing_cb,
            buy_limit_cb,
            sell_short_limit_cb,
            cancel_limit_cb,
        ) = callbacks

        # 测试 buy 回调
        result = buy_cb("BTC", 100.0, 5)
        assert "BTC" in result
        assert "$100" in result
        assert "5x" in result

        # 测试 sell 回调
        result = sell_cb("BTC")
        assert "BTC" in result

        # 测试 do_nothing 回调
        result = nothing_cb("市场不明确")
        assert "市场不明确" in result

        # 测试限价单回调
        result = buy_limit_cb("BTC", 100.0, 5, 50000.0)
        assert "BTC" in result
        assert "限价" in result
        assert "$50000" in result

        result = cancel_limit_cb("BTC", 12345)
        assert "BTC" in result
        assert "12345" in result

    def test_tool_factory_creation(self):
        """测试工具工厂创建"""
        callbacks = create_mock_callbacks()
        factory = TradingToolFactory(*callbacks)

        tools = factory.get_all_tools()
        assert len(tools) == 8  # 永续基础工具 + 限价单工具

        # 检查工具名称
        tool_names = [t.name for t in tools]
        assert "buy" in tool_names
        assert "sell" in tool_names
        assert "sell_short" in tool_names
        assert "buy_to_cover" in tool_names
        assert "do_nothing" in tool_names
        assert "buy_limit" in tool_names
        assert "sell_short_limit" in tool_names
        assert "cancel_limit_order" in tool_names

    def test_get_callbacks_dict(self):
        """测试获取回调字典"""
        callbacks = create_mock_callbacks()
        factory = TradingToolFactory(*callbacks)

        callbacks_dict = factory.get_callbacks_dict()
        assert "buy" in callbacks_dict
        assert "sell" in callbacks_dict
        assert "sell_short" in callbacks_dict
        assert "buy_to_cover" in callbacks_dict
        assert "do_nothing" in callbacks_dict
        assert "buy_limit" in callbacks_dict
        assert "sell_short_limit" in callbacks_dict
        assert "cancel_limit_order" in callbacks_dict


class TestBuyInput:
    """测试买入输入模型"""

    def test_valid_input(self):
        """测试有效输入"""
        input_data = BuyInput(symbol="BTC", amount=100.0, leverage=5)
        assert input_data.symbol == "BTC"
        assert input_data.amount == 100.0
        assert input_data.leverage == 5

    def test_leverage_conversion(self):
        """测试杠杆转换"""
        input_data = BuyInput(symbol="BTC", leverage=5.5)
        assert input_data.leverage == 5  # 应该转换为整数

    def test_optional_fields(self):
        """测试可选字段"""
        input_data = BuyInput(symbol="BTC")
        assert input_data.symbol == "BTC"
        assert input_data.amount is None
        assert input_data.leverage is None


class TestMessageReducer:
    """测试消息 reducer"""

    def test_add_messages(self):
        """测试消息累积"""
        from langchain_core.messages import AIMessage, HumanMessage

        left = [HumanMessage(content="Hello")]
        right = [AIMessage(content="Hi")]

        result = add_messages(left, right)
        assert len(result) == 2
        assert result[0].content == "Hello"
        assert result[1].content == "Hi"


class TestTradingAgentState:
    """测试交易 Agent 状态"""

    def test_create_initial_state(self):
        """测试创建初始状态"""
        from src.agents.trading.state import create_initial_state

        state = create_initial_state(
            symbol="BTC",
            market_data={"current_price": 50000.0},
            multi_timeframe_trends={"1h": "上涨"},
            current_positions=[],
            max_positions=5,
            trade_amount=100.0,
            max_leverage=10,
            take_profit_ratio=0.05,
            stop_loss_ratio=0.02,
        )

        assert state["symbol"] == "BTC"
        assert state["current_price"] == 50000.0
        assert state["trade_amount"] == 100.0
        assert state["max_leverage"] == 10
        assert state["current_step"] == "start"
        assert len(state["errors"]) == 0


class TestExecutionAgentState:
    """测试执行 Agent 状态"""

    def test_create_initial_state(self):
        """测试创建初始状态"""
        from src.agents.execution.state import create_initial_state

        state = create_initial_state(
            decision_text="买入开多 BTC",
            symbol="BTC",
        )

        assert state["symbol"] == "BTC"
        assert state["decision_text"] == "买入开多 BTC"
        assert state["current_step"] == "start"
        assert state["success"] is False

    def test_decision_type_enum(self):
        """测试决策类型枚举"""
        from src.agents.execution.state import DecisionType

        assert DecisionType.BUY.value == "BUY"
        assert DecisionType.SELL.value == "SELL"
        assert DecisionType.DO_NOTHING.value == "DO_NOTHING"


class TestReviewAgentState:
    """测试复盘 Agent 状态"""

    def test_create_initial_state(self):
        """测试创建初始状态"""
        from src.agents.review.state import create_initial_state

        decision_records = [{"decision": "BUY", "market_data": {"current_price": 50000}}]

        state = create_initial_state(
            symbol="BTC",
            decision_records=decision_records,
        )

        assert state["symbol"] == "BTC"
        assert len(state["decision_records"]) == 1
        assert state["current_step"] == "start"
        assert len(state["lessons"]) == 0


class TestImports:
    """测试模块导入"""

    def test_import_trading_agent(self):
        """测试导入交易 Agent"""
        from src.agents.trading import get_trading_workflow

        # 测试延迟导入
        TradingAgentWorkflow = get_trading_workflow()
        assert TradingAgentWorkflow is not None

    def test_import_execution_agent(self):
        """测试导入执行 Agent"""
        from src.agents.execution import get_execution_workflow

        # 测试延迟导入
        ExecutionAgentWorkflow = get_execution_workflow()
        assert ExecutionAgentWorkflow is not None

    def test_import_review_agent(self):
        """测试导入复盘 Agent"""
        from src.agents.review import get_review_workflow

        # 测试延迟导入
        ReviewAgentWorkflow = get_review_workflow()
        assert ReviewAgentWorkflow is not None

    def test_import_common_modules(self):
        """测试导入通用模块"""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
