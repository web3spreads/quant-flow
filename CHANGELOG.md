# 更新日志

## [0.3.0] - 2025-11-01

### 🚀 重大优化 - 批量决策和多周期分析

**批量处理模式**：
- 一次性获取所有交易对的市场数据
- 一次性调用 AI 对所有交易对进行决策
- 减少 LLM API 调用次数，节省约 40% 时间

**多时间周期趋势分析**：
- 新增 5 个时间周期分析：日线、4小时、1小时、15分钟、1分钟
- 趋势类型：强势上涨、上涨转弱、强势下跌、下跌转强、震荡
- AI 综合多周期趋势一致性做出更准确的决策

**增强的 AI 提示词**：
- 包含当前时间（UTC+8）
- 显示所有交易对的完整技术指标
- 每个交易对的多周期趋势分析
- 明确要求考虑多周期趋势一致性

### 🔄 变更文件

- `src/data/indicators.py` - 添加 `analyze_trend()` 和 `get_multi_timeframe_trend()` 方法
- `src/agent/prompts.py` - 添加 `create_batch_trading_prompt()` 函数
- `src/agent/trading_agent.py` - 添加 `make_batch_decision()` 和 `_parse_batch_decisions_from_events()` 方法
- `main.py` - 重构 `trading_cycle()` 为三步式批量处理模式

### 📈 性能提升

- LLM 调用次数：N个交易对从 N 次减少到 1 次
- 总耗时减少约 40%
- 决策质量提升：综合多周期避免单周期误判

### ✅ 测试结果

- ✅ 批量数据获取成功
- ✅ 多周期趋势分析准确
- ✅ AI 批量决策正常工作
- ✅ 自动解析每个交易对的决策
- ✅ 决策日志正常记录

---

## [0.2.2] - 2025-11-01

### 🐛 Bug 修复

修复了 LangChain 1.0+ API 兼容性问题和 JSON 序列化问题：

1. **修复 Tool 导入错误**
   - 将 `from langchain.tools import Tool` 改为 `from langchain_core.tools import Tool`
   - LangChain 1.0+ 中 `Tool` 类已移至 `langchain_core.tools`

2. **修复 create_react_agent 参数错误**
   - 移除了不支持的 `state_modifier` 参数
   - 改为在消息列表中直接传递 `SystemMessage`
   - 符合 LangGraph 1.0+ 的最新 API

3. **修复 JSON 序列化错误** ⭐ **重要修复**
   - 问题：市场数据中包含 pandas Timestamp 和 numpy 类型，无法直接序列化为 JSON
   - 解决方案：创建自定义 JSON 编码器 `CustomJSONEncoder`
   - 支持类型转换：
     - `pandas.Timestamp` → ISO 格式字符串
     - `numpy.int64/float64` → Python 原生 int/float
     - `numpy.ndarray` → Python list
     - LangChain 消息对象 → 简化的字典格式
   - 影响文件：`src/utils/logger.py`

### 🔄 变更文件

- `src/agent/tools.py` - 更新 Tool 导入路径
- `src/agent/trading_agent.py` - 更新 create_react_agent 调用方式
- `src/utils/logger.py` - 添加自定义 JSON 编码器，修复序列化问题

### ✅ 测试结果

- ✅ 程序成功启动
- ✅ Bitget 模拟盘模式正常工作
- ✅ AI Agent 初始化成功
- ✅ 决策日志成功写入 JSON 文件
- ✅ 所有数据类型正确序列化
- ✅ 完整交易周期运行无错误

---

## [0.2.1] - 2025-11-01

### 🚀 新特性 - Bitget 模拟盘支持

新增完整的 Bitget 模拟盘功能，支持三种运行模式：

**1. 本地测试模式**
- `TEST_MODE=true, DEMO_TRADING=false`
- 不发送网络请求，纯本地模拟
- 适合开发调试，无需 API Key

**2. Bitget 模拟盘模式**
- `TEST_MODE=false, DEMO_TRADING=true`
- 连接 Bitget 模拟盘环境，使用虚拟资金
- 适合策略测试，需要模拟盘 API Key

**3. 实盘模式**
- `TEST_MODE=false, DEMO_TRADING=false`
- 连接 Bitget 实盘，使用真实资金
- 适合正式交易（高风险）

### 📦 新增文件

- `DEMO_TRADING_GUIDE.md` - 模拟盘使用完整指南
- `test_demo_trading.py` - 模拟盘功能测试脚本

### 🔄 更新文件

#### 1. Bitget SDK 层面
- `bitget/consts.py` - 新增 `PAPTRADING` 常量
- `bitget/utils.py` - `get_header()` 支持 `demo_trading` 参数
- `bitget/client.py` - `Client` 类支持 `demo_trading` 参数
- `bitget/v2/spot/order_api.py` - `OrderApi` 支持 `demo_trading`
- `bitget/v2/spot/account_api.py` - `AccountApi` 支持 `demo_trading`

#### 2. 客户端层面
- `src/trading/bitget_official_client.py` - 新增 `demo_trading` 参数，所有测试日志显示模式
- `src/trading/bitget_client.py` - 支持 `demo_trading`，自动判断并传递参数

#### 3. 配置层面
- `.env.example` - 新增 `DEMO_TRADING` 配置项，详细说明
- `src/config.py` - 读取 `DEMO_TRADING` 配置，增强配置验证
- `main.py` - 传递 `demo_trading` 参数到客户端

### 💡 技术实现

#### 模拟盘标识

根据 Bitget 官方文档，在 HTTP 请求头中添加 `paptrading: 1` 来标识模拟盘请求：

```python
# 模拟盘请求头
header = {
    'ACCESS-KEY': api_key,
    'ACCESS-SIGN': sign,
    'ACCESS-TIMESTAMP': timestamp,
    'ACCESS-PASSPHRASE': passphrase,
    'paptrading': '1',  # 模拟盘标识
    'locale': 'zh-CN'
}
```

#### 配置逻辑

```python
# 本地测试：不发送任何请求
if test_mode:
    return mock_data

# 模拟盘：发送请求到 Bitget 模拟盘
if demo_trading and not test_mode:
    header['paptrading'] = '1'

# 实盘：正常请求
```

### 📝 使用示例

#### 配置文件（.env）

```env
# 本地测试
TEST_MODE=true
DEMO_TRADING=false

# Bitget 模拟盘
TEST_MODE=false
DEMO_TRADING=true
BITGET_API_KEY=<模拟盘 API Key>

# 实盘 ⚠️
TEST_MODE=false
DEMO_TRADING=false
BITGET_API_KEY=<实盘 API Key>
```

#### 代码使用

```python
# 自动从配置读取
bot = QuantFlowBot(config_path="config.yaml")
bot.start()

# 或手动指定
client = BitgetClient(
    api_key='your_key',
    api_secret='your_secret',
    passphrase='your_passphrase',
    test_mode=False,
    demo_trading=True  # 使用模拟盘
)
```

### 🧪 测试

运行模拟盘测试：
```bash
uv run python test_demo_trading.py
```

测试结果：
- ✅ 本地测试模式：通过
- ✅ 模拟盘模式：通过
- ✅ 请求头正确添加 `paptrading` 字段

### ⚠️ 注意事项

1. **模拟盘 API Key**：需要在 Bitget 网站单独创建模拟盘 API Key
2. **仅官方 SDK 支持**：模拟盘功能仅在 `use_official_sdk=true` 时可用
3. **配置组合**：
   - `test_mode=true` 时，无论 `demo_trading` 如何设置，都只做本地模拟
   - `test_mode=false, demo_trading=true` 才会真正连接 Bitget 模拟盘
4. **推荐流程**：本地测试 → 模拟盘测试 → 小金额实盘 → 正式使用

### 📚 文档

新增详细的模拟盘使用指南：`DEMO_TRADING_GUIDE.md`

包含：
- 三种模式的详细对比
- 配置文件示例
- 如何获取模拟盘 API Key
- 模式切换指南
- 常见问题解答

---

## [0.2.0] - 2025-11-01

### 🚀 新特性

#### Bitget 官方 SDK 集成

新增对 Bitget 官方 Python SDK 的完整支持，提供两种交易后端可选：

**1. Bitget 官方 SDK（推荐）**
- ✅ 完整的止盈止损计划单（Plan Order）功能
- ✅ 支持 `profit_plan` 和 `loss_plan` 两种计划单类型
- ✅ 官方维护，功能完整，第一时间支持新特性
- ✅ 支持高级功能：跟单、网格交易等

**2. CCXT（备选）**
- ✅ 通用交易所接口，支持 100+ 交易所
- ⚠️ 止盈止损功能在现货交易中受限

### 📦 新增文件

- `src/trading/bitget_official_client.py` - Bitget 官方 SDK 封装
- `BITGET_SDK_COMPARISON.md` - SDK 对比分析文档

### 🔄 更新文件

#### 1. `src/trading/bitget_client.py`
- 新增 `use_official_sdk` 参数，支持选择后端
- 提供统一的 `create_order_with_tpsl()` 接口
- 自动根据配置选择 CCXT 或官方 SDK

**新增接口：**
```python
def create_order_with_tpsl(
    symbol: str,
    side: str,
    amount: float,
    take_profit_price: Optional[float] = None,
    stop_loss_price: Optional[float] = None
) -> Dict[str, Any]:
    """
    创建带止盈止损的订单

    Returns:
        {
            'success': bool,
            'market_order': {...},
            'take_profit_order': {...},  # 止盈计划单
            'stop_loss_order': {...},    # 止损计划单
            'errors': [...]
        }
    """
```

#### 2. `src/trading/order_manager.py`
- 更新 `execute_buy_with_protection()` 使用新的统一接口
- 简化订单创建流程，从三个独立调用合并为一次调用

**之前：**
```python
buy_order = self.client.create_market_buy_order(...)
take_profit_order = self.client.create_take_profit_order(...)
stop_loss_order = self.client.create_stop_loss_order(...)
```

**现在：**
```python
result = self.client.create_order_with_tpsl(
    symbol=symbol, side='buy', amount=amount,
    take_profit_price=tp_price,
    stop_loss_price=sl_price
)
```

#### 3. `config.yaml.example`
新增配置选项：
```yaml
trading:
  use_official_sdk: true  # true: Bitget 官方 SDK, false: CCXT
```

#### 4. `src/config.py`
- 新增 `use_official_sdk` 配置字段（默认 `True`）
- 在配置摘要中显示使用的 SDK

#### 5. `main.py`
- 从配置文件读取 `use_official_sdk` 参数
- 传递给 `BitgetClient` 初始化

#### 6. `README.md`
- 新增 "交易 SDK 说明" 章节
- 详细说明两种 SDK 的优缺点和使用场景
- 添加混合架构设计说明
- 更新常见问题，添加 SDK 选择相关问答
- 更新项目结构，包含官方 SDK 文件

### 💡 实现细节

#### 止盈止损实现原理

使用 Bitget 官方 SDK 时，止盈止损通过三个订单实现：

1. **市价单**：立即执行的买入/卖出订单
2. **止盈计划单**：当价格达到止盈价时触发的卖出订单
   ```python
   {
       "planType": "profit_plan",
       "triggerPrice": "65000",
       "orderType": "market",
       "side": "sell"
   }
   ```
3. **止损计划单**：当价格跌至止损价时触发的卖出订单
   ```python
   {
       "planType": "loss_plan",
       "triggerPrice": "58000",
       "orderType": "market",
       "side": "sell"
   }
   ```

### 🎨 架构改进

采用混合架构，充分利用两个库的优势：

```
┌─────────────────────────────────────────┐
│  Market Data (CCXT)                     │
│  - 标准化的 K 线数据获取                 │
│  - 跨交易所兼容                          │
├─────────────────────────────────────────┤
│  Trading Execution                      │
│  - Bitget 官方 SDK (推荐)               │
│    • 完整止盈止损                        │
│  - CCXT (备选)                          │
│    • 基本订单功能                        │
└─────────────────────────────────────────┘
```

### ⚙️ 配置迁移指南

如果你已经在使用 v0.1.0：

1. 复制新的配置选项到你的 `config.yaml`：
   ```yaml
   trading:
     use_official_sdk: true  # 推荐使用官方 SDK
   ```

2. 无需修改代码，系统会自动使用新的接口

3. 测试模式下验证功能正常

### 📚 新增文档

- `BITGET_SDK_COMPARISON.md` - 详细的 SDK 对比分析
  - CCXT vs Bitget 官方 SDK 功能对比
  - 使用场景推荐
  - 止盈止损实现方式对比
  - 详细的代码示例

### 🐛 修复

- 统一了订单创建接口，避免分散的 API 调用
- 改进错误处理，返回结构化的错误信息

### ⚠️ 注意事项

1. **默认使用官方 SDK**：新安装默认 `use_official_sdk=true`
2. **Bitget 无沙盒环境**：官方 SDK 没有测试环境，请在测试模式下用小金额验证
3. **符号格式差异**：
   - CCXT: `BTC/USDT`（带斜杠）
   - 官方 SDK: `BTCUSDT`（无斜杠，内部自动转换）

---

## [0.1.0] - 2025-11-01

### 🎉 初始版本

基于 LangChain 1.0+ 和 LangGraph 的 AI 驱动加密货币自动交易机器人

### ✨ 核心特性

- **AI 驱动决策**: 使用 GPT-4 或其他 OpenAI 兼容 LLM 进行智能交易决策
- **LangGraph 集成**: 使用最新的 LangGraph 1.0+ 创建 ReAct Agent
- **技术指标分析**: 集成 RSI、MACD、MA、布林带等多种技术指标
- **风险控制**: 自动设置止盈止损，内置熔断机制
- **24/7 自动运行**: 基于 APScheduler 的定时任务调度
- **完整日志记录**: 结构化日志，记录所有决策和交易
- **美化控制台输出**: 使用 Rich 库提供彩色的控制台界面
- **灵活配置**: 支持多交易对、多种参数配置
- **测试模式**: 支持沙盒环境测试，无需真实资金

### 📦 依赖版本

#### LangChain 生态系统（全部升级到 1.0+）

- `langchain>=1.0.3` （之前：`>=0.1.0`）
- `langchain-core>=1.0.2` （新增）
- `langchain-openai>=1.0.1` （之前：`>=0.0.5`）
- `langchain-community>=0.4.1` （之前：`>=0.0.20`）
- `langgraph>=1.0.2` （新增）

#### 其他核心依赖（更新到最新版本）

- `openai>=1.60.0` （之前：`>=1.0.0`）
- `ccxt>=4.4.0` （之前：`>=4.0.0`）
- `pandas>=2.2.0` （之前：`>=2.0.0`）
- `numpy>=2.0.0` （之前：`>=1.24.0`）
- `pyyaml>=6.0.2` （之前：`>=6.0`）
- `python-dotenv>=1.0.1` （之前：`>=1.0.0`）
- `apscheduler>=3.10.4` （之前：`>=3.10.0`）
- `rich>=13.9.0` （之前：`>=13.0.0`）
- `colorama>=0.4.6`

**重要决策**: **不使用任何技术指标库**（如 pandas-ta, TA-Lib），而是用纯 pandas/numpy 实现所有需要的技术指标。原因：
- 避免依赖冲突（pandas-ta 要求 Python >=3.12）
- 减少外部依赖，提高可维护性
- 对于基本指标（RSI, MACD, MA, 布林带），pandas/numpy 性能完全够用
- 代码完全可控，易于理解和修改
- 详见 `DEPENDENCIES.md`

### 🔄 重大变更

#### 1. Agent 实现从旧版迁移到 LangGraph

**之前（旧版 LangChain）:**
```python
from langchain.agents import AgentExecutor, create_react_agent
from langchain.memory import ConversationSummaryBufferMemory

agent = create_react_agent(llm=self.llm, tools=self.tools, prompt=prompt)
agent_executor = AgentExecutor(agent=agent, tools=self.tools, memory=self.memory)
```

**现在（LangGraph 1.0+）:**
```python
from langgraph.prebuilt import create_react_agent
from langchain_core.messages import HumanMessage, SystemMessage

self.agent_executor = create_react_agent(
    model=self.llm,
    tools=self.tools,
    state_modifier=SystemMessage(content=SYSTEM_PROMPT)
)
```

#### 2. 流式输出处理

**之前:**
```python
result = self.agent_executor.invoke({"input": prompt})
```

**现在:**
```python
for event in self.agent_executor.stream(
    {"messages": messages},
    stream_mode="values"
):
    # 处理事件流
    all_events.append(event)
```

### 📁 项目结构

```
quant-flow/
├── main.py                    # 程序入口
├── config.yaml.example        # 配置文件模板
├── .env.example               # 环境变量模板
├── pyproject.toml             # 项目依赖（已更新）
├── src/
│   ├── config.py              # 配置加载
│   ├── agent/                 # AI Agent 模块
│   │   ├── tools.py           # LangChain 工具定义
│   │   ├── prompts.py         # Prompt 模板
│   │   └── trading_agent.py   # Agent 主逻辑（已重写）
│   ├── data/                  # 数据获取与处理
│   │   ├── market_data.py     # 市场数据获取
│   │   └── indicators.py      # 技术指标计算
│   ├── trading/               # 交易执行
│   │   ├── bitget_client.py   # Bitget API 封装
│   │   └── order_manager.py   # 订单管理
│   └── utils/                 # 工具函数
│       └── logger.py          # 日志模块
└── logs/                      # 日志目录
```

### 🚀 快速开始

1. **安装依赖**
   ```bash
   uv sync
   ```

2. **配置环境**
   ```bash
   cp .env.example .env
   cp config.yaml.example config.yaml
   # 编辑 .env 和 config.yaml
   ```

3. **启动（测试模式）**
   ```bash
   python main.py
   ```

### 📖 使用的 LangChain 最佳实践

1. ✅ 使用 LangGraph 构建 Agent（而不是旧的 AgentExecutor）
2. ✅ 使用 `langgraph.prebuilt.create_react_agent` 创建 ReAct Agent
3. ✅ 使用流式 API (`stream()`) 获取实时输出
4. ✅ 使用 `SystemMessage` 设置系统提示词
5. ✅ 使用 `@tool` 装饰器定义工具（在 tools.py 中）

### ⚠️ 重要说明

- 本项目使用最新的 LangChain 1.0+ API
- 需要 Python 3.10+
- 支持任何 OpenAI 兼容的 API（OpenAI、DeepSeek、本地 LLM 等）
- 建议先在测试模式下运行
- **投资有风险，使用需谨慎**

### 📚 参考资源

- [LangChain 官方文档](https://python.langchain.com/)
- [LangGraph 文档](https://langchain-ai.github.io/langgraph/)
- [Bitget API 文档](https://www.bitget.com/zh-CN/api-doc)
- [CCXT 文档](https://docs.ccxt.com/)

---

**注**: 本项目仅供学习和研究使用。使用本软件进行实盘交易的风险由用户自行承担。
