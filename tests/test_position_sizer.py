"""
仓位管理器测试
"""

import pytest
from src.trading.position_sizer import (
    PositionSizer,
    PositionSizeResult,
    PositionSizeMethod,
    TradeHistory,
    calculate_optimal_leverage,
    create_position_sizer_from_config
)


class TestPositionSizer:
    """仓位管理器测试类"""

    @pytest.fixture
    def sizer(self):
        """创建默认仓位管理器"""
        return PositionSizer(
            max_position_size=1000.0,
            max_account_risk=0.02,
            max_total_exposure=0.5,
            kelly_fraction=0.25,
            min_position_ratio=0.1,
            max_position_ratio=1.0
        )

    # === 基础仓位计算测试 ===

    def test_calculate_position_size_basic(self, sizer):
        """测试基础仓位计算"""
        result = sizer.calculate_position_size(
            account_balance=10000.0,
            signal_score=0.7,
            stop_loss_pct=0.02,
            method=PositionSizeMethod.FIXED
        )

        assert isinstance(result, PositionSizeResult)
        assert result.adjusted_size > 0
        assert result.adjusted_size <= sizer.max_position_size
        assert 0 <= result.size_ratio <= 1

    def test_calculate_position_size_zero_balance(self, sizer):
        """测试零余额时的仓位计算"""
        result = sizer.calculate_position_size(
            account_balance=0.0,
            signal_score=0.7,
            stop_loss_pct=0.02
        )

        assert result.adjusted_size == 0

    def test_calculate_position_size_zero_stop_loss(self, sizer):
        """测试零止损时的仓位计算"""
        result = sizer.calculate_position_size(
            account_balance=10000.0,
            signal_score=0.7,
            stop_loss_pct=0.0
        )

        assert result.base_size == 0

    # === 信号因子测试 ===

    def test_signal_factor_high_score(self, sizer):
        """测试高信号评分"""
        result = sizer.calculate_position_size(
            account_balance=10000.0,
            signal_score=0.9,
            stop_loss_pct=0.02,
            method=PositionSizeMethod.SIGNAL_BASED
        )

        # 高信号评分应该有较大的仓位
        assert result.signal_factor >= 0.8

    def test_signal_factor_low_score(self, sizer):
        """测试低信号评分"""
        result = sizer.calculate_position_size(
            account_balance=10000.0,
            signal_score=0.3,
            stop_loss_pct=0.02,
            method=PositionSizeMethod.SIGNAL_BASED
        )

        # 低信号评分应该有较小的仓位
        assert result.signal_factor <= 0.3

    def test_signal_factor_comparison(self, sizer):
        """测试信号因子对比"""
        result_high = sizer.calculate_position_size(
            account_balance=10000.0,
            signal_score=0.8,
            stop_loss_pct=0.02,
            method=PositionSizeMethod.SIGNAL_BASED
        )

        result_low = sizer.calculate_position_size(
            account_balance=10000.0,
            signal_score=0.4,
            stop_loss_pct=0.02,
            method=PositionSizeMethod.SIGNAL_BASED
        )

        # 高信号评分应该产生更高的信号因子
        assert result_high.signal_factor >= result_low.signal_factor

    # === 波动率因子测试 ===

    def test_volatility_factor_normal(self, sizer):
        """测试正常波动率"""
        result = sizer.calculate_position_size(
            account_balance=10000.0,
            signal_score=0.7,
            stop_loss_pct=0.02,
            current_volatility=0.015,
            average_volatility=0.015,
            method=PositionSizeMethod.VOLATILITY_ADJUSTED
        )

        # 正常波动率因子应该接近1
        assert 0.9 <= result.volatility_factor <= 1.1

    def test_volatility_factor_high(self, sizer):
        """测试高波动率"""
        result = sizer.calculate_position_size(
            account_balance=10000.0,
            signal_score=0.7,
            stop_loss_pct=0.02,
            current_volatility=0.03,
            average_volatility=0.015,
            method=PositionSizeMethod.VOLATILITY_ADJUSTED
        )

        # 高波动率应该减少仓位
        assert result.volatility_factor < 1.0

    def test_volatility_factor_low(self, sizer):
        """测试低波动率"""
        result = sizer.calculate_position_size(
            account_balance=10000.0,
            signal_score=0.7,
            stop_loss_pct=0.02,
            current_volatility=0.005,
            average_volatility=0.015,
            method=PositionSizeMethod.VOLATILITY_ADJUSTED
        )

        # 低波动率可以增加仓位
        assert result.volatility_factor >= 1.0

    # === 回撤因子测试 ===

    def test_drawdown_factor_no_drawdown(self, sizer):
        """测试无回撤时"""
        sizer.update_balance(10000.0)
        sizer.update_balance(10500.0)  # 新高

        result = sizer.calculate_position_size(
            account_balance=10500.0,
            signal_score=0.7,
            stop_loss_pct=0.02
        )

        # 无回撤时因子应该为1
        assert result.drawdown_factor == 1.0

    def test_drawdown_factor_with_drawdown(self, sizer):
        """测试有回撤时"""
        sizer.update_balance(10000.0)
        sizer.update_balance(9000.0)  # 10% 回撤

        result = sizer.calculate_position_size(
            account_balance=9000.0,
            signal_score=0.7,
            stop_loss_pct=0.02
        )

        # 有回撤时应该减少仓位
        assert result.drawdown_factor < 1.0

    def test_drawdown_factor_severe(self, sizer):
        """测试严重回撤时"""
        sizer.update_balance(10000.0)
        sizer.update_balance(8000.0)  # 20% 回撤

        result = sizer.calculate_position_size(
            account_balance=8000.0,
            signal_score=0.7,
            stop_loss_pct=0.02
        )

        # 严重回撤时应该大幅减少仓位
        assert result.drawdown_factor <= 0.4

    # === 敞口限制测试 ===

    def test_exposure_limit_no_exposure(self, sizer):
        """测试无现有敞口时"""
        result = sizer.calculate_position_size(
            account_balance=10000.0,
            signal_score=0.7,
            stop_loss_pct=0.02,
            current_exposure=0.0
        )

        # 无敞口时不受限制
        assert result.adjusted_size > 0

    def test_exposure_limit_partial(self, sizer):
        """测试部分敞口时"""
        result = sizer.calculate_position_size(
            account_balance=10000.0,
            signal_score=0.7,
            stop_loss_pct=0.02,
            current_exposure=4000.0  # 40% 敞口
        )

        # 还有10%的空间
        assert result.adjusted_size > 0

    def test_exposure_limit_full(self, sizer):
        """测试满敞口时"""
        result = sizer.calculate_position_size(
            account_balance=10000.0,
            signal_score=0.7,
            stop_loss_pct=0.02,
            current_exposure=5000.0  # 50% 敞口 = 最大
        )

        # 满敞口时应该返回0
        assert result.adjusted_size == 0

    # === 交易记录测试 ===

    def test_record_trade_win(self, sizer):
        """测试记录盈利交易"""
        sizer.record_trade(is_win=True, pnl_pct=0.05, signal_score=0.7)

        stats = sizer.get_statistics()
        assert stats['total_trades'] == 1
        assert stats['win_rate'] == 1.0

    def test_record_trade_loss(self, sizer):
        """测试记录亏损交易"""
        sizer.record_trade(is_win=False, pnl_pct=-0.02, signal_score=0.6)

        stats = sizer.get_statistics()
        assert stats['total_trades'] == 1
        assert stats['win_rate'] == 0.0

    def test_record_multiple_trades(self, sizer):
        """测试记录多次交易"""
        sizer.record_trade(is_win=True, pnl_pct=0.05, signal_score=0.7)
        sizer.record_trade(is_win=True, pnl_pct=0.03, signal_score=0.6)
        sizer.record_trade(is_win=False, pnl_pct=-0.02, signal_score=0.5)
        sizer.record_trade(is_win=True, pnl_pct=0.04, signal_score=0.8)

        stats = sizer.get_statistics()
        assert stats['total_trades'] == 4
        assert stats['win_rate'] == 0.75  # 3/4

    def test_consecutive_losses(self, sizer):
        """测试连续亏损计数"""
        sizer.record_trade(is_win=True, pnl_pct=0.05, signal_score=0.7)
        sizer.record_trade(is_win=False, pnl_pct=-0.02, signal_score=0.5)
        sizer.record_trade(is_win=False, pnl_pct=-0.02, signal_score=0.5)
        sizer.record_trade(is_win=False, pnl_pct=-0.02, signal_score=0.5)

        assert sizer.get_consecutive_losses() == 3

    def test_consecutive_losses_reset(self, sizer):
        """测试连续亏损重置"""
        sizer.record_trade(is_win=False, pnl_pct=-0.02, signal_score=0.5)
        sizer.record_trade(is_win=False, pnl_pct=-0.02, signal_score=0.5)
        sizer.record_trade(is_win=True, pnl_pct=0.05, signal_score=0.7)

        assert sizer.get_consecutive_losses() == 0

    # === 凯利公式测试 ===

    def test_kelly_factor_with_history(self, sizer):
        """测试有历史记录时的凯利因子"""
        # 添加一些交易记录
        for _ in range(5):
            sizer.record_trade(is_win=True, pnl_pct=0.05, signal_score=0.7)
        for _ in range(3):
            sizer.record_trade(is_win=False, pnl_pct=-0.02, signal_score=0.5)

        result = sizer.calculate_position_size(
            account_balance=10000.0,
            signal_score=0.7,
            stop_loss_pct=0.02,
            method=PositionSizeMethod.KELLY
        )

        # 凯利因子应该在合理范围内
        assert 0.1 <= result.kelly_factor <= 1.0

    def test_kelly_factor_no_history(self, sizer):
        """测试无历史记录时的凯利因子"""
        result = sizer.calculate_position_size(
            account_balance=10000.0,
            signal_score=0.7,
            stop_loss_pct=0.02,
            method=PositionSizeMethod.KELLY
        )

        # 无历史时使用信号评分估计
        assert 0.1 <= result.kelly_factor <= 1.0

    # === 结果汇总测试 ===

    def test_result_summary(self, sizer):
        """测试结果摘要"""
        result = sizer.calculate_position_size(
            account_balance=10000.0,
            signal_score=0.7,
            stop_loss_pct=0.02,
            current_volatility=0.02,
            average_volatility=0.015,
            method=PositionSizeMethod.SIGNAL_BASED
        )

        summary = result.get_summary()
        assert isinstance(summary, str)
        assert "$" in summary
        assert "%" in summary

    def test_statistics(self, sizer):
        """测试统计信息"""
        sizer.record_trade(is_win=True, pnl_pct=0.05, signal_score=0.7)
        sizer.record_trade(is_win=False, pnl_pct=-0.02, signal_score=0.5)
        sizer.update_balance(10000.0)

        stats = sizer.get_statistics()

        assert 'total_trades' in stats
        assert 'win_rate' in stats
        assert 'avg_win' in stats
        assert 'avg_loss' in stats
        assert 'profit_factor' in stats
        assert 'current_drawdown' in stats


class TestCalculateOptimalLeverage:
    """最优杠杆计算测试"""

    def test_optimal_leverage_high_signal(self):
        """测试高信号时的杠杆"""
        leverage = calculate_optimal_leverage(
            signal_score=0.8,
            stop_loss_pct=0.02,
            max_leverage=10
        )

        assert 1 <= leverage <= 10

    def test_optimal_leverage_low_signal(self):
        """测试低信号时的杠杆"""
        leverage = calculate_optimal_leverage(
            signal_score=0.3,
            stop_loss_pct=0.02,
            max_leverage=10
        )

        # 低信号应该使用低杠杆
        assert leverage <= 5

    def test_optimal_leverage_comparison(self):
        """测试杠杆对比"""
        leverage_high = calculate_optimal_leverage(
            signal_score=0.9,
            stop_loss_pct=0.02,
            max_leverage=10
        )

        leverage_low = calculate_optimal_leverage(
            signal_score=0.4,
            stop_loss_pct=0.02,
            max_leverage=10
        )

        # 高信号应该允许更高杠杆
        assert leverage_high >= leverage_low

    def test_optimal_leverage_zero_stop_loss(self):
        """测试零止损时的杠杆"""
        leverage = calculate_optimal_leverage(
            signal_score=0.7,
            stop_loss_pct=0.0,
            max_leverage=10
        )

        assert leverage == 1

    def test_optimal_leverage_respects_max(self):
        """测试杠杆不超过最大值"""
        leverage = calculate_optimal_leverage(
            signal_score=1.0,
            stop_loss_pct=0.001,  # 很小的止损
            max_leverage=5
        )

        assert leverage <= 5


class TestCreatePositionSizerFromConfig:
    """从配置创建仓位管理器测试"""

    def test_create_from_config_default(self):
        """测试默认配置"""
        config = {}
        sizer = create_position_sizer_from_config(config)

        assert isinstance(sizer, PositionSizer)
        assert sizer.max_position_size == 1000.0

    def test_create_from_config_custom(self):
        """测试自定义配置"""
        config = {
            'trading': {
                'max_trade_amount': 500.0
            },
            'enhanced_analysis': {
                'risk': {
                    'max_risk_per_trade': 0.01,
                    'max_total_exposure': 0.3,
                    'kelly_fraction': 0.5
                }
            }
        }

        sizer = create_position_sizer_from_config(config)

        assert sizer.max_position_size == 500.0
        assert sizer.max_account_risk == 0.01
        assert sizer.max_total_exposure == 0.3
        assert sizer.kelly_fraction == 0.5


class TestTradeHistory:
    """交易历史测试"""

    def test_trade_history_creation(self):
        """测试创建交易历史"""
        history = TradeHistory(
            is_win=True,
            pnl_pct=0.05,
            signal_score=0.7
        )

        assert history.is_win
        assert history.pnl_pct == 0.05
        assert history.signal_score == 0.7
