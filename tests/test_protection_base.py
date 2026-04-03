"""
保护插件基础架构测试
测试 IProtection、ProtectionReturn、ProtectionContext 等核心数据结构。
"""

import pytest

from src.plugins.protections.base import (
    IProtection,
    ProtectionAction,
    ProtectionContext,
    ProtectionReturn,
)
from datetime import datetime


class TestProtectionReturn:
    """ProtectionReturn 数据结构测试"""

    def test_default_values(self):
        """默认值正确"""
        r = ProtectionReturn(triggered=False)
        assert r.triggered is False
        assert r.action == ProtectionAction.NONE
        assert r.reason == ""
        assert r.should_pause is False
        assert r.affected_symbols is None
        assert r.details == {}

    def test_triggered_result(self):
        """触发结果正确"""
        r = ProtectionReturn(
            triggered=True,
            action=ProtectionAction.CLOSE_ALL_POSITIONS,
            reason="回撤超限",
            should_pause=True,
            affected_symbols=["BTC"],
            details={"drawdown": 0.15},
        )
        assert r.triggered is True
        assert r.action == ProtectionAction.CLOSE_ALL_POSITIONS
        assert r.affected_symbols == ["BTC"]


class TestProtectionContext:
    """ProtectionContext 数据结构测试"""

    def test_context_creation(self):
        """上下文创建正确"""
        ctx = ProtectionContext(
            balance=10000,
            equity=9500,
            unrealized_pnl=-500,
            margin_used=2000,
            current_positions=[{"symbol": "BTC", "size": 0.1}],
        )
        assert ctx.balance == 10000
        assert ctx.equity == 9500
        assert isinstance(ctx.timestamp, datetime)


class TestProtectionAction:
    """ProtectionAction 枚举测试"""

    def test_action_values(self):
        """枚举值正确"""
        assert ProtectionAction.NONE == "none"
        assert ProtectionAction.PAUSE_NEW_TRADES == "pause_new_trades"
        assert ProtectionAction.CLOSE_ALL_POSITIONS == "close_all_positions"


class TestIProtection:
    """IProtection 抽象基类测试"""

    def test_cannot_instantiate_abstract(self):
        """抽象基类不可直接实例化"""
        with pytest.raises(TypeError):
            IProtection(config={})
