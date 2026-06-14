"""
最大回撤保护插件
当账户净值从峰值回撤超过阈值时，触发全部平仓并暂停交易。
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


class MaxDrawdownProtection(IProtection):
    """最大回撤保护"""

    @property
    def name(self) -> str:
        return "max_drawdown"

    def __init__(self, **kwargs):
        self._peak_equity: float = 0.0
        self._is_paused: bool = False
        self._pause_reason: str = ""
        self._last_protection_time: datetime | None = None
        super().__init__(**kwargs)

    def check(self, context: ProtectionContext) -> ProtectionReturn:
        """检查最大回撤"""
        max_drawdown_pct = self.config.get("max_drawdown_pct", 0.10)
        pause_hours = self.config.get("pause_hours", 4.0)

        with self._lock:
            # 净值非法守卫：行情/接口抖动导致 equity<=0 时，若继续计算会算出巨大回撤
            # （(peak-0)/peak≈100%）误触发 CLOSE_ALL 平掉全部=实亏，或用坏值污染峰值。
            # 遇到非法净值直接跳过本次检查，不更新峰值、不触发。
            if context.equity <= 0:
                logger.warning("最大回撤保护跳过：净值非法 (%.4f)", context.equity)
                return ProtectionReturn(triggered=False)

            # 更新峰值
            if context.equity > self._peak_equity:
                self._peak_equity = context.equity

            # 检查暂停期是否已过
            if self._is_paused and self._last_protection_time:
                elapsed = (context.timestamp - self._last_protection_time).total_seconds() / 3600
                if elapsed >= pause_hours:
                    self._is_paused = False
                    self._pause_reason = ""
                    logger.info("最大回撤保护暂停期已过，恢复交易")

            # 如果仍在暂停中
            if self._is_paused:
                return ProtectionReturn(
                    triggered=True,
                    action=ProtectionAction.PAUSE_NEW_TRADES,
                    reason=self._pause_reason,
                    should_pause=True,
                    details={"peak_equity": self._peak_equity},
                )

            # 计算回撤
            if self._peak_equity <= 0:
                self.save_state()
                return ProtectionReturn(triggered=False)

            drawdown_pct = (self._peak_equity - context.equity) / self._peak_equity

            if drawdown_pct >= max_drawdown_pct:
                reason = (
                    f"最大回撤保护触发: 回撤 {drawdown_pct:.1%} >= 阈值 {max_drawdown_pct:.1%} "
                    f"(峰值 ${self._peak_equity:.2f} → 当前 ${context.equity:.2f})"
                )
                self._is_paused = True
                self._pause_reason = reason
                self._last_protection_time = context.timestamp
                self.save_state()

                self._send_cloud_event(drawdown_pct, max_drawdown_pct)

                return ProtectionReturn(
                    triggered=True,
                    action=ProtectionAction.CLOSE_ALL_POSITIONS,
                    reason=reason,
                    should_pause=True,
                    details={
                        "drawdown_pct": drawdown_pct,
                        "peak_equity": self._peak_equity,
                        "current_equity": context.equity,
                    },
                )

            self.save_state()
            return ProtectionReturn(triggered=False)

    def _send_cloud_event(self, drawdown_pct: float, threshold: float) -> None:
        """上报风控事件到云端"""
        try:
            from src.utils.cloud_logger import get_cloud_logger

            cloud = get_cloud_logger()
            if cloud:
                cloud.send_risk_event(
                    symbol="ALL",
                    risk_type="max_drawdown",
                    details={
                        "drawdown_pct": drawdown_pct,
                        "threshold": threshold,
                        "peak_equity": self._peak_equity,
                    },
                    level="error",
                )
        except Exception as e:
            logger.warning("上报回撤风控事件失败: %s", e)

    def _reset_state(self) -> None:
        self._peak_equity = 0.0
        self._is_paused = False
        self._pause_reason = ""
        self._last_protection_time = None

    def _get_state_dict(self) -> dict[str, Any]:
        return {
            "peak_equity": self._peak_equity,
            "is_paused": self._is_paused,
            "pause_reason": self._pause_reason,
            "last_protection_time": (
                self._last_protection_time.isoformat() if self._last_protection_time else None
            ),
        }

    def _restore_state_dict(self, state: dict[str, Any]) -> None:
        self._peak_equity = state.get("peak_equity", 0.0)
        self._is_paused = state.get("is_paused", False)
        self._pause_reason = state.get("pause_reason", "")
        lpt = state.get("last_protection_time")
        self._last_protection_time = datetime.fromisoformat(lpt) if lpt else None
