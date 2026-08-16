# CLAUDE.md

## 默认语言为中文

文档、代码注释、commit、PR/Issue、日志输出与 AI 交互使用中文。
例外：标识符与技术术语；commit message

## 项目

Quant Flow：AI 驱动的 Hyperliquid 自动交易系统。铁律：**LLM 只产出结构化 JSON 决策，
下单、止盈止损与风控永远由确定性代码完成。** 永续（`src/strategy/perp.py`）与网格
（`src/strategy/grid.py`）两策略由 `config.yaml` 开关控制，共用 `main.py` 入口。
架构详见 `docs/architecture.md`。

## 命令

```bash
uv run python main.py --config config.yaml --env-file .env
uv run pytest tests/ && uv run ruff check src/ tests/   # 改动后必跑
tail -f logs/main.log                                    # trades/equity 同目录 jsonl
```

## 设计原则

1. LLM 故障（解析失败、非法 action、调用异常）一律降级 HOLD / KEEP_GRID，绝不透传执行层；
   兜底决策必须打 `llm_ok=False` 标记
2. 所有配置键有内置默认值（`src/config.py`），安全机制默认启用；
   新功能不得要求用户新增配置才获得安全行为
3. 账户级保护实现 `IProtection` 并注册 `PROTECTION_REGISTRY`；风控强平不上报虚假 pnl
4. 状态文件原子写入（tempfile + move）；核心计算用 Decimal，仅 API 边界转 float
5. 资金安全机制必须带重试且失败路径有日志（如止损单失败 → 立即回滚平仓）

## 规范与注意

- 测试禁网络与真实密钥：LLM 用 `conftest.FakeLLM`，订单层用 `FakeOrderManager`
- Hyperliquid：简单符号格式（`BTC`，非 `BTC/USDT`）；持仓 `szi` 为带符号字符串；
  `HYPERLIQUID_TESTNET` 切网（默认测试网）；手续费 API 动态获取、失败回退 Tier0
- `.env`、`config.yaml` 已在 `.gitignore`，不得提交；`logs/`、`data/` 不入库
