"""
QLib 量化引擎模块

基于微软 QLib 框架的量化决策核心，取代纯 LLM 驱动的决策方式。
QLib 为主脑（数据驱动、可量化、可回测），LLM 为顾问（情报分析、定性补充）。
"""

from .engine.experiment import ExperimentManager
from .engine.qlib_engine import QuantFlowQLibEngine

__all__ = [
    "QuantFlowQLibEngine",
    "OnlineModelManager",
    "ExperimentManager",
]


def __getattr__(name):
    """延迟导入依赖外部 SDK 的模块"""
    if name == "OnlineModelManager":
        from .engine.online import OnlineModelManager
        return OnlineModelManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
