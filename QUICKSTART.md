# 🚀 快速开始指南 - Quant Flow

## 5分钟快速启动

### 步骤 1: 安装依赖 (1分钟)

```bash
cd quant-flow
pip install -e .
```

### 步骤 2: 配置环境变量 (2分钟)

```bash
# 复制模板
cp .env.example .env

# 编辑 .env
nano .env  # 或使用你喜欢的编辑器
```

**Hyperliquid 支持两种钱包模式：**

#### 模式1: 单钱包模式（推荐用于测试）

最小配置：

```env
# OpenAI API（必需）
OPENAI_API_KEY=sk-your-key-here

# Hyperliquid - 单钱包模式
HYPERLIQUID_PRIVATE_KEY=0xyour-private-key-here
HYPERLIQUID_TESTNET=true
# HYPERLIQUID_ACCOUNT_ADDRESS 留空
```

#### 模式2: API 钱包代理模式（推荐用于生产）

```env
# OpenAI API（必需）
OPENAI_API_KEY=sk-your-key-here

# Hyperliquid - API 钱包模式
HYPERLIQUID_PRIVATE_KEY=0xapi-wallet-private-key
HYPERLIQUID_ACCOUNT_ADDRESS=0xmain-wallet-address
HYPERLIQUID_TESTNET=true
```

**API 钱包模式的优势：**
- 🔒 主钱包私钥更安全（不需要暴露）
- 🤖 API 钱包专门用于程序交易
- 🎛️ 可以在网页端随时撤销 API 钱包权限

**如何设置 API 钱包：**
1. 创建一个新钱包作为 API 钱包（用于签名）
2. 在 Hyperliquid 网页端用主钱包登录
3. 进入 API 设置，授权 API 钱包地址
4. 配置 `.env`：
   - `HYPERLIQUID_PRIVATE_KEY` = API 钱包私钥
   - `HYPERLIQUID_ACCOUNT_ADDRESS` = 主钱包地址

> ⚠️ **安全提示**：私钥是你的资产凭证，切勿泄露！
>
> 💡 **配置检查**：运行 `python check_account.py` 检查配置是否正确

### 步骤 3: 配置交易参数 (1分钟)

```bash
# 复制模板
cp config.yaml.example config.yaml

# 可选：编辑参数（或使用默认值）
nano config.yaml
```

默认配置已经可以使用：
- 交易对: BTC, ETH
- 每次交易: $100
- 杠杆: 10x
- 止盈: 5% / 止损: 2%

### 步骤 4: 获取测试资金 (1分钟)

访问 Hyperliquid 测试网水龙头：
https://app.hyperliquid-testnet.xyz/faucet

连接你的钱包，领取测试 USDC。

### 步骤 5: 运行测试

```bash
python test_hyperliquid_migration.py
```

应该看到：

```
✅ 依赖安装: 通过
✅ 客户端连接: 通过
✅ 市场数据: 通过

🎉 所有测试通过！
```

### 步骤 6: 启动机器人 🎉

```bash
python main.py
```

## 下一步

### 切换到主网（谨慎！）

**⚠️ 只有在测试网充分测试后才切换到主网！**

1. 编辑 `.env`:
   ```env
   HYPERLIQUID_TESTNET=false
   ```

2. 从小金额开始！建议：
   - 初始金额: $50-100
   - 低杠杆: 2-5x
   - 观察1-2天再增加

### 调整策略

编辑 `config.yaml` 调整参数：

```yaml
trading:
  trade_amount: 50      # 每次投入金额
  default_leverage: 5   # 降低杠杆
  take_profit_ratio: 0.03  # 降低止盈目标（更容易达到）
  stop_loss_ratio: 0.01    # 收紧止损（更快止损）
```

### 监控运行

机器人会在控制台显示：
- 💰 账户余额
- 📊 市场数据和技术指标
- 🤖 AI 决策过程
- 📝 交易执行结果

日志保存在 `logs/` 目录。

## 常见问题

### Q: 私钥从哪里获取？

A: 使用 MetaMask 等钱包导出私钥，或创建新钱包：

```python
from eth_account import Account
account = Account.create()
print(f"地址: {account.address}")
print(f"私钥: {account.key.hex()}")
```

### Q: 如何获取 OpenAI API Key？

A: 访问 https://platform.openai.com/api-keys

或使用兼容的 API（如 DeepSeek）：

```env
OPENAI_API_BASE=https://api.deepseek.com/v1
OPENAI_API_KEY=your-deepseek-key
OPENAI_MODEL=deepseek-chat
```

### Q: 测试网和主网有什么区别？

A:
- **测试网**: 虚拟资金，用于测试，免费
- **主网**: 真实资金，实际交易，有风险

### Q: 杠杆如何选择？

A:
- **新手**: 2-3x（风险较低）
- **有经验**: 5-10x（中等风险）
- **专业**: 10-20x（高风险）
- **极限**: 20-50x（极高风险，不推荐）

记住：杠杆越高，爆仓风险越大！

### Q: 如何停止机器人？

A: 按 `Ctrl+C` 即可安全停止。

### Q: 出错了怎么办？

A: 使用诊断工具和查看故障排除指南：

**1. 运行诊断工具:**
```bash
# 检查账户和开放利益上限
python check_oi_caps.py

# 测试交易功能
python test_trading_functions.py

# 检查账户余额
python check_account.py
```

**2. 查看详细的故障排除指南:**
```bash
# 查看 TROUBLESHOOTING.md 文件
cat TROUBLESHOOTING.md
```

**3. 常见错误:**
- `Cannot increase position when open interest is at cap` → 资产达到开放利益上限，换其他资产
- `能查余额但不能下单` → API钱包未授权，切换到单钱包模式或授权API钱包
- `余额不足` → 访问测试网水龙头获取资金

**4. 其他检查:**
- `.env` 配置是否正确
- `config.yaml` 格式是否正确
- 网络连接是否正常
- 查看 `logs/` 目录的错误日志

## 安全建议

1. ✅ 先用测试网测试至少1-2天
2. ✅ 主网从小金额开始（$50-100）
3. ✅ 使用低杠杆（2-5x）
4. ✅ 设置合理的止损
5. ✅ 定期检查运行状态
6. ✅ 不要把所有资金投入
7. ✅ 理解你在做什么

## 支持

- 📚 查看 `README.md` 了解详细信息
- 🔗 访问 [Hyperliquid 文档](https://hyperliquid.gitbook.io/)

---

**免责声明**: 加密货币交易风险极高，杠杆交易风险更大。本软件仅供学习研究，使用风险自负。
