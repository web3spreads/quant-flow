"""
连续亏损保护插件
连续亏损次数达到阈值时暂停交易。支持全局模式和交易对级锁定模式。
"""

import logging
from datetime import datetime, timedelta
from typing import Any

from src.plugins.protections.base import (
    IProtection,
    ProtectionAction,
    ProtectionContext,
    ProtectionReturn,
)

logger = logging.getLogger(__name__)


class ConsecutiveLossProtection(IProtection):
    """连续亏损保护"""

    @property
    def name(self) -> str:
        return "consecutive_loss"

    def __init__(self, **kwargs):
        self._global_losses: int = 0
        self._symbol_losses: dict[str, int] = {}
        self._locked_symbols: dict[str, str] = {}  # symbol -> 锁定截止时间 ISO
        self._is_paused: bool = False
        self._pause_reason: str = ""
        self._last_protection_time: datetime | None = None
        super().__init__(**kwargs)

    def check(self, context: ProtectionContext) -> ProtectionReturn:
        """检查连续亏损"""
        max_losses = self.config.get("max_consecutive_losses", 5)
        per_symbol = self.config.get("per_symbol", False)
        pause_hours = self.config.get("pause_hours", 4.0)

        with self._lock:
            # 检查暂停期是否已过
            if self._is_paused and self._last_protection_time:
                elapsed = (context.timestamp - self._last_protection_time).total_seconds() / 3600
                if elapsed >= pause_hours:
                    self._is_paused = False
                    self._pause_reason = ""
                    logger.info("连续亏损保护暂停期已过，恢复交易")

            # 清理已过期的 symbol 锁定
            self._cleanup_expired_locks(context.timestamp)

            if self._is_paused:
                return ProtectionReturn(
                    triggered=True,
                    action=ProtectionAction.PAUSE_NEW_TRADES,
                    reason=self._pause_reason,
                    should_pause=True,
                )

            # 全局模式检查
            if not per_symbol and self._global_losses >= max_losses:
                reason = (
                    f"连续亏损保护触发: 全局连续亏损 {self._global_losses} 次 >= 阈值 {max_losses}"
                )
                self._is_paused = True
                self._pause_reason = reason
                self._last_protection_time = context.timestamp
                self.save_state()

                self._send_cloud_event("global", self._global_losses, max_losses)

                return ProtectionReturn(
                    triggered=True,
                    action=ProtectionAction.PAUSE_NEW_TRADES,
                    reason=reason,
                    should_pause=True,
                    details={"consecutive_losses": self._global_losses},
                )

            self.save_state()
            return ProtectionReturn(triggered=False)

    def on_trade_close(self, symbol: str, pnl: float) -> None:
        """平仓事件：更新连续亏损计数"""
        max_losses = self.config.get("max_consecutive_losses", 5)
        per_symbol = self.config.get("per_symbol", False)
        pause_hours = self.config.get("pause_hours", 4.0)

        with self._lock:
            if pnl > 0:
                # 盈利：重置计数
                self._global_losses = 0
                if symbol in self._symbol_losses:
                    self._symbol_losses[symbol] = 0
            else:
                # 亏损：递增计数
                self._global_losses += 1
                self._symbol_losses[symbol] = self._symbol_losses.get(symbol, 0) + 1

                # per_symbol 模式：检查该交易对是否达阈值
                if per_symbol and self._symbol_losses.get(symbol, 0) >= max_losses:
                    lock_until = datetime.now() + timedelta(hours=pause_hours)
                    self._locked_symbols[symbol] = lock_until.isoformat()
                    logger.warning(
                        "连续亏损保护: %s 连续亏损 %d 次，锁定至 %s",
                        symbol,
                        self._symbol_losses[symbol],
                        lock_until.strftime("%H:%M"),
                    )
                    self._send_cloud_event(symbol, self._symbol_losses[symbol], max_losses)

            self.save_state()

    def is_symbol_locked(self, symbol: str) -> tuple[bool, str]:
        """
        查询指定交易对是否被锁定

        Returns:
            (是否锁定, 锁定原因)
        """
        with self._lock:
            if symbol in self._locked_symbols:
                lock_until_str = self._locked_symbols[symbol]
                lock_until = datetime.fromisoformat(lock_until_str)
                if datetime.now() < lock_until:
                    losses = self._symbol_losses.get(symbol, 0)
                    return True, (
                        f"{symbol} 连续亏损 {losses} 次，锁定至 {lock_until.strftime('%H:%M')}"
                    )
                else:
                    # 锁定已过期
                    del self._locked_symbols[symbol]
                    self._symbol_losses[symbol] = 0
            return False, ""

    def _cleanup_expired_locks(self, now: datetime) -> None:
        """清理已过期的 symbol 锁定"""
        expired = [
            sym
            for sym, until_str in self._locked_symbols.items()
            if now >= datetime.fromisoformat(until_str)
        ]
        for sym in expired:
            del self._locked_symbols[sym]
            self._symbol_losses[sym] = 0

    def _send_cloud_event(self, scope: str, losses: int, threshold: int) -> None:
        """上报风控事件到云端"""
        try:
            from src.utils.cloud_logger import get_cloud_logger

            cloud = get_cloud_logger()
            if cloud:
                cloud.send_risk_event(
                    symbol=scope,
                    risk_type="consecutive_loss",
                    details={
                        "consecutive_losses": losses,
                        "threshold": threshold,
                        "per_symbol": self.config.get("per_symbol", False),
                    },
                    level="warning",
                )
        except Exception as e:
            logger.warning("上报连续亏损风控事件失败: %s", e)

    def _get_state_dict(self) -> dict[str, Any]:
        return {
            "global_losses": self._global_losses,
            "symbol_losses": self._symbol_losses,
            "locked_symbols": self._locked_symbols,
            "is_paused": self._is_paused,
            "pause_reason": self._pause_reason,
            "last_protection_time": (
                self._last_protection_time.isoformat() if self._last_protection_time else None
            ),
        }

    def _restore_state_dict(self, state: dict[str, Any]) -> None:
        self._global_losses = state.get("global_losses", 0)
        self._symbol_losses = state.get("symbol_losses", {})
        self._locked_symbols = state.get("locked_symbols", {})
        self._is_paused = state.get("is_paused", False)
        self._pause_reason = state.get("pause_reason", "")
        lpt = state.get("last_protection_time")
        self._last_protection_time = datetime.fromisoformat(lpt) if lpt else None
