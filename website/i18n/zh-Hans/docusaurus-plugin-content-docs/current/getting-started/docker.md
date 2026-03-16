---
sidebar_position: 1
title: Docker 部署
description: 使用 Docker Compose 在生产环境部署 Quant Flow
---

# Docker 部署

Docker Compose 是生产环境运行 Quant Flow 的推荐方式。它负责进程监控、日志管理和自动重启。

## 前置条件

- Docker 24+ 和 Docker Compose v2
- 已充值的 Hyperliquid 账户
- 至少一个 LLM 提供商的 API 密钥（OpenAI、DeepSeek、NVIDIA NIM 等）

## 部署步骤

### 1. 克隆仓库

```bash
git clone https://github.com/loadchange/quant-flow
cd quant-flow
```

### 2. 初始化部署环境

运行初始化脚本，创建必要目录并设置正确权限：

```bash
bash init-deployment.sh
```

此脚本会创建 `logs/`、`data/`、`backtest_results/` 目录并设置正确的权限。

:::tip
如果后续出现 `PermissionError: Permission denied: '/app/logs/...'`，重新运行 `bash init-deployment.sh` 即可。
:::

### 3. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env` 填入你的凭据：

```bash
# LLM 提供商
OPENAI_API_KEY=sk-...
# 或者使用 NVIDIA NIM
NVIDIA_API_KEY=nvapi-...

# Hyperliquid
HYPERLIQUID_PRIVATE_KEY=0x...
HYPERLIQUID_TESTNET=true   # 生产环境设为 false
```

### 4. 配置交易参数

```bash
cp config.yaml.example config.yaml
```

最基本的配置：

```yaml
llm:
  client_type: openai
  model: gpt-4o-mini

trading:
  symbols: [BTC]            # 从一个交易对开始
  max_trade_amount: 50      # 每笔交易 USD 上限
  max_leverage: 3           # 保守杠杆
```

详细配置说明请参考 [config.yaml 参考](../configuration/config-yaml.md)。

### 5. 选择运行模式

在 `docker-compose.yml` 或环境变量中设置 `RUN_MODE`：

| `RUN_MODE` | 运行内容 |
|---|---|
| `main`（默认）| 仅运行永续合约 Agent |
| `grid` | 仅运行网格交易 |
| `all` | 同时运行两种策略 |

```yaml
# docker-compose.yml
environment:
  - RUN_MODE=main
```

如需网格模式，还需配置 `config.grid.yaml`：

```bash
cp config.grid.yaml.example config.grid.yaml
```

## 运行命令

```bash
# 后台启动
docker compose up -d

# 查看日志
docker compose logs -f

# 查看特定策略日志
tail -f logs/main.log
tail -f logs/grid.log

# 停止
docker compose down
```

## 健康检查

```bash
# 检查容器状态
docker compose ps

# 查看最近日志
docker compose logs --tail=50

# 重启单个服务
docker compose restart quant-flow
```

## 更新

```bash
git pull
docker compose build
docker compose up -d
```

## 部署后的文件结构

```
quant-flow/
├── logs/
│   ├── main.log        # 永续合约 Agent 日志
│   └── grid.log        # 网格交易日志
├── data/
│   └── *.json          # 市场数据缓存
├── backtest_results/   # 回测结果
├── grid_state.json     # 网格状态（持久化保存）
└── .env                # 凭据（绝不提交到版本控制）
```

:::danger 私钥安全
永远不要将 `.env` 提交到版本控制。仓库的 `.gitignore` 已排除该文件，但在推送到任何远程仓库前请务必再次确认。
:::

## Docker Compose 说明

当 `RUN_MODE=all` 时，`docker-compose.yml` 内部使用 `supervisord` 管理多个进程。每个策略的日志输出通过 tee 分别写入 `logs/` 下的对应文件。

## 下一步

- [环境变量](../configuration/env.md) — 完整的 `.env` 参考
- [config.yaml 参考](../configuration/config-yaml.md) — 所有交易参数
- [网格配置](../configuration/grid-config.md) — 网格交易配置
