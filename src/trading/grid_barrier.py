"""
Triple Barrier 网格级风控

实现网格级别的三重屏障（止损 + 止盈 + 时间限制 + 追踪止损），
作为单层 TP/SL trigger 之上的全局兜底保护。
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from src.utils.precision import to_decimal


@dataclass
class TripleBarrierConfig:
    """三重屏障风控配置"""

    # 1. 止损：当总亏损百分比达到或超过此值时触发全部平仓（PnL <= -stop_loss_pct）
    stop_loss_pct: Decimal | None = field(default_factory=lambda: Decimal("0.05"))

    # 2. 止盈：整个网格的净 PnL% 高于此值 -> 全部平仓获利了结
    take_profit_pct: Decimal | None = field(default_factory=lambda: Decimal("0.10"))

    # 3. 时间限制：网格运行超过此秒数 -> 全部平仓
    time_limit_seconds: int | None = 14400  # 4 小时

    # 4. 追踪止损
    trailing_stop_activation_pct: Decimal | None = field(default_factory=lambda: Decimal("0.03"))
    trailing_stop_delta_pct: Decimal | None = field(default_factory=lambda: Decimal("0.01"))

    # 5. 限价保护：价格超出此范围 -> 触发平仓
    price_lower_limit: Decimal | None = None
    price_upper_limit: Decimal | None = None

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "TripleBarrierConfig":
        """从 config.grid.yaml 的 risk_management 段构建。"""
        if not config:
            return cls()

        barrier = cls()

        # 字段名到转换函数的映射
        converters: dict[str, type] = {
            "stop_loss_pct": to_decimal,
            "take_profit_pct": to_decimal,
            "time_limit_seconds": int,
            "trailing_stop_activation_pct": to_decimal,
            "trailing_stop_delta_pct": to_decimal,
            "price_lower_limit": to_decimal,
            "price_upper_limit": to_decimal,
        }

        for field_name, converter in converters.items():
            if field_name in config:
                value = config[field_name]
                setattr(
                    barrier,
                    field_name,
                    converter(value) if value is not None else None,
                )

        return barrier


class GridBarrierMonitor:
    """三重屏障监控器"""

    def __init__(self, config: TripleBarrierConfig, start_time: float):
        self.config = config
        self.start_time = start_time
        self._trailing_stop_high_water: Decimal | None = None

    def check(
        self,
        current_price: Decimal,
        net_pnl_pct: Decimal,
        current_time: float,
    ) -> str | None:
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
        if cfg.trailing_stop_activation_pct is not None and cfg.trailing_stop_delta_pct is not None:
            trigger = self._check_trailing_stop(net_pnl_pct)
            if trigger:
                return trigger

        # 5. 整体止盈
        if cfg.take_profit_pct is not None:
            if net_pnl_pct >= cfg.take_profit_pct:
                return (
                    f"TAKE_PROFIT: PnL {float(net_pnl_pct):.2%} >= {float(cfg.take_profit_pct):.2%}"
                )

        return None

    def _check_trailing_stop(self, net_pnl_pct: Decimal) -> str | None:
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
