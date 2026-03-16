---
sidebar_position: 2
title: 多空辩论 Agent
description: Bull/Bear 双 Agent 消除 LLM 确认偏见
---

# 多空辩论 Agent

**论文依据**：[arXiv:2412.20138](https://arxiv.org/abs/2412.20138) — TradingAgents，多 Agent 全面超越单 Agent 基线

两个独立 Agent 分别从看多/看空角度分析，辩论结果注入主决策 Prompt，消除单 Agent 的确认偏见。

## 启用方式

```yaml
debate:
  enabled: true   # 每次决策额外 2 次 LLM 调用
```

## 工作流程

```
数据 ──→ BullAgent（强制看多）──→ 3 条论点 + 置信度
     └──→ BearAgent（强制看空）──→ 3 条论点 + 置信度
                    ↓
           综合双方论点
                    ↓
         注入主决策 Prompt ({{ debate_summary }})
```

:::info 延迟影响
启用后每次决策增加约 2-4 秒延迟（2 次额外 LLM 调用）。
:::

## 注意事项

- 可随时开关，不影响其他功能
- 辩论结果通过 `{{ debate_summary }}` 变量注入所有 Prompt 模板
- 核心代码：`src/agent/debate.py` — `run_bull_bear_debate()`
