"""
增强型交易系统单元测试
测试市场状态分析、风险管理、信号评分和增强引擎
"""

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from src.data.market_state import (
    MarketState,
    MarketStateAnalyzer,
    MomentumAnalysis,
    SupportResistance,
    TrendAnalysis,
    VolatilityAnalysis,
    VolumeAnalysis,
    format_analysis_for_prompt,
)
from src.data.signal_scorer import (
    SignalQuality,
    SignalScorer,
    SignalType,
    TradingSignal,
    format_signal_for_prompt,
)
from src.trading.enhanced_engine import (
    EnhancedDecision,
    EnhancedTradingEngine,
    create_enhanced_engine_from_config,
)
from src.trading.risk_manager import (
    PositionSizeResult,
    RiskAssessment,
    RiskLevel,
    RiskManager,
    RiskParameters,
    StopLossResult,
    TakeProfitResult,
    format_risk_assessment_for_prompt,
)


def create_sample_df(periods: int = 100, trend: str = "up") -> pd.DataFrame:
    """创建模拟的OHLCV DataFrame"""
    dates = pd.date_range(end=datetime.now(), periods=periods, freq="15min")

    # 基础价格
    base_price = 50000.0

    if trend == "up":
        prices = base_price + np.linspace(0, 2000, periods) + np.random.randn(periods) * 100
    elif trend == "down":
        prices = base_price - np.linspace(0, 2000, periods) + np.random.randn(periods) * 100
    else:  # sideways
        prices = base_price + np.random.randn(periods) * 200

    df = pd.DataFrame(
        {
            "timestamp": dates,
            "open": prices - np.random.rand(periods) * 50,
            "high": prices + np.random.rand(periods) * 100,
            "low": prices - np.random.rand(periods) * 100,
            "close": prices,
            "volume": np.random.rand(periods) * 1000000 + 500000,
        }
    )

    # 添加技术指标
    df["ma_7"] = df["close"].rolling(7).mean()
    df["ma_25"] = df["close"].rolling(25).mean()
    df["ma_99"] = df["close"].rolling(99).mean()

    # RSI
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    df["rsi"] = 100 - (100 / (1 + rs))

    # ATR
    high_low = df["high"] - df["low"]
    high_close = abs(df["high"] - df["close"].shift())
    low_close = abs(df["low"] - df["close"].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["atr_14"] = tr.rolling(14).mean()

    # MACD
    exp12 = df["close"].ewm(span=12).mean()
    exp26 = df["close"].ewm(span=26).mean()
    df["macd"] = exp12 - exp26
    df["macd_signal"] = df["macd"].ewm(span=9).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    # Bollinger Bands
    df["bb_middle"] = df["close"].rolling(20).mean()
    bb_std = df["close"].rolling(20).std()
    df["bb_upper"] = df["bb_middle"] + 2 * bb_std
    df["bb_lower"] = df["bb_middle"] - 2 * bb_std

    return df.dropna()


class TestMarketStateAnalyzer:
    """市场状态分析器测试"""

    def test_analyzer_initialization(self):
        """测试分析器初始化"""
        analyzer = MarketStateAnalyzer()
        assert analyzer.atr_period == 14
        assert analyzer.rsi_period == 14
        assert analyzer.ma_periods == [7, 25, 99]

    def test_analyze_uptrend(self):
        """测试上涨趋势分析"""
        analyzer = MarketStateAnalyzer()
        df = create_sample_df(100, trend="up")
        current_price = df["close"].iloc[-1]

        result = analyzer.analyze(df, current_price)

        assert result is not None
        assert isinstance(result.state, MarketState)
        assert isinstance(result.trend, TrendAnalysis)
        assert isinstance(result.volatility, VolatilityAnalysis)
        assert isinstance(result.momentum, MomentumAnalysis)
        assert isinstance(result.volume, VolumeAnalysis)
        assert isinstance(result.support_resistance, SupportResistance)

    def test_analyze_downtrend(self):
        """测试下跌趋势分析"""
        analyzer = MarketStateAnalyzer()
        df = create_sample_df(100, trend="down")
        current_price = df["close"].iloc[-1]

        result = analyzer.analyze(df, current_price)

        assert result is not None
        # 下跌趋势应该识别为bearish或下跌相关状态
        assert result.state in [
            MarketState.DOWNTREND,
            MarketState.STRONG_DOWNTREND,
            MarketState.WEAK_DOWNTREND,
            MarketState.CONSOLIDATION,
            MarketState.BREAKOUT_DOWN,
            MarketState.REVERSAL_BEARISH,
            MarketState.UNKNOWN,
        ]

    def test_multi_timeframe_alignment(self):
        """测试多周期一致性计算"""
        analyzer = MarketStateAnalyzer()
        df = create_sample_df(100, trend="up")
        current_price = df["close"].iloc[-1]

        mtf_trends = {"15m": "上涨趋势", "1h": "强势上涨", "4h": "上涨"}

        result = analyzer.analyze(df, current_price, mtf_trends)

        assert result.multi_timeframe_alignment >= 0
        assert result.multi_timeframe_alignment <= 1

    def test_format_analysis_for_prompt(self):
        """测试分析结果格式化"""
        analyzer = MarketStateAnalyzer()
        df = create_sample_df(100, trend="up")
        current_price = df["close"].iloc[-1]

        result = analyzer.analyze(df, current_price)
        formatted = format_analysis_for_prompt(result)

        assert isinstance(formatted, str)
        assert len(formatted) > 0
        assert "市场状态分析" in formatted


class TestRiskManager:
    """风险管理器测试"""

    def test_risk_manager_initialization(self):
        """测试风险管理器初始化"""
        rm = RiskManager()
        assert rm.params.max_risk_per_trade == 0.02
        assert rm.params.default_stop_loss_pct == 0.02

    def test_custom_risk_parameters(self):
        """测试自定义风险参数"""
        params = RiskParameters(
            max_risk_per_trade=0.03, default_stop_loss_pct=0.025, atr_stop_loss_multiplier=2.0
        )
        rm = RiskManager(risk_params=params)

        assert rm.params.max_risk_per_trade == 0.03
        assert rm.params.default_stop_loss_pct == 0.025
        assert rm.params.atr_stop_loss_multiplier == 2.0

    def test_stop_loss_rejects_nonpositive_entry_price(self):
        """入场价为 0/负（行情缺失）时止损计算应抛 ValueError 而非除零崩溃"""
        rm = RiskManager()
        for bad_price in (0.0, -100.0):
            with pytest.raises(ValueError):
                rm.calculate_dynamic_stop_loss(
                    entry_price=bad_price, is_long=True, current_atr=500.0
                )

    def test_take_profit_rejects_nonpositive_entry_price(self):
        """入场价为 0/负时止盈计算应抛 ValueError"""
        rm = RiskManager()
        with pytest.raises(ValueError):
            rm.calculate_dynamic_take_profit(
                entry_price=0.0, stop_loss_price=100.0, is_long=True, current_atr=10.0
            )

    def test_position_size_rejects_nonpositive_entry_price(self):
        """入场价为 0/负时仓位计算应抛 ValueError，避免 ZeroDivisionError"""
        rm = RiskManager()
        with pytest.raises(ValueError):
            rm.calculate_position_size(
                account_balance=10000.0, entry_price=0.0, stop_loss_price=0.0, leverage=5
            )

    def test_position_size_handles_zero_stop_distance(self):
        """入场价==止损价（止损距离为0）时不应除零崩溃，应回退最小风险距离"""
        rm = RiskManager()
        result = rm.calculate_position_size(
            account_balance=10000.0,
            entry_price=50000.0,
            stop_loss_price=50000.0,  # 与入场价相同 → risk_per_unit=0
            leverage=5,
        )
        assert isinstance(result, PositionSizeResult)
        assert result.position_size >= 0  # 不崩溃且产出有限仓位

    def test_calculate_dynamic_stop_loss_long(self):
        """测试多头动态止损计算"""
        rm = RiskManager()

        result = rm.calculate_dynamic_stop_loss(
            entry_price=50000.0, is_long=True, current_atr=500.0, volatility_state="normal"
        )

        assert isinstance(result, StopLossResult)
        assert result.stop_loss_price < 50000.0  # 多头止损在入场价下方
        assert result.stop_loss_pct > 0

    def test_calculate_dynamic_stop_loss_short(self):
        """测试空头动态止损计算"""
        rm = RiskManager()

        result = rm.calculate_dynamic_stop_loss(
            entry_price=50000.0, is_long=False, current_atr=500.0, volatility_state="normal"
        )

        assert isinstance(result, StopLossResult)
        assert result.stop_loss_price > 50000.0  # 空头止损在入场价上方

    def test_calculate_dynamic_take_profit(self):
        """测试动态止盈计算"""
        rm = RiskManager()

        sl_result = rm.calculate_dynamic_stop_loss(
            entry_price=50000.0, is_long=True, current_atr=500.0
        )

        tp_result = rm.calculate_dynamic_take_profit(
            entry_price=50000.0,
            stop_loss_price=sl_result.stop_loss_price,
            is_long=True,
            current_atr=500.0,
        )

        assert isinstance(tp_result, TakeProfitResult)
        assert tp_result.take_profit_price > 50000.0  # 多头止盈在入场价上方
        assert tp_result.risk_reward_ratio >= 1.0  # 风险回报比至少1:1

    def test_calculate_position_size(self):
        """测试仓位计算"""
        rm = RiskManager()

        result = rm.calculate_position_size(
            account_balance=10000.0,
            entry_price=50000.0,
            stop_loss_price=49000.0,
            leverage=3,
            volatility_state="normal",
        )

        assert isinstance(result, PositionSizeResult)
        assert result.position_size > 0
        assert result.position_size <= 10000.0  # 不超过账户余额
        assert result.position_pct <= 1.0

    def test_assess_risk(self):
        """测试风险评估"""
        rm = RiskManager()

        positions = [{"coin": "BTC", "szi": "0.1", "entryPx": "50000", "unrealizedPnl": "100"}]

        result = rm.assess_risk(
            account_balance=10000.0, current_positions=positions, market_volatility="normal"
        )

        assert isinstance(result, RiskAssessment)
        assert isinstance(result.risk_level, RiskLevel)
        assert 0 <= result.risk_score <= 100
        assert isinstance(result.can_trade, bool)

    def test_trailing_stop(self):
        """测试追踪止损"""
        rm = RiskManager()

        # 初始化追踪止损
        state = rm.initialize_trailing_stop(
            symbol="BTC", entry_price=50000.0, is_long=True, initial_stop=49000.0
        )

        assert state is not None
        assert state.current_stop_price == 49000.0
        assert not state.is_active

        # 更新追踪止损（价格上涨）
        updated = rm.update_trailing_stop("BTC", 51000.0, True)

        assert updated is not None
        # 如果价格超过激活价格，追踪止损应该激活

    def test_format_risk_assessment(self):
        """测试风险评估格式化"""
        rm = RiskManager()

        result = rm.assess_risk(
            account_balance=10000.0, current_positions=[], market_volatility="normal"
        )

        formatted = format_risk_assessment_for_prompt(result)

        assert isinstance(formatted, str)
        assert "风险评估" in formatted


class TestSignalScorer:
    """信号评分器测试"""

    def test_scorer_initialization(self):
        """测试评分器初始化"""
        scorer = SignalScorer()
        assert len(scorer.weights) == 6
        assert scorer.min_confirmations == 3

    def test_custom_weights(self):
        """测试自定义权重"""
        weights = {
            "trend": 0.30,
            "momentum": 0.25,
            "volume": 0.15,
            "volatility": 0.10,
            "price_action": 0.10,
            "multi_timeframe": 0.10,
        }
        scorer = SignalScorer(weights=weights)

        assert scorer.weights["trend"] == 0.30
        assert scorer.weights["momentum"] == 0.25

    def test_score_signal_bullish(self):
        """测试看涨信号评分"""
        scorer = SignalScorer()

        # 创建模拟的分析结果
        class MockTrend:
            direction = "bullish"
            strength = 0.7
            ma_alignment = "aligned_up"

        class MockMomentum:
            rsi_value = 55
            rsi_state = "neutral"
            macd_state = "bullish"
            macd_crossover = "golden_cross"
            rsi_divergence = None

        class MockVolume:
            volume_ratio = 1.5
            volume_trend = "increasing"
            volume_confirmation = True
            unusual_volume = False

        class MockVolatility:
            current_atr = 500
            atr_percentile = 50
            volatility_state = "normal"
            suggested_sl_multiplier = 1.5
            suggested_tp_multiplier = 3.0

        class MockSR:
            nearest_support = 49000
            nearest_resistance = 52000
            support_strength = 0.7
            resistance_strength = 0.5
            price_to_support_pct = 2.0
            price_to_resistance_pct = 4.0

        signal = scorer.score_signal(
            symbol="BTC",
            market_data={"current_price": 50000},
            trend_analysis=MockTrend(),
            momentum_analysis=MockMomentum(),
            volume_analysis=MockVolume(),
            volatility_analysis=MockVolatility(),
            support_resistance=MockSR(),
            multi_timeframe_trends={"15m": "上涨", "1h": "强势"},
        )

        assert isinstance(signal, TradingSignal)
        assert signal.symbol == "BTC"
        assert isinstance(signal.signal_type, SignalType)
        assert isinstance(signal.quality, SignalQuality)
        assert 0 <= signal.confidence <= 1

    def test_signal_quality_thresholds(self):
        """测试信号质量阈值"""
        scorer = SignalScorer()

        # 测试各质量级别的阈值
        assert scorer.quality_thresholds["excellent"] == 80
        assert scorer.quality_thresholds["good"] == 60
        assert scorer.quality_thresholds["fair"] == 40
        assert scorer.quality_thresholds["poor"] == 20

    def test_format_signal_for_prompt(self):
        """测试信号格式化"""
        scorer = SignalScorer()

        # 创建最小化的模拟分析结果
        class MockAnalysis:
            pass

        trend = MockAnalysis()
        trend.direction = "neutral"
        trend.strength = 0.5
        trend.ma_alignment = "mixed"

        momentum = MockAnalysis()
        momentum.rsi_value = 50
        momentum.rsi_state = "neutral"
        momentum.macd_state = "neutral"
        momentum.macd_crossover = None
        momentum.rsi_divergence = None

        volume = MockAnalysis()
        volume.volume_ratio = 1.0
        volume.volume_trend = "stable"
        volume.volume_confirmation = False
        volume.unusual_volume = False

        volatility = MockAnalysis()
        volatility.current_atr = 500
        volatility.atr_percentile = 50
        volatility.volatility_state = "normal"
        volatility.suggested_sl_multiplier = 1.5
        volatility.suggested_tp_multiplier = 3.0

        sr = MockAnalysis()
        sr.nearest_support = 49000
        sr.nearest_resistance = 51000
        sr.support_strength = 0.5
        sr.resistance_strength = 0.5
        sr.price_to_support_pct = 2.0
        sr.price_to_resistance_pct = 2.0

        signal = scorer.score_signal(
            symbol="BTC",
            market_data={"current_price": 50000},
            trend_analysis=trend,
            momentum_analysis=momentum,
            volume_analysis=volume,
            volatility_analysis=volatility,
            support_resistance=sr,
        )

        formatted = format_signal_for_prompt(signal)

        assert isinstance(formatted, str)
        assert "交易信号评分" in formatted


class TestEnhancedTradingEngine:
    """增强型交易引擎测试"""

    def test_engine_initialization(self):
        """测试引擎初始化"""
        engine = EnhancedTradingEngine()

        assert engine.min_confidence == 0.4
        assert engine.enable_risk_filter
        assert engine.enable_timing_filter

    def test_custom_engine_config(self):
        """测试自定义引擎配置"""
        params = RiskParameters(max_risk_per_trade=0.03)
        engine = EnhancedTradingEngine(
            risk_params=params,
            min_signal_quality=SignalQuality.GOOD,
            min_confidence=0.5,
            enable_risk_filter=False,
        )

        assert engine.min_signal_quality == SignalQuality.GOOD
        assert engine.min_confidence == 0.5
        assert not engine.enable_risk_filter

    def test_analyze_and_decide(self):
        """测试完整分析决策流程"""
        engine = EnhancedTradingEngine()
        df = create_sample_df(100, trend="up")
        current_price = df["close"].iloc[-1]

        decision = engine.analyze_and_decide(
            symbol="BTC",
            df=df,
            current_price=current_price,
            account_balance=10000.0,
            current_positions=[],
            leverage=3,
        )

        assert isinstance(decision, EnhancedDecision)
        assert decision.symbol == "BTC"
        assert decision.entry_price == current_price
        assert isinstance(decision.should_trade, bool)
        assert decision.action in ["buy", "sell", "sell_short", "buy_to_cover", "hold"]
        assert 0 <= decision.overall_confidence <= 1

    def test_decision_with_positions(self):
        """测试有持仓时的决策"""
        engine = EnhancedTradingEngine()
        df = create_sample_df(100, trend="down")
        current_price = df["close"].iloc[-1]

        positions = [{"coin": "BTC", "szi": "0.1", "entryPx": str(current_price + 500)}]

        decision = engine.analyze_and_decide(
            symbol="BTC",
            df=df,
            current_price=current_price,
            account_balance=10000.0,
            current_positions=positions,
            leverage=3,
        )

        assert isinstance(decision, EnhancedDecision)
        # 有多头持仓且下跌趋势，应该考虑平仓

    def test_get_analysis_summary(self):
        """测试获取分析摘要"""
        engine = EnhancedTradingEngine()
        df = create_sample_df(100, trend="up")
        current_price = df["close"].iloc[-1]

        decision = engine.analyze_and_decide(
            symbol="BTC",
            df=df,
            current_price=current_price,
            account_balance=10000.0,
            current_positions=[],
            leverage=3,
        )

        summary = engine.get_analysis_summary(decision)

        assert isinstance(summary, str)
        assert "BTC" in summary

    def test_decision_history(self):
        """测试决策历史记录"""
        engine = EnhancedTradingEngine()
        df = create_sample_df(100, trend="up")
        current_price = df["close"].iloc[-1]

        # 执行多次决策
        for _ in range(3):
            engine.analyze_and_decide(
                symbol="BTC",
                df=df,
                current_price=current_price,
                account_balance=10000.0,
                current_positions=[],
                leverage=3,
            )

        history = engine.get_decision_history(symbol="BTC")

        assert len(history) == 3

    def test_create_engine_from_config(self):
        """测试从配置创建引擎"""
        config = {
            "risk": {
                "max_risk_per_trade": 0.025,
                "stop_loss_ratio": 0.02,
                "take_profit_ratio": 0.06,
            },
            "filter": {
                "min_signal_quality": "good",
                "min_confidence": 0.5,
                "enable_risk_filter": True,
            },
        }

        engine = create_enhanced_engine_from_config(config)

        assert engine.min_signal_quality == SignalQuality.GOOD
        assert engine.min_confidence == 0.5

    def test_prompt_injection_generation(self):
        """测试Prompt注入文本生成"""
        engine = EnhancedTradingEngine()
        df = create_sample_df(100, trend="up")
        current_price = df["close"].iloc[-1]

        decision = engine.analyze_and_decide(
            symbol="BTC",
            df=df,
            current_price=current_price,
            account_balance=10000.0,
            current_positions=[],
            leverage=3,
        )

        assert decision.prompt_injection is not None
        assert len(decision.prompt_injection) > 0
        assert "智能分析系统评估" in decision.prompt_injection


class TestIntegration:
    """集成测试"""

    def test_full_analysis_pipeline(self):
        """测试完整分析流水线"""
        # 1. 创建数据
        df = create_sample_df(100, trend="up")
        current_price = df["close"].iloc[-1]

        # 2. 市场状态分析
        analyzer = MarketStateAnalyzer()
        market_analysis = analyzer.analyze(df, current_price, {"15m": "上涨", "1h": "强势"})

        # 3. 风险评估
        rm = RiskManager()
        risk_assessment = rm.assess_risk(
            account_balance=10000.0,
            current_positions=[],
            market_volatility=market_analysis.volatility.volatility_state,
        )

        # 4. 使用增强引擎
        engine = EnhancedTradingEngine()
        decision = engine.analyze_and_decide(
            symbol="BTC",
            df=df,
            current_price=current_price,
            account_balance=10000.0,
            current_positions=[],
            multi_timeframe_trends={"15m": "上涨", "1h": "强势"},
            leverage=3,
        )

        # 验证所有组件协同工作
        assert market_analysis is not None
        assert risk_assessment is not None
        assert decision is not None

        # 决策应该基于市场状态和风险评估
        assert decision.market_analysis is not None
        assert decision.risk_assessment is not None
        assert decision.trading_signal is not None

    def test_volatile_market_handling(self):
        """测试高波动市场处理"""
        # 创建高波动数据
        df = create_sample_df(100, trend="up")
        # 人工增加波动
        df["high"] = df["high"] * 1.05
        df["low"] = df["low"] * 0.95

        # 重新计算ATR
        high_low = df["high"] - df["low"]
        high_close = abs(df["high"] - df["close"].shift())
        low_close = abs(df["low"] - df["close"].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df["atr_14"] = tr.rolling(14).mean()

        current_price = df["close"].iloc[-1]

        engine = EnhancedTradingEngine()
        decision = engine.analyze_and_decide(
            symbol="BTC",
            df=df.dropna(),
            current_price=current_price,
            account_balance=10000.0,
            current_positions=[],
            leverage=3,
        )

        # 高波动时应该有警告或建议
        assert decision is not None
        # 检查是否正确识别了波动性


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
