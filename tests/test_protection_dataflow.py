"""
风控插件数据流端到端测试

验证 P1 修复：Agent 工具回调写回 _current_trade_event → make_decision 合入 details
→ main.py 根据 details.size > 0 调用 protection_manager.on_trade_open/close
拿到真实 entry_price/size/pnl/leverage，而非默认 0。
"""

import pytest

from src.plugins.protections import (
    ProtectionContext,
    ProtectionManager,
)


@pytest.fixture
def manager(tmp_path):
    """构建包含 consecutive_loss + position_timeout 的真实 manager"""
    return ProtectionManager(
        protections_config=[
            {
                "name": "consecutive_loss",
                "max_consecutive_losses": 5,
                "per_symbol": False,
                "pause_hours": 1.0,
            },
            {"name": "position_timeout", "max_position_hours": 48.0},
        ],
        data_dir=tmp_path,
    )


def _simulate_main_dispatch(manager: ProtectionManager, decision: str, details: dict):
    """模拟 main.py:1045-1066 的派发逻辑（保持与生产代码一致）"""
    if not (manager and details.get("size", 0) > 0):
        return
    if decision in ("BUY", "SELL_SHORT"):
        manager.on_trade_open(
            symbol=details.get("symbol", "BTC"),
            entry_price=float(details.get("entry_price", 0)),
            size=float(details["size"]),
            is_long=(decision == "BUY"),
            leverage=int(details.get("leverage", 1)),
        )
    elif decision in ("SELL", "BUY_TO_COVER"):
        manager.on_trade_close(
            symbol=details.get("symbol", "BTC"),
            pnl=float(details.get("pnl", 0)),
        )


class TestExecutionFailedSkipsRiskUpdate:
    """details 中没有真实成交字段（执行失败）时，不应误触发风控"""

    def test_failed_buy_does_not_register_position(self, manager):
        """失败的 BUY 决策（details 无 size）→ position_timeout 不应记录"""
        details = {"output": "...", "events": [], "prompt": "...", "symbol": "BTC"}
        _simulate_main_dispatch(manager, "BUY", details)

        for plugin in manager.plugins:
            if plugin.name == "position_timeout":
                assert "BTC" not in plugin._position_records

    def test_failed_sell_does_not_increment_loss(self, manager):
        """失败的 SELL 决策（details 无 size/pnl）→ consecutive_loss 不应递增"""
        details = {"output": "...", "events": [], "prompt": "...", "symbol": "BTC"}
        _simulate_main_dispatch(manager, "SELL", details)

        for plugin in manager.plugins:
            if plugin.name == "consecutive_loss":
                assert plugin._global_losses == 0


class TestRealizedPnlPropagation:
    """details 中带真实成交字段时，pnl 正确传给 ConsecutiveLossProtection"""

    def test_profit_resets_consecutive_losses(self, manager):
        """真实盈利的 SELL 应重置连续亏损计数"""
        # 先制造 3 次亏损
        for _ in range(3):
            _simulate_main_dispatch(
                manager,
                "SELL",
                {"symbol": "BTC", "size": 0.1, "pnl": -50.0},
            )
        for plugin in manager.plugins:
            if plugin.name == "consecutive_loss":
                assert plugin._global_losses == 3

        # 一笔盈利重置
        _simulate_main_dispatch(
            manager,
            "SELL",
            {"symbol": "BTC", "size": 0.1, "pnl": 100.0},
        )
        for plugin in manager.plugins:
            if plugin.name == "consecutive_loss":
                assert plugin._global_losses == 0

    def test_pnl_zero_treated_as_loss(self, manager):
        """pnl=0 仍然被算作"非盈利"递增计数 —— 这是 ConsecutiveLossProtection 既有行为，
        但 P1 修复保证只有真实成交（size>0）才会进入这个分支"""
        # 真实成交但盈亏为 0（罕见，例如手续费抵消的小额平仓）
        _simulate_main_dispatch(
            manager,
            "SELL",
            {"symbol": "BTC", "size": 0.1, "pnl": 0.0},
        )
        for plugin in manager.plugins:
            if plugin.name == "consecutive_loss":
                assert plugin._global_losses == 1


class TestPositionTimeoutTrueSize:
    """position_timeout 应记录真实 size，避免幻影持仓"""

    def test_real_open_records_correct_size(self, manager):
        """开仓 details 含真实 size → 记录里有正确数值"""
        _simulate_main_dispatch(
            manager,
            "BUY",
            {
                "symbol": "BTC",
                "size": 0.5,
                "entry_price": 50000,
                "leverage": 10,
            },
        )
        for plugin in manager.plugins:
            if plugin.name == "position_timeout":
                rec = plugin._position_records.get("BTC")
                assert rec is not None
                assert rec["size"] == 0.5
                assert rec["entry_price"] == 50000
                assert rec["leverage"] == 10
                assert rec["is_long"] is True

    def test_close_removes_record(self, manager):
        """真实平仓的 SELL 应清除 position_timeout 记录"""
        _simulate_main_dispatch(
            manager,
            "BUY",
            {"symbol": "BTC", "size": 0.5, "entry_price": 50000, "leverage": 10},
        )
        _simulate_main_dispatch(
            manager,
            "SELL",
            {"symbol": "BTC", "size": 0.5, "pnl": 100.0},
        )
        for plugin in manager.plugins:
            if plugin.name == "position_timeout":
                assert "BTC" not in plugin._position_records


class TestConsecutiveLossThreshold:
    """验证 P1 修复后，连续亏损保护按真实 pnl 触发，而非 5 次 SELL 决策"""

    def test_5_profitable_sells_do_not_trigger(self, manager):
        """5 次盈利 SELL 决策不应触发连续亏损保护"""
        for _ in range(5):
            _simulate_main_dispatch(
                manager,
                "SELL",
                {"symbol": "BTC", "size": 0.1, "pnl": 50.0},
            )

        ctx = ProtectionContext(
            balance=10000, equity=10500, unrealized_pnl=0, margin_used=0, current_positions=[]
        )
        results = manager.check_all(ctx)
        # consecutive_loss 不应触发
        triggered_names = [r.plugin_name for r in results]
        assert "consecutive_loss" not in triggered_names

    def test_5_losing_sells_do_trigger(self, manager):
        """5 次真实亏损 SELL 应触发连续亏损保护"""
        for _ in range(5):
            _simulate_main_dispatch(
                manager,
                "SELL",
                {"symbol": "BTC", "size": 0.1, "pnl": -30.0},
            )

        ctx = ProtectionContext(
            balance=10000, equity=9850, unrealized_pnl=0, margin_used=0, current_positions=[]
        )
        results = manager.check_all(ctx)
        triggered_names = [r.plugin_name for r in results]
        assert "consecutive_loss" in triggered_names


class TestPluginNamePropagation:
    """验证 ProtectionReturn.plugin_name 在 manager 内被正确填入"""

    def test_plugin_name_set_on_triggered_results(self, manager):
        """触发的 ProtectionReturn 应携带 plugin_name"""
        # 手动制造 position_timeout 的触发条件
        from datetime import datetime, timedelta

        for plugin in manager.plugins:
            if plugin.name == "position_timeout":
                plugin._position_records["BTC"] = {
                    "entry_time": (datetime.now() - timedelta(hours=72)).isoformat(),
                    "entry_price": 50000,
                    "size": 0.5,
                    "is_long": True,
                    "leverage": 10,
                }

        ctx = ProtectionContext(
            balance=10000, equity=10000, unrealized_pnl=0, margin_used=0, current_positions=[]
        )
        results = manager.check_all(ctx)

        timeout_results = [r for r in results if r.plugin_name == "position_timeout"]
        assert len(timeout_results) == 1
        assert "BTC" in (timeout_results[0].affected_symbols or [])
