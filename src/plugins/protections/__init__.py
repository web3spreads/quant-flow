"""
保护插件包
提供注册表和所有内置保护插件的自动注册。
"""

from src.plugins.protections.base import (
    IProtection,
    ProtectionAction,
    ProtectionContext,
    ProtectionReturn,
)
from src.plugins.protections.consecutive_loss import ConsecutiveLossProtection
from src.plugins.protections.daily_loss import DailyLossProtection
from src.plugins.protections.drawdown import MaxDrawdownProtection
from src.plugins.protections.manager import ProtectionManager
from src.plugins.protections.position_timeout import PositionTimeoutProtection

# 注册表：插件名称 → 类
PROTECTION_REGISTRY: dict[str, type[IProtection]] = {
    "max_drawdown": MaxDrawdownProtection,
    "daily_loss": DailyLossProtection,
    "consecutive_loss": ConsecutiveLossProtection,
    "position_timeout": PositionTimeoutProtection,
}

__all__ = [
    "PROTECTION_REGISTRY",
    "IProtection",
    "ProtectionAction",
    "ProtectionContext",
    "ProtectionManager",
    "ProtectionReturn",
    "MaxDrawdownProtection",
    "DailyLossProtection",
    "ConsecutiveLossProtection",
    "PositionTimeoutProtection",
]
