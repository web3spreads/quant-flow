---
sidebar_position: 2
title: 网格交易 Grid Flow
description: AI 驱动的动态网格做市策略
---

# 网格交易 Grid Flow

Grid Flow 是 AI 驱动的动态网格做市策略。它将 AI 决策与严格的数学引擎结合：LLM 判断市场方向和网格宽度，数学引擎计算最优参数，GridManager 负责布单和同步管理。在统一的架构中，通过在 `config.yaml` 中设置 `trading.grid_enabled: true` 并运行 `main.py` 即可启用。

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

通过在配置文件中启用策略，直接运行统一主程序：

```bash
# 本地运行
uv run python main.py

# Docker 运行
# 在 config.yaml 中配置 perp_enabled 和 grid_enabled 开关，然后启动即可：
docker compose up -d
```

## 核心参数

在 `config.yaml` 中配置：

```yaml
trading:
  grid_enabled: true
  symbols: [ETH]
  max_trade_amount: 500           # 总投入上限（USD）
  max_leverage: 5
  grid_limit_order_take_profit_enabled: true
  grid_limit_order_stop_loss_enabled: true
  grid_reduce_only_exit_orders_enabled: true

agent:
  grid_width:
    min_pct: 0.02                 # 最小网格宽度 2%
    max_pct: 0.15                 # 最大网格宽度 15%
    fallback_pct: 0.05            # 数据异常回退宽度
    ai_blend_weight: 0.35         # AI 与市场数据融合权重

scheduler:
  interval_minutes: 5
```

## 动态融合机制

数学引擎（`src/utils/grid_math.py`）将行情历史波动率（ATR）计算得到的“统计网格宽度”与 AI 推荐的“AI 建议网格宽度”按权重（默认 65% : 35%）进行动态加权融合。该公式不仅能发挥 AI 对大趋势的感知力，也通过 ATR 对纯 AI 决策进行了合理限幅（防止其由于情绪影响得出过宽或过窄的网格宽度，降低极端行情下的穿仓风险）。

## 安全与防护机制

- **止盈止损自动补单**：限价单成交后，GridManager 异步自动计算并挂单止盈（TP）与硬止损（SL）。
- **孤儿 Trigger 挂单清理**：如检测到没有主仓位对应的止盈止损单，自动进行撤单防踏空。
- **状态持久化**：每次布单变化使用 tempfile + rename 原子式保存至 `grid_state.json`，确保掉电或容器重启时可安全恢复对齐。
