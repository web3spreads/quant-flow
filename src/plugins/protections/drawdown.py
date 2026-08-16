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
        # 疑似坏采样待确认标记（内存态即可：重启后重新确认一轮无碍）
        self._suspect_pending: bool = False
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

            # 坏采样确认守卫：净值较峰值单次骤降超过 suspect_drop_ratio（默认 50%）
            # 大概率是账户接口降级形态（历史事故：统一账户 marginSummary 只报
            # 被占用抵押，净值被低估近 80%），而非真实亏损——真实回撤会跨周期
            # 持续。首次出现只告警等待下一周期复核；连续两次仍骤降才放行进入
            # 正常回撤判定。CLOSE_ALL 不可逆，宁可迟一个周期也不能被单次坏
            # 采样触发（该场景平仓即实亏）。
            suspect_ratio = float(self.config.get("suspect_drop_ratio", 0.5) or 0)
            if 0 < suspect_ratio < 1 and self._peak_equity > 0:
                collapsed = context.equity < self._peak_equity * (1 - suspect_ratio)
                if collapsed and not self._suspect_pending:
                    self._suspect_pending = True
                    logger.critical(
                        "最大回撤保护：净值 $%.2f 较峰值 $%.2f 骤降逾 %.0f%%，"
                        "疑似账户接口坏采样，等待下一周期确认后再判定回撤",
                        context.equity,
                        self._peak_equity,
                        suspect_ratio * 100,
                    )
                    return ProtectionReturn(triggered=False)
                # 连续两周期骤降=确认为真实回撤，清掉标记进入正常判定；
                # 净值恢复同样清掉标记（单次坏采样已被吸收）
                self._suspect_pending = False

            # 更新峰值
            if context.equity > self._peak_equity:
                self._peak_equity = context.equity

            # 检查暂停期是否已过
            if self._is_paused and self._last_protection_time:
                elapsed = (context.timestamp - self._last_protection_time).total_seconds() / 3600
                if elapsed >= pause_hours:
                    self._is_paused = False
                    self._pause_reason = ""
                    # 冷静期结束后，把高水位峰值重置为当前净值，从新基准重新计量回撤。
                    # 否则峰值永远停在触发前的旧高点：恢复交易后，同一笔已经发生的回撤会
                    # 立刻被再次判定超限而重新暂停——"暂停 N 小时后自动恢复"形同死代码，
                    # 账户被永久锁死（尤其 CLOSE_ALL 平成空仓后净值盯市冻结，永远回不到
                    # 旧峰值）。重置后从新基准继续保护：再跌一个阈值仍会照常触发。
                    self._peak_equity = context.equity
                    self._last_protection_time = None
                    logger.info(
                        "最大回撤保护暂停期已过，恢复交易（高水位重置为当前净值 $%.2f）",
                        context.equity,
                    )

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

            # 绝对额下限（可选，默认 0=关闭）：小账户上纯百分比触发线是噪声级别——
            # 线上 $8.61 账户的 10% 触发线只有 $0.86，一根普通 K 线即可击穿；且冷静期后
            # 高水位重置使熔断变成「下跌节拍器」（$12.64→$11.37→$10.21→$8.94→$7.71，
            # 每级恰好 ~10%）。要求回撤绝对额同时达标可避免噪声级触发。
            min_drawdown_usd = float(self.config.get("min_drawdown_usd", 0) or 0)
            drawdown_usd = self._peak_equity - context.equity
            if min_drawdown_usd > 0 and drawdown_usd < min_drawdown_usd:
                self.save_state()
                return ProtectionReturn(triggered=False)

            if drawdown_pct >= max_drawdown_pct:
                reason = (
                    f"最大回撤保护触发: 回撤 {drawdown_pct:.1%} >= 阈值 {max_drawdown_pct:.1%} "
                    f"(峰值 ${self._peak_equity:.2f} → 当前 ${context.equity:.2f})"
                )
                self._is_paused = True
                self._pause_reason = reason
                self._last_protection_time = context.timestamp
                self.save_state()

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
