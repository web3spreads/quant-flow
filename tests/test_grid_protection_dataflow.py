"""网格逐轮盈亏 → 账户级连亏熔断 数据通路测试

验证修复：网格 ``_record_round_trip`` 每完成一轮 round-trip，通过
``on_round_trip_close`` 回调把逐轮盈亏喂给 ``ProtectionManager.on_trade_close``，
使 ``consecutive_loss``（连亏熔断）在纯网格模式下真正生效——此前网格从不上报
逐笔盈亏，该插件形同虚设。

注意：网格只接 ``on_trade_close``（盈亏事件），不接 ``on_trade_open``——网格持续
滚动库存、无干净的「单笔开仓」语义，position_timeout 不适用，故不接线。
"""

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

from src.plugins.protections import ProtectionContext, ProtectionManager
from src.trading.grid_manager import GridManager
from src.utils.grid_math import GridLevel, GridLevelState


class _DummyLogger:
    def print_info(self, *a, **k):
        pass

    def print_warning(self, *a, **k):
        pass

    def print_error(self, *a, **k):
        pass

    def print_header(self, *a, **k):
        pass

    def print_section(self, *a, **k):
        pass

    def log_trade(self, *a, **k):
        pass


def _make_level(close_fill_price: str, open_fill_price: str = "2000") -> GridLevel:
    """构造一个已完成开平仓的层级；close > open 为盈利，close < open 为亏损。"""
    level = GridLevel(
        id="L0",
        price=Decimal(open_fill_price),
        amount=Decimal("25"),
        side="LONG",
        state=GridLevelState.COMPLETED,
    )
    level.open_fill_price = Decimal(open_fill_price)
    level.open_fill_amount = Decimal("0.01")
    level.close_fill_price = Decimal(close_fill_price)
    return level


def _make_grid_manager(state_file: str, on_round_trip_close=None) -> GridManager:
    return GridManager(
        order_manager=MagicMock(),
        logger=_DummyLogger(),
        state_file=state_file,
        on_round_trip_close=on_round_trip_close,
    )


class TestRoundTripCallbackWiring(unittest.TestCase):
    """回调本身的触发与盈亏符号"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state_file = str(Path(self._tmp.name) / "grid_state.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_callback_fires_with_loss_pnl(self):
        """亏损轮回应以负 pnl 回调上报"""
        captured = []
        gm = _make_grid_manager(
            self.state_file, on_round_trip_close=lambda s, p: captured.append((s, p))
        )

        gm._record_round_trip("ETH", _make_level(close_fill_price="1980"))  # 亏损

        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0][0], "ETH")
        self.assertLess(captured[0][1], 0)

    def test_callback_fires_with_profit_pnl(self):
        """盈利轮回应以正 pnl 回调上报"""
        captured = []
        gm = _make_grid_manager(
            self.state_file, on_round_trip_close=lambda s, p: captured.append((s, p))
        )

        gm._record_round_trip("ETH", _make_level(close_fill_price="2010"))  # 盈利

        self.assertEqual(len(captured), 1)
        self.assertGreater(captured[0][1], 0)

    def test_no_callback_does_not_raise(self):
        """未配置回调（protection_manager 为空）时不应报错"""
        gm = _make_grid_manager(self.state_file, on_round_trip_close=None)
        gm._record_round_trip("ETH", _make_level(close_fill_price="2010"))  # 不抛异常即通过

    def test_callback_exception_does_not_break_record(self):
        """回调内部异常被吞掉，不得拖垮网格主流程（PnL 仍正常累计）"""

        def _boom(symbol, pnl):
            raise RuntimeError("风控记账炸了")

        gm = _make_grid_manager(self.state_file, on_round_trip_close=_boom)
        level = _make_level(close_fill_price="2010")
        gm._record_round_trip("ETH", level)  # 不应抛出

        # 轮回计数仍正常推进，证明主流程未被回调异常中断
        self.assertEqual(level.round_trip_count, 1)


class TestGridFeedsConsecutiveLoss(unittest.TestCase):
    """端到端：网格逐轮盈亏接入真实 ProtectionManager 的连亏熔断"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state_file = str(Path(self._tmp.name) / "grid_state.json")
        self.manager = ProtectionManager(
            protections_config=[
                {
                    "name": "consecutive_loss",
                    "max_consecutive_losses": 3,
                    "per_symbol": False,
                    "pause_hours": 1.0,
                }
            ],
            data_dir=Path(self._tmp.name) / "protection",
        )
        self.gm = _make_grid_manager(
            self.state_file,
            on_round_trip_close=lambda s, p: self.manager.on_trade_close(symbol=s, pnl=p),
        )

    def tearDown(self):
        self._tmp.cleanup()

    def _ctx(self) -> ProtectionContext:
        return ProtectionContext(
            balance=10000, equity=9900, unrealized_pnl=0, margin_used=0, current_positions=[]
        )

    def test_three_losing_round_trips_trigger_consecutive_loss(self):
        """连续 3 轮亏损 round-trip 应触发连亏熔断"""
        for _ in range(3):
            self.gm._record_round_trip("ETH", _make_level(close_fill_price="1980"))

        results = self.manager.check_all(self._ctx())
        self.assertIn("consecutive_loss", [r.plugin_name for r in results])

    def test_profit_round_trip_resets_counter(self):
        """盈利轮回重置计数：2 亏 + 1 盈 + 2 亏 不应触发（阈值 3）"""
        self.gm._record_round_trip("ETH", _make_level(close_fill_price="1980"))
        self.gm._record_round_trip("ETH", _make_level(close_fill_price="1980"))
        self.gm._record_round_trip("ETH", _make_level(close_fill_price="2010"))  # 盈利重置
        self.gm._record_round_trip("ETH", _make_level(close_fill_price="1980"))
        self.gm._record_round_trip("ETH", _make_level(close_fill_price="1980"))

        results = self.manager.check_all(self._ctx())
        self.assertNotIn("consecutive_loss", [r.plugin_name for r in results])


if __name__ == "__main__":
    unittest.main()
