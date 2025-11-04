# 🤖 Quant Flow - AI驱动的永续合约交易机器人

> 基于 LangChain Agent 和 LLM 的 24/7 智能永续合约交易系统

## ✨ 核心特性

- 🧠 **AI驱动决策** - 使用 Deepseek 等 LLM 进行智能交易
- 🤖 **多 Agent 架构** - 独立上下文 + 汇总分析 + 现货定投决策
- 🗜️ **上下文压缩** ⭐ - 使用 LangChain 压缩技术，Token 减少 75%
- 📊 **技术指标分析** - RSI、MACD、MA、布林带等
- 🛡️ **风险管理** - 自动止盈止损、熔断机制
- 💱 **Hyperliquid** - 去中心化永续合约交易所
- 🎯 **双向交易** - 完整的做多/做空支持
- ⚡ **高性能** - 批量决策、多周期分析
- 🧪 **测试网支持** - 安全的测试环境

## �� 快速开始

### 1. 安装依赖
\`\`\`bash
pip install -e .
\`\`\`

### 2. 配置
\`\`\`bash
cp .env.example .env
cp config.yaml.example config.yaml
# 编辑 .env 填入你的配置
\`\`\`

### 3. 启动

**传统批量决策模式:**
\`\`\`bash
python main.py
\`\`\`

**多 Agent 独立决策模式:**
\`\`\`bash
python main_multi_agent.py
\`\`\`

👉 不确定使用哪个？查看 [使用对比文档](USAGE_COMPARISON.md)

### 🔧 遇到问题？

**诊断工具：**
\`\`\`bash
# 检查账户和开放利益上限
python check_oi_caps.py

# 测试杠杆设置
python test_leverage_fix.py

# 测试交易功能
python test_trading_functions.py
\`\`\`

📖 **完整指南:**
- [故障排除指南](./TROUBLESHOOTING.md)
- [快速开始](./QUICKSTART.md)
- [多 Agent 架构文档](./MULTI_AGENT_ARCHITECTURE.md) ⭐ 新增
- [使用对比](./USAGE_COMPARISON.md) ⭐ 新增
- [汇总 Agent V2 升级](./SUMMARY_V2_QUICK_START.md) ⭐ 最新

---

## 🎯 多 Agent 架构

Quant Flow 现在支持两种运行模式：

### 1️⃣ 传统批量决策模式 (`main.py`)

- 一次性分析所有交易对
- 快速执行，低资源消耗
- 适合少量交易对

### 2️⃣ 多 Agent 独立决策模式 (`main_multi_agent.py`) ⭐ 新增

**架构组成:**

```
┌─────────────────────────────────────────┐
│         Multi-Agent Architecture        │
├─────────────────────────────────────────┤
│                                         │
│  ┌────────────┐  ┌────────────┐        │
│  │ BTC Agent  │  │ ETH Agent  │  ...   │
│  │ 独立上下文 │  │ 独立上下文 │        │
│  └─────┬──────┘  └─────┬──────┘        │
│        │                │               │
│        └────────┬───────┘               │
│                 ▼                       │
│         ┌──────────────┐                │
│         │ 汇总 Agent   │                │
│         │ 历史汇总生成 │                │
│         └──────┬───────┘                │
│                │                        │
│                ▼                        │
│    发现定投机会？                       │
│         Yes │                           │
│             ▼                           │
│      ┌─────────────┐                    │
│      │现货定投Agent│                    │
│      │ 严格评估    │                    │
│      └─────────────┘                    │
│                                         │
└─────────────────────────────────────────┘
```

**核心优势:**
- 🎯 **独立上下文**: 每个交易对有独立的分析窗口
- 📝 **历史汇总**: 前10次 + 前10-20次 → 综合分析
- 💎 **两层审核**: 单币推荐 → 现货 Agent 严格评估
- 🔄 **专业分工**: 合约 Agent + 现货 Agent 各司其职

**详细文档:**
- [多 Agent 架构详解](./MULTI_AGENT_ARCHITECTURE.md)
- [两种模式对比](./USAGE_COMPARISON.md)

**常见问题快速解决：**
- ❌ \`Cannot increase position when open interest is at cap\` → 切换到其他资产
- ❌ \`Invalid leverage value\` → 查看资产最大杠杆限制（ETH最大25x）
- ❌ \`余额不足\` → 访问测试网水龙头

## ⚠️ 重要提示

1. **先用测试网** - 设置 `HYPERLIQUID_TESTNET=true`
2. **保管私钥** - 这是你的资产凭证
3. **小金额起步** - 测试通过后再加大投入
4. **理解风险** - 杠杆交易风险极大

## 📁 项目结构

\`\`\`
├── main.py                  # 主程序
├── src/
│   ├── trading/
│   │   ├── client.py        # Hyperliquid 客户端
│   │   └── order_manager.py # 订单管理
│   ├── data/
│   │   ├── market_data.py   # 市场数据
│   │   └── indicators.py    # 技术指标
│   ├── agent/               # AI Agent
│   └── utils/               # 工具函数
├── config.yaml              # 配置
└── .env                     # 环境变量
\`\`\`

## 🔗 链接

- [Hyperliquid官网](https://hyperliquid.xyz/)
- [Python SDK](https://github.com/hyperliquid-dex/hyperliquid-python-sdk)
- [测试网水龙头](https://app.hyperliquid-testnet.xyz/faucet)

---

**免责声明**: 仅供学习研究。加密货币交易风险极高，请谨慎使用！
