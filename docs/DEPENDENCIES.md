# 依赖说明

## 技术指标实现：为什么选择纯 pandas/numpy？

### ✅ 最终方案：纯 pandas/numpy 实现

我们**不使用**任何第三方技术指标库（如 pandas-ta, TA-Lib 等），而是用纯 **pandas + numpy** 实现所有需要的技术指标。

### 为什么这样做？

#### 1. **避免依赖冲突**
- `pandas-ta` 要求 Python >=3.12
- `pandas-ta-classic` 虽然兼容性好，但增加了额外依赖
- `TA-Lib` 安装复杂，需要 C 编译器

#### 2. **减少依赖数量**
- 只需要 pandas 和 numpy（已经是项目必需依赖）
- 减少依赖 = 更少的版本冲突 = 更容易维护

#### 3. **性能足够**
- 对于我们需要的基本指标（RSI, MACD, MA, 布林带），pandas/numpy 性能完全够用
- 每 3 分钟才运行一次，性能不是瓶颈

#### 4. **完全可控**
- 理解每个指标的计算原理
- 可以根据需要调整算法
- 无需担心第三方库 API 变化

#### 5. **学习价值**
- 深入理解技术指标的数学原理
- 更好的代码可读性和可维护性

### 实现的技术指标

| 指标 | 公式 | 实现复杂度 |
|------|------|-----------|
| **MA (移动平均)** | `df['close'].rolling(window=period).mean()` | ⭐ 简单 |
| **RSI (相对强弱)** | `100 - (100 / (1 + RS))` | ⭐⭐ 中等 |
| **MACD** | `EMA(fast) - EMA(slow)` | ⭐⭐ 中等 |
| **布林带** | `SMA ± (std_dev × σ)` | ⭐ 简单 |
| **成交量分析** | `volume.pct_change()` | ⭐ 简单 |

所有这些指标都可以用 pandas 的内置方法轻松实现：
- `rolling()` - 滚动窗口计算
- `ewm()` - 指数加权移动平均
- `std()` - 标准差
- `pct_change()` - 百分比变化

### 代码示例

```python
# RSI 计算（纯 pandas）
def calculate_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)

    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()

    rs = avg_gain / avg_loss
    df['rsi'] = 100 - (100 / (1 + rs))
    return df

# MACD 计算（纯 pandas）
def calculate_macd(df: pd.DataFrame, fast=12, slow=26, signal=9):
    ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['close'].ewm(span=slow, adjust=False).mean()

    df['macd'] = ema_fast - ema_slow
    df['macd_signal'] = df['macd'].ewm(span=signal, adjust=False).mean()
    df['macd_hist'] = df['macd'] - df['macd_signal']
    return df
```

### 第三方库对比

| 库 | 优点 | 缺点 | 适用场景 |
|----|------|------|---------|
| **纯 pandas/numpy** ✅ | 无额外依赖、完全可控、易维护 | 需要自己实现 | 基本指标（本项目）|
| **pandas-ta** | 功能丰富 | Python >=3.12 | 不适合本项目 |
| **pandas-ta-classic** | 兼容性好 | 额外依赖 | 需要复杂指标 |
| **TA-Lib** | 性能最好（C实现）| 安装困难 | 高频交易 |
| **ta** | 简单易用 | 功能有限 | 快速原型 |

### 何时考虑第三方库？

如果项目需要以下特性，再考虑引入第三方库：

- ❌ 需要 **100+ 种技术指标**（本项目只需 5 种）
- ❌ **高频交易**（毫秒级响应，本项目 3 分钟间隔）
- ❌ **复杂的自定义指标**（Ichimoku Cloud, Renko 图表等）
- ❌ **性能是关键瓶颈**（处理海量历史数据）

### 最终依赖列表

```toml
[project]
requires-python = ">=3.10"
dependencies = [
    # LangChain 生态系统 (1.0+)
    "langchain>=1.0.3",
    "langchain-core>=1.0.2",
    "langchain-openai>=1.0.1",
    "langchain-community>=0.4.1",
    "langgraph>=1.0.2",

    # AI 和数据处理
    "openai>=1.60.0",
    "pandas>=2.2.0",      # ✅ 用于技术指标计算
    "numpy>=2.0.0",       # ✅ 用于技术指标计算

    # 交易所接口
    "ccxt>=4.4.0",

    # 配置和工具
    "pyyaml>=6.0.2",
    "python-dotenv>=1.0.1",
    "apscheduler>=3.10.4",

    # 控制台输出
    "colorama>=0.4.6",
    "rich>=13.9.0",
]
```

**注意**: 无需任何技术指标库！pandas 和 numpy 足够了。

### 安装和验证

```bash
# 安装（使用 uv）
uv sync

# 验证技术指标计算
python -c "
import pandas as pd
import numpy as np
from src.data.indicators import TechnicalIndicators

# 创建示例数据
df = pd.DataFrame({'close': range(100, 200)})

# 计算 RSI
df = TechnicalIndicators.calculate_rsi(df)
print(f'RSI 计算成功: {df[\"rsi\"].iloc[-1]:.2f}')
"
```

### 总结

✅ **简单**: 只用 pandas/numpy，无额外依赖
✅ **可靠**: 代码完全可控，无版本冲突
✅ **高效**: 对于 3 分钟间隔的交易，性能足够
✅ **可维护**: 代码清晰，易于理解和修改
✅ **可移植**: 兼容 Python 3.10-3.13

**结论**: 对于本项目需要的基本技术指标，纯 pandas/numpy 实现是最佳选择！
