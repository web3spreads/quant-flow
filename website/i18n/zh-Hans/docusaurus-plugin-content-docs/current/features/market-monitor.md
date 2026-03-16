---
sidebar_position: 5
title: 市场主动监控
description: 独立线程检测异常波动，实时触发决策循环
---

# 市场主动监控

独立线程在常规决策周期间隔内持续轻量监控价格。检测到异常波动时主动触发决策循环，无需等待下一个定时周期。

## 工作流程

```
监控线程 (30s 间隔)
  → all_mids() 获取最新价格
  → 与参考窗口基准价格对比
  → 波动超阈值 → 生成 VolatilityAlert
  → 触发 trading_cycle(triggered_by_alert=True)
  → {{ volatility_alert }} 注入 LLM Prompt
```

## 告警等级

| 等级 | 阈值 | 行为 |
|------|------|------|
| NORMAL | < 1.5% | 忽略 |
| ELEVATED | ≥ 1.5% | 记录日志（节流 60s），不触发决策 |
| HIGH | ≥ 3.0% | 触发决策 + 进入冷却期 |
| EXTREME | ≥ 5.0% | 触发决策 + 冷却期 + 额外告警 |

## 配置

```yaml
market_monitor:
  enabled: true
  check_interval_seconds: 30      # 检查间隔
  alert_threshold_pct: 3.0        # HIGH 触发阈值（%）
  elevated_threshold_pct: 1.5     # ELEVATED 阈值（%）
  extreme_threshold_pct: 5.0      # EXTREME 阈值（%）
  cooldown_minutes: 5             # 触发后冷却时间
  reference_window_minutes: 10    # 价格基准窗口
```

## 线程安全

- 冷却期**按交易对独立管理**，BTC 触发不影响 ETH
- 预热期内（参考窗口未满）不触发误报
- 使用独立锁保护价格历史、统计计数器和告警队列

核心代码：`src/data/market_monitor.py`
