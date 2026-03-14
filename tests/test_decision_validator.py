"""
决策验证器测试
"""

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from src.trading.decision_validator import (
    DecisionValidation,
    DecisionValidator,
    ValidationCheck,
    ValidationResult,
)


class TestDecisionValidator:
    """决策验证器测试类"""

    @pytest.fixture
    def validator(self):
        """创建默认验证器"""
        return DecisionValidator(
            require_trend_alignment=True,
            min_aligned_timeframes=2,
            min_signal_score=0.4,
            min_risk_reward_ratio=1.5,
            avoid_high_volatility=True,
            prefer_pullback_entry=True,
        )

    @pytest.fixture
    def sample_indicators(self):
        """样本指标数据"""
        return {
            "current_price": 50000.0,
            "rsi": 45.0,
            "rsi_available": True,
            "macd": 100.0,
            "macd_signal": 80.0,
            "macd_hist": 20.0,
            "macd_available": True,
            "bb_position": 0.4,
            "bb_upper": 52000.0,
            "bb_lower": 48000.0,
            "bb_available": True,
            "ema_20": 49500.0,
            "ema_50": 49000.0,
            "atr_14": 500.0,
            "volume": 1000000,
            "volume_ma_20": 900000,
        }

    @pytest.fixture
    def bullish_trends(self):
        """看涨趋势"""
        return {"15分钟": "上涨", "1小时": "强势上涨", "4小时": "上涨", "日线": "强势上涨"}

    @pytest.fixture
    def bearish_trends(self):
        """看跌趋势"""
        return {"15分钟": "下跌", "1小时": "强势下跌", "4小时": "下跌", "日线": "下跌"}

    @pytest.fixture
    def mixed_trends(self):
        """混合趋势"""
        return {"15分钟": "上涨", "1小时": "下跌", "4小时": "震荡", "日线": "上涨"}

    @pytest.fixture
    def sample_df(self):
        """样本 OHLCV 数据"""
        dates = pd.date_range(end=datetime.now(), periods=100, freq="15min")
        np.random.seed(42)

        # 生成价格数据（轻微上涨趋势）
        base_price = 50000
        prices = base_price + np.cumsum(np.random.randn(100) * 50)

        df = pd.DataFrame(
            {
                "open": prices - np.random.rand(100) * 100,
                "high": prices + np.random.rand(100) * 200,
                "low": prices - np.random.rand(100) * 200,
                "close": prices,
                "volume": np.random.randint(500000, 1500000, 100),
            },
            index=dates,
        )

        return df

    # === 趋势共振测试 ===

    def test_trend_alignment_bullish_pass(self, validator, bullish_trends):
        """测试：多个周期看涨时做多应该通过"""
        validation = validator.validate_decision(
            decision="BUY",
            symbol="BTC",
            current_price=50000.0,
            indicators={"current_price": 50000.0, "rsi": 50.0},
            multi_timeframe_trends=bullish_trends,
            take_profit_ratio=0.05,
            stop_loss_ratio=0.02,
            leverage=5,
        )

        # 找到趋势共振检查
        trend_check = next((c for c in validation.checks if c.name == "trend_alignment"), None)
        assert trend_check is not None
        assert trend_check.result == ValidationResult.PASS

    def test_trend_alignment_bearish_pass(self, validator, bearish_trends):
        """测试：多个周期看跌时做空应该通过"""
        validation = validator.validate_decision(
            decision="SELL_SHORT",
            symbol="BTC",
            current_price=50000.0,
            indicators={"current_price": 50000.0, "rsi": 50.0},
            multi_timeframe_trends=bearish_trends,
            take_profit_ratio=0.05,
            stop_loss_ratio=0.02,
            leverage=5,
        )

        trend_check = next((c for c in validation.checks if c.name == "trend_alignment"), None)
        assert trend_check is not None
        assert trend_check.result == ValidationResult.PASS

    def test_trend_alignment_mixed_block(self, validator, mixed_trends):
        """测试：趋势混乱时应该阻止或警告"""
        validation = validator.validate_decision(
            decision="BUY",
            symbol="BTC",
            current_price=50000.0,
            indicators={"current_price": 50000.0, "rsi": 50.0},
            multi_timeframe_trends=mixed_trends,
            take_profit_ratio=0.05,
            stop_loss_ratio=0.02,
            leverage=5,
        )

        trend_check = next((c for c in validation.checks if c.name == "trend_alignment"), None)
        assert trend_check is not None
        # 混合趋势应该被阻止或警告
        assert trend_check.result in [ValidationResult.BLOCK, ValidationResult.WARN]

    def test_trend_alignment_opposite_direction(self, validator, bearish_trends):
        """测试：趋势与方向相反时应该阻止"""
        validation = validator.validate_decision(
            decision="BUY",  # 做多
            symbol="BTC",
            current_price=50000.0,
            indicators={"current_price": 50000.0, "rsi": 50.0},
            multi_timeframe_trends=bearish_trends,  # 但趋势看跌
            take_profit_ratio=0.05,
            stop_loss_ratio=0.02,
            leverage=5,
        )

        trend_check = next((c for c in validation.checks if c.name == "trend_alignment"), None)
        assert trend_check is not None
        assert trend_check.result == ValidationResult.BLOCK

    # === 信号质量测试 ===

    def test_signal_quality_strong_bullish(self, validator, bullish_trends, sample_indicators):
        """测试：强信号应该通过"""
        # RSI 超卖，MACD 金叉 - 强烈看涨信号
        indicators = sample_indicators.copy()
        indicators["rsi"] = 25.0  # 超卖

        validation = validator.validate_decision(
            decision="BUY",
            symbol="BTC",
            current_price=50000.0,
            indicators=indicators,
            multi_timeframe_trends=bullish_trends,
            take_profit_ratio=0.05,
            stop_loss_ratio=0.02,
            leverage=5,
        )

        signal_check = next((c for c in validation.checks if c.name == "signal_quality"), None)
        assert signal_check is not None
        assert signal_check.score >= 0.6

    def test_signal_quality_weak(self, validator, bullish_trends, sample_indicators):
        """测试：弱信号应该得低分"""
        # RSI 超买时做多 - 不是好时机
        indicators = sample_indicators.copy()
        indicators["rsi"] = 75.0  # 超买
        indicators["macd"] = -100.0  # MACD 在信号线下方
        indicators["macd_signal"] = -80.0
        indicators["macd_hist"] = -20.0

        validation = validator.validate_decision(
            decision="BUY",
            symbol="BTC",
            current_price=50000.0,
            indicators=indicators,
            multi_timeframe_trends=bullish_trends,
            take_profit_ratio=0.05,
            stop_loss_ratio=0.02,
            leverage=5,
        )

        signal_check = next((c for c in validation.checks if c.name == "signal_quality"), None)
        assert signal_check is not None
        assert signal_check.score < 0.5

    # === 风险回报测试 ===

    def test_risk_reward_good_ratio(self, validator, bullish_trends, sample_indicators):
        """测试：好的风险回报比应该通过"""
        validation = validator.validate_decision(
            decision="BUY",
            symbol="BTC",
            current_price=50000.0,
            indicators=sample_indicators,
            multi_timeframe_trends=bullish_trends,
            take_profit_ratio=0.06,  # 6% 止盈
            stop_loss_ratio=0.02,  # 2% 止损 -> 3:1
            leverage=5,
        )

        rr_check = next((c for c in validation.checks if c.name == "risk_reward"), None)
        assert rr_check is not None
        assert rr_check.result == ValidationResult.PASS

    def test_risk_reward_bad_ratio(self, validator, bullish_trends, sample_indicators):
        """测试：差的风险回报比应该阻止"""
        validation = validator.validate_decision(
            decision="BUY",
            symbol="BTC",
            current_price=50000.0,
            indicators=sample_indicators,
            multi_timeframe_trends=bullish_trends,
            take_profit_ratio=0.02,  # 2% 止盈
            stop_loss_ratio=0.03,  # 3% 止损 -> 0.67:1 太差
            leverage=5,
        )

        rr_check = next((c for c in validation.checks if c.name == "risk_reward"), None)
        assert rr_check is not None
        assert rr_check.result == ValidationResult.BLOCK

    def test_risk_reward_with_leverage(self, validator, bullish_trends, sample_indicators):
        """测试：高杠杆时需要更高的风险回报比"""
        # 相同的止盈止损比例，高杠杆应该有更严格的要求
        validation_low_lev = validator.validate_decision(
            decision="BUY",
            symbol="BTC",
            current_price=50000.0,
            indicators=sample_indicators,
            multi_timeframe_trends=bullish_trends,
            take_profit_ratio=0.03,
            stop_loss_ratio=0.02,  # 1.5:1
            leverage=2,
        )

        validation_high_lev = validator.validate_decision(
            decision="BUY",
            symbol="BTC",
            current_price=50000.0,
            indicators=sample_indicators,
            multi_timeframe_trends=bullish_trends,
            take_profit_ratio=0.03,
            stop_loss_ratio=0.02,  # 1.5:1
            leverage=10,
        )

        rr_low = next((c for c in validation_low_lev.checks if c.name == "risk_reward"), None)
        rr_high = next((c for c in validation_high_lev.checks if c.name == "risk_reward"), None)

        # 高杠杆时得分应该更低
        assert rr_high.score <= rr_low.score

    # === 市场环境测试 ===

    def test_market_regime_normal(self, validator, bullish_trends, sample_indicators, sample_df):
        """测试：正常市场环境应该通过"""
        validation = validator.validate_decision(
            decision="BUY",
            symbol="BTC",
            current_price=50000.0,
            indicators=sample_indicators,
            multi_timeframe_trends=bullish_trends,
            take_profit_ratio=0.05,
            stop_loss_ratio=0.02,
            leverage=5,
            df=sample_df,
        )

        market_check = next((c for c in validation.checks if c.name == "market_regime"), None)
        assert market_check is not None
        # 正常市场环境应该通过或只是警告
        assert market_check.result in [ValidationResult.PASS, ValidationResult.WARN]

    def test_market_regime_high_volatility(self, validator, bullish_trends, sample_indicators):
        """测试：高波动率环境应该警告或阻止"""
        indicators = sample_indicators.copy()
        indicators["atr_14"] = 2000.0  # 高波动率 (4% of price)

        validation = validator.validate_decision(
            decision="BUY",
            symbol="BTC",
            current_price=50000.0,
            indicators=indicators,
            multi_timeframe_trends=bullish_trends,
            take_profit_ratio=0.05,
            stop_loss_ratio=0.02,
            leverage=5,
        )

        market_check = next((c for c in validation.checks if c.name == "market_regime"), None)
        assert market_check is not None
        # 高波动率环境应该有警告
        assert "波动率" in str(market_check.details.get("warnings", []))

    # === 入场时机测试 ===

    def test_entry_timing_good(self, validator, bullish_trends, sample_indicators, sample_df):
        """测试：好的入场时机"""
        # 价格接近区间底部
        indicators = sample_indicators.copy()
        indicators["current_price"] = 48500.0  # 接近低点
        indicators["ema_20"] = 49000.0  # 价格在均线下方

        validation = validator.validate_decision(
            decision="BUY",
            symbol="BTC",
            current_price=48500.0,
            indicators=indicators,
            multi_timeframe_trends=bullish_trends,
            take_profit_ratio=0.05,
            stop_loss_ratio=0.02,
            leverage=5,
            df=sample_df,
        )

        entry_check = next((c for c in validation.checks if c.name == "entry_timing"), None)
        assert entry_check is not None
        # 接近底部入场应该得高分
        assert entry_check.score >= 0.5

    def test_entry_timing_chasing(self, validator, bullish_trends, sample_indicators, sample_df):
        """测试：追高入场应该警告"""
        # 价格远高于均线
        indicators = sample_indicators.copy()
        indicators["current_price"] = 52000.0  # 价格过高
        indicators["ema_20"] = 49000.0  # 价格远高于均线

        validation = validator.validate_decision(
            decision="BUY",
            symbol="BTC",
            current_price=52000.0,
            indicators=indicators,
            multi_timeframe_trends=bullish_trends,
            take_profit_ratio=0.05,
            stop_loss_ratio=0.02,
            leverage=5,
            df=sample_df,
        )

        entry_check = next((c for c in validation.checks if c.name == "entry_timing"), None)
        assert entry_check is not None
        # 追高应该得低分
        assert entry_check.score < 0.7

    # === 综合测试 ===

    def test_do_nothing_always_pass(self, validator, mixed_trends, sample_indicators):
        """测试：DO_NOTHING 决策总是通过"""
        validation = validator.validate_decision(
            decision="DO_NOTHING",
            symbol="BTC",
            current_price=50000.0,
            indicators=sample_indicators,
            multi_timeframe_trends=mixed_trends,
            take_profit_ratio=0.05,
            stop_loss_ratio=0.02,
            leverage=5,
        )

        assert validation.is_valid
        assert validation.overall_score == 1.0
        assert len(validation.checks) == 0

    def test_overall_validation_pass(self, validator, bullish_trends, sample_indicators, sample_df):
        """测试：所有条件良好时应该通过"""
        # 设置良好的条件
        indicators = sample_indicators.copy()
        indicators["rsi"] = 35.0  # 接近超卖
        indicators["macd"] = 100.0
        indicators["macd_signal"] = 50.0
        indicators["macd_hist"] = 50.0
        indicators["bb_position"] = 0.3  # 接近下轨

        validation = validator.validate_decision(
            decision="BUY",
            symbol="BTC",
            current_price=50000.0,
            indicators=indicators,
            multi_timeframe_trends=bullish_trends,
            take_profit_ratio=0.05,
            stop_loss_ratio=0.02,
            leverage=5,
            df=sample_df,
        )

        # 应该通过验证
        assert validation.is_valid
        assert validation.overall_score >= 0.5
        assert len(validation.blockers) == 0

    def test_overall_validation_block(self, validator, bearish_trends, sample_indicators):
        """测试：条件不佳时应该阻止"""
        # 做多但趋势看跌
        validation = validator.validate_decision(
            decision="BUY",
            symbol="BTC",
            current_price=50000.0,
            indicators=sample_indicators,
            multi_timeframe_trends=bearish_trends,
            take_profit_ratio=0.02,  # 低止盈
            stop_loss_ratio=0.03,  # 高止损
            leverage=10,
        )

        # 应该被阻止
        assert not validation.is_valid
        assert validation.validated_decision == "DO_NOTHING"
        assert len(validation.blockers) > 0

    def test_size_multiplier_calculation(self, validator, bullish_trends, sample_indicators):
        """测试：仓位调整系数计算"""
        # 好条件 -> 高系数
        indicators_good = sample_indicators.copy()
        indicators_good["rsi"] = 30.0

        validation_good = validator.validate_decision(
            decision="BUY",
            symbol="BTC",
            current_price=50000.0,
            indicators=indicators_good,
            multi_timeframe_trends=bullish_trends,
            take_profit_ratio=0.06,
            stop_loss_ratio=0.02,
            leverage=3,
        )

        # 一般条件 -> 低系数
        indicators_fair = sample_indicators.copy()
        indicators_fair["rsi"] = 55.0

        mixed_trends = {"15分钟": "上涨", "1小时": "震荡", "4小时": "上涨", "日线": "震荡"}

        validation_fair = validator.validate_decision(
            decision="BUY",
            symbol="BTC",
            current_price=50000.0,
            indicators=indicators_fair,
            multi_timeframe_trends=mixed_trends,
            take_profit_ratio=0.04,
            stop_loss_ratio=0.02,
            leverage=5,
        )

        # 好条件的仓位系数应该更高
        assert (
            validation_good.suggested_size_multiplier >= validation_fair.suggested_size_multiplier
        )

    def test_validation_summary(self, validator, bullish_trends, sample_indicators):
        """测试：验证摘要生成"""
        validation = validator.validate_decision(
            decision="BUY",
            symbol="BTC",
            current_price=50000.0,
            indicators=sample_indicators,
            multi_timeframe_trends=bullish_trends,
            take_profit_ratio=0.05,
            stop_loss_ratio=0.02,
            leverage=5,
        )

        summary = validation.get_summary()
        assert isinstance(summary, str)
        assert len(summary) > 0
        # 摘要应该包含状态信息
        assert "通过" in summary or "阻止" in summary


class TestValidationCheck:
    """ValidationCheck 测试"""

    def test_validation_check_creation(self):
        """测试创建验证检查"""
        check = ValidationCheck(
            name="test_check",
            result=ValidationResult.PASS,
            score=0.8,
            message="测试通过",
            details={"key": "value"},
        )

        assert check.name == "test_check"
        assert check.result == ValidationResult.PASS
        assert check.score == 0.8
        assert check.message == "测试通过"
        assert check.details["key"] == "value"


class TestDecisionValidation:
    """DecisionValidation 测试"""

    def test_decision_validation_creation(self):
        """测试创建验证结果"""
        validation = DecisionValidation(
            is_valid=True,
            overall_score=0.75,
            decision="BUY",
            validated_decision="BUY",
            blockers=[],
            warnings=["警告1"],
            suggestions=["建议1"],
        )

        assert validation.is_valid
        assert validation.overall_score == 0.75
        assert validation.decision == "BUY"
        assert len(validation.warnings) == 1

    def test_get_summary_pass(self):
        """测试通过时的摘要"""
        validation = DecisionValidation(
            is_valid=True, overall_score=0.85, decision="BUY", validated_decision="BUY"
        )

        summary = validation.get_summary()
        assert "通过" in summary
        assert "0.85" in summary

    def test_get_summary_block(self):
        """测试阻止时的摘要"""
        validation = DecisionValidation(
            is_valid=False,
            overall_score=0.35,
            decision="BUY",
            validated_decision="DO_NOTHING",
            blockers=["趋势不一致"],
        )

        summary = validation.get_summary()
        assert "阻止" in summary
        assert "趋势不一致" in summary
