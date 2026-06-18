---
sidebar_position: 1
title: 永续合约 Agent
description: 基于 Pydantic AI 的多 Agent 永续合约交易策略
---

# 永续合约 Agent

永续合约 Agent 是 Quant Flow 的主交易策略，入口为 `main.py`。它采用多 Agent 架构，每个交易对拥有独立的决策上下文，避免不同资产之间的干扰。

## 架构概览

```
MarketData (K线/指标) ──→ EnhancedSingleSymbolAgent ──→ ExecutionAgent
                              ↑                              │
                    MarketMonitor (波动触发)                  ↓
                                                      OrderManager
                                                      HyperliquidClient
```

每个交易对独立运行以下流程：

1. **数据采集** — 获取 OHLCV 数据、计算技术指标（MA、RSI、MACD、Bollinger）
2. **增强分析** — 可选：CEX 资金费率、链上数据、多空辩论
3. **LLM 决策** — 基于 FinCoT 推理链生成结构化决策
4. **决策验证** — 多周期趋势共振、信号质量、风险回报比校验
5. **仓位计算** — 凯利公式动态仓位
6. **执行** — 下单、设置止盈止损

## 快速运行

```bash
# 本地运行
uv run python main.py

# 指定配置文件
uv run python main.py --config config.yaml --env .env

# Docker 运行
docker compose up -d
```

## 决策间隔

策略支持两种触发机制：

| 机制 | 说明 |
|------|------|
| **定时触发** | `scheduler.interval_minutes` 配置，默认每 3 分钟一次 |
| **波动触发** | 启用 `market_monitor` 后，价格异常波动时立即触发 |

建议将定时间隔设置为较大值（如 30 分钟），依靠市场监控覆盖突发行情，降低无效 LLM 调用。

## 上下文压缩

长期运行时，历史消息会积累导致 Token 成本上升。`SummaryAgentV2` 定期将历史对话压缩为摘要，保留关键信息的同时显著降低 Token 消耗。

## 支持的 Prompt 策略

| 策略集 | 风格 |
|--------|------|
| `default` | 标准 FinCoT，均衡策略 |
| `conservative` | 趋势分歧即 HOLD，盈亏比 ≥ 2.0 |
| `aggressive` | 3 条件入场，盈亏比 ≥ 1.2 |
| `nof1-improved` | 推荐：完整 FinCoT + 增强数据集成 |
| `realtime` | 价格行为优先于滞后指标 |

在 `config.yaml` 中设置：

```yaml
prompt:
  set: nof1-improved
```

## 相关文档

- [配置参考](../configuration/config-yaml) — 完整配置说明
- [FinCoT 推理](../features/fincot) — 6 步推理链详解
- [多空辩论](../features/debate) — Bull/Bear 辩论机制
- [Regime 自适应](../features/regime-adaptive) — 市场状态动态调参
