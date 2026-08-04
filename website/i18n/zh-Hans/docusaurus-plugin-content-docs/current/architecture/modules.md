---
sidebar_position: 2
title: 模块参考
description: 核心模块说明和代码入口
---

# 模块参考

## src/agent/ — Agent 实现

| 文件 | 说明 |
|------|------|
| `single_symbol_agent.py` | 单币种交易决策 |
| `enhanced_single_symbol_agent.py` | 增强版 Agent（集成风控+辩论+Regime） |
| `grid_agent.py` | 网格交易 AI 决策引擎 |
| `debate.py` | 多空辩论引擎 |
| `summary_agent_v2.py` | 历史上下文压缩 |
| `review_agent.py` | 复盘学习 |
| `execution_agent.py` | 结构化执行计划生成 |
| `instant_reflection.py` | 即时反思（每笔平仓后） |
| `weekly_reflection.py` | 每周策略级反思 |
| `prompt_meta_reflection.py` | Prompt 自优化元反思 |

## src/trading/ — 交易核心

| 文件 | 说明 |
|------|------|
| `client.py` | Hyperliquid SDK 封装，含安全重试机制 |
| `order_manager.py` | 订单管理（限价单 TP/SL 开关） |
| `grid_manager.py` | 网格交易管理器（布单/同步/安全机制） |
| `decision_validator.py` | 决策多维度验证 |
| `risk_manager.py` | ATR 动态止盈止损与仓位计算（含固定金额、固定风险、凯利公式） |
| `enhanced_engine.py` | 增强引擎（Regime 参数覆盖） |

## src/plugins/protections/ — 插件化风控

| 文件 | 说明 |
|------|------|
| `base.py` | `IProtection` 抽象基类 + `ProtectionContext` / `ProtectionReturn` 数据结构 |
| `manager.py` | `ProtectionManager` 编排多插件：`check_all()` / `on_trade_open` / `on_trade_close` / `is_symbol_locked` |
| `drawdown.py` | 最大回撤保护 → CLOSE_ALL + 暂停 |
| `daily_loss.py` | 单日亏损保护 → PAUSE_NEW_TRADES |
| `consecutive_loss.py` | 连续亏损保护，支持 `per_symbol` 锁定（仅锁亏损交易对） |
| `position_timeout.py` | 持仓超时保护 → 上报 affected_symbols 由调用方平仓 |

## src/data/ — 数据模块

| 文件 | 说明 |
|------|------|
| `market_data.py` | K 线和市场数据获取 |
| `indicators.py` | 技术指标（MA、RSI、MACD、Bollinger） |
| `data_enricher.py` | 数据增强（CEX 费率/链上/恐惧贪婪） |
| `market_monitor.py` | 市场主动监控（独立线程） |
| `market_state.py` | 市场状态分析（11 种状态枚举） |
| `signal_scorer.py` | 多因子信号评分（Regime 自适应权重） |
| `regime_adapter.py` | 市场 Regime 自适应参数切换 |

## src/llm/ — LLM 客户端

`llm_client.py` — 多供应商统一封装：OpenAI / Cloudflare / Google / LiteLLM / NVIDIA
