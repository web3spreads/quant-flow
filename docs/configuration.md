# 配置说明

敏感信息在 `.env`，策略参数在 `config.yaml`。所有 YAML 键都有内置默认值
（权威清单见 `src/config.py`），最小可运行配置只需 `llm` 段与 `trading.symbols`。

## .env

- `HYPERLIQUID_PRIVATE_KEY`（必填）：交易钱包或 API 钱包私钥
- `HYPERLIQUID_ACCOUNT_ADDRESS`：仅 API 钱包模式需要（主钱包地址，须网页端授权）
- `HYPERLIQUID_TESTNET`：默认 `true`；主网须显式设 `false`
- `LLM_API_KEY`（必填，兼容旧名 `OPENAI_API_KEY`）；`LOG_LEVEL` 默认 `INFO`

## config.yaml 要点

`llm` 段接任意 OpenAI 兼容端点（`base_url` / `model` / `temperature` / `timeout`）。
常用旋钮（其余键与默认值见 `src/config.py`）：

- **trading**：`symbols`（简单符号格式，网格用第一个）、`perp_enabled` / `grid_enabled`、
  `max_trade_amount`、`max_leverage`、`take_profit_ratio` / `stop_loss_ratio`、
  `min_confidence`、`timeframe`
- **grid**：安全机制全部默认启用。`interval_minutes`、`width_min_pct` /
  `width_max_pct`（网格宽度上下限）、`force_neutral`（默认 true，消除方向反手
  亏损）、`max_position_notional_usd`（0=关）、`halt_below_usd`（0=关）、
  `trend_filter_enabled`、`rebuild_cooldown_seconds`（全量重建冷却，默认 3600s；
  价格突破旧区间 0.5% 时自动提前解除，该比例固定不可配）、`rebuild_min_change_pct`
  （区间变化低于此比例不重建，默认 0.01）、`barrier` 覆盖项（默认：止损 -5% /
  止盈 +10% / 时限 4h / 追踪止损 3% 激活 1% 回撤）
- **protections**：省略用默认链，`protections: []` 全关（不建议）。可配
  `max_drawdown`（回撤全平）/ `daily_loss`（暂停开仓）/ `consecutive_loss`
  （连亏锁定，`per_symbol`、`forced_close_no_reset`）/ `position_timeout`（超时强平）

## 旧版配置迁移（PR #92 之前的部署）

旧 schema 的未知键会被忽略并在启动日志告警（`[配置迁移]` 前缀）——**按告警逐条迁移，
重点核对库存上限、停机线等安全阀是否仍生效**。主要变化：`run_mode` → 两个布尔开关；
`trading.grid_*` 扁平键 → `grid:` 段（去 `grid_` 前缀）；`risk_management` →
`grid.barrier` + `protections`；钉钉通知与云日志已移除，告警只落 `logs/main.log`。

## Docker

- 挂载卷属主须 UID 1000：`sudo chown -R 1000:1000 ./logs ./data`
- 永续与网格并行时，网格独占 `symbols[0]`，永续自动跳过该交易对
  （单向持仓下同交易对会互相强平）
