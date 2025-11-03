"""
市场数据获取模块
使用 CCXT 库获取加密货币交易所的市场数据
"""

import ccxt
import pandas as pd
from typing import Optional, List
from datetime import datetime, timedelta


class MarketDataFetcher:
    """市场数据获取器"""

    def __init__(
        self,
        exchange_id: str = "bitget",
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        password: Optional[str] = None,
        demo_trading: bool = False
    ):
        """
        初始化市场数据获取器

        Args:
            exchange_id: 交易所ID（默认 bitget）
            api_key: API Key（可选，仅获取公开数据时可不提供）
            api_secret: API Secret
            password: API Passphrase
            demo_trading: 是否使用模拟盘（True=模拟盘，False=实盘）
        """
        self.exchange_id = exchange_id

        # 初始化交易所
        exchange_class = getattr(ccxt, exchange_id)
        config = {
            'enableRateLimit': True,  # 启用请求频率限制
            'timeout': 30000,  # 超时时间 30秒
        }

        # 如果提供了 API 凭证，添加到配置
        if api_key and api_secret:
            config['apiKey'] = api_key
            config['secret'] = api_secret
            if password:
                config['password'] = password

        # 模拟盘模式使用沙盒环境
        if demo_trading:
            config['sandbox'] = True

        self.exchange = exchange_class(config)

        # 加载市场信息
        try:
            self.exchange.load_markets()
        except Exception as e:
            print(f"警告: 无法加载市场信息: {e}")

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = '15m',
        limit: int = 100
    ) -> Optional[pd.DataFrame]:
        """
        获取 OHLCV (K线) 数据

        Args:
            symbol: 交易对，如 'BTC/USDT'
            timeframe: 时间周期，如 '1m', '5m', '15m', '1h', '1d'
            limit: 获取的K线数量

        Returns:
            包含 OHLCV 数据的 DataFrame，列名为: timestamp, open, high, low, close, volume
            如果失败返回 None
        """
        try:
            # 获取 K 线数据
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)

            if not ohlcv:
                return None

            # 转换为 DataFrame
            df = pd.DataFrame(
                ohlcv,
                columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
            )

            # 转换时间戳为 datetime
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)

            return df

        except Exception as e:
            print(f"获取 {symbol} K线数据失败: {e}")
            return None

    def fetch_current_price(self, symbol: str) -> Optional[float]:
        """
        获取当前市场价格

        Args:
            symbol: 交易对，如 'BTC/USDT'

        Returns:
            当前价格（浮点数），失败返回 None
        """
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            # 使用最新成交价
            return float(ticker['last'])
        except Exception as e:
            print(f"获取 {symbol} 价格失败: {e}")
            return None

    def fetch_ticker(self, symbol: str) -> Optional[dict]:
        """
        获取最新的 Ticker 数据（包含当前价格、24小时涨跌等）

        Args:
            symbol: 交易对

        Returns:
            Ticker 字典，包含以下字段：
            - last: 最新价格
            - bid: 买一价
            - ask: 卖一价
            - high: 24小时最高价
            - low: 24小时最低价
            - volume: 24小时成交量
            - percentage: 24小时涨跌幅
        """
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            return {
                'last': ticker.get('last'),
                'bid': ticker.get('bid'),
                'ask': ticker.get('ask'),
                'high': ticker.get('high'),
                'low': ticker.get('low'),
                'volume': ticker.get('baseVolume'),
                'percentage': ticker.get('percentage'),
                'timestamp': datetime.now()
            }
        except Exception as e:
            print(f"获取 {symbol} Ticker 数据失败: {e}")
            return None

    def fetch_order_book(self, symbol: str, limit: int = 20) -> Optional[dict]:
        """
        获取订单簿（盘口数据）

        Args:
            symbol: 交易对
            limit: 获取的档位数量

        Returns:
            订单簿字典，包含 bids 和 asks
        """
        try:
            order_book = self.exchange.fetch_order_book(symbol, limit=limit)
            return {
                'bids': order_book['bids'][:limit],  # 买盘 [[price, amount], ...]
                'asks': order_book['asks'][:limit],  # 卖盘 [[price, amount], ...]
                'timestamp': datetime.now()
            }
        except Exception as e:
            print(f"获取 {symbol} 订单簿失败: {e}")
            return None

    def get_available_symbols(self, quote_currency: str = 'USDT') -> List[str]:
        """
        获取可用的交易对列表

        Args:
            quote_currency: 报价货币（如 USDT, BUSD）

        Returns:
            交易对列表
        """
        try:
            markets = self.exchange.load_markets()
            symbols = [
                symbol for symbol in markets.keys()
                if quote_currency in symbol and markets[symbol].get('active', False)
            ]
            return sorted(symbols)
        except Exception as e:
            print(f"获取交易对列表失败: {e}")
            return []

    def check_symbol_exists(self, symbol: str) -> bool:
        """
        检查交易对是否存在

        Args:
            symbol: 交易对

        Returns:
            是否存在
        """
        try:
            return symbol in self.exchange.markets
        except Exception:
            return False


def test_market_data():
    """测试市场数据获取功能"""
    print("=== 测试市场数据获取 ===\n")

    # 创建数据获取器（不需要 API Key，仅获取公开数据）
    fetcher = MarketDataFetcher(demo_trading=False)

    # 测试获取 K 线数据
    print("1. 获取 BTC/USDT 15分钟 K线数据...")
    df = fetcher.fetch_ohlcv('BTC/USDT', '15m', limit=10)
    if df is not None:
        print(df.tail())
        print(f"\n数据形状: {df.shape}")
    else:
        print("获取失败")

    # 测试获取 Ticker
    print("\n2. 获取 BTC/USDT Ticker 数据...")
    ticker = fetcher.fetch_ticker('BTC/USDT')
    if ticker:
        print(f"最新价格: {ticker['last']}")
        print(f"24h 涨跌幅: {ticker['percentage']:.2f}%")
        print(f"24h 成交量: {ticker['volume']:.2f}")

    # 测试获取订单簿
    print("\n3. 获取 BTC/USDT 订单簿...")
    order_book = fetcher.fetch_order_book('BTC/USDT', limit=5)
    if order_book:
        print("买一价:", order_book['bids'][0][0] if order_book['bids'] else 'N/A')
        print("卖一价:", order_book['asks'][0][0] if order_book['asks'] else 'N/A')

    # 测试获取可用交易对
    print("\n4. 获取 USDT 交易对列表...")
    symbols = fetcher.get_available_symbols('USDT')
    print(f"共有 {len(symbols)} 个 USDT 交易对")
    print("前 10 个:", symbols[:10])


if __name__ == "__main__":
    test_market_data()
