"""
层级循环复用生命周期测试
"""

import unittest
from decimal import Decimal

from src.utils.grid_math import GridLevel, GridLevelState
from src.utils.precision import quantize_price, quantize_size, to_decimal


class TestGridLevelState(unittest.TestCase):
    def test_all_states(self):
        """所有状态值"""
        self.assertEqual(GridLevelState.IDLE.value, "IDLE")
        self.assertEqual(GridLevelState.OPEN_PENDING.value, "OPEN_PENDING")
        self.assertEqual(GridLevelState.OPEN_FILLED.value, "OPEN_FILLED")
        self.assertEqual(GridLevelState.CLOSE_PENDING.value, "CLOSE_PENDING")
        self.assertEqual(GridLevelState.COMPLETED.value, "COMPLETED")


class TestGridLevel(unittest.TestCase):
    def _make_level(self, **kwargs) -> GridLevel:
        defaults = {
            "id": "L0",
            "price": Decimal("2000"),
            "amount": Decimal("25"),
            "side": "LONG",
        }
        defaults.update(kwargs)
        return GridLevel(**defaults)

    def test_initial_state(self):
        """初始状态为 IDLE"""
        level = self._make_level()
        self.assertEqual(level.state, GridLevelState.IDLE)
        self.assertIsNone(level.open_order_id)
        self.assertEqual(level.round_trip_count, 0)
        self.assertEqual(level.cumulative_pnl, Decimal("0"))

    def test_reset_preserves_stats(self):
        """reset 保留统计数据"""
        level = self._make_level()
        level.state = GridLevelState.COMPLETED
        level.open_order_id = 12345
        level.open_fill_price = Decimal("2000")
        level.open_fill_amount = Decimal("0.01")
        level.close_order_id = 12346
        level.close_fill_price = Decimal("2010")
        level.round_trip_count = 3
        level.cumulative_pnl = Decimal("1.5")

        level.reset()

        self.assertEqual(level.state, GridLevelState.IDLE)
        self.assertIsNone(level.open_order_id)
        self.assertIsNone(level.open_fill_price)
        self.assertIsNone(level.close_order_id)
        # 统计保留
        self.assertEqual(level.round_trip_count, 3)
        self.assertEqual(level.cumulative_pnl, Decimal("1.5"))

    def test_to_dict(self):
        """序列化"""
        level = self._make_level()
        level.state = GridLevelState.CLOSE_PENDING
        level.open_order_id = 12345
        level.open_fill_price = Decimal("2001.5")
        level.open_fill_amount = Decimal("0.0125")
        level.close_order_id = 12350

        d = level.to_dict()

        self.assertEqual(d["id"], "L0")
        self.assertEqual(d["price"], "2000")
        self.assertEqual(d["amount"], "25")
        self.assertEqual(d["side"], "LONG")
        self.assertEqual(d["state"], "CLOSE_PENDING")
        self.assertEqual(d["open_order_id"], 12345)
        self.assertEqual(d["open_fill_price"], "2001.5")
        self.assertEqual(d["close_order_id"], 12350)
        self.assertIsNone(d["close_fill_price"])

    def test_from_dict(self):
        """反序列化"""
        data = {
            "id": "L2",
            "price": "2500.5",
            "amount": "30",
            "side": "SHORT",
            "state": "OPEN_FILLED",
            "open_order_id": 99999,
            "open_fill_price": "2500.3",
            "open_fill_amount": "0.012",
            "open_fill_time": 1711234567.89,
            "close_order_id": None,
            "close_fill_price": None,
            "close_fill_amount": None,
            "close_fill_time": None,
            "round_trip_count": 5,
            "cumulative_pnl": "3.45",
        }

        level = GridLevel.from_dict(data)

        self.assertEqual(level.id, "L2")
        self.assertEqual(level.price, Decimal("2500.5"))
        self.assertEqual(level.amount, Decimal("30"))
        self.assertEqual(level.side, "SHORT")
        self.assertEqual(level.state, GridLevelState.OPEN_FILLED)
        self.assertEqual(level.open_order_id, 99999)
        self.assertEqual(level.open_fill_price, Decimal("2500.3"))
        self.assertIsNone(level.close_order_id)
        self.assertIsNone(level.close_fill_price)
        self.assertEqual(level.round_trip_count, 5)
        self.assertEqual(level.cumulative_pnl, Decimal("3.45"))

    def test_serialization_roundtrip(self):
        """序列化 -> 反序列化一致性"""
        level = self._make_level(id="L3", side="SHORT")
        level.state = GridLevelState.COMPLETED
        level.open_order_id = 111
        level.open_fill_price = Decimal("2100")
        level.open_fill_amount = Decimal("0.015")
        level.open_fill_time = 1700000000.0
        level.close_order_id = 222
        level.close_fill_price = Decimal("2090")
        level.close_fill_amount = Decimal("0.015")
        level.close_fill_time = 1700001000.0
        level.round_trip_count = 10
        level.cumulative_pnl = Decimal("5.67")

        restored = GridLevel.from_dict(level.to_dict())

        self.assertEqual(restored.id, level.id)
        self.assertEqual(restored.price, level.price)
        self.assertEqual(restored.side, level.side)
        self.assertEqual(restored.state, level.state)
        self.assertEqual(restored.open_order_id, level.open_order_id)
        self.assertEqual(restored.open_fill_price, level.open_fill_price)
        self.assertEqual(restored.close_fill_price, level.close_fill_price)
        self.assertEqual(restored.round_trip_count, level.round_trip_count)
        self.assertEqual(restored.cumulative_pnl, level.cumulative_pnl)

    def test_lifecycle_flow(self):
        """完整生命周期流程"""
        level = self._make_level()

        # IDLE -> OPEN_PENDING
        level.open_order_id = 100
        level.state = GridLevelState.OPEN_PENDING
        self.assertEqual(level.state, GridLevelState.OPEN_PENDING)

        # OPEN_PENDING -> OPEN_FILLED
        level.open_fill_price = Decimal("2000")
        level.open_fill_amount = Decimal("0.01")
        level.state = GridLevelState.OPEN_FILLED
        self.assertEqual(level.state, GridLevelState.OPEN_FILLED)

        # OPEN_FILLED -> CLOSE_PENDING
        level.close_order_id = 101
        level.state = GridLevelState.CLOSE_PENDING
        self.assertEqual(level.state, GridLevelState.CLOSE_PENDING)

        # CLOSE_PENDING -> COMPLETED
        level.close_fill_price = Decimal("2010")
        level.close_fill_amount = Decimal("0.01")
        level.state = GridLevelState.COMPLETED
        self.assertEqual(level.state, GridLevelState.COMPLETED)

        # COMPLETED -> IDLE (reset)
        level.round_trip_count = 1
        level.cumulative_pnl = Decimal("0.10")
        level.reset()
        self.assertEqual(level.state, GridLevelState.IDLE)
        self.assertIsNone(level.open_order_id)
        self.assertEqual(level.round_trip_count, 1)


class TestPrecisionTools(unittest.TestCase):
    def test_to_decimal_from_float(self):
        """float -> Decimal（通过 str 中转）"""
        result = to_decimal(0.1)
        self.assertEqual(result, Decimal("0.1"))

    def test_to_decimal_from_string(self):
        """str -> Decimal"""
        result = to_decimal("123.456")
        self.assertEqual(result, Decimal("123.456"))

    def test_to_decimal_from_decimal(self):
        """Decimal -> Decimal（直接返回）"""
        d = Decimal("99.99")
        result = to_decimal(d)
        self.assertIs(result, d)

    def test_to_decimal_none(self):
        """None -> 默认值"""
        result = to_decimal(None)
        self.assertEqual(result, Decimal("0"))
        result = to_decimal(None, "10")
        self.assertEqual(result, Decimal("10"))

    def test_to_decimal_invalid(self):
        """无效值 -> 默认值"""
        result = to_decimal("not_a_number", "0")
        self.assertEqual(result, Decimal("0"))

    def test_quantize_price(self):
        """价格精度对齐"""
        result = quantize_price(Decimal("2000.37"), Decimal("0.1"))
        self.assertEqual(result, Decimal("2000.4"))

        result = quantize_price(Decimal("2000.34"), Decimal("0.1"))
        self.assertEqual(result, Decimal("2000.3"))

    def test_quantize_size(self):
        """数量精度对齐（向下取整）"""
        result = quantize_size(Decimal("0.01259"), Decimal("0.001"))
        self.assertEqual(result, Decimal("0.012"))

        # 不会向上取整
        result = quantize_size(Decimal("0.01299"), Decimal("0.001"))
        self.assertEqual(result, Decimal("0.012"))

    def test_quantize_zero_step(self):
        """步长为 0 时原值返回"""
        result = quantize_price(Decimal("100"), Decimal("0"))
        self.assertEqual(result, Decimal("100"))
        result = quantize_size(Decimal("100"), Decimal("0"))
        self.assertEqual(result, Decimal("100"))


class TestCalculateGridConfig(unittest.TestCase):
    def test_basic_neutral(self):
        """NEUTRAL 模式基本计算"""
        from src.utils.grid_math import calculate_grid_config

        result = calculate_grid_config(
            current_price=2000.0,
            available_balance=100.0,
            mode="NEUTRAL",
            width_pct=0.05,
            grid_num=6,
            leverage=10,
        )

        self.assertEqual(result["action"], "UPDATE_GRID")
        self.assertEqual(result["mode"], "NEUTRAL")
        self.assertEqual(result["grid_num"], 6)
        # 区间应对称
        self.assertAlmostEqual(result["lower_price"], 1950.0, places=1)
        self.assertAlmostEqual(result["upper_price"], 2050.0, places=1)
        # tp_ratio > 0
        self.assertGreater(result["tp_ratio"], 0)
        # sl_ratio > tp_ratio
        self.assertGreater(result["sl_ratio"], result["tp_ratio"])

    def test_long_mode(self):
        """LONG 模式"""
        from src.utils.grid_math import calculate_grid_config

        result = calculate_grid_config(
            current_price=2000.0,
            available_balance=100.0,
            mode="LONG",
            width_pct=0.05,
        )
        self.assertLess(result["lower_price"], 2000.0)
        # upper 应接近 current_price * 1.01
        self.assertAlmostEqual(result["upper_price"], 2020.0, places=0)

    def test_short_mode(self):
        """SHORT 模式"""
        from src.utils.grid_math import calculate_grid_config

        result = calculate_grid_config(
            current_price=2000.0,
            available_balance=100.0,
            mode="SHORT",
            width_pct=0.05,
        )
        self.assertGreater(result["upper_price"], 2000.0)

    def test_amount_clamping(self):
        """金额被限制在 [15.5, 30.0] 范围"""
        from src.utils.grid_math import calculate_grid_config

        # 大余额应被限制
        result = calculate_grid_config(
            current_price=2000.0,
            available_balance=10000.0,  # 很大
            mode="NEUTRAL",
            width_pct=0.05,
            grid_num=6,
            leverage=10,
        )
        self.assertLessEqual(result["amount_per_grid"], 30.0)

        # 小余额应被提升到最低
        result = calculate_grid_config(
            current_price=2000.0,
            available_balance=1.0,  # 很小
            mode="NEUTRAL",
            width_pct=0.05,
            grid_num=6,
            leverage=10,
        )
        self.assertGreaterEqual(result["amount_per_grid"], 15.5)

    def test_precision_no_float_error(self):
        """Decimal 计算不产生浮点误差"""
        from src.utils.grid_math import calculate_grid_config

        result = calculate_grid_config(
            current_price=2000.0,
            available_balance=77.0,
            mode="NEUTRAL",
            width_pct=0.05,
            grid_num=8,
            leverage=10,
        )

        # 所有输出应为精确的 float（没有 .0000000001 之类的尾巴）
        amount = result["amount_per_grid"]
        self.assertEqual(amount, round(amount, 2))


if __name__ == "__main__":
    unittest.main()
