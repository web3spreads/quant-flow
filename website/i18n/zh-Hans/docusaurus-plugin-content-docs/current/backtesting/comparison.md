---
sidebar_position: 3
title: A/B 对比回测
description: 对比不同功能配置的效果差异
---

# A/B 对比回测

使用 `backtest_comparison.py` 工具自动对比不同功能配置的回测效果。

## 用法

```bash
# 对比所有功能
uv run python backtest_comparison.py --symbol BTC --compare all \
  --start-date 2025-01-01 --end-date 2025-06-01

# 对比特定功能
uv run python backtest_comparison.py --symbol BTC --compare fincot    # FinCoT
uv run python backtest_comparison.py --symbol BTC --compare debate    # 多空辩论
uv run python backtest_comparison.py --symbol BTC --compare onchain   # 链上数据
uv run python backtest_comparison.py --symbol BTC --compare regime    # Regime 自适应
```

## 对比维度

每次对比会运行**基线配置**和**启用目标功能的配置**两次回测，输出对比报告：

- 总收益差异
- 最大回撤变化
- 胜率变化
- Sharpe 比率对比
- Token 消耗变化（FinCoT 对比）
