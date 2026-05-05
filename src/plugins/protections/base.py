"""
保护插件基础架构
定义 IProtection 抽象基类、ProtectionReturn、ProtectionContext 等核心数据结构。
"""

import json
import logging
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ProtectionAction(StrEnum):
    """保护动作类型"""

    NONE = "none"
    PAUSE_NEW_TRADES = "pause_new_trades"
    CLOSE_ALL_POSITIONS = "close_all_positions"


@dataclass
class ProtectionReturn:
    """单个保护插件的检查结果"""

    triggered: bool
    action: ProtectionAction = ProtectionAction.NONE
    reason: str = ""
    should_pause: bool = False
    affected_symbols: list[str] | None = None  # None = 全局
    details: dict[str, Any] = field(default_factory=dict)
    plugin_name: str = ""  # 由 ProtectionManager 在分发时填入，便于调用方识别来源


@dataclass
class ProtectionContext:
    """传递给各插件的上下文"""

    balance: float
    equity: float
    unrealized_pnl: float
    margin_used: float
    current_positions: list[dict[str, Any]]
    timestamp: datetime = field(default_factory=datetime.now)


class IProtection(ABC):
    """保护插件抽象基类"""

    def __init__(self, config: dict[str, Any], data_dir: Path | None = None):
        """
        初始化保护插件

        Args:
            config: 插件配置字典
            data_dir: 数据存储根目录（插件会在其下创建以 name 命名的子目录）
        """
        self.config = config
        self.enabled = config.get("enabled", True)
        self._data_dir = (data_dir or Path("data/protection")) / self.name
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._state_file = self._data_dir / "state.json"
        self.load_state()

    @property
    @abstractmethod
    def name(self) -> str:
        """插件名称（用于注册表和状态文件目录）"""
        ...

    @abstractmethod
    def check(self, context: ProtectionContext) -> ProtectionReturn:
        """
        执行保护检查

        Args:
            context: 当前账户和持仓上下文

        Returns:
            ProtectionReturn: 检查结果
        """
        ...

    def on_trade_open(
        self,
        symbol: str,
        entry_price: float,
        size: float,
        is_long: bool,
        leverage: int = 1,
        timestamp: datetime | None = None,
    ) -> None:
        """开仓事件回调（子类按需覆盖）"""
        return  # noqa: B027 — 非抽象，子类按需覆盖

    def on_trade_close(self, symbol: str, pnl: float, timestamp: datetime | None = None) -> None:
        """平仓事件回调（子类按需覆盖）"""
        return  # noqa: B027 — 非抽象，子类按需覆盖

    def save_state(self) -> None:
        """保存插件状态到 JSON 文件"""
        state = self._get_state_dict()
        if state is None:
            return
        try:
            tmp_file = self._state_file.with_suffix(".tmp")
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, default=str)
            tmp_file.replace(self._state_file)
        except Exception as e:
            logger.warning("保存 %s 状态失败: %s", self.name, e)

    def load_state(self) -> None:
        """从 JSON 文件加载插件状态"""
        if not self._state_file.exists():
            return
        try:
            with open(self._state_file, encoding="utf-8") as f:
                state = json.load(f)
            self._restore_state_dict(state)
        except Exception as e:
            logger.warning("加载 %s 状态失败: %s", self.name, e)

    def _get_state_dict(self) -> dict[str, Any] | None:
        """返回需要持久化的状态字典（子类覆盖）"""
        return None

    def _restore_state_dict(self, state: dict[str, Any]) -> None:
        """从字典恢复状态（子类覆盖）"""
        return  # noqa: B027 — 非抽象，子类按需覆盖

    def reset(self) -> None:
        """重置插件状态（磁盘 + 内存）"""
        with self._lock:
            if self._state_file.exists():
                self._state_file.unlink()
            self._reset_state()

    def _reset_state(self) -> None:
        """重置内存状态（子类覆盖）"""
        return  # noqa: B027 — 非抽象，子类按需覆盖
