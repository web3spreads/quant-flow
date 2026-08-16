# 架构说明

核心原则：**LLM 只产出 JSON 决策，执行永远在确定性代码里。**
LLM 故障一律降级保守动作（HOLD / KEEP_GRID）并打 `llm_ok=False`；
金额与杠杆按上限截断，置信度不足不开仓，重复开仓/无仓平仓执行前拦截。

## 分层

```
Engine（engine.py）        K 线节拍主循环 + 网格周期线程，共享一把交易锁
  → Strategy（strategy/）  perp / grid / grid_agent：决策、校验、自愈
  → Trading（trading/）    order_manager · client【止损失败自动回滚】· grid_manager
  → Protections（plugins/protections/）账户保护链
```

## 永续周期

K 线收盘后触发：查余额持仓 → 保护链检查 → 拉行情算指标 → LLM 输出
`{"action","confidence","amount_usd","leverage","reason"}` → 校验执行。
止盈止损不由 LLM 定价：按 `take_profit_ratio` / `stop_loss_ratio` 自动挂 trigger 单，
止损挂失败立即回滚平仓（带重试），绝不留裸仓。

## 网格周期（检查顺序有真实事故背书，勿调换）

1. **净额对冲归因**：Hyperliquid 单向持仓下平仓多走净额对冲、不经层级状态机，
   须先以链上成交补记盈亏，本轮风控才看得见
2. 账户级熔断 → 3. 净值停机线（短路整轮）→ 4. **Triple Barrier**
   （止损/止盈/时限/追踪止损，KEEP_GRID/ERROR 周期也照查）→
5. 趋势过滤（迟滞确认去抖，「暂停加仓」先行「平逆势库存」靠后）→
6. AI 决策 → LLM 健康跟踪 → 空转自愈 → `GridManager.sync_grid` 布单

GridManager 另有：孤儿单清理、reduce-only 分层减仓、撤单硬超时、状态原子写入、
库存名义额硬上限、强制中性模式（默认开）。

## 保护插件链

`IProtection` + `PROTECTION_REGISTRY` 注册即插：`max_drawdown`（全平）、
`daily_loss`（暂停开仓）、`consecutive_loss`（锁定交易对）、`position_timeout`（强平）。
账户级按周期 `check_all`，逐笔级走 `on_trade_open/close`；风控强平不上报虚假 pnl。

## 可观测性

`logs/main.log`（人读）；`logs/decisions|trades|equity/*.jsonl`
（决策审计 / 成交 pnl 归因 / 净值曲线）。
