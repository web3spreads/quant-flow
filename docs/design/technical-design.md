# Quant Flow × QLib 重构技术方案

> **版本**: v1.0
> **日期**: 2026-02-24
> **状态**: 实施中

---

## 1. 设计原则

### 1.1 核心理念

**QLib 为主脑，LLM 为顾问**

- QLib 提供数据驱动的、可量化的、可回测的决策基础
- LLM 降级为辅助角色（市场情报、异常事件、定性分析）
- 现有执行层（HyperliquidClient、OrderManager、AccountProtector）完全复用

### 1.2 设计约束

- 保持与现有 Hyperliquid 执行层的兼容性
- 加密货币 24/7 不间断交易
- 永续合约特有数据（资金费率、未平仓量等）
- 不依赖 QLib 的股票市场假设（涨跌停、交易单位等）

---

## 2. 新架构总览

```
┌─────────────────────────────────────────────────────────────┐
│                    src/qlib_engine/                          │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 数据层 (data/)                                        │   │
│  │  collector.py    → Hyperliquid OHLCV 数据收集         │   │
│  │  handler.py      → CryptoAlpha158 DataHandler         │   │
│  │  calendar.py     → 加密货币 24/7 交易日历              │   │
│  │  perpetual.py    → 永续合约特有因子                    │   │
│  └──────────────────────────────────────────────────────┘   │
│                         ↓                                    │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 模型层 (model/)                                       │   │
│  │  trainer.py      → 多模型训练管线                      │   │
│  │  predictor.py    → 信号预测器                          │   │
│  │  evaluator.py    → 模型评估和信号分析                  │   │
│  └──────────────────────────────────────────────────────┘   │
│                         ↓                                    │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 策略层 (strategy/)                                    │   │
│  │  signal_strategy.py  → 基于 QLib 信号的交易策略        │   │
│  │  risk_integrator.py  → 风控模块集成                    │   │
│  └──────────────────────────────────────────────────────┘   │
│                         ↓                                    │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 引擎层 (engine/)                                      │   │
│  │  qlib_engine.py  → QLib 核心引擎（初始化/调度/协调）   │   │
│  │  online.py       → 在线服务（模型更新/增量学习）       │   │
│  │  experiment.py   → 实验管理                            │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│              现有执行层 (src/trading/) [复用]                 │
│  HyperliquidClient + OrderManager + AccountProtector         │
│  + RiskManager + PositionSizer + DecisionValidator           │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. 模块详细设计

### 3.1 数据层

#### 3.1.1 数据收集器 (`collector.py`)

从 Hyperliquid API 收集数据并转换为 QLib 可用的 DataFrame 格式。

```python
class HyperliquidDataCollector:
    """从 Hyperliquid 收集永续合约数据，输出 QLib 格式"""

    def collect_ohlcv(self, symbols, start_date, end_date, freq="1h") -> pd.DataFrame:
        """收集 OHLCV 数据，返回 MultiIndex DataFrame (datetime, instrument)"""

    def collect_perpetual_features(self, symbols, start_date, end_date) -> pd.DataFrame:
        """收集永续合约特有特征（资金费率、未平仓量等）"""

    def to_qlib_format(self, df) -> pd.DataFrame:
        """转换为 QLib 标准格式 (MultiIndex: datetime × instrument)"""
```

#### 3.1.2 加密货币日历 (`calendar.py`)

```python
class CryptoCalendar:
    """加密货币 24/7 交易日历生成器"""

    def generate(self, start, end, freq="1h") -> list[str]:
        """生成连续交易时间序列"""
```

#### 3.1.3 DataHandler (`handler.py`)

基于 QLib 的 DataHandlerLP，扩展 Alpha158 因子集以适配加密货币永续合约。

```python
class CryptoAlpha158(DataHandlerLP):
    """加密货币永续合约专用 DataHandler"""

    # 基础 Alpha158 因子 + 永续合约特有因子
    # 标签：未来 N 期收益率
```

#### 3.1.4 永续合约因子 (`perpetual.py`)

```python
# 永续合约特有因子定义
PERPETUAL_FEATURES = {
    "funding_rate":           "资金费率",
    "open_interest":          "未平仓合约量",
    "open_interest_change":   "未平仓量变化率",
    "long_short_ratio":       "多空比",
    "liquidation_volume":     "清算量",
    "basis":                  "基差",
    "volume_ratio":           "成交量比率",
}
```

### 3.2 模型层

#### 3.2.1 模型训练管线 (`trainer.py`)

```python
class QLibModelTrainer:
    """多模型训练、评估和选择"""

    def train(self, dataset, model_type="lightgbm") -> Model:
        """训练单个模型"""

    def train_all(self, dataset) -> dict[str, Model]:
        """训练所有候选模型并对比"""

    def evaluate(self, model, dataset) -> dict:
        """评估模型性能 (IC/ICIR/年化收益/夏普/回撤)"""

    def select_best(self, results) -> str:
        """根据 ICIR 选择最优模型"""
```

支持的模型：
- LightGBM（默认首选，训练快、效果好）
- Linear（基线模型）
- LSTM / GRU（时序深度学习）
- Transformer（注意力机制）

#### 3.2.2 信号预测器 (`predictor.py`)

```python
class SignalPredictor:
    """基于训练好的模型生成交易信号"""

    def predict(self, model, latest_data) -> pd.Series:
        """对最新数据生成预测信号"""

    def get_signal_strength(self, score) -> float:
        """将预测分数转换为标准化的信号强度 [-1, 1]"""

    def get_trading_direction(self, score) -> str:
        """根据信号确定交易方向 (long/short/neutral)"""
```

### 3.3 策略层

#### 3.3.1 信号策略 (`signal_strategy.py`)

```python
class QLibSignalStrategy:
    """基于 QLib 模型信号的交易策略"""

    def generate_decision(self, signal, market_context) -> TradeDecision:
        """
        根据 QLib 信号和市场上下文生成交易决策
        整合现有风控模块：
        - DecisionValidator: 多维度验证
        - PositionSizer: 凯利公式仓位
        - RiskManager: ATR 止盈止损
        """
```

#### 3.3.2 风控集成器 (`risk_integrator.py`)

```python
class RiskIntegrator:
    """将 QLib 信号与现有风控模块对接"""

    def apply_risk_controls(self, signal, decision) -> TradeDecision:
        """应用风控规则到交易决策"""
```

### 3.4 引擎层

#### 3.4.1 QLib 核心引擎 (`qlib_engine.py`)

```python
class QuantFlowQLibEngine:
    """QLib 核心引擎 - 系统的中枢控制器"""

    def initialize(self, config):
        """初始化 QLib 环境和组件"""

    def prepare_data(self, symbols, lookback_days=365):
        """准备数据（收集 + 处理 + 构建因子）"""

    def train_models(self):
        """训练模型"""

    def predict(self, symbol) -> dict:
        """对指定交易对生成预测信号"""

    def generate_trade_decision(self, symbol) -> TradeDecision:
        """生成最终交易决策（QLib信号 + 风控）"""
```

#### 3.4.2 在线服务 (`online.py`)

```python
class OnlineModelManager:
    """在线模型管理 - 滚动更新和增量学习"""

    def should_retrain(self) -> bool:
        """检查是否需要重新训练"""

    def rolling_update(self):
        """滚动更新模型"""

    def update_predictions(self):
        """增量更新预测"""
```

---

## 4. 数据流设计

```
Hyperliquid API
    │
    ├── OHLCV K线 (1h/4h/1d)
    ├── 资金费率
    ├── 未平仓量
    └── 订单簿摘要
    │
    ↓
HyperliquidDataCollector
    │
    ↓
MultiIndex DataFrame (datetime × instrument)
    │ columns: $open, $high, $low, $close, $volume,
    │          $funding_rate, $open_interest, ...
    │
    ↓
CryptoAlpha158 DataHandler
    │
    ├── 特征工程 (158+ 因子)
    │   ├── K线特征: KMID, KLEN, KLOW, KHIGH, ...
    │   ├── 价格特征: 多窗口涨跌幅
    │   ├── 滚动特征: ROC, MA, STD, BETA, RSQR, RESI, ...
    │   ├── 成交量特征: WVMA, VSTD, ...
    │   └── 永续特有: 资金费率衍生因子, 未平仓量衍生因子
    │
    ├── 数据处理
    │   ├── RobustZScoreNorm (鲁棒标准化)
    │   ├── Fillna (缺失值填充)
    │   └── CSRankNorm (截面排名归一化)
    │
    └── 标签生成
        └── 未来 N 期收益率
    │
    ↓
DatasetH (train/valid/test 分割)
    │
    ↓
模型训练 → 预测信号 (score per instrument)
    │
    ↓
SignalStrategy → 交易方向 + 强度
    │
    ↓
RiskIntegrator (DecisionValidator + PositionSizer + RiskManager)
    │
    ↓
TradeDecision → OrderManager → HyperliquidClient
```

---

## 5. 配置设计

在现有 `config.yaml` 中新增 `qlib` 配置块：

```yaml
qlib:
  # 数据配置
  data:
    provider_uri: "~/.qlib/crypto_data"     # QLib 数据存储路径
    freq: "1h"                               # 默认数据频率
    lookback_days: 365                       # 历史数据回溯天数
    symbols: [BTC, ETH, SOL]                 # 交易标的

  # 因子配置
  features:
    handler: "CryptoAlpha158"                # DataHandler 类型
    include_perpetual: true                  # 是否包含永续合约因子
    label_rule: "Ref($close, -5)/Ref($close, -1) - 1"  # 标签定义

  # 模型配置
  model:
    default: "lightgbm"                      # 默认模型
    candidates:                              # 候选模型列表
      - lightgbm
      - linear
    lightgbm:
      loss: mse
      learning_rate: 0.05
      num_leaves: 128
      max_depth: 6
      colsample_bytree: 0.85

  # 策略配置
  strategy:
    signal_threshold: 0.3                    # 信号阈值（绝对值）
    rebalance_freq: "1h"                     # 调仓频率
    max_positions: 3                         # 最大持仓数

  # 在线服务配置
  online:
    retrain_interval_hours: 168              # 模型重训练间隔（7天）
    rolling_window_days: 180                 # 滚动窗口
    min_train_samples: 1000                  # 最小训练样本数

  # 信号与现有风控的融合权重
  risk_integration:
    qlib_signal_weight: 0.7                  # QLib 信号权重
    existing_risk_weight: 0.3                # 现有风控权重
```

---

## 6. 目录结构

```
src/
├── qlib_engine/                  # 新增：QLib 引擎核心
│   ├── __init__.py
│   ├── data/                     # 数据层
│   │   ├── __init__.py
│   │   ├── collector.py          # Hyperliquid 数据收集器
│   │   ├── handler.py            # CryptoAlpha158 DataHandler
│   │   ├── calendar.py           # 加密货币交易日历
│   │   └── perpetual.py          # 永续合约特有因子
│   ├── model/                    # 模型层
│   │   ├── __init__.py
│   │   ├── trainer.py            # 多模型训练管线
│   │   ├── predictor.py          # 信号预测器
│   │   └── evaluator.py          # 模型评估
│   ├── strategy/                 # 策略层
│   │   ├── __init__.py
│   │   ├── signal_strategy.py    # 信号交易策略
│   │   └── risk_integrator.py    # 风控集成
│   └── engine/                   # 引擎层
│       ├── __init__.py
│       ├── qlib_engine.py        # 核心引擎
│       ├── online.py             # 在线服务
│       └── experiment.py         # 实验管理
├── trading/                      # 现有：复用
├── data/                         # 现有：保留兼容
├── agent/                        # 现有：LLM 辅助（降级）
└── ...
```

---

## 7. 与现有系统的集成方式

### 7.1 main.py 改造

```python
# 新增 QLib 引擎初始化
qlib_engine = QuantFlowQLibEngine(config)
qlib_engine.initialize()

# 交易循环改造：
# 旧: LLM Agent 直接决策
# 新: QLib 信号 → 风控验证 → 执行
for symbol in symbols:
    # QLib 生成预测信号
    prediction = qlib_engine.predict(symbol)

    # 生成交易决策（集成现有风控）
    decision = qlib_engine.generate_trade_decision(symbol)

    # 执行交易（复用现有 OrderManager）
    if decision.should_trade:
        order_manager.execute(decision)
```

### 7.2 保持向后兼容

- 通过 `config.yaml` 中 `qlib.enabled: true/false` 切换新旧模式
- 旧 LLM Agent 模式保留，可作为对比基准
- 数据层同时支持旧格式和 QLib 格式
