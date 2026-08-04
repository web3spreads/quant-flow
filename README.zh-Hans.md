<div align="center">

# Quant Flow

**基于 Pydantic AI 的 AI 加密货币永续合约与网格交易机器人，支持 Hyperliquid DEX**

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![Pydantic AI](https://img.shields.io/badge/Pydantic%20AI-latest-red.svg)](https://ai.pydantic.dev)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![文档](https://img.shields.io/badge/文档-GitHub%20Pages-blue)](https://web3spreads.github.io/quant-flow/zh-Hans/docs/intro)

[**📖 完整文档**](https://web3spreads.github.io/quant-flow/zh-Hans/docs/intro) · [**English README**](README.md)

</div>

> ⚠️ **免责声明**：本项目仅供学习研究使用。杠杆交易存在重大亏损风险，请先在测试网验证策略，生产环境使用需自行承担风险。

---

## 项目简介

Quant Flow 是基于 [Hyperliquid DEX](https://hyperliquid.xyz/) 的 AI 自动交易系统。本项目已**全面重构至 Pydantic AI 框架**，用以提供原生类型安全、结构化输出以及极佳的性能。

永续合约方向交易与网格做市策略已合并到单一程序入口（`main.py`），且均可通过配置文件独立的开关灵活启用或禁用。

| 策略 | 配置开关 | 说明 |
|------|----------|------|
| **永续合约 Agent** | `trading.perp_enabled` | 多 Agent 架构，每个交易对独立决策上下文 |
| **网格交易 Grid Flow** | `trading.grid_enabled` | AI 驱动的动态网格做市，LLM 判断方向和宽度，数学引擎计算参数 |

## 核心功能

### 基础能力

- 🤖 **多 Agent 架构** — 基于 Pydantic AI 实现的每个交易对独立决策 Agent
- 🔌 **多 LLM 支持** — OpenAI、NVIDIA、Google、Cloudflare、LiteLLM
- 📊 **统一运行器** — 支持在单进程中同时并发运行永续合约与网格做市策略
- 📐 **凯利公式仓位管理** — 动态计算最优仓位
- 🛡️ **ATR 动态止盈止损** — 波动率自适应风险管理
- 🔒 **账户保护** — 插件化风控：最大回撤 / 单日亏损 / 连续亏损 / 持仓超时，可独立开关组合
- 🔍 **决策验证** — 多周期趋势共振、信号质量评估
- 📈 **回测支持** — `single/grid` 双策略，支持中断恢复
- 🔄 **API 回退机制** — LLM 和 Hyperliquid API 双重回退

### AI 决策增强功能（基于论文研究）

| 功能 | 论文 | 配置开关 | 说明 |
|------|------|----------|------|
| **FinCoT 结构化推理** | [arXiv:2506.16123](https://arxiv.org/abs/2506.16123) | `prompt.set: nof1-improved` | 6步强制推理链，准确率 +17%，token 消耗 -8.9x |
| **多空辩论 Agent** | [arXiv:2412.20138](https://arxiv.org/abs/2412.20138) | `debate.enabled` | Bull/Bear 双 Agent 消除确认偏见 |
| **CEX 领先信号 + 链上数据** | [MDPI 2026](https://www.mdpi.com/2227-7390/14/2/346) | `enhanced_analysis.enabled` | Binance 资金费率、恐惧贪婪指数、MVRV/SOPR |
| **Regime 自适应策略** | [Springer 2025](https://link.springer.com/article/10.1007/s42521-024-00123-2) | `regime_adaptive.enabled` | 趋势/震荡/高波动三种市场状态动态调参 |
| **市场主动监控** | — | `market_monitor.enabled` | 独立线程，异常波动触发决策循环 |

所有增强功能通过配置文件**独立开关控制，默认关闭**。

## 快速开始

### Docker 部署（推荐）

```bash
# 1. 初始化（自动配置 UID/GID、创建目录）
bash init-deployment.sh

# 2. 配置
cp config.yaml.example config.yaml
vim .env           # 填入 API 密钥和私钥
vim config.yaml    # 配置交易参数，启用/禁用 perp 或 grid

# 3. 启动（单进程同时并发执行所有已启用的策略）
docker compose up -d

# 查看日志
docker compose logs -f
```

### 本地开发

```bash
# 安装 uv（如未安装）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 安装依赖（需要 Python 3.11+）
uv sync

# 配置
cp .env.example .env
cp config.yaml.example config.yaml

# 运行主程序
uv run python main.py
```

## 配置指南

### 环境变量 (`.env`)

```bash
# LLM API (根据 config.yaml 中的 client_type 配置)
NVIDIA_API_KEY=xxx
OPENAI_API_KEY=xxx
OPENAI_API_BASE=xxx

# Hyperliquid
HYPERLIQUID_PRIVATE_KEY=0x...   # 钱包私钥
HYPERLIQUID_TESTNET=true        # true=测试网, false=主网
```

> **钱包模式**：单钱包模式（仅填写 `HYPERLIQUID_PRIVATE_KEY`）或 API 代理钱包模式（同时填写 `HYPERLIQUID_ACCOUNT_ADDRESS`，需在网页端完成授权）。

### 交易参数配置 (`config.yaml`)

```yaml
llm:
  client_type: langchain_openai   # openai / cloudflare / google / litellm / nvidia
  model: qwen/qwen3-next-80b-a3b-instruct   # 根据供应商可用的模型填写
  temperature: 0.2

trading:
  # 策略总开关
  perp_enabled: true
  grid_enabled: false

  symbols: [BTC, ETH]
  max_trade_amount: 100
  max_leverage: 10

prompt:
  set: nof1-improved   # 推荐：整合了 FinCoT 6步推理

enhanced_analysis:
  enabled: true

debate:
  enabled: false       # 开启会增加每次决策的 LLM 调用次数

regime_adaptive:
  enabled: false       # 需要同时启用 enhanced_analysis

# 插件化账户级别风控保护。空列表表示关闭全部风控。
protections:
  - name: max_drawdown
    max_drawdown_pct: 0.10
    pause_hours: 4
  - name: daily_loss
    max_daily_loss_pct: 0.05
    pause_hours: 4
  - name: consecutive_loss
    max_consecutive_losses: 5
    per_symbol: true   # true = 仅锁定亏损的交易对
    pause_hours: 4
  - name: position_timeout
    max_position_hours: 48

market_monitor:
  enabled: false
  alert_threshold_pct: 3.0
```

完整的参数详细说明参考 [`config.yaml.example`](config.yaml.example)。

## 策略回测

```bash
# 永续合约 Agent 单币种策略回测
uv run python backtest.py --symbol BTC --strategy single \
  --start-date 2024-01-01 --end-date 2024-12-01

# 网格做市策略回测
uv run python backtest.py --symbol BTC --strategy grid \
  --start-date 2024-01-01 --end-date 2024-12-01

# 从中断处（检查点）恢复回测
uv run python backtest.py --resume-from backtest_results/backtest_BTC_xxx/live_report.json

# 确定性回看录制：录制 LLM 决策，并在几秒钟内完成多次规则调试 (仅限 single 策略)
uv run python backtest.py --symbol BTC --strategy single \
  --start-date 2024-01-01 --end-date 2024-03-01 \
  --record-decisions decisions.jsonl
uv run python backtest.py --symbol BTC --strategy single \
  --start-date 2024-01-01 --end-date 2024-03-01 \
  --replay-decisions decisions.jsonl
```

更详细的回测系统说明参考 [`BACKTEST_README.md`](BACKTEST_README.md)。

## 单元测试

```bash
uv run pytest tests/
uv run pytest tests/test_decision_validator.py -v
uv run pytest tests/ --cov=src
```

## 项目结构

```
quant-flow/
├── main.py                    # 机器人主入口（运行永续合约与网格交易）
├── backtest.py                # 策略回测运行器
├── src/
│   ├── agent/                 # 基于 Pydantic AI 的 Agent 实现
│   ├── trading/               # 交易核心逻辑（订单管理、网格管理、客户端等）
│   ├── plugins/protections/   # 插件化风控保护机制
│   ├── data/                  # 市场行情抓取、技术指标、多周期分析等
│   ├── llm/                   # 统一 LLM 客户端层
│   ├── backtest/              # 回测引擎及决策录制回放实现
│   └── notification/          # 钉钉、飞书等通知推送模块
├── prompts/                   # 8 套系统级别 Prompt 模板
├── website/                   # Docusaurus 本地文档部署
└── tests/                     # 单元测试与回归测试套件
```

## Docker 常用管理命令

```bash
docker compose up -d           # 后台启动
docker compose down            # 停止并清理容器
docker compose logs -f         # 实时追踪日志
docker compose ps              # 运行状态查看

# 更新代码并重新构建启动
git pull && docker compose build && docker compose up -d
```

## 常见问题诊断

| 问题现象 | 解决办法 |
|----------|----------|
| `PermissionError: /app/logs/...` | 运行 `bash init-deployment.sh` 修复目录权限 |
| `open interest is at cap` | 交易所持仓量限制，请调小开单量或更换币种 |
| `Leverage exceeds maximum allowed` | 调低 `config.yaml` 中设置的 `max_leverage` |
| 代理钱包无法成交 | 确保已在主钱包网页中完成对 API 代理钱包的授权 |

## 外部链接

- 📖 [完整系统设计及教程文档](https://web3spreads.github.io/quant-flow/)
- 🏦 [Hyperliquid 交易所](https://hyperliquid.xyz/)
- 🚰 [测试网领水地址](https://app.hyperliquid-testnet.xyz/faucet)
- ⚙️ [Pydantic AI 官方文档](https://ai.pydantic.dev)

---

[🇺🇸 English README.md](README.md)
