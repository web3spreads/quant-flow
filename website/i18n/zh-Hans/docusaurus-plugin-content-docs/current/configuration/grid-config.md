---
sidebar_position: 3
title: 网格模式配置
description: 网格交易 config.grid.yaml 配置参考
---

# 网格交易配置

网格交易策略使用独立的配置文件 `config.grid.yaml`：

```bash
cp config.grid.yaml.example config.grid.yaml
```

## 交易参数

```yaml
trading:
  symbols: [ETH]                 # 网格交易的交易对
  max_total_investment: 500      # 总投入上限（USD）
  max_leverage: 5                # 最大杠杆

  # 网格订单功能
  grid_limit_order_take_profit_enabled: true   # 网格成交后是否补止盈单
  grid_limit_order_stop_loss_enabled: true     # 网格成交后是否补止损单
  grid_reduce_only_exit_orders_enabled: true   # 是否启用分层 reduce-only 退出单
```

### 退出单模式说明

| 选项 | 说明 |
|---|---|
| `grid_limit_order_take_profit_enabled` | 每个网格层成交后，在下一个网格边界处下限价止盈单 |
| `grid_limit_order_stop_loss_enabled` | 每个网格层成交后，在成交价下方/上方下止损单 |
| `grid_reduce_only_exit_orders_enabled` | 使用分层 reduce-only 退出单代替一次性全平，减少大仓位的滑点 |

:::tip
建议开启所有三种退出单模式。`grid_reduce_only_exit_orders_enabled` 对大仓位尤为重要。
:::

## Agent（AI 决策）参数

```yaml
agent:
  grid_width:
    min_pct: 0.02           # 最小网格间距（占价格的百分比，2%）
    max_pct: 0.15           # 最大网格间距（15%）
    fallback_pct: 0.05      # 数据异常时的回退宽度（5%）
    ai_blend_weight: 0.35   # AI 输出在最终结果中的权重
```

### 网格宽度融合

最终网格宽度是加权融合的结果：

```
最终宽度 = (市场数据宽度 × 0.65) + (AI 建议宽度 × 0.35)
```

市场数据部分使用 ATR 和波动率计算数据驱动的宽度。AI 部分允许 LLM 根据更广泛的背景（新闻、情绪、Regime）进行调整。65/35 的融合比例确保数学引擎主导，同时 AI 提供方向细节。

## LLM 配置

```yaml
llm:
  client_type: langchain_nvidia
  model: qwen/qwen3.5-122b-a10b
  temperature: 0.3               # 网格策略可略高——宽度估算允许更多创意
```

## 调度器

```yaml
scheduler:
  interval_minutes: 5    # 网格重新评估间隔
```

网格决策每 5 分钟运行一次。在间隔期间，GridManager 自动监控成交并下退出单——成交处理无需 LLM 调用。

## 数据配置

```yaml
data:
  timeframe: 15m    # 较短周期更适合网格——对价格行为更敏感
```

:::info
与永续合约 Agent（1h）相比，网格交易更适合使用较短周期（15m、5m），这样可以获得更精确的波动率估算用于网格间距计算。
:::

## 完整示例

```yaml
llm:
  client_type: langchain_nvidia
  model: qwen/qwen3.5-122b-a10b
  temperature: 0.3

trading:
  symbols: [ETH]
  max_total_investment: 500
  max_leverage: 5
  grid_limit_order_take_profit_enabled: true
  grid_limit_order_stop_loss_enabled: true
  grid_reduce_only_exit_orders_enabled: true

agent:
  grid_width:
    min_pct: 0.02
    max_pct: 0.15
    fallback_pct: 0.05
    ai_blend_weight: 0.35

scheduler:
  interval_minutes: 5

data:
  timeframe: 15m
```

## 网格状态持久化

GridManager 在每次订单操作后将状态**原子写入**到 `grid_state.json`（通过 tempfile + rename 实现），防止意外关机时的文件损坏。

重启机器人时，GridManager 会读取 `grid_state.json` 并与交易所对账，检测离线期间的成交情况。

:::warning 请勿手动编辑 grid_state.json
直接编辑 `grid_state.json` 可能导致 GridManager 丢失对活跃订单的追踪，进而在交易所产生孤儿订单。
:::

## 安全机制

GridManager 内置多项安全功能：

- **孤儿触发单清理** — 检测并撤销没有对应仓位的触发单
- **分层 reduce-only 退出** — 大仓位逐层退出，降低市场冲击
- **撤单硬超时（20s）** — 撤单 20 秒未确认则重试并告警
- **原子写入状态** — 进程中断时防止 JSON 文件损坏

## 下一步

- [网格交易策略](../strategies/grid-flow.md) — 网格交易算法工作原理
- [网格回测](../backtesting/grid.md) — 实盘前先测试网格配置
