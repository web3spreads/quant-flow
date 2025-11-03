# 快速开始指南 - Quant Flow 交易机器人

## 🚀 5 分钟快速测试

### 第一步：配置 API 密钥

编辑 `config.yaml`：

```yaml
# Bitget API 配置
bitget_api_key: "你的API密钥"
bitget_api_secret: "你的API密钥"
bitget_passphrase: "你的密码短语"

# 重要：使用模拟盘！
demo_trading: true
test_mode: false
```

### 第二步：运行测试脚本

```bash
uv run python test_trading_functions.py
```

### 第三步：选择测试功能

**推荐测试顺序**：

1. 先选择 `1` - 查询账户信息
   - 确认能正常连接 API
   - 查看账户余额

2. 再选择 `2` - 测试买入
   - 输入交易对（如 BTC/USDT）
   - 输入金额（如 20）
   - 确认买入

3. 然后选择 `3` - 测试卖出
   - 确认卖出刚才买入的仓位

4. 最后选择 `6` - 运行完整测试流程
   - 自动测试所有功能

## 📋 测试清单

完成以下测试后，即可开始使用：

- [ ] ✅ 账户信息查询成功
- [ ] ✅ 能看到余额信息
- [ ] ✅ 买入测试成功
- [ ] ✅ 卖出测试成功
- [ ] ✅ 做空测试成功（可选）
- [ ] ✅ 平空测试成功（可选）

## 🎯 常见问题

### Q1: API 密钥从哪里获取？

A:
1. 登录 Bitget 官网
2. 进入「API 管理」
3. 创建「模拟盘」API Key
4. 复制 Key、Secret、Passphrase 到 config.yaml

### Q2: 为什么余额是 0？

A: 模拟盘需要先在 Bitget 网站充值虚拟资金。

如果看到 `[模拟盘] 返回默认余额 10000.0 USDT`，说明：
- API 密钥配置可能不完整
- 但功能仍然正常，使用的是默认模拟余额

### Q3: 测试完成后如何运行主程序？

A:
```bash
uv run python main.py
```

主程序会：
- 定期获取市场数据
- AI 分析并做出交易决策
- 自动执行买入/卖出/做空操作

## ⚠️ 重要提醒

**在配置真实 API 密钥前**：

1. ✅ 先在模拟盘测试所有功能
2. ✅ 确认交易逻辑符合预期
3. ✅ 观察 AI 决策是否合理
4. ⚠️ 真实交易需谨慎！

## 🔗 更多文档

- 📖 [测试脚本详细说明](TEST_TRADING_FUNCTIONS_README.md)
- 📖 [余额检查功能](BALANCE_CHECK_FEATURE.md)
- 📖 [做空功能说明](SHORT_SELLING_FEATURE.md)
- 📖 [模拟盘修复说明](DEMO_BALANCE_FIX.md)

## 🎉 开始使用

```bash
# 1. 测试功能（推荐）
uv run python test_trading_functions.py

# 2. 运行主程序
uv run python main.py
```

祝交易顺利！🚀
