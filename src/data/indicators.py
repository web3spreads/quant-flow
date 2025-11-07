"""
技术指标计算模块
使用纯 pandas/numpy 实现，无需额外依赖
"""

import pandas as pd
import numpy as np
from typing import Dict, Any, List


class TechnicalIndicators:
    """技术指标计算器 - 纯 pandas/numpy 实现"""

    @staticmethod
    def calculate_ma(df: pd.DataFrame, periods: List[int]) -> pd.DataFrame:
        """
        计算简单移动平均线 (Simple Moving Average)

        Args:
            df: OHLCV DataFrame
            periods: MA 周期列表，如 [7, 25, 99]

        Returns:
            添加了 MA 列的 DataFrame
        """
        for period in periods:
            df[f'ma_{period}'] = df['close'].rolling(window=period).mean()
        return df

    @staticmethod
    def calculate_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """
        计算相对强弱指数 (Relative Strength Index)

        RSI = 100 - (100 / (1 + RS))
        其中 RS = 平均涨幅 / 平均跌幅

        Args:
            df: OHLCV DataFrame
            period: RSI 周期，默认 14

        Returns:
            添加了 RSI 列的 DataFrame
        """
        # 计算价格变化
        delta = df['close'].diff()

        # 分离涨跌
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)

        # 计算平均涨跌幅（使用 Wilder's smoothing）
        avg_gain = gain.rolling(window=period).mean()
        avg_loss = loss.rolling(window=period).mean()

        # 计算 RS 和 RSI
        rs = avg_gain / avg_loss
        df['rsi'] = 100 - (100 / (1 + rs))

        return df

    @staticmethod
    def calculate_macd(
        df: pd.DataFrame,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9
    ) -> pd.DataFrame:
        """
        计算 MACD (Moving Average Convergence Divergence)

        MACD = EMA(fast) - EMA(slow)
        Signal = EMA(MACD, signal)
        Histogram = MACD - Signal

        Args:
            df: OHLCV DataFrame
            fast: 快线周期，默认 12
            slow: 慢线周期，默认 26
            signal: 信号线周期，默认 9

        Returns:
            添加了 MACD, MACD信号线, MACD柱状图 列的 DataFrame
        """
        # 计算 EMA
        ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
        ema_slow = df['close'].ewm(span=slow, adjust=False).mean()

        # 计算 MACD
        df['macd'] = ema_fast - ema_slow

        # 计算信号线
        df['macd_signal'] = df['macd'].ewm(span=signal, adjust=False).mean()

        # 计算柱状图
        df['macd_hist'] = df['macd'] - df['macd_signal']

        return df

    @staticmethod
    def calculate_bollinger_bands(
        df: pd.DataFrame,
        period: int = 20,
        std_dev: float = 2.0
    ) -> pd.DataFrame:
        """
        计算布林带 (Bollinger Bands)

        Middle Band = SMA(period)
        Upper Band = Middle Band + (std_dev * std)
        Lower Band = Middle Band - (std_dev * std)

        Args:
            df: OHLCV DataFrame
            period: 周期，默认 20
            std_dev: 标准差倍数，默认 2.0

        Returns:
            添加了布林带上轨、中轨、下轨列的 DataFrame
        """
        # 中轨（简单移动平均）
        df['bb_middle'] = df['close'].rolling(window=period).mean()

        # 标准差
        std = df['close'].rolling(window=period).std()

        # 上轨和下轨
        df['bb_upper'] = df['bb_middle'] + (std_dev * std)
        df['bb_lower'] = df['bb_middle'] - (std_dev * std)

        return df

    @staticmethod
    def calculate_volume_analysis(df: pd.DataFrame) -> pd.DataFrame:
        """
        计算成交量相关指标

        Args:
            df: OHLCV DataFrame

        Returns:
            添加了成交量指标列的 DataFrame
        """
        # 成交量移动平均
        df['volume_ma_20'] = df['volume'].rolling(window=20).mean()

        # 成交量变化率（百分比）
        df['volume_change'] = df['volume'].pct_change() * 100

        return df

    @staticmethod
    def calculate_ema(series: pd.Series, period: int) -> pd.Series:
        """
        计算指数移动平均 (Exponential Moving Average)

        Args:
            series: 价格序列
            period: 周期

        Returns:
            EMA 序列
        """
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def calculate_all_indicators(
        df: pd.DataFrame,
        ma_periods: List[int] = [7, 25, 99],
        rsi_period: int = 14,
        macd_params: Dict[str, int] = None,
        bollinger_params: Dict[str, Any] = None
    ) -> pd.DataFrame:
        """
        计算所有技术指标

        Args:
            df: OHLCV DataFrame
            ma_periods: MA 周期列表
            rsi_period: RSI 周期
            macd_params: MACD 参数 {'fast': 12, 'slow': 26, 'signal': 9}
            bollinger_params: 布林带参数 {'period': 20, 'std_dev': 2.0}

        Returns:
            添加了所有指标列的 DataFrame
        """
        if macd_params is None:
            macd_params = {'fast': 12, 'slow': 26, 'signal': 9}

        if bollinger_params is None:
            bollinger_params = {'period': 20, 'std_dev': 2.0}

        # 创建副本以避免修改原始数据
        df = df.copy()

        # 计算移动平均线
        df = TechnicalIndicators.calculate_ma(df, ma_periods)

        # 计算 RSI
        df = TechnicalIndicators.calculate_rsi(df, rsi_period)

        # 计算 MACD
        df = TechnicalIndicators.calculate_macd(
            df,
            macd_params['fast'],
            macd_params['slow'],
            macd_params['signal']
        )

        # 计算布林带
        df = TechnicalIndicators.calculate_bollinger_bands(
            df,
            bollinger_params['period'],
            bollinger_params['std_dev']
        )

        # 计算成交量指标
        df = TechnicalIndicators.calculate_volume_analysis(df)

        return df

    @staticmethod
    def get_latest_indicators(df: pd.DataFrame) -> Dict[str, Any]:
        """
        获取最新的指标数据（最后一行）

        Args:
            df: 计算好指标的 DataFrame

        Returns:
            包含最新指标的字典
        """
        if df.empty:
            return {}

        latest = df.iloc[-1]
        indicators = {
            'timestamp': latest.name,
            'current_price': latest['close'],
            'open': latest['open'],
            'high': latest['high'],
            'low': latest['low'],
            'volume': latest['volume'],
        }

        # 添加 MA - 处理 nan 值
        for col in df.columns:
            if col.startswith('ma_'):
                value = latest[col]
                # 如果 MA 是 nan，使用当前价格作为替代
                if pd.isna(value) or np.isnan(value):
                    indicators[col] = latest['close']
                else:
                    indicators[col] = value

        # 添加 RSI - 处理 nan 值
        if 'rsi' in df.columns:
            rsi_value = latest['rsi']
            # 如果 RSI 是 nan，使用中性值 50
            if pd.isna(rsi_value) or np.isnan(rsi_value):
                indicators['rsi'] = 50.0
            else:
                indicators['rsi'] = rsi_value

        # 添加 MACD - 处理 nan 值
        if 'macd' in df.columns:
            macd_value = latest['macd']
            macd_signal_value = latest['macd_signal']
            macd_hist_value = latest['macd_hist']

            # 如果 MACD 是 nan，使用 0
            indicators['macd'] = 0.0 if pd.isna(macd_value) or np.isnan(macd_value) else macd_value
            indicators['macd_signal'] = 0.0 if pd.isna(macd_signal_value) or np.isnan(macd_signal_value) else macd_signal_value
            indicators['macd_hist'] = 0.0 if pd.isna(macd_hist_value) or np.isnan(macd_hist_value) else macd_hist_value

        # 添加布林带 - 处理 nan 值
        if 'bb_upper' in df.columns:
            bb_upper_value = latest['bb_upper']
            bb_middle_value = latest['bb_middle']
            bb_lower_value = latest['bb_lower']
            current_price = latest['close']

            # 如果布林带是 nan，使用当前价格作为所有轨道的默认值
            if pd.isna(bb_middle_value) or np.isnan(bb_middle_value):
                indicators['bb_upper'] = current_price
                indicators['bb_middle'] = current_price
                indicators['bb_lower'] = current_price
                indicators['bb_position'] = 0.5  # 中性位置
            else:
                indicators['bb_upper'] = bb_upper_value
                indicators['bb_middle'] = bb_middle_value
                indicators['bb_lower'] = bb_lower_value

                # 计算价格在布林带中的位置（0-1）
                bb_range = bb_upper_value - bb_lower_value
                if bb_range > 0 and not np.isnan(bb_range):
                    indicators['bb_position'] = (current_price - bb_lower_value) / bb_range
                else:
                    indicators['bb_position'] = 0.5  # 如果范围为0，返回中性位置

        # 添加成交量指标 - 处理 nan 和 inf 值
        if 'volume_ma_20' in df.columns:
            volume_ma_value = latest['volume_ma_20']
            volume_change_value = latest['volume_change']

            # 处理成交量均线 nan
            if pd.isna(volume_ma_value) or np.isnan(volume_ma_value):
                indicators['volume_ma_20'] = latest['volume']
            else:
                indicators['volume_ma_20'] = volume_ma_value

            # 处理成交量变化 nan 或 inf
            if pd.isna(volume_change_value) or np.isnan(volume_change_value) or np.isinf(volume_change_value):
                indicators['volume_change'] = 0.0  # 使用0表示无变化
            else:
                indicators['volume_change'] = volume_change_value

        return indicators

    @staticmethod
    def analyze_trend(df: pd.DataFrame, ma_short: int = 7, ma_long: int = 25) -> str:
        """
        分析趋势方向

        Args:
            df: OHLCV DataFrame（已计算指标）
            ma_short: 短期均线
            ma_long: 长期均线

        Returns:
            趋势描述：上涨、下跌、震荡
        """
        if df.empty or len(df) < max(ma_short, ma_long):
            return "数据不足"

        latest = df.iloc[-1]

        # 获取均线值
        ma_s = latest.get(f'ma_{ma_short}', None)
        ma_l = latest.get(f'ma_{ma_long}', None)
        current_price = latest['close']

        if ma_s is None or ma_l is None or np.isnan(ma_s) or np.isnan(ma_l):
            return "数据不足"

        # 计算价格相对于均线的位置
        if current_price > ma_s > ma_l:
            return "强势上涨"
        elif current_price > ma_s and ma_s < ma_l:
            return "上涨转弱"
        elif current_price < ma_s < ma_l:
            return "强势下跌"
        elif current_price < ma_s and ma_s > ma_l:
            return "下跌转强"
        else:
            return "震荡"

    @staticmethod
    def get_multi_timeframe_trend(market_data_fetcher, symbol: str) -> Dict[str, str]:
        """
        获取多时间周期趋势

        Args:
            market_data_fetcher: MarketDataFetcher 实例
            symbol: 交易对

        Returns:
            包含各时间周期趋势的字典
        """
        timeframes = {
            '1d': '日线',
            '4h': '4小时',
            '1h': '1小时',
            '15m': '15分钟',
            '1m': '1分钟'
        }

        trends = {}

        for tf, tf_name in timeframes.items():
            try:
                # 获取数据
                df = market_data_fetcher.fetch_ohlcv(symbol, tf, limit=100)
                if df is None or df.empty:
                    trends[tf_name] = "无数据"
                    continue

                # 计算简单均线用于趋势判断
                df = TechnicalIndicators.calculate_ma(df, [7, 25])

                # 分析趋势
                trend = TechnicalIndicators.analyze_trend(df)
                trends[tf_name] = trend

            except Exception as e:
                trends[tf_name] = "获取失败"

        return trends

    @staticmethod
    def generate_market_summary(indicators: Dict[str, Any]) -> str:
        """
        生成市场数据的文字摘要

        Args:
            indicators: 指标字典

        Returns:
            市场摘要文本
        """
        summary_parts = []

        # 价格信息
        price = indicators.get('current_price', 0)
        summary_parts.append(f"当前价格: {price:.2f}")

        # RSI 分析
        rsi = indicators.get('rsi')
        if rsi is not None and not np.isnan(rsi):
            if rsi > 70:
                rsi_status = "超买"
            elif rsi < 30:
                rsi_status = "超卖"
            else:
                rsi_status = "中性"
            summary_parts.append(f"RSI: {rsi:.2f} ({rsi_status})")

        # MACD 分析
        macd = indicators.get('macd')
        macd_signal = indicators.get('macd_signal')
        if macd is not None and macd_signal is not None:
            if not np.isnan(macd) and not np.isnan(macd_signal):
                if macd > macd_signal:
                    macd_status = "多头"
                else:
                    macd_status = "空头"
                summary_parts.append(f"MACD: {macd_status}")

        # MA 趋势
        ma_7 = indicators.get('ma_7')
        ma_25 = indicators.get('ma_25')
        if ma_7 is not None and ma_25 is not None:
            if not np.isnan(ma_7) and not np.isnan(ma_25):
                if ma_7 > ma_25:
                    trend = "短期上涨趋势"
                else:
                    trend = "短期下跌趋势"
                summary_parts.append(f"趋势: {trend}")

        # 布林带位置
        bb_position = indicators.get('bb_position')
        if bb_position is not None and not np.isnan(bb_position):
            if bb_position > 0.8:
                bb_status = "接近上轨"
            elif bb_position < 0.2:
                bb_status = "接近下轨"
            else:
                bb_status = "中间区域"
            summary_parts.append(f"布林带: {bb_status}")

        return " | ".join(summary_parts)


def test_indicators():
    """测试技术指标计算"""
    print("=== 测试技术指标计算（纯 pandas/numpy 实现）===\n")

    # 创建示例数据
    from src.data.market_data import MarketDataFetcher

    fetcher = MarketDataFetcher(demo_trading=False)
    df = fetcher.fetch_ohlcv('BTC/USDT', '15m', limit=100)

    if df is None:
        print("无法获取数据")
        return

    print("1. 计算所有技术指标...")
    df = TechnicalIndicators.calculate_all_indicators(df)
    print(df.tail())

    print("\n2. 获取最新指标...")
    indicators = TechnicalIndicators.get_latest_indicators(df)
    for key, value in indicators.items():
        if isinstance(value, (int, float)):
            if not np.isnan(value):
                print(f"{key}: {value:.4f}")
        else:
            print(f"{key}: {value}")

    print("\n3. 生成市场摘要...")
    summary = TechnicalIndicators.generate_market_summary(indicators)
    print(summary)


if __name__ == "__main__":
    test_indicators()
