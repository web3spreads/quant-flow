"""
通知模块 - 支持钉钉、飞书、邮件等多种通知方式
"""

from .notifier import Notifier, NotificationEvent

__all__ = ["Notifier", "NotificationEvent"]
