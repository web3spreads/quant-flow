"""可调用对象的签名探测工具。

用于向后兼容地扩展回调/插件签名：给既有接口新增参数后，仍需支持没跟着升级的
旧实现（自定义接线、回测桩、第三方插件）。
"""

import inspect
from typing import Any


def accepts_parameter(func: Any, name: str) -> bool:
    """判断 ``func`` 能否接受名为 ``name`` 的参数。

    ``*args`` / ``**kwargs`` 视为能接受。

    **为什么不用 try/except TypeError 降级重调**：被调方内部自己抛出 TypeError 时，
    会被误判成「签名不兼容」而按旧签名重调一次——对于上报盈亏这类带副作用的调用，
    等于同一笔事件被计两次，凭空制造连亏。签名探测没有这个歧义。

    取不到签名（C 扩展、内建函数、部分 mock）时返回 True，即按新签名调用：现役
    实现都已升级，误判成旧签名会静默丢掉参数，比调用失败更难发现。

    调用方应缓存结果——``inspect.signature`` 实测约 46 微秒/次，不适合放在每次
    事件分发的热路径里重复求值。
    """
    try:
        params = inspect.signature(func).parameters
    except (TypeError, ValueError):
        return True

    for param in params.values():
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            return True
    return name in params
