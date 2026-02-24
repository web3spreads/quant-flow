# QLib 框架深度研究报告

> **研究日期**: 2026-02-24
> **研究目的**: 全面了解微软 QLib 量化投资平台的所有能力，为 Quant Flow 项目基于 QLib 的全面重构提供技术基础
> **研究结论**: QLib 具备从数据处理、因子挖掘、模型训练、回测验证到在线部署的完整量化投资闭环能力，可作为本项目核心决策引擎

---

## 目录

1. [QLib 概述](#1-qlib-概述)
2. [整体架构](#2-整体架构)
3. [数据层](#3-数据层)
4. [Alpha 因子系统](#4-alpha-因子系统)
5. [模型层（AI/ML）](#5-模型层aiml)
6. [策略与投资组合管理](#6-策略与投资组合管理)
7. [回测系统](#7-回测系统)
8. [嵌套决策执行框架](#8-嵌套决策执行框架)
9. [工作流与实验管理](#9-工作流与实验管理)
10. [在线服务模块](#10-在线服务模块)
11. [强化学习模块](#11-强化学习模块)
12. [元学习框架](#12-元学习框架)
13. [RD-Agent 自动化研发](#13-rd-agent-自动化研发)
14. [信号分析与诊断工具](#14-信号分析与诊断工具)
15. [任务管理系统](#15-任务管理系统)
16. [Point-in-Time 数据库](#16-point-in-time-数据库)
17. [序列化机制](#17-序列化机制)
18. [加密货币适配分析](#18-加密货币适配分析)
19. [与现有 Quant Flow 的对比](#19-与现有-quant-flow-的对比)
20. [重构建议](#20-重构建议)

---

## 1. QLib 概述

### 1.1 基本信息

- **名称**: QLib (Quantitative Library)
- **开发者**: 微软亚洲研究院 (Microsoft Research Asia)
- **开源地址**: https://github.com/microsoft/qlib
- **Star 数**: 31.1K+ (截至 2026 年)
- **许可证**: MIT License
- **Python 版本**: 3.8 - 3.12
- **安装方式**: `pip install pyqlib`

### 1.2 定位

QLib 是一个**面向 AI 的量化投资平台**，旨在利用 AI 技术赋能量化研究，覆盖从探索想法到生产落地的全链路。支持多种机器学习范式：

- **监督学习**: 传统的预测建模
- **市场动态建模**: 适应市场非平稳性
- **强化学习**: 端到端的策略优化
- **元学习**: 跨任务知识迁移

### 1.3 核心优势

1. **端到端覆盖**: 数据处理 → 特征工程 → 模型训练 → 回测验证 → 在线部署
2. **AI 原生**: 内置 20+ 种机器学习/深度学习模型
3. **高性能**: 二进制数据存储 + 多层缓存 + 表达式引擎
4. **可扩展**: 模块化设计，每个组件都可以自定义替换
5. **生产级**: 支持在线服务、模型滚动更新、增量学习
6. **RD-Agent**: 最新集成 LLM 驱动的自动化因子挖掘和模型优化

---

## 2. 整体架构

### 2.1 三层架构

```
┌─────────────────────────────────────────────────────────────────┐
│                    Interface Layer（接口层）                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐│
│  │ Workflow  │  │ Analyzer │  │ Recorder │  │ Online Serving   ││
│  │  (qrun)  │  │ (分析报告)│  │(实验追踪)│  │  (在线服务)      ││
│  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘│
├─────────────────────────────────────────────────────────────────┤
│                    Workflow Layer（工作流层）                     │
│  ┌──────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ ┌────────────┐ │
│  │Model │ │ Strategy │ │ Backtest │ │  RL    │ │Meta Learn  │ │
│  │(模型)│ │  (策略)  │ │  (回测)  │ │(强化)  │ │ (元学习)   │ │
│  └──────┘ └──────────┘ └──────────┘ └────────┘ └────────────┘ │
├─────────────────────────────────────────────────────────────────┤
│                 Infrastructure Layer（基础设施层）                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │  DataServer   │  │ Expression   │  │     Cache System     │  │
│  │  (数据服务)   │  │  Engine      │  │   (缓存系统)         │  │
│  │              │  │  (表达式引擎) │  │  Mem + Disk          │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 数据流

```
原始数据(CSV/API)
    ↓ [数据转换]
QLib 二进制格式 (.bin)
    ↓ [DataLoader]
原始 DataFrame (OHLCV + 衍生字段)
    ↓ [DataHandler / Processor]
处理后的特征矩阵 (归一化/填充/截面标准化)
    ↓ [Dataset]
训练/验证/测试数据集
    ↓ [Model]
预测信号 (scores)
    ↓ [Strategy]
交易决策 (买/卖/持有 + 权重)
    ↓ [Executor / Backtest]
订单执行 / 回测结果
    ↓ [Analyzer]
绩效报告 (IC/IR/夏普/回撤)
```

---

## 3. 数据层

### 3.1 数据存储格式

QLib 采用自定义的二进制格式（`.bin` 文件），专为金融时间序列数据优化：

```
data/
├── calendars/         # 交易日历（按区域划分）
│   ├── day.txt        # 日频交易日
│   └── min.txt        # 分钟频交易时间
├── instruments/       # 股票池/资产池配置
│   ├── all.txt        # 全部资产
│   └── csi300.txt     # CSI300 成分股
├── features/          # 每只资产的原始特征
│   ├── BTC/
│   │   ├── open.day.bin
│   │   ├── high.day.bin
│   │   ├── low.day.bin
│   │   ├── close.day.bin
│   │   └── volume.day.bin
│   └── ETH/
│       └── ...
└── cache/             # 计算结果缓存
```

### 3.2 数据转换工具

```python
# CSV 转 QLib 格式
python scripts/dump_bin.py dump_all \
    --csv_path ./csv_data \
    --qlib_dir ./qlib_data \
    --freq day \
    --date_field_name date \
    --symbol_field_name symbol
```

支持的数据源：
- **Yahoo Finance**: 内置数据收集器
- **CoinGecko**: 加密货币数据收集器（PR #733）
- **自定义 CSV/Parquet**: 通用转换工具
- **Wind/TuShare**: 中国A股数据

### 3.3 DataLoader

```python
from qlib.data.dataset.loader import QlibDataLoader

# 定义特征和标签
features = ["$close", "$volume", "Ref($close, 1)/$close - 1"]
labels = ["Ref($close, -2)/Ref($close, -1) - 1"]  # 未来2日收益

loader = QlibDataLoader(
    config=(features, labels),
    freq="day"
)
data = loader.load(instruments="all", start_time="2020-01-01", end_time="2025-12-31")
```

### 3.4 DataHandler

DataHandler 是数据处理的核心组件，提供"可学习处理器"框架：

```
原始数据 → [共享处理器] → [推理处理器(infer)] + [学习处理器(learn)]
```

**内置 DataHandler：**

| 名称 | 特征数 | 说明 |
|------|--------|------|
| Alpha158 | 158 | 经典量化因子集，涵盖动量、波动率、成交量等 |
| Alpha360 | 360 | 扩展因子集，更多时间窗口和统计量 |

**内置处理器：**

| 处理器 | 功能 |
|--------|------|
| DropnaProcessor | 删除缺失值 |
| Fillna | 填充缺失值 |
| ProcessInf | 处理无穷值 |
| MinMaxNorm | 最小-最大归一化 |
| ZscoreNorm | Z-Score 标准化 |
| RobustZScoreNorm | 鲁棒 Z-Score（使用中位数和 MAD） |
| CSZScoreNorm | 截面 Z-Score 标准化 |
| CSRankNorm | 截面排名归一化 |

```python
from qlib.contrib.data.handler import Alpha158

handler = Alpha158(
    instruments="csi300",
    start_time="2020-01-01",
    end_time="2025-12-31",
    fit_start_time="2020-01-01",
    fit_end_time="2023-12-31",
    infer_processors=[
        {"class": "RobustZScoreNorm", "kwargs": {"fields_group": "feature", "clip_outlier": True}},
        {"class": "Fillna", "kwargs": {"fields_group": "feature"}},
    ],
    learn_processors=[
        {"class": "DropnaLabel"},
        {"class": "CSRankNorm", "kwargs": {"fields_group": "label"}},
    ],
)
```

### 3.5 缓存机制

QLib 实现了三级缓存加速数据访问：

| 缓存层级 | 类型 | 存储位置 | 说明 |
|---------|------|---------|------|
| 第一级 | MemCache | 内存 | 全局缓存日历、股票列表、常用特征 |
| 第二级 | ExpressionCache | 磁盘 | 缓存表达式计算结果 |
| 第三级 | DatasetCache | 磁盘 | 缓存完整数据集 |

### 3.6 表达式引擎

QLib 的表达式引擎是其核心优势之一，支持通过字符串公式定义复杂特征：

```python
# 内置操作符
"$close"                          # 收盘价
"$volume"                         # 成交量
"Ref($close, 5)"                  # 5日前的收盘价
"Ref($close, 5) / $close - 1"    # 5日涨跌幅
"Mean($close, 20)"               # 20日均价
"Std($close, 20)"                # 20日标准差
"EMA($close, 12)"                # 12日指数移动平均
"Corr($close, $volume, 20)"      # 价量20日相关性
"Rank($close)"                   # 截面排名
"Delta($close, 5)"               # 5日变化量
"WMA($close, 20)"                # 加权移动平均
"Kurt($close, 20)"               # 20日峰度
"Skew($close, 20)"               # 20日偏度
"Quantile($close, 20, 0.8)"      # 20日80分位数

# 组合表达式 - 构建 MACD 因子
"EMA($close, 12) - EMA($close, 26)"

# 条件表达式
"If($close > Ref($close, 1), $volume, -$volume)"
```

**操作符分类：**

| 类别 | 操作符 |
|------|--------|
| 算术 | Add, Sub, Mul, Div, Power, Abs, Sign, Log |
| 统计 | Mean, Std, Var, Skew, Kurt, Quantile, Median |
| 时间序列 | Ref, Delta, EMA, WMA, SMA, Slope |
| 排名 | Rank, CSRank, TSRank |
| 比较 | Greater, Less, Eq, If |
| 相关性 | Corr, Cov |
| 计数 | Count, Sum |
| 极值 | Max, Min, IdxMax, IdxMin |

---

## 4. Alpha 因子系统

### 4.1 内置因子集

#### Alpha158（158 个因子）

包含以下类别的因子：

| 类别 | 因子数量 | 示例 |
|------|---------|------|
| 价格动量 | ~30 | 1/5/10/20/60日涨跌幅 |
| 成交量特征 | ~20 | 成交量均值、标准差、变化率 |
| 波动率特征 | ~20 | ATR、布林带宽度、波动率排名 |
| 价量相关性 | ~15 | 价量相关系数、OBV |
| 技术指标 | ~30 | RSI、MACD、KDJ、WR |
| 高阶统计 | ~20 | 偏度、峰度、自相关系数 |
| 均线特征 | ~23 | MA偏离度、均线交叉 |

#### Alpha360（360 个因子）

在 Alpha158 基础上扩展，增加了：
- 更多时间窗口（增加 120 日、240 日周期）
- 更多截面统计量
- 更复杂的组合因子

### 4.2 自定义因子

```python
# 方式1：表达式定义
custom_features = [
    "($high - $low) / $close",                    # 振幅
    "Std(Ref($close, 1)/$close - 1, 20)",         # 20日波动率
    "Corr($close, $volume, 10)",                  # 10日价量相关性
    "(EMA($close, 12) - EMA($close, 26)) / $close", # MACD归一化
    "($close - Mean($close, 20)) / Std($close, 20)", # 布林带位置
]

# 方式2：继承 ExpressionOps 实现自定义操作符
from qlib.data.ops import ExpressionOps

class MyCustomFactor(ExpressionOps):
    def __init__(self, feature, N):
        self.feature = feature
        self.N = N

    def _load_internal(self, instrument, start_index, end_index, freq):
        # 自定义计算逻辑
        ...
```

### 4.3 因子分析工具

```python
from qlib.contrib.report import analysis_model

# IC 分析（信息系数）
analysis_model.model_performance_graph(
    pred_label,          # 预测值和实际标签
    lag=1,               # 滞后期
    N=5,                 # 分位数
    reverse=False,
    rank=True,           # 使用排名IC
)

# 输出指标：
# - IC 均值和标准差
# - ICIR（信息比率）
# - 排名 IC
# - 累积 IC 曲线
# - 月度 IC 热力图
```

---

## 5. 模型层（AI/ML）

### 5.1 模型接口

所有模型继承自 `qlib.model.base.Model`，需实现两个核心方法：

```python
class Model:
    def fit(self, dataset: DatasetH, reweighter=None):
        """从数据集学习模型参数"""
        ...

    def predict(self, dataset: DatasetH, segment: str = "test") -> pd.Series:
        """对指定数据段生成预测评分"""
        ...
```

### 5.2 内置模型库（Model Zoo）

#### 树模型

| 模型 | 类名 | 模块路径 | 说明 |
|------|------|---------|------|
| **LightGBM** | LGBModel | qlib.contrib.model.gbdt | 梯度提升决策树，训练速度快 |
| **XGBoost** | XGBModel | qlib.contrib.model.xgboost_model | 极端梯度提升 |
| **CatBoost** | CatBoostModel | qlib.contrib.model.catboost_model | 支持类别特征 |
| **DoubleEnsemble** | DEnsemble | qlib.contrib.model.double_ensemble | 双集成模型 (ICDM 2020) |

#### 线性模型

| 模型 | 类名 | 说明 |
|------|------|------|
| **Linear** | LinearModel | 线性回归基准 |

#### 深度学习模型（PyTorch）

| 模型 | 类名 | 参考文献 | 说明 |
|------|------|---------|------|
| **DNN** | DNNModelPytorch | - | 通用深度神经网络 |
| **LSTM** | LSTM | Hochreiter 1997 | 长短期记忆网络 |
| **GRU** | GRU | Cho 2014 | 门控循环单元 |
| **ALSTM** | ALSTM | Qin 2017 | 注意力 LSTM |
| **Transformer** | Transformer | Vaswani 2017 | 自注意力机制 |
| **Localformer** | Localformer | Jiang | 本地化 Transformer |
| **TRA** | TRA | Dong 2021 (KDD) | 时序路由适配器 |
| **TCN** | TCN | Bai | 时序卷积网络 |
| **TabNet** | TabNet | Arik 2019 (AAAI) | 表格数据注意力 |
| **SFM** | SFM | Zhang 2017 (KDD) | 股票预测模型 |
| **GATs** | GATs | Velickovic 2017 | 图注意力网络 |
| **KRNN** | KRNN | - | 核递归神经网络 |
| **ADD** | ADD | - | 对抗判别域适应 |
| **IGMTF** | IGMTF | - | 组间互信息时序特征迁移 |

#### TensorFlow 模型

| 模型 | 说明 |
|------|------|
| **TFT** | 时序融合 Transformer (Lim 2019) |

#### 高级模型

| 模型 | 说明 |
|------|------|
| **TCTS** | 任务对比时序偏移 (ICML 2021) |
| **HIST** | 基于概念的历史信息股票趋势 |
| **ADARNN** | 自适应递归神经网络 |
| **Sandwich** | 三明治模型 |
| **BPQP** | 端到端学习框架 |

### 5.3 模型训练配置示例

```yaml
model:
    class: LGBModel
    module_path: qlib.contrib.model.gbdt
    kwargs:
        loss: mse
        colsample_bytree: 0.8879
        learning_rate: 0.0421
        subsample: 0.8789
        lambda_l1: 205.6999
        lambda_l2: 580.9768
        max_depth: 8
        num_leaves: 210
        num_threads: 20
```

### 5.4 自定义模型集成

```python
from qlib.model.base import Model
import torch

class MyCryptoModel(Model):
    """自定义加密货币预测模型"""

    def __init__(self, input_dim=158, hidden_dim=128, output_dim=1):
        self.model = torch.nn.Sequential(
            torch.nn.Linear(input_dim, hidden_dim),
            torch.nn.ReLU(),
            torch.nn.Dropout(0.3),
            torch.nn.Linear(hidden_dim, output_dim),
        )

    def fit(self, dataset, reweighter=None):
        train_data = dataset.prepare("train")
        # 训练逻辑...

    def predict(self, dataset, segment="test"):
        test_data = dataset.prepare(segment)
        # 预测逻辑...
        return pd.Series(predictions, index=test_data.index)
```

---

## 6. 策略与投资组合管理

### 6.1 策略接口

```python
from qlib.strategy.base import BaseStrategy

class BaseStrategy:
    def generate_trade_decision(self, execute_result=None):
        """在每个交易时段生成交易决策"""
        ...
```

### 6.2 内置策略

#### TopkDropoutStrategy

```python
from qlib.contrib.strategy import TopkDropoutStrategy

strategy = TopkDropoutStrategy(
    signal=pred_score,       # 模型预测信号
    topk=50,                 # 持有前50只
    n_drop=5,                # 每日替换5只
    only_tradable=True,      # 仅交易可交易标的
)
```

**算法逻辑：**
1. 按预测信号排名选择 topk 只资产
2. 每日卖出评分最低的 n_drop 只
3. 买入评分最高的 n_drop 只未持有资产
4. 自动生成等权重或信号权重仓位

#### EnhancedIndexingStrategy

增强指数策略，在跟踪基准的同时追求超额收益：
- 控制跟踪误差
- 风险约束优化
- 换手率控制

#### WeightStrategyBase

权重策略基类，用于自定义仓位管理：

```python
from qlib.contrib.strategy import WeightStrategyBase

class MyWeightStrategy(WeightStrategyBase):
    def generate_target_weight_position(self, score, current_position, trade_date):
        """根据信号生成目标仓位权重"""
        # 自定义仓位分配逻辑
        weights = score / score.abs().sum()  # 简单信号权重
        return weights
```

### 6.3 投资组合优化

QLib 支持多种组合优化方式：
- **等权重分配**: 所有资产等权
- **信号权重**: 按预测信号强度分配
- **风险平价**: 按风险贡献均等分配
- **均值-方差优化**: 马科维茨模型
- **自定义优化器**: 继承基类实现

---

## 7. 回测系统

### 7.1 回测引擎

```python
from qlib.backtest import backtest_daily, backtest

# 日频回测
portfolio_metrics, indicator = backtest_daily(
    pred=pred_score,
    strategy=strategy,
    executor=executor,
    account=100000,            # 初始资金
    benchmark="SH000300",      # 基准
    exchange_kwargs={
        "freq": "day",
        "limit_threshold": 0.095,  # 涨跌停限制
        "deal_price": "close",     # 成交价格
        "open_cost": 0.0005,       # 买入手续费
        "close_cost": 0.0015,      # 卖出手续费
        "min_cost": 5,             # 最小手续费
    }
)
```

### 7.2 交易成本模型

| 参数 | 说明 | 默认值 |
|------|------|--------|
| open_cost | 买入手续费率 | 0.0005 |
| close_cost | 卖出手续费率 | 0.0015 |
| min_cost | 最低手续费 | 5 元 |
| deal_price | 成交价格 | close |
| limit_threshold | 涨跌停限制 | 0.095 (9.5%) |
| trade_unit | 最小交易单位 | 100 (A股为100股) |

### 7.3 执行器

```python
from qlib.backtest.executor import SimulatorExecutor

executor = SimulatorExecutor(
    time_per_step="day",      # 执行频率
    generate_portfolio_metrics=True,
)
```

### 7.4 绩效指标

回测输出的核心指标：

| 指标 | 说明 |
|------|------|
| 年化收益率 (ARR) | 策略的年化回报 |
| 夏普比率 (Sharpe) | 风险调整后收益 |
| 信息比率 (IR) | 超额收益 / 跟踪误差 |
| 最大回撤 (MDD) | 净值从峰值到谷值的最大跌幅 |
| 卡尔马比率 (Calmar) | 年化收益 / 最大回撤 |
| 胜率 (Win Rate) | 盈利交易占比 |
| 换手率 (Turnover) | 日均换手率 |
| IC / Rank IC | 信息系数 / 排名信息系数 |
| ICIR | IC 的信息比率 |

---

## 8. 嵌套决策执行框架

### 8.1 概述

嵌套决策执行框架（Nested Decision Execution Framework）是 QLib 的高级特性，支持多层级交易策略的联合优化和回测。

### 8.2 架构

```
┌─────────────────────────────────────────────┐
│              日频策略层                       │
│  ┌─────────────┐  ┌──────────┐  ┌────────┐ │
│  │信息提取器    │→│预测模型   │→│决策生成 │ │
│  └─────────────┘  └──────────┘  └────┬───┘ │
│                                      ↓      │
│  ┌──────────────────────────────────────┐   │
│  │          日频执行环境                 │   │
│  │  ┌─────────────────────────────────┐ │   │
│  │  │       日内策略层                 │ │   │
│  │  │ ┌────────┐ ┌──────┐ ┌────────┐ │ │   │
│  │  │ │信息提取│→│预测  │→│决策生成│ │ │   │
│  │  │ └────────┘ └──────┘ └───┬────┘ │ │   │
│  │  │                         ↓      │ │   │
│  │  │ ┌──────────────────────────┐   │ │   │
│  │  │ │    日内执行环境           │   │ │   │
│  │  │ │  (TWAP/VWAP/最优执行)    │   │ │   │
│  │  │ └──────────────────────────┘   │ │   │
│  │  └─────────────────────────────────┘ │   │
│  └──────────────────────────────────────┘   │
└─────────────────────────────────────────────┘
```

### 8.3 核心组件

每个层级包含三个模块：

1. **交易代理（Trading Agent）**
   - 信息提取器（State Interpreter）：从市场数据中提取有效信息
   - 预测模型：生成交易信号
   - 决策生成器：基于信号产生交易决定

2. **执行环境（Execution Env）**
   - 模拟真实市场执行环境
   - 支持订单簿数据
   - 考虑市场冲击和滑点

### 8.4 应用场景

- **日频 + 日内联合优化**: 日频确定交易方向，日内优化执行时机
- **订单拆分**: 大单拆分为小单，降低市场冲击
- **TWAP/VWAP 执行**: 时间加权/成交量加权平均执行
- **高频交易回测**: 基于 tick/分钟数据的高频策略验证

---

## 9. 工作流与实验管理

### 9.1 qrun 命令

```bash
# 一键执行完整工作流
qrun configuration.yaml
```

### 9.2 配置文件结构

```yaml
qlib_init:
    provider_uri: "~/.qlib/qlib_data/cn_data"
    region: cn

market: &market csi300
benchmark: &benchmark SH000300

data_handler_config: &data_handler_config
    start_time: "2020-01-01"
    end_time: "2025-12-31"
    fit_start_time: "2020-01-01"
    fit_end_time: "2023-12-31"
    instruments: *market

task:
    model:
        class: LGBModel
        module_path: qlib.contrib.model.gbdt
        kwargs:
            loss: mse
            learning_rate: 0.0421
    dataset:
        class: DatasetH
        module_path: qlib.data.dataset
        kwargs:
            handler:
                class: Alpha158
                module_path: qlib.contrib.data.handler
                kwargs: *data_handler_config
            segments:
                train: ["2020-01-01", "2022-12-31"]
                valid: ["2023-01-01", "2023-12-31"]
                test: ["2024-01-01", "2025-12-31"]
    record:
        - class: SignalRecord
          module_path: qlib.workflow.record_temp
        - class: SigAnaRecord
          module_path: qlib.workflow.record_temp
        - class: PortAnaRecord
          module_path: qlib.workflow.record_temp
          kwargs:
              config:
                  strategy:
                      class: TopkDropoutStrategy
                      kwargs:
                          signal: <PRED>
                          topk: 50
                          n_drop: 5
                  backtest:
                      start_time: "2024-01-01"
                      end_time: "2025-12-31"
                      account: 100000000
                      benchmark: *benchmark
```

### 9.3 Recorder 实验管理

```python
from qlib.workflow import R

# 创建实验
with R.start(experiment_name="crypto_model_v1", recorder_name="run_001"):
    # 记录参数
    R.log_params(learning_rate=0.04, num_leaves=210)

    # 训练模型
    model.fit(dataset)

    # 记录指标
    pred = model.predict(dataset)
    R.log_metrics(ic=0.05, icir=1.2, sharpe=2.1)

    # 保存模型
    R.save_objects(model=model, pred=pred)

# 检索实验
recorder = R.get_recorder(experiment_name="crypto_model_v1")
loaded_model = recorder.load_object("model")
```

### 9.4 记录模板

| 模板 | 功能 |
|------|------|
| SignalRecord | 保存模型预测信号 |
| SigAnaRecord | 信号分析（IC/ICIR/排名IC） |
| PortAnaRecord | 组合分析（回测结果+风险指标） |
| HFSignalRecord | 高频交易信号记录 |

---

## 10. 在线服务模块

### 10.1 架构

```
┌──────────────────────────────────────────┐
│           Online Manager                  │
│  ┌──────────┐  ┌──────────┐  ┌────────┐ │
│  │ Online   │  │ Online   │  │Updater │ │
│  │ Strategy │  │  Tool    │  │        │ │
│  └──────────┘  └──────────┘  └────────┘ │
└──────────────────────────────────────────┘
```

### 10.2 核心组件

#### Online Manager

```python
from qlib.workflow.online.manager import OnlineManager

manager = OnlineManager(strategies=[rolling_strategy])

# 日常例行流程
manager.routine()
# 内部执行: 更新预测 → 准备任务 → 准备在线模型 → 准备信号

# 历史回溯模拟
manager.simulate(start_time="2024-01-01", end_time="2025-12-31")
```

#### Online Strategy

```python
from qlib.workflow.online.strategy import RollingStrategy

strategy = RollingStrategy(
    exp_name="crypto_rolling",
    task_template=task_config,
    rolling_period=20,         # 每20天滚动更新
)
```

#### Updater

```python
from qlib.workflow.online.update import PredUpdater, LabelUpdater

# 更新预测
pred_updater = PredUpdater(record=recorder, to_date="2025-12-31")
pred_updater.update()

# 更新标签
label_updater = LabelUpdater(record=recorder)
label_updater.update()
```

### 10.3 训练方案

| 方案 | 训练方式 | 适用场景 |
|------|---------|---------|
| Online + Trainer | 每次 routine 立即训练 | 实时要求高 |
| Online + DelayTrainer | 并行准备，集中训练 | 计算效率优先 |
| Simulation + Trainer | 历史回溯逐步训练 | 策略验证 |
| Simulation + DelayTrainer | 完全延迟训练 | 无时间依赖模型 |

---

## 11. 强化学习模块

### 11.1 QlibRL 概述

QLib 的强化学习工具包专为量化投资设计，核心应用场景：

1. **订单执行优化**: 学习最优执行策略，最小化交易成本和市场冲击
2. **投资组合构建**: 优化资产配置决策，最大化收益或夏普比率

### 11.2 框架组件

```
┌─────────────────────────────────────────┐
│              QlibRL 框架                 │
│                                          │
│  ┌─────────────┐  ┌──────────────────┐  │
│  │ Environment │  │     Agent         │  │
│  │  (模拟器)   │  │  ┌────────────┐  │  │
│  │             │←→│  │  Policy    │  │  │
│  │  市场状态   │  │  │  (策略)    │  │  │
│  │  订单簿     │  │  └────────────┘  │  │
│  │  执行反馈   │  │  ┌────────────┐  │  │
│  │             │  │  │  Reward    │  │  │
│  │             │  │  │  (奖励)    │  │  │
│  └─────────────┘  │  └────────────┘  │  │
│                    └──────────────────┘  │
│                                          │
│  ┌─────────────────────────────────────┐│
│  │         Interpreter 层               ││
│  │  StateInterpreter: 状态编码         ││
│  │  ActionInterpreter: 动作解码        ││
│  └─────────────────────────────────────┘│
│                                          │
│  ┌─────────────────────────────────────┐│
│  │         Trainer 层                   ││
│  │  TrainingVessel + Checkpoint         ││
│  │  + EarlyStopping                     ││
│  └─────────────────────────────────────┘│
└─────────────────────────────────────────┘
```

### 11.3 订单执行 RL

```python
# 单资产订单执行 (SAOE)
from qlib.rl.order_execution import SAOEStrategy

# 状态空间: 订单簿信息 + 历史价格 + 波动率 + 已执行比例
# 动作空间: 每个时间步的执行数量
# 奖励函数: 执行价格优化 + 完成率
```

### 11.4 投资组合 RL

适用场景：
- 股票市场组合优化
- **加密货币组合配置**（官方文档明确提及）
- 外汇市场资产配置

---

## 12. 元学习框架

### 12.1 概述

Meta Controller 模块学习**一系列预测任务间的规律模式**，用于指导后续预测任务。

### 12.2 三大组件

#### Meta Task（元任务）

```python
from qlib.workflow.task.meta import MetaTask

# 三种模式
MetaTask.FULL       # 完整模式（训练+测试）
MetaTask.TEST       # 测试模式
MetaTask.TRANSFER   # 迁移模式
```

#### Meta Dataset（元数据集）

管理元任务列表，为元模型提供训练数据。

#### Meta Model（元模型）

两种子类型：

1. **MetaTaskModel**: 直接修改任务定义（如调整数据集时间窗口）
2. **MetaGuideModel**: 参与基础模型训练过程，提供实时指导

### 12.3 应用：DDG-DA

Domain-Driven Generalization with Domain Adaptation（DDG-DA）：
1. 收集历史任务数据
2. 训练元模型识别市场动态变化
3. 自适应调整基础模型配置
4. 应用到新的市场环境

**对加密货币的意义**: 加密市场的非平稳性极强，元学习可帮助模型快速适应市场状态切换。

---

## 13. RD-Agent 自动化研发

### 13.1 概述

RD-Agent-Quant（R&D-Agent(Q)）是 2024-2025 年 QLib 最重要的更新，由微软亚洲研究院推出，论文已被 NeurIPS 2025 接收。

核心理念：**用 AI 驱动 AI**，通过 LLM 驱动的多智能体框架自动化量化研发过程。

### 13.2 五大功能单元

```
┌──────────────────────────────────────────────┐
│              RD-Agent-Quant                    │
│                                                │
│  ┌──────────┐    ┌──────────┐                 │
│  │ 规范单元  │───→│ 构思单元  │                 │
│  │Spec Unit │    │Synthesis │                 │
│  │(统一约束) │    │(假设生成) │                 │
│  └──────────┘    └────┬─────┘                 │
│                       ↓                        │
│               ┌──────────────┐                 │
│               │  实现单元     │                 │
│               │Implementation│                 │
│               │ (Co-STEER)   │                 │
│               └──────┬───────┘                 │
│                      ↓                         │
│               ┌──────────────┐                 │
│               │  验证单元     │                 │
│               │ Validation   │                 │
│               │(去重+回测)    │                 │
│               └──────┬───────┘                 │
│                      ↓                         │
│               ┌──────────────┐                 │
│               │  分析单元     │                 │
│               │  Analysis    │                 │
│               │(Bandit调度)   │                 │
│               └──────────────┘                 │
└──────────────────────────────────────────────┘
```

1. **规范单元**: 统一数据接口、输出格式、回测环境
2. **构思单元**: 基于"想法森林"自动生成因子/模型假设
3. **实现单元**: Co-STEER 代码智能体自动实现
4. **验证单元**: 因子去重 + 真实回测验证
5. **分析单元**: Thompson 采样调度器决定优化方向（因子 vs 模型）

### 13.3 实验效果

- 因子数量减少 70%+ 的同时提升 IC 和年化收益
- 在 CSI 500 和 NASDAQ 100 市场均取得领先表现
- Bandit 调度器在有限算力下最大化整体增益

### 13.4 使用方式

```bash
# 自动因子挖掘
rdagent fin_factor

# 自动模型优化
rdagent fin_model

# 从研报中提取因子
rdagent fin_factor_report
```

---

## 14. 信号分析与诊断工具

### 14.1 持仓分析 (analysis_position)

```python
from qlib.contrib.report import analysis_position

# 1. 投资组合报告
analysis_position.report_graph(report_normal_df, report_long_short_df)
# 输出: 累积收益曲线、基准对比、回撤曲线

# 2. 信号 IC 分析
analysis_position.score_ic_graph(pred_label)
# 输出: 皮尔逊相关系数、秩相关系数

# 3. 累积收益曲线
analysis_position.cumulative_return_graph(report_df)

# 4. 风险分析
analysis_position.risk_analysis_graph(report_df)
# 输出: 月度标准差、年化收益、信息比率、最大回撤

# 5. 排名标签分析
analysis_position.rank_label_graph(report_df)
```

### 14.2 模型表现分析 (analysis_model)

```python
from qlib.contrib.report import analysis_model

analysis_model.model_performance_graph(pred_label, lag=1, N=5)
# 输出:
# - 5 分位组合累积收益
# - 多空策略收益差
# - IC 时间序列
# - 月度 IC 热力图
# - 预测自相关性
```

### 14.3 关键指标说明

| 指标 | 全称 | 说明 |
|------|------|------|
| IC | Information Coefficient | 预测值与实际收益的相关系数 |
| Rank IC | Rank Information Coefficient | 基于排名的 IC，更稳健 |
| ICIR | IC Information Ratio | IC均值/IC标准差，衡量IC稳定性 |
| CAR | Cumulative Abnormal Return | 累积超额收益 |
| MDD | Maximum Drawdown | 最大回撤 |

---

## 15. 任务管理系统

### 15.1 四阶段流程

```
任务生成(TaskGen)
    ↓
任务存储(TaskManager/MongoDB)
    ↓
任务训练(Trainer/TrainerRM)
    ↓
结果收集(Collector/Group/Ensemble)
```

### 15.2 任务状态

| 状态 | 说明 |
|------|------|
| WAITING | 等待训练 |
| RUNNING | 训练中 |
| PART_DONE | 部分完成 |
| DONE | 全部完成 |

### 15.3 滚动任务生成

```python
from qlib.workflow.task.gen import RollingGen

gen = RollingGen(
    step=20,          # 每20天滚动一次
    rtype="expanding"  # 扩展窗口（或 "sliding" 滑动窗口）
)
tasks = gen.generate(task_template)
```

### 15.4 结果集成

```python
from qlib.model.ens.ensemble import RollingEnsemble

# 滚动集成：使用每个时间段最新模型的预测
ensemble = RollingEnsemble()
final_pred = ensemble(pred_dict)
```

---

## 16. Point-in-Time 数据库

### 16.1 问题背景

金融数据（特别是财报数据）可能多次修订。如果在回测中直接使用最新版本的数据，会导致**数据泄露**（look-ahead bias）。

### 16.2 PIT 解决方案

PIT 数据库记录每个数据点的所有历史版本和发布时间，确保在回测中获取**历史任意时刻的正确数据版本**。

### 16.3 文件格式

```
每条记录: [date, period, value, _next]
- date: 数据发布日期
- period: 数据所属周期（年度/季度）
- value: 数据值
- _next: 下一版本的字节索引
```

### 16.4 限制

- 仅支持季度或年度因子
- 计算方式尚有优化空间
- 对加密货币意义较小（加密市场无财报修订问题）

---

## 17. 序列化机制

### 17.1 Serializable 基类

```python
from qlib.utils.serial import Serializable

class MyComponent(Serializable):
    # 不以 _ 开头的属性会被自动序列化
    param1 = 100
    _cache = {}  # 不会被序列化

    def to_pickle(self, path="component.pkl"):
        """保存到磁盘"""
        ...
```

### 17.2 后端

- **pickle**（默认）: 标准 Python 序列化
- **dill**: 支持函数等复杂对象

### 17.3 注意事项

- 仅保存状态（如归一化参数），**不保存实际数据**
- 重新加载后需要重新初始化

---

## 18. 加密货币适配分析

### 18.1 QLib 对加密货币的支持现状

| 能力 | 支持程度 | 说明 |
|------|---------|------|
| 数据收集 | ⚠️ 有限 | CoinGecko 收集器已合并，但缺少 OHLC |
| 数据格式转换 | ✅ 支持 | 可将 CSV 转换为 QLib 格式 |
| 特征工程 | ✅ 完全适用 | Alpha158/360 基于 OHLCV，完全适用 |
| 模型训练 | ✅ 完全适用 | 所有模型均可用于加密货币 |
| 回测 | ⚠️ 需适配 | 需自定义交易规则（24/7 交易、无涨跌停） |
| RL 模块 | ✅ 官方支持 | 文档明确提及加密货币场景 |
| 在线服务 | ⚠️ 需适配 | 需自定义数据更新频率 |

### 18.2 需要自定义的部分

#### 1. 数据收集器

需要开发 Hyperliquid 永续合约数据收集器：

```python
class HyperliquidDataCollector:
    """从 Hyperliquid DEX 收集永续合约数据"""

    def collect(self, symbols, start_date, end_date, freq="1h"):
        # 从 Hyperliquid API 获取 OHLCV 数据
        # 转换为 QLib 格式
        ...
```

#### 2. 交易日历

加密货币 24/7 交易，需自定义日历：

```python
# 加密货币没有休市日
# 需生成连续的交易日历
crypto_calendar = generate_continuous_calendar(
    start="2020-01-01",
    end="2026-12-31",
    freq="1h"  # 小时级别
)
```

#### 3. 交易规则

```python
exchange_kwargs = {
    "freq": "1h",              # 小时频
    "limit_threshold": None,    # 无涨跌停
    "deal_price": "close",
    "open_cost": 0.0002,        # Hyperliquid 手续费
    "close_cost": 0.0002,
    "min_cost": 0,
    "trade_unit": None,         # 无最小交易单位限制
}
```

#### 4. 永续合约特有因子

需要扩展 Alpha158 加入永续合约特有特征：

```python
perpetual_features = [
    "funding_rate",              # 资金费率
    "open_interest",             # 未平仓合约量
    "long_short_ratio",          # 多空比
    "liquidation_volume",        # 清算量
    "basis",                     # 基差（现货-期货价差）
    "mark_price_deviation",      # 标记价格偏差
]
```

### 18.3 社区经验

- GitHub Issue #107: 期货交易支持仍为开放状态
- GitHub Issue #927: 比特币预测需求
- PR #733: CoinGecko 数据收集器已合并
- 社区反馈：将 QLib 用于加密货币需要较多自定义工作

---

## 19. 与现有 Quant Flow 的对比

### 19.1 决策机制对比

| 维度 | 当前 Quant Flow | QLib 方案 |
|------|----------------|-----------|
| 决策核心 | LLM Prompt Engineering | ML/DL 模型预测 |
| 随机性 | 高（LLM 输出不确定） | 低（模型输出确定性） |
| 可解释性 | 中（LLM 给出理由） | 高（特征重要性、IC 分析） |
| 回测验证 | 简单模拟回测 | 严格的统计回测框架 |
| 特征工程 | 手动计算少量指标 | 158-360 个自动化因子 |
| 模型选择 | 无（纯 LLM） | 20+ 种 ML/DL 模型 |
| 超参优化 | 无 | 支持网格搜索和自动调参 |
| 信号评估 | 无系统化评估 | IC/IR/夏普等多维评估 |
| 在线更新 | 无模型更新 | 滚动更新、增量学习 |

### 19.2 两者互补关系

```
┌──────────────────────────────────────────────────────┐
│                    QLib 核心决策层                     │
│  数据处理 → 因子计算 → 模型预测 → 策略生成            │
│  (确定性的、可回测的、可量化的决策基础)                │
└──────────────────────┬───────────────────────────────┘
                       ↓
┌──────────────────────────────────────────────────────┐
│                    LLM 辅助层                         │
│  市场情报解读 → 异常事件判断 → 执行策略微调           │
│  (非结构化信息处理、定性分析补充)                      │
└──────────────────────┬───────────────────────────────┘
                       ↓
┌──────────────────────────────────────────────────────┐
│                    执行层                             │
│  Hyperliquid 交易执行 → 风控管理 → 监控通知           │
│  (沿用现有的成熟执行基础设施)                         │
└──────────────────────────────────────────────────────┘
```

### 19.3 可复用的现有模块

| 模块 | 复用程度 | 说明 |
|------|---------|------|
| HyperliquidClient | ✅ 完全复用 | 交易执行层不变 |
| OrderManager | ✅ 完全复用 | 订单管理不变 |
| AccountProtector | ✅ 完全复用 | 账户保护不变 |
| 通知系统 | ✅ 完全复用 | 通知机制不变 |
| 日志系统 | ✅ 完全复用 | 日志记录不变 |
| MarketData | 🔄 需重构 | 适配 QLib 数据格式 |
| Indicators | ❌ 替换 | QLib 表达式引擎替代 |
| DecisionValidator | 🔄 需重构 | 基于 QLib 信号的验证 |
| PositionSizer | 🔄 需增强 | 结合 QLib 风险模型 |
| RiskManager | 🔄 需增强 | 结合 QLib 回测指标 |
| LLM Agent | 🔄 降级为辅助 | 从主决策降为辅助分析 |
| 回测引擎 | ❌ 替换 | QLib 回测系统替代 |

---

## 20. 重构建议

### 20.1 总体架构方向

**核心原则**: QLib 负责量化决策，LLM 负责辅助分析。QLib 是主脑，LLM 是顾问。

```
新架构:

┌──────────────────────────────────────────────────────────┐
│                     QLib 决策核心                          │
│                                                            │
│  ┌────────────┐  ┌────────────┐  ┌────────────────────┐  │
│  │ 数据层      │  │ 因子层      │  │ 模型层             │  │
│  │ Hyperliquid │→│ Alpha158+  │→│ LightGBM/LSTM/     │  │
│  │ DataHandler │  │ 永续因子   │  │ Transformer        │  │
│  └────────────┘  └────────────┘  └────────┬───────────┘  │
│                                            ↓               │
│  ┌────────────────┐  ┌──────────────────────────────┐     │
│  │ 回测/验证系统   │  │ 策略层                       │     │
│  │ QLib Backtest  │←│ 信号 → 仓位 → 风控 → 订单    │     │
│  └────────────────┘  └──────────────┬───────────────┘     │
│                                      ↓                     │
│  ┌──────────────────────────────────────────────────┐     │
│  │ 在线服务: 模型滚动更新 + 增量学习                  │     │
│  └──────────────────────────────────────────────────┘     │
│                                      ↓                     │
│  ┌──────────────────────────────────────────────────┐     │
│  │ RD-Agent: 自动因子挖掘 + 模型优化                  │     │
│  └──────────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────────┐
│                     LLM 辅助层                            │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────┐ │
│  │ 市场情报分析  │  │ 异常事件解读  │  │ 定性因子补充   │ │
│  │ (Exa API)    │  │ (新闻/公告)  │  │ (情感/热度)    │ │
│  └──────────────┘  └──────────────┘  └────────────────┘ │
└──────────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────────┐
│                     执行层 (复用现有)                      │
│  HyperliquidClient + OrderManager + AccountProtector      │
│  + 通知系统 + 日志系统                                     │
└──────────────────────────────────────────────────────────┘
```

### 20.2 需要全面应用的 QLib 能力清单

| 序号 | QLib 能力 | 应用方式 |
|------|----------|---------|
| 1 | 数据层 (DataHandler) | 自定义 Hyperliquid DataHandler，适配永续合约数据 |
| 2 | 表达式引擎 | 替代现有手工技术指标，使用 Alpha158+ 自定义因子 |
| 3 | 内置模型 (20+) | 多模型对比，选择最优预测模型 |
| 4 | 回测系统 | 替代现有简单回测，使用严格统计回测 |
| 5 | 信号分析 | IC/IR/夏普等多维信号诊断 |
| 6 | 策略系统 | 基于预测信号的量化策略 |
| 7 | 在线服务 | 模型滚动更新、增量学习 |
| 8 | 强化学习 | 订单执行优化、组合构建 |
| 9 | 元学习 | 适应加密市场状态切换 |
| 10 | 实验管理 | 系统化追踪模型实验 |
| 11 | 任务管理 | 自动化滚动训练 |
| 12 | RD-Agent | 自动因子挖掘和模型优化 |
| 13 | 嵌套决策 | 多层级决策（日频方向+小时级执行） |
| 14 | PIT 数据 | 避免回测中的数据泄露 |
| 15 | 序列化 | 模型和数据处理器的持久化 |

### 20.3 重构阶段规划

#### 第一阶段：数据基础设施

- 开发 Hyperliquid 数据收集器
- 实现 CSV → QLib 二进制格式转换
- 创建加密货币交易日历（24/7）
- 构建永续合约特有因子（资金费率、未平仓量等）
- 适配 Alpha158 因子集

#### 第二阶段：模型与预测

- 部署 QLib 模型训练管线
- 多模型对比评估（LightGBM, LSTM, Transformer 等）
- 信号分析和因子有效性验证
- 建立模型评估基准

#### 第三阶段：策略与回测

- 基于 QLib 信号构建交易策略
- 利用 QLib 回测系统进行严格验证
- 整合现有风控模块（仓位管理、止盈止损、账户保护）
- 嵌套决策框架（日频趋势+小时级执行）

#### 第四阶段：在线服务

- 部署 QLib 在线服务模块
- 实现模型滚动更新
- 增量学习适应市场变化
- 与 Hyperliquid 交易执行对接

#### 第五阶段：高级功能

- 集成 RD-Agent 自动化因子挖掘
- 部署 RL 模块优化订单执行
- 元学习适应市场状态切换
- LLM 辅助层集成（情报分析、异常事件解读）

#### 第六阶段：LLM 辅助集成

- LLM 作为市场情报分析器
- LLM 辅助解读异常市场事件
- LLM 生成定性因子（情感、热度）
- LLM 辅助策略解释和报告生成

---

## 参考资料

- QLib GitHub: https://github.com/microsoft/qlib
- QLib 文档: https://qlib.readthedocs.io/en/latest/
- RD-Agent GitHub: https://github.com/microsoft/RD-Agent
- RD-Agent-Quant 论文: https://arxiv.org/html/2505.15155v2
- QLib 论文: https://www.microsoft.com/en-us/research/publication/qlib-an-ai-oriented-quantitative-investment-platform/
- 加密货币数据 PR: https://github.com/microsoft/qlib/pull/733
- 期货交易 Issue: https://github.com/microsoft/qlib/issues/107
