"""
网格计算数学引擎 (理科男强修版 - 彻底修复 Hyperliquid 保证金占用逻辑)
"""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any

from src.utils.precision import to_decimal


class GridLevelState(Enum):
    """层级状态机"""

    IDLE = "IDLE"  # 空闲，等待挂开仓单
    OPEN_PENDING = "OPEN_PENDING"  # 开仓单已挂，等待成交
    OPEN_FILLED = "OPEN_FILLED"  # 开仓成交，等待挂平仓单
    CLOSE_PENDING = "CLOSE_PENDING"  # 平仓单已挂，等待成交
    COMPLETED = "COMPLETED"  # 开平仓均完成 -> 即将 reset 回 IDLE


@dataclass
class GridLevel:
    """单个网格层级"""

    id: str  # "L0", "L1", ...
    price: Decimal  # 该层挂单价格
    amount: Decimal  # 该层下单金额（quote，USD）
    side: str  # "LONG" or "SHORT"
    state: GridLevelState = GridLevelState.IDLE

    # 订单追踪
    open_order_id: int | None = None
    open_fill_price: Decimal | None = None
    open_fill_amount: Decimal | None = None  # base 数量
    open_fill_time: float | None = None

    close_order_id: int | None = None
    close_fill_price: Decimal | None = None
    close_fill_amount: Decimal | None = None
    close_fill_time: float | None = None

    # 统计
    round_trip_count: int = 0
    cumulative_pnl: Decimal = field(default_factory=lambda: Decimal("0"))

    def reset(self):
        """完成一轮后重置，保留统计数据。"""
        self.state = GridLevelState.IDLE
        self.open_order_id = None
        self.open_fill_price = None
        self.open_fill_amount = None
        self.open_fill_time = None
        self.close_order_id = None
        self.close_fill_price = None
        self.close_fill_amount = None
        self.close_fill_time = None

    def to_dict(self) -> dict[str, Any]:
        """序列化为可 JSON 持久化的字典。"""
        return {
            "id": self.id,
            "price": str(self.price),
            "amount": str(self.amount),
            "side": self.side,
            "state": self.state.value,
            "open_order_id": self.open_order_id,
            "open_fill_price": str(self.open_fill_price)
            if self.open_fill_price is not None
            else None,
            "open_fill_amount": str(self.open_fill_amount)
            if self.open_fill_amount is not None
            else None,
            "open_fill_time": self.open_fill_time,
            "close_order_id": self.close_order_id,
            "close_fill_price": str(self.close_fill_price)
            if self.close_fill_price is not None
            else None,
            "close_fill_amount": str(self.close_fill_amount)
            if self.close_fill_amount is not None
            else None,
            "close_fill_time": self.close_fill_time,
            "round_trip_count": self.round_trip_count,
            "cumulative_pnl": str(self.cumulative_pnl),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GridLevel":
        """从字典反序列化恢复层级（崩溃恢复）。"""
        level = cls(
            id=data["id"],
            price=to_decimal(data["price"]),
            amount=to_decimal(data["amount"]),
            side=data["side"],
            state=GridLevelState(data.get("state", "IDLE")),
        )
        level.open_order_id = data.get("open_order_id")
        level.open_fill_price = (
            to_decimal(data["open_fill_price"]) if data.get("open_fill_price") is not None else None
        )
        level.open_fill_amount = (
            to_decimal(data["open_fill_amount"])
            if data.get("open_fill_amount") is not None
            else None
        )
        level.open_fill_time = data.get("open_fill_time")
        level.close_order_id = data.get("close_order_id")
        level.close_fill_price = (
            to_decimal(data["close_fill_price"])
            if data.get("close_fill_price") is not None
            else None
        )
        level.close_fill_amount = (
            to_decimal(data["close_fill_amount"])
            if data.get("close_fill_amount") is not None
            else None
        )
        level.close_fill_time = data.get("close_fill_time")
        level.round_trip_count = data.get("round_trip_count", 0)
        level.cumulative_pnl = to_decimal(data.get("cumulative_pnl", "0"))
        return level


def extract_order_id(limit_order_res: dict[str, Any]) -> int | None:
    """
    从下单响应中提取订单 ID，兼容 resting（挂单中）和 filled（立即成交）两种状态。

    Args:
        limit_order_res: place_limit_order 的返回结果

    Returns:
        订单 ID，提取失败时返回 None
    """
    try:
        statuses = limit_order_res.get("response", {}).get("data", {}).get("statuses", [])
        if not statuses:
            return None
        status = statuses[0]
        if "resting" in status:
            return status["resting"]["oid"]
        if "filled" in status:
            return status["filled"]["oid"]
        return None
    except (KeyError, IndexError, TypeError, AttributeError):
        return None


def calculate_grid_config(
    current_price: float,
    available_balance: float,
    mode: str = "NEUTRAL",
    width_pct: float = 0.05,
    grid_num: int = 6,
    leverage: int = 10,
    adaptive_sizing: bool = False,
    min_order_notional_usd: float = 11.0,
    min_grid_num: int = 3,
) -> dict[str, Any]:
    """
    针对 Hyperliquid 保证金占用逻辑进行强力修正。

    【终极修正逻辑】：
    Hyperliquid 测试网对于限价单的保证金检查极其严苛。
    如果我们有 $77 可用余额，10x 杠杆，理论总额度 $770。
    但如果一次性挂 6-8 个格子，系统会因为并行订单的潜在占用导致 Insufficient margin。

    修正方案：将单格金额大幅度压缩，确保 (单格金额 * 格子数) 远低于 (可用余额 * 杠杆)。

    内部使用 Decimal 精确计算，输出仍为 float（兼容 API 边界）。
    """
    # 参数校验（grid_num/leverage 来自 AI 输出，需防御非法值）
    grid_num = max(1, int(grid_num))
    leverage = max(1, int(leverage))

    # Decimal 精确计算
    d_price = to_decimal(current_price)
    d_balance = to_decimal(available_balance)
    d_width = to_decimal(width_pct)
    d_grid_num = Decimal(str(grid_num))
    d_leverage = Decimal(str(leverage))

    # 1. 计算区间
    if mode == "LONG":
        lower_price = d_price * (Decimal("1") - d_width)
        upper_price = d_price * Decimal("1.01")
    elif mode == "SHORT":
        lower_price = d_price * Decimal("0.99")
        upper_price = d_price * (Decimal("1") + d_width)
    else:  # NEUTRAL
        lower_price = d_price * (Decimal("1") - d_width / Decimal("2"))
        upper_price = d_price * (Decimal("1") + d_width / Decimal("2"))

    # 2. 【核心修正】极其保守的金额分配
    conservative_safety = Decimal("0.4")

    # 总额度计算
    total_notional_cap = d_balance * d_leverage * conservative_safety

    # 单格金额
    amount_per_grid = total_notional_cap / d_grid_num

    if adaptive_sizing:
        # 自适应仓位：单格金额与真实净值挂钩。
        # 历史行为的 $15.5 硬下限会在小账户上把总名义额抬到净值的十几倍
        # （线上 $7.71 账户被抬成 $124 名义敞口 ≈ 16 倍杠杆），保守系数 0.4 被完全反转。
        # 这里改为：单格不足最小名义额时【减少格数】而非抬高单格金额；
        # 格数低于下限说明资金撑不起最小网格，直接拒绝布单。
        d_min_order = to_decimal(min_order_notional_usd)
        if amount_per_grid < d_min_order:
            if d_min_order <= 0:
                d_min_order = Decimal("11")
            reduced_num = int(total_notional_cap / d_min_order)
            if reduced_num < max(2, int(min_grid_num)):
                required_balance = (
                    d_min_order * Decimal(str(min_grid_num)) / (d_leverage * conservative_safety)
                )
                return {
                    "action": "INSUFFICIENT_CAPITAL",
                    "mode": mode,
                    "reason": (
                        f"资金不足以支撑最小网格: 可用 ${float(d_balance):.2f} × 杠杆 {leverage} × "
                        f"安全系数 0.4 = 总额度 ${float(total_notional_cap):.2f}，"
                        f"按单格最小 ${float(d_min_order):.2f} 只够 {reduced_num} 格 "
                        f"(< 最少 {min_grid_num} 格)。需要余额 ≥ ${float(required_balance):.2f}"
                    ),
                    "required_balance": float(required_balance.quantize(Decimal("0.01"))),
                }
            grid_num = reduced_num
            d_grid_num = Decimal(str(grid_num))
            amount_per_grid = total_notional_cap / d_grid_num
    else:
        # 历史行为（默认）：硬编码钳制单格金额。仅对 ≳$100 的账户成立，
        # 小账户上下限会反噬保守性——新部署建议启用 grid_adaptive_sizing_enabled。
        # 强制兜底：如果算出来太大，强制压低到 25.5 USD
        if amount_per_grid > Decimal("30"):
            amount_per_grid = Decimal("25.5")

        # Hyperliquid 最小限制
        if amount_per_grid < Decimal("15.5"):
            amount_per_grid = Decimal("15.5")

    # 3. 计算止盈止损
    # 止盈：每格宽度的 80%，确保覆盖双边手续费（Hyperliquid 约 0.035% Maker）
    fee_rate = Decimal("0.00035")
    min_tp_ratio = fee_rate * Decimal("4")  # 双边手续费 x 2 倍缓冲
    raw_tp_ratio = (d_width / d_grid_num) * Decimal("0.8")
    tp_ratio = max(raw_tp_ratio, min_tp_ratio)

    # 止损：控制在止盈的 2 倍内，避免单次止损亏损过多
    max_sl_ratio = Decimal("0.02")
    min_sl_ratio = Decimal("0.005")
    sl_ratio = min(max(tp_ratio * Decimal("2"), min_sl_ratio), max_sl_ratio)

    # 输出为 float（API 边界兼容），保留合理精度
    return {
        "action": "UPDATE_GRID",
        "lower_price": float(lower_price.quantize(Decimal("0.1"))),
        "upper_price": float(upper_price.quantize(Decimal("0.1"))),
        "grid_num": int(grid_num),
        "amount_per_grid": float(amount_per_grid.quantize(Decimal("0.01"))),
        "tp_ratio": float(tp_ratio.quantize(Decimal("0.0001"))),
        "sl_ratio": float(sl_ratio.quantize(Decimal("0.0001"))),
        "mode": mode,
    }
