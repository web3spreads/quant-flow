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

Quant Flow 是一个基于 LangChain/LangGraph 的 AI 永续合约自动交易机器人，专为 Hyperliquid DEX 设计。采用多 Agent 架构，每个交易对独立决策，支持上下文压缩以降低 Token 成本。

**技术栈**: Python 3.11+, LangChain, LangGraph, Hyperliquid SDK, Pydantic

## 常用命令

```bash
# 安装依赖
pip install -e .

# 运行主程序
python main.py

# 指定配置文件运行
python main.py --config config.yaml --env .env

# 运行测试
pytest tests/
pytest tests/test_agents_langgraph.py -v  # 单个文件

# 语法检查
python -m py_compile src/trading/client.py

# Docker 部署
docker-compose up -d
docker-compose logs -f
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
                          │  AccountProtector   │           │  OrderManager  │
                          │  (回撤保护/超时清仓) │           │  (订单执行)     │
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
                          ┌──────────────────────────┼──────────────────────────┐
                          ↓                          ↓                          ↓
                  ┌──────────────┐          ┌───────────────┐          ┌──────────────┐
                  │SummaryAgentV2│          │  ReviewAgent  │          │  SpotAgent   │
                  │(历史压缩)     │          │ (复盘学习)    │          │ (现货定投)   │
                  └──────────────┘          └───────────────┘          └──────────────┘
```

### 目录结构

```
src/
├── agent/                    # Agent 实现 (旧版)
│   ├── single_symbol_agent.py      # 单币种交易决策
│   ├── enhanced_single_symbol_agent.py  # 增强版 Agent (集成风控)
│   ├── summary_agent_v2.py         # 历史压缩
│   ├── spot_agent.py               # 现货定投
│   ├── review_agent.py             # 复盘学习
│   └── execution_agent.py          # 执行计划生成
├── agents/                   # Agent 实现 (新版 LangGraph)
│   ├── trading/                    # 交易 Agent workflow
│   ├── execution/                  # 执行 Agent workflow
│   ├── review/                     # 复盘 Agent workflow
│   └── common/                     # 共享组件 (state, tools, utils)
├── trading/                  # 交易核心模块
│   ├── client.py                   # Hyperliquid SDK 封装
│   ├── order_manager.py            # 订单管理
│   ├── decision_validator.py       # 决策多维度验证 ⭐
│   ├── position_sizer.py           # 凯利公式仓位计算 ⭐
│   ├── risk_manager.py             # ATR动态止盈止损 ⭐
│   ├── account_protector.py        # 账户保护 (回撤/超时) ⭐
│   └── enhanced_engine.py          # 增强交易引擎 ⭐
├── data/                     # 数据模块
│   ├── market_data.py              # K线和市场数据
│   ├── indicators.py               # 技术指标 (MA, RSI, MACD, Bollinger)
│   ├── data_enricher.py            # 数据增强
│   ├── market_state.py             # 市场状态分析 ⭐
│   └── signal_scorer.py            # 信号质量评分 ⭐
├── llm/                      # LLM 客户端
│   └── llm_client.py               # 多供应商支持 (OpenAI/Cloudflare/Google/LiteLLM/NVIDIA)
├── backtest/                 # 回测模块
└── notification/             # 通知模块
```

**⭐ 标记的是最近新增的风险管理模块**

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

### 账户保护 (`account_protector.py`)

实现最大回撤保护和持仓超时机制：

```python
# 保护动作
ProtectionAction.PAUSE_NEW_TRADES        # 暂停新开仓
ProtectionAction.CLOSE_LOSING_POSITIONS  # 关闭亏损仓位
ProtectionAction.CLOSE_ALL_POSITIONS     # 全部平仓
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

项目支持多套 Prompt 策略，位于 `prompts/` 目录：

| 策略 | 说明 |
|------|------|
| `default` | 默认策略 |
| `conservative` | 保守策略 |
| `aggressive` | 激进策略 |
| `nof1` / `nof1-improved` | 增强版策略 |
| `realtime` / `realtime-eng` | 实时策略 |

通过 `config.yaml` 中的 `prompt.set` 切换策略。`PromptManager` 负责加载和渲染 Jinja2 模板。

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
  client_type: openai           # openai/cloudflare/google/litellm/nvidia

trading:
  symbols: [BTC, ETH, SOL]      # 交易对（简单符号，非交易对格式）
  max_trade_amount: 100         # 单笔上限（美元）
  max_leverage: 10              # 最大杠杆

# 增强分析配置 (新增)
enhanced_analysis:
  enabled: true
  signal_threshold: 0.6         # 信号阈值
  require_trend_alignment: true # 要求趋势共振

# 账户保护配置 (新增)
account_protection:
  max_drawdown_pct: 10.0        # 最大回撤 10%
  max_position_hours: 24        # 最大持仓时间
  daily_loss_limit: 5.0         # 单日亏损上限 5%
```

## 设计模式

1. **工具回调模式**: `TradingTools` 通过回调函数将 LLM 决策映射到实际交易操作
2. **延迟导入**: `src/agent/__init__.py` 使用 `__getattr__` 延迟导入避免循环依赖
3. **单例模式**: `LLMClientManager` 和 `Config` 使用单例确保全局一致性
4. **结构化输出**: `ExecutionAgent` 使用 Pydantic 模型 (`ExecutionPlan`) 确保决策格式正确
5. **防御性编程**: 风险管理模块默认值初始化防止空值异常

## 测试

测试位于 `tests/` 目录，使用 pytest：

```bash
# 运行所有测试
pytest tests/

# 运行特定测试
pytest tests/test_decision_validator.py -v

# 带覆盖率
pytest tests/ --cov=src
```

主要测试文件：
- `test_agents_langgraph.py`: Agent 架构测试
- `test_decision_validator.py`: 决策验证器测试
- `test_external_info_agent.py`: 外部信息收集测试
- `test_review_daily_logger.py`: 复盘日志测试

## 注意事项

### 开发规范
- 代码和注释主要使用中文
- 新增代码需要删除未使用的导入和变量
- 异常处理的 except 块需要命名异常变量（如 `except Exception as e`）
- 安全机制需要包含重试逻辑

### Hyperliquid 特性
- 使用简单符号格式（`BTC`, `ETH`），不是交易对格式（不是 `BTC/USDT`）
- 测试网和主网通过 `HYPERLIQUID_TESTNET` 环境变量切换
- 手续费率从 API 动态获取，失败时回退到默认 Tier0

### Git 注意事项
- `.gitignore` 中 `/data/` 只忽略根目录的 data 文件夹，不影响 `src/data/`
- 敏感配置（`.env`, `config.yaml`）不应提交
