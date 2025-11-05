#!/usr/bin/env python3
"""
单元测试：杠杆收益率计算
测试 single_symbol_agent.py 中的 PnL 百分比计算逻辑
"""

import unittest
from unittest.mock import Mock, MagicMock, patch
from typing import Dict, Any


class TestLeveragePnLCalculation(unittest.TestCase):
    """测试杠杆收益率计算逻辑"""

    def calculate_pnl_percent(self, position: Dict[str, Any], pnl: float) -> float:
        """
        模拟 single_symbol_agent.py 中的收益率计算逻辑
        这是从实际代码中提取的计算方法
        """
        entry_price = position.get('entryPx', 0)
        szi = position.get('szi', 0)
        size = abs(float(szi)) if szi else 0

        # 获取杠杆值
        leverage_info = position.get('leverage', {})
        if isinstance(leverage_info, dict):
            leverage = float(leverage_info.get('value', 1))
        elif leverage_info:
            leverage = float(leverage_info)
        else:
            leverage = 1

        # 计算收益率
        entry_price_float = float(entry_price) if entry_price else 0
        position_value = size * entry_price_float

        if position_value > 0 and leverage > 0:
            margin = position_value / leverage
            pnl_percent = (pnl / margin) * 100
        else:
            pnl_percent = 0

        return pnl_percent

    def test_long_position_1x_leverage_profit(self):
        """测试多头仓位，1倍杠杆，盈利情况"""
        position = {
            'entryPx': '50000',  # 入场价格 $50,000
            'szi': '0.1',        # 仓位大小 0.1 BTC
            'leverage': {'value': 1}
        }
        # 价格上涨 10%，PnL = 0.1 * 5000 = $500
        pnl = 500

        pnl_percent = self.calculate_pnl_percent(position, pnl)

        # 1倍杠杆：保证金 = 5000，收益率 = 500/5000 * 100 = 10%
        self.assertAlmostEqual(pnl_percent, 10.0, places=2)

    def test_long_position_10x_leverage_profit(self):
        """测试多头仓位，10倍杠杆，盈利情况"""
        position = {
            'entryPx': '50000',
            'szi': '0.1',
            'leverage': {'value': 10}
        }
        # 价格上涨 10%，PnL = $500
        pnl = 500

        pnl_percent = self.calculate_pnl_percent(position, pnl)

        # 10倍杠杆：保证金 = 500，收益率 = 500/500 * 100 = 100%
        self.assertAlmostEqual(pnl_percent, 100.0, places=2)

    def test_long_position_5x_leverage_profit(self):
        """测试多头仓位，5倍杠杆，盈利情况"""
        position = {
            'entryPx': '100',
            'szi': '10',
            'leverage': {'value': 5}
        }
        # 仓位价值 = $1000，价格上涨到 $110，PnL = $100
        pnl = 100

        pnl_percent = self.calculate_pnl_percent(position, pnl)

        # 5倍杠杆：保证金 = 200，收益率 = 100/200 * 100 = 50%
        self.assertAlmostEqual(pnl_percent, 50.0, places=2)

    def test_short_position_10x_leverage_profit(self):
        """测试空头仓位，10倍杠杆，盈利情况"""
        position = {
            'entryPx': '50000',
            'szi': '-0.1',  # 负数表示空头
            'leverage': {'value': 10}
        }
        # 价格下跌 5%，PnL = $250
        pnl = 250

        pnl_percent = self.calculate_pnl_percent(position, pnl)

        # 10倍杠杆：保证金 = 500，收益率 = 250/500 * 100 = 50%
        self.assertAlmostEqual(pnl_percent, 50.0, places=2)

    def test_long_position_10x_leverage_loss(self):
        """测试多头仓位，10倍杠杆，亏损情况"""
        position = {
            'entryPx': '50000',
            'szi': '0.1',
            'leverage': {'value': 10}
        }
        # 价格下跌 5%，PnL = -$250
        pnl = -250

        pnl_percent = self.calculate_pnl_percent(position, pnl)

        # 10倍杠杆：保证金 = 500，收益率 = -250/500 * 100 = -50%
        self.assertAlmostEqual(pnl_percent, -50.0, places=2)

    def test_leverage_dict_format(self):
        """测试杠杆为字典格式 {'value': N}"""
        position = {
            'entryPx': '1000',
            'szi': '1',
            'leverage': {'value': 20}
        }
        pnl = 100

        pnl_percent = self.calculate_pnl_percent(position, pnl)

        # 20倍杠杆：保证金 = 50，收益率 = 100/50 * 100 = 200%
        self.assertAlmostEqual(pnl_percent, 200.0, places=2)

    def test_leverage_numeric_format(self):
        """测试杠杆为数字格式（兼容性）"""
        position = {
            'entryPx': '1000',
            'szi': '1',
            'leverage': 20  # 直接是数字
        }
        pnl = 100

        pnl_percent = self.calculate_pnl_percent(position, pnl)

        # 20倍杠杆：保证金 = 50，收益率 = 100/50 * 100 = 200%
        self.assertAlmostEqual(pnl_percent, 200.0, places=2)

    def test_leverage_missing_defaults_to_1x(self):
        """测试缺失杠杆信息时默认为1倍"""
        position = {
            'entryPx': '1000',
            'szi': '1'
            # 没有 leverage 字段
        }
        pnl = 100

        pnl_percent = self.calculate_pnl_percent(position, pnl)

        # 默认1倍杠杆：保证金 = 1000，收益率 = 100/1000 * 100 = 10%
        self.assertAlmostEqual(pnl_percent, 10.0, places=2)

    def test_leverage_empty_dict_defaults_to_1x(self):
        """测试空字典时默认为1倍"""
        position = {
            'entryPx': '1000',
            'szi': '1',
            'leverage': {}  # 空字典
        }
        pnl = 100

        pnl_percent = self.calculate_pnl_percent(position, pnl)

        # 默认1倍杠杆：保证金 = 1000，收益率 = 100/1000 * 100 = 10%
        self.assertAlmostEqual(pnl_percent, 10.0, places=2)

    def test_zero_position_value(self):
        """测试仓位价值为0的边界情况"""
        position = {
            'entryPx': '0',
            'szi': '1',
            'leverage': {'value': 10}
        }
        pnl = 100

        pnl_percent = self.calculate_pnl_percent(position, pnl)

        # 仓位价值为0，应返回0
        self.assertEqual(pnl_percent, 0.0)

    def test_zero_size(self):
        """测试仓位大小为0的边界情况"""
        position = {
            'entryPx': '1000',
            'szi': '0',
            'leverage': {'value': 10}
        }
        pnl = 100

        pnl_percent = self.calculate_pnl_percent(position, pnl)

        # 仓位大小为0，应返回0
        self.assertEqual(pnl_percent, 0.0)

    def test_zero_leverage(self):
        """测试杠杆为0的边界情况（不应发生但需要处理）"""
        position = {
            'entryPx': '1000',
            'szi': '1',
            'leverage': {'value': 0}
        }
        pnl = 100

        pnl_percent = self.calculate_pnl_percent(position, pnl)

        # 杠杆为0，应返回0避免除零错误
        self.assertEqual(pnl_percent, 0.0)

    def test_high_leverage_scenario(self):
        """测试高杠杆场景（100倍）"""
        position = {
            'entryPx': '50000',
            'szi': '0.1',
            'leverage': {'value': 100}
        }
        # 价格上涨 1%，PnL = $50
        pnl = 50

        pnl_percent = self.calculate_pnl_percent(position, pnl)

        # 100倍杠杆：保证金 = 50，收益率 = 50/50 * 100 = 100%
        self.assertAlmostEqual(pnl_percent, 100.0, places=2)

    def test_real_world_scenario_btc(self):
        """测试真实场景：BTC交易"""
        position = {
            'entryPx': '65000',
            'szi': '0.05',  # $3,250 仓位
            'leverage': {'value': 10}
        }
        # 价格涨到 $68,250 (+5%)，PnL = $162.5
        pnl = 162.5

        pnl_percent = self.calculate_pnl_percent(position, pnl)

        # 10倍杠杆：保证金 = 325，收益率 = 162.5/325 * 100 = 50%
        self.assertAlmostEqual(pnl_percent, 50.0, places=1)

    def test_negative_size_short_position(self):
        """测试负数仓位大小（空头）"""
        position = {
            'entryPx': '2000',
            'szi': '-5',  # 空头，但计算时会取绝对值
            'leverage': {'value': 3}
        }
        # 价格下跌，PnL = $300
        pnl = 300

        pnl_percent = self.calculate_pnl_percent(position, pnl)

        # 3倍杠杆：保证金 = 10000/3 = 3333.33，收益率 = 300/3333.33 * 100 = 9%
        self.assertAlmostEqual(pnl_percent, 9.0, places=1)


class TestPnLCalculationComparison(unittest.TestCase):
    """对比旧方法和新方法的计算差异"""

    def old_long_calculation(self, entry_price: float, exit_price: float) -> float:
        """旧的多头计算方法（不考虑杠杆）"""
        return (exit_price - entry_price) / entry_price * 100 if entry_price > 0 else 0

    def old_short_calculation(self, entry_price: float, exit_price: float, leverage: float) -> float:
        """旧的空头计算方法（错误地将杠杆与价格变化相乘）"""
        return ((entry_price - exit_price) / entry_price * leverage * 100) if entry_price > 0 else 0

    def new_calculation(self, entry_price: float, size: float, leverage: float, pnl: float) -> float:
        """新的计算方法（基于保证金）"""
        position_value = size * entry_price
        if position_value > 0 and leverage > 0:
            margin = position_value / leverage
            return (pnl / margin) * 100
        return 0

    def test_comparison_10x_leverage(self):
        """对比10倍杠杆下的差异"""
        entry_price = 50000
        exit_price = 55000  # 上涨10%
        size = 0.1
        leverage = 10
        pnl = 500  # 实际盈利 $500

        # 旧方法（多头，不考虑杠杆）
        old_long = self.old_long_calculation(entry_price, exit_price)
        self.assertAlmostEqual(old_long, 10.0)  # 只显示10%

        # 新方法（正确）
        new_result = self.new_calculation(entry_price, size, leverage, pnl)
        self.assertAlmostEqual(new_result, 100.0)  # 正确显示100%

        # 验证新方法更准确
        self.assertNotEqual(old_long, new_result)
        self.assertAlmostEqual(new_result, old_long * leverage)


if __name__ == '__main__':
    print("=" * 80)
    print("运行杠杆收益率计算单元测试")
    print("=" * 80)
    unittest.main(verbosity=2)
