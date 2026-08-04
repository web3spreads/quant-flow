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

最小本地配置示例：

```yaml
llm:
  client_type: openai
  model: gpt-4o-mini
  temperature: 0.2

trading:
  perp_enabled: true
  grid_enabled: false
  symbols: [BTC]
  max_trade_amount: 10    # 本地测试建议设小一些
  max_leverage: 2

scheduler:
  interval_minutes: 5
```

## 运行机器人

运行统一的交易机器人主程序：

```bash
uv run python main.py
```

指定特定的配置文件和环境文件：

```bash
uv run python main.py --config config.yaml --env-file .env
```

通过配置 `config.yaml` 文件中的 `perp_enabled` 和 `grid_enabled` 开关，主程序可以同时或单独运行永续合约 Agent 交易和网格做市策略。

## 运行测试

```bash
# 运行全部测试
uv run pytest tests/

# 运行特定测试文件
uv run pytest tests/test_decision_validator.py -v

# 运行测试并生成覆盖率报告
uv run pytest tests/ --cov=src

# 运行历史回测（无实盘资金交互）
uv run python backtest.py --symbol BTC --strategy single \
  --start-date 2024-01-01 --end-date 2024-12-01
```

## 语法检查

```bash
# 检查特定模块的语法错误
uv run python -m py_compile src/trading/client.py

# 检查所有源文件
find src -name "*.py" | xargs uv run python -m py_compile
```

## 添加新依赖

```bash
# 添加运行时依赖
uv add requests

# 添加开发依赖
uv add --group dev pytest-mock
```

## 日志输出

本地运行时，日志会输出到终端（stdout）并同时写入 `logs/` 目录：

```bash
tail -f logs/main.log    # 永续合约/网格交易统一主日志
```

## 测试网 vs 主网

:::warning 强烈建议从测试网开始
开发期间请在 `.env` 中设置 `HYPERLIQUID_TESTNET=true`。测试网接口和主网完全一致，但不涉及真实资金安全。

当且仅当策略在测试网运行稳定并验证无误后，再切回主网：
```bash
HYPERLIQUID_TESTNET=false
```
:::

## 常见问题诊断

| 问题现象 | 解决办法 |
|---|---|
| `ModuleNotFoundError` | 重新运行 `uv sync` 安装依赖 |
| `PermissionError on logs/` | `mkdir -p logs && chmod 755 logs` 修复权限 |
| `Cannot connect to Hyperliquid` | 检查 `HYPERLIQUID_TESTNET` 连通性和网络 |
| 代理钱包无法成交 | 确保已在 Hyperliquid 网页主钱包中授权了 API 代理钱包地址 |

## 下一步

- [环境变量配置](../configuration/env.md)
- [config.yaml 详细参考](../configuration/config-yaml.md)
- [策略历史回测](../backtesting/single.md)
