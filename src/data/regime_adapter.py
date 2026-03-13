"""
市场 Regime 自适应策略切换模块

基于 Regime Switching Forecasting (Springer Digital Finance 2025) 研究，
将 MarketState 映射为 3 种 Regime，并根据 Regime 动态调整交易参数。

Regime 定义：
- trending: 趋势明确，适合跟随入场
- ranging: 震荡无方向，应减少交易
- volatile: 高波动/反转，需极度谨慎
"""

import logging
from dataclasses import dataclass

from src.data.market_state import MarketState

logger = logging.getLogger(__name__)


# MarketState → Regime 映射
REGIME_MAP: dict[str, list[MarketState]] = {
    "trending": [
        MarketState.STRONG_UPTREND,
        MarketState.UPTREND,
        MarketState.STRONG_DOWNTREND,
        MarketState.DOWNTREND,
        MarketState.BREAKOUT_UP,
        MarketState.BREAKOUT_DOWN,
    ],
    "ranging": [
        MarketState.CONSOLIDATION,
        MarketState.WEAK_UPTREND,
        MarketState.WEAK_DOWNTREND,
        MarketState.UNKNOWN,
    ],
    "volatile": [
        MarketState.REVERSAL_BULLISH,
        MarketState.REVERSAL_BEARISH,
    ],
}

# 反向映射：MarketState → Regime 名称
_STATE_TO_REGIME: dict[MarketState, str] = {}
for regime, states in REGIME_MAP.items():
    for state in states:
        _STATE_TO_REGIME[state] = regime


@dataclass
class RegimeParams:
    """Regime 自适应参数"""

    regime: str  # "trending" | "ranging" | "volatile"
    signal_threshold: float  # 信号阈值
    min_confidence: float  # 最低置信度
    max_leverage: int  # 杠杆上限
    position_pct: float  # 仓位比例上限
    prompt_hint: str  # 注入 Prompt 的策略提示


# 默认参数矩阵
DEFAULT_REGIME_PARAMS: dict[str, RegimeParams] = {
    "trending": RegimeParams(
        regime="trending",
        signal_threshold=0.5,
        min_confidence=0.35,
        max_leverage=10,
        position_pct=0.8,
        prompt_hint="当前为趋势市，适合跟随趋势方向入场，可适当放大仓位",
    ),
    "ranging": RegimeParams(
        regime="ranging",
        signal_threshold=0.75,
        min_confidence=0.55,
        max_leverage=5,
        position_pct=0.4,
        prompt_hint="当前为震荡市，优先观望，仅在极强信号时入场，控制仓位和杠杆",
    ),
    "volatile": RegimeParams(
        regime="volatile",
        signal_threshold=0.85,
        min_confidence=0.65,
        max_leverage=3,
        position_pct=0.3,
        prompt_hint="当前为高波动/反转市，极度谨慎，宁可错过不可做错，小仓位低杠杆",
    ),
}


def classify_regime(market_state: MarketState) -> str:
    """
    将 MarketState 映射为 Regime 类别

    Args:
        market_state: 市场状态枚举

    Returns:
        Regime 名称: "trending" | "ranging" | "volatile"
    """
    return _STATE_TO_REGIME.get(market_state, "ranging")


def get_regime_params(
    market_state: MarketState,
    config_overrides: dict | None = None,
) -> RegimeParams:
    """
    根据市场状态获取自适应参数

    Args:
        market_state: 市场状态
        config_overrides: 配置覆盖（来自 config.yaml 的 regime_adaptive 节）

    Returns:
        RegimeParams 自适应参数
    """
    regime = classify_regime(market_state)
    params = DEFAULT_REGIME_PARAMS[regime]

    # 如果有配置覆盖，应用自定义参数
    if config_overrides:
        regime_config = config_overrides.get(regime, {})
        if regime_config:
            params = RegimeParams(
                regime=regime,
                signal_threshold=regime_config.get("signal_threshold", params.signal_threshold),
                min_confidence=regime_config.get("min_confidence", params.min_confidence),
                max_leverage=regime_config.get("max_leverage", params.max_leverage),
                position_pct=regime_config.get("position_pct", params.position_pct),
                prompt_hint=regime_config.get("prompt_hint", params.prompt_hint),
            )

    return params


def format_regime_hint(market_state: MarketState, params: RegimeParams) -> str:
    """
    格式化 Regime 提示文本，用于注入 Prompt

    Args:
        market_state: 市场状态
        params: Regime 参数

    Returns:
        格式化的 Regime 提示文本
    """
    regime_zh = {"trending": "趋势市", "ranging": "震荡市", "volatile": "高波动市"}
    regime_name = regime_zh.get(params.regime, params.regime)

    return f"""## 📡 市场 Regime 识别

**当前状态**: {market_state.value} → **{regime_name}**
**策略建议**: {params.prompt_hint}
**参数约束**: 信号阈值≥{params.signal_threshold}, 置信度≥{params.min_confidence}, 杠杆≤{params.max_leverage}x, 仓位≤{params.position_pct:.0%}

> ⚠️ 以上参数约束由 Regime 自适应系统自动设定，你的决策必须在此范围内。"""
