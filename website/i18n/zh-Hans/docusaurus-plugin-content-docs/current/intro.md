---
sidebar_position: 1
title: 简介
description: Quant Flow — 基于 LangChain/LangGraph 的 AI 加密货币永续合约交易机器人
---

# Quant Flow

**Quant Flow** 是一个基于 [LangChain](https://langchain.com/) / [LangGraph](https://langchain-ai.github.io/langgraph/) 构建的 AI 加密货币交易系统，专为 [Hyperliquid DEX](https://hyperliquid.xyz/) 永续合约市场设计。它将大语言模型与量化交易逻辑相结合，在永续合约市场上自主执行交易决策。

## Quant Flow 是什么？

Quant Flow **不是**简单的规则型机器人。它使用 LLM 作为决策引擎，并通过以下数据进行增强：

- 多周期技术指标（MA、RSI、MACD、布林带）
- 来自 Binance 的实时 CEX 资金费率信号
- 链上数据：MVRV、SOPR、恐惧贪婪指数
- 多智能体辩论（多空双方）以消除确认偏差
- 市场 Regime 检测（趋势 / 震荡 / 高波动）
- 持久化复盘与反思系统，从历史交易中持续学习

## 两种独立策略

| 策略 | 入口文件 | 描述 |
|---|---|---|
| **永续合约 Agent** | `main.py` | 多智能体架构，每个交易对独立运行，支持上下文压缩 |
| **网格交易 Grid Flow** | `grid_main.py` | AI 驱动的网格做市策略，LLM 判断方向和宽度，数学引擎计算参数 |

两种策略完全解耦，可通过 Docker `RUN_MODE` 独立或同时运行。

## 核心特性

- 🤖 **LLM 驱动决策** — 支持 GPT-4、DeepSeek、Gemini、Claude 等兼容 OpenAI 接口的模型
- 📊 **FinCoT 结构化推理** — 6步强制推理链（+17.3% 准确率，−8.9× Token 消耗）
- ⚔️ **多空辩论** — 每次交易前两个独立 Agent 分别论证多空方向
- 🌐 **CEX 领先信号** — Binance 资金费率背离作为领先指标
- 📡 **市场 Regime 自适应** — 根据市场状态动态切换策略参数
- 🚨 **主动市场监控** — 独立线程检测价格异常波动并即时触发决策
- 🧠 **6层复盘系统** — 即时反思、每周反思、Regime 感知记忆、偏差保护等
- 🛡️ **账户保护** — 最大回撤熔断、单日亏损上限、持仓超时清仓
- ⚡ **网格交易** — AI 引导的网格做市，支持分层 reduce-only 退出单

## 技术栈

| 组件 | 技术 |
|---|---|
| 编程语言 | Python 3.11+ |
| AI 框架 | LangChain、LangGraph |
| 交易所 SDK | Hyperliquid Python SDK |
| 数据验证 | Pydantic |
| 包管理 | uv |
| 部署 | Docker / Docker Compose |

## 快速开始

```bash
# 1. 克隆仓库
git clone https://github.com/loadchange/quant-flow
cd quant-flow

# 2. 安装依赖
uv sync

# 3. 配置环境变量
cp .env.example .env
# 编辑 .env，填入 API 密钥和 Hyperliquid 私钥

# 4. 配置交易参数
cp config.yaml.example config.yaml
# 编辑 config.yaml

# 5. 运行（推荐先在测试网验证）
uv run python main.py
```

:::warning 请先在测试网测试
在使用真实资金交易前，务必先在 Hyperliquid **测试网**验证配置。在 `.env` 中设置 `HYPERLIQUID_TESTNET=true`。
:::

## 仓库地址

- **GitHub**: [https://github.com/loadchange/quant-flow](https://github.com/loadchange/quant-flow)

## 下一步

- [Docker 部署](./getting-started/docker.md) — 推荐的生产环境部署方式
- [本地部署](./getting-started/local.md) — 开发和测试环境
- [配置参考](./configuration/config-yaml.md) — 所有配置项说明
- [永续合约 Agent 策略](./strategies/perpetual-agent.md) — 主 Agent 工作原理
- [网格交易策略](./strategies/grid-flow.md) — 网格交易工作原理
