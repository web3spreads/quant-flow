"""
市场状态分析模块
提供全面的市场状态识别、趋势判断和交易信号生成

核心功能：
1. 市场状态分类（趋势、震荡、突破、反转）
2. 多维度信号评分系统
3. 波动性自适应参数调整
4. 风险评估和仓位建议
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import pandas as pd


class MarketState(Enum):
    """市场状态枚举"""
    STRONG_UPTREND = "strong_uptrend"      # 强势上涨
    UPTREND = "uptrend"                     # 上涨趋势
    WEAK_UPTREND = "weak_uptrend"           # 弱势上涨
    CONSOLIDATION = "consolidation"          # 盘整震荡
    WEAK_DOWNTREND = "weak_downtrend"       # 弱势下跌
    DOWNTREND = "downtrend"                 # 下跌趋势
    STRONG_DOWNTREND = "strong_downtrend"   # 强势下跌
    BREAKOUT_UP = "breakout_up"             # 向上突破
    BREAKOUT_DOWN = "breakout_down"         # 向下突破
    REVERSAL_BULLISH = "reversal_bullish"   # 看涨反转
    REVERSAL_BEARISH = "reversal_bearish"   # 看跌反转
    UNKNOWN = "unknown"                      # 无法判断


class SignalStrength(Enum):
    """信号强度枚举"""
    VERY_STRONG = 5
    STRONG = 4
    MODERATE = 3
    WEAK = 2
    VERY_WEAK = 1
    NEUTRAL = 0


@dataclass
class TrendAnalysis:
    """趋势分析结果"""
    direction: str  # "bullish", "bearish", "neutral"
    strength: float  # 0-1 趋势强度
    ma_alignment: str  # "aligned_up", "aligned_down", "mixed"
    price_position: str  # "above_ma", "below_ma", "at_ma"
    momentum: str  # "accelerating", "decelerating", "stable"


@dataclass
class VolatilityAnalysis:
    """波动性分析结果"""
    current_atr: float
    atr_percentile: float  # 当前ATR在历史中的百分位
    volatility_state: str  # "low", "normal", "high", "extreme"
    bb_width: float  # 布林带宽度
    bb_width_percentile: float
    suggested_sl_multiplier: float  # 建议的止损ATR倍数
    suggested_tp_multiplier: float  # 建议的止盈ATR倍数


@dataclass
class MomentumAnalysis:
    """动量分析结果"""
    rsi_state: str  # "oversold", "neutral", "overbought"
    rsi_value: float
    rsi_divergence: str | None  # "bullish", "bearish", None
    macd_state: str  # "bullish", "bearish", "neutral"
    macd_histogram_trend: str  # "increasing", "decreasing", "stable"
    macd_crossover: str | None  # "golden_cross", "death_cross", None


@dataclass
class VolumeAnalysis:
    """成交量分析结果"""
    volume_trend: str  # "increasing", "decreasing", "stable"
    volume_ratio: float  # 当前成交量/平均成交量
    volume_confirmation: bool  # 成交量是否确认趋势
    unusual_volume: bool  # 是否有异常成交量


@dataclass
class SupportResistance:
    """支撑阻力位分析"""
    nearest_support: float
    nearest_resistance: float
    support_strength: float  # 0-1
    resistance_strength: float  # 0-1
    price_to_support_pct: float  # 价格距离支撑位的百分比
    price_to_resistance_pct: float  # 价格距离阻力位的百分比


@dataclass
class TradeSignal:
    """交易信号"""
    action: str  # "buy", "sell", "sell_short", "buy_to_cover", "hold"
    confidence: float  # 0-1 置信度
    strength: SignalStrength
    reasons: list[str]
    risk_reward_ratio: float
    suggested_entry: float
    suggested_stop_loss: float
    suggested_take_profit: float
    suggested_position_size_pct: float  # 建议仓位比例 0-1
    warnings: list[str] = field(default_factory=list)


@dataclass
class MarketAnalysisResult:
    """综合市场分析结果"""
    state: MarketState
    trend: TrendAnalysis
    volatility: VolatilityAnalysis
    momentum: MomentumAnalysis
    volume: VolumeAnalysis
    support_resistance: SupportResistance
    signal: TradeSignal
    multi_timeframe_alignment: float  # 多周期一致性 0-1
    overall_score: float  # 综合评分 -1 到 1
    analysis_timestamp: str
    raw_data: dict[str, Any] = field(default_factory=dict)


class MarketStateAnalyzer:
    """市场状态分析器"""

    def __init__(
        self,
        atr_period: int = 14,
        rsi_period: int = 14,
        ma_periods: list[int] = None,
        volatility_lookback: int = 100,
        support_resistance_lookback: int = 50
    ):
        """
        初始化市场状态分析器

        Args:
            atr_period: ATR计算周期
            rsi_period: RSI计算周期
            ma_periods: 均线周期列表
            volatility_lookback: 波动性计算回看周期
            support_resistance_lookback: 支撑阻力位计算回看周期
        """
        self.atr_period = atr_period
        self.rsi_period = rsi_period
        self.ma_periods = ma_periods or [7, 25, 99]
        self.volatility_lookback = volatility_lookback
        self.sr_lookback = support_resistance_lookback

    def analyze(
        self,
        df: pd.DataFrame,
        current_price: float,
        multi_timeframe_trends: dict[str, str] | None = None
    ) -> MarketAnalysisResult:
        """
        执行完整的市场分析

        Args:
            df: OHLCV DataFrame（已计算技术指标）
            current_price: 当前价格
            multi_timeframe_trends: 多周期趋势字典

        Returns:
            MarketAnalysisResult: 综合分析结果
        """
        from datetime import datetime

        # 各维度分析
        trend = self._analyze_trend(df, current_price)
        volatility = self._analyze_volatility(df, current_price)
        momentum = self._analyze_momentum(df)
        volume = self._analyze_volume(df)
        sr = self._analyze_support_resistance(df, current_price)

        # 确定市场状态
        state = self._determine_market_state(trend, momentum, volatility)

        # 计算多周期一致性
        mtf_alignment = self._calculate_mtf_alignment(multi_timeframe_trends)

        # 计算综合评分
        overall_score = self._calculate_overall_score(
            trend, momentum, volume, mtf_alignment
        )

        # 生成交易信号
        signal = self._generate_signal(
            state, trend, volatility, momentum, volume, sr,
            current_price, overall_score, mtf_alignment
        )

        return MarketAnalysisResult(
            state=state,
            trend=trend,
            volatility=volatility,
            momentum=momentum,
            volume=volume,
            support_resistance=sr,
            signal=signal,
            multi_timeframe_alignment=mtf_alignment,
            overall_score=overall_score,
            analysis_timestamp=datetime.now().isoformat(),
            raw_data={
                "current_price": current_price,
                "multi_timeframe_trends": multi_timeframe_trends
            }
        )

    def _analyze_trend(self, df: pd.DataFrame, current_price: float) -> TrendAnalysis:
        """分析趋势"""
        if df.empty or len(df) < max(self.ma_periods):
            return TrendAnalysis(
                direction="neutral",
                strength=0.0,
                ma_alignment="mixed",
                price_position="at_ma",
                momentum="stable"
            )

        latest = df.iloc[-1]

        # 获取均线值
        ma_values = {}
        for period in self.ma_periods:
            col = f'ma_{period}'
            if col in df.columns:
                val = latest.get(col)
                if val is not None and not pd.isna(val):
                    ma_values[period] = val

        if not ma_values:
            return TrendAnalysis(
                direction="neutral",
                strength=0.0,
                ma_alignment="mixed",
                price_position="at_ma",
                momentum="stable"
            )

        # 判断均线排列
        sorted_periods = sorted(ma_values.keys())
        ma_sorted_values = [ma_values[p] for p in sorted_periods]

        # 检查是否多头排列（短期MA > 长期MA）
        is_bullish_aligned = all(
            ma_sorted_values[i] >= ma_sorted_values[i+1]
            for i in range(len(ma_sorted_values)-1)
        )
        # 检查是否空头排列
        is_bearish_aligned = all(
            ma_sorted_values[i] <= ma_sorted_values[i+1]
            for i in range(len(ma_sorted_values)-1)
        )

        if is_bullish_aligned:
            ma_alignment = "aligned_up"
        elif is_bearish_aligned:
            ma_alignment = "aligned_down"
        else:
            ma_alignment = "mixed"

        # 价格相对于均线的位置
        short_ma = ma_values.get(min(ma_values.keys()), current_price)
        if current_price > short_ma * 1.01:
            price_position = "above_ma"
        elif current_price < short_ma * 0.99:
            price_position = "below_ma"
        else:
            price_position = "at_ma"

        # 计算趋势强度
        if len(df) >= 10:
            price_changes = df['close'].pct_change().tail(10)
            avg_change = price_changes.mean()
            trend_consistency = (price_changes > 0).sum() / len(price_changes) if avg_change > 0 else (price_changes < 0).sum() / len(price_changes)
            strength = min(1.0, abs(avg_change) * 100 * trend_consistency)
        else:
            strength = 0.0

        # 判断趋势方向
        if ma_alignment == "aligned_up" and price_position == "above_ma":
            direction = "bullish"
        elif ma_alignment == "aligned_down" and price_position == "below_ma":
            direction = "bearish"
        else:
            direction = "neutral"

        # 判断动量变化
        if len(df) >= 5:
            recent_changes = df['close'].pct_change().tail(5).values
            if len(recent_changes) >= 5:
                first_half = abs(recent_changes[:2]).mean() if len(recent_changes[:2]) > 0 else 0
                second_half = abs(recent_changes[2:]).mean() if len(recent_changes[2:]) > 0 else 0
                if second_half > first_half * 1.2:
                    momentum = "accelerating"
                elif second_half < first_half * 0.8:
                    momentum = "decelerating"
                else:
                    momentum = "stable"
            else:
                momentum = "stable"
        else:
            momentum = "stable"

        return TrendAnalysis(
            direction=direction,
            strength=strength,
            ma_alignment=ma_alignment,
            price_position=price_position,
            momentum=momentum
        )

    def _analyze_volatility(self, df: pd.DataFrame, current_price: float) -> VolatilityAnalysis:
        """分析波动性"""
        default_result = VolatilityAnalysis(
            current_atr=0.0,
            atr_percentile=50.0,
            volatility_state="normal",
            bb_width=0.0,
            bb_width_percentile=50.0,
            suggested_sl_multiplier=1.5,
            suggested_tp_multiplier=3.0
        )

        if df.empty or len(df) < self.atr_period:
            return default_result

        latest = df.iloc[-1]

        # 获取ATR
        atr_col = f'atr_{self.atr_period}'
        if atr_col not in df.columns:
            # 尝试其他ATR列
            atr_cols = [c for c in df.columns if c.startswith('atr_')]
            if atr_cols:
                atr_col = atr_cols[0]
            else:
                return default_result

        current_atr = latest.get(atr_col, 0)
        if pd.isna(current_atr) or current_atr == 0:
            return default_result

        # 计算ATR百分位
        atr_history = df[atr_col].dropna().tail(self.volatility_lookback)
        if len(atr_history) > 0:
            atr_percentile = (atr_history < current_atr).sum() / len(atr_history) * 100
        else:
            atr_percentile = 50.0

        # 布林带宽度
        bb_width = 0.0
        bb_width_percentile = 50.0
        if 'bb_upper' in df.columns and 'bb_lower' in df.columns:
            bb_upper = latest.get('bb_upper', 0)
            bb_lower = latest.get('bb_lower', 0)
            bb_middle = latest.get('bb_middle', current_price)

            if bb_middle > 0 and not pd.isna(bb_upper) and not pd.isna(bb_lower):
                bb_width = (bb_upper - bb_lower) / bb_middle

                # 计算BB宽度历史百分位
                df_copy = df.copy()
                df_copy['bb_width'] = (df_copy['bb_upper'] - df_copy['bb_lower']) / df_copy['bb_middle']
                bb_history = df_copy['bb_width'].dropna().tail(self.volatility_lookback)
                if len(bb_history) > 0:
                    bb_width_percentile = (bb_history < bb_width).sum() / len(bb_history) * 100

        # 判断波动性状态
        if atr_percentile < 20:
            volatility_state = "low"
            sl_mult, tp_mult = 1.0, 2.0  # 低波动时收紧止损止盈
        elif atr_percentile < 40:
            volatility_state = "normal"
            sl_mult, tp_mult = 1.5, 3.0
        elif atr_percentile < 70:
            volatility_state = "high"
            sl_mult, tp_mult = 2.0, 4.0  # 高波动时放宽止损止盈
        else:
            volatility_state = "extreme"
            sl_mult, tp_mult = 2.5, 5.0  # 极端波动时进一步放宽

        return VolatilityAnalysis(
            current_atr=float(current_atr),
            atr_percentile=float(atr_percentile),
            volatility_state=volatility_state,
            bb_width=float(bb_width),
            bb_width_percentile=float(bb_width_percentile),
            suggested_sl_multiplier=sl_mult,
            suggested_tp_multiplier=tp_mult
        )

    def _analyze_momentum(self, df: pd.DataFrame) -> MomentumAnalysis:
        """分析动量"""
        default_result = MomentumAnalysis(
            rsi_state="neutral",
            rsi_value=50.0,
            rsi_divergence=None,
            macd_state="neutral",
            macd_histogram_trend="stable",
            macd_crossover=None
        )

        if df.empty or len(df) < 2:
            return default_result

        latest = df.iloc[-1]
        previous = df.iloc[-2]

        # RSI分析
        rsi = latest.get('rsi', 50)
        if pd.isna(rsi):
            rsi = 50

        if rsi > 70:
            rsi_state = "overbought"
        elif rsi < 30:
            rsi_state = "oversold"
        else:
            rsi_state = "neutral"

        # RSI背离检测
        rsi_divergence = None
        if len(df) >= 10:
            price_trend = df['close'].tail(10).diff().sum()
            rsi_series = df['rsi'].tail(10).dropna()
            if len(rsi_series) >= 2:
                rsi_trend = rsi_series.diff().sum()
                # 价格上涨但RSI下降 = 看跌背离
                if price_trend > 0 and rsi_trend < 0 and rsi > 60:
                    rsi_divergence = "bearish"
                # 价格下跌但RSI上升 = 看涨背离
                elif price_trend < 0 and rsi_trend > 0 and rsi < 40:
                    rsi_divergence = "bullish"

        # MACD分析
        macd = latest.get('macd', 0)
        macd_signal = latest.get('macd_signal', 0)
        macd_hist = latest.get('macd_hist', 0)
        prev_macd = previous.get('macd', 0)
        prev_macd_signal = previous.get('macd_signal', 0)
        prev_macd_hist = previous.get('macd_hist', 0)

        # 处理NaN
        for var in [macd, macd_signal, macd_hist, prev_macd, prev_macd_signal, prev_macd_hist]:
            if pd.isna(var):
                var = 0

        if macd > macd_signal:
            macd_state = "bullish"
        elif macd < macd_signal:
            macd_state = "bearish"
        else:
            macd_state = "neutral"

        # MACD柱状图趋势
        if not pd.isna(macd_hist) and not pd.isna(prev_macd_hist):
            if macd_hist > prev_macd_hist:
                macd_histogram_trend = "increasing"
            elif macd_hist < prev_macd_hist:
                macd_histogram_trend = "decreasing"
            else:
                macd_histogram_trend = "stable"
        else:
            macd_histogram_trend = "stable"

        # MACD交叉检测
        macd_crossover = None
        if not any(pd.isna(x) for x in [macd, macd_signal, prev_macd, prev_macd_signal]):
            if prev_macd <= prev_macd_signal and macd > macd_signal:
                macd_crossover = "golden_cross"
            elif prev_macd >= prev_macd_signal and macd < macd_signal:
                macd_crossover = "death_cross"

        return MomentumAnalysis(
            rsi_state=rsi_state,
            rsi_value=float(rsi) if not pd.isna(rsi) else 50.0,
            rsi_divergence=rsi_divergence,
            macd_state=macd_state,
            macd_histogram_trend=macd_histogram_trend,
            macd_crossover=macd_crossover
        )

    def _analyze_volume(self, df: pd.DataFrame) -> VolumeAnalysis:
        """分析成交量"""
        default_result = VolumeAnalysis(
            volume_trend="stable",
            volume_ratio=1.0,
            volume_confirmation=False,
            unusual_volume=False
        )

        if df.empty or 'volume' not in df.columns:
            return default_result

        if len(df) < 20:
            return default_result

        latest_volume = df['volume'].iloc[-1]
        if pd.isna(latest_volume) or latest_volume == 0:
            return default_result

        # 计算成交量均值
        volume_ma = df['volume'].tail(20).mean()
        volume_ratio = latest_volume / volume_ma if volume_ma > 0 else 1.0

        # 成交量趋势
        recent_volumes = df['volume'].tail(5)
        if len(recent_volumes) >= 5:
            vol_change = recent_volumes.pct_change().mean()
            if vol_change > 0.1:
                volume_trend = "increasing"
            elif vol_change < -0.1:
                volume_trend = "decreasing"
            else:
                volume_trend = "stable"
        else:
            volume_trend = "stable"

        # 是否异常成交量
        volume_std = df['volume'].tail(50).std()
        unusual_volume = latest_volume > volume_ma + 2 * volume_std

        # 成交量确认
        price_change = df['close'].iloc[-1] - df['close'].iloc[-2] if len(df) >= 2 else 0
        # 价格上涨+成交量放大 或 价格下跌+成交量放大 = 确认
        volume_confirmation = volume_ratio > 1.2 and abs(price_change) > 0

        return VolumeAnalysis(
            volume_trend=volume_trend,
            volume_ratio=float(volume_ratio),
            volume_confirmation=volume_confirmation,
            unusual_volume=unusual_volume
        )

    def _analyze_support_resistance(
        self, df: pd.DataFrame, current_price: float
    ) -> SupportResistance:
        """分析支撑阻力位"""
        default_result = SupportResistance(
            nearest_support=current_price * 0.95,
            nearest_resistance=current_price * 1.05,
            support_strength=0.5,
            resistance_strength=0.5,
            price_to_support_pct=5.0,
            price_to_resistance_pct=5.0
        )

        if df.empty or len(df) < self.sr_lookback:
            return default_result

        recent_df = df.tail(self.sr_lookback)

        # 找出局部高点和低点
        highs = recent_df['high'].values
        lows = recent_df['low'].values

        # 使用简单的方法找支撑阻力位
        # 支撑位：低于当前价格的局部低点
        potential_supports = [low_val for low_val in lows if low_val < current_price]
        # 阻力位：高于当前价格的局部高点
        potential_resistances = [h for h in highs if h > current_price]

        if potential_supports:
            # 找最近的支撑位（最高的低于当前价格的点）
            nearest_support = max(potential_supports)
            # 计算该价位被触及的次数作为强度
            touches = sum(1 for low_val in lows if abs(low_val - nearest_support) / nearest_support < 0.01)
            support_strength = min(1.0, touches / 5)
        else:
            nearest_support = current_price * 0.95
            support_strength = 0.3

        if potential_resistances:
            nearest_resistance = min(potential_resistances)
            touches = sum(1 for h in highs if abs(h - nearest_resistance) / nearest_resistance < 0.01)
            resistance_strength = min(1.0, touches / 5)
        else:
            nearest_resistance = current_price * 1.05
            resistance_strength = 0.3

        price_to_support_pct = (current_price - nearest_support) / current_price * 100
        price_to_resistance_pct = (nearest_resistance - current_price) / current_price * 100

        return SupportResistance(
            nearest_support=float(nearest_support),
            nearest_resistance=float(nearest_resistance),
            support_strength=float(support_strength),
            resistance_strength=float(resistance_strength),
            price_to_support_pct=float(price_to_support_pct),
            price_to_resistance_pct=float(price_to_resistance_pct)
        )

    def _determine_market_state(
        self,
        trend: TrendAnalysis,
        momentum: MomentumAnalysis,
        volatility: VolatilityAnalysis
    ) -> MarketState:
        """确定市场状态"""
        # 检测突破
        if volatility.volatility_state == "extreme":
            if trend.direction == "bullish" and trend.momentum == "accelerating":
                return MarketState.BREAKOUT_UP
            elif trend.direction == "bearish" and trend.momentum == "accelerating":
                return MarketState.BREAKOUT_DOWN

        # 检测反转信号
        if momentum.rsi_divergence == "bullish" and momentum.macd_crossover == "golden_cross":
            return MarketState.REVERSAL_BULLISH
        elif momentum.rsi_divergence == "bearish" and momentum.macd_crossover == "death_cross":
            return MarketState.REVERSAL_BEARISH

        # 趋势判断
        if trend.direction == "bullish":
            if trend.strength > 0.7 and trend.ma_alignment == "aligned_up":
                return MarketState.STRONG_UPTREND
            elif trend.strength > 0.4:
                return MarketState.UPTREND
            else:
                return MarketState.WEAK_UPTREND
        elif trend.direction == "bearish":
            if trend.strength > 0.7 and trend.ma_alignment == "aligned_down":
                return MarketState.STRONG_DOWNTREND
            elif trend.strength > 0.4:
                return MarketState.DOWNTREND
            else:
                return MarketState.WEAK_DOWNTREND
        else:
            return MarketState.CONSOLIDATION

    def _calculate_mtf_alignment(
        self, multi_timeframe_trends: dict[str, str] | None
    ) -> float:
        """计算多周期一致性"""
        if not multi_timeframe_trends:
            return 0.5

        bullish_count = 0
        bearish_count = 0
        total = 0

        bullish_keywords = ["上涨", "强势", "bullish", "up"]
        bearish_keywords = ["下跌", "弱势", "bearish", "down"]

        for _tf, trend in multi_timeframe_trends.items():
            if any(kw in trend.lower() for kw in bullish_keywords):
                bullish_count += 1
            elif any(kw in trend.lower() for kw in bearish_keywords):
                bearish_count += 1
            total += 1

        if total == 0:
            return 0.5

        # 返回主导方向的一致性比例
        max_count = max(bullish_count, bearish_count)
        return max_count / total

    def _calculate_overall_score(
        self,
        trend: TrendAnalysis,
        momentum: MomentumAnalysis,
        volume: VolumeAnalysis,
        mtf_alignment: float
    ) -> float:
        """计算综合评分 (-1 到 1)"""
        score = 0.0

        # 趋势得分 (权重 40%)
        if trend.direction == "bullish":
            trend_score = trend.strength
        elif trend.direction == "bearish":
            trend_score = -trend.strength
        else:
            trend_score = 0
        score += trend_score * 0.4

        # 动量得分 (权重 30%)
        momentum_score = 0
        if momentum.macd_state == "bullish":
            momentum_score += 0.5
        elif momentum.macd_state == "bearish":
            momentum_score -= 0.5

        if momentum.rsi_state == "oversold":
            momentum_score += 0.3  # 超卖是看涨信号
        elif momentum.rsi_state == "overbought":
            momentum_score -= 0.3  # 超买是看跌信号

        if momentum.macd_crossover == "golden_cross":
            momentum_score += 0.4
        elif momentum.macd_crossover == "death_cross":
            momentum_score -= 0.4

        score += momentum_score * 0.3

        # 成交量确认 (权重 15%)
        if volume.volume_confirmation:
            if trend.direction == "bullish":
                score += 0.15
            elif trend.direction == "bearish":
                score -= 0.15

        # 多周期一致性 (权重 15%)
        # mtf_alignment 是 0-1，需要结合趋势方向
        if trend.direction == "bullish":
            score += (mtf_alignment - 0.5) * 0.3  # 高一致性加分
        elif trend.direction == "bearish":
            score -= (mtf_alignment - 0.5) * 0.3

        return max(-1.0, min(1.0, score))

    def _generate_signal(
        self,
        state: MarketState,
        trend: TrendAnalysis,
        volatility: VolatilityAnalysis,
        momentum: MomentumAnalysis,
        volume: VolumeAnalysis,
        sr: SupportResistance,
        current_price: float,
        overall_score: float,
        mtf_alignment: float
    ) -> TradeSignal:
        """生成交易信号"""
        reasons = []
        warnings = []

        # 默认值
        action = "hold"
        confidence = 0.5
        strength = SignalStrength.NEUTRAL

        # 基于综合评分和市场状态决定行动
        score_threshold = 0.3  # 需要超过这个阈值才考虑交易

        if overall_score > score_threshold:
            if state in [MarketState.STRONG_UPTREND, MarketState.UPTREND, MarketState.BREAKOUT_UP]:
                action = "buy"
                confidence = min(0.9, 0.5 + overall_score * 0.4)
                reasons.append(f"市场处于{state.value}状态")

                if trend.ma_alignment == "aligned_up":
                    reasons.append("均线多头排列")
                    confidence += 0.05

                if momentum.macd_crossover == "golden_cross":
                    reasons.append("MACD金叉")
                    confidence += 0.1

                if volume.volume_confirmation:
                    reasons.append("成交量确认上涨")
                    confidence += 0.05

            elif state == MarketState.REVERSAL_BULLISH:
                action = "buy"
                confidence = 0.6
                reasons.append("检测到看涨反转信号")
                if momentum.rsi_divergence == "bullish":
                    reasons.append("RSI看涨背离")

        elif overall_score < -score_threshold:
            if state in [MarketState.STRONG_DOWNTREND, MarketState.DOWNTREND, MarketState.BREAKOUT_DOWN]:
                action = "sell_short"
                confidence = min(0.9, 0.5 + abs(overall_score) * 0.4)
                reasons.append(f"市场处于{state.value}状态")

                if trend.ma_alignment == "aligned_down":
                    reasons.append("均线空头排列")
                    confidence += 0.05

                if momentum.macd_crossover == "death_cross":
                    reasons.append("MACD死叉")
                    confidence += 0.1

            elif state == MarketState.REVERSAL_BEARISH:
                action = "sell_short"
                confidence = 0.6
                reasons.append("检测到看跌反转信号")
                if momentum.rsi_divergence == "bearish":
                    reasons.append("RSI看跌背离")

        # 添加警告
        if volatility.volatility_state == "extreme":
            warnings.append("当前市场波动极大，建议减小仓位")

        if mtf_alignment < 0.4:
            warnings.append("多周期趋势不一致，信号可靠性降低")

        if momentum.rsi_state == "overbought" and action == "buy":
            warnings.append("RSI超买，追高风险较大")
        elif momentum.rsi_state == "oversold" and action == "sell_short":
            warnings.append("RSI超卖，追空风险较大")

        # 计算止损止盈
        atr = volatility.current_atr if volatility.current_atr > 0 else current_price * 0.02

        if action == "buy":
            suggested_stop_loss = current_price - atr * volatility.suggested_sl_multiplier
            suggested_take_profit = current_price + atr * volatility.suggested_tp_multiplier
        elif action == "sell_short":
            suggested_stop_loss = current_price + atr * volatility.suggested_sl_multiplier
            suggested_take_profit = current_price - atr * volatility.suggested_tp_multiplier
        else:
            suggested_stop_loss = current_price * 0.98
            suggested_take_profit = current_price * 1.02

        # 风险回报比
        potential_profit = abs(suggested_take_profit - current_price)
        potential_loss = abs(current_price - suggested_stop_loss)
        risk_reward_ratio = potential_profit / potential_loss if potential_loss > 0 else 1.0

        # 建议仓位比例（基于置信度和波动性调整）
        base_position = 0.1  # 基础10%
        confidence_factor = confidence
        volatility_factor = 1.0 if volatility.volatility_state == "normal" else 0.7 if volatility.volatility_state == "high" else 0.5
        suggested_position_size_pct = base_position * confidence_factor * volatility_factor

        # 确定信号强度
        if confidence >= 0.8:
            strength = SignalStrength.VERY_STRONG
        elif confidence >= 0.65:
            strength = SignalStrength.STRONG
        elif confidence >= 0.5:
            strength = SignalStrength.MODERATE
        elif confidence >= 0.35:
            strength = SignalStrength.WEAK
        else:
            strength = SignalStrength.VERY_WEAK

        if action == "hold":
            reasons.append("综合评分未达到交易阈值，建议观望")

        return TradeSignal(
            action=action,
            confidence=min(1.0, confidence),
            strength=strength,
            reasons=reasons,
            risk_reward_ratio=risk_reward_ratio,
            suggested_entry=current_price,
            suggested_stop_loss=suggested_stop_loss,
            suggested_take_profit=suggested_take_profit,
            suggested_position_size_pct=suggested_position_size_pct,
            warnings=warnings
        )


def format_analysis_for_prompt(analysis: MarketAnalysisResult) -> str:
    """
    将分析结果格式化为可注入Prompt的文本

    Args:
        analysis: MarketAnalysisResult 分析结果

    Returns:
        格式化的分析文本
    """
    lines = []
    lines.append("## 📊 市场状态分析")
    lines.append(f"**当前状态**: {analysis.state.value}")
    lines.append(f"**综合评分**: {analysis.overall_score:.2f} (-1到1)")
    lines.append(f"**多周期一致性**: {analysis.multi_timeframe_alignment:.0%}")
    lines.append("")

    # 趋势分析
    trend = analysis.trend
    lines.append("### 趋势分析")
    lines.append(f"- 方向: {trend.direction}")
    lines.append(f"- 强度: {trend.strength:.2f}")
    lines.append(f"- 均线排列: {trend.ma_alignment}")
    lines.append(f"- 价格位置: {trend.price_position}")
    lines.append(f"- 动量: {trend.momentum}")
    lines.append("")

    # 波动性分析
    vol = analysis.volatility
    lines.append("### 波动性分析")
    lines.append(f"- ATR: {vol.current_atr:.4f} (百分位: {vol.atr_percentile:.0f}%)")
    lines.append(f"- 波动状态: {vol.volatility_state}")
    lines.append(f"- 布林带宽度: {vol.bb_width:.4f}")
    lines.append(f"- 建议止损倍数: {vol.suggested_sl_multiplier}x ATR")
    lines.append(f"- 建议止盈倍数: {vol.suggested_tp_multiplier}x ATR")
    lines.append("")

    # 动量分析
    mom = analysis.momentum
    lines.append("### 动量分析")
    lines.append(f"- RSI: {mom.rsi_value:.1f} ({mom.rsi_state})")
    if mom.rsi_divergence:
        lines.append(f"- RSI背离: {mom.rsi_divergence}")
    lines.append(f"- MACD状态: {mom.macd_state}")
    lines.append(f"- MACD柱状图趋势: {mom.macd_histogram_trend}")
    if mom.macd_crossover:
        lines.append(f"- MACD交叉: {mom.macd_crossover}")
    lines.append("")

    # 成交量分析
    vol_analysis = analysis.volume
    lines.append("### 成交量分析")
    lines.append(f"- 趋势: {vol_analysis.volume_trend}")
    lines.append(f"- 量比: {vol_analysis.volume_ratio:.2f}")
    lines.append(f"- 量价确认: {'是' if vol_analysis.volume_confirmation else '否'}")
    if vol_analysis.unusual_volume:
        lines.append("- ⚠️ 检测到异常成交量")
    lines.append("")

    # 支撑阻力
    sr = analysis.support_resistance
    lines.append("### 支撑阻力位")
    lines.append(f"- 最近支撑: ${sr.nearest_support:.2f} (距离: {sr.price_to_support_pct:.1f}%)")
    lines.append(f"- 最近阻力: ${sr.nearest_resistance:.2f} (距离: {sr.price_to_resistance_pct:.1f}%)")
    lines.append("")

    # 交易信号
    sig = analysis.signal
    lines.append("### 🎯 交易信号")
    lines.append(f"- 建议操作: {sig.action}")
    lines.append(f"- 置信度: {sig.confidence:.0%}")
    lines.append(f"- 信号强度: {sig.strength.name}")
    lines.append(f"- 风险回报比: {sig.risk_reward_ratio:.2f}")
    lines.append(f"- 建议入场价: ${sig.suggested_entry:.2f}")
    lines.append(f"- 建议止损价: ${sig.suggested_stop_loss:.2f}")
    lines.append(f"- 建议止盈价: ${sig.suggested_take_profit:.2f}")
    lines.append(f"- 建议仓位比例: {sig.suggested_position_size_pct:.1%}")

    if sig.reasons:
        lines.append("- 理由:")
        for r in sig.reasons:
            lines.append(f"  - {r}")

    if sig.warnings:
        lines.append("- ⚠️ 警告:")
        for w in sig.warnings:
            lines.append(f"  - {w}")

    return "\n".join(lines)
