---
sidebar_position: 2
title: 网格交易 Grid Flow
description: AI 驱动的动态网格做市策略
---

# 网格交易 Grid Flow

Grid Flow 是独立的网格做市策略，入口为 `grid_main.py`。它将 AI 决策与严格的数学引擎结合：LLM 判断市场方向和网格宽度，数学引擎计算最优参数，GridManager 负责布单和同步管理。

## 架构概览

```
MarketData ──→ GridAgent (AI 决策)
                  │ 方向 LONG/SHORT/NEUTRAL
                  │ + 网格宽度建议
                  ↓
           calculate_grid_config (数学引擎)
           65% 市场数据 + 35% AI 融合
                  ↓
           GridManager (布单管理)
           ├── 孤儿 trigger 单清理
           ├── reduce-only 分层减仓
           ├── 撤单硬超时保护 (20s)
           └── 状态文件原子写入
                  ↓
           HyperliquidClient (执行)
```

## 运行方式

```bash
# 本地运行
uv run python grid_main.py --config config.grid.yaml --env-file .env

# Docker 运行
RUN_MODE=grid docker compose up -d

# 同时运行主策略 + 网格策略
RUN_MODE=all docker compose up -d
```

## 核心参数

在 `config.grid.yaml` 中配置：

```yaml
trading:
  symbols: [ETH]
  max_total_investment: 500       # 总投入上限（USD）
  max_leverage: 5

agent:
  grid_width:
    min_pct: 0.02                 # 最小网格宽度 2%
    max_pct: 0.15                 # 最大网格宽度 15%
    fallback_pct: 0.05            # 数据异常回退宽度
    ai_blend_weight: 0.35         # AI 与市场数据融合权重

scheduler:
  interval_minutes: 5             # 网格决策间隔
```

## AI 融合机制

网格宽度通过以下方式计算：

```
最终宽度 = 市场数据宽度 × 0.65 + AI建议宽度 × 0.35
```

市场数据依据 ATR、布林带宽度等指标确定"客观宽度"，AI 输出则反映对当前行情的主观判断。两者加权融合，既避免纯 AI 的不稳定性，也引入对市场状态的理解。

## 安全机制

GridManager 包含多项安全保护：

| 机制 | 说明 |
|------|------|
| 孤儿订单清理 | 自动清理没有对应触发单的悬空订单 |
| 撤单超时保护 | 撤单操作硬超时 20 秒，防止卡死 |
| 原子状态写入 | 使用 tempfile + move 防止进程中断导致状态文件损坏 |
| reduce-only 减仓 | 支持分层 reduce-only 出场单，有序平仓 |

## 止盈止损配置

```yaml
trading:
  grid_limit_order_take_profit_enabled: true   # 网格成交后补止盈单
  grid_limit_order_stop_loss_enabled: true     # 网格成交后补止损单
  grid_reduce_only_exit_orders_enabled: true   # 启用分层减仓单
```

## 相关文档

- [网格模式配置](../configuration/grid-config) — 完整网格配置参考
- [网格回测](../backtesting/grid) — 回测网格策略
