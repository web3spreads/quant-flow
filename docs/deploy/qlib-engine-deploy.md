# QLib 量化引擎部署文档

## 目录

1. [概述](#概述)
2. [环境要求](#环境要求)
3. [安装步骤](#安装步骤)
4. [配置说明](#配置说明)
5. [模块架构](#模块架构)
6. [使用指南](#使用指南)
7. [运维指南](#运维指南)
8. [测试验证](#测试验证)
9. [常见问题](#常见问题)

---

## 概述

QLib 量化引擎是 Quant Flow 项目的核心决策模块，基于微软 QLib 框架的设计理念构建。核心原则：

- **QLib 为主脑**：数据驱动、可量化、可回测的 ML 模型生成交易信号
- **LLM 为顾问**：情报分析、定性补充，不再作为主要决策者

引擎实现了从数据采集到交易决策的完整闭环：

```
数据收集 → Alpha 因子计算 → 模型训练/预测 → 信号生成 → 策略决策 → 风控验证
```

## 环境要求

### Python 版本

- Python 3.11+（项目最低要求）

### 系统依赖

| 依赖 | 版本要求 | 用途 |
|------|---------|------|
| pandas | >= 2.3.3 | 数据处理核心 |
| numpy | >= 2.3.5 | 数值计算 |
| scikit-learn | >= 1.4.0 | 线性模型（Ridge）、数据预处理 |
| lightgbm | >= 4.0.0 | 梯度提升树模型（主力模型） |
| xgboost | >= 2.0.0 | XGBoost 模型（候选模型） |
| hyperliquid-python-sdk | >= 0.21.0 | Hyperliquid 交易所数据和执行 |

### 硬件建议

| 场景 | CPU | 内存 | 磁盘 |
|------|-----|------|------|
| 开发/测试 | 2 核 | 4 GB | 10 GB |
| 生产（单交易对） | 2 核 | 4 GB | 20 GB |
| 生产（多交易对） | 4 核 | 8 GB | 50 GB |

> 注意：LightGBM 和 XGBoost 训练时会使用多线程，默认 4 线程。

## 安装步骤

### 1. 安装 uv（如尚未安装）

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. 安装基础依赖

```bash
# 从项目根目录
uv sync
```

这会自动安装 `pyproject.toml` 中定义的所有依赖，包括新增的 `lightgbm`、`scikit-learn`、`xgboost`。

### 3. 验证安装

```bash
# 验证核心模块可导入
uv run python -c "from src.qlib_engine import QuantFlowQLibEngine; print('QLib 引擎安装成功')"

# 验证 ML 库
uv run python -c "import lightgbm; import xgboost; import sklearn; print('ML 库安装成功')"
```

### 4. 运行测试

```bash
# 运行所有 QLib 引擎测试（96 个测试用例）
uv run pytest tests/test_qlib_engine.py -v

# 快速检查
uv run pytest tests/test_qlib_engine.py --tb=short
```

### 5. 准备目录结构

引擎运行时会创建以下目录（自动创建）：

```
models/qlib/          # 训练好的模型文件（.pkl）
experiments/qlib/     # 实验记录（JSON 文件）
```

## 配置说明

在 `config.yaml` 中添加 `qlib` 配置段。完整配置项参考 `config.yaml.example`。

### 最小配置

```yaml
qlib:
  enabled: true
```

使用默认值即可启动，以下为可调参数。

### 核心配置详解

```yaml
qlib:
  # 总开关
  enabled: true

  # === 数据层 ===
  data:
    freq: 1h                    # 数据频率（建议 1h 平衡信息量与噪声）
    candles_limit: 500          # 历史 K 线数量（影响训练样本量）
    include_perpetual: true     # 是否包含永续合约特有因子
    label_periods: 5            # 标签预测期数（预测未来 5 根 K 线的收益率）

  # === 模型层 ===
  model:
    model_dir: models/qlib      # 模型保存路径
    candidates:                 # 候选模型（训练时全部训练，选最优）
      - lightgbm                # 推荐：表现稳定
      - xgboost                 # 备选
      - linear                  # 基线模型

  # === 策略层 ===
  strategy:
    signal_threshold: 0.3       # 信号阈值（低于此值视为中性，不交易）
    max_position_pct: 0.3       # 单笔最大仓位占比
    default_stop_loss_pct: 0.02 # 默认止损 2%
    default_take_profit_pct: 0.06  # 默认止盈 6%
    min_confidence: 0.3         # 最低置信度

  # === 风控集成 ===
  risk_integration:
    qlib_signal_weight: 0.7     # QLib 信号权重（70% QLib + 30% 传统风控）

  # === 在线学习 ===
  online:
    retrain_interval_hours: 168 # 重训练间隔（168 = 1 周）
    rolling_window: 2000        # 滚动训练窗口
    min_retrain_samples: 200    # 最小样本数
    max_model_versions: 10      # 保留模型版本数

  # === 实验管理 ===
  experiment:
    experiment_dir: experiments/qlib
```

### 参数调优建议

| 参数 | 保守 | 默认 | 激进 |
|------|------|------|------|
| `signal_threshold` | 0.5 | 0.3 | 0.2 |
| `max_position_pct` | 0.15 | 0.3 | 0.5 |
| `default_stop_loss_pct` | 0.015 | 0.02 | 0.03 |
| `default_take_profit_pct` | 0.04 | 0.06 | 0.10 |
| `qlib_signal_weight` | 0.5 | 0.7 | 0.9 |
| `retrain_interval_hours` | 336 | 168 | 72 |

## 模块架构

### 目录结构

```
src/qlib_engine/
├── __init__.py                     # 模块入口（延迟导入）
├── data/                           # 数据层
│   ├── __init__.py
│   ├── calendar.py                 # 24/7 加密货币日历
│   ├── perpetual.py                # 永续合约因子定义（60+ 因子）
│   ├── collector.py                # Hyperliquid 数据收集器
│   └── handler.py                  # CryptoAlpha158 因子处理器
├── model/                          # 模型层
│   ├── __init__.py
│   ├── trainer.py                  # 多模型训练管线
│   ├── evaluator.py                # 模型评估器（IC/ICIR/夏普等）
│   └── predictor.py                # 信号预测器
├── strategy/                       # 策略层
│   ├── __init__.py
│   ├── signal_strategy.py          # QLib 信号交易策略
│   └── risk_integrator.py          # 风控集成器
└── engine/                         # 引擎层
    ├── __init__.py
    ├── qlib_engine.py              # 核心引擎（统一调度）
    ├── online.py                   # 在线模型管理
    └── experiment.py               # 实验追踪
```

### 数据流

```
Hyperliquid API
    ↓
HyperliquidDataCollector（收集 OHLCV + 永续合约数据）
    ↓
CryptoAlpha158（计算 60+ Alpha 因子 → Z-Score 标准化）
    ↓
QLibModelTrainer（训练 LightGBM/XGBoost/Linear → 选择最优模型）
    ↓
SignalPredictor（模型原始分数 → 标准化 → 方向/强度/置信度）
    ↓
QLibSignalStrategy（信号 → 交易动作 + 仓位大小 + 止盈止损）
    ↓
RiskIntegrator（QLib 决策 × 0.7 + 传统风控 × 0.3 → 最终决策）
    ↓
OrderManager（执行交易）
```

### 因子体系

引擎内置 60+ Alpha 因子，分为以下类别：

| 类别 | 数量 | 说明 |
|------|------|------|
| K 线形态因子 | 7 | KMID、KLEN、KSFT、KUP、KLOW 等 |
| 价格动量因子（ROC） | 8 | 1/2/3/5/10/20/30/60 期 |
| 均线偏离因子 | 5 | 5/10/20/30/60 期 MA 偏离 |
| 波动率因子（CV） | 5 | 5/10/20/30/60 期变异系数 |
| 滚动统计因子 | 15 | VSTD、VWSTD、POSITION 各 5 期 |
| 价量相关性因子 | 4 | 5/10/20/60 期 |
| 永续合约因子 | 18+ | 资金费率、未平仓量、溢价率衍生因子 |

## 使用指南

### 基础用法

```python
from src.qlib_engine import QuantFlowQLibEngine

# 1. 创建引擎
engine = QuantFlowQLibEngine(config=qlib_config)

# 2. 初始化（传入现有风控模块，实现无缝集成）
engine.initialize(
    testnet=True,
    decision_validator=decision_validator,
    position_sizer=position_sizer,
    risk_manager=risk_manager,
    account_protector=account_protector,
)

# 3. 训练模型（首次启动或定期重训练）
train_result = engine.prepare_and_train(
    symbols=["BTC", "ETH", "SOL"],
    freq="1h",
    limit=500,
)
print(f"最优模型: {train_result['best_model']}")

# 4. 交易循环中生成决策
decision = engine.generate_trade_decision(
    symbol="BTC",
    current_position=current_position,
    account_balance=account_balance,
    account_info=account_info,
)

if decision.should_trade:
    print(f"执行: {decision.action}, 仓位: {decision.suggested_size_pct:.1%}")
    # 调用 OrderManager 执行
```

### 在线模型管理

```python
from src.qlib_engine.engine.online import OnlineModelManager

online_manager = OnlineModelManager(
    collector=engine.collector,
    handler=engine.handler,
    trainer=engine.trainer,
    evaluator=engine.evaluator,
    config=qlib_config.get("online", {}),
)

# 定期检查是否需要重训练
if online_manager.should_retrain():
    result = online_manager.rolling_retrain(symbols=["BTC", "ETH"])
    print(f"重训练完成: 是否切换={result['switched']}")
```

### 实验追踪

```python
from src.qlib_engine.engine.experiment import ExperimentManager

exp_manager = ExperimentManager(experiment_dir="experiments/qlib")

# 记录训练实验
record = exp_manager.start_experiment(
    experiment_type="train",
    tags={"model": "lightgbm", "symbols": "BTC,ETH"},
)
record.log_params({"learning_rate": 0.05, "num_leaves": 128})
record.log_metrics({"IC": 0.065, "ICIR": 1.23, "夏普比率": 2.1})
exp_manager.end_experiment()

# 查询历史实验
experiments = exp_manager.list_experiments(experiment_type="train", limit=10)
best = exp_manager.get_best_experiment(metric="ICIR")
```

## 历史数据回填

QLib 引擎依赖本地历史数据进行模型训练。首次部署前建议先回填足够的历史数据。

> 完整的回填指南参见 [backfill-guide.md](./backfill-guide.md)

### 本地运行

```bash
# 回填最近 90 天数据（默认）
uv run python backfill_qlib_data.py

# 回填 180 天，指定交易对
uv run python backfill_qlib_data.py --days 180 --symbols BTC ETH SOL

# 回填指定日期范围
uv run python backfill_qlib_data.py --start-date 2025-09-01 --end-date 2026-03-06

# 预览模式（不写入文件）
uv run python backfill_qlib_data.py --dry-run
```

### Docker 运行

```bash
# 默认参数回填
docker compose run --rm backfill

# 自定义参数
docker compose run --rm -e BACKFILL_ARGS="--days 180 --symbols BTC ETH SOL" backfill
```

### 首次部署推荐流程

```bash
# 1. 回填历史数据（建议 180 天以上）
docker compose run --rm -e BACKFILL_ARGS="--days 180" backfill

# 2. 确认数据文件
ls -lh data/qlib/

# 3. 启动交易服务（首次启动会自动训练模型）
docker compose up -d quant-flow
```

## 运维指南

### 模型生命周期

1. **首次部署**：启动时自动训练模型（`prepare_and_train`）
2. **定期重训练**：默认每 168 小时（1 周）重训练一次
3. **模型版本管理**：新模型需通过 ICIR 验证才会替换旧模型
4. **模型文件清理**：默认保留最近 10 个版本

### 监控指标

关注以下指标判断模型健康度：

| 指标 | 健康范围 | 说明 |
|------|---------|------|
| IC | > 0.02 | 预测值与实际收益的相关系数 |
| Rank IC | > 0.02 | 基于排名的 IC，更鲁棒 |
| ICIR | > 0.5 | IC 信息比率，衡量 IC 稳定性 |
| 夏普比率 | > 1.0 | 年化风险调整收益 |
| 最大回撤 | > -0.15 | 最大净值回撤（负值） |

### 模型退化处理

当模型指标持续恶化时：

1. 检查数据质量（API 是否正常返回数据）
2. 缩短重训练间隔（如从 168 小时缩短到 72 小时）
3. 增大滚动训练窗口（如从 2000 增加到 5000）
4. 调整 `qlib_signal_weight` 降低 QLib 权重

### 日志

引擎使用 `QuantFlow.QLib` 日志名称空间：

```python
import logging
logging.getLogger("QuantFlow.QLib").setLevel(logging.INFO)
```

关键日志事件：
- `模型训练完成` - 训练完成，含最优模型信息
- `信号生成` - 每次预测的信号方向和强度
- `交易决策` - 最终决策动作和参数
- `模型已切换到新版本` - 在线更新切换

## 测试验证

### 测试覆盖

测试文件：`tests/test_qlib_engine.py`，共 96 个测试用例：

| 测试类 | 用例数 | 覆盖模块 |
|-------|-------|---------|
| TestCryptoCalendar | 9 | 24/7 日历生成 |
| TestPerpetualFactors | 7 | 因子定义和配置 |
| TestCryptoAlpha158 | 9 | 因子计算和标准化 |
| TestQLibModelTrainer | 10 | 模型训练和持久化 |
| TestModelEvaluator | 6 | 模型评估指标 |
| TestSignalPredictor | 8 | 信号预测和方向判定 |
| TestTradingSignal | 4 | 信号数据结构 |
| TestQLibSignalStrategy | 9 | 交易策略决策 |
| TestRiskIntegrator | 4 | 风控集成 |
| TestExperimentManager | 7 | 实验管理 |
| TestOnlineModelManager | 11 | 在线模型管理 |
| TestQuantFlowQLibEngine | 9 | 核心引擎 |
| TestEndToEndPipeline | 3 | 端到端集成 |

### 运行测试

```bash
# 全部测试
uv run pytest tests/test_qlib_engine.py -v

# 指定模块测试
uv run pytest tests/test_qlib_engine.py -k "TestCryptoAlpha158" -v

# 集成测试
uv run pytest tests/test_qlib_engine.py -k "TestEndToEndPipeline" -v
```

## 常见问题

### Q: `ModuleNotFoundError: No module named 'lightgbm'`

```bash
uv sync  # 重新同步依赖
```

如果 uv sync 后仍然缺失，手动添加：
```bash
uv add lightgbm
```

如果安装失败（Linux 无编译环境），使用 conda：
```bash
conda install -c conda-forge lightgbm
```

### Q: `ModuleNotFoundError: No module named 'hyperliquid'`

数据收集器依赖 Hyperliquid SDK：
```bash
uv sync  # 重新同步依赖
```

### Q: 训练样本不足

增加 `candles_limit` 配置：
```yaml
qlib:
  data:
    candles_limit: 1000  # 增加到 1000
```

或降低频率（从 1h 改为 4h，每根 K 线包含更多信息）。

### Q: 如何禁用 QLib 引擎回退到纯 LLM 模式

```yaml
qlib:
  enabled: false
```

### Q: 模型训练很慢

1. 减少候选模型数量（只保留 `lightgbm`）
2. 减少训练数据量（降低 `candles_limit` 或 `rolling_window`）
3. 调整 LightGBM 参数（降低 `n_estimators` 或 `num_leaves`）

```yaml
qlib:
  model:
    candidates:
      - lightgbm    # 只训练一个模型
    lightgbm:
      n_estimators: 200   # 从 500 降低
      num_leaves: 64       # 从 128 降低
```
