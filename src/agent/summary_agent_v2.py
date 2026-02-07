"""
汇总 Agent 模块 v2 - 使用 LangChain 上下文压缩技术
负责对历史决策和市场走势进行分层汇总，生成压缩的上下文摘要

v2.1 增强版：
- 增强市场数据压缩，包含趋势强度、波动性、支撑阻力位
- 增加决策效果分析，统计历史决策的实际盈亏
- 智能压缩策略，根据信息类型选择不同压缩方式
- 与增强分析模块集成，利用市场状态分析结果
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.messages.utils import count_tokens_approximately
from pydantic import BaseModel, Field

from src.llm import LLMClientManager
from src.utils.logger import TradingLogger


class TrendStrength(StrEnum):
    """趋势强度枚举"""

    VERY_STRONG = "very_strong"
    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"
    NEUTRAL = "neutral"


class VolatilityLevel(StrEnum):
    """波动性级别枚举"""

    EXTREME = "extreme"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


# 结构化的市场走势汇总（增强版）
class MarketTrendSummary(BaseModel):
    """市场走势汇总结构（增强版）"""

    price_trend: str = Field(description="价格趋势：上涨/下跌/震荡")
    trend_strength: str = Field(
        default="moderate", description="趋势强度：very_strong/strong/moderate/weak/neutral"
    )
    price_range: str = Field(description="价格区间：如 $60000-$62000")
    price_change_pct: float = Field(default=0.0, description="价格变化百分比")
    key_levels: list[float] = Field(description="关键价位列表")
    support_level: float | None = Field(default=None, description="支撑位")
    resistance_level: float | None = Field(default=None, description="阻力位")
    rsi_status: str = Field(description="RSI 状态：超买/超卖/中性")
    rsi_trend: str = Field(default="stable", description="RSI 趋势：rising/falling/stable")
    macd_status: str = Field(default="neutral", description="MACD 状态：bullish/bearish/neutral")
    volume_pattern: str = Field(description="成交量模式：放大/缩小/正常")
    volatility_level: str = Field(
        default="normal", description="波动性级别：extreme/high/normal/low"
    )
    overall_sentiment: str = Field(description="整体市场情绪")
    market_phase: str = Field(
        default="unknown", description="市场阶段：accumulation/markup/distribution/markdown"
    )


# 结构化的决策汇总（增强版）
class DecisionSummary(BaseModel):
    """决策汇总结构（增强版）"""

    total_decisions: int = Field(description="决策总数")
    buy_count: int = Field(description="买入次数")
    sell_count: int = Field(description="卖出次数")
    do_nothing_count: int = Field(description="观望次数")
    short_count: int = Field(default=0, description="做空次数")
    close_count: int = Field(default=0, description="平仓次数")
    key_reasons: list[str] = Field(description="主要决策理由")
    strategy_pattern: str = Field(description="策略模式描述")
    risk_events: list[str] = Field(description="风险事件列表")
    # 效果分析（新增）
    profitable_decisions: int = Field(default=0, description="盈利决策数")
    losing_decisions: int = Field(default=0, description="亏损决策数")
    win_rate: float = Field(default=0.0, description="胜率")
    avg_profit_pct: float = Field(default=0.0, description="平均盈利百分比")
    avg_loss_pct: float = Field(default=0.0, description="平均亏损百分比")
    max_consecutive_losses: int = Field(default=0, description="最大连续亏损次数")
    decision_quality_score: float = Field(default=0.5, description="决策质量得分 0-1")


class SummaryAgentV2:
    """增强版汇总 Agent - 使用上下文压缩技术"""

    def __init__(
        self,
        logger: TradingLogger,
        llm_manager: LLMClientManager,
        temperature: float = 0.1,
        max_context_tokens: int = 2000,
    ):
        """
        初始化增强版汇总 Agent

        Args:
            logger: 日志记录器
            llm_manager: LLM 客户端管理器
            temperature: 温度参数
            max_context_tokens: 最大上下文 token 数
        """
        self.logger = logger
        self.llm_manager = llm_manager
        self.max_context_tokens = max_context_tokens

        # 初始化主 LLM
        self.llm = self.llm_manager.get_client(temperature=temperature)

        # 初始化压缩用的快速 LLM
        self.compression_llm = self.llm_manager.get_client(temperature=0.1)

    def _calculate_trend_strength(
        self,
        prices: list[float],
        ma_short: list[float] | None = None,
        ma_long: list[float] | None = None,
    ) -> tuple[str, str]:
        """
        计算趋势方向和强度

        Args:
            prices: 价格列表
            ma_short: 短期均线列表
            ma_long: 长期均线列表

        Returns:
            (趋势方向, 趋势强度)
        """
        if not prices or len(prices) < 3:
            return "震荡", "neutral"

        # 计算价格变化
        price_change = (prices[-1] - prices[0]) / prices[0] if prices[0] != 0 else 0

        # 计算趋势一致性（价格单调性）
        up_count = sum(1 for i in range(1, len(prices)) if prices[i] > prices[i - 1])
        down_count = sum(1 for i in range(1, len(prices)) if prices[i] < prices[i - 1])
        total_moves = len(prices) - 1

        # 趋势方向
        if price_change > 0.02:  # 2% 以上涨幅
            direction = "上涨"
        elif price_change < -0.02:  # 2% 以上跌幅
            direction = "下跌"
        else:
            direction = "震荡"

        # 趋势强度
        consistency = max(up_count, down_count) / total_moves if total_moves > 0 else 0.5

        if abs(price_change) > 0.05 and consistency > 0.7:
            strength = "very_strong"
        elif abs(price_change) > 0.03 and consistency > 0.6:
            strength = "strong"
        elif abs(price_change) > 0.01:
            strength = "moderate"
        elif abs(price_change) > 0.005:
            strength = "weak"
        else:
            strength = "neutral"

        return direction, strength

    def _calculate_volatility_level(
        self, prices: list[float], atr_values: list[float] | None = None
    ) -> str:
        """
        计算波动性级别

        Args:
            prices: 价格列表
            atr_values: ATR 值列表（可选）

        Returns:
            波动性级别
        """
        if not prices or len(prices) < 2:
            return "normal"

        # 计算价格波动率
        avg_price = sum(prices) / len(prices)
        price_std = (sum((p - avg_price) ** 2 for p in prices) / len(prices)) ** 0.5
        volatility_pct = (price_std / avg_price) * 100 if avg_price > 0 else 0

        # 如果有 ATR 数据，结合 ATR 分析
        if atr_values and len(atr_values) > 0:
            avg_atr = sum(atr_values) / len(atr_values)
            atr_pct = (avg_atr / avg_price) * 100 if avg_price > 0 else 0
            volatility_pct = (volatility_pct + atr_pct) / 2

        if volatility_pct > 5:
            return "extreme"
        elif volatility_pct > 3:
            return "high"
        elif volatility_pct > 1:
            return "normal"
        else:
            return "low"

    def _detect_market_phase(
        self, prices: list[float], volumes: list[float], trend_direction: str
    ) -> str:
        """
        检测市场阶段（威科夫理论）

        Args:
            prices: 价格列表
            volumes: 成交量列表
            trend_direction: 趋势方向

        Returns:
            市场阶段
        """
        if not prices or len(prices) < 5:
            return "unknown"

        # 计算价格和成交量的变化趋势
        (prices[-1] - prices[0]) / prices[0] if prices[0] != 0 else 0

        # 成交量趋势
        vol_first_half = sum(volumes[: len(volumes) // 2]) if volumes else 0
        vol_second_half = sum(volumes[len(volumes) // 2 :]) if volumes else 0
        vol_trend = (
            "increasing"
            if vol_second_half > vol_first_half * 1.1
            else "decreasing"
            if vol_second_half < vol_first_half * 0.9
            else "stable"
        )

        # 根据价格趋势和成交量变化判断市场阶段
        if trend_direction == "上涨":
            if vol_trend == "increasing":
                return "markup"  # 上升阶段
            else:
                return "distribution"  # 派发阶段（价格涨但量缩）
        elif trend_direction == "下跌":
            if vol_trend == "increasing":
                return "markdown"  # 下降阶段
            else:
                return "accumulation"  # 积累阶段（价格跌但量缩）
        else:
            # 震荡阶段
            if vol_trend == "decreasing":
                return "accumulation"
            else:
                return "distribution"

    def _find_support_resistance(self, prices: list[float]) -> tuple[float | None, float | None]:
        """
        寻找支撑位和阻力位

        Args:
            prices: 价格列表

        Returns:
            (支撑位, 阻力位)
        """
        if not prices or len(prices) < 5:
            return None, None

        # 简单方法：使用局部最低点和最高点
        current_price = prices[-1]

        # 寻找低于当前价格的局部最低点作为支撑
        lows = []
        for i in range(1, len(prices) - 1):
            if prices[i] < prices[i - 1] and prices[i] < prices[i + 1]:
                if prices[i] < current_price:
                    lows.append(prices[i])

        # 寻找高于当前价格的局部最高点作为阻力
        highs = []
        for i in range(1, len(prices) - 1):
            if prices[i] > prices[i - 1] and prices[i] > prices[i + 1]:
                if prices[i] > current_price:
                    highs.append(prices[i])

        support = max(lows) if lows else min(prices)
        resistance = min(highs) if highs else max(prices)

        return support, resistance

    def _analyze_decision_effectiveness(
        self, decision_records: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """
        分析决策效果

        Args:
            decision_records: 决策记录列表

        Returns:
            效果分析结果
        """
        result = {
            "profitable_decisions": 0,
            "losing_decisions": 0,
            "win_rate": 0.0,
            "avg_profit_pct": 0.0,
            "avg_loss_pct": 0.0,
            "max_consecutive_losses": 0,
            "decision_quality_score": 0.5,
        }

        if not decision_records:
            return result

        profits = []
        losses = []
        consecutive_losses = 0
        max_consecutive_losses = 0

        for record in decision_records:
            action_details = record.get("action_details", {})
            pnl = action_details.get("pnl", 0) if action_details else 0
            pnl_pct = action_details.get("pnl_pct", 0) if action_details else 0

            # 如果没有 pnl 数据，尝试从其他字段推断
            if pnl == 0 and action_details:
                # 检查是否有盈亏相关的字段
                closed_pnl = action_details.get("closed_pnl", 0)
                unrealized_pnl = action_details.get("unrealized_pnl", 0)
                pnl = closed_pnl or unrealized_pnl

            if pnl > 0:
                result["profitable_decisions"] += 1
                profits.append(pnl_pct if pnl_pct else pnl)
                consecutive_losses = 0
            elif pnl < 0:
                result["losing_decisions"] += 1
                losses.append(abs(pnl_pct if pnl_pct else pnl))
                consecutive_losses += 1
                max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)

        # 计算胜率
        total_closed = result["profitable_decisions"] + result["losing_decisions"]
        if total_closed > 0:
            result["win_rate"] = result["profitable_decisions"] / total_closed

        # 计算平均盈亏
        if profits:
            result["avg_profit_pct"] = sum(profits) / len(profits)
        if losses:
            result["avg_loss_pct"] = sum(losses) / len(losses)

        result["max_consecutive_losses"] = max_consecutive_losses

        # 计算决策质量得分
        # 综合考虑胜率、盈亏比、连续亏损
        win_rate_score = result["win_rate"]
        profit_loss_ratio = (
            result["avg_profit_pct"] / result["avg_loss_pct"] if result["avg_loss_pct"] > 0 else 1
        )
        profit_loss_score = min(profit_loss_ratio / 2, 1)  # 盈亏比 2:1 得满分
        consecutive_loss_penalty = max(0, 1 - max_consecutive_losses * 0.1)  # 每次连续亏损扣 10%

        result["decision_quality_score"] = (
            win_rate_score * 0.4 + profit_loss_score * 0.4 + consecutive_loss_penalty * 0.2
        )

        return result

    def compress_market_history(
        self, symbol: str, market_records: list[dict[str, Any]]
    ) -> MarketTrendSummary:
        """
        压缩市场历史数据为结构化汇总（增强版）

        Args:
            symbol: 交易对
            market_records: 市场数据记录列表

        Returns:
            结构化的市场走势汇总
        """
        if not market_records:
            return MarketTrendSummary(
                price_trend="未知",
                price_range="无数据",
                key_levels=[],
                rsi_status="未知",
                volume_pattern="无数据",
                overall_sentiment="无数据",
            )

        try:
            # 提取关键市场数据
            prices = [
                r.get("market_data", {}).get("current_price", 0)
                for r in market_records
                if r.get("market_data", {}).get("current_price", 0) > 0
            ]
            rsi_values = [r.get("market_data", {}).get("rsi", 50) for r in market_records]
            volumes = [r.get("market_data", {}).get("volume", 0) for r in market_records]
            volume_changes = [
                r.get("market_data", {}).get("volume_change", 0) for r in market_records
            ]
            atr_values = [
                r.get("market_data", {}).get("atr_14", 0)
                for r in market_records
                if r.get("market_data", {}).get("atr_14", 0) > 0
            ]
            macd_values = [r.get("market_data", {}).get("macd", 0) for r in market_records]
            macd_signals = [r.get("market_data", {}).get("macd_signal", 0) for r in market_records]

            if not prices:
                return MarketTrendSummary(
                    price_trend="未知",
                    price_range="无有效价格数据",
                    key_levels=[],
                    rsi_status="未知",
                    volume_pattern="无数据",
                    overall_sentiment="数据不足",
                )

            # 基础统计
            price_min = min(prices)
            price_max = max(prices)
            price_first = prices[0]
            price_last = prices[-1]
            price_change_pct = (
                ((price_last - price_first) / price_first * 100) if price_first > 0 else 0
            )
            avg_rsi = sum(rsi_values) / len(rsi_values) if rsi_values else 50
            avg_volume_change = sum(volume_changes) / len(volume_changes) if volume_changes else 0

            # 使用增强分析函数
            price_trend, trend_strength = self._calculate_trend_strength(prices)
            volatility_level = self._calculate_volatility_level(prices, atr_values)
            market_phase = self._detect_market_phase(prices, volumes, price_trend)
            support_level, resistance_level = self._find_support_resistance(prices)

            # RSI 状态和趋势
            rsi_status = "中性"
            if avg_rsi > 70:
                rsi_status = "超买"
            elif avg_rsi > 60:
                rsi_status = "偏强"
            elif avg_rsi < 30:
                rsi_status = "超卖"
            elif avg_rsi < 40:
                rsi_status = "偏弱"

            # RSI 趋势
            rsi_trend = "stable"
            if len(rsi_values) >= 3:
                rsi_first_half = sum(rsi_values[: len(rsi_values) // 2]) / (len(rsi_values) // 2)
                rsi_second_half = sum(rsi_values[len(rsi_values) // 2 :]) / (
                    len(rsi_values) - len(rsi_values) // 2
                )
                if rsi_second_half > rsi_first_half + 5:
                    rsi_trend = "rising"
                elif rsi_second_half < rsi_first_half - 5:
                    rsi_trend = "falling"

            # MACD 状态
            macd_status = "neutral"
            if macd_values and macd_signals:
                recent_macd = macd_values[-1] if macd_values else 0
                recent_signal = macd_signals[-1] if macd_signals else 0
                if recent_macd > recent_signal and recent_macd > 0:
                    macd_status = "bullish"
                elif recent_macd < recent_signal and recent_macd < 0:
                    macd_status = "bearish"

            # 成交量模式
            volume_pattern = "正常"
            if avg_volume_change > 30:
                volume_pattern = "显著放大"
            elif avg_volume_change > 15:
                volume_pattern = "放大"
            elif avg_volume_change < -30:
                volume_pattern = "显著缩小"
            elif avg_volume_change < -15:
                volume_pattern = "缩小"

            # 生成整体情绪描述（不再调用 LLM，直接基于数据分析）
            sentiment_parts = []
            if price_trend == "上涨":
                sentiment_parts.append(f"价格上涨{abs(price_change_pct):.1f}%")
            elif price_trend == "下跌":
                sentiment_parts.append(f"价格下跌{abs(price_change_pct):.1f}%")
            else:
                sentiment_parts.append("价格震荡")

            if trend_strength in ["very_strong", "strong"]:
                sentiment_parts.append("趋势明确")
            elif trend_strength == "weak":
                sentiment_parts.append("趋势不明")

            if volatility_level in ["extreme", "high"]:
                sentiment_parts.append("波动较大")
            elif volatility_level == "low":
                sentiment_parts.append("波动平静")

            overall_sentiment = "，".join(sentiment_parts)

            # 关键价位
            key_levels = [price_min]
            if support_level and support_level != price_min:
                key_levels.append(support_level)
            key_levels.append((price_min + price_max) / 2)
            if resistance_level and resistance_level != price_max:
                key_levels.append(resistance_level)
            key_levels.append(price_max)
            key_levels = sorted(set(key_levels))

            return MarketTrendSummary(
                price_trend=price_trend,
                trend_strength=trend_strength,
                price_range=f"${price_min:.2f}-${price_max:.2f}",
                price_change_pct=price_change_pct,
                key_levels=key_levels,
                support_level=support_level,
                resistance_level=resistance_level,
                rsi_status=rsi_status,
                rsi_trend=rsi_trend,
                macd_status=macd_status,
                volume_pattern=volume_pattern,
                volatility_level=volatility_level,
                overall_sentiment=overall_sentiment,
                market_phase=market_phase,
            )

        except Exception as e:
            self.logger.logger.error(f"市场数据压缩失败: {e}")
            return MarketTrendSummary(
                price_trend="未知",
                price_range="处理失败",
                key_levels=[],
                rsi_status="未知",
                volume_pattern="未知",
                overall_sentiment=str(e)[:50],
            )

    def compress_decision_history(
        self, symbol: str, decision_records: list[dict[str, Any]]
    ) -> DecisionSummary:
        """
        压缩决策历史为结构化汇总（增强版）

        Args:
            symbol: 交易对
            decision_records: 决策记录列表

        Returns:
            结构化的决策汇总
        """
        if not decision_records:
            return DecisionSummary(
                total_decisions=0,
                buy_count=0,
                sell_count=0,
                do_nothing_count=0,
                key_reasons=[],
                strategy_pattern="无决策历史",
                risk_events=[],
            )

        try:
            # 统计决策类型（增强版）
            buy_count = 0
            sell_count = 0
            do_nothing_count = 0
            short_count = 0
            close_count = 0

            for r in decision_records:
                decision = r.get("decision", "").upper()
                if "BUY_LONG" in decision or (decision == "BUY" and "SHORT" not in decision):
                    buy_count += 1
                elif "SELL_SHORT" in decision or "SHORT" in decision:
                    short_count += 1
                elif "SELL" in decision or "CLOSE" in decision or "COVER" in decision:
                    if "BUY" not in decision:
                        close_count += 1
                    else:
                        sell_count += 1
                elif "DO_NOTHING" in decision or "HOLD" in decision:
                    do_nothing_count += 1

            # 分析决策效果
            effectiveness = self._analyze_decision_effectiveness(decision_records)

            # 提取决策理由（智能压缩）
            reasons = []
            for r in decision_records:
                reason = r.get("reason", "")
                if reason:
                    # 截取更有意义的部分
                    reason_clean = reason[:150].strip()
                    if reason_clean:
                        reasons.append(reason_clean)

            key_reasons = []
            if len(reasons) > 5:
                # 使用 LLM 压缩理由（仅在理由较多时）
                reasons_text = "\n".join([f"{i + 1}. {r}" for i, r in enumerate(reasons[:10])])

                prompt = f"""总结以下 {symbol} 的决策理由，提取3-5个关键要点：

{reasons_text}

要求：
1. 每个要点不超过20个字
2. 只列出最重要的决策依据
3. 避免重复，合并类似观点
4. 突出关键技术指标和市场状态"""

                messages = [
                    SystemMessage(content="你是决策分析专家，善于提炼核心逻辑。"),
                    HumanMessage(content=prompt),
                ]

                try:
                    response = self.compression_llm.invoke(messages)
                    key_reasons = [
                        line.strip()
                        for line in response.content.strip().split("\n")
                        if line.strip() and not line.strip().startswith("#")
                    ][:5]
                except Exception:
                    # LLM 调用失败时使用简单方法
                    key_reasons = reasons[:3]
            else:
                key_reasons = reasons[:5]

            # 识别策略模式（增强版）
            total_active = buy_count + short_count + close_count + sell_count
            total_all = total_active + do_nothing_count

            if total_all == 0:
                strategy_pattern = "无决策记录"
            elif do_nothing_count / total_all > 0.7:
                strategy_pattern = "保守观望"
            elif buy_count > short_count and buy_count > do_nothing_count * 0.5:
                if effectiveness["win_rate"] > 0.6:
                    strategy_pattern = "积极做多（胜率较高）"
                else:
                    strategy_pattern = "积极做多"
            elif short_count > buy_count and short_count > do_nothing_count * 0.5:
                if effectiveness["win_rate"] > 0.6:
                    strategy_pattern = "积极做空（胜率较高）"
                else:
                    strategy_pattern = "积极做空"
            elif close_count > buy_count + short_count:
                strategy_pattern = "频繁平仓"
            elif buy_count + short_count > do_nothing_count:
                strategy_pattern = "双向交易"
            else:
                strategy_pattern = "观望为主"

            # 根据决策质量调整描述
            if effectiveness["decision_quality_score"] < 0.3:
                strategy_pattern += "（需优化）"
            elif effectiveness["decision_quality_score"] > 0.7:
                strategy_pattern += "（效果良好）"

            # 识别风险事件（增强版）
            risk_events = []
            risk_keywords = [
                "止损",
                "风险",
                "警告",
                "异常",
                "亏损",
                "熔断",
                "爆仓",
                "stop",
                "loss",
                "risk",
            ]

            for record in decision_records:
                reason = record.get("reason", "").lower()
                decision = record.get("decision", "").upper()

                # 检查风险关键词
                if any(keyword in reason for keyword in risk_keywords):
                    event_text = record.get("reason", "")[:60]
                    if event_text not in risk_events:
                        risk_events.append(event_text)

                # 检查强制平仓类决策
                if "STOP_LOSS" in decision or "FORCE" in decision:
                    action_details = record.get("action_details", {})
                    pnl = action_details.get("pnl", 0) if action_details else 0
                    if pnl < 0:
                        risk_events.append(f"止损平仓: {pnl:.2f}")

            return DecisionSummary(
                total_decisions=len(decision_records),
                buy_count=buy_count,
                sell_count=sell_count,
                do_nothing_count=do_nothing_count,
                short_count=short_count,
                close_count=close_count,
                key_reasons=key_reasons,
                strategy_pattern=strategy_pattern,
                risk_events=risk_events[:5],
                profitable_decisions=effectiveness["profitable_decisions"],
                losing_decisions=effectiveness["losing_decisions"],
                win_rate=effectiveness["win_rate"],
                avg_profit_pct=effectiveness["avg_profit_pct"],
                avg_loss_pct=effectiveness["avg_loss_pct"],
                max_consecutive_losses=effectiveness["max_consecutive_losses"],
                decision_quality_score=effectiveness["decision_quality_score"],
            )

        except Exception as e:
            self.logger.logger.error(f"决策历史压缩失败: {e}")
            return DecisionSummary(
                total_decisions=len(decision_records),
                buy_count=0,
                sell_count=0,
                do_nothing_count=0,
                key_reasons=[],
                strategy_pattern=f"压缩失败: {str(e)[:30]}",
                risk_events=[],
            )

    def create_compressed_summary(
        self,
        symbol: str,
        recent_records: list[dict[str, Any]],
        older_records: list[dict[str, Any]] | None = None,
    ) -> str:
        """
        创建压缩的上下文汇总（增强版 - 包含更丰富的分析信息）

        Args:
            symbol: 交易对
            recent_records: 最近的记录（前10次）
            older_records: 较早的记录（前10-20次，可选）

        Returns:
            压缩的汇总文本
        """
        try:
            self.logger.logger.info(f"[汇总Agent V2.1] 开始压缩 {symbol} 的历史信息...")

            # 1. 压缩最近的市场走势
            recent_market = self.compress_market_history(symbol, recent_records)

            # 2. 压缩最近的决策历史
            recent_decisions = self.compress_decision_history(symbol, recent_records)

            # 3. 构建增强汇总文本
            summary_parts = [
                f"## {symbol} 历史汇总 (增强版)\n",
                "### 📊 市场状态",
                f"- 趋势: {recent_market.price_trend} ({recent_market.trend_strength})",
                f"- 价格: {recent_market.price_range} ({recent_market.price_change_pct:+.2f}%)",
                f"- 阶段: {self._translate_market_phase(recent_market.market_phase)}",
                f"- 波动: {self._translate_volatility(recent_market.volatility_level)}",
            ]

            # 添加关键价位
            if recent_market.support_level and recent_market.resistance_level:
                summary_parts.append(
                    f"- 关键位: 支撑 ${recent_market.support_level:.2f} / 阻力 ${recent_market.resistance_level:.2f}"
                )

            # 技术指标状态
            summary_parts.extend(
                [
                    "\n### 📈 技术指标",
                    f"- RSI: {recent_market.rsi_status} (趋势: {self._translate_trend(recent_market.rsi_trend)})",
                    f"- MACD: {self._translate_macd(recent_market.macd_status)}",
                    f"- 成交量: {recent_market.volume_pattern}",
                    f"- 综合: {recent_market.overall_sentiment}",
                ]
            )

            # 决策统计
            summary_parts.extend(
                [
                    f"\n### 🎯 决策统计 (共{recent_decisions.total_decisions}次)",
                    f"- 做多: {recent_decisions.buy_count}次 | 做空: {recent_decisions.short_count}次 | 平仓: {recent_decisions.close_count}次 | 观望: {recent_decisions.do_nothing_count}次",
                    f"- 策略: {recent_decisions.strategy_pattern}",
                ]
            )

            # 决策效果（如果有数据）
            if recent_decisions.profitable_decisions + recent_decisions.losing_decisions > 0:
                summary_parts.extend(
                    [
                        "\n### 💰 决策效果",
                        f"- 胜率: {recent_decisions.win_rate:.1%} ({recent_decisions.profitable_decisions}盈/{recent_decisions.losing_decisions}亏)",
                        f"- 质量得分: {recent_decisions.decision_quality_score:.2f}/1.00",
                    ]
                )
                if recent_decisions.max_consecutive_losses > 0:
                    summary_parts.append(f"- 最大连亏: {recent_decisions.max_consecutive_losses}次")

            # 关键理由
            if recent_decisions.key_reasons:
                summary_parts.append("\n### 💡 关键决策依据")
                for i, reason in enumerate(recent_decisions.key_reasons[:4], 1):
                    # 清理理由文本
                    reason_clean = reason.strip()
                    if reason_clean and not reason_clean.startswith(str(i)):
                        summary_parts.append(f"{i}. {reason_clean}")
                    elif reason_clean:
                        summary_parts.append(reason_clean)

            # 风险事件
            if recent_decisions.risk_events:
                summary_parts.append("\n### ⚠️ 风险事件")
                for event in recent_decisions.risk_events[:3]:
                    summary_parts.append(f"- {event}")

            # 4. 如果有较早记录，添加趋势演变分析
            if older_records and len(older_records) >= 5:
                older_market = self.compress_market_history(symbol, older_records)
                older_decisions = self.compress_decision_history(symbol, older_records)

                summary_parts.extend(
                    [
                        "\n### 🔄 趋势演变",
                        f"- 市场: {older_market.price_trend} → {recent_market.price_trend}",
                        f"- 波动: {self._translate_volatility(older_market.volatility_level)} → {self._translate_volatility(recent_market.volatility_level)}",
                        f"- 策略: {older_decisions.strategy_pattern} → {recent_decisions.strategy_pattern}",
                    ]
                )

                # 效果对比
                if older_decisions.win_rate > 0 or recent_decisions.win_rate > 0:
                    win_rate_change = recent_decisions.win_rate - older_decisions.win_rate
                    change_symbol = (
                        "↑" if win_rate_change > 0 else "↓" if win_rate_change < 0 else "→"
                    )
                    summary_parts.append(
                        f"- 胜率变化: {older_decisions.win_rate:.1%} {change_symbol} {recent_decisions.win_rate:.1%}"
                    )

            # 5. 生成交易建议提示
            summary_parts.append(self._generate_trading_hints(recent_market, recent_decisions))

            summary = "\n".join(summary_parts)

            # 6. 检查 token 数量，如果超限则进行智能压缩
            token_count = count_tokens_approximately(summary)

            if token_count > self.max_context_tokens:
                self.logger.logger.warning(
                    f"汇总超出 token 限制 ({token_count} > {self.max_context_tokens})，进行智能压缩..."
                )
                summary = self._smart_compress(summary, self.max_context_tokens)
                new_token_count = count_tokens_approximately(summary)
                self.logger.logger.info(f"智能压缩完成: {token_count} → {new_token_count} tokens")

            self.logger.logger.info(
                f"[汇总Agent V2.1] {symbol} 压缩完成，最终 token 数: {count_tokens_approximately(summary)}"
            )

            return summary

        except Exception as e:
            self.logger.logger.error(f"创建压缩汇总失败: {e}")
            return f"## {symbol} 历史汇总\n\n汇总生成失败: {str(e)}"

    def _translate_market_phase(self, phase: str) -> str:
        """翻译市场阶段"""
        translations = {
            "accumulation": "积累阶段（可能触底）",
            "markup": "上升阶段（趋势向上）",
            "distribution": "派发阶段（可能见顶）",
            "markdown": "下降阶段（趋势向下）",
            "unknown": "未知",
        }
        return translations.get(phase, phase)

    def _translate_volatility(self, level: str) -> str:
        """翻译波动性级别"""
        translations = {
            "extreme": "极高波动",
            "high": "高波动",
            "normal": "正常波动",
            "low": "低波动",
        }
        return translations.get(level, level)

    def _translate_trend(self, trend: str) -> str:
        """翻译趋势方向"""
        translations = {"rising": "上升", "falling": "下降", "stable": "稳定"}
        return translations.get(trend, trend)

    def _translate_macd(self, status: str) -> str:
        """翻译MACD状态"""
        translations = {"bullish": "看涨信号", "bearish": "看跌信号", "neutral": "中性"}
        return translations.get(status, status)

    def _generate_trading_hints(
        self, market: MarketTrendSummary, decisions: DecisionSummary
    ) -> str:
        """
        基于市场状态和决策历史生成交易提示

        Args:
            market: 市场汇总
            decisions: 决策汇总

        Returns:
            交易提示文本
        """
        hints = ["\n### 📋 历史经验提示"]

        # 基于市场阶段的提示
        if market.market_phase == "accumulation":
            hints.append("- 📈 积累阶段：注意潜在反转信号，可考虑分批建仓")
        elif market.market_phase == "distribution":
            hints.append("- 📉 派发阶段：注意风险，考虑减仓或设置更紧止损")
        elif market.market_phase == "markup":
            hints.append("- 🚀 上升阶段：趋势向好，可顺势操作")
        elif market.market_phase == "markdown":
            hints.append("- ⚠️ 下降阶段：谨慎操作，避免抄底")

        # 基于波动性的提示
        if market.volatility_level in ["extreme", "high"]:
            hints.append("- ⚡ 高波动环境：建议减小仓位，扩大止损距离")
        elif market.volatility_level == "low":
            hints.append("- 😴 低波动环境：可能即将突破，注意方向选择")

        # 基于决策效果的提示
        if decisions.decision_quality_score < 0.3:
            hints.append("- 🔴 近期决策效果不佳：建议更保守，减少交易频率")
        elif decisions.decision_quality_score > 0.7:
            hints.append("- 🟢 近期决策效果良好：可维持当前策略")

        if decisions.max_consecutive_losses >= 3:
            hints.append(
                f"- ⚠️ 出现连续{decisions.max_consecutive_losses}次亏损：建议暂停交易或降低仓位"
            )

        # 基于RSI的提示
        if market.rsi_status == "超买" and market.rsi_trend == "falling":
            hints.append("- 📊 RSI超买回落：注意可能的回调")
        elif market.rsi_status == "超卖" and market.rsi_trend == "rising":
            hints.append("- 📊 RSI超卖反弹：注意可能的反弹机会")

        if len(hints) == 1:
            hints.append("- 暂无特别提示")

        return "\n".join(hints)

    def _smart_compress(self, text: str, max_tokens: int) -> str:
        """
        智能压缩文本，优先保留关键信息

        Args:
            text: 原始文本
            max_tokens: 最大 token 数

        Returns:
            压缩后的文本
        """
        try:
            # 先尝试简单删除次要部分
            lines = text.split("\n")
            essential_keywords = ["趋势", "价格", "策略", "胜率", "风险", "提示"]
            important_lines = []
            optional_lines = []

            for line in lines:
                if (
                    any(kw in line for kw in essential_keywords)
                    or line.startswith("##")
                    or line.startswith("###")
                ):
                    important_lines.append(line)
                else:
                    optional_lines.append(line)

            # 首先用重要行构建
            result = "\n".join(important_lines)

            # 如果还有空间，逐步添加可选行
            for line in optional_lines:
                test_result = result + "\n" + line
                if count_tokens_approximately(test_result) <= max_tokens * 0.9:
                    result = test_result
                else:
                    break

            # 如果仍然超限，使用 LLM 压缩
            if count_tokens_approximately(result) > max_tokens:
                compress_prompt = f"""将以下内容压缩到约 {max_tokens // 2} 个 token，保留最关键信息：

{result}

要求：
1. 必须保留：趋势方向、关键价位、决策策略、胜率
2. 可以删除：详细理由、次要技术指标
3. 使用简洁表达"""

                messages = [
                    SystemMessage(content="你是信息压缩专家。"),
                    HumanMessage(content=compress_prompt),
                ]

                response = self.compression_llm.invoke(messages)
                result = response.content.strip()

            return result

        except Exception as e:
            self.logger.logger.warning(f"智能压缩失败，返回截断文本: {e}")
            # 降级方案：简单截断
            return text[: max_tokens * 4]  # 粗略估计每个 token 约 4 个字符


class DecisionHistory:
    """决策历史管理器 - 为每个交易对维护独立的决策历史（保持不变）"""

    def __init__(self, max_history: int = 50):
        """
        初始化决策历史管理器

        Args:
            max_history: 每个交易对保存的最大历史记录数
        """
        # 为每个交易对维护独立的历史记录 {symbol: [records]}
        self.histories: dict[str, list[dict[str, Any]]] = {}
        self.max_history = max_history

    def add_decision(
        self,
        symbol: str,
        decision: str,
        market_data: dict[str, Any],
        reason: str = "",
        action_details: dict[str, Any] | None = None,
    ):
        """
        添加决策记录

        Args:
            symbol: 交易对
            decision: 决策类型
            market_data: 市场数据
            reason: 决策原因
            action_details: 操作详情
        """
        if symbol not in self.histories:
            self.histories[symbol] = []

        data_ts = market_data.get("timestamp") if isinstance(market_data, dict) else None
        record = {
            # 使用数据时间而不是当前时间，便于回测报告准确反映交易时间
            "timestamp": data_ts if data_ts is not None else datetime.now(),
            "decision": decision,
            "market_data": market_data,
            "reason": reason,
            "action_details": action_details,
        }

        self.histories[symbol].append(record)

        # 保持历史记录数量限制
        if len(self.histories[symbol]) > self.max_history:
            self.histories[symbol] = self.histories[symbol][-self.max_history :]

    def get_recent_decisions(self, symbol: str, count: int = 10) -> list[dict[str, Any]]:
        """
        获取最近的N次决策

        Args:
            symbol: 交易对
            count: 记录数量

        Returns:
            决策记录列表（倒序，最新的在前）
        """
        if symbol not in self.histories:
            return []

        return list(reversed(self.histories[symbol][-count:]))

    def get_decisions_range(
        self, symbol: str, start_index: int, end_index: int
    ) -> list[dict[str, Any]]:
        """
        获取指定范围的决策记录

        Args:
            symbol: 交易对
            start_index: 起始索引（从最新往前数）
            end_index: 结束索引（从最新往前数）

        Returns:
            决策记录列表（倒序，最新的在前）
        """
        if symbol not in self.histories:
            return []

        history = self.histories[symbol]

        # 从后往前取
        if end_index > len(history):
            end_index = len(history)

        if start_index >= end_index:
            return []

        # 倒序返回
        return (
            list(reversed(history[-end_index:-start_index]))
            if start_index > 0
            else list(reversed(history[-end_index:]))
        )

    def get_history_count(self, symbol: str) -> int:
        """
        获取历史记录数量

        Args:
            symbol: 交易对

        Returns:
            记录数量
        """
        return len(self.histories.get(symbol, []))
