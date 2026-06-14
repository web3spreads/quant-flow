"""
单日亏损保护插件
当日亏损超过阈值时暂停新开仓。每日自动重置基准净值。
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


class DailyLossProtection(IProtection):
    """单日亏损保护"""

    @property
    def name(self) -> str:
        return "daily_loss"

    def __init__(self, **kwargs):
        self._daily_start_equity: float = 0.0
        self._daily_start_date: str = ""
        self._is_paused: bool = False
        self._pause_reason: str = ""
        self._last_protection_time: datetime | None = None
        super().__init__(**kwargs)

    def check(self, context: ProtectionContext) -> ProtectionReturn:
        """检查单日亏损"""
        max_daily_loss_pct = self.config.get("max_daily_loss_pct", 0.05)
        pause_hours = self.config.get("pause_hours", 4.0)

        with self._lock:
            # 净值非法守卫：equity<=0 会污染日初基准并算出错误日亏损率，跳过本次检查
            if context.equity <= 0:
                logger.warning("单日亏损保护跳过：净值非法 (%.4f)", context.equity)
                return ProtectionReturn(triggered=False)

            today = context.timestamp.strftime("%Y-%m-%d")

            # 判断当前暂停是否仍在 pause_hours 冷却期内：跨天也必须遵守该冷却，
            # 不能因日期翻转（如 23:00 触发、00:00 跨天）提前解除暂停。
            pause_still_active = False
            if self._is_paused and self._last_protection_time:
                elapsed_h = (
                    context.timestamp - self._last_protection_time
                ).total_seconds() / 3600
                pause_still_active = elapsed_h < pause_hours

            # 新的一天，重置日亏损基准；但暂停冷却未到期时保留暂停状态
            if today != self._daily_start_date:
                self._daily_start_equity = context.equity
                self._daily_start_date = today
                if not pause_still_active:
                    self._is_paused = False
                    self._pause_reason = ""

            # 首次检查时设置基准
            if self._daily_start_equity <= 0:
                self._daily_start_equity = context.equity
                self.save_state()
                return ProtectionReturn(triggered=False)

            # 检查暂停期是否已过
            if self._is_paused and self._last_protection_time:
                elapsed = (context.timestamp - self._last_protection_time).total_seconds() / 3600
                if elapsed >= pause_hours:
                    self._is_paused = False
                    self._pause_reason = ""
                    logger.info("单日亏损保护暂停期已过，恢复交易")

            if self._is_paused:
                return ProtectionReturn(
                    triggered=True,
                    action=ProtectionAction.PAUSE_NEW_TRADES,
                    reason=self._pause_reason,
                    should_pause=True,
                )

            # 计算日亏损
            daily_loss_pct = (self._daily_start_equity - context.equity) / self._daily_start_equity

            if daily_loss_pct >= max_daily_loss_pct:
                reason = (
                    f"单日亏损保护触发: 日亏损 {daily_loss_pct:.1%} >= 阈值 {max_daily_loss_pct:.1%} "
                    f"(日初 ${self._daily_start_equity:.2f} → 当前 ${context.equity:.2f})"
                )
                self._is_paused = True
                self._pause_reason = reason
                self._last_protection_time = context.timestamp
                self.save_state()

                self._send_cloud_event(daily_loss_pct, max_daily_loss_pct)

                return ProtectionReturn(
                    triggered=True,
                    action=ProtectionAction.PAUSE_NEW_TRADES,
                    reason=reason,
                    should_pause=True,
                    details={
                        "daily_loss_pct": daily_loss_pct,
                        "daily_start_equity": self._daily_start_equity,
                        "current_equity": context.equity,
                    },
                )

            self.save_state()
            return ProtectionReturn(triggered=False)

    def _send_cloud_event(self, loss_pct: float, threshold: float) -> None:
        """上报风控事件到云端"""
        try:
            from src.utils.cloud_logger import get_cloud_logger

            cloud = get_cloud_logger()
            if cloud:
                cloud.send_risk_event(
                    symbol="ALL",
                    risk_type="daily_loss",
                    details={
                        "daily_loss_pct": loss_pct,
                        "threshold": threshold,
                        "daily_start_equity": self._daily_start_equity,
                    },
                    level="error",
                )
        except Exception as e:
            logger.warning("上报日亏损风控事件失败: %s", e)

    def _reset_state(self) -> None:
        self._daily_start_equity = 0.0
        self._daily_start_date = ""
        self._is_paused = False
        self._pause_reason = ""
        self._last_protection_time = None

    def _get_state_dict(self) -> dict[str, Any]:
        return {
            "daily_start_equity": self._daily_start_equity,
            "daily_start_date": self._daily_start_date,
            "is_paused": self._is_paused,
            "pause_reason": self._pause_reason,
            "last_protection_time": (
                self._last_protection_time.isoformat() if self._last_protection_time else None
            ),
        }

    def _restore_state_dict(self, state: dict[str, Any]) -> None:
        self._daily_start_equity = state.get("daily_start_equity", 0.0)
        self._daily_start_date = state.get("daily_start_date", "")
        self._is_paused = state.get("is_paused", False)
        self._pause_reason = state.get("pause_reason", "")
        lpt = state.get("last_protection_time")
        self._last_protection_time = datetime.fromisoformat(lpt) if lpt else None
