"""
保护插件管理器
基于注册表模式编排多个保护插件，提供统一的检查和事件分发接口。
"""

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.plugins.protections.base import (
    IProtection,
    ProtectionAction,
    ProtectionContext,
    ProtectionReturn,
)

logger = logging.getLogger(__name__)


class ProtectionManager:
    """保护插件管理器"""

    def __init__(
        self,
        protections_config: list[dict[str, Any]],
        data_dir: Path | None = None,
        on_protection_triggered: Callable[[str], None] | None = None,
    ):
        """
        初始化保护管理器

        Args:
            protections_config: 保护插件配置列表（来自 config.yaml 的 protections 块）
            data_dir: 状态文件根目录
            on_protection_triggered: 保护触发时的回调函数
        """
        from src.plugins.protections import PROTECTION_REGISTRY

        self._plugins: list[IProtection] = []
        self._on_triggered = on_protection_triggered
        self._data_dir = data_dir or Path("data/protection")

        for cfg in protections_config:
            name = cfg.get("name", "")
            enabled = cfg.get("enabled", True)
            if not enabled:
                logger.info("保护插件 %s 已禁用，跳过", name)
                continue

            cls = PROTECTION_REGISTRY.get(name)
            if cls is None:
                logger.warning("未知的保护插件: %s，跳过", name)
                continue

            plugin = cls(config=cfg, data_dir=self._data_dir)
            self._plugins.append(plugin)
            logger.info("已加载保护插件: %s", name)

    @property
    def plugins(self) -> list[IProtection]:
        """返回已加载的插件列表"""
        return list(self._plugins)

    def check_all(self, context: ProtectionContext) -> list[ProtectionReturn]:
        """
        顺序执行所有保护插件的检查

        Args:
            context: 当前账户和持仓上下文

        Returns:
            所有触发的保护结果列表
        """
        results: list[ProtectionReturn] = []
        for plugin in self._plugins:
            if not plugin.enabled:
                continue
            try:
                result = plugin.check(context)
                if result.triggered:
                    results.append(result)
                    logger.warning(
                        "保护插件 %s 触发: %s (动作: %s)",
                        plugin.name,
                        result.reason,
                        result.action,
                    )
                    if self._on_triggered:
                        self._on_triggered(result.reason)
            except Exception as e:
                logger.error("保护插件 %s 检查异常: %s", plugin.name, e)
        return results

    @staticmethod
    def get_most_severe_action(results: list[ProtectionReturn]) -> ProtectionAction:
        """
        从多个保护结果中取最严重的动作

        优先级: CLOSE_ALL_POSITIONS > PAUSE_NEW_TRADES > NONE
        """
        if not results:
            return ProtectionAction.NONE

        severity = {
            ProtectionAction.NONE: 0,
            ProtectionAction.PAUSE_NEW_TRADES: 1,
            ProtectionAction.CLOSE_ALL_POSITIONS: 2,
        }
        return max(
            (r.action for r in results),
            key=lambda a: severity.get(a, 0),
        )

    def is_symbol_locked(self, symbol: str) -> tuple[bool, str]:
        """
        查询指定交易对是否被锁定

        Args:
            symbol: 交易对符号

        Returns:
            (是否锁定, 锁定原因)
        """
        for plugin in self._plugins:
            if not plugin.enabled:
                continue
            if hasattr(plugin, "is_symbol_locked"):
                locked, reason = plugin.is_symbol_locked(symbol)
                if locked:
                    return True, reason
        return False, ""

    def get_timeout_symbols(self) -> list[str]:
        """
        从支持超时检测的插件中获取所有超时持仓符号

        Returns:
            超时持仓的交易对符号列表
        """
        result: list[str] = []
        for plugin in self._plugins:
            if not plugin.enabled:
                continue
            if hasattr(plugin, "get_timeout_symbols"):
                result.extend(plugin.get_timeout_symbols())
        return result

    def on_trade_open(
        self,
        symbol: str,
        entry_price: float,
        size: float,
        is_long: bool,
        leverage: int = 1,
    ) -> None:
        """分发开仓事件到所有插件"""
        for plugin in self._plugins:
            if not plugin.enabled:
                continue
            try:
                plugin.on_trade_open(symbol, entry_price, size, is_long, leverage)
            except Exception as e:
                logger.error("插件 %s on_trade_open 异常: %s", plugin.name, e)

    def on_trade_close(self, symbol: str, pnl: float) -> None:
        """分发平仓事件到所有插件"""
        for plugin in self._plugins:
            if not plugin.enabled:
                continue
            try:
                plugin.on_trade_close(symbol, pnl)
            except Exception as e:
                logger.error("插件 %s on_trade_close 异常: %s", plugin.name, e)
