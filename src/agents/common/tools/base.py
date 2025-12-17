"""
工具基类定义

提供工具的基础类和通用数据结构。
"""

from typing import Any, Optional, Dict
from dataclasses import dataclass
from abc import ABC, abstractmethod
from enum import Enum


class ToolStatus(str, Enum):
    """工具执行状态"""
    SUCCESS = "success"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass
class ToolResult:
    """
    工具执行结果

    统一的工具返回格式，便于状态管理和错误处理。
    """
    status: ToolStatus
    message: str
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        result = {
            "status": self.status.value,
            "message": self.message,
        }
        if self.data:
            result["data"] = self.data
        if self.error:
            result["error"] = self.error
        return result

    @property
    def is_success(self) -> bool:
        """是否执行成功"""
        return self.status == ToolStatus.SUCCESS

    def __str__(self) -> str:
        """字符串表示"""
        if self.is_success:
            return f"✅ {self.message}"
        return f"❌ {self.message}" + (f": {self.error}" if self.error else "")


class ToolError(Exception):
    """
    工具执行异常

    用于在工具执行过程中抛出的可恢复错误。
    """

    def __init__(self, message: str, details: Optional[Dict] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_result(self) -> ToolResult:
        """转换为 ToolResult"""
        return ToolResult(
            status=ToolStatus.ERROR,
            message=self.message,
            error=str(self),
            data=self.details,
        )


class BaseTool(ABC):
    """
    工具基类

    定义工具的基本接口，子类需实现 execute 方法。
    """

    def __init__(self, name: str, description: str):
        """
        初始化工具

        Args:
            name: 工具名称
            description: 工具描述
        """
        self.name = name
        self.description = description

    @abstractmethod
    def execute(self, **kwargs) -> ToolResult:
        """
        执行工具

        Args:
            **kwargs: 工具参数

        Returns:
            ToolResult: 执行结果
        """
        pass

    def validate_params(self, **kwargs) -> Optional[str]:
        """
        验证参数

        Args:
            **kwargs: 工具参数

        Returns:
            验证错误信息，如果验证通过则返回 None
        """
        return None

    def __call__(self, **kwargs) -> ToolResult:
        """
        调用工具

        提供便捷的调用方式，自动进行参数验证。
        """
        error = self.validate_params(**kwargs)
        if error:
            return ToolResult(
                status=ToolStatus.ERROR,
                message=f"参数验证失败: {error}",
                error=error,
            )
        try:
            return self.execute(**kwargs)
        except ToolError as e:
            return e.to_result()
        except Exception as e:
            return ToolResult(
                status=ToolStatus.ERROR,
                message=f"工具执行异常: {self.name}",
                error=str(e),
            )
