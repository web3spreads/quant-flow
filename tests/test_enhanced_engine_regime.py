"""
EnhancedTradingEngine Regime 自适应集成测试
测试 regime_config 参数注入和 _apply_filters 动态覆盖
"""

from unittest.mock import MagicMock

import pytest

from src.data.signal_scorer import SignalQuality, SignalType
from src.trading.enhanced_engine import EnhancedTradingEngine


class TestRegimeConfigInit:
    """Regime 配置初始化测试"""

    def test_default_no_regime(self):
        """测试默认不启用 regime"""
        engine = EnhancedTradingEngine()
        assert engine.enable_regime_adaptive is False
        assert engine.regime_config == {}

    def test_regime_enabled(self):
        """测试启用 regime"""
        config = {"enabled": True, "params": {"trending": {"max_leverage": 15}}}
        engine = EnhancedTradingEngine(regime_config=config)
        assert engine.enable_regime_adaptive is True
        assert engine.regime_config == config

    def test_regime_disabled_explicit(self):
        """测试显式禁用 regime"""
        config = {"enabled": False}
        engine = EnhancedTradingEngine(regime_config=config)
        assert engine.enable_regime_adaptive is False

    def test_regime_none_config(self):
        """测试 None 配置"""
        engine = EnhancedTradingEngine(regime_config=None)
        assert engine.enable_regime_adaptive is False
        assert engine.regime_config == {}


class TestApplyFiltersWithRegime:
    """_apply_filters 使用 Regime 参数测试"""

    @pytest.fixture
    def engine(self):
        return EnhancedTradingEngine(min_confidence=0.4)

    @pytest.fixture
    def mock_signal_low_conf(self):
        """模拟低置信度信号"""
        signal = MagicMock()
        signal.confidence = 0.45  # 高于默认 0.4，但可能低于 regime 的 min_confidence
        signal.quality = SignalQuality.GOOD
        signal.signal_type = SignalType.LONG_ENTRY
        signal.timing.is_optimal = True
        signal.timing.timing_score = 0.8
        signal.validation.is_valid = True
        signal.raw_score = 30
        signal.normalized_score = 65.0  # (30+100)/2 = 65，模拟中等信号
        return signal

    @pytest.fixture
    def mock_risk(self):
        risk = MagicMock()
        risk.can_trade = True
        risk.risk_level.value = 3
        risk.risk_score = 30
        return risk

    @pytest.fixture
    def mock_market(self):
        from src.data.market_state import MarketState

        market = MagicMock()
        market.state = MarketState.CONSOLIDATION
        market.multi_timeframe_alignment = 0.5
        return market

    def test_filters_pass_without_regime(
        self, engine, mock_signal_low_conf, mock_risk, mock_market
    ):
        """测试无 regime 时，0.45 置信度通过默认 0.4 阈值"""
        should_trade, action, blockers = engine._apply_filters(
            mock_signal_low_conf, mock_risk, mock_market, None
        )
        # 0.45 > 0.4 默认阈值，应通过
        confidence_blockers = [b for b in blockers if "置信度" in b]
        assert len(confidence_blockers) == 0

    def test_filters_block_with_regime_higher_confidence(
        self, engine, mock_signal_low_conf, mock_risk, mock_market
    ):
        """测试 regime 设置更高置信度要求时，0.45 被阻止"""
        from src.data.regime_adapter import RegimeParams

        regime_params = RegimeParams(
            regime="ranging",
            signal_threshold=0.75,
            min_confidence=0.55,  # 高于信号的 0.45
            max_leverage=5,
            position_pct=0.4,
            prompt_hint="震荡市",
        )
        should_trade, action, blockers = engine._apply_filters(
            mock_signal_low_conf,
            mock_risk,
            mock_market,
            None,
            regime_params=regime_params,
        )
        confidence_blockers = [b for b in blockers if "置信度" in b]
        assert len(confidence_blockers) > 0

    def test_filters_pass_with_trending_regime(
        self, engine, mock_signal_low_conf, mock_risk, mock_market
    ):
        """测试趋势市 regime 放宽置信度要求"""
        from src.data.regime_adapter import RegimeParams

        regime_params = RegimeParams(
            regime="trending",
            signal_threshold=0.5,
            min_confidence=0.35,  # 低于信号的 0.45
            max_leverage=10,
            position_pct=0.8,
            prompt_hint="趋势市",
        )
        should_trade, action, blockers = engine._apply_filters(
            mock_signal_low_conf,
            mock_risk,
            mock_market,
            None,
            regime_params=regime_params,
        )
        confidence_blockers = [b for b in blockers if "置信度" in b]
        assert len(confidence_blockers) == 0


class TestCreateFromConfig:
    """工厂函数配置测试"""

    def test_factory_reads_regime_config(self):
        """测试工厂函数读取 regime_adaptive 配置"""
        from src.trading.enhanced_engine import create_enhanced_engine_from_config

        config = {
            "regime_adaptive": {
                "enabled": True,
                "params": {
                    "trending": {"max_leverage": 15},
                },
            },
        }
        engine = create_enhanced_engine_from_config(config)
        assert engine.enable_regime_adaptive is True

    def test_factory_without_regime_config(self):
        """测试工厂函数无 regime 配置"""
        from src.trading.enhanced_engine import create_enhanced_engine_from_config

        engine = create_enhanced_engine_from_config({})
        assert engine.enable_regime_adaptive is False
