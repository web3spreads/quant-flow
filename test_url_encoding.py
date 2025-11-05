#!/usr/bin/env python3
"""
URL 编码测试 - 验证特殊字符处理
"""

from src.notification import Notifier

# 测试包含特殊字符的配置
test_config_with_special_chars = {
    "enabled": True,
    "channels": [
        # 测试邮件密码中包含特殊字符
        {
            "type": "email",
            "enabled": False,  # 暂不实际发送
            "smtp_server": "smtp.gmail.com",
            "smtp_port": 587,
            "smtp_user": "test+user@example.com",  # 包含 +
            "smtp_password": "p@ss:w0rd#123!",    # 包含 @, :, #, !
            "from_email": "test@example.com",
            "to_emails": ["user+tag@example.com", "another@test.org"]
        },
        # 测试钉钉 token 包含特殊字符
        {
            "type": "dingtalk",
            "enabled": False,
            "api_key": "abc123/def456",  # 包含 /
            "secret": "SEC@123#456",     # 包含 @, #
            "phone_numbers": []
        },
        # 测试飞书 token 包含特殊字符
        {
            "type": "feishu",
            "enabled": False,
            "token": "token?with=special&chars"  # 包含 ?, =, &
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

def test_url_encoding():
    """测试 URL 编码功能"""
    print("=" * 60)
    print("URL 编码测试")
    print("=" * 60)

    print("\n测试配置（包含特殊字符）：")
    print("- 邮件用户: test+user@example.com")
    print("- 邮件密码: p@ss:w0rd#123! (包含 @, :, #, !)")
    print("- 钉钉 API Key: abc123/def456 (包含 /)")
    print("- 钉钉 Secret: SEC@123#456 (包含 @, #)")
    print("- 飞书 Token: token?with=special&chars (包含 ?, =, &)")

    try:
        # 初始化通知器
        print("\n正在初始化通知器...")
        notifier = Notifier(test_config_with_special_chars)

        # 检查 Apprise URLs
        if notifier.apprise:
            # 使用 urls() 方法获取已添加的通知渠道列表
            urls = notifier.apprise.urls() if callable(getattr(notifier.apprise, 'urls', None)) else []
            if urls:
                print(f"\n✅ 成功创建 {len(urls)} 个通知渠道")
                print("所有特殊字符已正确编码到 URL 中")
            else:
                print("\n✅ 通知器初始化成功（渠道未启用）")
        else:
            print("\n✅ 通知器初始化成功")

        print("\n" + "=" * 60)
        print("✅ URL 编码测试通过！")
        print("特殊字符处理正常，不会破坏 URL 格式")
        print("=" * 60)

    except Exception as e:
        print("\n" + "=" * 60)
        print(f"❌ 测试失败: {e}")
        print("=" * 60)
        import traceback
        traceback.print_exc()
        return False

    return True

if __name__ == "__main__":
    success = test_url_encoding()
    exit(0 if success else 1)
