---
sidebar_position: 99
title: 常见问题
description: 常见错误和解决方案
---

# 常见问题

## 部署问题

### PermissionError: Permission denied: '/app/logs/...'

运行初始化脚本自动配置 UID/GID：

```bash
bash init-deployment.sh
```

### API 钱包无法下单

能查询余额但无法下单，说明 API 钱包未获授权：

> 在主钱包网页端（hyperliquid.xyz）→ 设置 → API 钱包 → 授权对应地址

### Docker 容器一直重启

检查 `.env` 中的私钥格式，必须以 `0x` 开头：

```bash
HYPERLIQUID_PRIVATE_KEY=0x你的私钥
```

## 交易问题

### Cannot increase position when open interest is at cap

该资产已达到开放利益上限，换其他交易对即可：

```yaml
trading:
  symbols: [ETH]  # 改为其他支持的交易对
```

### Leverage exceeds maximum allowed

不同资产有不同的最大杠杆限制（如 ETH 最大 25x），降低配置：

```yaml
trading:
  max_leverage: 5   # 降低杠杆
```

### LLM 调用超时

可配置 LLM 回退地址：

```bash
LLM_FALLBACK_API_BASE=xxx
LLM_FALLBACK_API_KEY=xxx
LLM_FALLBACK_MODEL=xxx
```

## 回测问题

### 回测中断后如何恢复

```bash
uv run python backtest.py \
  --resume-from backtest_results/backtest_BTC_xxx/live_report.json
```

## 测试资金

获取 Hyperliquid 测试网水龙头资金：[https://app.hyperliquid-testnet.xyz/faucet](https://app.hyperliquid-testnet.xyz/faucet)
