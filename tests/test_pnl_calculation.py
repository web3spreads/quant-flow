"""
平仓盈亏计算逻辑的单元测试

验证做多/做空平仓时 PnL 金额和百分比的计算正确性，
包括正盈亏、负盈亏、持平等场景。
"""

import pytest


def calc_long_pnl(entry_price: float, exit_price: float, size: float, leverage: int = 1):
    """做多盈亏计算（与 agent 回调中的逻辑一致）"""
    pnl = (exit_price - entry_price) * size
    pnl_percent = (
        (exit_price - entry_price) / entry_price * leverage * 100
        if entry_price > 0
        else 0
    )
    return pnl, pnl_percent


def calc_short_pnl(entry_price: float, exit_price: float, size: float, leverage: int = 1):
    """做空盈亏计算（与 agent 回调中的逻辑一致）"""
    pnl = (entry_price - exit_price) * size
    pnl_percent = (
        (entry_price - exit_price) / entry_price * leverage * 100
        if entry_price > 0
        else 0
    )
    return pnl, pnl_percent


# ==================== 做多测试 ====================


class TestLongPnl:
    """做多平仓盈亏计算测试"""

    def test_long_profit(self):
        """做多盈利：价格上涨"""
        pnl, pnl_pct = calc_long_pnl(
            entry_price=2000.0, exit_price=2100.0, size=0.5
        )
        assert pnl == pytest.approx(50.0)  # (2100 - 2000) * 0.5 = 50
        assert pnl_pct == pytest.approx(5.0)  # +5%

    def test_long_loss(self):
        """做多亏损：价格下跌"""
        pnl, pnl_pct = calc_long_pnl(
            entry_price=2000.0, exit_price=1900.0, size=0.5
        )
        assert pnl == pytest.approx(-50.0)  # (1900 - 2000) * 0.5 = -50
        assert pnl_pct == pytest.approx(-5.0)  # -5%

    def test_long_breakeven(self):
        """做多持平：价格不变"""
        pnl, pnl_pct = calc_long_pnl(
            entry_price=2000.0, exit_price=2000.0, size=1.0
        )
        assert pnl == pytest.approx(0.0)
        assert pnl_pct == pytest.approx(0.0)

    def test_long_with_leverage(self):
        """做多带杠杆：收益率按杠杆放大"""
        pnl, pnl_pct = calc_long_pnl(
            entry_price=2000.0, exit_price=2100.0, size=0.5, leverage=10
        )
        # PnL 金额不受杠杆影响（已经体现在 size 中）
        assert pnl == pytest.approx(50.0)
        # 收益率按杠杆放大：5% * 10 = 50%
        assert pnl_pct == pytest.approx(50.0)

    def test_long_small_eth_position(self):
        """模拟用户实际场景：ETH 做多小仓位"""
        pnl, pnl_pct = calc_long_pnl(
            entry_price=2127.90, exit_price=2110.00, size=0.0282
        )
        # 价格下跌，做多亏损
        assert pnl == pytest.approx(-0.5049, abs=0.01)
        assert pnl_pct < 0


# ==================== 做空测试 ====================


class TestShortPnl:
    """做空平仓盈亏计算测试"""

    def test_short_profit(self):
        """做空盈利：价格下跌"""
        pnl, pnl_pct = calc_short_pnl(
            entry_price=2000.0, exit_price=1900.0, size=0.5
        )
        assert pnl == pytest.approx(50.0)  # (2000 - 1900) * 0.5 = 50
        assert pnl_pct == pytest.approx(5.0)  # +5%

    def test_short_loss(self):
        """做空亏损：价格上涨"""
        pnl, pnl_pct = calc_short_pnl(
            entry_price=2000.0, exit_price=2100.0, size=0.5
        )
        assert pnl == pytest.approx(-50.0)  # (2000 - 2100) * 0.5 = -50
        assert pnl_pct == pytest.approx(-5.0)  # -5%

    def test_short_breakeven(self):
        """做空持平：价格不变"""
        pnl, pnl_pct = calc_short_pnl(
            entry_price=2000.0, exit_price=2000.0, size=1.0
        )
        assert pnl == pytest.approx(0.0)
        assert pnl_pct == pytest.approx(0.0)

    def test_short_with_leverage(self):
        """做空带杠杆：收益率按杠杆放大"""
        pnl, pnl_pct = calc_short_pnl(
            entry_price=2000.0, exit_price=1900.0, size=0.5, leverage=10
        )
        assert pnl == pytest.approx(50.0)
        assert pnl_pct == pytest.approx(50.0)  # 5% * 10x

    def test_short_eth_actual_case(self):
        """模拟用户实际场景：ETH 做空 $2127.90 -> $2110.00"""
        pnl, pnl_pct = calc_short_pnl(
            entry_price=2127.90, exit_price=2110.00, size=0.0282
        )
        # 做空盈利：(2127.90 - 2110.00) * 0.0282 = 0.5049
        assert pnl == pytest.approx(0.5049, abs=0.01)
        assert pnl_pct > 0


# ==================== 符号一致性测试 ====================


class TestPnlSignConsistency:
    """验证做多/做空 PnL 符号方向的一致性"""

    def test_price_up_long_profit_short_loss(self):
        """价格上涨时：做多盈利，做空亏损"""
        long_pnl, _ = calc_long_pnl(entry_price=100, exit_price=110, size=1.0)
        short_pnl, _ = calc_short_pnl(entry_price=100, exit_price=110, size=1.0)
        assert long_pnl > 0, "价格上涨时做多应盈利"
        assert short_pnl < 0, "价格上涨时做空应亏损"
        # 绝对值应相等
        assert abs(long_pnl) == pytest.approx(abs(short_pnl))

    def test_price_down_long_loss_short_profit(self):
        """价格下跌时：做多亏损，做空盈利"""
        long_pnl, _ = calc_long_pnl(entry_price=100, exit_price=90, size=1.0)
        short_pnl, _ = calc_short_pnl(entry_price=100, exit_price=90, size=1.0)
        assert long_pnl < 0, "价格下跌时做多应亏损"
        assert short_pnl > 0, "价格下跌时做空应盈利"
        assert abs(long_pnl) == pytest.approx(abs(short_pnl))

    def test_entry_price_zero_returns_zero_percent(self):
        """入场价为 0 时，收益率应为 0（防止除零）"""
        _, long_pct = calc_long_pnl(entry_price=0, exit_price=100, size=1.0)
        _, short_pct = calc_short_pnl(entry_price=0, exit_price=100, size=1.0)
        assert long_pct == 0
        assert short_pct == 0
