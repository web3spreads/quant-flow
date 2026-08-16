# Quant Flow

AI 驱动的 [Hyperliquid](https://hyperliquid.xyz) 自动交易系统。 **LLM 只产出结构化
JSON 决策，下单、止盈止损与风控永远由确定性代码完成**，LLM 故障绝不放大成交易动作。

两种策略共用入口 `main.py`，可独立或并行（开关 `trading.perp_enabled` / `grid_enabled`）：

- **永续**：K 线收盘后 LLM 输出 BUY / SELL_SHORT / CLOSE / HOLD，校验后执行并自动挂止盈止损
- **网格**：LLM 判方向与宽度，数学引擎算参数，GridManager 管理层级生命周期

## 快速开始

```bash
uv sync
cp .env.example .env                 # 私钥与 LLM API Key
cp config.yaml.example config.yaml   # 所有键都有默认值
uv run python main.py                # 默认测试网（HYPERLIQUID_TESTNET=true）
```

Docker：`docker compose up -d`；测试：`uv run pytest tests/ && uv run ruff check src/ tests/`

详见 [docs/architecture.md](docs/architecture.md) 与 [docs/configuration.md](docs/configuration.md)。

## 风险提示

仅供学习研究。合约交易可能损失全部本金。
