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

## 网格周期（顺序有真实事故背书，勿调换）

1. **净额对冲归因**：Hyperliquid 单向持仓下平仓多走净额对冲、不经层级状态机，
   须先以链上成交补记盈亏，本轮风控才看得见
2. **强平重试**：上一轮紧急平仓/熔断平仓失败的仓位绝不脱管，先于熔断判定重试
3. 账户级熔断：`CLOSE_ALL` 平仓撤单后结束本轮；`PAUSE` 只记标记**不 return**
   ——暂停的是新开仓，持仓的风控维护照常，否则暂停期内亏损不封底
4. 净值快照 + 停机线（只在无持仓时短路整轮，先于屏障无风险）
5. **Triple Barrier**（止损/止盈/时限/追踪止损）：独立于 AI action 分支与暂停
   状态，KEEP_GRID/ERROR 周期也照查
6. 暂停期分支：不调 LLM、不布新单，但 `maintain_protective_orders` 照常
7. 行情与指标
8. 趋势过滤（迟滞确认去抖，「暂停加仓」先行「平逆势库存」靠后）→ AI 决策
   （GridAgent 强制中性模式默认开，忽略 AI 方向以消除反手亏损）→ LLM 健康
   跟踪 → 空转自愈
9. **连亏 per-symbol 锁定**：锁定期内 `UPDATE_GRID` 降级为 `KEEP_GRID`，
   只锁重建/扩建，保护单维护不受影响
10. 记录决策 → `GridManager.sync_grid` 布单

### sync_grid 内部（顺序同样有事故背书）

孤儿 trigger 单清理 → **先认领交易所成交**（被动同步，不新增敞口）→ 判定是否
重建 → 布单。认领必须先于判定：刚成交但本地仍是 `OPEN_PENDING` 的层级，在判定
眼里就是「还挂着的开仓单」，会被全撤全建连同持仓与 PnL 归因一起丢弃。

重建闸门：首次建网格 / 挂单不足 / 参数异常 / 价格真突破旧区间 → 重建；否则
冷却期内一律不建（冷却是抑制高频撤换单的主闸）。重建时**在途层级（已成交待
平仓）连同其 reduce_only 平仓单跨代保留**，不撤不弃，并入新一代层级集合——
否则持仓变成无人认领的库存，这一轮开平仓的盈亏永远归因不了。

GridManager 另有：层级状态机与 round-trip 盈亏归因、孤儿单清理与对账、
reduce-only 分层减仓、撤单硬超时、状态原子写入、库存名义额硬上限、
手术式减仓逆势库存、网格空转判定。

## 保护插件链

`IProtection` + `PROTECTION_REGISTRY` 注册即插：`max_drawdown`（全平）、
`daily_loss`（暂停开仓）、`consecutive_loss`（锁定交易对）、`position_timeout`（强平）。
账户级按周期 `check_all`，逐笔级走 `on_trade_open/close`；风控强平不上报虚假 pnl。

## 可观测性

`logs/main.log`（人读）；`logs/decisions|trades|equity/*.jsonl`
（决策审计 / 成交 pnl 归因 / 净值曲线）。
