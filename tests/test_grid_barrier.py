"""
Triple Barrier 风控测试
"""

import time
import unittest
from decimal import Decimal

from src.trading.grid_barrier import GridBarrierMonitor, TripleBarrierConfig


class TestTripleBarrierConfig(unittest.TestCase):
    def test_default_config(self):
        """默认配置值"""
        cfg = TripleBarrierConfig()
        self.assertEqual(cfg.stop_loss_pct, Decimal("0.05"))
        self.assertEqual(cfg.take_profit_pct, Decimal("0.10"))
        self.assertEqual(cfg.time_limit_seconds, 14400)
        self.assertEqual(cfg.trailing_stop_activation_pct, Decimal("0.03"))
        self.assertEqual(cfg.trailing_stop_delta_pct, Decimal("0.01"))
        self.assertIsNone(cfg.price_lower_limit)
        self.assertIsNone(cfg.price_upper_limit)

    def test_from_config(self):
        """从字典构建配置"""
        cfg = TripleBarrierConfig.from_config(
            {
                "stop_loss_pct": 0.03,
                "take_profit_pct": 0.08,
                "time_limit_seconds": 7200,
                "trailing_stop_activation_pct": None,
                "price_lower_limit": 1800,
                "price_upper_limit": 2200,
            }
        )
        self.assertEqual(cfg.stop_loss_pct, Decimal("0.03"))
        self.assertEqual(cfg.take_profit_pct, Decimal("0.08"))
        self.assertEqual(cfg.time_limit_seconds, 7200)
        self.assertIsNone(cfg.trailing_stop_activation_pct)
        self.assertEqual(cfg.price_lower_limit, Decimal("1800"))
        self.assertEqual(cfg.price_upper_limit, Decimal("2200"))

    def test_from_empty_config(self):
        """空字典使用默认值"""
        cfg = TripleBarrierConfig.from_config({})
        self.assertEqual(cfg.stop_loss_pct, Decimal("0.05"))


class TestGridBarrierMonitor(unittest.TestCase):
    def setUp(self):
        self.start_time = time.time()

    def test_no_trigger_normal(self):
        """正常情况不触发"""
        cfg = TripleBarrierConfig()
        monitor = GridBarrierMonitor(cfg, self.start_time)

        result = monitor.check(
            current_price=Decimal("2000"),
            net_pnl_pct=Decimal("0.01"),  # 1% 盈利
            current_time=self.start_time + 100,
        )
        self.assertIsNone(result)

    def test_stop_loss_trigger(self):
        """止损触发"""
        cfg = TripleBarrierConfig(stop_loss_pct=Decimal("0.05"))
        monitor = GridBarrierMonitor(cfg, self.start_time)

        result = monitor.check(
            current_price=Decimal("2000"),
            net_pnl_pct=Decimal("-0.06"),  # -6% < -5%
            current_time=self.start_time + 100,
        )
        self.assertIsNotNone(result)
        self.assertIn("STOP_LOSS", result)

    def test_stop_loss_boundary(self):
        """止损边界：刚好等于阈值也触发"""
        cfg = TripleBarrierConfig(stop_loss_pct=Decimal("0.05"))
        monitor = GridBarrierMonitor(cfg, self.start_time)

        result = monitor.check(
            current_price=Decimal("2000"),
            net_pnl_pct=Decimal("-0.05"),
            current_time=self.start_time + 100,
        )
        self.assertIsNotNone(result)

    def test_take_profit_trigger(self):
        """止盈触发"""
        cfg = TripleBarrierConfig(take_profit_pct=Decimal("0.10"))
        monitor = GridBarrierMonitor(cfg, self.start_time)

        result = monitor.check(
            current_price=Decimal("2000"),
            net_pnl_pct=Decimal("0.12"),  # 12% > 10%
            current_time=self.start_time + 100,
        )
        self.assertIsNotNone(result)
        self.assertIn("TAKE_PROFIT", result)

    def test_time_limit_trigger(self):
        """时间限制触发"""
        cfg = TripleBarrierConfig(time_limit_seconds=3600)
        monitor = GridBarrierMonitor(cfg, self.start_time)

        result = monitor.check(
            current_price=Decimal("2000"),
            net_pnl_pct=Decimal("0.01"),
            current_time=self.start_time + 3700,  # 超过 1 小时
        )
        self.assertIsNotNone(result)
        self.assertIn("TIME_LIMIT", result)

    def test_time_limit_not_trigger(self):
        """时间限制未到"""
        cfg = TripleBarrierConfig(time_limit_seconds=3600)
        monitor = GridBarrierMonitor(cfg, self.start_time)

        result = monitor.check(
            current_price=Decimal("2000"),
            net_pnl_pct=Decimal("0.01"),
            current_time=self.start_time + 1800,  # 30 分钟
        )
        self.assertIsNone(result)

    def test_price_lower_limit_trigger(self):
        """下限价格触发"""
        cfg = TripleBarrierConfig(
            stop_loss_pct=None,
            price_lower_limit=Decimal("1800"),
        )
        monitor = GridBarrierMonitor(cfg, self.start_time)

        result = monitor.check(
            current_price=Decimal("1750"),
            net_pnl_pct=Decimal("0"),
            current_time=self.start_time + 100,
        )
        self.assertIsNotNone(result)
        self.assertIn("PRICE_LIMIT", result)

    def test_price_upper_limit_trigger(self):
        """上限价格触发"""
        cfg = TripleBarrierConfig(
            stop_loss_pct=None,
            price_upper_limit=Decimal("2200"),
        )
        monitor = GridBarrierMonitor(cfg, self.start_time)

        result = monitor.check(
            current_price=Decimal("2300"),
            net_pnl_pct=Decimal("0"),
            current_time=self.start_time + 100,
        )
        self.assertIsNotNone(result)
        self.assertIn("PRICE_LIMIT", result)

    def test_trailing_stop_not_activated(self):
        """追踪止损未激活"""
        cfg = TripleBarrierConfig(
            stop_loss_pct=None,
            take_profit_pct=None,
            time_limit_seconds=None,
            trailing_stop_activation_pct=Decimal("0.03"),
            trailing_stop_delta_pct=Decimal("0.01"),
        )
        monitor = GridBarrierMonitor(cfg, self.start_time)

        # PnL 2% < 3% 激活阈值
        result = monitor.check(
            current_price=Decimal("2000"),
            net_pnl_pct=Decimal("0.02"),
            current_time=self.start_time + 100,
        )
        self.assertIsNone(result)
        self.assertIsNone(monitor._trailing_stop_high_water)

    def test_trailing_stop_activated_then_trigger(self):
        """追踪止损激活后触发"""
        cfg = TripleBarrierConfig(
            stop_loss_pct=None,
            take_profit_pct=None,
            time_limit_seconds=None,
            trailing_stop_activation_pct=Decimal("0.03"),
            trailing_stop_delta_pct=Decimal("0.01"),
        )
        monitor = GridBarrierMonitor(cfg, self.start_time)

        # 第一次：激活（PnL 达 5%）
        result = monitor.check(
            current_price=Decimal("2000"),
            net_pnl_pct=Decimal("0.05"),
            current_time=self.start_time + 100,
        )
        self.assertIsNone(result)
        self.assertEqual(monitor._trailing_stop_high_water, Decimal("0.05"))

        # 第二次：更新高水位到 7%
        result = monitor.check(
            current_price=Decimal("2000"),
            net_pnl_pct=Decimal("0.07"),
            current_time=self.start_time + 200,
        )
        self.assertIsNone(result)
        self.assertEqual(monitor._trailing_stop_high_water, Decimal("0.07"))

        # 第三次：回撤到 5.5%（回撤 1.5% >= 1%）
        result = monitor.check(
            current_price=Decimal("2000"),
            net_pnl_pct=Decimal("0.055"),
            current_time=self.start_time + 300,
        )
        self.assertIsNotNone(result)
        self.assertIn("TRAILING_STOP", result)

    def test_trailing_stop_no_trigger_small_drawdown(self):
        """追踪止损激活但回撤不够不触发"""
        cfg = TripleBarrierConfig(
            stop_loss_pct=None,
            take_profit_pct=None,
            time_limit_seconds=None,
            trailing_stop_activation_pct=Decimal("0.03"),
            trailing_stop_delta_pct=Decimal("0.01"),
        )
        monitor = GridBarrierMonitor(cfg, self.start_time)

        # 激活
        monitor.check(
            current_price=Decimal("2000"),
            net_pnl_pct=Decimal("0.05"),
            current_time=self.start_time + 100,
        )

        # 小回撤 0.5% < 1%
        result = monitor.check(
            current_price=Decimal("2000"),
            net_pnl_pct=Decimal("0.045"),
            current_time=self.start_time + 200,
        )
        self.assertIsNone(result)

    def test_priority_stop_loss_over_take_profit(self):
        """优先级：止损 > 止盈（理论上不会同时满足，但测试优先级逻辑）"""
        cfg = TripleBarrierConfig(
            stop_loss_pct=Decimal("0.05"),
            take_profit_pct=Decimal("0.10"),
        )
        monitor = GridBarrierMonitor(cfg, self.start_time)

        result = monitor.check(
            current_price=Decimal("2000"),
            net_pnl_pct=Decimal("-0.06"),
            current_time=self.start_time + 100,
        )
        self.assertIn("STOP_LOSS", result)

    def test_disabled_barriers(self):
        """所有屏障禁用时不触发"""
        cfg = TripleBarrierConfig(
            stop_loss_pct=None,
            take_profit_pct=None,
            time_limit_seconds=None,
            trailing_stop_activation_pct=None,
            trailing_stop_delta_pct=None,
        )
        monitor = GridBarrierMonitor(cfg, self.start_time)

        result = monitor.check(
            current_price=Decimal("2000"),
            net_pnl_pct=Decimal("-0.50"),  # 大亏但禁用了
            current_time=self.start_time + 999999,
        )
        self.assertIsNone(result)

    def test_reset(self):
        """重置监控器"""
        cfg = TripleBarrierConfig()
        monitor = GridBarrierMonitor(cfg, self.start_time)
        monitor._trailing_stop_high_water = Decimal("0.05")

        new_start = time.time()
        monitor.reset(new_start)

        self.assertEqual(monitor.start_time, new_start)
        self.assertIsNone(monitor._trailing_stop_high_water)


if __name__ == "__main__":
    unittest.main()
