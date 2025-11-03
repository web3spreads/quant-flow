# README 更新说明

## 需要更新的部分

### 1. 核心特性（保持不变，可选添加）

在 "## ✨ 核心特性" 部分，可以添加：

```markdown
- 🔥 **真实合约做空**: 默认使用 U本位合约做空，支持杠杆（1-125倍）
- 💰 **高资金效率**: 合约交易资金效率是现货的 10-20倍
```

### 2. 交易 SDK 说明（需要完全替换）

**删除以下部分**（行 20-89）:
- "## 📦 交易 SDK 说明"
- "### 1. Bitget 官方 SDK（推荐）⭐"
- "### 2. CCXT（通用交易所接口）"
- "### 混合架构设计"

**替换为**:

```markdown
## 📦 交易平台

本项目专注于 **Bitget 交易所**，使用 **Bitget 官方 Python SDK**：

### Bitget 官方 SDK

**特性：**
- ✅ 官方维护，功能完整，第一时间支持新特性
- ✅ **完整止盈止损支持**：一次 API 调用创建带 TP/SL 的订单
- ✅ **真实合约做空**：支持 U本位合约，杠杆 1-125倍
- ✅ **现货交易**：支持现货买卖、计划单
- ✅ **模拟盘支持**：可在模拟环境安全测试策略

**为什么选择 Bitget？**
- 低手续费，流动性好
- API 稳定，响应快速
- 支持模拟盘测试
- 合约交易功能强大

### 运行模式

系统支持两种运行模式：

| 模式 | 环境变量 | 说明 | 适用场景 |
|------|----------|------|----------|
| **模拟盘** | `DEMO_TRADING=true` | 使用 Bitget 模拟盘 API | 策略测试、学习使用 |
| **实盘** | `DEMO_TRADING=false` | 使用真实资金交易 | 正式交易 |

**推荐流程：**
1. 在模拟盘充分测试策略
2. 确认策略稳定后切换到实盘
3. 实盘初期使用小额资金
4. 逐步增加交易规模
```

### 3. 系统架构（可选更新）

将原有的架构图替换为：

```markdown
## 🏗️ 系统架构

```
┌──────────────────────────────────────────────────────────────┐
│                     Quant Flow 架构                           │
├──────────────────────────────────────────────────────────────┤
│  Scheduler (每 3 分钟)                                       │
│      ↓                                                        │
│  Market Data Fetcher (CCXT)                                  │
│      ↓                                                        │
│  Technical Indicators (RSI, MACD, MA, BB, ...)               │
│      ↓                                                        │
│  LangChain Agent (GPT-4/DeepSeek + ReAct)                    │
│      ↓                                                        │
│  Order Manager                                                │
│    ├─ Spot Trading (Bitget Official SDK)                    │
│    └─ Contract Trading (Bitget Contract API)                │
│         • 开多/开空                                           │
│         • 杠杆 1-125x                                         │
│         • 止盈止损                                            │
│      ↓                                                        │
│  Logging & Monitoring                                         │
└──────────────────────────────────────────────────────────────┘
```
```

### 4. 环境变量配置（需要更新）

找到 "### 3. 配置环境变量" 部分，更新为：

```markdown
### 3. 配置环境变量

创建 `.env` 文件：

```bash
# Bitget API 配置（必需）
BITGET_API_KEY=your_api_key_here
BITGET_API_SECRET=your_api_secret_here
BITGET_PASSPHRASE=your_passphrase_here

# 运行模式（推荐先使用模拟盘）
DEMO_TRADING=true   # true=模拟盘, false=实盘

# OpenAI API 配置（使用 GPT-4）
OPENAI_API_BASE=https://api.openai.com/v1
OPENAI_API_KEY=your_openai_key_here
OPENAI_MODEL=gpt-4

# 或者使用其他 LLM 提供商（如 DeepSeek）
# OPENAI_API_BASE=https://api.deepseek.com/v1
# OPENAI_API_KEY=your_deepseek_key
# OPENAI_MODEL=deepseek-chat
```

**获取 Bitget API Key：**

1. 登录 [Bitget 官网](https://www.bitget.com/)
2. 进入 API 管理页面
3. 创建 API Key（实盘和模拟盘分别创建）
4. 记录 API Key、Secret 和 Passphrase
5. 设置 API 权限：开启"交易"权限，限制IP（推荐）

**重要提醒：**
- ⚠️ 模拟盘和实盘使用不同的 API Key
- ⚠️ 不要将 API Key 提交到代码库
- ⚠️ 建议限制 API 的 IP 白名单
- ⚠️ 定期更换 API Key
```
```

### 5. 配置文件（可选更新）

找到 `config.yaml` 的说明部分，移除 `use_official_sdk` 相关说明：

```yaml
trading:
  symbols: ["BTC/USDT", "ETH/USDT"]
  trade_amount: 100
  take_profit_ratio: 0.05
  stop_loss_ratio: 0.02
  max_positions: 2
  # 注意：use_official_sdk 已移除，系统默认使用 Bitget 官方 SDK
```

### 6. 添加新特性说明

在 README 末尾添加：

```markdown
## 🔥 最新更新（2025-11-02）

### 重大重构

项目进行了全面简化和优化：

1. **架构简化**
   - 移除 `test_mode` 概念
   - 只保留模拟盘和实盘两种模式
   - 配置更简单，代码减少 30-50%

2. **专注平台**
   - 移除 CCXT 交易逻辑
   - 专注 Bitget 官方 SDK
   - 更稳定、更高效

3. **真实做空**
   - 默认使用合约做空（10倍杠杆）
   - 资金效率提升 10-20倍
   - 支持止盈止损

4. **合约交易**
   - 支持 U本位合约
   - 杠杆 1-125倍
   - 真实做空收益

详见：[重构文档](docs/REFACTOR_2025-11-02.md)

### 升级指南

如果从旧版本升级：

1. 更新环境变量：
```bash
# 移除
TEST_MODE=false
USE_OFFICIAL_SDK=true

# 只保留
DEMO_TRADING=true
```

2. 拉取最新代码：
```bash
git pull origin main
```

3. 测试运行：
```bash
DEMO_TRADING=true uv run python main.py
```
```

## 完成后的效果

更新后的 README 将：
- ✅ 移除 CCXT vs 官方 SDK 的选择说明
- ✅ 强调专注于 Bitget 平台
- ✅ 突出合约做空功能
- ✅ 简化配置说明
- ✅ 添加最新更新说明

## 建议

由于 README 较长，建议：
1. 保留现有 README 作为 `README.old.md`
2. 根据本文档更新 README.md
3. 或者直接使用 Git 提交记录追踪变更
