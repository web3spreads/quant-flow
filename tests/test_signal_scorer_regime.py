"""
SignalScorer Regime 自适应权重 + 新数据源因子测试
测试 Regime 动态权重调整、CEX funding/Fear&Greed/Funding extreme 评分因子
"""

from unittest.mock import MagicMock

import pytest

from src.data.signal_scorer import SignalFactor, SignalScorer


class TestRegimeWeights:
    """Regime 自适应权重测试"""

    @pytest.fixture
    def scorer(self):
        return SignalScorer()

    def test_default_weights_unchanged(self, scorer):
        """测试默认权重不变（向后兼容）"""
        assert scorer.weights["trend"] == 0.25
        assert scorer.weights["momentum"] == 0.20
        assert scorer.weights["volume"] == 0.15
        assert scorer.weights["volatility"] == 0.10
        assert scorer.weights["price_action"] == 0.15
        assert scorer.weights["multi_timeframe"] == 0.15

    def test_regime_weights_exist(self):
        """测试 REGIME_WEIGHTS 字典存在且包含 3 种 regime"""
        assert hasattr(SignalScorer, "REGIME_WEIGHTS")
        for regime in ("trending", "ranging", "volatile"):
            assert regime in SignalScorer.REGIME_WEIGHTS

    def test_regime_weights_sum_to_one(self):
        """测试每种 regime 的权重总和为 1"""
        for regime, weights in SignalScorer.REGIME_WEIGHTS.items():
            total = sum(weights.values())
            assert abs(total - 1.0) < 1e-6, f"{regime} 权重总和为 {total}，应为 1.0"

    def test_trending_emphasizes_trend_momentum(self):
        """测试趋势市上调 trend 和 momentum"""
        trending = SignalScorer.REGIME_WEIGHTS["trending"]
        default = SignalScorer.DEFAULT_WEIGHTS
        assert trending["trend"] > default["trend"]
        assert trending["momentum"] > default["momentum"]

    def test_ranging_emphasizes_volume_price_action(self):
        """测试震荡市上调 volume 和 price_action"""
        ranging = SignalScorer.REGIME_WEIGHTS["ranging"]
        default = SignalScorer.DEFAULT_WEIGHTS
        assert ranging["volume"] > default["volume"]
        assert ranging["price_action"] > default["price_action"]

    def test_volatile_emphasizes_volatility(self):
        """测试高波动市上调 volatility"""
        volatile = SignalScorer.REGIME_WEIGHTS["volatile"]
        default = SignalScorer.DEFAULT_WEIGHTS
        assert volatile["volatility"] > default["volatility"]

    def test_get_weights_for_regime_returns_correct(self, scorer):
        """测试 get_weights_for_regime 方法"""
        weights = scorer.get_weights_for_regime("trending")
        assert weights == SignalScorer.REGIME_WEIGHTS["trending"]

    def test_get_weights_for_regime_none_returns_default(self, scorer):
        """测试 regime=None 时返回默认权重"""
        weights = scorer.get_weights_for_regime(None)
        assert weights == scorer.weights

    def test_get_weights_for_regime_unknown_returns_default(self, scorer):
        """测试未知 regime 返回默认权重"""
        weights = scorer.get_weights_for_regime("unknown_regime")
        assert weights == scorer.weights


class TestCexFundingFactor:
    """CEX Funding 评分因子测试"""

    @pytest.fixture
    def scorer(self):
        return SignalScorer()

    def test_cex_leading_bullish(self, scorer):
        """测试 CEX 领先看多信号"""
        enriched = {"cex_funding_signal_type": "cex_leading_bullish"}
        factor = scorer._score_cex_funding(enriched)

        assert isinstance(factor, SignalFactor)
        assert factor.name == "cex_funding"
        assert factor.score > 0
        assert factor.weight == pytest.approx(0.1, abs=0.02)

    def test_cex_leading_bearish(self, scorer):
        """测试 CEX 领先看空信号"""
        enriched = {"cex_funding_signal_type": "cex_leading_bearish"}
        factor = scorer._score_cex_funding(enriched)

        assert factor.score < 0

    def test_cex_neutral(self, scorer):
        """测试 CEX 中性信号"""
        enriched = {"cex_funding_signal_type": "neutral"}
        factor = scorer._score_cex_funding(enriched)

        assert factor.score == 0.0

    def test_cex_unknown(self, scorer):
        """测试 CEX 未知信号"""
        enriched = {"cex_funding_signal_type": "unknown"}
        factor = scorer._score_cex_funding(enriched)

        assert factor.score == 0.0

    def test_cex_missing_key(self, scorer):
        """测试缺少 key 时不报错"""
        factor = scorer._score_cex_funding({})
        assert factor.score == 0.0

    def test_cex_none_enriched(self, scorer):
        """测试 enriched_data 为 None"""
        factor = scorer._score_cex_funding(None)
        assert factor.score == 0.0


class TestFearGreedFactor:
    """恐惧贪婪指数评分因子测试"""

    @pytest.fixture
    def scorer(self):
        return SignalScorer()

    def test_bullish_contrarian(self, scorer):
        """测试极度恐惧 → 逆向看多"""
        enriched = {"fear_greed_signal_bias": "bullish_contrarian"}
        factor = scorer._score_fear_greed(enriched)

        assert factor.score > 0
        assert factor.name == "fear_greed"

    def test_bearish_contrarian(self, scorer):
        """测试极度贪婪 → 逆向看空"""
        enriched = {"fear_greed_signal_bias": "bearish_contrarian"}
        factor = scorer._score_fear_greed(enriched)

        assert factor.score < 0

    def test_mild_bullish(self, scorer):
        """测试温和看多"""
        enriched = {"fear_greed_signal_bias": "mild_bullish"}
        factor = scorer._score_fear_greed(enriched)

        assert factor.score > 0
        assert factor.score < 0.5  # 弱于 contrarian

    def test_mild_bearish(self, scorer):
        """测试温和看空"""
        enriched = {"fear_greed_signal_bias": "mild_bearish"}
        factor = scorer._score_fear_greed(enriched)

        assert factor.score < 0
        assert factor.score > -0.5

    def test_neutral(self, scorer):
        """测试中性"""
        enriched = {"fear_greed_signal_bias": "neutral"}
        factor = scorer._score_fear_greed(enriched)

        assert factor.score == 0.0

    def test_missing_key(self, scorer):
        """测试缺少 key"""
        factor = scorer._score_fear_greed({})
        assert factor.score == 0.0


class TestFundingExtremeFactor:
    """资金费率极值评分因子测试"""

    @pytest.fixture
    def scorer(self):
        return SignalScorer()

    def test_bullish_contrarian(self, scorer):
        """测试极端负费率 → 逆向看多"""
        enriched = {"funding_rate_signal_strength": "bullish_contrarian"}
        factor = scorer._score_funding_extreme(enriched)

        assert factor.score > 0
        assert factor.name == "funding_extreme"

    def test_bearish_contrarian(self, scorer):
        """测试极端正费率 → 逆向看空"""
        enriched = {"funding_rate_signal_strength": "bearish_contrarian"}
        factor = scorer._score_funding_extreme(enriched)

        assert factor.score < 0

    def test_mild_bullish(self, scorer):
        """测试温和看多"""
        enriched = {"funding_rate_signal_strength": "mild_bullish"}
        factor = scorer._score_funding_extreme(enriched)

        assert 0 < factor.score < 0.5

    def test_mild_bearish(self, scorer):
        """测试温和看空"""
        enriched = {"funding_rate_signal_strength": "mild_bearish"}
        factor = scorer._score_funding_extreme(enriched)

        assert -0.5 < factor.score < 0

    def test_neutral(self, scorer):
        """测试中性"""
        enriched = {"funding_rate_signal_strength": "neutral"}
        factor = scorer._score_funding_extreme(enriched)

        assert factor.score == 0.0


class TestScoreSignalWithEnrichedData:
    """score_signal 集成新因子测试"""

    @pytest.fixture
    def scorer(self):
        return SignalScorer()

    @pytest.fixture
    def mock_analyses(self):
        """模拟分析结果"""
        trend = MagicMock()
        trend.strength = 0.6
        trend.direction = "up"
        trend.ma_alignment = "bullish"

        momentum = MagicMock()
        momentum.rsi_value = 55
        momentum.rsi_state = "neutral"
        momentum.rsi_divergence = None
        momentum.macd_state = "bullish"
        momentum.macd_cross = None

        volume = MagicMock()
        volume.volume_ratio = 1.1
        volume.volume_trend = "increasing"
        volume.is_abnormal = False

        volatility = MagicMock()
        volatility.atr_percentile = 50
        volatility.volatility_state = "normal"
        volatility.atr = 100
        volatility.current_atr = 100
        volatility.suggested_sl_multiplier = 1.5
        volatility.suggested_tp_multiplier = 3.0

        sr = MagicMock()
        sr.nearest_support = 49000
        sr.nearest_resistance = 52000
        sr.support_levels = [49000, 48000]
        sr.resistance_levels = [52000, 53000]
        sr.price_to_support_pct = 2.0
        sr.price_to_resistance_pct = 4.0
        sr.space_ratio = 0.5

        return trend, momentum, volume, volatility, sr

    def test_score_signal_without_enriched_unchanged(self, scorer, mock_analyses):
        """测试不传 enriched_data 时行为不变"""
        trend, momentum, volume, volatility, sr = mock_analyses
        market_data = {"current_price": 50000.0}

        # 不传 enriched_data 不应报错
        result = scorer.score_signal("BTC", market_data, trend, momentum, volume, volatility, sr)
        assert result is not None
        # 原有 6 个因子
        base_factor_names = {f.name for f in result.factors}
        assert "trend" in base_factor_names
        assert "momentum" in base_factor_names

    def test_score_signal_with_enriched_adds_factors(self, scorer, mock_analyses):
        """测试传入 enriched_data 时添加新因子"""
        trend, momentum, volume, volatility, sr = mock_analyses
        market_data = {"current_price": 50000.0}

        enriched = {
            "cex_funding_signal_type": "cex_leading_bullish",
            "fear_greed_signal_bias": "bullish_contrarian",
            "funding_rate_signal_strength": "neutral",
        }

        result = scorer.score_signal(
            "BTC",
            market_data,
            trend,
            momentum,
            volume,
            volatility,
            sr,
            enriched_data=enriched,
        )

        factor_names = {f.name for f in result.factors}
        assert "cex_funding" in factor_names
        assert "fear_greed" in factor_names
        assert "funding_extreme" in factor_names

    def test_score_signal_with_regime(self, scorer, mock_analyses):
        """测试传入 regime 时使用对应权重"""
        trend, momentum, volume, volatility, sr = mock_analyses
        market_data = {"current_price": 50000.0}

        result = scorer.score_signal(
            "BTC",
            market_data,
            trend,
            momentum,
            volume,
            volatility,
            sr,
            regime="trending",
        )

        # 趋势市下，trend 因子权重应更高
        trend_factor = next(f for f in result.factors if f.name == "trend")
        assert trend_factor.weight == SignalScorer.REGIME_WEIGHTS["trending"]["trend"]

    def test_enriched_factors_are_additive(self, scorer, mock_analyses):
        """测试新因子为加分项"""
        trend, momentum, volume, volatility, sr = mock_analyses
        market_data = {"current_price": 50000.0}

        # 无 enriched 的基线分
        result_base = scorer.score_signal(
            "BTC", market_data, trend, momentum, volume, volatility, sr
        )

        # 有 enriched 且全部看多的分
        enriched = {
            "cex_funding_signal_type": "cex_leading_bullish",
            "fear_greed_signal_bias": "bullish_contrarian",
            "funding_rate_signal_strength": "bullish_contrarian",
        }
        result_enriched = scorer.score_signal(
            "BTC",
            market_data,
            trend,
            momentum,
            volume,
            volatility,
            sr,
            enriched_data=enriched,
        )

        # 有看多 enriched 时，raw_score 应更高
        assert result_enriched.raw_score >= result_base.raw_score
