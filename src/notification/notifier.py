"""
通知管理器 - 使用 Apprise 库实现多渠道通知
支持钉钉、飞书、邮件等多种通知方式
"""

import logging
from enum import StrEnum
from typing import Any
from urllib.parse import quote

from apprise import Apprise


class NotificationEvent(StrEnum):
    """通知事件类型"""

    SYSTEM_STARTUP = "system_startup"  # 系统启动
    TRADE_OPENED = "trade_opened"  # 开仓
    TRADE_CLOSED = "trade_closed"  # 平仓
    STOP_LOSS = "stop_loss"  # 止损
    TAKE_PROFIT = "take_profit"  # 止盈
    SPOT_INVESTMENT = "spot_investment"  # 现货定投
    ERROR = "error"  # 错误
    CIRCUIT_BREAKER = "circuit_breaker"  # 熔断
    SYSTEM_SHUTDOWN = "system_shutdown"  # 系统关闭
    EXTERNAL_INFO_SUMMARY = "external_info_summary"  # 外部信息汇总完成
    REVIEW_LESSON_LEARNED = "review_lesson_learned"  # 复盘获得新经验


class Notifier:
    """
    通知管理器 - 基于 Apprise 库的统一通知接口

    特性：
    - 支持多种通知渠道（钉钉、飞书、邮件等）
    - 灵活的事件过滤配置
    - 优雅的错误处理
    - 作为配置项，用户配置则启用
    """

    def __init__(self, config: dict[str, Any], is_testnet: bool = True):
        """
        初始化通知管理器

        Args:
            config: 通知配置字典
            is_testnet: 是否为测试网（用于构建explorer URL）
        """
        self.logger = logging.getLogger(__name__)
        self.config = config
        self.enabled = config.get("enabled", False)
        self.apprise = Apprise()
        self.is_testnet = is_testnet

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
                elif channel_type == "lark":
                    self._add_lark_channel(channel)
                elif channel_type == "email":
                    self._add_email_channel(channel)
                else:
                    self.logger.warning(f"⚠️ 不支持的通知渠道类型: {channel_type}")

            except Exception as e:
                self.logger.error(f"❌ 初始化通知渠道失败: {e}", exc_info=True)

    def _add_dingtalk_channel(self, channel: dict[str, Any]) -> None:
        """添加钉钉通知渠道"""
        api_key = channel.get("api_key")
        secret = channel.get("secret", "")
        phone_numbers = channel.get("phone_numbers", [])

        if not api_key:
            self.logger.error("❌ 钉钉渠道缺少 api_key 配置")
            return

        try:
            # 构建钉钉 URL - 对参数进行 URL 编码
            # 格式: dingtalk://{secret}@{api_key}/{phone1}/{phone2}
            # API key 可能包含特殊字符，为安全起见应完全编码
            api_key_encoded = quote(api_key, safe="")
            if secret:
                # Secret 可能包含特殊字符，需要完全编码
                secret_encoded = quote(secret, safe="")
                url = f"dingtalk://{secret_encoded}@{api_key_encoded}"
            else:
                url = f"dingtalk://{api_key_encoded}"

            # 添加电话号码（电话号码通常不含特殊字符，但为安全起见也编码）
            if phone_numbers:
                encoded_phones = [quote(str(phone), safe="") for phone in phone_numbers]
                url += "/" + "/".join(encoded_phones)

            # 验证是否成功添加
            if not self.apprise.add(url):
                self.logger.error("❌ 钉钉通知渠道添加失败")
                return

            self.logger.info("✅ 钉钉通知渠道已添加")

        except Exception:
            # 不记录敏感信息（token/secret）到日志
            self.logger.error("❌ 钉钉通知渠道配置错误")

    def _add_feishu_channel(self, channel: dict[str, Any]) -> None:
        """添加飞书通知渠道"""
        token = channel.get("token")

        if not token:
            self.logger.error("❌ 飞书渠道缺少 token 配置")
            return

        try:
            # 构建飞书 URL - 对 token 进行 URL 编码
            # 格式: feishu://{token}
            # Token 通常包含字母数字、连字符和下划线，保留这些字符以提高可读性
            token_encoded = quote(token, safe="")
            url = f"feishu://{token_encoded}"

            # 验证是否成功添加
            if not self.apprise.add(url):
                self.logger.error("❌ 飞书通知渠道添加失败")
                return

            self.logger.info("✅ 飞书通知渠道已添加")

        except Exception:
            # 不记录敏感信息（token）到日志
            self.logger.error("❌ 飞书通知渠道配置错误")

    def _add_lark_channel(self, channel: dict[str, Any]) -> None:
        """添加lark通知渠道"""
        token = channel.get("token")

        if not token:
            self.logger.error("❌ lark渠道缺少 token 配置")
            return

        try:
            # 构建lark URL - 对 token 进行 URL 编码
            # 格式: lark://{token}
            # Token 通常包含字母数字、连字符和下划线，保留这些字符以提高可读性
            token_encoded = quote(token, safe="")
            url = f"lark://{token_encoded}"

            # 验证是否成功添加
            if not self.apprise.add(url):
                self.logger.error("❌ lark通知渠道添加失败")
                return

            self.logger.info("✅ lark通知渠道已添加")

        except Exception:
            # 不记录敏感信息（token）到日志
            self.logger.error("❌ lark通知渠道配置错误")

    def _add_email_channel(self, channel: dict[str, Any]) -> None:
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

        try:
            # 构建邮件 URL - 对所有参数进行 URL 编码以处理特殊字符
            # 格式: mailtos://{user}:{password}@{server}:{port}?from={from}&to={to1},{to2}
            # URL 编码可防止密码或邮箱中的特殊字符（@, :, /, ?, # 等）破坏 URL 格式
            smtp_user_encoded = quote(smtp_user, safe="")
            smtp_password_encoded = quote(smtp_password, safe="")
            from_email_encoded = quote(from_email, safe="")
            to_emails_str = ",".join(quote(email, safe="") for email in to_emails)

            url = (
                f"mailtos://{smtp_user_encoded}:{smtp_password_encoded}@{smtp_server}:{smtp_port}"
                f"?from={from_email_encoded}&to={to_emails_str}"
            )

            # 验证是否成功添加
            if not self.apprise.add(url):
                self.logger.error("❌ 邮件通知渠道添加失败")
                return

            self.logger.info("✅ 邮件通知渠道已添加")

        except Exception:
            # 不记录敏感信息（密码等）到日志
            self.logger.error("❌ 邮件通知渠道配置错误")

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

    def notify(self, event: NotificationEvent, title: str, message: str, **kwargs) -> bool:
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
            result = self.apprise.notify(title=title, body=message)

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
        leverage: int = 1,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        position_value: float | None = None,
        margin: float | None = None,
        reason: str | None = None,
        order_hash: str | None = None,
    ):
        """
        发送开仓通知

        Args:
            symbol: 交易对
            side: 方向（long/short）
            quantity: 数量
            price: 价格
            leverage: 杠杆倍数
            stop_loss: 止损价格
            take_profit: 止盈价格
            position_value: 持仓价值
            margin: 保证金
            reason: 开仓理由
            order_hash: 订单哈希
        """
        side_text = "做多 📈" if side.lower() == "long" else "做空 📉"
        title = f"🔔 开仓通知: {symbol} {side_text}"

        # 计算持仓价值和保证金
        if position_value is None:
            position_value = quantity * price
        if margin is None and leverage > 0:
            margin = position_value / leverage

        # 构建消息
        lines = [
            f"【交易对】{symbol}",
            f"【方向】{side_text}",
            f"【开仓价】${price:,.4f}" if price < 1 else f"【开仓价】${price:,.2f}",
            f"【数量】{quantity:,.4f}",
            f"【杠杆】{leverage}x",
            "",  # 空行
            f"【持仓价值】${position_value:,.2f}",
        ]

        if margin:
            lines.append(f"【保证金】${margin:,.2f}")

        # 添加止盈止损信息
        if stop_loss or take_profit:
            lines.append("")  # 空行

        if stop_loss:
            sl_diff_pct = abs((stop_loss - price) / price * 100)
            lines.append(
                f"【止损价】${stop_loss:,.4f} ({sl_diff_pct:.2f}%)"
                if stop_loss < 1
                else f"【止损价】${stop_loss:,.2f} (-{sl_diff_pct:.2f}%)"
            )

        if take_profit:
            tp_diff_pct = abs((take_profit - price) / price * 100)
            lines.append(
                f"【止盈价】${take_profit:,.4f} (+{tp_diff_pct:.2f}%)"
                if take_profit < 1
                else f"【止盈价】${take_profit:,.2f} (+{tp_diff_pct:.2f}%)"
            )

        # 添加风险收益比
        if stop_loss and take_profit:
            risk = abs(price - stop_loss)
            reward = abs(take_profit - price)
            rr_ratio = reward / risk if risk > 0 else 0
            lines.append(f"【风险收益比】1:{rr_ratio:.2f}")

        # 添加开仓理由
        if reason:
            lines.append("")  # 空行
            lines.append(f"【开仓理由】{reason}")

        # 添加订单哈希和浏览器链接
        if order_hash:
            lines.append("")  # 空行
            if len(order_hash) >= 18:
                lines.append(f"【交易哈希】{order_hash[:10]}...{order_hash[-8:]}")
            else:
                lines.append(f"【交易哈希】{order_hash}")
            # Hyperliquid 浏览器链接 - 根据testnet/mainnet使用不同URL
            if self.is_testnet:
                explorer_url = f"https://app.hyperliquid-testnet.xyz/explorer/tx/{order_hash}"
            else:
                explorer_url = f"https://app.hyperliquid.xyz/explorer/tx/{order_hash}"
            lines.append(f"【查看详情】{explorer_url}")

        message = "\n".join(lines)
        self.notify(NotificationEvent.TRADE_OPENED, title, message)

    def notify_trade_closed(
        self,
        symbol: str,
        side: str,
        quantity: float,
        entry_price: float,
        exit_price: float,
        pnl: float,
        pnl_percent: float,
        leverage: int | None = None,
        holding_time: str | None = None,
        close_reason: str | None = None,
        order_hash: str | None = None,
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
            leverage: 杠杆倍数
            holding_time: 持仓时间
            close_reason: 平仓原因
            order_hash: 交易哈希
        """
        side_text = "做多" if side.lower() == "long" else "做空"

        # 根据盈亏选择表情和颜色
        if pnl > 0:
            pnl_emoji = "🎉"
            result_text = "盈利"
        elif pnl < 0:
            pnl_emoji = "😢"
            result_text = "亏损"
        else:
            pnl_emoji = "➖"
            result_text = "持平"

        title = f"{pnl_emoji} 平仓通知: {symbol} {result_text}"

        # 构建消息
        lines = [
            f"【交易对】{symbol}",
            f"【方向】{side_text}",
            f"【数量】{quantity:,.4f}",
        ]

        if leverage:
            lines.append(f"【杠杆】{leverage}x")

        lines.extend(
            [
                "",  # 空行
                f"【开仓价】${entry_price:,.4f}"
                if entry_price < 1
                else f"【开仓价】${entry_price:,.2f}",
                f"【平仓价】${exit_price:,.4f}"
                if exit_price < 1
                else f"【平仓价】${exit_price:,.2f}",
                f"【价格变动】{pnl_percent:+.2f}%",
                "",  # 空行
                f"【盈亏金额】${pnl:+,.2f} USD",
                f"【收益率】{pnl_percent:+.2f}%",
            ]
        )

        # 添加持仓时间
        if holding_time:
            lines.append(f"【持仓时长】{holding_time}")

        # 添加平仓原因
        if close_reason:
            lines.append("")  # 空行
            lines.append(f"【平仓原因】{close_reason}")

        # 添加订单哈希和浏览器链接
        if order_hash:
            lines.append("")  # 空行
            if len(order_hash) >= 18:
                lines.append(f"【交易哈希】{order_hash[:10]}...{order_hash[-8:]}")
            else:
                lines.append(f"【交易哈希】{order_hash}")
            # Hyperliquid 浏览器链接
            if self.is_testnet:
                explorer_url = f"https://app.hyperliquid-testnet.xyz/explorer/tx/{order_hash}"
            else:
                explorer_url = f"https://app.hyperliquid.xyz/explorer/tx/{order_hash}"
            lines.append(f"【查看详情】{explorer_url}")

        # 添加总结
        if pnl > 0:
            lines.append("")  # 空行
            lines.append("✨ 恭喜盈利出场！")
        elif pnl < 0:
            lines.append("")  # 空行
            lines.append("⚠️ 及时止损，保护资金")

        message = "\n".join(lines)
        self.notify(NotificationEvent.TRADE_CLOSED, title, message)

    def notify_stop_loss(
        self, symbol: str, side: str, price: float, loss: float, loss_percent: float
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
        self, symbol: str, side: str, price: float, profit: float, profit_percent: float
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
        amount: float,
        order_hash: str | None = None,
    ):
        """
        发送现货定投通知

        Args:
            symbol: 交易对
            quantity: 数量
            price: 价格
            amount: 投资金额
            order_hash: 订单哈希
        """
        title = f"💎 现货定投: {symbol}"

        lines = [
            f"【交易对】{symbol}",
            f"【数量】{quantity:,.4f}",
            f"【价格】${price:,.4f}" if price < 1 else f"【价格】${price:,.2f}",
            f"【金额】${amount:,.2f}",
        ]

        # 添加订单哈希和浏览器链接
        if order_hash:
            lines.append("")  # 空行
            if len(order_hash) >= 18:
                lines.append(f"【交易哈希】{order_hash[:10]}...{order_hash[-8:]}")
            else:
                lines.append(f"【交易哈希】{order_hash}")
            # Hyperliquid 浏览器链接 - 根据testnet/mainnet使用不同URL
            if self.is_testnet:
                explorer_url = f"https://app.hyperliquid-testnet.xyz/explorer/tx/{order_hash}"
            else:
                explorer_url = f"https://app.hyperliquid.xyz/explorer/tx/{order_hash}"
            lines.append(f"【查看详情】{explorer_url}")

        message = "\n".join(lines)
        self.notify(NotificationEvent.SPOT_INVESTMENT, title, message)

    def notify_error(self, title: str, error_message: str, context: str | None = None):
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

    def notify_circuit_breaker(self, reason: str, pause_minutes: int):
        """
        发送熔断通知

        Args:
            reason: 熔断原因
            pause_minutes: 暂停时间（分钟）
        """
        title = "🚨 熔断机制触发"
        message = f"原因: {reason}\n暂停时间: {pause_minutes} 分钟\n交易已暂停，请注意风险"
        self.notify(NotificationEvent.CIRCUIT_BREAKER, title, message)

    def notify_system_startup(
        self,
        version: str | None = None,
        symbols: list | None = None,
        config_info: dict[str, Any] | None = None,
    ):
        """
        发送系统启动通知

        Args:
            version: 系统版本
            symbols: 监控的交易对列表
            config_info: 配置信息
        """
        from datetime import datetime

        title = "🚀 交易系统启动成功"

        lines = [
            f"【启动时间】{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        ]

        if version:
            lines.append(f"【系统版本】{version}")

        if symbols:
            lines.append("")
            lines.append(f"【监控币种】{', '.join(symbols)}")
            lines.append(f"【币种数量】{len(symbols)} 个")

        if config_info:
            lines.append("")
            if "trade_amount" in config_info:
                lines.append(f"【单笔金额】${config_info['trade_amount']:.2f}")
            if "max_positions" in config_info:
                lines.append(f"【最大持仓】{config_info['max_positions']} 个")
            if "leverage" in config_info:
                lines.append(f"【杠杆倍数】{config_info['leverage']}x")
            if "check_interval" in config_info:
                lines.append(f"【检查间隔】{config_info['check_interval']} 分钟")

        lines.append("")
        lines.append("✅ 系统已就绪，开始监控市场")

        message = "\n".join(lines)
        self.notify(NotificationEvent.SYSTEM_STARTUP, title, message)

    def notify_system_shutdown(
        self,
        reason: str | None = None,
        runtime: str | None = None,
        statistics: dict[str, Any] | None = None,
    ):
        """
        发送系统关闭通知

        Args:
            reason: 关闭原因
            runtime: 运行时长
            statistics: 运行统计
        """
        from datetime import datetime

        title = "⏹️ 交易系统已关闭"

        lines = [
            f"【关闭时间】{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        ]

        if reason:
            lines.append(f"【关闭原因】{reason}")

        if runtime:
            lines.append(f"【运行时长】{runtime}")

        if statistics:
            lines.append("")
            lines.append("【运行统计】")
            if "total_trades" in statistics:
                lines.append(f"  总交易次数: {statistics['total_trades']}")
            if "profitable_trades" in statistics:
                lines.append(f"  盈利交易: {statistics['profitable_trades']}")
            if "total_pnl" in statistics:
                lines.append(f"  总盈亏: ${statistics['total_pnl']:+.2f}")

        lines.append("")
        lines.append("👋 系统已安全退出")

        message = "\n".join(lines)
        self.notify(NotificationEvent.SYSTEM_SHUTDOWN, title, message)

    def notify_review_lesson(
        self, symbol: str, lessons: list[dict[str, Any]], summary: str | None = None
    ):
        """
        发送复盘经验通知

        Args:
            symbol: 交易对
            lessons: 新获得的经验列表
            summary: 复盘总结
        """
        from datetime import datetime

        if not lessons:
            return

        title = f"🧠 {symbol} 复盘获得新经验"

        lines = [
            f"【时间】{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"【币种】{symbol}",
            f"【新经验数量】{len(lessons)} 条",
        ]

        if summary:
            lines.append("")
            lines.append(f"【复盘总结】{summary[:200]}")

        lines.append("")
        lines.append("【新获得的经验】")

        for i, lesson in enumerate(lessons[:3], 1):  # 最多显示3条
            rule = lesson.get("rule", "")
            action = lesson.get("action", "")
            confidence = lesson.get("confidence", 0)

            lines.append(f"\n{i}. {rule[:100]}")
            lines.append(f"   → 建议行动: {action[:50]}")
            lines.append(f"   → 置信度: {confidence:.1%}")

        if len(lessons) > 3:
            lines.append(f"\n... 还有 {len(lessons) - 3} 条经验")

        message = "\n".join(lines)
        self.notify(NotificationEvent.REVIEW_LESSON_LEARNED, title, message)

    def notify_external_info_summary(self, summary: str = "", file_path: str = ""):
        """
        发送外部信息汇总完成通知

        Args:
            summary: 报告摘要内容
            file_path: 保存的文件路径
        """
        from datetime import datetime
        from pathlib import Path

        title = "📰 外部信息汇总完成"

        lines = [
            f"【完成时间】{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        ]

        if file_path:
            # 使用 pathlib 提取文件名（跨平台兼容）
            file_name = Path(file_path).name
            lines.append(f"【报告文件】{file_name}")

        if summary:
            lines.append("")
            lines.append("【市场信息摘要】")
            lines.append(summary)
        else:
            lines.append("")
            lines.append("✅ 市场信息已更新，可用于交易决策参考")

        message = "\n".join(lines)
        self.notify(NotificationEvent.EXTERNAL_INFO_SUMMARY, title, message)
