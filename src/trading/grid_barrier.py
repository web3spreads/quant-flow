"""
Triple Barrier 网格级风控

实现网格级别的三重屏障（止损 + 止盈 + 时间限制 + 追踪止损），
作为单层 TP/SL trigger 之上的全局兜底保护。
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional

from src.utils.precision import to_decimal


@dataclass
class TripleBarrierConfig:
    """三重屏障风控配置"""

    # 1. 止损：整个网格的未实现+已实现 PnL% 低于此值 -> 全部平仓
    stop_loss_pct: Optional[Decimal] = field(default_factory=lambda: Decimal("0.05"))

    # 2. 止盈：整个网格的净 PnL% 高于此值 -> 全部平仓获利了结
    take_profit_pct: Optional[Decimal] = field(default_factory=lambda: Decimal("0.10"))

    # 3. 时间限制：网格运行超过此秒数 -> 全部平仓
    time_limit_seconds: Optional[int] = 14400  # 4 小时

    # 4. 追踪止损
    trailing_stop_activation_pct: Optional[Decimal] = field(
        default_factory=lambda: Decimal("0.03")
    )
    trailing_stop_delta_pct: Optional[Decimal] = field(
        default_factory=lambda: Decimal("0.01")
    )

    # 5. 限价保护：价格超出此范围 -> 触发平仓
    price_lower_limit: Optional[Decimal] = None
    price_upper_limit: Optional[Decimal] = None

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "TripleBarrierConfig":
        """从 config.grid.yaml 的 risk_management 段构建。"""
        if not config:
            return cls()

        barrier = cls()

        if "stop_loss_pct" in config:
            val = config["stop_loss_pct"]
            barrier.stop_loss_pct = to_decimal(val) if val is not None else None

        if "take_profit_pct" in config:
            val = config["take_profit_pct"]
            barrier.take_profit_pct = to_decimal(val) if val is not None else None

        if "time_limit_seconds" in config:
            val = config["time_limit_seconds"]
            barrier.time_limit_seconds = int(val) if val is not None else None

        if "trailing_stop_activation_pct" in config:
            val = config["trailing_stop_activation_pct"]
            barrier.trailing_stop_activation_pct = to_decimal(val) if val is not None else None

        if "trailing_stop_delta_pct" in config:
            val = config["trailing_stop_delta_pct"]
            barrier.trailing_stop_delta_pct = to_decimal(val) if val is not None else None

        if "price_lower_limit" in config:
            val = config["price_lower_limit"]
            barrier.price_lower_limit = to_decimal(val) if val is not None else None

        if "price_upper_limit" in config:
            val = config["price_upper_limit"]
            barrier.price_upper_limit = to_decimal(val) if val is not None else None

        return barrier


class GridBarrierMonitor:
    """三重屏障监控器"""

    def __init__(self, config: TripleBarrierConfig, start_time: float):
        self.config = config
        self.start_time = start_time
        self._trailing_stop_high_water: Optional[Decimal] = None

    def check(
        self,
        current_price: Decimal,
        net_pnl_pct: Decimal,
        current_time: float,
    ) -> Optional[str]:
        """
        检查是否触发屏障。
        返回 None = 安全，返回字符串 = 触发原因。
        优先级：止损 > 限价 > 时限 > 追踪止损 > 止盈
        """
        cfg = self.config

        # 1. 止损
        if cfg.stop_loss_pct is not None:
            if net_pnl_pct <= -cfg.stop_loss_pct:
                return f"STOP_LOSS: PnL {float(net_pnl_pct):.2%} <= -{float(cfg.stop_loss_pct):.2%}"

        # 2. 限价保护
        if cfg.price_lower_limit is not None and current_price <= cfg.price_lower_limit:
            return f"PRICE_LIMIT: {current_price} <= {cfg.price_lower_limit}"
        if cfg.price_upper_limit is not None and current_price >= cfg.price_upper_limit:
            return f"PRICE_LIMIT: {current_price} >= {cfg.price_upper_limit}"

        # 3. 时间限制
        if cfg.time_limit_seconds is not None:
            elapsed = current_time - self.start_time
            if elapsed >= cfg.time_limit_seconds:
                return f"TIME_LIMIT: {elapsed:.0f}s >= {cfg.time_limit_seconds}s"

        # 4. 追踪止损
        if (
            cfg.trailing_stop_activation_pct is not None
            and cfg.trailing_stop_delta_pct is not None
        ):
            trigger = self._check_trailing_stop(net_pnl_pct)
            if trigger:
                return trigger

        # 5. 整体止盈
        if cfg.take_profit_pct is not None:
            if net_pnl_pct >= cfg.take_profit_pct:
                return f"TAKE_PROFIT: PnL {float(net_pnl_pct):.2%} >= {float(cfg.take_profit_pct):.2%}"

        return None

    def _check_trailing_stop(self, net_pnl_pct: Decimal) -> Optional[str]:
        cfg = self.config

        if self._trailing_stop_high_water is None:
            # 尚未激活：PnL 首次达到激活阈值
            if net_pnl_pct >= cfg.trailing_stop_activation_pct:
                self._trailing_stop_high_water = net_pnl_pct
            return None

        # 已激活：更新高水位
        if net_pnl_pct > self._trailing_stop_high_water:
            self._trailing_stop_high_water = net_pnl_pct

        # 检查回撤
        drawdown = self._trailing_stop_high_water - net_pnl_pct
        if drawdown >= cfg.trailing_stop_delta_pct:
            return (
                f"TRAILING_STOP: 高水位 {float(self._trailing_stop_high_water):.2%} "
                f"回撤 {float(drawdown):.2%} >= {float(cfg.trailing_stop_delta_pct):.2%}"
            )

        return None

    def reset(self, start_time: float):
        """重置监控器（全量重建后调用）。"""
        self.start_time = start_time
        self._trailing_stop_high_water = None
