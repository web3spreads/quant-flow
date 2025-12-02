"""
国际化 (i18n) 模块
提供中英文文本资源
"""

from typing import Dict, Any


# 语言资源字典
LANGUAGE_RESOURCES = {
    "zh": {
        # 持仓状态相关
        "no_position": "无持仓 ❌",
        "position_details": "持仓详情",
        "basic_info": "基础信息",
        "position_side": "持仓方向",
        "long": "做多 (Long)",
        "short": "做空 (Short)",
        "position_size": "持仓数量",
        "entry_price": "入场价格",
        "current_price": "当前价格",
        "leverage": "杠杆倍数",
        "pnl_status": "盈亏状况",
        "price_change": "价格变化",
        "distance_from_entry": "距离入场价",
        "unrealized_pnl": "未实现盈亏",
        "position_value": "持仓价值",
        "margin_used": "使用保证金",
        "liquidation_price": "清算价格",
        "distance_to_liquidation": "距离清算",
        "important_notice": "重要提示",
        "profit_status": "盈利",
        "flat_status": "持平",
        "loss_status": "亏损",
        "current_status_notice": "当前{status}状态，请根据市场情况决定是否止盈/止损",
        "leverage_risk": "杠杆倍数为 {leverage}x，风险{risk_level}",
        "risk_high": "较高",
        "risk_moderate": "适中",
        "watch_price_notice": "请关注价格走势，及时调整策略",

        # 账户余额相关
        "account_balance": "账户余额（实时）",
        "total_value": "账户总价值",
        "occupied_margin": "已占用保证金",
        "available_balance": "可用余额",
        "unrealized_pnl_total": "未实现盈亏",
        "balance_notice_title": "重要提示",
        "balance_check_notice": "你必须根据可用余额决定是否开仓",
        "insufficient_balance_notice": "如果可用余额不足以支持交易，必须选择 do_nothing",
        "large_loss_notice": "关注未实现盈亏，如果亏损较大应更谨慎",
        "balance_check_for_dca": "你必须根据可用余额决定是否定投",
        "insufficient_balance_dca": "如果可用余额不足，必须选择 do_nothing",

        # 历史决策相关
        "historical_summary": "历史决策汇总",
        "historical_hint": "以上是你过去的决策记录汇总，可以帮助你理解市场演变和之前的策略。但请基于当前市场数据做出独立判断。",

        # 趋势相关
        "multi_timeframe_trends": "多时间周期趋势",
        "daily": "日线",
        "4h": "4小时",
        "1h": "1小时",
        "15m": "15分钟",
        "1m": "1分钟",

        # 现货相关
        "has_spot": "已持有 ✅",
        "no_spot": "未持有 ❌",

        # Recommendation related
        "recommendation_reason_default": "未提供原因",
        "recommendation_timestamp_default": "未知时间",

        # Yes/No indicators
        "yes": "是 ✅",
        "no": "否 ❌",
    },
    "en": {
        # Position status related
        "no_position": "No Position ❌",
        "position_details": "Position Details",
        "basic_info": "Basic Information",
        "position_side": "Position Side",
        "long": "Long",
        "short": "Short",
        "position_size": "Position Size",
        "entry_price": "Entry Price",
        "current_price": "Current Price",
        "leverage": "Leverage",
        "pnl_status": "P&L Status",
        "price_change": "Price Change",
        "distance_from_entry": "From Entry Price",
        "unrealized_pnl": "Unrealized P&L",
        "position_value": "Position Value",
        "margin_used": "Margin Used",
        "liquidation_price": "Liquidation Price",
        "distance_to_liquidation": "To Liquidation",
        "important_notice": "Important Notice",
        "profit_status": "profit",
        "flat_status": "flat",
        "loss_status": "loss",
        "current_status_notice": "Currently in {status} status. Consider taking profit/loss based on market conditions",
        "leverage_risk": "Leverage at {leverage}x, risk level is {risk_level}",
        "risk_high": "high",
        "risk_moderate": "moderate",
        "watch_price_notice": "Monitor price movements and adjust strategy accordingly",

        # Account balance related
        "account_balance": "Account Balance (Real-time)",
        "total_value": "Total Account Value",
        "occupied_margin": "Occupied Margin",
        "available_balance": "Available Balance",
        "unrealized_pnl_total": "Unrealized P&L",
        "balance_notice_title": "Important Notice",
        "balance_check_notice": "You must decide whether to open positions based on available balance",
        "insufficient_balance_notice": "If available balance is insufficient to support trading, you must choose do_nothing",
        "large_loss_notice": "Monitor unrealized P&L; be more cautious if losses are significant",
        "balance_check_for_dca": "You must decide whether to invest based on available balance",
        "insufficient_balance_dca": "If available balance is insufficient, you must choose do_nothing",

        # Historical decisions related
        "historical_summary": "Historical Decision Summary",
        "historical_hint": "The above is a summary of your past decision records, which can help you understand market evolution and previous strategies. However, please make independent judgments based on current market data.",

        # Trend related
        "multi_timeframe_trends": "Multi-Timeframe Trends",
        "daily": "Daily",
        "4h": "4-Hour",
        "1h": "1-Hour",
        "15m": "15-Minute",
        "1m": "1-Minute",

        # Spot related
        "has_spot": "Held ✅",
        "no_spot": "Not Held ❌",

        # Recommendation related
        "recommendation_reason_default": "No reason provided",
        "recommendation_timestamp_default": "Unknown time",

        # Yes/No indicators
        "yes": "Yes ✅",
        "no": "No ❌",
    }
}


def get_text(language: str, key: str, **kwargs) -> str:
    """
    获取指定语言的文本

    Args:
        language: 语言代码 ("zh" 或 "en")
        key: 文本键
        **kwargs: 格式化参数

    Returns:
        格式化后的文本，如果键不存在则返回键本身
    """
    # 默认使用中文
    if language not in LANGUAGE_RESOURCES:
        language = "zh"

    text = LANGUAGE_RESOURCES[language].get(key, key)

    # 如果有格式化参数，进行格式化
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, ValueError):
            pass

    return text


def get_timeframe_name(language: str, timeframe: str) -> str:
    """
    获取时间周期的本地化名称

    Args:
        language: 语言代码
        timeframe: 时间周期标识 ("daily", "4h", "1h", "15m", "1m")

    Returns:
        本地化的时间周期名称
    """
    return get_text(language, timeframe)
