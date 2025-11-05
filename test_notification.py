#!/usr/bin/env python3
"""
通知系统测试脚本
用于验证通知配置是否正确
"""

from src.notification import Notifier, NotificationEvent

# 测试配置
test_config = {
    "enabled": True,
    "channels": [
        # 钉钉测试（需要替换为真实配置）
        {
            "type": "dingtalk",
            "enabled": False,  # 改为 True 并配置 api_key 后可测试
            "api_key": "your_dingtalk_api_key_here",
            "secret": "",
            "phone_numbers": []
        },
        # 飞书测试（需要替换为真实配置）
        {
            "type": "feishu",
            "enabled": False,  # 改为 True 并配置 token 后可测试
            "token": "your_feishu_token_here"
        },
        # 邮件测试（需要替换为真实配置）
        {
            "type": "email",
            "enabled": False,  # 改为 True 并配置邮箱后可测试
            "smtp_server": "smtp.gmail.com",
            "smtp_port": 587,
            "smtp_user": "your_email@gmail.com",
            "smtp_password": "your_app_password",
            "from_email": "your_email@gmail.com",
            "to_emails": ["recipient@example.com"]
        }
    ],
    "events": {
        "trade_opened": True,
        "trade_closed": True,
        "stop_loss": True,
        "take_profit": True,
        "spot_investment": True,
        "error": True,
        "circuit_breaker": True
    }
}

def test_notification_system():
    """测试通知系统"""
    print("=" * 60)
    print("通知系统测试")
    print("=" * 60)

    # 创建通知器
    print("\n1. 初始化通知器...")
    notifier = Notifier(test_config)

    if not test_config["enabled"]:
        print("⚠️  通知系统未启用（enabled=False）")
        print("提示：修改 test_config 中的配置以测试实际通知")
        return

    # 检查是否有已启用的渠道
    enabled_channels = [ch for ch in test_config["channels"] if ch.get("enabled")]
    if not enabled_channels:
        print("⚠️  没有启用的通知渠道")
        print("提示：在 test_config 中将至少一个渠道的 enabled 设为 True")
        return

    print(f"✅ 通知器初始化成功，已启用 {len(enabled_channels)} 个渠道")

    # 测试各种通知
    print("\n2. 测试开仓通知...")
    notifier.notify_trade_opened(
        symbol="BTC",
        side="long",
        quantity=0.001,
        price=50000.0,
        leverage=10
    )

    print("\n3. 测试平仓通知...")
    notifier.notify_trade_closed(
        symbol="BTC",
        side="long",
        quantity=0.001,
        entry_price=50000.0,
        exit_price=52000.0,
        pnl=200.0,
        pnl_percent=4.0
    )

    print("\n4. 测试止损通知...")
    notifier.notify_stop_loss(
        symbol="ETH",
        side="long",
        price=3000.0,
        loss=50.0,
        loss_percent=2.0
    )

    print("\n5. 测试止盈通知...")
    notifier.notify_take_profit(
        symbol="ETH",
        side="long",
        price=3200.0,
        profit=150.0,
        profit_percent=5.0
    )

    print("\n6. 测试现货定投通知...")
    notifier.notify_spot_investment(
        symbol="BTC",
        quantity=0.002,
        price=48000.0,
        amount=100.0
    )

    print("\n7. 测试错误通知...")
    notifier.notify_error(
        title="测试错误",
        error_message="这是一个测试错误消息",
        context="通知系统单元测试"
    )

    print("\n8. 测试熔断通知...")
    notifier.notify_circuit_breaker(
        reason="价格波动超过 10%",
        pause_minutes=30
    )

    print("\n" + "=" * 60)
    print("✅ 所有通知测试完成！")
    print("提示：请检查您配置的通知渠道是否收到了测试消息")
    print("=" * 60)

if __name__ == "__main__":
    try:
        test_notification_system()
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
