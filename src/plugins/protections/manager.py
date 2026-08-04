"""
保护插件管理器
基于注册表模式编排多个保护插件，提供统一的检查和事件分发接口。
"""

import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from src.plugins.protections.base import (
    IProtection,
    ProtectionAction,
    ProtectionContext,
    ProtectionReturn,
)
from src.utils.introspect import accepts_parameter

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
        # 触发日志去重：暂停期内每个周期都会重复触发同一原因（线上单次熔断刷出 726 条
        # 重复 WARNING），仅在原因变化时用 WARNING，重复时降为 DEBUG。
        self._last_trigger_reason: dict[str, str] = {}
        # 插件名 → on_trade_close 是否接受 forced 参数（注册时一次性探测，见下）
        self._accepts_forced: dict[str, bool] = {}

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
            # 签名探测在注册时做一次即可：插件实例此后不变，而 inspect.signature
            # 约 46 微秒/次，放在每次事件分发里是白烧
            self._accepts_forced[plugin.name] = accepts_parameter(plugin.on_trade_close, "forced")
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
                    result.plugin_name = plugin.name
                    results.append(result)
                    if self._last_trigger_reason.get(plugin.name) == result.reason:
                        # 暂停期内同一原因重复触发：降为 DEBUG，避免刷屏淹没真实事件
                        logger.debug(
                            "保护插件 %s 持续触发中: %s (动作: %s)",
                            plugin.name,
                            result.reason,
                            result.action,
                        )
                    else:
                        self._last_trigger_reason[plugin.name] = result.reason
                        logger.warning(
                            "保护插件 %s 触发: %s (动作: %s)",
                            plugin.name,
                            result.reason,
                            result.action,
                        )
                    if self._on_triggered:
                        self._on_triggered(result.reason)
                else:
                    # 恢复正常后清除去重记录：下次再触发（哪怕同一原因）重新用 WARNING
                    self._last_trigger_reason.pop(plugin.name, None)
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

    def is_symbol_locked(self, symbol: str, timestamp: datetime | None = None) -> tuple[bool, str]:
        """
        查询指定交易对是否被锁定

        Args:
            symbol: 交易对符号
            timestamp: 当前时间戳（回测时传入模拟时间，默认 datetime.now()）

        Returns:
            (是否锁定, 锁定原因)
        """
        for plugin in self._plugins:
            if not plugin.enabled:
                continue
            if hasattr(plugin, "is_symbol_locked"):
                locked, reason = plugin.is_symbol_locked(symbol, timestamp=timestamp)
                if locked:
                    return True, reason
        return False, ""

    def get_timeout_symbols(self, timestamp: datetime | None = None) -> list[str]:
        """
        从支持超时检测的插件中获取所有超时持仓符号

        Args:
            timestamp: 当前时间戳（回测时传入模拟时间，默认 datetime.now()）

        Returns:
            超时持仓的交易对符号列表
        """
        result: list[str] = []
        for plugin in self._plugins:
            if not plugin.enabled:
                continue
            if hasattr(plugin, "get_timeout_symbols"):
                result.extend(plugin.get_timeout_symbols(timestamp=timestamp))
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
        """分发开仓事件到所有插件"""
        for plugin in self._plugins:
            if not plugin.enabled:
                continue
            try:
                plugin.on_trade_open(
                    symbol, entry_price, size, is_long, leverage, timestamp=timestamp
                )
            except Exception as e:
                logger.error("插件 %s on_trade_open 异常: %s", plugin.name, e)

    def on_trade_close(
        self,
        symbol: str,
        pnl: float,
        timestamp: datetime | None = None,
        forced: bool = False,
    ) -> None:
        """分发平仓事件到所有插件（forced=True 表示风控强制平仓，见 IProtection 文档）"""
        for plugin in self._plugins:
            if not plugin.enabled:
                continue
            try:
                # 兼容未升级签名的自定义插件：不支持 forced 参数时退回旧调用。
                # 判定结果在插件注册时已探测并缓存，此处不再反射。
                if self._accepts_forced.get(plugin.name, True):
                    plugin.on_trade_close(symbol, pnl, timestamp=timestamp, forced=forced)
                else:
                    plugin.on_trade_close(symbol, pnl, timestamp=timestamp)
            except Exception as e:
                logger.error("插件 %s on_trade_close 异常: %s", plugin.name, e)

    def on_position_dropped(self, symbol: str) -> None:
        """
        分发「持仓被风控强制平仓」事件到所有插件。

        用于回撤强平 / 超时强平等风控主动平仓场景：仅让维护持仓状态的插件
        （如 position_timeout）清理其内部记录，不向基于盈亏的插件
        （如 consecutive_loss）上报虚假 pnl。
        """
        for plugin in self._plugins:
            if not plugin.enabled:
                continue
            try:
                plugin.on_position_dropped(symbol)
            except Exception as e:
                logger.error("插件 %s on_position_dropped 异常: %s", plugin.name, e)
