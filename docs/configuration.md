# 配置说明

配置分两处：**敏感信息在 `.env`（环境变量），策略参数在 `config.yaml`**。
所有 YAML 键都有内置默认值（见 `src/config.py`），按需覆盖即可；
最小可运行配置只需 `llm` 段与 `trading.symbols`。

## 环境变量（.env）

| 变量 | 必填 | 说明 |
|------|------|------|
| `HYPERLIQUID_PRIVATE_KEY` | ✅ | 钱包私钥（0x 开头）。单钱包模式填交易钱包私钥；API 钱包模式填 API 钱包私钥 |
| `HYPERLIQUID_ACCOUNT_ADDRESS` | | 主钱包地址，仅 API 钱包模式需要（须先在 Hyperliquid 网页端授权） |
| `HYPERLIQUID_TESTNET` | | `true`=测试网（**默认**），`false`=主网 |
| `LLM_API_KEY` | ✅ | LLM API 密钥（兼容旧变量名 `OPENAI_API_KEY`） |
| `LOG_LEVEL` | | 日志级别，默认 `INFO` |

## config.yaml

### llm

任意 OpenAI 兼容端点（DeepSeek / OpenAI / 本地部署 / 各类网关）：

```yaml
llm:
  base_url: https://api.deepseek.com/v1
  model: deepseek-chat
  temperature: 0.2      # 交易决策建议低温度
  timeout: 120          # 单次请求超时（秒）
```

### trading

| 键 | 默认 | 说明 |
|----|------|------|
| `symbols` | `[BTC]` | 交易对列表（简单符号格式；网格使用第一个） |
| `perp_enabled` | `true` | 永续策略开关 |
| `grid_enabled` | `false` | 网格策略开关（可与永续并行） |
| `max_trade_amount` | `100` | 单笔投入上限（USD） |
| `max_leverage` | `5` | 杠杆上限（LLM 请求超过会被截断） |
| `max_positions` | `3` | 永续最大同时持仓数 |
| `take_profit_ratio` | `0.05` | 止盈比例（开仓价 ±5%） |
| `stop_loss_ratio` | `0.02` | 止损比例（开仓价 ∓2%） |
| `min_confidence` | `0.6` | 永续开仓最低置信度 |
| `timeframe` | `1h` | 决策 K 线周期（永续按收盘节拍触发） |

### grid

网格安全机制（强制中性、趋势过滤、库存上限严格模式、KEEP_GRID 对账、净额归因、
自适应仓位）**全部默认启用**，无需配置。常用旋钮：

| 键 | 默认 | 说明 |
|----|------|------|
| `interval_minutes` | `5` | 网格决策周期（分钟） |
| `width_min_pct` / `width_max_pct` | `0.02` / `0.15` | 网格宽度上下限 |
| `force_neutral` | `true` | 强制中性网格（忽略 AI 方向，消除反手亏损） |
| `max_position_notional_usd` | `0` | 库存硬上限（USD 名义额，0=关闭） |
| `halt_below_usd` | `0` | 净值停机线：低于此值且无持仓时整轮短路（0=关闭） |
| `trend_filter_enabled` | `true` | 多周期一致强势时暂停加仓 |
| `trend_confirm_cycles` | `2` | 连续 N 周期同向确认才暂停（迟滞去抖） |
| `flatten_min_cycles` | `3` | 平逆势库存需更多连续确认（暂停先行、平仓靠后） |
| `llm_failure_alert_cycles` | `6` | LLM 连续失败 N 周期后告警（0=关闭） |
| `llm_fallback_rebuild_cycles` | `12` | 空转 N 周期后纯市场数据兜底重建（0=关闭） |
| `barrier` | 见下 | Triple Barrier 覆盖项 |

Triple Barrier 默认值：止损 -5%、止盈 +10%、时限 4 小时、追踪止损 3% 激活 / 1% 回撤。
按需覆盖：

```yaml
grid:
  barrier:
    stop_loss_pct: 0.05
    take_profit_pct: 0.10
    time_limit_seconds: 14400
    trailing_stop_activation_pct: 0.03
    trailing_stop_delta_pct: 0.01
```

### protections

省略本段时使用内置默认保护链；显式写 `protections: []` 可全部关闭（不建议）：

```yaml
protections:
  - name: max_drawdown
    max_drawdown_pct: 0.10      # 回撤 10% → 全部平仓
    pause_hours: 4
  - name: daily_loss
    max_daily_loss_pct: 0.05    # 单日亏损 5% → 暂停新开仓
    pause_hours: 4
  - name: consecutive_loss
    max_consecutive_losses: 5   # 连亏 5 次 → 锁定
    per_symbol: true            # true=只锁该交易对
    forced_close_no_reset: false  # true=风控强平的盈利不重置连亏计数
    pause_hours: 4
  - name: position_timeout
    max_position_hours: 48      # 持仓超 48h → 强平
```
