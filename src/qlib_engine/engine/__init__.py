"""
QLib 引擎层

核心引擎，负责初始化、调度和协调各层组件。
"""

from .experiment import ExperimentManager, ExperimentRecord
from .qlib_engine import QuantFlowQLibEngine

__all__ = [
    "QuantFlowQLibEngine",
    "OnlineModelManager",
    "ExperimentManager",
    "ExperimentRecord",
]


def __getattr__(name):
    """延迟导入 OnlineModelManager（依赖链较深）"""
    if name == "OnlineModelManager":
        from .online import OnlineModelManager
        return OnlineModelManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
