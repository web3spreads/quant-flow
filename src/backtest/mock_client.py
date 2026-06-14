"""
模拟 Hyperliquid 客户端
用于回测，不实际执行交易，只记录交易意图和模拟账户状态
"""

import math
from copy import deepcopy
from datetime import datetime
from typing import Any

import pandas as pd

# 真实 Hyperliquid 永续合约 szDecimals(数量精度),取自交易所 meta 接口。
# 价格越高的资产 szDecimals 越大(可交易更小的数量)。此前 mock 对所有币种
# 硬编码 szDecimals=3,导致 BTC(~$78k)等高价资产的小额网格单 size 被四舍五入
# 为 0 而遭 place_limit_order 误拒(报"下单数量必须大于0"),网格回测零成交。
# 仅收录 szDecimals >= 2 的主流(多为高价)资产;低价资产 szDecimals 多为 0-1,
# 且单位数量较大不会因取整为 0,未收录者按价格量级启发式回退(见 get_asset_info)。
_HL_SZ_DECIMALS: dict[str, int] = {
    "BTC": 5, "ETH": 4,
    "BCH": 3, "BNB": 3, "PAXG": 3, "TAO": 3, "UNIBOT": 3, "XMR": 3,
    "AAVE": 2, "ACE": 2, "APT": 2, "AR": 2, "ATOM": 2, "AVAX": 2, "BSV": 2,
    "COMP": 2, "DASH": 2, "EIGEN": 2, "ENS": 2, "ETC": 2, "GMX": 2, "HYPE": 2,
    "ILV": 2, "LTC": 2, "NEO": 2, "OMNI": 2, "ORDI": 2, "SOL": 2, "TRB": 2,
    "VVV": 2, "ZEC": 2, "ZEN": 2,
}


def _infer_sz_decimals(symbol: str, price: float | None) -> int:
    """推断资产数量精度 szDecimals。

    已知主流资产用真实值;未知资产按价格量级回退:szDecimals ≈ round(log10(price)),
    使最小数量步长的名义价值维持在约 $0.1~$1 量级,与 Hyperliquid 的实际分布一致。
    高价资产得到更高精度(避免小额单取整为 0);无价格信息时回退到 2(偏保守,
    不会像旧默认值 3 那样使高价资产 size 归零)。
    """
    known = _HL_SZ_DECIMALS.get(symbol)
    if known is not None:
        return known
    if price and price > 0:
        return max(0, min(5, round(math.log10(price))))
    return 2


class MockHyperliquidClient:
    """
    模拟 Hyperliquid 客户端
    提供与真实客户端相同的接口，但使用历史数据模拟交易
    """

    def __init__(self, historical_data: pd.DataFrame, initial_balance: float = 1000.0):
        """
        初始化模拟客户端

        Args:
            historical_data: 历史K线数据（DataFrame，包含timestamp, open, high, low, close, volume）
            initial_balance: 初始余额（USD）
        """
        self.historical_data = historical_data.copy()
        self.historical_data = self.historical_data.sort_values("timestamp").reset_index(drop=True)
        self.current_index = 0
        self.initial_balance = initial_balance

        # 账户状态
        self.account_value = initial_balance
        self.total_margin_used = 0.0
        self.total_raw_usd = initial_balance

        # 持仓列表（模拟）
        self.positions: list[dict[str, Any]] = []

        # 挂单列表（用于网格回测）
        self.open_orders: list[dict[str, Any]] = []
        self._oid_seed: int = 100000
        self._position_id_seed: int = 1

        # 交易历史（用于记录）
        self.trade_history: list[dict[str, Any]] = []

        # 资产信息缓存（模拟）
        self.asset_info_cache: dict[str, dict[str, Any]] = {}

        print(f"✅ 模拟客户端初始化完成（初始余额: ${initial_balance:.2f}）")

    def set_current_time(self, timestamp: datetime):
        """
        设置当前回测时间

        Args:
            timestamp: 当前时间戳
        """
        # 找到最接近的时间点
        if len(self.historical_data) == 0:
            return

        # 找到时间戳对应的索引
        time_diffs = (self.historical_data["timestamp"] - timestamp).abs()
        self.current_index = time_diffs.idxmin()

        # 确保索引在有效范围内
        if self.current_index >= len(self.historical_data):
            self.current_index = len(self.historical_data) - 1

    def get_current_price(self, symbol: str) -> float | None:
        """
        获取当前价格（从历史数据中获取）

        Args:
            symbol: 交易对符号（忽略，因为只有单一交易对）

        Returns:
            当前价格（使用收盘价）
        """
        if self.current_index >= len(self.historical_data):
            return None

        row = self.historical_data.iloc[self.current_index]
        return float(row["close"])

    def get_balance(self) -> dict[str, Any] | None:
        """
        获取账户余额信息

        Returns:
            余额信息字典
        """
        return {
            "accountValue": self.account_value,
            "totalMarginUsed": self.total_margin_used,
            "totalRawUsd": self.total_raw_usd,
            "withdrawable": str(self.account_value - self.total_margin_used),
        }

    def get_positions(self) -> list[dict[str, Any]]:
        """
        获取当前持仓

        Returns:
            持仓列表
        """
        return self.positions.copy()

    def get_open_orders(self, include_trigger: bool = False) -> list[dict[str, Any]]:
        """
        获取当前挂单

        Args:
            include_trigger: 回测中保留此参数以对齐真实客户端接口

        Returns:
            挂单列表
        """
        _ = include_trigger
        return deepcopy(self.open_orders)

    def get_asset_info(self, symbol: str) -> dict[str, Any] | None:
        """
        获取交易对的元数据信息（模拟）

        Args:
            symbol: 交易对符号

        Returns:
            交易对元数据
        """
        if symbol not in self.asset_info_cache:
            # 按真实 Hyperliquid 精度推断 szDecimals(高价资产需更高精度,
            # 否则小额单 size 取整为 0 被误拒);未知资产按当前价格量级回退。
            sz_decimals = _infer_sz_decimals(symbol, self.get_current_price(symbol))
            self.asset_info_cache[symbol] = {
                "name": symbol,
                "szDecimals": sz_decimals,
                "maxLeverage": 50,
                "tickSize": 0.1,
            }
        return self.asset_info_cache[symbol]

    def format_price(self, symbol: str, price: float) -> float:
        """
        格式化价格（模拟）

        Args:
            symbol: 交易对符号
            price: 原始价格

        Returns:
            格式化后的价格（四舍五入到0.1）
        """
        return round(round(price / 0.1) * 0.1, 1)

    def update_leverage(self, symbol: str, leverage: int, is_cross: bool = False) -> dict[str, Any]:
        """
        更新杠杆（模拟，总是成功）

        Args:
            symbol: 交易对符号
            leverage: 杠杆倍数
            is_cross: 是否全仓模式

        Returns:
            成功结果
        """
        return {"status": "ok", "message": "杠杆设置成功（模拟）"}

    def place_market_order(
        self,
        symbol: str,
        is_buy: bool,
        size: float,
        reduce_only: bool = False,
        slippage: float = 0.01,
    ) -> dict[str, Any]:
        """
        下市价单（模拟）

        Args:
            symbol: 交易对符号
            is_buy: True=买入，False=卖出
            size: 下单数量
            reduce_only: 是否只减仓
            slippage: 滑点容忍度

        Returns:
            订单结果（模拟成功）
        """
        current_price = self.get_current_price(symbol)
        if current_price is None:
            return {"status": "error", "message": "无法获取当前价格"}

        # 记录交易意图（实际执行由BacktestEngine处理）
        return {
            "status": "ok",
            "response": {
                "type": "order",
                "data": {"statuses": [{"resting": {"oid": f"mock_{len(self.trade_history)}"}}]},
            },
            "symbol": symbol,
            "is_buy": is_buy,
            "size": size,
            "price": current_price,
        }

    def place_limit_order(
        self,
        symbol: str,
        is_buy: bool,
        size: float,
        price: float,
        reduce_only: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        下限价单（模拟）
        """
        if size <= 0:
            return {"status": "error", "message": "下单数量必须大于0"}

        self._oid_seed += 1
        oid = self._oid_seed

        order = {
            "oid": oid,
            "coin": symbol,
            "side": "B" if is_buy else "A",
            "sz": str(size),
            "limitPx": str(self.format_price(symbol, price)),
            "reduceOnly": bool(reduce_only),
            "orderType": {"limit": {"tif": "Gtc"}},
        }
        if metadata:
            order.update(metadata)

        self.open_orders.append(order)

        return {
            "status": "ok",
            "response": {"type": "order", "data": {"statuses": [{"resting": {"oid": oid}}]}},
        }

    def cancel_order(self, symbol: str, oid: int) -> dict[str, Any]:
        """
        取消挂单（模拟）
        """
        before = len(self.open_orders)
        self.open_orders = [
            o for o in self.open_orders if not (o.get("coin") == symbol and o.get("oid") == oid)
        ]
        if len(self.open_orders) == before:
            return {"status": "error", "message": f"订单不存在: {oid}"}
        return {"status": "ok"}

    def match_limit_orders(
        self,
        symbol: str,
        candle_low: float,
        candle_high: float,
    ) -> list[dict[str, Any]]:
        """
        在一根K线内撮合可成交的限价单
        """
        if candle_low > candle_high:
            candle_low, candle_high = candle_high, candle_low

        filled_orders: list[dict[str, Any]] = []
        remaining_orders: list[dict[str, Any]] = []

        for order in self.open_orders:
            if order.get("coin") != symbol:
                remaining_orders.append(order)
                continue

            try:
                limit_px = float(order.get("limitPx", 0))
            except (TypeError, ValueError):
                remaining_orders.append(order)
                continue

            side = str(order.get("side", "")).upper()
            is_buy = side in {"B", "BUY", "BID"}
            can_fill = (limit_px >= candle_low) if is_buy else (limit_px <= candle_high)

            if can_fill:
                filled_orders.append(deepcopy(order))
            else:
                remaining_orders.append(order)

        self.open_orders = remaining_orders
        return filled_orders

    def place_order_with_tpsl(
        self,
        symbol: str,
        is_buy: bool,
        size: float,
        take_profit_price: float,
        stop_loss_price: float,
    ) -> dict[str, Any]:
        """
        下带止盈止损的订单（模拟）

        Args:
            symbol: 交易对符号
            is_buy: True=买入，False=卖出
            size: 下单数量
            take_profit_price: 止盈价格
            stop_loss_price: 止损价格

        Returns:
            订单结果
        """
        market_order = self.place_market_order(symbol, is_buy, size)

        if market_order.get("status") == "ok":
            return {
                "success": True,
                "market_order": market_order,
                "take_profit_order": {"status": "ok", "price": take_profit_price},
                "stop_loss_order": {"status": "ok", "price": stop_loss_price},
                "errors": [],
            }
        else:
            return {
                "success": False,
                "market_order": market_order,
                "take_profit_order": None,
                "stop_loss_order": None,
                "errors": [market_order],
            }

    def close_position(self, symbol: str, size: float | None = None) -> dict[str, Any]:
        """
        平仓（模拟）

        Args:
            symbol: 交易对符号
            size: 平仓数量（None=全平）

        Returns:
            平仓结果
        """
        # 检查是否有持仓
        position = next((p for p in self.positions if p.get("coin") == symbol), None)
        if not position:
            return {"status": "error", "message": f"没有 {symbol} 的持仓"}

        return {"status": "ok", "message": "平仓成功（模拟）", "symbol": symbol, "size": size}

    def add_position(
        self,
        symbol: str,
        size: float,
        entry_price: float,
        leverage: int,
        is_long: bool,
        take_profit_price: float | None = None,
        stop_loss_price: float | None = None,
    ):
        """
        添加持仓（由BacktestEngine调用）

        Args:
            symbol: 交易对符号
            size: 持仓数量（正数）
            entry_price: 入场价格
            leverage: 杠杆倍数
            is_long: True=多仓，False=空仓
            take_profit_price: 止盈价格
            stop_loss_price: 止损价格
        """
        # 获取当前时间戳
        if self.current_index < len(self.historical_data):
            current_timestamp = self.historical_data.iloc[self.current_index]["timestamp"]
        else:
            current_timestamp = self.historical_data.iloc[-1]["timestamp"]

        position_id = self._position_id_seed
        self._position_id_seed += 1

        position = {
            "position_id": position_id,
            "coin": symbol,
            "szi": str(size if is_long else -size),
            "entryPx": str(entry_price),
            "positionValue": str(abs(size * entry_price)),
            "unrealizedPnl": "0",
            "leverage": {"type": "isolated", "value": leverage},
            "take_profit_price": take_profit_price,
            "stop_loss_price": stop_loss_price,
            "is_long": is_long,
            "entry_time": current_timestamp,
        }
        self.positions.append(position)

    def remove_position(self, symbol: str, position_id: int | None = None):
        """
        移除持仓（由BacktestEngine调用）

        Args:
            symbol: 交易对符号
            position_id: 指定持仓ID（None=移除该symbol全部持仓）
        """
        if position_id is None:
            self.positions = [p for p in self.positions if p.get("coin") != symbol]
            return

        self.positions = [
            p
            for p in self.positions
            if not (p.get("coin") == symbol and p.get("position_id") == position_id)
        ]

    def update_position_pnl(self, symbol: str, unrealized_pnl: float):
        """
        更新持仓的未实现盈亏

        Args:
            symbol: 交易对符号
            unrealized_pnl: 未实现盈亏
        """
        for position in self.positions:
            if position.get("coin") == symbol:
                position["unrealizedPnl"] = str(unrealized_pnl)
                break

    def update_account_value(self, account_value: float, margin_used: float):
        """
        更新账户价值

        Args:
            account_value: 账户总价值
            margin_used: 已用保证金
        """
        self.account_value = account_value
        self.total_margin_used = margin_used
        self.total_raw_usd = account_value - margin_used

    @property
    def address(self) -> str:
        """返回模拟地址"""
        return "mock_address_for_backtest"
