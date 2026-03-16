---
sidebar_position: 4
title: Regime 自适应策略
description: 根据市场状态动态调整交易参数
---

# Regime 自适应策略

**论文依据**：[Springer Digital Finance 2025](https://link.springer.com/article/10.1007/s42521-024-00123-2) — Regime 感知策略显著优于静态策略

根据市场状态（趋势/震荡/高波动）自动切换交易参数矩阵，避免用同一套参数应对所有市场环境。

## 三种 Regime 默认参数

| Regime | 信号阈值 | 最低置信度 | 最大杠杆 | 仓位比例 |
|--------|----------|-----------|----------|----------|
| 趋势市 (trending) | 0.5 | 0.35 | 10x | 80% |
| 震荡市 (ranging) | 0.75 | 0.55 | 5x | 40% |
| 高波动 (volatile) | 0.85 | 0.65 | 3x | 30% |

## 启用方式

```yaml
enhanced_analysis:
  enabled: true      # 依赖项，必须先启用

regime_adaptive:
  enabled: true
```

## 自定义参数覆盖

```yaml
regime_adaptive:
  enabled: true
  trending:
    signal_threshold: 0.5
    max_leverage: 10
  ranging:
    signal_threshold: 0.75
    max_leverage: 5
  volatile:
    signal_threshold: 0.85
    max_leverage: 3
```

:::warning 依赖关系
`regime_adaptive` 依赖 `enhanced_analysis.enabled: true`，需先开启增强分析。
:::

核心代码：`src/data/regime_adapter.py`、`src/trading/enhanced_engine.py`
