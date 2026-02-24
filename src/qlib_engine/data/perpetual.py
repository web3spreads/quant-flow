"""
永续合约特有因子定义

定义加密货币永续合约独有的特征，这些特征在传统股票市场中不存在，
包括资金费率、未平仓合约量、多空比等。
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
# 永续合约衍生因子配置
# ============================================================

# 基于永续合约原始数据构建的衍生因子
# 使用 QLib 表达式引擎语法
PERPETUAL_FEATURE_CONFIG = [
    # --- 资金费率因子 ---
    # 资金费率原值
    ("$funding_rate", "资金费率"),
    # 资金费率移动平均（平滑后的趋势）
    ("Mean($funding_rate, 8)", "资金费率_8期均值"),
    ("Mean($funding_rate, 24)", "资金费率_24期均值"),
    # 资金费率标准差（波动性）
    ("Std($funding_rate, 24)", "资金费率_24期标准差"),
    # 资金费率偏移变化
    ("$funding_rate - Ref($funding_rate, 1)", "资金费率_1期变化"),
    ("$funding_rate - Mean($funding_rate, 24)", "资金费率_偏离均值"),
    # --- 未平仓量因子 ---
    # 未平仓量原值（对数化）
    ("Log($open_interest + 1)", "未平仓量_对数"),
    # 未平仓量变化率
    ("$open_interest / Ref($open_interest, 1) - 1", "未平仓量_1期变化率"),
    ("$open_interest / Ref($open_interest, 24) - 1", "未平仓量_24期变化率"),
    # 未平仓量与成交量的比率
    ("$open_interest / ($volume + 1)", "未平仓量_成交量比"),
    # 未平仓量移动平均
    ("Mean($open_interest, 24)", "未平仓量_24期均值"),
    # 未平仓量趋势强度
    ("$open_interest / Mean($open_interest, 24) - 1", "未平仓量_偏离均值"),
    # --- 溢价率因子 ---
    ("$premium", "溢价率"),
    ("Mean($premium, 24)", "溢价率_24期均值"),
    ("Std($premium, 24)", "溢价率_24期标准差"),
    # --- 价量关系因子（增强版）---
    # 成交量异常检测
    ("$volume / Mean($volume, 24) - 1", "成交量_偏离24期均值"),
    ("$volume / Mean($volume, 168) - 1", "成交量_偏离168期均值"),
    # 价量背离指标
    ("Corr($close, $volume, 24)", "价量相关性_24期"),
]


# ============================================================
# Alpha158 基础因子配置（适配加密货币）
# ============================================================

# K线形态因子
KBAR_FEATURE_CONFIG = [
    # K线基本形态
    ("($close - $open) / $open", "KMID"),  # K线中点（涨跌幅）
    ("($high - $low) / $open", "KLEN"),  # K线长度（振幅）
    ("($close - $open) / ($high - $low + 1e-12)", "KSFT"),  # K线偏移
    ("($high - Max($open, $close)) / $open", "KUP"),  # 上影线
    ("(Min($open, $close) - $low) / $open", "KLOW"),  # 下影线
    ("($high - Max($open, $close)) / ($high - $low + 1e-12)", "KSHUP"),  # 上影线比例
    ("(Min($open, $close) - $low) / ($high - $low + 1e-12)", "KSHDN"),  # 下影线比例
]

# 价格动量因子（多时间窗口）
PRICE_FEATURE_CONFIG = []
for _window in [1, 2, 3, 5, 10, 20, 30, 60]:
    PRICE_FEATURE_CONFIG.append((f"Ref($close, {_window}) / $close - 1", f"ROC_{_window}"))

# 均线偏离因子
MA_FEATURE_CONFIG = []
for _window in [5, 10, 20, 30, 60]:
    MA_FEATURE_CONFIG.append((f"Mean($close, {_window}) / $close - 1", f"MA_偏离_{_window}"))

# 波动率因子
VOLATILITY_FEATURE_CONFIG = []
for _window in [5, 10, 20, 30, 60]:
    VOLATILITY_FEATURE_CONFIG.append(
        (f"Std($close, {_window}) / Mean($close, {_window})", f"CV_{_window}")
    )

# 滚动统计因子
ROLLING_FEATURE_CONFIG = []
for _window in [5, 10, 20, 30, 60]:
    # 收益率标准差
    ROLLING_FEATURE_CONFIG.append(
        (f"Std(Ref($close, 1) / $close - 1, {_window})", f"VSTD_{_window}")
    )
    # 成交量标准差
    ROLLING_FEATURE_CONFIG.append(
        (f"Std($volume, {_window}) / (Mean($volume, {_window}) + 1e-12)", f"VWSTD_{_window}")
    )
    # 最高价/最低价位置
    ROLLING_FEATURE_CONFIG.append(
        (
            f"($close - Min($low, {_window})) / (Max($high, {_window}) - Min($low, {_window}) + 1e-12)",
            f"POSITION_{_window}",
        )
    )

# 相关性因子
CORRELATION_FEATURE_CONFIG = [
    ("Corr($close, Log($volume + 1), 5)", "价量相关性_5"),
    ("Corr($close, Log($volume + 1), 10)", "价量相关性_10"),
    ("Corr($close, Log($volume + 1), 20)", "价量相关性_20"),
    ("Corr($close, Log($volume + 1), 60)", "价量相关性_60"),
]


def get_all_feature_config(include_perpetual: bool = True) -> list[tuple[str, str]]:
    """
    获取完整的因子配置列表

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

    if include_perpetual:
        features.extend(PERPETUAL_FEATURE_CONFIG)

    return features


def get_feature_expressions(include_perpetual: bool = True) -> list[str]:
    """
    获取所有因子的表达式列表（用于 QLib DataLoader）

    Args:
        include_perpetual: 是否包含永续合约特有因子

    Returns:
        表达式字符串列表
    """
    return [expr for expr, _name in get_all_feature_config(include_perpetual)]


def get_feature_names(include_perpetual: bool = True) -> list[str]:
    """
    获取所有因子的名称列表

    Args:
        include_perpetual: 是否包含永续合约特有因子

    Returns:
        因子名称列表
    """
    return [name for _expr, name in get_all_feature_config(include_perpetual)]
