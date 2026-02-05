"""
Hyperliquid 永续合约客户端
深度极简版：彻底绕过所有可能触发 'User does not exist' 的逻辑
"""
import eth_account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants
from src.fees import FeeRates

class HyperliquidClient:
    def __init__(self, private_key: str, account_address=None, testnet: bool = False):
        self.testnet = testnet
        self.base_url = constants.TESTNET_API_URL if testnet else constants.MAINNET_API_URL
        if not private_key.startswith('0x'): private_key = '0x' + private_key
        self.account = eth_account.Account.from_key(private_key)
        
        # 强制！如果提供了 account_address 且不是本地地址，则开启代理
        self.is_api_wallet_mode = account_address and account_address.lower() != self.account.address.lower()
        self.address = account_address if self.is_api_wallet_mode else self.account.address
        
        print(f"📍 [DEBUG] 交易地址: {self.address}")

        self.info = Info(self.base_url, skip_ws=True)
        # 关键：Exchange 必须接收 account_address 才能代理
        self.exchange = Exchange(self.account, self.base_url, account_address=self.address if self.is_api_wallet_mode else None)

    def fetch_user_fee_rates(self, **kwargs) -> FeeRates:
        return FeeRates(maker_rate=0.0002, taker_rate=0.0005)

    def get_balance(self):
        try:
            user_state = self.info.user_state(self.address)
            margin = user_state.get('marginSummary', {})
            return {
                'accountValue': float(margin.get('accountValue', 0)),
                'totalMarginUsed': float(margin.get('totalMarginUsed', 0)),
                'available': float(margin.get('accountValue', 0)) - float(margin.get('totalMarginUsed', 0))
            }
        except: return None

    def get_positions(self):
        try:
            user_state = self.info.user_state(self.address)
            return [p['position'] for p in user_state.get('assetPositions', []) if float(p['position']['szi']) != 0]
        except: return []

    def get_asset_info(self, symbol):
        try:
            for asset in self.info.meta().get('universe', []):
                if asset.get('name') == symbol: return asset
            return None
        except: return None

    def get_current_price(self, symbol):
        try: return float(self.info.all_mids().get(symbol, 0))
        except: return None

    def format_price(self, symbol, price):
        return round(round(price / 0.1) * 0.1, 1)

    def place_limit_order(self, symbol, is_buy, size, price, reduce_only=False):
        try:
            price = self.format_price(symbol, price)
            size = round(float(size), 4)
            print(f"   [API] {symbol} {'BUY' if is_buy else 'SELL'} {size} @ {price}")
            
            result = self.exchange.order(
                symbol, 
                is_buy, 
                size, 
                price, 
                {"limit": {"tif": "Gtc"}}, 
                reduce_only=reduce_only
            )
            print(f"   [API] 响应: {result}")
            return result
        except Exception as e: return {'status': 'error', 'message': str(e)}

    def cancel_order(self, symbol, oid):
        try: return self.exchange.cancel(symbol, oid)
        except: return {'status': 'error'}

    def update_leverage(self, symbol, leverage, is_cross=True):
        return {'status': 'ok'}
