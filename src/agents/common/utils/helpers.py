"""
通用辅助函数

提供类型转换、文本处理、JSON 提取等通用功能。
"""

import json
import logging
import re
from typing import Any


def safe_float(value: Any, default: float = 0.0) -> float:
    """
    安全地将值转换为 float

    Args:
        value: 要转换的值
        default: 转换失败时的默认值

    Returns:
        转换后的 float 值或默认值

    Examples:
        >>> safe_float("123.45")
        123.45
        >>> safe_float(None)
        0.0
        >>> safe_float("invalid", default=-1.0)
        -1.0
    """
    try:
        if value is None:
            return default
        return float(value)
    except (ValueError, TypeError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    """
    安全地将值转换为 int

    Args:
        value: 要转换的值
        default: 转换失败时的默认值

    Returns:
        转换后的 int 值或默认值

    Examples:
        >>> safe_int("123")
        123
        >>> safe_int(45.6)
        45
        >>> safe_int(None)
        0
    """
    try:
        if value is None:
            return default
        return int(value)
    except (ValueError, TypeError):
        return default


def safe_leverage(leverage_data: Any, default: int = 1) -> int:
    """
    安全地从 leverage 数据中提取杠杆倍数

    Hyperliquid API 返回的 leverage 字段格式:
    {
        "type": "cross" | "isolated",
        "value": 10
    }

    Args:
        leverage_data: leverage 数据，可能是字典、数字或 None
        default: 提取失败时的默认值

    Returns:
        杠杆倍数（整数）

    Examples:
        >>> safe_leverage({"type": "cross", "value": 10})
        10
        >>> safe_leverage(5)
        5
        >>> safe_leverage(None)
        1
    """
    try:
        if leverage_data is None:
            return default

        # 如果是字典，尝试提取 value 字段
        if isinstance(leverage_data, dict):
            value = leverage_data.get("value", default)
            return int(value)

        # 如果直接是数字，转换为整数
        return int(leverage_data)
    except (ValueError, TypeError, KeyError):
        return default


def shorten_text(text: str, limit: int = 140) -> str:
    """
    裁剪长文本，减少 token 消耗

    Args:
        text: 要裁剪的文本
        limit: 最大长度

    Returns:
        裁剪后的文本

    Examples:
        >>> shorten_text("Hello World", limit=5)
        'He...'
        >>> shorten_text("Short")
        'Short'
    """
    if not text:
        return ""
    clean = re.sub(r"\s+", " ", text).strip()
    return clean if len(clean) <= limit else clean[: limit - 3] + "..."


def extract_json_from_text(text: str) -> dict | None:
    """
    从文本中提取 JSON 对象

    支持以下格式：
    1. Markdown 代码块: ```json {...} ```
    2. 纯 JSON: {...}
    3. 带有文本的混合内容（JSON 对象嵌入在其他文本中）

    Args:
        text: 包含 JSON 对象的文本

    Returns:
        提取的 JSON 对象（字典），如果提取失败则返回 None

    Examples:
        >>> extract_json_from_text('```json\\n{"key": "value"}\\n```')
        {'key': 'value'}
        >>> extract_json_from_text('Some text {"key": "value"} more text')
        {'key': 'value'}
    """
    if not text:
        return None

    # 方法 1: 尝试提取 markdown 代码块中的 JSON
    code_block_markers = [
        ("```json", "```"),
        ("```", "```"),
    ]
    for start_marker, end_marker in code_block_markers:
        start_idx = text.find(start_marker)
        if start_idx != -1:
            start_idx += len(start_marker)
            end_idx = text.find(end_marker, start_idx)
            if end_idx != -1:
                code_content = text[start_idx:end_idx].strip()
                try:
                    return json.loads(code_content)
                except json.JSONDecodeError:
                    continue

    # 方法 2: 提取第一个平衡的 JSON 对象
    start = text.find("{")
    if start != -1:
        stack = []
        for i in range(start, len(text)):
            if text[i] == "{":
                stack.append("{")
            elif text[i] == "}":
                if stack:
                    stack.pop()
                if not stack:
                    json_str = text[start : i + 1]
                    try:
                        return json.loads(json_str)
                    except json.JSONDecodeError:
                        break

    return None


def format_timestamp(dt: Any = None) -> str:
    """
    格式化时间戳

    Args:
        dt: datetime 对象或 None（使用当前时间）

    Returns:
        ISO 格式的时间戳字符串
    """
    from datetime import datetime

    if dt is None:
        dt = datetime.now()
    return dt.isoformat()


def merge_dicts(base: dict, override: dict) -> dict:
    """
    深度合并两个字典

    Args:
        base: 基础字典
        override: 覆盖字典

    Returns:
        合并后的新字典
    """
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_dicts(result[key], value)
        else:
            result[key] = value
    return result


def batch_list(items: list, batch_size: int) -> list[list]:
    """
    将列表分批

    Args:
        items: 要分批的列表
        batch_size: 每批大小

    Returns:
        分批后的列表的列表
    """
    return [items[i : i + batch_size] for i in range(0, len(items), batch_size)]


def send_error_notification(
    notifier: Any,
    exception: Exception,
    title: str,
    context_details: dict[str, str],
) -> None:
    """
    安全地发送错误通知

    自动包含异常类型，内部捕获通知失败以避免影响调用方的控制流。

    Args:
        notifier: 通知管理器实例（可为 None）
        exception: 捕获的异常对象
        title: 通知标题
        context_details: 上下文信息字典（如 交易对、阶段、说明等）
    """
    if not notifier:
        return

    try:
        context_lines = [f"异常类型: {type(exception).__name__}"]
        for key, value in context_details.items():
            if value is not None:
                context_lines.append(f"{key}: {value}")

        notifier.notify_error(
            title=title,
            error_message=str(exception),
            context="\n".join(context_lines),
        )
    except Exception as notify_err:
        logger = logging.getLogger(__name__)
        logger.error(f"错误通知发送失败: {notify_err}", exc_info=True)
