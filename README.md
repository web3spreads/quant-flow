# Quant Flow

基于 LangChain/LangGraph 的 AI 永续合约交易机器人，支持 Hyperliquid DEX。

## 功能

### 核心能力
- 多 Agent 架构，每个交易对独立决策
- 支持多种 LLM 供应商（OpenAI、Cloudflare、Google、LiteLLM、NVIDIA）
- 凯利公式动态仓位管理
- ATR 动态止盈止损
- 账户保护（最大回撤限制、持仓超时）
- 决策验证（多周期趋势共振、信号质量评估）
- 上下文压缩，降低 Token 成本

### LLM 决策增强（基于论文研究）

| 功能 | 论文依据 | 配置开关 | 说明 |
|------|----------|----------|------|
| FinCoT 结构化推理 | [arXiv:2506.16123](https://arxiv.org/abs/2506.16123) | `prompt.set: nof1-improved` | 6步强制推理链，准确率 +17%，token 消耗 -8.9x |
| 多空辩论 Agent | [arXiv:2412.20138](https://arxiv.org/abs/2412.20138) | `debate.enabled` | Bull/Bear 双 Agent 消除确认偏见 |
| CEX 领先信号 + 链上数据 | [MDPI 2026](https://www.mdpi.com/2227-7390/14/2/346) | `enhanced_analysis.enabled` | Binance 费率对比、MVRV/SOPR 方向信号 |
| Regime 自适应策略 | [Springer 2025](https://link.springer.com/article/10.1007/s42521-024-00123-2) | `regime_adaptive.enabled` | 趋势/震荡/高波动三种市场状态动态调参 |
| 市场主动监控 | — | `market_monitor.enabled` | 异常波动自动触发决策循环，无需等待定时周期 |

所有增强功能**通过配置独立开关控制，默认不影响现有流程**。

## 快速开始

### Docker 部署

```bash
# 初始化（自动创建配置文件和目录，配置 UID/GID）
bash init-deployment.sh

# 编辑配置
vim .env           # 填入 API 密钥和私钥
vim config.yaml    # 配置交易参数

# 启动
docker compose up -d

# 查看日志
docker compose logs -f
```

### 本地部署

```bash
# 安装 uv（如尚未安装）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 安装依赖（需要 Python 3.11+）
uv sync

# 配置
cp .env.example .env
cp config.yaml.example config.yaml
# 编辑 .env 和 config.yaml

# 运行
uv run python main.py
```

## 配置

### 环境变量 (.env)

```bash
# LLM API（根据 config.yaml 中的 client_type 选择配置）
NVIDIA_API_KEY=xxx              # NVIDIA AI Endpoints
OPENAI_API_BASE=xxx             # OpenAI 兼容 API
OPENAI_API_KEY=xxx

# Hyperliquid
HYPERLIQUID_PRIVATE_KEY=0x...   # 钱包私钥
HYPERLIQUID_TESTNET=true        # true=测试网，false=主网
```

**钱包模式说明：**

- 单钱包模式：只填 `HYPERLIQUID_PRIVATE_KEY`，直接用该钱包交易
- API 钱包代理模式：额外填 `HYPERLIQUID_ACCOUNT_ADDRESS`（主钱包地址），需在网页端授权

### 交易配置 (config.yaml)

```yaml
llm:
  client_type: langchain_nvidia   # openai/cloudflare/google/litellm/nvidia
  model: deepseek-ai/deepseek-v3.2
  temperature: 0.2                # 交易决策建议低温度

trading:
  symbols: [BTC, ETH]            # 交易对，使用简单符号
  max_trade_amount: 100           # 单笔上限（美元）
  max_leverage: 10

scheduler:
  interval_minutes: 3             # 决策间隔

prompt:
  set: nof1-improved              # 推荐使用集成 FinCoT 的增强 Prompt

# 增强分析（启用后自动采集 CEX/链上/恐惧贪婪数据）
enhanced_analysis:
  enabled: true
  min_confidence: 0.4

# 多空辩论（每次决策额外 2 次 LLM 调用）
debate:
  enabled: false

# Regime 自适应策略（根据市场状态动态调整参数）
regime_adaptive:
  enabled: false
  # 可选参数覆盖：
  # trending:
  #   signal_threshold: 0.5
  #   max_leverage: 10
  # ranging:
  #   signal_threshold: 0.75
  #   max_leverage: 5

# 账户保护
account_protection:
  enabled: true
  max_drawdown_pct: 0.10          # 最大回撤 10%
  max_daily_loss_pct: 0.05        # 单日亏损 5%
  max_position_hours: 48

# 市场主动监控（异常波动触发决策循环）
market_monitor:
  enabled: false
  check_interval_seconds: 30      # 检查间隔
  alert_threshold_pct: 3.0        # HIGH 告警阈值（%）
  cooldown_minutes: 5             # 冷却时间（分钟）
  reference_window_minutes: 10    # 价格基准窗口（分钟）
```

完整配置参考 `config.yaml.example`。

## 增强功能使用指南

### FinCoT 结构化推理

FinCoT 通过 Prompt 集实现，无需额外配置。切换到带 FinCoT 的 Prompt：

```yaml
prompt:
  set: nof1-improved   # 推荐：完整 FinCoT + 增强数据集成
  # 其他可选：default, aggressive, conservative, realtime
```

所有 8 套 Prompt 模板均已集成 FinCoT 6 步推理链，不同策略的区别在于阈值和风格：
- `conservative`: 趋势分歧即 HOLD，盈亏比 ≥ 2.0
- `aggressive`: 3 条件即可入场，盈亏比 ≥ 1.2
- `realtime`: 价格行为优先于滞后指标

### 多空辩论

启用后，每次交易决策前会由两个独立 Agent 分别从看多/看空角度分析，辩论结果注入主决策 Prompt：

```yaml
debate:
  enabled: true
```

注意事项：
- 每次决策增加 2 次 LLM 调用（延迟 +2-4s）
- 辩论结果通过 `{{ debate_summary }}` 变量注入所有 Prompt 模板
- 可随时关闭，不影响其他功能

### CEX 领先信号 + 链上数据

启用 `enhanced_analysis` 后自动采集，无需单独配置。采集的数据源：

| 数据 | 来源 | 频率 | 降级策略 |
|------|------|------|----------|
| CEX 资金费率 | Binance 公开 API | 每次决策 | 返回默认值 |
| 恐惧贪婪指数 | alternative.me | 每次决策 | 返回中性 |
| 链上 MVRV/SOPR | blockchain.info | 每次决策 | 返回中性 |

数据通过 Prompt 变量 `{{ cex_funding_signal }}`、`{{ onchain_summary }}` 注入 LLM 决策。

### Regime 自适应策略

启用后根据 `market_state.py` 分析的市场状态自动切换参数矩阵：

```yaml
regime_adaptive:
  enabled: true
```

三种 Regime 的默认参数：

| Regime | 信号阈值 | 最低置信度 | 最大杠杆 | 仓位比例 |
|--------|----------|-----------|----------|----------|
| 趋势市 (trending) | 0.5 | 0.35 | 10x | 80% |
| 震荡市 (ranging) | 0.75 | 0.55 | 5x | 40% |
| 高波动 (volatile) | 0.85 | 0.65 | 3x | 30% |

依赖关系：`regime_adaptive` 依赖 `enhanced_analysis.enabled: true`。

### 市场主动监控

启用后，独立线程在决策周期间隔内持续监控价格波动，检测到异常波动时主动触发决策循环：

```yaml
market_monitor:
  enabled: true
  check_interval_seconds: 30       # 每 30 秒检查一次价格
  alert_threshold_pct: 3.0         # 波动 ≥3% 触发决策
  cooldown_minutes: 5              # 触发后 5 分钟内不重复触发
```

工作流程：
1. 监控线程每 30 秒通过 `all_mids()` 获取最新价格
2. 与参考窗口（默认 10 分钟）内的基准价格对比
3. 波动超过阈值时生成 `VolatilityAlert`，触发 `trading_cycle`
4. 告警上下文通过 `{{ volatility_alert }}` 注入 LLM Prompt，辅助决策

注意事项：
- 独立于其他增强功能，不依赖 `enhanced_analysis`
- 冷却期按交易对独立管理，BTC 触发不影响 ETH
- 预热期（窗口内无基准数据）不会误触发

### A/B 回测对比

使用内置的回测对比工具验证各功能效果：

```bash
# 对比所有功能组合
uv run python backtest_comparison.py --symbol BTC --compare all \
  --start-date 2025-01-01 --end-date 2025-06-01

# 单独对比特定功能
uv run python backtest_comparison.py --symbol BTC --compare fincot   # FinCoT
uv run python backtest_comparison.py --symbol BTC --compare debate   # 多空辩论
uv run python backtest_comparison.py --symbol BTC --compare onchain  # 链上数据
uv run python backtest_comparison.py --symbol BTC --compare regime   # Regime 自适应
```

## 项目结构

```
quant-flow/
├── main.py                        # 入口
├── backtest_comparison.py         # A/B 回测对比工具
├── src/
│   ├── agent/                     # Agent 实现
│   │   ├── enhanced_single_symbol_agent.py  # 增强版 Agent（主决策）
│   │   └── debate.py              # 多空辩论引擎
│   ├── agents/                    # LangGraph Agent
│   ├── trading/                   # 交易模块
│   │   ├── client.py              # Hyperliquid 客户端
│   │   ├── enhanced_engine.py     # 增强交易引擎
│   │   ├── decision_validator.py  # 决策验证
│   │   ├── position_sizer.py      # 仓位计算
│   │   ├── risk_manager.py        # 风险管理
│   │   └── account_protector.py   # 账户保护
│   ├── data/                      # 数据处理
│   │   ├── data_enricher.py       # 数据增强（CEX/链上/恐惧贪婪）
│   │   ├── market_monitor.py      # 市场主动监控（异常波动触发决策）
│   │   ├── signal_scorer.py       # 多因子信号评分
│   │   ├── regime_adapter.py      # Regime 自适应
│   │   ├── market_state.py        # 市场状态分析
│   │   └── market_data.py         # K线数据
│   └── llm/                       # LLM 客户端
├── prompts/                       # Prompt 模板（8 套策略）
├── tests/                         # 测试（257 个用例）
└── logs/                          # 日志
```

## Docker 管理

```bash
docker compose up -d      # 启动
docker compose down       # 停止
docker compose restart    # 重启
docker compose logs -f    # 日志
docker compose ps         # 状态

# 更新
git pull
docker compose build
docker compose up -d
```

**运行模式** (通过 `RUN_MODE` 环境变量控制)：
- `main` — 仅主交易（默认）
- `grid` — 仅网格交易
- `all` — 同时运行

## 测试

```bash
# 运行所有测试
uv run pytest tests/

# 单个测试
uv run pytest tests/test_decision_validator.py -v

# 带覆盖率
uv run pytest tests/ --cov=src
```

获取测试资金：[Hyperliquid 测试网水龙头](https://app.hyperliquid-testnet.xyz/faucet)

## 常见问题

**权限错误**: `PermissionError: Permission denied: '/app/logs/...'`
→ 运行 `bash init-deployment.sh` 自动配置 UID/GID

**OI 上限错误**: `Cannot increase position when open interest is at cap`
→ 该资产达到开放利益上限，换其他交易对

**杠杆超限**: `Leverage exceeds maximum allowed`
→ 不同资产杠杆上限不同（如 ETH 最大 25x），降低 `max_leverage`

**API 钱包无法交易**
→ 能查余额但不能下单，需要在主钱包网页端授权 API 钱包地址

## 风险提示

- 先在测试网验证策略
- 主网从小金额开始
- 杠杆交易有爆仓风险
- 私钥泄露等于资产丢失
- 定期检查运行状态

## 相关链接

- [Hyperliquid](https://hyperliquid.xyz/)
- [测试网水龙头](https://app.hyperliquid-testnet.xyz/faucet)
- [LangChain](https://python.langchain.com/)

---

免责声明：本项目仅供学习研究，生产环境使用需自行承担风险。
