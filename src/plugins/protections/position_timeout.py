"""
持仓超时保护插件
持仓时间超过阈值时触发平仓或仅通知。
"""

import logging
from datetime import datetime
from typing import Any

from src.plugins.protections.base import (
    IProtection,
    ProtectionAction,
    ProtectionContext,
    ProtectionReturn,
)

logger = logging.getLogger(__name__)


class PositionTimeoutProtection(IProtection):
    """持仓超时保护"""

    @property
    def name(self) -> str:
        return "position_timeout"

    def __init__(self, **kwargs):
        self._position_records: dict[str, dict[str, Any]] = {}
        super().__init__(**kwargs)

    def check(self, context: ProtectionContext) -> ProtectionReturn:
        """检查持仓超时"""
        max_hours = self.config.get("max_position_hours", 48.0)

        with self._lock:
            timeout_symbols = []
            now = context.timestamp

            for symbol, record in self._position_records.items():
                entry_time = datetime.fromisoformat(record["entry_time"])
                holding_hours = (now - entry_time).total_seconds() / 3600
                if holding_hours >= max_hours:
                    timeout_symbols.append(symbol)

            if timeout_symbols:
                reason = f"持仓超时保护触发: {', '.join(timeout_symbols)} 持仓超过 {max_hours}h"
                self._send_cloud_event(timeout_symbols, max_hours)

                return ProtectionReturn(
                    triggered=True,
                    action=ProtectionAction.NONE,  # 由调用方决定是否平仓
                    reason=reason,
                    should_pause=False,
                    affected_symbols=timeout_symbols,
                    details={"max_position_hours": max_hours},
                )

            return ProtectionReturn(triggered=False)

    def get_timeout_symbols(self) -> list[str]:
        """返回所有超时持仓的符号列表"""
        max_hours = self.config.get("max_position_hours", 48.0)
        now = datetime.now()
        result = []

        with self._lock:
            for symbol, record in self._position_records.items():
                entry_time = datetime.fromisoformat(record["entry_time"])
                holding_hours = (now - entry_time).total_seconds() / 3600
                if holding_hours >= max_hours:
                    result.append(symbol)
        return result

    def on_trade_open(
        self,
        symbol: str,
        entry_price: float,
        size: float,
        is_long: bool,
        leverage: int = 1,
        timestamp: datetime | None = None,
    ) -> None:
        """记录开仓"""
        ts = timestamp or datetime.now()
        with self._lock:
            self._position_records[symbol] = {
                "entry_time": ts.isoformat(),
                "entry_price": entry_price,
                "size": size,
                "is_long": is_long,
                "leverage": leverage,
            }
            self.save_state()

    def on_trade_close(self, symbol: str, pnl: float, timestamp: datetime | None = None) -> None:
        """移除持仓记录"""
        with self._lock:
            self._position_records.pop(symbol, None)
            self.save_state()

    def _send_cloud_event(self, symbols: list[str], max_hours: float) -> None:
        """上报风控事件到云端"""
        try:
            from src.utils.cloud_logger import get_cloud_logger

            cloud = get_cloud_logger()
            if cloud:
                for symbol in symbols:
                    cloud.send_risk_event(
                        symbol=symbol,
                        risk_type="position_timeout",
                        details={"max_position_hours": max_hours},
                        level="warning",
                    )
        except Exception as e:
            logger.warning("上报超时风控事件失败: %s", e)

    def _get_state_dict(self) -> dict[str, Any]:
        return {"position_records": self._position_records}

    def _restore_state_dict(self, state: dict[str, Any]) -> None:
        self._position_records = state.get("position_records", {})
