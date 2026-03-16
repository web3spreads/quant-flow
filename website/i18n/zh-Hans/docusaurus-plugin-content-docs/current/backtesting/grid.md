---
sidebar_position: 2
title: 网格策略回测
description: Grid Flow 网格交易策略回测
---

# 网格策略回测

对网格交易策略进行历史数据回测。

## 基本用法

```bash
uv run python backtest.py \
  --symbol ETH \
  --strategy grid \
  --start-date 2024-01-01 \
  --end-date 2024-12-01
```

## 网格回测特点

网格策略回测会模拟完整的网格布单过程：
- AI 决策市场方向（LONG/SHORT/NEUTRAL）
- 数学引擎计算网格参数
- 模拟限价单成交和重布过程
- 统计网格收益和手续费

## 中断恢复

```bash
uv run python backtest.py \
  --resume-from backtest_results/backtest_grid_ETH_xxx/live_report.json
```
