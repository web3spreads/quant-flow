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

Quant Flow 是一个 AI 驱动的加密货币自动交易系统，专为 Hyperliquid DEX 设计。核心原则：
**LLM 只产出结构化 JSON 决策，下单、止盈止损与风控永远由确定性代码完成。**

两种独立策略，由 `config.yaml` 的 `trading.perp_enabled` / `grid_enabled` 开关控制，
可独立或并行运行（共用 `main.py` 统一入口）：

- **永续策略**（`src/strategy/perp.py`）：K 线收盘节拍触发，LLM 输出
  `BUY / SELL_SHORT / CLOSE / HOLD` JSON 决策，经边界校验后由 OrderManager 执行
- **网格策略**（`src/strategy/grid.py`）：固定间隔触发，LLM 判断方向与宽度，
  数学引擎（`grid_math`）计算参数，GridManager 布单管理

## 常用命令

```bash
uv run python main.py --config config.yaml --env-file .env   # 运行（策略开关在 config.yaml）

tail -f logs/main.log                            # 运行日志
tail -f logs/trades/trades_$(date +%Y%m%d).jsonl # 成交记录（含 pnl/reason 归因）
tail -f logs/equity/equity_$(date +%Y%m%d).jsonl # 净值快照
```

## 架构

架构详解见 `docs/architecture.md`（数据流、组件职责与目录导览）。
【关键安全机制】止损单失败 → 立即回滚平仓（带重试），实现于交易层
（`src/trading/client.py` + `order_manager.py`）。

## 设计原则

1. **决策与执行分离**：LLM 输出只经 `extract_json` 归一为 JSON 决策；非法 action、
   解析失败、调用异常一律降级为保守动作（HOLD / KEEP_GRID），绝不透传到执行层
2. **故障标记**：兜底决策与 AI 真实决策同形，必须打 `llm_ok=False` 标区分，
   供连续故障告警与网格空转自愈使用
3. **配置能省则省**：所有配置键有内置默认值（`src/config.py` 的 frozen dataclass），
   安全机制默认启用；新增功能不得要求用户新增配置才能获得安全行为
4. **插件化风控**：账户级保护实现 `IProtection` 接口并注册到 `PROTECTION_REGISTRY`，
   由 `ProtectionManager` 统一编排；风控强平不向连亏插件上报虚假 pnl
5. **原子写入**：状态文件（如 `data/grid_state.json`）使用 tempfile + move 写入
6. **Decimal 精度**：核心计算路径用 Decimal（`utils/precision.py`），仅在 API 边界转 float

## 开发规范

- 代码注释与 docstring 使用中文；日志输出使用中文
- 新增代码需删除未使用的导入和变量；异常变量必须命名（`except Exception as e`）
- 涉及资金安全的机制必须包含重试逻辑，且失败路径要有日志
- 修改后运行 `uv run pytest tests/` 与 `uv run ruff check src/ tests/`
- 测试不得依赖网络与真实密钥：LLM 用 `conftest.FakeLLM`，订单层用 `FakeOrderManager`

## Hyperliquid 特性

- 使用简单符号格式（`BTC`、`ETH`），不是交易对格式（不是 `BTC/USDT`）
- 测试网/主网通过环境变量 `HYPERLIQUID_TESTNET` 切换（默认测试网）
- 持仓 `szi` 为带符号字符串：正数=多仓，负数=空仓
- 手续费率从 API 动态获取，失败回退默认 Tier0（`src/fees.py`）

## Git 注意事项

- 敏感文件（`.env`、`config.yaml`）已在 `.gitignore`，不应提交
- `logs/` 与 `/data/` 为运行时产物，不入库
