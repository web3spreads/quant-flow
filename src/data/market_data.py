"""
Hyperliquid 市场数据获取模块
使用 Hyperliquid Info API 获取K线和市场数据
"""

from datetime import datetime

import pandas as pd
from hyperliquid.info import Info
from hyperliquid.utils import constants


class MarketDataFetcher:
    """Hyperliquid 市场数据获取器"""

    def __init__(
        self,
        testnet: bool = False
    ):
        """
        初始化市场数据获取器

        Args:
            testnet: 是否使用测试网
        """
        self.testnet = testnet
        self.base_url = constants.TESTNET_API_URL if testnet else constants.MAINNET_API_URL

        # 初始化 Info API
        self.info = Info(self.base_url, skip_ws=True)

        print(f"📊 市场数据获取器初始化完成 ({'测试网' if testnet else '主网'})")

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "15m",
        limit: int = 100
    ) -> pd.DataFrame | None:
        """
        获取 OHLCV K线数据

        Args:
            symbol: 交易对符号（如 'ETH'）
            timeframe: 时间周期（1m, 5m, 15m, 1h, 4h, 1d）
            limit: 获取的K线数据量

        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume
        """
        try:
            # 验证交易对是否存在
            if not self.is_symbol_available(symbol):
                print(f"❌ Hyperliquid 不支持交易对 '{symbol}'")
                available = self.get_available_symbols()
                if available:
                    print(f"💡 可用的交易对示例: {', '.join(available[:10])}")
                return None

            # 计算时间范围
            end_time = int(datetime.now().timestamp() * 1000)

            # 根据 timeframe 计算开始时间
            timeframe_minutes = self._parse_timeframe(timeframe)
            start_time = end_time - (timeframe_minutes * 60 * 1000 * limit)

            # 获取K线数据
            candles = self.info.candles_snapshot(
                name=symbol,
                interval=timeframe,
                startTime=start_time,
                endTime=end_time
            )

            if not candles:
                print(f"⚠️ 没有获取到 {symbol} 的K线数据")
                return None

            # 转换为 DataFrame
            df = pd.DataFrame(candles)

            # 重命名列（Hyperliquid 返回的字段）
            # 格式: {'t': start_time_ms, 'T': end_time_ms, 'o': open, 'h': high, 'l': low, 'c': close, 'v': volume, 'n': trades}
            # 只保留需要的列并重命名
            column_mapping = {
                't': 'timestamp',  # 使用开始时间
                'o': 'open',
                'h': 'high',
                'l': 'low',
                'c': 'close',
                'v': 'volume'
            }

            # 只选择存在的列
            df = df[[col for col in column_mapping if col in df.columns]]
            df = df.rename(columns=column_mapping)

            # 确保有必需的列
            required_cols = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
            if not all(col in df.columns for col in required_cols):
                print(f"⚠️ K线数据缺少必需的列: {df.columns.tolist()}")
                return None

            # 转换数据类型
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = pd.to_numeric(df[col], errors='coerce')

            # 按时间排序
            df = df.sort_values('timestamp').reset_index(drop=True)

            # 只保留需要的列
            df = df[required_cols]

            print(f"✅ 获取 {symbol} K线数据: {len(df)} 条 ({timeframe})")

            return df

        except Exception as e:
            print(f"❌ 获取K线数据失败: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _parse_timeframe(self, timeframe: str) -> int:
        """
        解析时间周期为分钟数

        Args:
            timeframe: 如 '15m', '1h', '4h', '1d'

        Returns:
            分钟数
        """
        unit = timeframe[-1]
        value = int(timeframe[:-1])

        if unit == 'm':
            return value
        elif unit == 'h':
            return value * 60
        elif unit == 'd':
            return value * 60 * 24
        else:
            return 15  # 默认 15 分钟

    def get_ticker(self, symbol: str) -> dict | None:
        """
        获取 Ticker 信息（当前价格等）

        Args:
            symbol: 交易对符号

        Returns:
            {
                'symbol': str,
                'last': float,  # 最新价
                'bid': float,   # 买一价
                'ask': float,   # 卖一价
                'volume': float # 24h成交量
            }
        """
        try:
            # 获取所有市场的中间价
            all_mids = self.info.all_mids()

            if symbol not in all_mids:
                print(f"⚠️ 找不到交易对 {symbol}")
                return None

            price = float(all_mids[symbol])

            # 获取元数据（包含更多信息）
            meta = self.info.meta()
            universe = meta.get('universe', [])

            # 查找对应的交易对信息
            asset_info = next((a for a in universe if a['name'] == symbol), None)

            ticker = {
                'symbol': symbol,
                'last': price,
                'bid': price,  # Hyperliquid 只提供中间价，bid/ask 暂设为相同
                'ask': price,
                'volume': 0  # 需要从其他接口获取
            }

            if asset_info:
                # 添加更多信息
                ticker['szDecimals'] = asset_info.get('szDecimals', 0)
                ticker['maxLeverage'] = asset_info.get('maxLeverage', 0)

            return ticker

        except Exception as e:
            print(f"❌ 获取Ticker失败: {e}")
            return None

    def get_available_symbols(self) -> list[str]:
        """
        获取所有可用的交易对

        Returns:
            交易对列表
        """
        try:
            meta = self.info.meta()
            universe = meta.get('universe', [])
            symbols = [asset['name'] for asset in universe]
            return symbols
        except Exception as e:
            print(f"❌ 获取交易对列表失败: {e}")
            return []

    def is_symbol_available(self, symbol: str) -> bool:
        """
        验证交易对是否可用

        Args:
            symbol: 交易对符号

        Returns:
            True 如果交易对可用，否则 False
        """
        try:
            available_symbols = self.get_available_symbols()
            return symbol in available_symbols
        except Exception as e:
            print(f"❌ 验证交易对失败: {e}")
            return False

    def get_funding_rate(self, symbol: str) -> float | None:
        """
        获取资金费率

        Args:
            symbol: 交易对符号

        Returns:
            资金费率
        """
        try:
            meta = self.info.meta()
            universe = meta.get('universe', [])
            asset_info = next((a for a in universe if a['name'] == symbol), None)

            if asset_info and 'funding' in asset_info:
                return float(asset_info['funding'])
            return None

        except Exception as e:
            print(f"❌ 获取资金费率失败: {e}")
            return None

    def fetch_ohlcv_multi_timeframe(
        self,
        symbol: str,
        timeframes: list[str] = None
    ) -> dict:
        """
        获取多个时间周期的K线数据

        Args:
            symbol: 交易对符号
            timeframes: 时间周期列表

        Returns:
            {timeframe: DataFrame}
        """
        if not timeframes:
            timeframes = ['1m', '15m', '1h', '4h', '1d']

        result = {}
        for tf in timeframes:
            df = self.fetch_ohlcv(symbol, timeframe=tf, limit=100)
            if df is not None:
                result[tf] = df

        return result
