---
sidebar_position: 1
title: 系统架构
description: Quant Flow 整体数据流和架构设计
---

# 系统架构

## 永续合约主策略数据流

```
ExternalInfoAgent (Exa API 市场资讯)
          ↓
MarketData (K线/技术指标)
          ↓
EnhancedSingleSymbolAgent (每交易对独立决策)
    ↑ 波动触发
MarketMonitor (独立线程，30s 间隔)
          ↓
DecisionValidator + PositionSizer + RiskManager
          ↓
AccountProtector (回撤保护/超时清仓)
          ↓
OrderManager → HyperliquidClient
    [止损失败 → 自动平仓重试 ×3]
          ↓
SummaryAgentV2 (上下文压缩) + ReviewAgent (复盘学习)
```

## 网格策略数据流

```
MarketData → GridAgent (AI 决策: 方向 + 宽度)
                  ↓
          calculate_grid_config (数学引擎)
          65% 市场数据 + 35% AI 融合
                  ↓
          GridManager (布单/同步/安全机制)
                  ↓
          HyperliquidClient + OrderManager
```

## 设计原则

1. **策略解耦** — 主策略和网格策略完全独立，可并行运行
2. **功能开关** — 所有增强功能通过配置文件独立控制，默认关闭
3. **防御性设计** — 关键操作均包含重试和回退逻辑
4. **原子写入** — 状态文件使用 tempfile + move 保证一致性
5. **线程安全** — 监控线程与主线程通过锁隔离共享状态
