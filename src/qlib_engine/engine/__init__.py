"""
QLib 引擎层

核心引擎，负责初始化、调度和协调各层组件。
"""

from .experiment import ExperimentManager, ExperimentRecord
from .qlib_engine import QuantFlowQLibEngine


def get_dynamic_retrain_interval(
    sample_count: int,
    config_hours: float = 168,
) -> float:
    """
    根据样本量计算动态重训练间隔（小时）

    共享逻辑，供 QuantFlowQLibEngine 和 OnlineModelManager 使用。

    规则：
    - < 500 样本：min(6h, config_hours)
    - 500-2000 样本：min(4h, config_hours)
    - >= 2000 样本：config_hours

    Args:
        sample_count: 当前样本量
        config_hours: 配置中的重训练间隔（小时）

    Returns:
        动态计算的重训练间隔（小时）
    """
    if sample_count < 500:
        return min(6.0, config_hours)
    elif sample_count < 2000:
        return min(4.0, config_hours)
    else:
        return config_hours


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
