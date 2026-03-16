---
sidebar_position: 2
title: 本地部署
description: 在本地运行 Quant Flow 进行开发和测试
---

# 本地部署

本地运行适合开发、回测以及在无需 Docker 开销的情况下测试配置变更。

## 前置条件

- Python 3.11 或更高版本
- [uv](https://docs.astral.sh/uv/) 包管理器（推荐）或 pip

### 安装 uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## 安装步骤

### 1. 克隆并进入仓库

```bash
git clone https://github.com/web3spreads/quant-flow
cd quant-flow
```

### 2. 安装依赖

```bash
# 安装所有运行时依赖
uv sync

# 安装开发依赖（用于测试、代码检查）
uv sync --group dev
```

### 3. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`：

```bash
# LLM API 密钥（至少配置一个）
OPENAI_API_KEY=sk-...
CLOUDFLARE_ACCOUNT_ID=...       # 可选：Cloudflare Workers AI
CLOUDFLARE_API_TOKEN=...        # 可选
GOOGLE_API_KEY=...              # 可选：Gemini
NVIDIA_API_KEY=nvapi-...        # 可选：NVIDIA NIM

# Hyperliquid
HYPERLIQUID_PRIVATE_KEY=0x...
HYPERLIQUID_TESTNET=true        # 强烈建议本地测试时使用测试网
```

### 4. 配置交易参数

```bash
cp config.yaml.example config.yaml
```

本地最小配置：

```yaml
llm:
  client_type: openai
  model: gpt-4o-mini
  temperature: 0.2

trading:
  symbols: [BTC]
  max_trade_amount: 10    # 测试时保持较小金额
  max_leverage: 2

scheduler:
  interval_minutes: 5
```

## 运行策略

### 永续合约 Agent

```bash
uv run python main.py
```

指定配置文件和环境变量文件：

```bash
uv run python main.py --config config.yaml --env .env
```

### 网格交易

```bash
cp config.grid.yaml.example config.grid.yaml
# 编辑 config.grid.yaml

uv run python grid_main.py --config config.grid.yaml --env-file .env
```

## 运行测试

```bash
# 运行所有测试
uv run pytest tests/

# 运行特定测试文件
uv run pytest tests/test_agents_langgraph.py -v

# 运行并生成覆盖率报告
uv run pytest tests/ --cov=src

# 运行回测（无实盘交易）
uv run python backtest.py --symbol BTC --strategy single \
  --start-date 2024-01-01 --end-date 2024-12-01
```

## 语法检查

```bash
# 检查特定模块
uv run python -m py_compile src/trading/client.py

# 检查所有源文件
find src -name "*.py" | xargs uv run python -m py_compile
```

## 添加依赖

```bash
# 添加运行时依赖
uv add requests

# 添加开发依赖
uv add --group dev pytest-mock
```

## 日志输出

本地运行时，日志同时输出到标准输出和 `logs/` 目录：

```bash
tail -f logs/main.log    # 永续合约 Agent
tail -f logs/grid.log    # 网格交易
```

## 测试网与主网

:::warning 务必先在测试网运行
开发期间请在 `.env` 中设置 `HYPERLIQUID_TESTNET=true`。测试网与主网使用相同的接口，但不涉及真实资金。

确认配置无误后再切换到主网：
```bash
HYPERLIQUID_TESTNET=false
```
:::

## 常见问题排查

| 问题 | 解决方案 |
|---|---|
| `ModuleNotFoundError` | 运行 `uv sync` 重新安装依赖 |
| `logs/ 权限错误` | `mkdir -p logs && chmod 755 logs` |
| 无法连接 Hyperliquid | 检查 `HYPERLIQUID_TESTNET` 配置和网络连接 |
| API 钱包无法交易 | 在 Hyperliquid 主钱包网页上授权 API 钱包地址 |

## 下一步

- [环境变量](../configuration/env.md)
- [config.yaml 参考](../configuration/config-yaml.md)
- [回测](../backtesting/single.md) — 无实盘交易的策略测试
