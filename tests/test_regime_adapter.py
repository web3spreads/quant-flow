"""
市场 Regime 自适应模块测试
测试 MarketState → Regime 映射、参数查表和 Prompt 格式化
"""

from src.data.market_state import MarketState
from src.data.regime_adapter import (
    DEFAULT_REGIME_PARAMS,
    REGIME_MAP,
    classify_regime,
    format_regime_hint,
    get_regime_params,
)


class TestRegimeMapping:
    """Regime 映射测试"""

    def test_all_market_states_have_regime(self):
        """测试所有 MarketState 都能映射到 Regime"""
        for state in MarketState:
            regime = classify_regime(state)
            assert regime in ("trending", "ranging", "volatile"), (
                f"MarketState.{state.name} 映射到未知 Regime: {regime}"
            )

    def test_trending_states(self):
        """测试趋势型状态映射"""
        trending_states = [
            MarketState.STRONG_UPTREND,
            MarketState.UPTREND,
            MarketState.STRONG_DOWNTREND,
            MarketState.DOWNTREND,
            MarketState.BREAKOUT_UP,
            MarketState.BREAKOUT_DOWN,
        ]
        for state in trending_states:
            assert classify_regime(state) == "trending", (
                f"MarketState.{state.name} 应映射为 trending"
            )

    def test_ranging_states(self):
        """测试震荡型状态映射"""
        ranging_states = [
            MarketState.CONSOLIDATION,
            MarketState.WEAK_UPTREND,
            MarketState.WEAK_DOWNTREND,
            MarketState.UNKNOWN,
        ]
        for state in ranging_states:
            assert classify_regime(state) == "ranging", f"MarketState.{state.name} 应映射为 ranging"

    def test_volatile_states(self):
        """测试高波动型状态映射"""
        volatile_states = [
            MarketState.REVERSAL_BULLISH,
            MarketState.REVERSAL_BEARISH,
        ]
        for state in volatile_states:
            assert classify_regime(state) == "volatile", (
                f"MarketState.{state.name} 应映射为 volatile"
            )

    def test_regime_map_covers_all_states(self):
        """测试 REGIME_MAP 包含所有 MarketState"""
        mapped_states = set()
        for states in REGIME_MAP.values():
            mapped_states.update(states)

        for state in MarketState:
            assert state in mapped_states, f"MarketState.{state.name} 未包含在 REGIME_MAP 中"


class TestRegimeParams:
    """Regime 参数测试"""

    def test_default_params_exist_for_all_regimes(self):
        """测试所有 Regime 都有默认参数"""
        for regime in ("trending", "ranging", "volatile"):
            assert regime in DEFAULT_REGIME_PARAMS

    def test_trending_params_are_most_permissive(self):
        """测试趋势市参数最宽松"""
        trending = DEFAULT_REGIME_PARAMS["trending"]
        ranging = DEFAULT_REGIME_PARAMS["ranging"]
        volatile = DEFAULT_REGIME_PARAMS["volatile"]

        assert trending.signal_threshold <= ranging.signal_threshold
        assert trending.signal_threshold <= volatile.signal_threshold
        assert trending.max_leverage >= ranging.max_leverage
        assert trending.max_leverage >= volatile.max_leverage
        assert trending.position_pct >= ranging.position_pct
        assert trending.position_pct >= volatile.position_pct

    def test_volatile_params_are_most_restrictive(self):
        """测试高波动市参数最严格"""
        volatile = DEFAULT_REGIME_PARAMS["volatile"]

        assert volatile.signal_threshold >= 0.8
        assert volatile.max_leverage <= 5
        assert volatile.position_pct <= 0.4

    def test_params_have_prompt_hint(self):
        """测试所有参数都有 Prompt 提示"""
        for regime, params in DEFAULT_REGIME_PARAMS.items():
            assert params.prompt_hint, f"{regime} 缺少 prompt_hint"
            assert len(params.prompt_hint) > 10

    def test_get_regime_params_default(self):
        """测试使用默认参数获取"""
        params = get_regime_params(MarketState.UPTREND)

        assert params.regime == "trending"
        assert params.signal_threshold == DEFAULT_REGIME_PARAMS["trending"].signal_threshold

    def test_get_regime_params_with_config_overrides(self):
        """测试配置覆盖参数"""
        overrides = {
            "trending": {
                "signal_threshold": 0.4,
                "max_leverage": 15,
            }
        }

        params = get_regime_params(MarketState.UPTREND, config_overrides=overrides)

        assert params.signal_threshold == 0.4
        assert params.max_leverage == 15
        # 未覆盖的字段保持默认值
        assert params.min_confidence == DEFAULT_REGIME_PARAMS["trending"].min_confidence

    def test_get_regime_params_override_only_affects_matching_regime(self):
        """测试覆盖仅影响匹配的 Regime"""
        overrides = {
            "trending": {"max_leverage": 20},
        }

        # ranging 状态不受 trending 覆盖影响
        params = get_regime_params(MarketState.CONSOLIDATION, config_overrides=overrides)

        assert params.regime == "ranging"
        assert params.max_leverage == DEFAULT_REGIME_PARAMS["ranging"].max_leverage

    def test_get_regime_params_empty_overrides(self):
        """测试空覆盖"""
        params = get_regime_params(MarketState.DOWNTREND, config_overrides={})

        assert params.regime == "trending"
        assert params.signal_threshold == DEFAULT_REGIME_PARAMS["trending"].signal_threshold


class TestFormatRegimeHint:
    """Regime 提示格式化测试"""

    def test_hint_contains_state_info(self):
        """测试提示包含状态信息"""
        params = get_regime_params(MarketState.CONSOLIDATION)
        hint = format_regime_hint(MarketState.CONSOLIDATION, params)

        assert "consolidation" in hint
        assert "震荡市" in hint

    def test_hint_contains_params(self):
        """测试提示包含参数约束"""
        params = get_regime_params(MarketState.REVERSAL_BEARISH)
        hint = format_regime_hint(MarketState.REVERSAL_BEARISH, params)

        assert str(params.signal_threshold) in hint
        assert str(params.max_leverage) in hint

    def test_hint_contains_strategy_advice(self):
        """测试提示包含策略建议"""
        params = get_regime_params(MarketState.STRONG_UPTREND)
        hint = format_regime_hint(MarketState.STRONG_UPTREND, params)

        assert params.prompt_hint in hint

    def test_hint_contains_warning(self):
        """测试提示包含约束警告"""
        params = get_regime_params(MarketState.UNKNOWN)
        hint = format_regime_hint(MarketState.UNKNOWN, params)

        assert "必须在此范围内" in hint

    def test_hint_regime_names_in_chinese(self):
        """测试 Regime 名称为中文"""
        for state, expected in [
            (MarketState.UPTREND, "趋势市"),
            (MarketState.CONSOLIDATION, "震荡市"),
            (MarketState.REVERSAL_BULLISH, "高波动市"),
        ]:
            params = get_regime_params(state)
            hint = format_regime_hint(state, params)
            assert expected in hint

    def test_hint_is_markdown_formatted(self):
        """测试提示为 Markdown 格式"""
        params = get_regime_params(MarketState.UPTREND)
        hint = format_regime_hint(MarketState.UPTREND, params)

        assert "##" in hint
        assert "**" in hint
