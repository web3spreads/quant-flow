"""
加密货币交易日历

加密货币市场 24/7 不间断交易，没有休市日。
本模块生成连续的交易时间序列，供 QLib 数据层使用。
"""

import logging
from datetime import datetime, timedelta

import pandas as pd

logger = logging.getLogger("QuantFlow.QLib")


class CryptoCalendar:
    """
    加密货币 24/7 交易日历生成器

    与传统股票市场不同，加密货币没有休市日和交易时段限制。
    本类生成连续的时间序列，适配 QLib 的日历系统。
    """

    # 支持的频率及其对应的时间间隔
    FREQ_MAP = {
        "1min": timedelta(minutes=1),
        "5min": timedelta(minutes=5),
        "15min": timedelta(minutes=15),
        "30min": timedelta(minutes=30),
        "1h": timedelta(hours=1),
        "4h": timedelta(hours=4),
        "1d": timedelta(days=1),
    }

    # Hyperliquid timeframe 到日历频率的映射
    TIMEFRAME_TO_FREQ = {
        "1m": "1min",
        "5m": "5min",
        "15m": "15min",
        "30m": "30min",
        "1h": "1h",
        "4h": "4h",
        "1d": "1d",
    }

    def __init__(self, freq: str = "1h"):
        """
        初始化日历生成器

        Args:
            freq: 时间频率，支持 1min/5min/15min/30min/1h/4h/1d
        """
        if freq not in self.FREQ_MAP:
            raise ValueError(f"不支持的频率: {freq}，支持: {list(self.FREQ_MAP.keys())}")
        self.freq = freq
        self.delta = self.FREQ_MAP[freq]

    @classmethod
    def from_timeframe(cls, timeframe: str) -> "CryptoCalendar":
        """
        从 Hyperliquid timeframe 格式创建日历

        Args:
            timeframe: Hyperliquid 格式的时间周期（如 '1h', '4h', '1d'）
        """
        freq = cls.TIMEFRAME_TO_FREQ.get(timeframe)
        if freq is None:
            raise ValueError(f"不支持的 timeframe: {timeframe}")
        return cls(freq=freq)

    def generate(
        self,
        start: str | datetime,
        end: str | datetime,
    ) -> list[datetime]:
        """
        生成连续的交易时间序列

        Args:
            start: 开始时间（字符串格式 'YYYY-MM-DD' 或 datetime）
            end: 结束时间

        Returns:
            时间点列表
        """
        if isinstance(start, str):
            start = pd.Timestamp(start)
        if isinstance(end, str):
            end = pd.Timestamp(end)

        timestamps = []
        current = start
        while current <= end:
            timestamps.append(current)
            current += self.delta

        logger.info(f"生成加密货币日历: {len(timestamps)} 个时间点 ({self.freq}), "
                     f"{start.strftime('%Y-%m-%d')} ~ {end.strftime('%Y-%m-%d')}")
        return timestamps

    def generate_index(
        self,
        start: str | datetime,
        end: str | datetime,
    ) -> pd.DatetimeIndex:
        """
        生成 pandas DatetimeIndex 格式的日历

        Args:
            start: 开始时间
            end: 结束时间

        Returns:
            DatetimeIndex
        """
        timestamps = self.generate(start, end)
        return pd.DatetimeIndex(timestamps, name="datetime")

    def get_trading_dates(
        self,
        start: str | datetime,
        end: str | datetime,
    ) -> list[str]:
        """
        获取交易日期列表（日频，用于 QLib instruments 文件）

        Args:
            start: 开始日期
            end: 结束日期

        Returns:
            日期字符串列表 ['2024-01-01', '2024-01-02', ...]
        """
        if isinstance(start, str):
            start = pd.Timestamp(start)
        if isinstance(end, str):
            end = pd.Timestamp(end)

        dates = []
        current = start.normalize()  # 去掉时间部分
        end_date = end.normalize()

        while current <= end_date:
            dates.append(current.strftime("%Y-%m-%d"))
            current += timedelta(days=1)

        return dates
