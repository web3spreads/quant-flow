# 通知系统使用指南

## 📢 功能概述

Quant Flow 已成功集成多渠道通知系统，基于 **Apprise** 开源库（14.8k+ GitHub stars）实现，支持钉钉、飞书、邮件等 100+ 种通知服务。

### ✨ 主要特性

- 🔔 **多渠道支持**：钉钉、飞书、邮件等主流通知方式
- 🎯 **事件驱动**：开仓、平仓、止损、止盈、定投、错误、熔断等关键事件
- ⚙️ **灵活配置**：作为可选配置项，启用/禁用随心所欲
- 🎨 **事件过滤**：可单独控制每种事件的通知开关
- 🚀 **零侵入**：不影响现有交易逻辑，优雅集成

---

## 🚀 快速开始

### 1. 更新依赖

```bash
pip install apprise>=1.9.0
# 或者
pip install -e .
```

### 2. 配置通知渠道

编辑 `config.yaml` 文件，添加通知配置：

```yaml
# 通知配置
notifications:
  # 总开关：设置为 true 启用通知
  enabled: true

  # 通知渠道配置
  channels:
    # 钉钉机器人
    - type: dingtalk
      enabled: true
      api_key: "your_dingtalk_api_key_here"
      secret: "your_dingtalk_secret_here"  # 可选
      phone_numbers:  # 可选，指定接收人
        - "13800138000"

    # 飞书机器人
    - type: feishu
      enabled: true
      token: "your_feishu_token_here"

    # 邮件通知
    - type: email
      enabled: true
      smtp_server: "smtp.gmail.com"
      smtp_port: 587
      smtp_user: "your_email@gmail.com"
      smtp_password: "your_app_password"
      from_email: "your_email@gmail.com"
      to_emails:
        - "recipient@example.com"

  # 事件通知开关
  events:
    trade_opened: true      # 开仓通知
    trade_closed: true      # 平仓通知
    stop_loss: true         # 止损通知
    take_profit: true       # 止盈通知
    spot_investment: true   # 现货定投通知
    error: true            # 错误通知
    circuit_breaker: true   # 熔断通知
```

### 3. 测试通知系统

运行测试脚本验证配置：

```bash
python test_notification.py
```

### 4. 启动机器人

```bash
python main.py
```

---

## 📋 详细配置说明

### 钉钉机器人配置

#### 获取配置信息

1. 登录钉钉管理后台
2. 进入 **应用开发** -> **机器人管理**
3. 创建自定义机器人
4. 获取 **Webhook URL**，从中提取 `access_token` 作为 `api_key`
5. 如果启用了加签验证，获取 `secret`

#### 配置示例

```yaml
- type: dingtalk
  enabled: true
  api_key: "abc123def456"  # Webhook URL 中的 access_token
  secret: "SECxxx"         # 加签密钥（可选）
  phone_numbers:           # @指定成员（可选）
    - "13800138000"
```

#### 通知格式示例

```
🔔 开仓通知: BTC
交易对: BTC
方向: 做多
数量: 0.001
价格: 50000.0
杠杆: 10x
```

---

### 飞书机器人配置

#### 获取配置信息

1. 登录飞书管理后台（https://open.feishu.cn/）
2. 进入 **应用** -> **机器人**
3. 创建自定义机器人
4. 获取 Webhook URL 中的 token

#### 配置示例

```yaml
- type: feishu
  enabled: true
  token: "your_token_here"  # Webhook URL 中的 token
```

---

### 邮件通知配置

#### Gmail 配置（推荐）

1. 登录 Gmail 账户
2. 前往 **账户设置** -> **安全性**
3. 启用 **两步验证**
4. 生成 **应用专用密码**
5. 使用应用密码作为 `smtp_password`

#### 配置示例

```yaml
- type: email
  enabled: true
  smtp_server: "smtp.gmail.com"
  smtp_port: 587
  smtp_user: "your_email@gmail.com"
  smtp_password: "your_app_password"  # 应用专用密码
  from_email: "your_email@gmail.com"
  to_emails:
    - "recipient1@example.com"
    - "recipient2@example.com"
```

#### 其他邮箱服务

| 服务商 | SMTP 服务器 | 端口 | 说明 |
|--------|-------------|------|------|
| Gmail | smtp.gmail.com | 587 | 需应用密码 |
| QQ 邮箱 | smtp.qq.com | 587 | 需授权码 |
| 163 邮箱 | smtp.163.com | 465 | 需授权码 |
| Outlook | smtp-mail.outlook.com | 587 | 需应用密码 |

---

## 🎯 支持的通知事件

### 1. 开仓通知 (`trade_opened`)

**触发时机**：执行买入开多或卖空开空操作成功后

**通知内容**：
- 交易对
- 方向（做多/做空）
- 数量
- 价格
- 杠杆倍数

**示例**：
```
🔔 开仓通知: BTC
交易对: BTC
方向: 做多
数量: 0.001
价格: 50000.0
杠杆: 10x
```

---

### 2. 平仓通知 (`trade_closed`)

**触发时机**：执行卖出平多或买入平空操作成功后

**通知内容**：
- 交易对
- 方向
- 数量
- 开仓价格
- 平仓价格
- 盈亏金额和百分比

**示例**：
```
💰 平仓通知: BTC
交易对: BTC
方向: 做多
数量: 0.001
开仓价: 50000.0
平仓价: 52000.0
盈亏: +200.00 USD (+4.00%)
```

---

### 3. 止损通知 (`stop_loss`)

**触发时机**：止损单被触发

**通知内容**：
- 交易对
- 方向
- 止损价格
- 亏损金额和百分比

**示例**：
```
⚠️ 止损触发: ETH
交易对: ETH
方向: 做多
止损价: 3000.0
亏损: -50.00 USD (2.00%)
```

---

### 4. 止盈通知 (`take_profit`)

**触发时机**：止盈单被触发

**通知内容**：
- 交易对
- 方向
- 止盈价格
- 盈利金额和百分比

**示例**：
```
🎉 止盈触发: ETH
交易对: ETH
方向: 做多
止盈价: 3200.0
盈利: +150.00 USD (+5.00%)
```

---

### 5. 现货定投通知 (`spot_investment`)

**触发时机**：执行现货定投操作成功后

**通知内容**：
- 交易对
- 数量
- 价格
- 投资金额

**示例**：
```
💎 现货定投: BTC
交易对: BTC
数量: 0.002
价格: 48000.0
金额: 100.0 USD
```

---

### 6. 错误通知 (`error`)

**触发时机**：交易周期执行异常或其他系统错误

**通知内容**：
- 错误标题
- 错误信息
- 错误上下文（可选）

**示例**：
```
❌ 错误通知: 交易周期异常
错误信息: Connection timeout
上下文: 交易决策循环执行时发生错误
```

---

### 7. 熔断通知 (`circuit_breaker`)

**触发时机**：风控熔断机制被触发

**通知内容**：
- 熔断原因
- 暂停时间

**示例**：
```
🚨 熔断机制触发
原因: 价格波动超过 10%
暂停时间: 30 分钟
交易已暂停，请注意风险
```

---

## 🔧 高级配置

### 禁用特定事件通知

如果只想接收某些类型的通知，可以单独控制每个事件：

```yaml
notifications:
  enabled: true
  channels: [...]
  events:
    trade_opened: true      # 接收开仓通知
    trade_closed: true      # 接收平仓通知
    stop_loss: false        # 不接收止损通知
    take_profit: false      # 不接收止盈通知
    spot_investment: true   # 接收定投通知
    error: true            # 接收错误通知
    circuit_breaker: true   # 接收熔断通知
```

### 多渠道同时启用

可以同时启用多个通知渠道，系统会并发发送：

```yaml
notifications:
  enabled: true
  channels:
    - type: dingtalk
      enabled: true
      # ... 钉钉配置

    - type: feishu
      enabled: true
      # ... 飞书配置

    - type: email
      enabled: true
      # ... 邮件配置
```

### 临时禁用通知

有两种方式临时禁用通知：

**方式 1：关闭总开关**
```yaml
notifications:
  enabled: false  # 关闭所有通知
```

**方式 2：禁用特定渠道**
```yaml
notifications:
  enabled: true
  channels:
    - type: dingtalk
      enabled: false  # 只禁用钉钉
```

---

## 🧪 测试与调试

### 运行测试脚本

项目提供了 `test_notification.py` 测试脚本，用于验证通知配置：

```bash
python test_notification.py
```

测试脚本会：
1. 初始化通知系统
2. 发送各种类型的测试通知
3. 验证配置是否正确

### 修改测试配置

编辑 `test_notification.py` 中的 `test_config` 字典：

```python
test_config = {
    "enabled": True,  # 启用测试
    "channels": [
        {
            "type": "dingtalk",
            "enabled": True,  # 改为 True 测试钉钉
            "api_key": "your_real_api_key",
            # ...
        }
    ],
    "events": { ... }
}
```

### 查看通知日志

通知系统会在日志中记录发送状态：

```
✅ 钉钉通知渠道已添加
📤 通知发送成功: 开仓通知 BTC
⚠️ 通知发送失败: 配置错误
```

---

## 🐛 常见问题

### Q1: 通知没有收到怎么办？

**排查步骤**：

1. **检查总开关**
   ```yaml
   notifications:
     enabled: true  # 确保为 true
   ```

2. **检查渠道开关**
   ```yaml
   channels:
     - type: dingtalk
       enabled: true  # 确保为 true
   ```

3. **检查事件开关**
   ```yaml
   events:
     trade_opened: true  # 确保对应事件为 true
   ```

4. **验证配置信息**
   - 钉钉：确认 `api_key` 和 `secret` 正确
   - 飞书：确认 `token` 正确
   - 邮件：确认 SMTP 服务器和密码正确

5. **查看日志**
   ```bash
   tail -f logs/quant_flow.log
   ```

---

### Q2: 钉钉通知返回 "sign not match" 错误

**原因**：加签验证失败

**解决方案**：
1. 确认钉钉机器人是否启用了加签验证
2. 如果启用，确保 `secret` 配置正确
3. 如果未启用，将 `secret` 留空或删除该配置项

```yaml
- type: dingtalk
  enabled: true
  api_key: "your_api_key"
  secret: ""  # 未启用加签则留空
```

---

### Q3: Gmail 邮件通知失败

**原因**：未使用应用专用密码

**解决方案**：
1. 登录 Gmail 账户
2. 前往 **账户设置** -> **安全性**
3. 启用 **两步验证**
4. 生成 **应用专用密码**
5. 使用应用密码替换普通密码

**不要使用普通密码！**

---

### Q4: 如何测试通知配置是否正确？

运行测试脚本：

```bash
python test_notification.py
```

或在 Python 中手动测试：

```python
from src.notification import Notifier

config = {
    "enabled": True,
    "channels": [
        {
            "type": "dingtalk",
            "enabled": True,
            "api_key": "your_api_key"
        }
    ],
    "events": {
        "trade_opened": True
    }
}

notifier = Notifier(config)
notifier.notify_trade_opened(
    symbol="BTC",
    side="long",
    quantity=0.001,
    price=50000.0,
    leverage=10
)
```

---

### Q5: 通知太频繁，如何减少通知数量？

**方案 1：禁用不重要的事件**

```yaml
events:
  trade_opened: true
  trade_closed: true
  stop_loss: true
  take_profit: true
  spot_investment: false  # 禁用定投通知
  error: true
  circuit_breaker: true
```

**方案 2：调整交易周期**

在 `config.yaml` 中增加决策间隔：

```yaml
scheduler:
  interval_minutes: 10  # 从 3 分钟改为 10 分钟
```

---

## 📚 Apprise 库介绍

本通知系统基于 **Apprise** 开源库实现：

- **GitHub**: https://github.com/caronc/apprise
- **Stars**: 14.8k+
- **支持服务**: 100+ 种通知服务
- **文档**: https://github.com/caronc/apprise/wiki

### 为什么选择 Apprise？

✅ **广泛采用**：14.8k+ GitHub stars，成熟稳定
✅ **开源免费**：MIT 许可证，完全开源
✅ **大用户基数**：被众多开源项目使用
✅ **简单易用**：统一的 API 接口
✅ **扩展性强**：支持 100+ 种通知服务

---

## 🎉 总结

通知系统现已完全集成到 Quant Flow 项目中，支持：

- ✅ 钉钉、飞书、邮件等多种通知方式
- ✅ 开仓、平仓、止损、止盈等关键事件通知
- ✅ 灵活的配置和事件过滤
- ✅ 作为可选配置项，不影响现有功能
- ✅ 基于成熟的开源库（Apprise），稳定可靠

如有问题或建议，欢迎反馈！

---

**最后更新**: 2025-11-05
**版本**: v1.0.0
