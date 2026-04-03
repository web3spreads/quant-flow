# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在本仓库中工作时提供指导。

---

## 宪法（Constitution）

> **以下条款具有最高优先级，适用于本项目的所有开发活动，不可被其他规则覆盖。**

### 第一条：官方语言

本项目的官方沟通语言为**中文**。所有文档、代码注释、提交信息（commit message）、PR 描述、Issue 讨论、变量命名说明、日志输出、以及与 AI 助手的交互，**必须使用中文**。禁止使用非中文语言进行沟通和文档编写。

**具体要求：**
- 所有 `.md` 文档必须使用中文撰写
- 代码注释和 docstring 必须使用中文
- Git commit message 必须使用英文分类前缀开头（如 `feat:`, `fix:`, `hotfix:`, `refactor:`, `docs:`, `style:`, `test:`, `chore:`, `perf:`, `ci:`, `build:`, `revert:`），分类前缀后的描述内容使用中文。禁止使用中文分类前缀（如"功能:"、"修复:"等）
- PR/Issue 标题和描述必须使用中文
- 日志输出信息必须使用中文
- 配置文件中的说明注释必须使用中文
- 与 Claude Code 的所有交互必须使用中文回复

**例外情况：**
- 代码中的变量名、函数名、类名等标识符使用英文（遵循 Python 编码规范）
- 第三方库的 API 调用和技术术语可保留英文原文
- 国际通用的技术缩写（如 API、SDK、LLM、OHLCV 等）可使用英文
- Git commit message 的分类前缀必须使用英文（如 `feat:`, `fix:`, `hotfix:` 等）

---

## 项目概述

Quant Flow 是一个基于 LangChain/LangGraph 的 AI 加密货币自动交易系统，专为 Hyperliquid DEX 设计。支持两种独立的交易策略：

- **永续合约 Agent**（`main.py`）：多 Agent 架构，每个交易对独立决策，支持上下文压缩以降低 Token 成本
- **网格交易 Grid Flow**（`grid_main.py`）：AI 驱动的网格做市策略，LLM 判断方向和宽度，数学引擎计算参数，GridManager 布单管理

两种策略完全解耦，可独立或并行运行（Docker `RUN_MODE=main|grid|all`）。

**技术栈**: Python 3.11+, LangChain, LangGraph, Hyperliquid SDK, Pydantic

## 常用命令

```bash
# 安装依赖（uv 管理）
uv sync                       # 安装所有依赖
uv sync --group dev           # 安装开发依赖

# 运行主程序（永续合约 Agent）
uv run python main.py

# 指定配置文件运行
uv run python main.py --config config.yaml --env .env

# 运行网格交易
uv run python grid_main.py --config config.grid.yaml --env-file .env

# 运行测试
uv run pytest tests/
uv run pytest tests/test_agents_langgraph.py -v  # 单个文件

# 语法检查
uv run python -m py_compile src/trading/client.py

# 添加新依赖
uv add <package>              # 添加运行时依赖
uv add --group dev <package>  # 添加开发依赖

# Docker 部署（通过 RUN_MODE 环境变量选择运行模式）
# RUN_MODE=main     仅主交易（默认）
# RUN_MODE=grid     仅网格交易
# RUN_MODE=all      同时运行主交易和网格交易
docker compose up -d
docker compose logs -f

# 回测（支持 single/grid 策略）
uv run python backtest.py --symbol BTC --strategy single --start-date 2024-01-01 --end-date 2024-12-01
uv run python backtest.py --symbol BTC --strategy grid --start-date 2024-01-01 --end-date 2024-12-01
uv run python backtest.py --symbol BTC --resume-from workspace/BTC_xxx/live_report.json  # 中断恢复

# A/B 回测对比（对比不同功能配置的效果差异）
uv run python backtest_comparison.py --symbol BTC --compare all
uv run python backtest_comparison.py --symbol BTC --compare debate
uv run python backtest_comparison.py --symbol BTC --compare regime

# 分别查看各程序日志（日志文件通过 tee 写入 logs/ 目录）
tail -f logs/main.log          # 主交易日志
tail -f logs/grid.log          # 网格交易日志
```

## 核心架构

### 系统数据流

```
                          ┌─────────────────────┐
                          │   ExternalInfoAgent │  (Exa API 市场资讯)
                          └──────────┬──────────┘
                                     ↓
┌──────────────┐    ┌─────────────────────────────────────┐    ┌────────────────┐
│  MarketData  │───→│  EnhancedSingleSymbolAgent / Agent  │───→│ ExecutionAgent │
│  (K线/指标)   │    │  (每个交易对独立决策上下文)            │    │ (结构化输出)    │
└──────────────┘    └──────────────────┬──────────────────┘    └───────┬────────┘
                                       ↑                               │
                    ┌──────────────────┘ (异常波动触发)                 │
                    │                                                   │
              ┌─────────────┐                                          │
              │MarketMonitor│  (独立线程，持续监控价格波动)              │
              │(波动检测)    │                                          │
              └─────────────┘                                          │
                                       │                               │
                    ┌──────────────────┼──────────────────┐           │
                    ↓                  ↓                  ↓           ↓
            ┌──────────────┐  ┌───────────────┐  ┌──────────────┐    │
            │DecisionValid.│  │PositionSizer  │  │ RiskManager  │    │
            │(多维度验证)   │  │(凯利公式仓位) │  │(ATR止盈止损) │    │
            └──────┬───────┘  └───────┬───────┘  └──────┬───────┘    │
                   │                  │                  │            │
                   └──────────────────┼──────────────────┘            │
                                      ↓                               ↓
                          ┌─────────────────────┐           ┌────────────────┐
                          │ProtectionManager   │           │  OrderManager  │
                          │ (插件链风控保护)     │           │  (订单执行)     │
                          └──────────┬──────────┘           └───────┬────────┘
                                     │                               │
                                     └───────────────┬───────────────┘
                                                     ↓
                          ┌─────────────────────────────────────────────┐
                          │          HyperliquidClient (交易执行)        │
                          │  ┌─────────────────────────────────────┐    │
                          │  │ 安全机制：止损失败自动平仓 (带重试)   │    │
                          │  └─────────────────────────────────────┘    │
                          └─────────────────────────────────────────────┘
                                                     │
                          ┌──────────────────────────┴──────────────────────────┐
                          ↓                                                     ↓
                  ┌──────────────┐                                     ┌───────────────┐
                  │SummaryAgentV2│                                     │  ReviewAgent  │
                  │(历史压缩)     │                                     │ (复盘学习)    │
                  └──────────────┘                                     └───────────────┘
```

### 网格交易数据流（Grid Flow，独立入口 `grid_main.py`）

```
┌──────────────┐    ┌─────────────────────────────────────┐
│  MarketData  │───→│  GridAgent (AI 决策)                 │
│  (K线/指标)   │    │  判断方向 LONG/SHORT/NEUTRAL         │
└──────────────┘    │  + 网格宽度/层数                     │
                    └──────────────────┬──────────────────┘
                                       ↓
                    ┌──────────────────────────────────────┐
                    │  calculate_grid_config (数学引擎)     │
                    │  65% 市场数据 + 35% AI 融合计算       │
                    └──────────────────┬───────────────────┘
                                       ↓
                    ┌──────────────────────────────────────┐
                    │  GridManager (网格管理器)              │
                    │  ┌────────────────────────────────┐  │
                    │  │ 安全机制：                       │  │
                    │  │ • 孤儿 trigger 单清理            │  │
                    │  │ • reduce-only 分层减仓           │  │
                    │  │ • 撤单硬超时保护 (20s)           │  │
                    │  │ • 状态文件原子写入               │  │
                    │  └────────────────────────────────┘  │
                    └──────────────────┬───────────────────┘
                                       ↓
                    ┌──────────────────────────────────────┐
                    │  HyperliquidClient + OrderManager    │
                    │  (共享组件，限价单布置/撤销)          │
                    └──────────────────────────────────────┘
```

### 目录结构

```
src/
├── agent/                    # Agent 实现
│   ├── single_symbol_agent.py      # 单币种交易决策
│   ├── enhanced_single_symbol_agent.py  # 增强版 Agent (集成风控+辩论+Regime)
│   ├── grid_agent.py               # 网格交易 AI 决策引擎
│   ├── debate.py                   # 多空辩论引擎
│   ├── summary_agent_v2.py         # 历史压缩
│   ├── review_agent.py             # 复盘学习
│   ├── generalization.py           # 经验泛化器（抗过拟合）
│   ├── helpers.py                  # 通用辅助函数
│   ├── instant_reflection.py       # 即时反思（每笔平仓后）
│   ├── weekly_reflection.py        # 每周策略级反思
│   ├── prompt_meta_reflection.py   # Prompt 自优化元反思
│   └── execution_agent.py          # 执行计划生成
├── trading/                  # 交易核心模块
│   ├── client.py                   # Hyperliquid SDK 封装
│   ├── order_manager.py            # 订单管理（支持限价单 TP/SL 开关）
│   ├── grid_manager.py             # 网格交易管理器（布单/同步/安全机制）
│   ├── decision_validator.py       # 决策多维度验证
│   ├── position_sizer.py           # 凯利公式仓位计算
│   ├── risk_manager.py             # ATR动态止盈止损
│   └── enhanced_engine.py          # 增强交易引擎 (Regime 参数覆盖)
├── plugins/                  # 插件系统
│   └── protections/                # 保护插件
│       ├── base.py                     # IProtection 抽象基类 + 数据结构
│       ├── manager.py                  # ProtectionManager 插件编排器
│       ├── drawdown.py                 # 最大回撤保护
│       ├── daily_loss.py               # 单日亏损保护
│       ├── consecutive_loss.py         # 连续亏损保护（支持 per-symbol 锁定）
│       └── position_timeout.py         # 持仓超时保护
├── data/                     # 数据模块
│   ├── market_data.py              # K线和市场数据
│   ├── indicators.py               # 技术指标 (MA, RSI, MACD, Bollinger)
│   ├── data_enricher.py            # 数据增强 (CEX费率/链上数据/恐惧贪婪)
│   ├── market_monitor.py           # 市场主动监控 (异常波动触发决策)
│   ├── market_state.py             # 市场状态分析 (11种状态枚举)
│   ├── signal_scorer.py            # 多因子信号评分 (Regime自适应权重)
│   └── regime_adapter.py           # 市场Regime自适应参数切换
├── utils/                    # 工具模块
│   ├── hyperliquid.py              # SDK 安全初始化（spotMeta 越界过滤）
│   ├── grid_math.py                # 网格数学计算引擎
│   ├── logger.py                   # 日志工具
│   └── cloud_logger.py             # 云端日志同步（aepipe 服务）
├── llm/                      # LLM 客户端
│   └── llm_client.py               # 多供应商支持 (OpenAI/Cloudflare/Google/LiteLLM/NVIDIA)
├── backtest/                 # 回测模块（支持 single/grid 策略 + 中断恢复）
└── notification/             # 通知模块
```

## LLM 决策增强功能

项目实现了五项 LLM 决策增强功能，**全部通过 `config.yaml` 独立开关控制，默认不影响现有流程**：

### 1. FinCoT 结构化推理链

**论文依据**: [FinCoT (arXiv:2506.16123)](https://arxiv.org/abs/2506.16123) — 准确率 +17.3pp，token 消耗 -8.9x

将 Prompt 决策框架从「条件罗列」改为「6步强制推理链」，强制 LLM 按固定步骤分析：

```
步骤1 趋势确认 → 多周期趋势是否一致？
步骤2 入场信号 → 哪些技术指标触发？（列出具体数值）
步骤3 情绪校验 → 资金费率/恐惧贪婪/多空辩论是否有逆向信号？
步骤4 复盘比对 → 当前情况匹配哪条历史经验？
步骤5 风险计算 → 止损/止盈距离、盈亏比、手续费覆盖率
步骤6 最终决策 → 综合以上 5 步，给出决策和置信度
```

**使用方式**: 选择带 FinCoT 的 Prompt 集（推荐 `nof1-improved`）：

```yaml
# config.yaml
prompt:
  set: nof1-improved   # 已集成 FinCoT 的增强 Prompt
```

**已同步模板**: default, aggressive, conservative, realtime（全部 8 套模板均已集成）

### 2. 多空辩论 Agent

**论文依据**: [TradingAgents (arXiv:2412.20138)](https://arxiv.org/abs/2412.20138) — 多 Agent 全面超越单 Agent 基线

两个独立 Agent 分别从看多/看空角度分析，消除单 Agent 确认偏见：

```
数据 → BullAgent（强制看多，输出 3 条论点 + 置信度）
     → BearAgent（强制看空，输出 3 条论点 + 置信度）
     → 综合双方论点 → 注入主决策 Prompt
```

**使用方式**:

```yaml
# config.yaml
debate:
  enabled: true   # 开启后每次决策额外 2 次 LLM 调用
```

**核心代码**: `src/agent/debate.py` — `run_bull_bear_debate()` 函数

### 3. CEX 领先信号 + 链上数据

**论文依据**:
- [MDPI Mathematics 2026](https://www.mdpi.com/2227-7390/14/2/346) — CEX 价格发现能力比 DEX 高 61%
- [ScienceDirect 2025](https://www.sciencedirect.com/science/article/pii/S266682702500057X) — MVRV/SOPR 被验证为强方向信号

新增 3 个外部数据源（自动优雅降级，API 不可用不影响主流程）：

| 数据源 | API | 信号逻辑 |
|--------|-----|----------|
| Binance CEX 资金费率 | 公开 API | CEX 费率急变但 HL 未跟随 → 领先预警 |
| 恐惧贪婪指数 | alternative.me | 极端恐惧/贪婪 → 逆向信号 |
| 链上 MVRV/SOPR | blockchain.info | MVRV>3.5 过热，<1.0 低估 |

**使用方式**: 数据增强默认启用（通过 `enhanced_analysis.enabled`），数据自动采集注入 Prompt：

```yaml
# config.yaml
enhanced_analysis:
  enabled: true   # 启用后自动采集 CEX 费率、链上数据
```

**核心代码**: `src/data/data_enricher.py` — `MarketDataEnricher` 类

### 4. 市场 Regime 自适应策略切换

**论文依据**: [Springer Digital Finance 2025](https://link.springer.com/article/10.1007/s42521-024-00123-2) — Regime 感知策略显著优于静态策略

根据市场状态（趋势/震荡/高波动）动态调整交易参数：

```python
# 11 种 MarketState → 3 种 Regime
"trending"  → 信号阈值放宽 (0.5)，允许高杠杆 (10x)，大仓位 (80%)
"ranging"   → 信号阈值收严 (0.75)，限制杠杆 (5x)，小仓位 (40%)
"volatile"  → 极严阈值 (0.85)，低杠杆 (3x)，极小仓位 (30%)
```

**使用方式**:

```yaml
# config.yaml
regime_adaptive:
  enabled: true   # 启用 Regime 自适应（依赖 enhanced_analysis）
  # 可选：覆盖默认参数
  # trending:
  #   signal_threshold: 0.5
  #   min_confidence: 0.35
  #   max_leverage: 10
```

**核心代码**:
- `src/data/regime_adapter.py` — Regime 映射和参数查表
- `src/data/signal_scorer.py` — Regime 自适应因子权重
- `src/trading/enhanced_engine.py` — `_apply_filters()` 动态阈值覆盖

### 5. 市场主动监控（异常波动触发决策）

在常规决策周期间隔内，独立线程持续轻量级监控市场价格。检测到异常波动时主动触发决策循环，无需等待下一个定时周期。

```
监控线程 (30s 间隔) → all_mids() 获取最新价格
  → 与参考窗口内基准价格对比
  → 波动超阈值 → 生成 VolatilityAlert
  → 回调触发 trading_cycle(triggered_by_alert=True)
  → 告警上下文通过 {{ volatility_alert }} 注入 LLM Prompt
```

**告警等级**:

| 等级 | 阈值 | 行为 |
|------|------|------|
| NORMAL | < 1.5% | 忽略 |
| ELEVATED | ≥ 1.5% | 记录日志（节流 60s），不触发决策 |
| HIGH | ≥ 3.0% | 触发决策循环 + 进入冷却期 |
| EXTREME | ≥ 5.0% | 触发决策循环 + 进入冷却期 + 额外告警 |

**线程安全机制**:
- `_history_lock`: 保护 `_price_history`（监控线程写入，主线程读取）
- `_stats_lock`: 保护统计计数器
- `_alert_lock`: 保护待处理告警队列（`main.py` 中）
- 冷却期按交易对独立管理，避免频繁触发

**使用方式**:

```yaml
# config.yaml
market_monitor:
  enabled: true                    # 启用市场主动监控
  check_interval_seconds: 30       # 检查间隔（秒）
  alert_threshold_pct: 3.0         # HIGH 告警阈值（%）
  elevated_threshold_pct: 1.5      # ELEVATED 阈值（%）
  extreme_threshold_pct: 5.0       # EXTREME 阈值（%）
  cooldown_minutes: 5              # 触发后冷却时间（分钟）
  reference_window_minutes: 10     # 价格基准窗口（分钟）
```

**核心代码**:
- `src/data/market_monitor.py` — `MarketMonitor` 类，独立线程监控
- `main.py` — `_on_market_alert()` 回调，`_consume_pending_alert()` 消费告警

### 6. 复盘系统增强（5 项改进）

基于学术论文验证的 5 项复盘/反思系统增量改进，全部通过 `config.yaml` 独立开关控制，默认关闭。

#### 6a. 双粒度反思

**论文依据**: [Adaptive Multi-Agent Bitcoin Trading (arXiv:2510.08068)](https://arxiv.org/abs/2510.08068) — 双粒度反思

- **即时反思**（每笔平仓后）：纯规则、无 LLM 调用，更新匹配经验的置信度（盈利 ×1.05、亏损 ×0.95）
- **每周反思**（周策略级）：调用 LLM 生成策略级调整建议，检测系统性偏差和反复错误

**核心代码**:
- `src/agent/instant_reflection.py` — `InstantReflector` 类
- `src/agent/weekly_reflection.py` — `WeeklyReflector` 类

#### 6b. Regime 感知记忆

**论文依据**: [Adaptive Memory for Bitcoin Regime Detection (engrXiv 2025)](https://engrxiv.org/)

经验存储附带 `source_regime` 字段（trending/ranging/volatile/unknown），Regime 不匹配时相似度降权（默认 ×0.4），VFT 段落标注 `[趋势市经验]`/`[震荡市经验]` 等。

**核心代码**: `src/agent/review_memory.py` — `get_similar_lessons()` 和 `get_verbal_finetuning_section()` 增强

#### 6c. 记忆确认偏差防护

**论文依据**: [FinCon (arXiv:2407.06567)](https://arxiv.org/abs/2407.06567) + Selective Memory Equilibrium

经验存储附带 `lesson_type` 字段（positive/negative/unknown），淘汰经验时保护 negative 经验不被过度淘汰，negative 经验的置信度给予加成（默认 ×1.15），VFT 段落中 negative 经验使用 `[避免]` 前缀。

**核心代码**:
- `src/agent/review_memory.py` — `_evict_with_bias_protection()` 和 `get_lesson_type_stats()`
- `src/agent/review_agent.py` — `_infer_lesson_type()` 静态方法

#### 6d. 事实-主观分离反思

**论文依据**: [FS-ReasoningAgent (arXiv:2410.12464, ICLR 2025)](https://arxiv.org/abs/2410.12464)

经验存储附带 `source_type` 字段（factual/subjective/mixed），趋势市中主观经验权重提升（默认 ×1.3），震荡/高波动市中事实经验权重提升（默认 ×1.3），VFT 段落标注 `[事实型]`/`[主观型]`。

**核心代码**: `src/agent/review_agent.py` — `_infer_source_type()` 静态方法

#### 6e. Prompt 自优化（元反思）

**论文依据**: [ATLAS Adaptive-OPRO (arXiv:2510.15949)](https://arxiv.org/abs/2510.15949)

4 个评估维度：FinCoT 6步完成度、经验引用率、决策一致性、置信度校准。在每周反思后触发，生成 Prompt 微调建议（需人工审核后手动应用）。

**核心代码**: `src/agent/prompt_meta_reflection.py` — `PromptMetaReflector` 类

**使用方式**:

```yaml
# config.yaml
review_agent:
  # 6a: 双粒度反思
  instant_reflection_enabled: false     # 即时反思
  weekly_reflection_enabled: false      # 每周反思
  weekly_reflection_day: 0              # 0=周一
  weekly_reflection_hour: 8

  # 6b: Regime 感知记忆
  regime_aware_enabled: false
  regime_mismatch_factor: 0.4           # Regime 不匹配时降权因子

  # 6c: 确认偏差防护
  bias_protection_enabled: false
  max_positive_ratio: 0.7               # 最大正面经验比例
  negative_confidence_boost: 1.15       # negative 经验置信度加成

  # 6d: 事实-主观分离
  fact_subjective_split_enabled: false
  trending_subjective_boost: 1.3        # 趋势市主观经验权重提升
  ranging_factual_boost: 1.3            # 震荡市事实经验权重提升

  # 6e: Prompt 自优化
  prompt_meta_reflection_enabled: false
  prompt_optimization_dir: "logs/prompt_optimization"
```

### 功能依赖关系

```
enhanced_analysis.enabled: true       ← 基础开关，启用增强分析和数据采集
  ├── debate.enabled: true            ← 可选，独立开关
  └── regime_adaptive.enabled: true   ← 可选，依赖 enhanced_analysis
market_monitor.enabled: true          ← 独立开关，不依赖其他功能
review_agent:                         ← 复盘系统增强（全部独立开关）
  ├── instant_reflection_enabled      ← 即时反思
  ├── weekly_reflection_enabled       ← 每周反思
  ├── regime_aware_enabled            ← Regime 感知（依赖 enhanced_analysis 获取 regime）
  ├── bias_protection_enabled         ← 确认偏差防护
  ├── fact_subjective_split_enabled   ← 事实-主观分离
  └── prompt_meta_reflection_enabled  ← Prompt 自优化（依赖 weekly_reflection）
cloud_logging.enabled: true          ← 独立开关，云端日志同步（aepipe 服务）
```

### A/B 回测对比

使用 `backtest_comparison.py` 对比不同功能配置的效果：

```bash
# 对比所有功能
uv run python backtest_comparison.py --symbol BTC --compare all

# 对比特定功能
uv run python backtest_comparison.py --symbol BTC --compare fincot    # FinCoT
uv run python backtest_comparison.py --symbol BTC --compare debate    # 辩论
uv run python backtest_comparison.py --symbol BTC --compare onchain   # 链上数据
uv run python backtest_comparison.py --symbol BTC --compare regime    # Regime
```

## 关键模块详解

### 决策验证 (`decision_validator.py`)

在执行交易前进行多维度验证：
- **多周期趋势共振**: 确保不同时间周期趋势一致
- **信号质量验证**: 确保信号强度达到阈值
- **风险回报验证**: 确保风险回报比合理
- **市场环境验证**: 避开不适合交易的市场状态
- **入场时机验证**: 等待更好的入场点

```python
# 验证结果类型
ValidationResult.PASS   # 通过验证
ValidationResult.WARN   # 警告但允许
ValidationResult.BLOCK  # 阻止交易
```

### 仓位管理 (`position_sizer.py`)

基于凯利公式动态计算最优仓位：

```python
# 仓位计算方法
PositionSizeMethod.KELLY                # 凯利公式
PositionSizeMethod.VOLATILITY_ADJUSTED  # 波动率调整
PositionSizeMethod.SIGNAL_BASED         # 基于信号强度

# 调整因子
kelly_factor      # 凯利系数调整
signal_factor     # 信号强度调整
volatility_factor # 波动率调整
drawdown_factor   # 连续亏损收缩
```

### 风险管理 (`risk_manager.py`)

提供动态止盈止损（基于 ATR）：

```python
# 核心参数
max_risk_per_trade: 0.02      # 单笔最大风险 2%
max_total_exposure: 0.5       # 最大总敞口 50%
min_risk_reward_ratio: 1.5    # 最小风险回报比
```

### 保护插件系统 (`src/plugins/protections/`)

插件化风控架构，每个保护规则可独立启用/禁用/配置：

```python
# 4 个内置插件
MaxDrawdownProtection     # 最大回撤 → 全部平仓
DailyLossProtection       # 单日亏损 → 暂停新开仓
ConsecutiveLossProtection # 连续亏损 → 暂停或锁定交易对（per-symbol）
PositionTimeoutProtection # 持仓超时 → 自动平仓

# 核心接口
ProtectionManager.check_all(context)       # 执行所有插件检查
ProtectionManager.is_symbol_locked(symbol) # 查询交易对级锁定
ProtectionManager.on_trade_open/close()    # 分发开平仓事件
```

### 交易客户端 (`client.py`)

Hyperliquid SDK 封装，包含关键安全机制：

```python
# 【关键安全机制】止损单失败时立即平仓（带重试）
if require_stop_loss and not stop_loss_success:
    max_rollback_retries = 3
    for attempt in range(1, max_rollback_retries + 1):
        rollback_result = self.close_position(symbol, size)
        if rollback_success:
            break
```

## Prompt 策略系统

项目支持多套 Prompt 策略，位于 `prompts/` 目录，全部已集成 FinCoT 6 步推理链：

| 策略 | 说明 |
|------|------|
| `default` | 默认策略（标准 FinCoT） |
| `conservative` | 保守策略（趋势分歧即 HOLD，盈亏比 ≥ 2.0） |
| `aggressive` | 激进策略（3 条件即可入场，盈亏比 ≥ 1.2） |
| `nof1` / `nof1-improved` | 增强版策略（推荐，完整 FinCoT + 增强数据集成） |
| `realtime` / `realtime-eng` | 实时策略（价格行为优先于滞后指标） |

通过 `config.yaml` 中的 `prompt.set` 切换策略。`PromptManager` 负责加载和渲染 Jinja2 模板。

模板支持的动态变量包括：`{{ debate_summary }}`（辩论结果）、`{{ volatility_alert }}`（异常波动告警）、`{{ regime_hint }}`（Regime 策略提示）、`{{ cex_funding_signal }}`（CEX 领先信号）、`{{ onchain_summary }}`（链上数据摘要）等。

## 配置结构

### 环境变量 (`.env`)

```bash
# LLM API 密钥
OPENAI_API_KEY=xxx
CLOUDFLARE_ACCOUNT_ID=xxx
CLOUDFLARE_API_TOKEN=xxx
GOOGLE_API_KEY=xxx

# Hyperliquid
HYPERLIQUID_PRIVATE_KEY=xxx
HYPERLIQUID_TESTNET=true/false   # 测试网/主网切换
```

### 配置文件 (`config.yaml`)

```yaml
llm:
  client_type: langchain_nvidia  # openai/cloudflare/google/litellm/nvidia
  temperature: 0.2               # 交易决策建议低温度

trading:
  symbols: [BTC, ETH]            # 交易对（简单符号，非交易对格式）
  max_trade_amount: 100           # 单笔上限（美元）
  max_leverage: 10                # 最大杠杆

# 调度配置
scheduler:
  interval_minutes: 30            # 兜底巡检，突发由 market_monitor 覆盖

# 数据配置
data:
  timeframe: 1h                   # 兜底决策用大周期 K 线，减少噪音

prompt:
  set: nof1-improved              # Prompt 集（推荐 nof1-improved）

# 增强分析（启用后自动采集 CEX/链上数据）
enhanced_analysis:
  enabled: true

# 多空辩论（每次决策额外 2 次 LLM 调用）
debate:
  enabled: true

# Regime 自适应策略（根据市场状态动态调整参数）
regime_adaptive:
  enabled: true

# 保护插件（可任意组合/禁用，空列表=关闭所有风控）
protections:
  - name: max_drawdown
    max_drawdown_pct: 0.10        # 最大回撤 10%
    pause_hours: 4
  - name: daily_loss
    max_daily_loss_pct: 0.05      # 单日亏损 5%
    pause_hours: 4
  - name: consecutive_loss
    max_consecutive_losses: 5
    per_symbol: true              # true=只锁该交易对
    pause_hours: 4
  - name: position_timeout
    max_position_hours: 48

# 市场主动监控（异常波动触发决策循环）
market_monitor:
  enabled: true                   # 突发行情由 monitor 30s 一检覆盖
  check_interval_seconds: 30      # 检查间隔（秒）
  alert_threshold_pct: 3.0        # HIGH 告警阈值（%）
  elevated_threshold_pct: 1.5     # ELEVATED 阈值（%）
  extreme_threshold_pct: 5.0      # EXTREME 阈值（%）
  cooldown_minutes: 5             # 触发后冷却时间（分钟）
  reference_window_minutes: 10    # 价格基准窗口（分钟）

# 复盘系统增强
review_agent:
  instant_reflection_enabled: true      # 即时反思
  weekly_reflection_enabled: true       # 每周反思
  regime_aware_enabled: true            # Regime 感知记忆
  bias_protection_enabled: true         # 确认偏差防护
  fact_subjective_split_enabled: true   # 事实-主观分离
  prompt_meta_reflection_enabled: true  # Prompt 自优化

# 云端日志同步（aepipe 服务）
cloud_logging:
  enabled: false                        # 启用后日志异步同步到云端
  base_url: "https://xxx.workers.dev"   # aepipe 服务地址
  token: "your_admin_token"             # ADMIN_TOKEN 认证令牌
  project: "quant-flow"                 # 项目名称
  logstore: "trading"                   # 日志存储名称
  flush_interval: 5.0                   # 批量发送间隔（秒）
```

### 网格交易配置 (`config.grid.yaml`)

```yaml
trading:
  symbols: [ETH]                          # 网格交易对
  max_total_investment: 500               # 总投入上限（USD）
  max_leverage: 5                         # 最大杠杆
  grid_limit_order_take_profit_enabled: true   # 网格成交后是否补止盈单
  grid_limit_order_stop_loss_enabled: true     # 网格成交后是否补止损单
  grid_reduce_only_exit_orders_enabled: true   # 是否启用分层减仓单

agent:
  grid_width:
    min_pct: 0.02            # 最小网格宽度 2%
    max_pct: 0.15            # 最大网格宽度 15%
    fallback_pct: 0.05       # 数据异常回退宽度
    ai_blend_weight: 0.35    # AI 输出与市场数据融合权重

scheduler:
  interval_minutes: 5        # 网格决策间隔
```

## 设计模式

1. **工具回调模式**: `TradingTools` 通过回调函数将 LLM 决策映射到实际交易操作
2. **延迟导入**: `src/agent/__init__.py` 使用 `__getattr__` 延迟导入避免循环依赖
3. **单例模式**: `LLMClientManager` 和 `Config` 使用单例确保全局一致性
4. **结构化输出**: `ExecutionAgent` 使用 Pydantic 模型 (`ExecutionPlan`) 确保决策格式正确
5. **防御性编程**: 风险管理模块默认值初始化防止空值异常
6. **功能开关模式**: 所有增强功能通过 `config.yaml` 独立开关控制，默认关闭
7. **原子写入**: `GridManager` 的状态文件使用 tempfile + move 实现原子写入，防止进程中断导致文件损坏

## 测试

测试位于 `tests/` 目录，使用 pytest：

```bash
# 运行所有测试
uv run pytest tests/

# 运行特定测试
uv run pytest tests/test_decision_validator.py -v

# 带覆盖率
uv run pytest tests/ --cov=src
```

主要测试文件：
- `test_agents_langgraph.py`: Agent 架构测试
- `test_decision_validator.py`: 决策验证器测试
- `test_debate.py`: 多空辩论引擎测试
- `test_signal_scorer_regime.py`: 信号评分 Regime 自适应测试
- `test_enhanced_engine_regime.py`: 增强引擎 Regime 集成测试
- `test_regime_adapter.py`: Regime 适配器测试
- `test_data_enricher_extended.py`: 数据增强扩展测试
- `test_review_memory_vft.py`: 复盘记忆测试
- `test_external_info_agent.py`: 外部信息收集测试
- `test_review_daily_logger.py`: 复盘日志测试
- `test_market_monitor.py`: 市场主动监控测试
- `test_instant_reflection.py`: 即时反思测试（改进6a）
- `test_weekly_reflection.py`: 每周反思测试（改进6a）
- `test_regime_aware_memory.py`: Regime 感知记忆测试（改进6b）
- `test_confirmation_bias_protection.py`: 确认偏差防护测试（改进6c）
- `test_fact_subjective_split.py`: 事实-主观分离测试（改进6d）
- `test_prompt_meta_reflection.py`: Prompt 自优化测试（改进6e）
- `test_grid_manager_exit_orders.py`: 网格交易分层减仓单测试
- `test_account_protection_integration.py`: 保护系统集成测试
- `test_protection_base.py`: 保护插件基础架构测试
- `test_protection_drawdown.py`: 最大回撤保护插件测试
- `test_protection_daily_loss.py`: 单日亏损保护插件测试
- `test_protection_consecutive_loss.py`: 连续亏损保护插件测试（含 per-symbol）
- `test_protection_position_timeout.py`: 持仓超时保护插件测试
- `test_protection_manager.py`: 保护插件管理器测试
- `test_protection_migration.py`: 保护配置迁移测试

## 注意事项

### 开发规范
- 代码和注释主要使用中文
- 新增代码需要删除未使用的导入和变量
- 异常处理的 except 块需要命名异常变量（如 `except Exception as e`）
- 安全机制需要包含重试逻辑
- 所有新功能必须通过 config 开关控制，默认关闭

### Hyperliquid 特性
- 使用简单符号格式（`BTC`, `ETH`），不是交易对格式（不是 `BTC/USDT`）
- 测试网和主网通过 `HYPERLIQUID_TESTNET` 环境变量切换
- 手续费率从 API 动态获取，失败时回退到默认 Tier0

### Git 注意事项
- `.gitignore` 中 `/data/` 只忽略根目录的 data 文件夹，不影响 `src/data/`
- 敏感配置（`.env`, `config.yaml`）不应提交
