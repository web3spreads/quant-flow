# 🤖 Quant Flow - AI 永续合约交易机器人

> 基于 LangChain 1.0 的智能交易系统

## ✨ 核心特性

- 🧠 **AI 智能决策** - Qwen / Deepseek 等 LLM 支持
- 🤖 **多 Agent 架构** - 独立上下文 + 智能汇总 + 现货定投
- 🗜️ **上下文压缩** - Token 减少 75%,成本降低 98%
- 📊 **全面分析** - RSI、MACD、布林带、多周期趋势
- 🛡️ **风险管理** - 自动止盈止损、仓位控制、熔断机制
- 💱 **Hyperliquid** - 去中心化永续合约 DEX

## 🚀 快速开始

### 方法一：Docker 部署（推荐 ⭐）

```bash
# 1. 配置环境
cp .env.example .env
cp config.yaml.example config.yaml
# 编辑 .env 填入 API 密钥和私钥

# 2. 启动容器
docker-compose up -d

# 3. 查看日志
docker-compose logs -f
```

**Docker 部署优势：**
- ✅ 环境一致性，无需手动配置依赖
- ✅ 自动重启，故障恢复
- ✅ 资源限制，防止过度消耗
- ✅ 日志管理，自动轮转

**重要：设置日志目录权限**
```bash
# 首次部署需要设置权限，避免权限错误
mkdir -p logs/decisions logs/trades
chmod -R 777 logs/
```

### 方法二：传统部署

```bash
# 1. 安装依赖
pip install -e .

# 2. 配置
cp .env.example .env
cp config.yaml.example config.yaml
# 编辑 .env 和 config.yaml

# 3. 运行
python main.py
```

## ⚙️ 配置说明

### 1. API 密钥配置 (.env)

编辑 `.env` 文件配置 API 密钥（**敏感信息，不要提交到版本控制**）：

```bash
# OpenAI 兼容 API 配置
OPENAI_API_BASE=https://api.deepseek.com/v1
OPENAI_API_KEY=your_api_key_here
OPENAI_MODEL=deepseek-chat

# Hyperliquid 钱包配置
HYPERLIQUID_PRIVATE_KEY=0xYourPrivateKeyHere
HYPERLIQUID_ACCOUNT_ADDRESS=              # API钱包模式才需要
HYPERLIQUID_TESTNET=true                  # true=测试网，false=主网

# 日志级别
LOG_LEVEL=INFO
```

**两种钱包模式：**

**模式1: 单钱包模式**（推荐用于测试）
- `HYPERLIQUID_PRIVATE_KEY` = 钱包私钥
- `HYPERLIQUID_ACCOUNT_ADDRESS` = 留空
- 直接使用该钱包交易，无需授权

**模式2: API 钱包代理模式**（推荐用于生产）
- `HYPERLIQUID_PRIVATE_KEY` = API 钱包私钥（用于签名）
- `HYPERLIQUID_ACCOUNT_ADDRESS` = 主钱包地址（有余额）
- ⚠️ 需在主钱包网页端授权 API 钱包地址

### 2. 交易参数配置 (config.yaml)

编辑 `config.yaml` 配置交易策略：

```yaml
trading:
  symbols: [BTC, ETH]           # 交易币种
  max_trade_amount: 100         # 单笔交易金额上限（USD）
  max_leverage: 10              # 最大杠杆倍数
  take_profit_ratio: 0.05       # 止盈比例（5%）
  stop_loss_ratio: 0.02         # 止损比例（2%）
  max_positions: 2              # 最大持仓数量

scheduler:
  interval_minutes: 3           # 决策间隔（分钟）
  run_immediately: true         # 启动时立即执行

# 更多配置见 config.yaml.example
```

### 3. 获取测试资金

访问 [Hyperliquid 测试网水龙头](https://app.hyperliquid-testnet.xyz/faucet) 领取测试 USDC。

## 🏗️ 多 Agent 架构

采用多 Agent 模式实现智能交易：

```
单币Agent (BTC/ETH/...) → 汇总Agent (历史压缩) → 现货Agent (严格评估)
     ↓                         ↓                      ↓
  独立决策                   分层汇总               定投决策
```

**优势:**
- ⚡ 每个交易对独立上下文，互不干扰
- 💰 上下文压缩技术，Token 减少 75%
- 📦 专业分工，决策更精准
- 🗜️ 智能汇总，成本降低 98%

## 📂 项目结构

```
quant-flow/
├── main.py                       # 启动入口
├── src/
│   ├── agent/                    # AI Agent 模块
│   │   ├── single_symbol_agent.py   # 单币 Agent
│   │   ├── summary_agent_v2.py      # 汇总 Agent V2
│   │   └── spot_agent.py            # 现货定投 Agent
│   ├── trading/                  # 交易模块
│   ├── data/                     # 数据处理
│   └── config.py                 # 配置管理
├── config.yaml                   # 交易配置
├── .env                          # API 密钥（不提交）
├── docker-compose.yml            # Docker 编排
└── prompts/                      # AI Prompt 模板
```

## 🐳 Docker 管理命令

```bash
# 启动
docker-compose up -d

# 停止
docker-compose down

# 重启
docker-compose restart

# 查看日志
docker-compose logs -f

# 查看状态
docker-compose ps

# 更新代码后重新构建
git pull
docker-compose build
docker-compose up -d
```

## 🛠️ 常见问题

### ❌ 权限错误（Docker）

**问题：** `PermissionError: [Errno 13] Permission denied: '/app/logs/...'`

**解决：**
```bash
docker-compose down
chmod -R 777 logs/
docker-compose up -d
```

### ❌ OI 上限错误

**问题：** `Cannot increase position when open interest is at cap`

**解决：** 该资产达到开放利益上限，切换到其他交易对。

### ❌ 杠杆错误

**问题：** `Leverage exceeds maximum allowed`

**解决：** 检查资产杠杆限制（如 ETH 最大 25x），降低 `max_leverage` 配置。

### ❌ 余额不足

**问题：** `Insufficient balance`

**解决：**
- 测试网：访问 [水龙头](https://app.hyperliquid-testnet.xyz/faucet) 获取资金
- 主网：充值账户

### ❌ API 钱包无法交易

**问题：** 能查余额但不能下单

**解决：** 在主钱包网页端授权 API 钱包地址，或切换到单钱包模式。

## ⚠️ 风险提示

1. **测试先行** - 先在测试网验证策略至少 1-2 天
2. **小额起步** - 主网从小金额开始（$50-100）
3. **理解风险** - 杠杆交易可能导致爆仓
4. **保管密钥** - 私钥泄露等同于资产丢失
5. **定期检查** - 监控运行状态和交易结果

## 🎯 高级功能

### 自定义 Prompt 策略

项目支持多套 Prompt 策略（default、conservative、aggressive）：

```yaml
# config.yaml
prompt:
  set: conservative  # 切换到保守策略
  config_file: prompts/prompts.yaml
```

创建自定义策略：
```bash
mkdir -p prompts/my_strategy
cp prompts/default/*.md prompts/my_strategy/
# 编辑 prompts/my_strategy/ 下的文件
# 在 prompts/prompts.yaml 中注册新策略
```

### 通知配置

支持钉钉、飞书、邮件通知：

```yaml
# config.yaml
notifications:
  enabled: true
  channels:
    - type: dingtalk
      enabled: true
      api_key: "your_webhook_here"
```

## 📊 生产部署建议

1. **切换到主网：** 编辑 `.env` 设置 `HYPERLIQUID_TESTNET=false`
2. **降低杠杆：** 建议 2-5x，避免高风险
3. **启用通知：** 配置钉钉/飞书接收交易通知
4. **监控资源：** `docker stats quant-flow-bot`
5. **定期备份：** 备份 `.env`、`config.yaml`、`logs/` 目录

## 🔗 相关链接

- [Hyperliquid 官网](https://hyperliquid.xyz/)
- [测试网水龙头](https://app.hyperliquid-testnet.xyz/faucet)
- [LangChain 文档](https://python.langchain.com/)

---

**免责声明**: 感谢关注，但如果你要在正式环境运行仍需三思，DYOR！！
