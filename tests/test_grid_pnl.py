"""
PnL 追踪器测试
"""

import unittest
from decimal import Decimal

from src.trading.grid_pnl import GridPnLTracker
from src.utils.grid_math import GridLevel, GridLevelState


class TestGridPnLTracker(unittest.TestCase):
    def _make_level(
        self,
        level_id: str = "L0",
        side: str = "LONG",
        price: str = "2000",
        amount: str = "25",
        state: GridLevelState = GridLevelState.COMPLETED,
        open_fill_price: str = "2000",
        open_fill_amount: str = "0.0125",
        close_fill_price: str = "2010",
    ) -> GridLevel:
        level = GridLevel(
            id=level_id,
            price=Decimal(price),
            amount=Decimal(amount),
            side=side,
            state=state,
        )
        level.open_fill_price = Decimal(open_fill_price)
        level.open_fill_amount = Decimal(open_fill_amount)
        level.close_fill_price = Decimal(close_fill_price)
        return level

    def test_record_round_trip_long(self):
        """做多轮回的 PnL 计算"""
        tracker = GridPnLTracker(maker_fee_rate=Decimal("0.00035"))
        level = self._make_level(
            side="LONG",
            open_fill_price="2000",
            open_fill_amount="0.01",
            close_fill_price="2010",
        )

        pnl = tracker.record_round_trip(level)

        # 毛利 = (2010 - 2000) * 0.01 = 0.10
        # 开仓手续费 = 2000 * 0.01 * 0.00035 = 0.007
        # 平仓手续费 = 2010 * 0.01 * 0.00035 = 0.007035
        # 净利 = 0.10 - 0.007 - 0.007035 = 0.085965
        self.assertAlmostEqual(float(pnl), 0.085965, places=6)
        self.assertEqual(tracker.completed_round_trips, 1)
        self.assertEqual(level.round_trip_count, 1)

    def test_record_round_trip_short(self):
        """做空轮回的 PnL 计算"""
        tracker = GridPnLTracker(maker_fee_rate=Decimal("0.00035"))
        level = self._make_level(
            side="SHORT",
            open_fill_price="2010",
            open_fill_amount="0.01",
            close_fill_price="2000",
        )

        pnl = tracker.record_round_trip(level)

        # 毛利 = (2010 - 2000) * 0.01 = 0.10
        # 手续费 = (2010 * 0.01 + 2000 * 0.01) * 0.00035 = 0.014035
        # 净利 = 0.10 - 0.014035 = 0.085965
        self.assertAlmostEqual(float(pnl), 0.085965, places=6)

    def test_record_round_trip_loss(self):
        """亏损轮回"""
        tracker = GridPnLTracker(maker_fee_rate=Decimal("0.00035"))
        level = self._make_level(
            side="LONG",
            open_fill_price="2000",
            open_fill_amount="0.01",
            close_fill_price="1980",  # 亏损 20 点
        )

        pnl = tracker.record_round_trip(level)

        # 毛亏 = (1980 - 2000) * 0.01 = -0.20
        self.assertTrue(pnl < 0)
        self.assertEqual(tracker.completed_round_trips, 1)

    def test_multiple_round_trips_accumulate(self):
        """多轮累计"""
        tracker = GridPnLTracker(maker_fee_rate=Decimal("0.00035"))

        for i in range(3):
            level = self._make_level(
                level_id=f"L{i}",
                open_fill_price="2000",
                open_fill_amount="0.01",
                close_fill_price="2010",
            )
            tracker.record_round_trip(level)

        self.assertEqual(tracker.completed_round_trips, 3)
        self.assertTrue(tracker.realized_pnl > 0)

    def test_unrealized_pnl_long(self):
        """持仓中的未实现盈亏 - 做多"""
        tracker = GridPnLTracker(maker_fee_rate=Decimal("0.00035"))
        level = self._make_level(
            state=GridLevelState.OPEN_FILLED,
            open_fill_price="2000",
            open_fill_amount="0.01",
            close_fill_price="0",  # 尚未平仓
        )

        # 当前价 2020 -> 浮盈
        unrealized = tracker.calculate_unrealized_pnl([level], Decimal("2020"))
        # (2020 - 2000) * 0.01 - 2020 * 0.01 * 0.00035
        expected = Decimal("0.20") - Decimal("2020") * Decimal("0.01") * Decimal("0.00035")
        self.assertAlmostEqual(float(unrealized), float(expected), places=6)

    def test_unrealized_pnl_short(self):
        """持仓中的未实现盈亏 - 做空"""
        tracker = GridPnLTracker(maker_fee_rate=Decimal("0.00035"))
        level = self._make_level(
            side="SHORT",
            state=GridLevelState.CLOSE_PENDING,
            open_fill_price="2000",
            open_fill_amount="0.01",
            close_fill_price="0",
        )

        # 当前价 1980 -> 浮盈（空头）
        unrealized = tracker.calculate_unrealized_pnl([level], Decimal("1980"))
        # (2000 - 1980) * 0.01 - 1980 * 0.01 * 0.00035
        self.assertTrue(unrealized > 0)

    def test_net_pnl_pct(self):
        """PnL 百分比计算"""
        tracker = GridPnLTracker(maker_fee_rate=Decimal("0.00035"))
        level = self._make_level(
            open_fill_price="2000",
            open_fill_amount="0.01",
            close_fill_price="2010",
        )
        tracker.record_round_trip(level)

        pct = tracker.get_net_pnl_pct([], Decimal("2010"), Decimal("100"))
        # realized_pnl / 100
        self.assertTrue(pct > 0)

    def test_net_pnl_pct_zero_investment(self):
        """投入为 0 时返回 0"""
        tracker = GridPnLTracker()
        pct = tracker.get_net_pnl_pct([], Decimal("2000"), Decimal("0"))
        self.assertEqual(pct, Decimal("0"))

    def test_get_summary(self):
        """完整报告"""
        tracker = GridPnLTracker(maker_fee_rate=Decimal("0.00035"))
        level = self._make_level(
            open_fill_price="2000",
            open_fill_amount="0.01",
            close_fill_price="2010",
        )
        tracker.record_round_trip(level)

        summary = tracker.get_summary([], Decimal("2010"), Decimal("100"))
        self.assertIn("realized_pnl", summary)
        self.assertIn("unrealized_pnl", summary)
        self.assertIn("net_pnl", summary)
        self.assertIn("completed_round_trips", summary)
        self.assertEqual(summary["completed_round_trips"], 1)

    def test_serialization_roundtrip(self):
        """序列化和反序列化"""
        tracker = GridPnLTracker(maker_fee_rate=Decimal("0.00035"))
        level = self._make_level(
            open_fill_price="2000",
            open_fill_amount="0.01",
            close_fill_price="2010",
        )
        tracker.record_round_trip(level)

        data = tracker.to_dict()
        restored = GridPnLTracker.from_dict(data)

        self.assertEqual(restored.realized_pnl, tracker.realized_pnl)
        self.assertEqual(restored.realized_fees, tracker.realized_fees)
        self.assertEqual(restored.completed_round_trips, tracker.completed_round_trips)
        self.assertEqual(restored.maker_fee_rate, tracker.maker_fee_rate)

    def test_record_with_missing_data(self):
        """缺少成交数据时返回 0"""
        tracker = GridPnLTracker()
        level = GridLevel(
            id="L0",
            price=Decimal("2000"),
            amount=Decimal("25"),
            side="LONG",
        )
        # open_fill_price 为 None
        pnl = tracker.record_round_trip(level)
        self.assertEqual(pnl, Decimal("0"))
        self.assertEqual(tracker.completed_round_trips, 0)


if __name__ == "__main__":
    unittest.main()
