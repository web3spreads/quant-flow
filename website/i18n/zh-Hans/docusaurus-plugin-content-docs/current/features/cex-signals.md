---
sidebar_position: 3
title: CEX 领先信号 + 链上数据
description: Binance 资金费率、恐惧贪婪指数、MVRV/SOPR 信号
---

# CEX 领先信号 + 链上数据

**论文依据**：
- [MDPI Mathematics 2026](https://www.mdpi.com/2227-7390/14/2/346) — CEX 价格发现能力比 DEX 高 61%
- [ScienceDirect 2025](https://www.sciencedirect.com/science/article/pii/S266682702500057X) — MVRV/SOPR 被验证为强方向信号

## 数据源

| 数据 | 来源 | 信号逻辑 |
|------|------|----------|
| CEX 资金费率 | Binance 公开 API | CEX 费率急变但 HL 未跟随 → 领先预警 |
| 恐惧贪婪指数 | alternative.me | 极端恐惧/贪婪 → 逆向信号 |
| 链上 MVRV/SOPR | blockchain.info | MVRV &gt; 3.5 过热，&lt; 1.0 低估 |

## 启用方式

```yaml
enhanced_analysis:
  enabled: true   # 启用后自动采集，无需单独配置
```

所有数据源支持**优雅降级**——API 不可用时不影响主流程，返回中性默认值。

## Prompt 注入变量

| 变量 | 内容 |
|------|------|
| `{{ cex_funding_signal }}` | CEX 与 HL 资金费率差异分析 |
| `{{ onchain_summary }}` | MVRV/SOPR 链上状态摘要 |
| `{{ fear_greed_index }}` | 当前恐惧贪婪指数及解读 |

核心代码：`src/data/data_enricher.py` — `MarketDataEnricher`
