# Quant Flow

AI 驱动的加密货币自动交易系统，专为 [Hyperliquid](https://hyperliquid.xyz) DEX 设计。

LLM 负责判断，代码负责执行：所有策略都遵循同一条铁律——**AI 只产出结构化 JSON 决策，
下单、止盈止损与风控永远由确定性代码完成**，LLM 故障绝不放大成交易动作。

## 两种策略

| 策略 | 说明 | 开关 |
|------|------|------|
| **永续合约** | 每根 K 线收盘后，LLM 按技术指标与多周期趋势输出 BUY/SELL_SHORT/CLOSE/HOLD 决策，经边界校验后执行，自动挂止盈止损单 | `trading.perp_enabled` |
| **网格做市** | LLM 判断方向与宽度，数学引擎计算布单参数，GridManager 管理层级生命周期（含 Triple Barrier、趋势过滤、库存上限等安全机制） | `trading.grid_enabled` |

两种策略共用一个入口 `main.py`，可独立或并行运行。

## 快速开始

```bash
# 1. 安装依赖（uv 管理）
uv sync

# 2. 配置密钥与策略
cp .env.example .env              # 填入私钥与 LLM API Key
cp config.yaml.example config.yaml  # 按需调整策略参数（所有键都有默认值）

# 3. 运行
uv run python main.py
```

默认连接 **测试网**（`HYPERLIQUID_TESTNET=true`），主网需显式设为 `false`。

### Docker 部署

```bash
docker compose up -d
docker compose logs -f
```

## 系统结构

```
市场数据 ─→ 策略层（LLM JSON 决策 + 校验）─→ 交易层（下单/止盈止损/回滚）
                    │                              │
              账户保护链（回撤/日亏/连亏/超时熔断）──┘
```

```
main.py              # 入口：加载配置 → 启动引擎
src/
├── engine.py        # 调度引擎：K 线节拍主循环 + 网格周期线程 + 风控接线
├── config.py        # 配置（全默认值，能省则省）
├── llm.py           # OpenAI 兼容 LLM 客户端（重试 + JSON 提取）
├── strategy/        # 策略层：perp（永续）/ grid + grid_agent（网格）
├── trading/         # 交易层：client / order_manager / grid_manager / barrier / pnl
├── data/            # 行情与技术指标
├── plugins/protections/  # 账户保护插件链（可插拔）
└── utils/           # 日志、K 线对齐、精度、网格数学
prompts/             # 永续策略 Prompt（系统提示 + 决策模板）
docs/                # 架构与配置文档
```

详见 [docs/architecture.md](docs/architecture.md) 与 [docs/configuration.md](docs/configuration.md)。

## 关键安全机制

- **止损单失败自动回滚**：开仓后止损单挂不上 → 立即平仓（带重试），绝不留裸仓
- **Triple Barrier**：网格级全局兜底（止损/止盈/时限/追踪止损），每周期无条件检查
- **账户保护链**：最大回撤全平、单日亏损暂停、连亏锁定交易对、持仓超时强平
- **LLM 故障降级**：调用失败/输出非法一律回退保守动作（HOLD / KEEP_GRID）并计数告警，
  网格空转达阈值后用纯市场数据兜底重建
- **强制中性网格**：默认忽略 AI 方向判断，从源头消除方向反手（whipsaw）亏损

## 测试

```bash
uv run pytest tests/          # 全部测试（无需网络与密钥）
uv run ruff check src/ tests/ # 静态检查
```

## 风险提示

本项目仅供学习研究。加密货币合约交易风险极高，可能损失全部本金；
请先在测试网充分验证，并只投入可承受损失的资金。
