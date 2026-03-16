---
sidebar_position: 6
title: 复盘反思系统
description: 6 项基于论文的复盘增强功能
---

# 复盘反思系统

复盘系统包含 6 项独立增强功能，全部通过 `config.yaml` 开关控制，默认关闭。

## 功能一览

### 6a. 双粒度反思

**论文**：[arXiv:2510.08068](https://arxiv.org/abs/2510.08068)

- **即时反思**：每笔平仓后触发，纯规则、无 LLM 调用，更新匹配经验的置信度
- **每周反思**：LLM 生成策略级调整建议，检测系统性偏差

### 6b. Regime 感知记忆

经验存储附带 `source_regime` 字段，Regime 不匹配时相似度降权（默认 ×0.4）。

### 6c. 确认偏差防护

**论文**：[arXiv:2407.06567](https://arxiv.org/abs/2407.06567) — FinCon

保护负面经验不被过度淘汰，negative 经验置信度加成（默认 ×1.15）。

### 6d. 事实-主观分离

**论文**：[arXiv:2410.12464](https://arxiv.org/abs/2410.12464) — FS-ReasoningAgent (ICLR 2025)

趋势市中主观经验权重提升（×1.3），震荡市中事实经验权重提升（×1.3）。

### 6e. Prompt 自优化（元反思）

**论文**：[arXiv:2510.15949](https://arxiv.org/abs/2510.15949) — ATLAS Adaptive-OPRO

4 个评估维度，每周反思后生成 Prompt 微调建议（需人工审核后手动应用）。

## 配置

```yaml
review_agent:
  instant_reflection_enabled: false      # 6a 即时反思
  weekly_reflection_enabled: false       # 6a 每周反思
  regime_aware_enabled: false            # 6b Regime 感知
  bias_protection_enabled: false         # 6c 偏差防护
  fact_subjective_split_enabled: false   # 6d 事实-主观分离
  prompt_meta_reflection_enabled: false  # 6e Prompt 自优化
```
