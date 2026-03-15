"""
Agent 工具和辅助函数测试
"""

import pytest

from src.agent.helpers import (
    extract_json_from_text,
    safe_float,
    safe_int,
    safe_leverage,
    shorten_text,
)
from src.agent.tools import TradingTools


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


class TestTradingTools:
    """测试交易工具"""

    def test_tools_creation(self):
        """测试工具创建"""

        def buy_cb(symbol, amount=None, leverage=None):
            return f"买入 {symbol}"

        def sell_cb(symbol):
            return f"卖出 {symbol}"

        def sell_short_cb(symbol, amount=None, leverage=None):
            return f"卖空 {symbol}"

        def buy_to_cover_cb(symbol):
            return f"平空 {symbol}"

        def do_nothing_cb(reason):
            return f"不操作: {reason}"

        tools = TradingTools(buy_cb, sell_cb, sell_short_cb, buy_to_cover_cb, do_nothing_cb)
        all_tools = tools.get_all_tools()

        # 基本工具：buy, sell, sell_short, buy_to_cover, do_nothing
        assert len(all_tools) == 5

        tool_names = [t.name for t in all_tools]
        assert "buy" in tool_names
        assert "sell" in tool_names
        assert "sell_short" in tool_names
        assert "buy_to_cover" in tool_names
        assert "do_nothing" in tool_names


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
