# Quant Flow

基于 LangChain/LangGraph 的 AI 永续合约交易机器人，支持 Hyperliquid DEX。

## 功能

- 多 Agent 架构，每个交易对独立决策
- 支持多种 LLM 供应商（OpenAI、Cloudflare、Google、LiteLLM、NVIDIA）
- 凯利公式动态仓位管理
- ATR 动态止盈止损
- 账户保护（最大回撤限制、持仓超时）
- 决策验证（多周期趋势共振、信号质量评估）
- 上下文压缩，降低 Token 成本

## 快速开始

### Docker 部署

```bash
# 配置
cp .env.example .env
cp config.yaml.example config.yaml
# 编辑 .env 填入 API 密钥和私钥

# 创建日志目录
mkdir -p logs/decisions logs/trades logs/review_daily
chmod -R 777 logs/

# 启动
docker-compose up -d

# 查看日志
docker-compose logs -f
```

### 本地部署

```bash
# 安装（需要 Python 3.11+）
pip install -e .

# 配置
cp .env.example .env
cp config.yaml.example config.yaml

# 运行
python main.py
```

## 配置

### 环境变量 (.env)

```bash
# LLM API
OPENAI_API_BASE=https://api.deepseek.com/v1
OPENAI_API_KEY=your_key
OPENAI_MODEL=deepseek-chat

# Hyperliquid
HYPERLIQUID_PRIVATE_KEY=0x...
HYPERLIQUID_TESTNET=true   # true=测试网，false=主网
```

**钱包模式说明：**

- 单钱包模式：只填 `HYPERLIQUID_PRIVATE_KEY`，直接用该钱包交易
- API 钱包代理模式：额外填 `HYPERLIQUID_ACCOUNT_ADDRESS`（主钱包地址），需在网页端授权

### 交易配置 (config.yaml)

```yaml
llm:
  client_type: openai   # openai/cloudflare/google/litellm/nvidia

trading:
  symbols: [BTC, ETH]   # 交易对，使用简单符号
  max_trade_amount: 100 # 单笔上限（美元）
  max_leverage: 10
  take_profit_ratio: 0.05
  stop_loss_ratio: 0.02

scheduler:
  interval_minutes: 3   # 决策间隔

# 增强分析
enhanced_analysis:
  enabled: true
  signal_threshold: 0.6
  require_trend_alignment: true

# 账户保护
account_protection:
  max_drawdown_pct: 10.0   # 最大回撤 10%
  max_position_hours: 24   # 最大持仓时间
  daily_loss_limit: 5.0    # 单日亏损上限 5%
```

完整配置参考 `config.yaml.example`。

## 项目结构

```
quant-flow/
├── main.py                 # 入口
├── src/
│   ├── agent/              # Agent 实现
│   ├── agents/             # LangGraph Agent
│   ├── trading/            # 交易模块
│   │   ├── client.py           # Hyperliquid 客户端
│   │   ├── order_manager.py    # 订单管理
│   │   ├── decision_validator.py   # 决策验证
│   │   ├── position_sizer.py       # 仓位计算
│   │   ├── risk_manager.py         # 风险管理
│   │   └── account_protector.py    # 账户保护
│   ├── data/               # 数据处理
│   │   ├── market_data.py      # K线数据
│   │   ├── indicators.py       # 技术指标
│   │   ├── market_state.py     # 市场状态
│   │   └── signal_scorer.py    # 信号评分
│   └── llm/                # LLM 客户端
├── prompts/                # Prompt 模板
├── tests/                  # 测试
└── logs/                   # 日志
```

## Docker 管理

```bash
docker-compose up -d      # 启动
docker-compose down       # 停止
docker-compose restart    # 重启
docker-compose logs -f    # 日志
docker-compose ps         # 状态

# 更新
git pull
docker-compose build
docker-compose up -d
```

## 常见问题

**权限错误**

```
PermissionError: [Errno 13] Permission denied: '/app/logs/...'
```

解决：`chmod -R 777 logs/`

**OI 上限错误**

```
Cannot increase position when open interest is at cap
```

该资产达到开放利益上限，换其他交易对。

**杠杆超限**

```
Leverage exceeds maximum allowed
```

不同资产杠杆上限不同（如 ETH 最大 25x），降低 `max_leverage`。

**API 钱包无法交易**

能查余额但不能下单，需要在主钱包网页端授权 API 钱包地址。

## 测试

```bash
# 运行所有测试
pytest tests/

# 单个测试
pytest tests/test_decision_validator.py -v
```

获取测试资金：[Hyperliquid 测试网水龙头](https://app.hyperliquid-testnet.xyz/faucet)

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
