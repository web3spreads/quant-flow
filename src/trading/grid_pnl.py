"""
网格级 PnL 追踪器

跟踪已实现/未实现盈亏、手续费，为 Triple Barrier 风控提供 net_pnl_pct。
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from src.utils.grid_math import GridLevel, GridLevelState
from src.utils.precision import to_decimal


@dataclass
class GridPnLTracker:
    """网格级 PnL 追踪器"""

    # 已实现（完成开平仓轮回的）
    realized_buy_volume: Decimal = field(default_factory=lambda: Decimal("0"))
    realized_sell_volume: Decimal = field(default_factory=lambda: Decimal("0"))
    realized_fees: Decimal = field(default_factory=lambda: Decimal("0"))
    realized_pnl: Decimal = field(default_factory=lambda: Decimal("0"))
    completed_round_trips: int = 0

    # 费率配置
    maker_fee_rate: Decimal = field(default_factory=lambda: Decimal("0.00035"))

    def record_round_trip(self, level: GridLevel) -> Decimal:
        """记录一次完成的开平仓轮回，返回本轮净盈亏。"""
        if level.open_fill_price is None or level.close_fill_price is None:
            return Decimal("0")
        if level.open_fill_amount is None:
            return Decimal("0")

        open_price = level.open_fill_price
        close_price = level.close_fill_price
        amount = level.open_fill_amount  # base 数量

        if level.side == "LONG":
            # 做多：买入 open，卖出 close
            buy_cost = open_price * amount
            sell_revenue = close_price * amount
            gross_pnl = sell_revenue - buy_cost
        else:
            # 做空：卖出 open，买入 close
            sell_revenue = open_price * amount
            buy_cost = close_price * amount
            gross_pnl = sell_revenue - buy_cost

        # 手续费：开仓 + 平仓各一次（限价单按 maker 费率）
        open_fee = open_price * amount * self.maker_fee_rate
        close_fee = close_price * amount * self.maker_fee_rate
        total_fee = open_fee + close_fee

        net_pnl = gross_pnl - total_fee

        self.realized_buy_volume += buy_cost
        self.realized_sell_volume += sell_revenue
        self.realized_fees += total_fee
        self.realized_pnl += net_pnl
        self.completed_round_trips += 1

        # 写回 level
        level.cumulative_pnl += net_pnl
        level.round_trip_count += 1

        return net_pnl

    def calculate_unrealized_pnl(
        self, levels: list[GridLevel], current_price: Decimal
    ) -> Decimal:
        """计算所有持仓中层级的未实现盈亏。"""
        unrealized = Decimal("0")

        for level in levels:
            if level.state not in (GridLevelState.OPEN_FILLED, GridLevelState.CLOSE_PENDING):
                continue

            if level.open_fill_price is None or level.open_fill_amount is None:
                continue

            amount = level.open_fill_amount
            entry = level.open_fill_price

            if level.side == "LONG":
                unrealized += (current_price - entry) * amount
            else:
                unrealized += (entry - current_price) * amount

            # 扣除预估平仓手续费
            unrealized -= current_price * amount * self.maker_fee_rate

        return unrealized

    def get_net_pnl(self, levels: list[GridLevel], current_price: Decimal) -> Decimal:
        """总 PnL = 已实现 + 未实现"""
        return self.realized_pnl + self.calculate_unrealized_pnl(levels, current_price)

    def get_net_pnl_pct(
        self,
        levels: list[GridLevel],
        current_price: Decimal,
        total_investment: Decimal,
    ) -> Decimal:
        """PnL 百分比（相对于总投入）"""
        if total_investment <= 0:
            return Decimal("0")
        return self.get_net_pnl(levels, current_price) / total_investment

    def get_summary(
        self,
        levels: list[GridLevel],
        current_price: Decimal,
        total_investment: Decimal,
    ) -> dict[str, Any]:
        """完整的 PnL 报告"""
        unrealized = self.calculate_unrealized_pnl(levels, current_price)
        net_pnl = self.realized_pnl + unrealized
        net_pnl_pct = net_pnl / total_investment if total_investment > 0 else Decimal("0")

        # 持仓中的层级
        open_levels = [
            level
            for level in levels
            if level.state in (GridLevelState.OPEN_FILLED, GridLevelState.CLOSE_PENDING)
        ]

        # 加权均价
        total_cost = Decimal("0")
        total_amount = Decimal("0")
        for level in open_levels:
            if level.open_fill_price and level.open_fill_amount:
                total_cost += level.open_fill_price * level.open_fill_amount
                total_amount += level.open_fill_amount
        avg_entry = total_cost / total_amount if total_amount > 0 else Decimal("0")

        return {
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": unrealized,
            "net_pnl": net_pnl,
            "net_pnl_pct": net_pnl_pct,
            "total_fees": self.realized_fees,
            "completed_round_trips": self.completed_round_trips,
            "open_positions": len(open_levels),
            "avg_entry_price": avg_entry,
            "current_price": current_price,
            "grid_efficiency": (
                self.realized_pnl / total_investment if total_investment > 0 else Decimal("0")
            ),
        }

    def to_dict(self) -> dict[str, str | int]:
        """序列化为可 JSON 持久化的字典。"""
        return {
            "realized_pnl": str(self.realized_pnl),
            "realized_buy_volume": str(self.realized_buy_volume),
            "realized_sell_volume": str(self.realized_sell_volume),
            "realized_fees": str(self.realized_fees),
            "completed_round_trips": self.completed_round_trips,
            "maker_fee_rate": str(self.maker_fee_rate),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GridPnLTracker":
        """从字典反序列化恢复。"""
        tracker = cls()
        tracker.realized_pnl = to_decimal(data.get("realized_pnl", "0"))
        tracker.realized_buy_volume = to_decimal(data.get("realized_buy_volume", "0"))
        tracker.realized_sell_volume = to_decimal(data.get("realized_sell_volume", "0"))
        tracker.realized_fees = to_decimal(data.get("realized_fees", "0"))
        tracker.completed_round_trips = int(data.get("completed_round_trips", 0))
        tracker.maker_fee_rate = to_decimal(
            data.get("maker_fee_rate", "0.00035")
        )
        return tracker
