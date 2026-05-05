"""
K 线节拍对齐工具
提供 timeframe 解析和下一根 K 线收盘时间计算，用于决策循环与 K 线边界对齐。
"""

import re
import time

# timeframe 后缀到秒数的映射
_UNIT_SECONDS = {
    "m": 60,
    "h": 3600,
    "d": 86400,
}

_TF_PATTERN = re.compile(r"^(\d+)([mhd])$")


def timeframe_to_seconds(tf: str) -> int:
    """
    将 timeframe 字符串转换为秒数

    Args:
        tf: K 线周期字符串，如 "1m", "5m", "15m", "30m", "1h", "4h", "1d"

    Returns:
        对应的秒数

    Raises:
        ValueError: 无法解析的 timeframe 格式
    """
    match = _TF_PATTERN.match(tf.strip().lower())
    if not match:
        raise ValueError(f"无法解析的 timeframe 格式: {tf!r}，期望格式如 '15m', '1h', '4h', '1d'")
    value = int(match.group(1))
    unit = match.group(2)
    return value * _UNIT_SECONDS[unit]


def next_candle_close_ts(timeframe: str, now_ts: float | None = None) -> float:
    """
    计算下一根 K 线收盘的 UTC 时间戳

    基于整数对齐：K 线收盘时间是 period 的整数倍（UTC 基准）。
    例如 1h K 线在 :00 收盘，4h K 线在 00:00/04:00/08:00/... 收盘。

    Args:
        timeframe: K 线周期字符串
        now_ts: 当前 UTC 时间戳（秒），默认 time.time()

    Returns:
        下一根 K 线收盘的 UTC 时间戳（秒）
    """
    period = timeframe_to_seconds(timeframe)
    now = now_ts if now_ts is not None else time.time()
    # 对齐到 period 的整数倍：当前所在区间的下一个边界
    return (now // period + 1) * period
