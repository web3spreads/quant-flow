# TODO：LLM 决策增强路线图

> 基于 40+ 篇加密货币量化交易论文的调研结果，按优先级排列。
> 更新日期：2026-03-12

---

## P0：FinCoT 结构化推理蓝图

### 需求描述

当前 Prompt 给 LLM 提供了大量市场数据和交易规则，但没有强制规定**分析步骤的顺序**，导致 LLM 可能跳过关键分析步骤或在无信号时「强行找理由」开仓。需要将领域专家的推理链硬编码进 Prompt，强制 LLM 按固定步骤输出分析。

### 论文依据

| 论文 | 发表 | 核心结果 |
|------|------|----------|
| [FinCoT: Grounding Chain-of-Thought in Expert Financial Reasoning](https://arxiv.org/abs/2506.16123) | EMNLP 2025 | Qwen3-8B 准确率 63.2%→**80.5%**（+17.3pp）；输出长度缩短 **8.9 倍**，token 成本大幅降低 |
| [Trading-R1: Financial Trading with LLM Reasoning via RL](https://arxiv.org/abs/2509.11420) | arXiv 2025.09 | Sharpe **2.72**，胜率 **70%**，回撤 3.68%；核心是结构化 CoT + 波动率感知推理 |
| [Market-Derived Financial Sentiment Analysis for Crypto](https://arxiv.org/abs/2502.14897) | arXiv 2025.02 | context-aware prompt 在趋势市 Sharpe **5.07**，中性市 **3.73**；证明 Prompt 结构对预测质量至关重要 |

### 核心改动

将 `nof1-improved/trading_prompt_template.md` 的决策框架从「条件罗列」改为「6步强制推理链」：

```
步骤1 趋势确认 → 多周期趋势是否一致？（必须先回答，不可跳过）
步骤2 入场信号 → 哪些技术指标触发？（列出具体数值，不可模糊）
步骤3 情绪校验 → 资金费率/恐惧贪婪是否有逆向信号？
步骤4 复盘比对 → 当前情况匹配哪条历史经验？
步骤5 风险计算 → 止损/止盈距离、盈亏比、手续费覆盖率（必须给出数字）
步骤6 最终决策 → 综合以上 5 步结论，给出决策和置信度
```

### 架构影响

- **改动范围**：仅 `prompts/nof1-improved/trading_prompt_template.md`（主模板）+ 可选同步其他模板
- **不涉及**：Python 代码、数据流、API 调用
- **风险**：低（纯 Prompt 工程，可随时回滚）
- **预期收益**：准确率 +17%，token 消耗 -50~80%

### 实施状态

- [ ] 重构 nof1-improved 决策框架为 6 步推理链
- [ ] 移除冗余的条件罗列（用推理链替代）
- [ ] 同步更新 realtime/default/aggressive/conservative 模板
- [ ] 人工验证：用 3 个历史 case 对比改前/改后的 LLM 输出质量

---

## P1-A：多空辩论 Agent（Bull/Bear Debate）

### 需求描述

当前系统由单个 Agent 完成决策，容易陷入**确认偏见**——一旦初步判断看多，就只挑看多的证据。需要引入对抗性辩论机制：Bull Agent 和 Bear Agent 各自独立分析，由 Synthesis Agent 综合双方论点做最终决策。

### 论文依据

| 论文 | 发表 | 核心结果 |
|------|------|----------|
| [TradingAgents: Multi-Agents LLM Financial Trading Framework](https://arxiv.org/abs/2412.20138) | arXiv 2024.12 | 累积收益、Sharpe、回撤**全面超越** Buy&Hold/MACD/RSI 基线；模拟真实交易公司架构 |
| [QuantAgent: Price-Driven Multi-Agent LLMs for HFT](https://arxiv.org/abs/2509.09995) | arXiv 2025.09 | 4h 方向准确率 **80%**，9 种金融工具零样本验证；4 专业 Agent 分工 |
| [LLM-Powered Multi-Agent System for Crypto Portfolio](https://arxiv.org/abs/2501.00826) | arXiv 2025.01 | 加密市场多 Agent **优于单 Agent 和市场基准**（2023.11-2024.09 评估） |

### 架构设计

```
当前流程:
  数据 → SingleAgent 决策 → DecisionValidator → 执行

改进流程:
  数据 → ┌─ BullAgent（系统提示强制看多立场，输出 3 条论点 + 置信度）
         │
         ├─ BearAgent（系统提示强制看空立场，输出 3 条论点 + 置信度）
         │
         └─ SynthesisAgent（综合双方论点 + 信号数据 → 最终决策）
              ↓
         DecisionValidator → 执行
```

**关键设计决策：**

1. Bull/Bear Agent 可以用同一个 LLM 调用（batch 两个 system prompt），**成本仅增加 1 次调用**
2. SynthesisAgent 可以复用现有的 SingleSymbolAgent，只是 Prompt 输入从「原始数据」变为「双方论点摘要 + 数据」
3. Bull/Bear 输出结构化为 `{stance, arguments[3], confidence, key_risk}`

### 改动评估

| 改动文件 | 改动类型 | 说明 |
|---------|---------|------|
| `src/agents/trading/nodes.py` | 新增节点 | `bull_analysis`, `bear_analysis`, `synthesize_debate` |
| `src/agents/trading/state.py` | 扩展 State | 新增 `bull_arguments`, `bear_arguments` 字段 |
| `src/agents/trading/workflow.py` | 修改拓扑 | 在 `prepare_prompt` 和 `analyze_market` 之间插入辩论环节 |
| `prompts/nof1-improved/` | 新增模板 | `bull_prompt.md`, `bear_prompt.md`, `synthesis_prompt.md` |
| `config.yaml` | 新增配置 | `debate_enabled: true`, `debate_model` 等 |

**风险：**
- LLM 调用次数增加（+2 次），单轮决策延迟增加 ~3-5 秒
- 需要通过 `config.yaml` 开关控制，可随时关闭

### 实施状态

- [ ] 设计 Bull/Bear Prompt 模板（强制立场 + 结构化输出）
- [ ] 扩展 `TradingAgentState` 添加辩论字段
- [ ] 新增 `bull_analysis` 和 `bear_analysis` LangGraph 节点
- [ ] 新增 `synthesize_debate` 节点（综合双方论点）
- [ ] 修改 workflow 拓扑，插入辩论环节
- [ ] 添加 config 开关 `debate.enabled`
- [ ] 回测对比：辩论模式 vs 单 Agent 模式的胜率/Sharpe

---

## P1-B：CEX 领先信号 + 链上 MVRV/SOPR

### 需求描述

我们在 Hyperliquid（DEX）上交易，但研究表明 CEX 是价格发现的主导场所，信息单向从 CEX 流向 DEX。监控 CEX 资金费率变化可作为领先指标。同时，链上 MVRV 和 SOPR 是经过验证的周期性方向信号。

### 论文依据

| 论文 | 发表 | 核心结果 |
|------|------|----------|
| [The Two-Tiered Structure of Cryptocurrency Funding Rate Markets](https://www.mdpi.com/2227-7390/14/2/346) | MDPI Mathematics 2026 | CEX 价格发现能力比 DEX 高 **61%**；信息流 CEX→DEX **单向**，零反向因果 |
| [Forecasting BTC Volatility from Whale Transactions](https://arxiv.org/abs/2211.08281) | IEEE Access 2025 | 鲸鱼交易是**最重要的预测特征**；Synthesizer Transformer 融合 CryptoQuant 链上数据优于 SOTA |
| [Bitcoin Price Direction with On-Chain Data](https://www.sciencedirect.com/science/article/pii/S266682702500057X) | ScienceDirect 2025 | SOPR≈1.03 和 MVRV≈2.3x 被验证为**强方向信号**，有效预测卖压减少 |
| [A Reflective LLM-based Agent for Crypto Trading](https://arxiv.org/html/2407.09546v1) | arXiv 2024.07 | 移除链上统计数据后性能**下降 16%**，证明链上数据是关键信号源 |

### 架构设计

**新增数据源：**

```
1. Binance CEX 资金费率（免费公开 API，无需密钥）
   API: GET https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&limit=3
   用途: 与 Hyperliquid 费率对比，计算领先/滞后差值
   信号: CEX 费率急涨但 HL 未跟随 → 多头预警
         CEX 费率急跌但 HL 未跟随 → 空头预警

2. 链上 MVRV 比率（CryptoQuant 或 Blockchain.com 免费 API）
   MVRV > 3.5 → 过热信号（历史牛市顶部区域）
   MVRV < 1.0 → 低估信号（历史熊市底部区域）
   MVRV 1.0-2.0 → 中性

3. 链上 SOPR（花费产出利润率）
   SOPR > 1.05 → 持有者在获利了结，潜在卖压
   SOPR < 0.95 → 持有者在亏损卖出，可能接近底部
   SOPR ≈ 1.0 → 中性
```

**数据流集成：**

```
data_enricher.py
├── _get_oi_and_funding()          # 已有：Hyperliquid 资金费率
├── _get_cex_funding_rate()        # 新增：Binance 资金费率 + 领先信号
├── _get_onchain_mvrv_sopr()       # 新增：MVRV + SOPR
└── _get_fear_greed_index()        # 已有：恐惧贪婪指数
         ↓
    enrich_market_data() → enriched_data dict
         ↓
    prompt_manager.py → 注入模板变量
         ↓
    trading_prompt_template.md → LLM 可见
```

### 改动评估

| 改动文件 | 改动类型 | 说明 |
|---------|---------|------|
| `src/data/data_enricher.py` | 新增方法 | `_get_cex_funding_rate()`, `_get_onchain_mvrv_sopr()` |
| `src/prompt_manager.py` | 新增默认值 | `cex_funding_signal`, `mvrv_signal`, `sopr_signal` |
| `prompts/*/trading_prompt_template.md` | 新增展示 | 在永续合约市场数据区块添加新字段 |
| `config.yaml` | 新增配置 | `onchain_data.enabled`, API 端点配置 |

**风险：**
- 外部 API 不可用时需要优雅降级（已有 fear_greed_index 的降级模式可复用）
- Binance API 有频率限制（1200 次/分钟），但我们每 3 分钟调用 1 次，远低于限制
- MVRV/SOPR 更新频率为日级，不适合高频信号，适合作为方向偏好参考

### 实施状态

- [ ] 实现 `_get_cex_funding_rate()` — Binance 公开 API
- [ ] 实现 `_get_onchain_mvrv_sopr()` — CryptoQuant 或替代 API
- [ ] 计算 CEX-DEX 费率差异领先信号
- [ ] Prompt 模板注入新字段
- [ ] 添加 config 开关和优雅降级
- [ ] 验证：对比添加链上数据前后的决策质量

---

## P2：市场 Regime 自适应策略切换

### 需求描述

当前系统用**同一套参数**处理所有市场状态。但加密市场有明显的 regime 切换——趋势市适合激进跟随、震荡市应该减少交易、高波动市需要缩小仓位。我们已有 `market_state.py`（11 种状态枚举 + 完整分析链），但它仅做分析输出，**没有实际根据状态切换策略参数**。

### 论文依据

| 论文 | 发表 | 核心结果 |
|------|------|----------|
| [Regime Switching Forecasting for Cryptocurrencies](https://link.springer.com/article/10.1007/s42521-024-00123-2) | Digital Finance (Springer) 2025 | Regime 感知策略**显著优于**静态策略，尤其高波动状态 |
| [Adaptive Multi-Agent Bitcoin Trading System](https://arxiv.org/html/2510.08068) | arXiv 2025.10 | 牛市收益比 B&H 高 **30%**，整体 **+15%**；Agent 根据市场状态动态调整权重 |
| [Volatility-Adaptive Trend-Following in Crypto](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5821842) | SSRN 2025 | 自适应模型在 regime 切换期**减少虚假信号**，表现更稳定 |
| [Catching Crypto Trends: A Tactical Approach](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5209907) | SSRN 2025.04 | Sharpe **1.58**，CAGR **30%**，回撤 **19%**（vs BTC 被动持有 80%+）；趋势跟踪 + 轮换策略 |

### 架构设计

**Regime 定义（基于现有 MarketState 枚举聚合为 3 大类）：**

```python
# 将 11 种 MarketState 映射到 3 种 Regime
REGIME_MAP = {
    # 趋势市：信号阈值放宽，杠杆/仓位放大
    "trending": [STRONG_UPTREND, UPTREND, STRONG_DOWNTREND, DOWNTREND,
                 BREAKOUT_UP, BREAKOUT_DOWN],

    # 震荡市：信号阈值收严，杠杆/仓位缩小，减少交易频率
    "ranging": [CONSOLIDATION, WEAK_UPTREND, WEAK_DOWNTREND],

    # 高波动市：极端谨慎，仅做确定性极高的交易
    "volatile": [REVERSAL_BULLISH, REVERSAL_BEARISH],
}
```

**自适应参数矩阵：**

```yaml
regime_adaptive:
  trending:
    signal_threshold: 0.5         # 放宽信号门槛
    min_confidence: 0.35          # 降低置信度要求
    max_leverage: 10              # 允许高杠杆
    position_pct: 0.8             # 允许大仓位
    prompt_hint: "当前为趋势市，适合跟随趋势入场"

  ranging:
    signal_threshold: 0.75        # 收严信号门槛
    min_confidence: 0.55          # 提高置信度要求
    max_leverage: 5               # 限制杠杆
    position_pct: 0.4             # 限制仓位
    prompt_hint: "当前为震荡市，优先观望，仅在极强信号时入场"

  volatile:
    signal_threshold: 0.85        # 极严信号门槛
    min_confidence: 0.65          # 高置信度要求
    max_leverage: 3               # 低杠杆
    position_pct: 0.3             # 小仓位
    prompt_hint: "当前为高波动市，极度谨慎，宁可错过不可做错"
```

**数据流修改：**

```
market_state.py（已有）
  ↓ MarketState 枚举
regime_adapter.py（新增）
  ↓ 映射为 3 种 Regime → 查表获取自适应参数
  ↓ 注入 enriched_data["regime_params"]
enhanced_engine.py（修改）
  ↓ 用 regime_params 覆盖默认的信号阈值/置信度/杠杆
prompt_manager.py（修改）
  ↓ 注入 regime_hint 到 Prompt
trading_prompt_template.md（修改）
  ↓ 在决策框架中展示当前 regime 和对应策略倾向
```

### 改动评估

| 改动文件 | 改动类型 | 说明 |
|---------|---------|------|
| `src/data/regime_adapter.py` | **新增** | Regime 映射 + 参数查表 + 注入逻辑 |
| `src/trading/enhanced_engine.py` | 修改 | `_apply_filters()` 中用 regime 参数覆盖默认阈值 |
| `src/data/data_enricher.py` | 修改 | `enrich_market_data()` 中调用 regime_adapter |
| `src/prompt_manager.py` | 修改 | 新增 `regime_hint` 默认值 |
| `prompts/*/trading_prompt_template.md` | 修改 | 展示 regime 状态和策略倾向 |
| `config.yaml` | 新增 | `regime_adaptive` 参数矩阵 |

**风险：**
- Regime 检测本身有滞后性（依赖历史 K 线），可能在 regime 切换初期误判
- 需要与现有 `enhanced_analysis` 配置协调，避免参数冲突
- 建议先用 config 开关控制，默认关闭，逐步验证

### 实施状态

- [ ] 新增 `src/data/regime_adapter.py`，实现 Regime 映射和参数查表
- [ ] 修改 `enhanced_engine.py`，支持 regime 参数覆盖
- [ ] 修改 `data_enricher.py`，在数据增强流程中注入 regime 信息
- [ ] Prompt 模板添加 `{{ regime_hint }}` 变量
- [ ] 添加 config 配置项 `regime_adaptive`
- [ ] 回测验证：对比自适应 vs 固定参数在不同市场周期的表现

---

## 补充参考论文库

以下论文在调研中发现，与上述四个方向有关联但不是直接依据，留作后续参考：

### LLM 情绪交易
- [Sentiment Trading with LLMs](https://arxiv.org/abs/2412.19245) — OPT 情绪预测准确率 74.4%，多空策略 Sharpe 3.05
- [FinDPO: Preference Optimization for Financial Sentiment](https://arxiv.org/abs/2507.18417) — DPO 微调 LLM，年化收益 67%，Sharpe 2.0
- [Deep Learning and NLP in Crypto Forecasting](https://arxiv.org/abs/2311.14759) — Twitter-RoBERTa + BART MNLI 零样本分类效果最优

### 多 Agent / 自反思
- [SEP: Self-Reflective LLMs for Stock Prediction](https://arxiv.org/abs/2402.03659) — 三步框架 Summarize-Explain-Predict，PPO 自主优化
- [Meta-RL-Crypto](https://arxiv.org/abs/2509.09751) — Actor/Judge/Meta-Judge 三角色自我迭代，无需人工监督
- [LLM Agent in Financial Trading: Survey](https://arxiv.org/abs/2408.06361) — 首篇系统综述，梳理三大 Agent 架构范式

### 微观结构 / 订单流
- [Microstructural Dynamics in Crypto LOBs](https://arxiv.org/abs/2506.05764) — 特征工程比堆叠隐藏层更重要
- [Order Book Filtration for Directional Signal](https://arxiv.org/html/2507.22712v1) — 过滤闪烁流动性和 spoofing 后 OBI 更可靠

### 波动率 / 趋势
- [Time-Varying Factor-Augmented Volatility Forecasting](https://arxiv.org/html/2508.01880) — 波动率预测提升 22.8%，可用于 ATR 参数调优
- [CryptoPulse: Dual-Prediction Forecasting](https://arxiv.org/abs/2502.19349) — 宏观+微观双预测融合，10 种对比方法中全面领先
- [LLMs for Nowcasting Crypto Markets](https://www.mdpi.com/2674-1032/4/4/53) — Gemini-2.5-Pro 在大多数资产上 nowcasting 最佳

### 工具 / 框架
- [FinRobot: Open-Source AI Agent Platform](https://arxiv.org/abs/2405.14767) — Smart Scheduler 动态选模型，GitHub 2k+ stars
- [DSL-Driven Trading Framework with ICL](https://link.springer.com/chapter/10.1007/978-981-96-9891-2_24) — DSL 中介匹配率 95.3%，解决 LLM 代码幻觉
