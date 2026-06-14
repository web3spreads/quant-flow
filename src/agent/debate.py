"""
多空辩论模块（Bull/Bear Debate）

基于 TradingAgents (arXiv:2412.20138) 的思路，
让两个独立视角的 Agent 分别从看多和看空角度分析，
消除单 Agent 的确认偏见。

使用精简的市场摘要（~200 tokens）而非完整 prompt，控制成本。
"""

import logging
from typing import Any

from pydantic_ai import Agent

from src.llm.llm_client import wrap_llm_client

logger = logging.getLogger(__name__)

# 多头研究员系统提示
BULL_SYSTEM_PROMPT = """你是一位坚定的多头研究员。你的任务是从看多的角度分析市场数据。
无论市场状况如何，你必须尽力找出支持做多的论据。
输出格式（严格遵守，不要输出其他内容）：
论点1: [具体论据，引用数据]
论点2: [具体论据，引用数据]
论点3: [具体论据，引用数据]
多头置信度: [0.0-1.0]"""

# 空头研究员系统提示
BEAR_SYSTEM_PROMPT = """你是一位坚定的空头研究员。你的任务是从看空/观望的角度分析市场数据。
无论市场状况如何，你必须尽力找出支持做空或观望的论据。
输出格式（严格遵守，不要输出其他内容）：
论点1: [具体论据，引用数据]
论点2: [具体论据，引用数据]
论点3: [具体论据，引用数据]
空头置信度: [0.0-1.0]"""


def build_debate_context(
    symbol: str,
    market_data: dict[str, Any],
    enriched_data: dict[str, Any] | None = None,
    multi_timeframe_trends: dict[str, str] | None = None,
) -> str:
    """
    构建精简的辩论上下文（控制在 ~200 tokens 以内）
    """
    price = market_data.get("current_price", 0)
    rsi = market_data.get("rsi", 50)
    macd = market_data.get("macd", 0)
    macd_signal = market_data.get("macd_signal", 0)

    ed = enriched_data or {}
    funding_signal = ed.get("funding_rate_signal", "")
    fear_greed = ed.get("fear_greed_sentiment", "")
    ema20 = ed.get("current_ema20", price)
    ema20_4h = ed.get("ema_20_4h", 0)
    ema50_4h = ed.get("ema_50_4h", 0)
    composite = ed.get("composite_signal", "")
    h4_trend = ed.get("h4_trend_analysis", "")
    volume = ed.get("volume_analysis", "")

    trends_str = ""
    if multi_timeframe_trends:
        trends_str = ", ".join(f"{k}:{v}" for k, v in multi_timeframe_trends.items())

    return f"""{symbol} 市场快照:
价格=${price} | EMA20=${ema20} | 偏离={(price / ema20 - 1) * 100 if ema20 else 0:.2f}%
RSI={rsi:.1f} | MACD={macd:.4f} (信号线={macd_signal:.4f})
4H EMA20=${ema20_4h} | 4H EMA50=${ema50_4h}
资金费率: {funding_signal}
恐惧贪婪: {fear_greed}
综合信号: {composite}
4H趋势: {h4_trend}
成交量: {volume}
多周期: {trends_str}"""


def run_bull_bear_debate(
    llm,
    symbol: str,
    market_data: dict[str, Any],
    enriched_data: dict[str, Any] | None = None,
    multi_timeframe_trends: dict[str, str] | None = None,
) -> str:
    """
    执行多空辩论，返回格式化的辩论摘要

    Args:
        llm: Pydantic AI Model 实例
        symbol: 交易对
        market_data: 市场数据
        enriched_data: 增强数据
        multi_timeframe_trends: 多周期趋势

    Returns:
        格式化的辩论摘要文本，可直接注入 prompt
    """
    context = build_debate_context(symbol, market_data, enriched_data, multi_timeframe_trends)
    llm = wrap_llm_client(llm)

    try:
        # 使用 Pydantic AI Agent 分别代表 Bull & Bear 进行辩论
        bull_agent = Agent(llm, system_prompt=BULL_SYSTEM_PROMPT)
        bear_agent = Agent(llm, system_prompt=BEAR_SYSTEM_PROMPT)

        bull_result = bull_agent.run_sync(context)
        bull_text = bull_result.output.strip() if bull_result.output else "（多头分析失败）"

        bear_result = bear_agent.run_sync(context)
        bear_text = bear_result.output.strip() if bear_result.output else "（空头分析失败）"

    except Exception as e:
        logger.warning("多空辩论 LLM 调用失败: %s", e)
        return ""

    return f"""## ⚔️ 多空辩论（独立分析，消除确认偏见）

**📈 多头研究员：**
{bull_text}

**📉 空头研究员：**
{bear_text}

> ⚠️ 你必须综合双方论点做出最终决策，不可只采信一方。如果双方置信度接近，倾向于观望。
"""
