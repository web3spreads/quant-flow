"""
数据增强模块
为nof1和nof1-improved prompts提供额外的市场数据
包括历史序列、4小时数据、持仓量、资金费率等
"""

import pandas as pd
from typing import Dict, Any, Optional, List
from datetime import datetime

class MarketDataEnricher:
    """市场数据增强器 - 提供额外的数据字段供prompt使用"""

    def __init__(self, market_fetcher, start_time: Optional[datetime] = None):
        """
        初始化数据增强器

        Args:
            market_fetcher: MarketDataFetcher实例
            start_time: 程序启动时间,用于计算elapsed_minutes
        """
        self.market_fetcher = market_fetcher
        self.start_time = start_time or datetime.now()

    def get_elapsed_minutes(self) -> int:
        """获取程序运行时长(分钟)"""
        elapsed = datetime.now() - self.start_time
        return int(elapsed.total_seconds() / 60)

    def enrich_market_data(
        self,
        symbol: str,
        market_data: Dict[str, Any],
        df_15m: Optional[pd.DataFrame] = None,
        df_4h: Optional[pd.DataFrame] = None
    ) -> Dict[str, Any]:
        """
        增强市场数据,添加nof1 prompts需要的额外字段

        Args:
            symbol: 交易对
            market_data: 基础市场数据
            df_15m: 15分钟K线DataFrame(已计算指标)
            df_4h: 4小时K线DataFrame(已计算指标)

        Returns:
            增强后的市场数据字典
        """
        enriched = market_data.copy()

        # 1. 添加程序运行时长
        enriched['elapsed_minutes'] = self.get_elapsed_minutes()

        # 2. 添加历史序列数据(最近10个数据点)
        if df_15m is not None and not df_15m.empty:
            series_data = self._get_historical_series(df_15m, period=10)
            enriched.update(series_data)
        else:
            # 提供默认值
            enriched.update(self._get_empty_series())

        # 3. 添加当前时刻指标
        enriched['current_ema20'] = market_data.get('ema_20', market_data.get('current_price', 0))
        enriched['current_rsi'] = market_data.get('rsi', 50)
        enriched['current_macd'] = market_data.get('macd', 0)

        # 4. 添加4小时时间框架数据
        if df_4h is not None and not df_4h.empty:
            h4_data = self._get_4h_data(df_4h)
            enriched.update(h4_data)
        else:
            enriched.update(self._get_empty_4h_data())

        # 5. 添加持仓量和资金费率
        oi_and_funding = self._get_oi_and_funding(symbol)
        enriched.update(oi_and_funding)

        # 6. 格式化数据为字符串(用于prompt模板)
        enriched = self._format_for_template(enriched)

        return enriched

    def _get_historical_series(self, df: pd.DataFrame, period: int = 10) -> Dict[str, Any]:
        """获取历史序列数据"""
        if len(df) < period:
            period = len(df)

        recent_df = df.tail(period)

        series = {}

        # 中间价格序列
        series['mid_prices'] = [f"{p:.2f}" for p in recent_df['close'].tolist()]
        series['mid_prices_raw'] = recent_df['close'].tolist()

        # EMA(20)序列
        if 'ema_20' in recent_df.columns:
            ema_values = recent_df['ema_20'].fillna(recent_df['close']).tolist()
            series['ema_indicators'] = [f"{v:.2f}" for v in ema_values]
            series['ema_indicators_raw'] = ema_values

        # MACD序列
        if 'macd' in recent_df.columns:
            macd_values = recent_df['macd'].fillna(0).tolist()
            series['macd_indicators'] = [f"{v:.4f}" for v in macd_values]
            series['macd_indicators_raw'] = macd_values

        # RSI(7)序列 - 需要先计算
        if 'rsi' in recent_df.columns:
            rsi_values = recent_df['rsi'].fillna(50).tolist()
            series['rsi_7_indicators'] = [f"{v:.2f}" for v in rsi_values]
            series['rsi_7_indicators_raw'] = rsi_values

        # RSI(14)序列 - 假设使用同样的rsi列
        series['rsi_14_indicators'] = series.get('rsi_7_indicators', ['50.00'] * period)
        series['rsi_14_indicators_raw'] = series.get('rsi_7_indicators_raw', [50.0] * period)

        return series

    def _get_empty_series(self) -> Dict[str, Any]:
        """返回空序列的默认值"""
        empty_list = []
        return {
            'mid_prices': empty_list,
            'mid_prices_raw': empty_list,
            'ema_indicators': empty_list,
            'ema_indicators_raw': empty_list,
            'macd_indicators': empty_list,
            'macd_indicators_raw': empty_list,
            'rsi_7_indicators': empty_list,
            'rsi_7_indicators_raw': empty_list,
            'rsi_14_indicators': empty_list,
            'rsi_14_indicators_raw': empty_list,
        }

    def _get_4h_data(self, df_4h: pd.DataFrame) -> Dict[str, Any]:
        """获取4小时时间框架数据"""
        if df_4h.empty:
            return self._get_empty_4h_data()

        latest = df_4h.iloc[-1]
        recent_df = df_4h.tail(10)

        h4_data = {}

        # EMA
        h4_data['ema_20_4h'] = latest.get('ema_20', latest['close'])
        h4_data['ema_50_4h'] = latest.get('ema_50', latest['close'])

        # ATR
        h4_data['atr_3_4h'] = latest.get('atr_3', 0)
        h4_data['atr_14_4h'] = latest.get('atr_14', 0)

        # 成交量
        h4_data['current_volume'] = latest['volume']
        h4_data['avg_volume'] = df_4h['volume'].mean()

        # MACD序列
        macd_values = recent_df['macd'].fillna(0).tolist() if 'macd' in recent_df.columns else [0] * 10
        h4_data['macd_4h_indicators'] = macd_values

        # RSI序列
        rsi_values = recent_df['rsi'].fillna(50).tolist() if 'rsi' in recent_df.columns else [50] * 10
        h4_data['rsi_14_4h_indicators'] = rsi_values

        return h4_data

    def _get_empty_4h_data(self) -> Dict[str, Any]:
        """返回4小时数据的默认值"""
        return {
            'ema_20_4h': 0,
            'ema_50_4h': 0,
            'atr_3_4h': 0,
            'atr_14_4h': 0,
            'current_volume': 0,
            'avg_volume': 0,
            'macd_4h_indicators': [0] * 10,
            'rsi_14_4h_indicators': [50] * 10,
        }

    def _get_oi_and_funding(self, symbol: str) -> Dict[str, Any]:
        """获取持仓量和资金费率"""
        try:
            # 获取资金费率
            funding_rate = self.market_fetcher.get_funding_rate(symbol)

            # 持仓量暂时使用占位符,需要从API获取
            # TODO: 从Hyperliquid API获取实际持仓量数据
            return {
                'oi_latest': 0,  # 需要API支持
                'oi_average': 0,  # 需要API支持
                'funding_rate': funding_rate if funding_rate else 0,
            }
        except Exception as e:
            return {
                'oi_latest': 0,
                'oi_average': 0,
                'funding_rate': 0,
            }

    def _format_for_template(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """格式化数据为模板友好的字符串格式"""
        formatted = data.copy()

        # 格式化浮点数字段
        float_fields = [
            'current_ema20', 'ema_20_4h', 'ema_50_4h',
            'atr_3_4h', 'atr_14_4h', 'current_volume', 'avg_volume',
            'oi_latest', 'oi_average'
        ]

        for field in float_fields:
            if field in formatted and isinstance(formatted[field], (int, float)):
                formatted[f'{field}_formatted'] = f"{formatted[field]:.2f}"

        # 格式化序列为逗号分隔字符串
        list_fields = [
            'mid_prices', 'ema_indicators', 'macd_indicators',
            'rsi_7_indicators', 'rsi_14_indicators',
            'macd_4h_indicators', 'rsi_14_4h_indicators'
        ]

        for field in list_fields:
            if field in formatted and isinstance(formatted[field], list):
                formatted[f'{field}_str'] = ', '.join(map(str, formatted[field]))

        # 资金费率格式化为科学计数法
        if 'funding_rate' in formatted:
            formatted['funding_rate_formatted'] = f"{formatted['funding_rate']:.6e}"

        return formatted

    def enrich_account_data(
        self,
        balance_info: Optional[Dict[str, float]],
        initial_balance: float = 10000.0
    ) -> Dict[str, Any]:
        """
        增强账户数据

        Args:
            balance_info: 账户余额信息
            initial_balance: 初始余额,用于计算回报率

        Returns:
            包含额外账户指标的字典
        """
        account_data = {}

        if balance_info:
            total = balance_info.get('total', 0)
            available = balance_info.get('available', 0)

            # 计算总回报率
            total_return_pct = ((total - initial_balance) / initial_balance) * 100 if initial_balance > 0 else 0

            account_data.update({
                'total_return_pct': total_return_pct,
                'available_cash': available,
                'account_value': total,
                'sharpe_ratio': 0,  # TODO: 需要历史收益数据计算
            })
        else:
            account_data.update({
                'total_return_pct': 0,
                'available_cash': 0,
                'account_value': initial_balance,
                'sharpe_ratio': 0,
            })

        return account_data
