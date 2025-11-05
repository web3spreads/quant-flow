# 🤖 Quant Flow - AI 永续合约交易机器人

> 基于 LangChain 1.0 的智能交易系统 | 支持多 Agent 架构 + 上下文压缩

## ✨ 核心特性

- 🧠 **AI 智能决策** - GPT-4 / Deepseek 等 LLM 支持
- 🤖 **多 Agent 架构** - 独立上下文 + 智能汇总 + 现货定投
- 🗜️ **上下文压缩** - Token 减少 75%，成本降低 98%
- 📊 **全面分析** - RSI、MACD、布林带、多周期趋势
- 🛡️ **风险管理** - 自动止盈止损、仓位控制、熔断机制
- 💱 **Hyperliquid** - 去中心化永续合约 DEX

## 🚀 快速开始

```bash
# 1. 安装
pip install -e .

# 2. 配置
cp .env.example .env
cp config.yaml.example config.yaml
# 编辑 .env 填入 API 密钥

# 3. 运行
python main.py
```

[快速开始](./QUICKSTART.md) - 详细安装配置

## 🏗️ 多 Agent 架构

采用多 Agent 模式实现智能交易：
- 🎯 **单币 Agent** - 每个交易对独立分析决策
- 📝 **汇总 Agent** - 智能历史汇总和上下文压缩
- 💎 **现货 Agent** - 专注于现货定投双层审核

**架构流程:**
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

**常见问题:**
- ❌ OI 上限 → 切换交易对
- ❌ 杠杆错误 → 检查资产限制（ETH 最大 25x）
- ❌ 余额不足 → [测试网水龙头](https://app.hyperliquid-testnet.xyz/faucet)

## 📂 项目结构

```
quant-flow/
├── main.py                       # 启动入口
├── src/
│   ├── agent/                       # AI Agent 模块
│   │   ├── single_symbol_agent.py   # 单币 Agent
│   │   ├── summary_agent_v2.py      # 汇总 Agent V2
│   │   └── spot_agent.py            # 现货定投 Agent
│   ├── trading/                     # 交易模块
│   └── data/                        # 数据处理
├── config.yaml                      # 交易配置
└── .env                             # API 密钥
```

## ⚠️ 风险提示

1. **测试先行** - 先在测试网验证策略
2. **小额起步** - 逐步增加投入金额
3. **理解风险** - 杠杆交易可能导致爆仓
4. **保管密钥** - 私钥泄露等同于资产丢失

## 🔗 相关链接

- [Hyperliquid 官网](https://hyperliquid.xyz/)
- [LangChain 文档](https://python.langchain.com/)
- [测试网水龙头](https://app.hyperliquid-testnet.xyz/faucet)

---

**免责声明**: 本项目仅供学习研究使用。加密货币交易风险极高，使用本系统的一切后果由使用者自行承担。
