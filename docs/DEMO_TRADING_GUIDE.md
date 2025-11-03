# Bitget 模拟盘使用指南

## 📋 概述

Quant Flow 现在支持三种运行模式：

| 模式 | TEST_MODE | DEMO_TRADING | 说明 |
|------|-----------|--------------|------|
| 本地测试 | `true` | `false` | 不发送网络请求，纯本地模拟 |
| Bitget 模拟盘 | `false` | `true` | 连接 Bitget 模拟盘，使用虚拟资金 |
| 实盘 ⚠️ | `false` | `false` | 连接 Bitget 实盘，使用真实资金 |

## 🎯 模式详解

### 1. 本地测试模式（推荐新手）

**配置（.env）：**
```env
TEST_MODE=true
DEMO_TRADING=false
BITGET_API_KEY=test_key  # 可以是任意值
```

**特点：**
- ✅ 不需要真实 API Key
- ✅ 不发送任何网络请求
- ✅ 完全安全，适合开发调试
- ✅ 可以快速验证策略逻辑

**适用场景：**
- 代码开发和调试
- 验证策略逻辑
- 学习使用系统

### 2. Bitget 模拟盘模式（推荐测试策略）

**配置（.env）：**
```env
TEST_MODE=false
DEMO_TRADING=true
BITGET_API_KEY=<你的模拟盘 API Key>
BITGET_API_SECRET=<你的模拟盘 API Secret>
BITGET_PASSPHRASE=<你的模拟盘 API Passphrase>
```

**特点：**
- ✅ 使用 Bitget 官方模拟盘环境
- ✅ 真实的市场数据
- ✅ 虚拟资金，无风险
- ✅ 完整测试交易流程
- ✅ 验证 API 连接和配置

**适用场景：**
- 策略在真实环境中的表现测试
- API Key 配置验证
- 正式上线前的最后验证

**如何获取模拟盘 API Key：**

1. 登录 [Bitget 网站](https://www.bitget.com/)
2. 完成 KYC 认证
3. 切换到模拟盘环境
4. 进入个人中心
5. 访问 API Key 管理
6. 创建模拟盘 API Key

### 3. 实盘模式 ⚠️（高风险）

**配置（.env）：**
```env
TEST_MODE=false
DEMO_TRADING=false
BITGET_API_KEY=<你的实盘 API Key>
BITGET_API_SECRET=<你的实盘 API Secret>
BITGET_PASSPHRASE=<你的实盘 API Passphrase>
```

**特点：**
- ⚠️ 使用真实资金
- ⚠️ 交易结果真实有效
- ⚠️ 可能导致资金损失

**适用场景：**
- 经过充分测试的策略
- 已在模拟盘验证成功
- 愿意承担风险

**重要提醒：**
1. 必须先在模拟盘充分测试
2. 初期建议使用小金额
3. 设置合理的止盈止损
4. 24小时监控系统运行
5. 理解并接受交易风险

## 📝 配置文件示例

### 完整的 .env 文件

```env
# ==========================================
# OpenAI API 配置
# ==========================================
OPENAI_API_BASE=https://api.openai.com/v1
OPENAI_API_KEY=your_openai_api_key_here
OPENAI_MODEL=gpt-4

# ==========================================
# Bitget API 配置
# ==========================================
# 根据模式使用不同的 API Key：
# - 本地测试：可以是任意值
# - 模拟盘：使用模拟盘 API Key
# - 实盘：使用实盘 API Key
BITGET_API_KEY=your_bitget_api_key_here
BITGET_API_SECRET=your_bitget_api_secret_here
BITGET_PASSPHRASE=your_bitget_passphrase_here

# ==========================================
# 运行模式配置
# ==========================================
# 本地测试模式
TEST_MODE=true

# 模拟盘模式（仅官方 SDK 支持）
# 注意：demo_trading=true 需要配合 test_mode=false 使用
DEMO_TRADING=false

# ==========================================
# 其他配置
# ==========================================
LOG_LEVEL=INFO
```

### config.yaml 示例

```yaml
trading:
  # 使用官方 SDK 以支持模拟盘
  use_official_sdk: true

  symbols:
    - BTC/USDT
    - ETH/USDT

  trade_amount: 100
  take_profit_ratio: 0.05
  stop_loss_ratio: 0.02
  max_positions: 2
```

## 🔄 切换模式

### 从本地测试切换到模拟盘

1. 在 Bitget 网站创建模拟盘 API Key
2. 更新 `.env` 文件：
   ```env
   TEST_MODE=false        # 改为 false
   DEMO_TRADING=true      # 改为 true
   BITGET_API_KEY=<模拟盘 API Key>
   ```
3. 重启程序

### 从模拟盘切换到实盘

**⚠️ 警告：请确保已充分测试！**

1. 确认策略在模拟盘运行良好
2. 在 Bitget 网站创建实盘 API Key
3. 更新 `.env` 文件：
   ```env
   TEST_MODE=false        # 保持 false
   DEMO_TRADING=false     # 改为 false
   BITGET_API_KEY=<实盘 API Key>
   ```
4. **建议先用小金额测试**
5. 重启程序并密切监控

## 🧪 测试流程

### 推荐的测试流程

```bash
# 1. 本地测试模式验证
TEST_MODE=true DEMO_TRADING=false python main.py

# 2. 模拟盘模式测试
TEST_MODE=false DEMO_TRADING=true python main.py

# 3. 小金额实盘测试
TEST_MODE=false DEMO_TRADING=false python main.py
# （config.yaml 中 trade_amount 设为小额，如 10 USDT）

# 4. 正式运行
# 确认无误后，调整 trade_amount 到正常值
```

### 运行测试脚本

```bash
# 测试模拟盘功能
uv run python test_demo_trading.py

# 测试 SDK 集成
uv run python test_sdk_integration.py
```

## 📊 模式对比

| 特性 | 本地测试 | Bitget 模拟盘 | 实盘 |
|------|---------|--------------|------|
| 网络请求 | ❌ 无 | ✅ 有 | ✅ 有 |
| API Key 要求 | ❌ 不需要 | ✅ 模拟盘 Key | ✅ 实盘 Key |
| 市场数据 | 📝 模拟 | ✅ 真实 | ✅ 真实 |
| 资金风险 | ✅ 无风险 | ✅ 无风险 | ⚠️ 高风险 |
| 订单执行 | 📝 本地模拟 | ✅ 模拟盘执行 | ✅ 实盘执行 |
| 适用场景 | 开发调试 | 策略测试 | 正式交易 |

## ⚙️ 技术实现

### 请求头区别

**模拟盘模式**（`demo_trading=true`）：
```python
headers = {
    'ACCESS-KEY': 'your_api_key',
    'ACCESS-SIGN': 'signature',
    'ACCESS-TIMESTAMP': 'timestamp',
    'ACCESS-PASSPHRASE': 'passphrase',
    'paptrading': '1',  # 模拟盘标识
    'locale': 'zh-CN'
}
```

**实盘模式**（`demo_trading=false`）：
```python
headers = {
    'ACCESS-KEY': 'your_api_key',
    'ACCESS-SIGN': 'signature',
    'ACCESS-TIMESTAMP': 'timestamp',
    'ACCESS-PASSPHRASE': 'passphrase',
    # 没有 paptrading 字段
    'locale': 'zh-CN'
}
```

### SDK 层面支持

```python
# BitgetOfficialClient 初始化
client = BitgetOfficialClient(
    api_key='your_key',
    api_secret='your_secret',
    passphrase='your_passphrase',
    test_mode=False,      # 是否本地测试
    demo_trading=True     # 是否使用模拟盘
)

# OrderApi 和 AccountApi 会自动传递 demo_trading 参数
# 所有请求都会包含正确的 header
```

## ❓ 常见问题

### Q: 模拟盘和本地测试有什么区别？

A:
- **本地测试**：完全不连接网络，所有数据都是模拟的，适合开发
- **模拟盘**：连接 Bitget 模拟盘环境，使用真实市场数据但虚拟资金，适合策略测试

### Q: 可以用实盘 API Key 连接模拟盘吗？

A: 不可以。模拟盘需要单独创建模拟盘 API Key。

### Q: CCXT 支持模拟盘吗？

A: 不支持。模拟盘功能仅在使用 Bitget 官方 SDK（`use_official_sdk=true`）时可用。

### Q: 如何确认当前运行在哪个模式？

A:
1. 查看启动时的日志输出
2. 查看配置摘要中的"运行模式"字段
3. 检查 `.env` 文件中的 `TEST_MODE` 和 `DEMO_TRADING` 配置

### Q: 模拟盘有资金限制吗？

A: Bitget 模拟盘提供虚拟资金供测试使用，具体额度请查看 Bitget 官方文档。

## 🔗 相关链接

- [Bitget 模拟盘官方文档](https://www.bitget.com/zh-CN/api-doc/common/demotrading/restapi)
- [Bitget API 文档](https://www.bitget.com/zh-CN/api-doc/)
- [项目 README](./README.md)
- [SDK 对比分析](./BITGET_SDK_COMPARISON.md)

## ⚠️ 免责声明

1. 即使在模拟盘模式下，也请谨慎操作，确保理解交易逻辑
2. 模拟盘测试成功不代表实盘一定盈利
3. 实盘交易有高风险，可能导致资金损失
4. 本项目仅供学习和研究使用，作者不对任何损失负责

---

**版本**: v0.2.1
**更新日期**: 2025-11-01
