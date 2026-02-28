"""
永续合约特有因子定义（v2 重构版）

定义加密货币永续合约独有的特征，这些特征在传统股票市场中不存在，
包括资金费率、未平仓合约量、多空比等。

v2 改进：
- 使用统一英文命名
- 精简特征定义，与 handler.py 保持一致
- 实际计算逻辑在 handler.py 中，本文件仅做定义和注册
"""

# ============================================================
# 永续合约原始特征列定义
# ============================================================

# 永续合约特有的原始数据列名（从 Hyperliquid API 收集）
PERPETUAL_RAW_COLUMNS = [
    "funding_rate",  # 资金费率（每 8 小时结算一次）
    "open_interest",  # 未平仓合约量（美元计价）
    "premium",  # 溢价率（标记价格 vs 指数价格）
]


# ============================================================
# 永续合约衍生因子配置（v2 精简版）
# ============================================================

PERPETUAL_FEATURE_CONFIG = [
    # --- 资金费率因子 ---
    ("$funding_rate", "FR"),
    ("Mean($funding_rate, 8)", "FR_MA8"),
    ("Mean($funding_rate, 24)", "FR_MA24"),
    ("Std($funding_rate, 24)", "FR_STD24"),
    ("$funding_rate - Ref($funding_rate, 1)", "FR_DIFF1"),
    ("$funding_rate - Mean($funding_rate, 24)", "FR_DEVMA"),
    # --- 未平仓量因子 ---
    ("Log($open_interest + 1)", "OI_LOG"),
    ("$open_interest / Ref($open_interest, 1) - 1", "OI_ROC1"),
    ("$open_interest / Ref($open_interest, 24) - 1", "OI_ROC24"),
    ("$open_interest / ($volume + 1)", "OI_VOL_RATIO"),
    ("$open_interest / Mean($open_interest, 24) - 1", "OI_DEVMA"),
    # --- 溢价率因子 ---
    ("$premium", "PREM"),
    ("Mean($premium, 24)", "PREM_MA24"),
    ("Std($premium, 24)", "PREM_STD24"),
]


# ============================================================
# Alpha158 基础因子配置（v2 精简版）
# ============================================================

# K线形态因子
KBAR_FEATURE_CONFIG = [
    ("($close - $open) / $open", "KMID"),
    ("($high - $low) / $open", "KLEN"),
    ("($close - $open) / ($high - $low + 1e-12)", "KSFT"),
    ("($high - Max($open, $close)) / $open", "KUP"),
    ("(Min($open, $close) - $low) / $open", "KLOW"),
    ("($high - Max($open, $close)) / ($high - $low + 1e-12)", "KSHUP"),
    ("(Min($open, $close) - $low) / ($high - $low + 1e-12)", "KSHDN"),
]

# 价格动量因子（精简窗口：去掉30/60）
PRICE_FEATURE_CONFIG = []
for _window in [1, 2, 3, 5, 10, 20]:
    PRICE_FEATURE_CONFIG.append((f"Ref($close, {_window}) / $close - 1", f"ROC_{_window}"))

# 均线偏离因子（精简窗口）
MA_FEATURE_CONFIG = []
for _window in [5, 10, 20]:
    MA_FEATURE_CONFIG.append((f"Mean($close, {_window}) / $close - 1", f"MA_BIAS_{_window}"))

# 波动率因子（精简窗口）
VOLATILITY_FEATURE_CONFIG = []
for _window in [5, 10, 20]:
    VOLATILITY_FEATURE_CONFIG.append(
        (f"Std($close, {_window}) / Mean($close, {_window})", f"CV_{_window}")
    )

# 滚动统计因子（精简窗口）
ROLLING_FEATURE_CONFIG = []
for _window in [5, 10, 20]:
    ROLLING_FEATURE_CONFIG.append(
        (f"Std(Ref($close, 1) / $close - 1, {_window})", f"VSTD_{_window}")
    )
    ROLLING_FEATURE_CONFIG.append(
        (f"Std($volume, {_window}) / (Mean($volume, {_window}) + 1e-12)", f"VWSTD_{_window}")
    )
    ROLLING_FEATURE_CONFIG.append(
        (
            f"($close - Min($low, {_window})) / (Max($high, {_window}) - Min($low, {_window}) + 1e-12)",
            f"POSITION_{_window}",
        )
    )

# 相关性因子（精简窗口）
CORRELATION_FEATURE_CONFIG = [
    ("Corr($close, Log($volume + 1), 5)", "CORR_PV_5"),
    ("Corr($close, Log($volume + 1), 10)", "CORR_PV_10"),
    ("Corr($close, Log($volume + 1), 20)", "CORR_PV_20"),
]

# 新增技术指标因子
# 注意：以下表达式为伪 QLib 表达式，仅做文档/注册用途。
# 实际计算逻辑在 handler.py 的 CryptoAlpha158.calculate_features() 中，
# 并非通过 QLib 表达式引擎执行。
TECHNICAL_FEATURE_CONFIG = [
    ("RSI($close, 14)", "RSI_14"),
    ("MACD_LINE($close, 12, 26) / $close", "MACD_LINE"),
    ("MACD_SIGNAL($close, 12, 26, 9) / $close", "MACD_SIGNAL"),
    ("MACD_HIST($close, 12, 26, 9) / $close", "MACD_HIST"),
    ("BB_PCTB($close, 20, 2)", "BB_PCTB"),
    ("BB_WIDTH($close, 20, 2)", "BB_WIDTH"),
    ("ATR($high, $low, $close, 14) / $close", "ATR_14"),
    ("OBV_ROC($close, $volume, 10)", "OBV_ROC_10"),
    ("$volume / Mean($volume, 24) - 1", "VOL_BIAS_24"),
]


def get_all_feature_config(include_perpetual: bool = True) -> list[tuple[str, str]]:
    """
    获取完整的因子配置列表（v2 版本）

    Args:
        include_perpetual: 是否包含永续合约特有因子

    Returns:
        因子配置列表，每个元素为 (表达式, 因子名称) 的元组
    """
    features = []
    features.extend(KBAR_FEATURE_CONFIG)
    features.extend(PRICE_FEATURE_CONFIG)
    features.extend(MA_FEATURE_CONFIG)
    features.extend(VOLATILITY_FEATURE_CONFIG)
    features.extend(ROLLING_FEATURE_CONFIG)
    features.extend(CORRELATION_FEATURE_CONFIG)
    features.extend(TECHNICAL_FEATURE_CONFIG)

    if include_perpetual:
        features.extend(PERPETUAL_FEATURE_CONFIG)

    return features


def get_feature_expressions(include_perpetual: bool = True) -> list[str]:
    """
    获取所有因子的表达式列表

    Args:
        include_perpetual: 是否包含永续合约特有因子

    Returns:
        表达式字符串列表
    """
    return [expr for expr, _name in get_all_feature_config(include_perpetual)]


