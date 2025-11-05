"""
通知管理器 - 使用 Apprise 库实现多渠道通知
支持钉钉、飞书、邮件等多种通知方式
"""

from enum import Enum
from typing import Dict, List, Optional, Any
import logging
from apprise import Apprise


class NotificationEvent(str, Enum):
    """通知事件类型"""
    TRADE_OPENED = "trade_opened"           # 开仓
    TRADE_CLOSED = "trade_closed"           # 平仓
    STOP_LOSS = "stop_loss"                 # 止损
    TAKE_PROFIT = "take_profit"             # 止盈
    SPOT_INVESTMENT = "spot_investment"     # 现货定投
    ERROR = "error"                         # 错误
    CIRCUIT_BREAKER = "circuit_breaker"     # 熔断


class Notifier:
    """
    通知管理器 - 基于 Apprise 库的统一通知接口

    特性：
    - 支持多种通知渠道（钉钉、飞书、邮件等）
    - 灵活的事件过滤配置
    - 优雅的错误处理
    - 作为配置项，用户配置则启用
    """

    def __init__(self, config: Dict[str, Any]):
        """
        初始化通知管理器

        Args:
            config: 通知配置字典
        """
        self.logger = logging.getLogger(__name__)
        self.config = config
        self.enabled = config.get("enabled", False)
        self.apprise = Apprise()

        # 如果通知功能未启用，则不初始化
        if not self.enabled:
            self.logger.info("📢 通知系统未启用")
            return

        # 初始化通知渠道
        self._init_channels()

        # 获取事件配置
        self.events_config = config.get("events", {})

        self.logger.info("✅ 通知系统初始化成功")

    def _init_channels(self):
        """初始化通知渠道"""
        channels = self.config.get("channels", [])

        if not channels:
            self.logger.warning("⚠️ 未配置任何通知渠道")
            return

        for channel in channels:
            try:
                channel_type = channel.get("type")
                channel_enabled = channel.get("enabled", True)

                if not channel_enabled:
                    self.logger.info(f"跳过已禁用的通知渠道: {channel_type}")
                    continue

                # 根据渠道类型构建 Apprise URL
                if channel_type == "dingtalk":
                    self._add_dingtalk_channel(channel)
                elif channel_type == "feishu":
                    self._add_feishu_channel(channel)
                elif channel_type == "email":
                    self._add_email_channel(channel)
                else:
                    self.logger.warning(f"⚠️ 不支持的通知渠道类型: {channel_type}")

            except Exception as e:
                self.logger.error(f"❌ 初始化通知渠道失败: {e}", exc_info=True)

    def _add_dingtalk_channel(self, channel: Dict[str, Any]):
        """添加钉钉通知渠道"""
        api_key = channel.get("api_key")
        secret = channel.get("secret", "")
        phone_numbers = channel.get("phone_numbers", [])

        if not api_key:
            self.logger.error("❌ 钉钉渠道缺少 api_key 配置")
            return

        # 构建钉钉 URL
        # 格式: dingtalk://{secret}@{api_key}/{phone1}/{phone2}
        if secret:
            url = f"dingtalk://{secret}@{api_key}"
        else:
            url = f"dingtalk://{api_key}"

        # 添加电话号码
        if phone_numbers:
            url += "/" + "/".join(phone_numbers)

        self.apprise.add(url)
        self.logger.info("✅ 钉钉通知渠道已添加")

    def _add_feishu_channel(self, channel: Dict[str, Any]):
        """添加飞书通知渠道"""
        token = channel.get("token")

        if not token:
            self.logger.error("❌ 飞书渠道缺少 token 配置")
            return

        # 构建飞书 URL
        # 格式: feishu://{token}
        url = f"feishu://{token}"

        self.apprise.add(url)
        self.logger.info("✅ 飞书通知渠道已添加")

    def _add_email_channel(self, channel: Dict[str, Any]):
        """添加邮件通知渠道"""
        smtp_user = channel.get("smtp_user")
        smtp_password = channel.get("smtp_password")
        smtp_server = channel.get("smtp_server", "smtp.gmail.com")
        smtp_port = channel.get("smtp_port", 587)
        from_email = channel.get("from_email", smtp_user)
        to_emails = channel.get("to_emails", [])

        if not smtp_user or not smtp_password:
            self.logger.error("❌ 邮件渠道缺少 smtp_user 或 smtp_password 配置")
            return

        if not to_emails:
            self.logger.error("❌ 邮件渠道缺少 to_emails 配置")
            return

        # 构建邮件 URL
        # 格式: mailtos://{user}:{password}@{server}:{port}?from={from}&to={to1},{to2}
        to_emails_str = ",".join(to_emails)
        url = (
            f"mailtos://{smtp_user}:{smtp_password}@{smtp_server}:{smtp_port}"
            f"?from={from_email}&to={to_emails_str}"
        )

        self.apprise.add(url)
        self.logger.info("✅ 邮件通知渠道已添加")

    def should_notify(self, event: NotificationEvent) -> bool:
        """
        判断是否应该发送通知

        Args:
            event: 通知事件类型

        Returns:
            是否应该发送通知
        """
        if not self.enabled:
            return False

        # 检查该事件是否启用通知
        return self.events_config.get(event.value, True)

    def notify(
        self,
        event: NotificationEvent,
        title: str,
        message: str,
        **kwargs
    ) -> bool:
        """
        发送通知

        Args:
            event: 通知事件类型
            title: 通知标题
            message: 通知内容
            **kwargs: 额外的通知参数

        Returns:
            是否发送成功
        """
        # 检查是否应该发送通知
        if not self.should_notify(event):
            self.logger.debug(f"事件 {event.value} 的通知已禁用，跳过发送")
            return False

        try:
            # 使用 Apprise 发送通知
            result = self.apprise.notify(
                title=title,
                body=message
            )

            if result:
                self.logger.info(f"📤 通知发送成功: {title}")
            else:
                self.logger.warning(f"⚠️ 通知发送失败: {title}")

            return result

        except Exception as e:
            self.logger.error(f"❌ 发送通知时发生错误: {e}", exc_info=True)
            return False

    def notify_trade_opened(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        leverage: int = 1
    ):
        """
        发送开仓通知

        Args:
            symbol: 交易对
            side: 方向（long/short）
            quantity: 数量
            price: 价格
            leverage: 杠杆倍数
        """
        side_text = "做多" if side.lower() == "long" else "做空"
        title = f"🔔 开仓通知: {symbol}"
        message = (
            f"交易对: {symbol}\n"
            f"方向: {side_text}\n"
            f"数量: {quantity}\n"
            f"价格: {price}\n"
            f"杠杆: {leverage}x"
        )
        self.notify(NotificationEvent.TRADE_OPENED, title, message)

    def notify_trade_closed(
        self,
        symbol: str,
        side: str,
        quantity: float,
        entry_price: float,
        exit_price: float,
        pnl: float,
        pnl_percent: float
    ):
        """
        发送平仓通知

        Args:
            symbol: 交易对
            side: 方向（long/short）
            quantity: 数量
            entry_price: 开仓价格
            exit_price: 平仓价格
            pnl: 盈亏金额
            pnl_percent: 盈亏百分比
        """
        side_text = "做多" if side.lower() == "long" else "做空"
        pnl_emoji = "💰" if pnl >= 0 else "📉"
        title = f"{pnl_emoji} 平仓通知: {symbol}"
        message = (
            f"交易对: {symbol}\n"
            f"方向: {side_text}\n"
            f"数量: {quantity}\n"
            f"开仓价: {entry_price}\n"
            f"平仓价: {exit_price}\n"
            f"盈亏: {pnl:+.2f} USD ({pnl_percent:+.2f}%)"
        )
        self.notify(NotificationEvent.TRADE_CLOSED, title, message)

    def notify_stop_loss(
        self,
        symbol: str,
        side: str,
        price: float,
        loss: float,
        loss_percent: float
    ):
        """
        发送止损通知

        Args:
            symbol: 交易对
            side: 方向
            price: 止损价格
            loss: 亏损金额
            loss_percent: 亏损百分比
        """
        side_text = "做多" if side.lower() == "long" else "做空"
        title = f"⚠️ 止损触发: {symbol}"
        message = (
            f"交易对: {symbol}\n"
            f"方向: {side_text}\n"
            f"止损价: {price}\n"
            f"亏损: -{loss:.2f} USD ({loss_percent:.2f}%)"
        )
        self.notify(NotificationEvent.STOP_LOSS, title, message)

    def notify_take_profit(
        self,
        symbol: str,
        side: str,
        price: float,
        profit: float,
        profit_percent: float
    ):
        """
        发送止盈通知

        Args:
            symbol: 交易对
            side: 方向
            price: 止盈价格
            profit: 盈利金额
            profit_percent: 盈利百分比
        """
        side_text = "做多" if side.lower() == "long" else "做空"
        title = f"🎉 止盈触发: {symbol}"
        message = (
            f"交易对: {symbol}\n"
            f"方向: {side_text}\n"
            f"止盈价: {price}\n"
            f"盈利: +{profit:.2f} USD (+{profit_percent:.2f}%)"
        )
        self.notify(NotificationEvent.TAKE_PROFIT, title, message)

    def notify_spot_investment(
        self,
        symbol: str,
        quantity: float,
        price: float,
        amount: float
    ):
        """
        发送现货定投通知

        Args:
            symbol: 交易对
            quantity: 数量
            price: 价格
            amount: 投资金额
        """
        title = f"💎 现货定投: {symbol}"
        message = (
            f"交易对: {symbol}\n"
            f"数量: {quantity}\n"
            f"价格: {price}\n"
            f"金额: {amount} USD"
        )
        self.notify(NotificationEvent.SPOT_INVESTMENT, title, message)

    def notify_error(
        self,
        title: str,
        error_message: str,
        context: Optional[str] = None
    ):
        """
        发送错误通知

        Args:
            title: 错误标题
            error_message: 错误信息
            context: 错误上下文（可选）
        """
        full_title = f"❌ 错误通知: {title}"
        message = f"错误信息: {error_message}"

        if context:
            message += f"\n上下文: {context}"

        self.notify(NotificationEvent.ERROR, full_title, message)

    def notify_circuit_breaker(
        self,
        reason: str,
        pause_minutes: int
    ):
        """
        发送熔断通知

        Args:
            reason: 熔断原因
            pause_minutes: 暂停时间（分钟）
        """
        title = "🚨 熔断机制触发"
        message = (
            f"原因: {reason}\n"
            f"暂停时间: {pause_minutes} 分钟\n"
            f"交易已暂停，请注意风险"
        )
        self.notify(NotificationEvent.CIRCUIT_BREAKER, title, message)


def get_notifier(config: Dict[str, Any]) -> Notifier:
    """
    获取通知管理器实例（工厂函数）

    Args:
        config: 通知配置字典

    Returns:
        Notifier 实例
    """
    return Notifier(config)
