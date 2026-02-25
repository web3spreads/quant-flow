"""
QLib 量化引擎全模块单元测试

覆盖范围：
- 数据层：CryptoCalendar, perpetual 因子, CryptoAlpha158, HyperliquidDataCollector
- 模型层：QLibModelTrainer, ModelEvaluator, SignalPredictor
- 策略层：QLibSignalStrategy, RiskIntegrator
- 引擎层：QuantFlowQLibEngine, OnlineModelManager, ExperimentManager
"""

import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from src.qlib_engine.data.calendar import CryptoCalendar
from src.qlib_engine.data.handler import CryptoAlpha158
from src.qlib_engine.data.perpetual import (
    CORRELATION_FEATURE_CONFIG,
    KBAR_FEATURE_CONFIG,
    MA_FEATURE_CONFIG,
    PERPETUAL_FEATURE_CONFIG,
    PERPETUAL_RAW_COLUMNS,
    PRICE_FEATURE_CONFIG,
    ROLLING_FEATURE_CONFIG,
    VOLATILITY_FEATURE_CONFIG,
    get_all_feature_config,
    get_feature_expressions,
    get_feature_names,
)
from src.qlib_engine.engine.experiment import ExperimentManager, ExperimentRecord
from src.qlib_engine.engine.qlib_engine import QuantFlowQLibEngine
from src.qlib_engine.model.evaluator import ModelEvaluator
from src.qlib_engine.model.predictor import SignalDirection, SignalPredictor, TradingSignal
from src.qlib_engine.model.trainer import QLibModelTrainer
from src.qlib_engine.strategy.risk_integrator import RiskIntegrator
from src.qlib_engine.strategy.signal_strategy import QLibSignalStrategy, TradeDecision

# ============================================================
# 公共 Fixtures
# ============================================================


@pytest.fixture
def sample_ohlcv_df():
    """生成模拟的单交易对 OHLCV 数据"""
    np.random.seed(42)
    n = 200
    dates = pd.date_range("2025-01-01", periods=n, freq="h")
    base_price = 50000.0
    close_prices = base_price + np.cumsum(np.random.randn(n) * 100)
    close_prices = np.maximum(close_prices, 1000)  # 保证正值

    df = pd.DataFrame(
        {
            "$open": close_prices + np.random.randn(n) * 50,
            "$high": close_prices + abs(np.random.randn(n) * 200),
            "$low": close_prices - abs(np.random.randn(n) * 200),
            "$close": close_prices,
            "$volume": np.random.uniform(1e6, 5e6, n),
        },
        index=dates,
    )
    df.index.name = "datetime"

    # 确保 high >= open, close 且 low <= open, close
    df["$high"] = df[["$open", "$high", "$close"]].max(axis=1) + 1
    df["$low"] = df[["$open", "$low", "$close"]].min(axis=1) - 1
    return df


@pytest.fixture
def sample_ohlcv_with_perpetual(sample_ohlcv_df):
    """带永续合约特征的 OHLCV 数据"""
    df = sample_ohlcv_df.copy()
    df["$funding_rate"] = np.random.uniform(-0.001, 0.001, len(df))
    df["$open_interest"] = np.random.uniform(1e8, 5e8, len(df))
    df["$premium"] = np.random.uniform(-0.005, 0.005, len(df))
    return df


@pytest.fixture
def sample_multiindex_df(sample_ohlcv_df):
    """生成 MultiIndex 格式的多交易对数据"""
    dfs = []
    for symbol in ["BTC", "ETH"]:
        df = sample_ohlcv_df.copy()
        if symbol == "ETH":
            df["$close"] = df["$close"] / 10  # ETH 价格低一些
            df["$open"] = df["$open"] / 10
            df["$high"] = df["$high"] / 10
            df["$low"] = df["$low"] / 10
        df["instrument"] = symbol
        df = df.reset_index()
        dfs.append(df)

    combined = pd.concat(dfs, ignore_index=True)
    combined = combined.set_index(["datetime", "instrument"])
    return combined.sort_index()


@pytest.fixture
def sample_features_and_labels():
    """生成模拟的特征和标签数据（用于模型训练）"""
    np.random.seed(42)
    n_samples = 300
    n_features = 20

    X = pd.DataFrame(
        np.random.randn(n_samples, n_features),
        columns=[f"feature_{i}" for i in range(n_features)],
    )
    # 标签与前几个特征有一定相关性（模拟可预测性）
    y = pd.Series(
        0.3 * X["feature_0"] + 0.2 * X["feature_1"] + 0.5 * np.random.randn(n_samples),
        name="label",
    )
    return X, y


@pytest.fixture
def tmp_dir():
    """临时目录（自动清理）"""
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d, ignore_errors=True)


# ============================================================
# 一、数据层测试
# ============================================================


class TestCryptoCalendar:
    """加密货币日历测试"""

    def test_init_valid_freq(self):
        """测试合法频率初始化"""
        for freq in ["1min", "5min", "15min", "30min", "1h", "4h", "1d"]:
            cal = CryptoCalendar(freq=freq)
            assert cal.freq == freq

    def test_init_invalid_freq(self):
        """测试非法频率报错"""
        with pytest.raises(ValueError, match="不支持的频率"):
            CryptoCalendar(freq="2h")

    def test_from_timeframe(self):
        """测试从 Hyperliquid timeframe 创建日历"""
        cal = CryptoCalendar.from_timeframe("1h")
        assert cal.freq == "1h"

        cal_4h = CryptoCalendar.from_timeframe("4h")
        assert cal_4h.freq == "4h"

    def test_from_timeframe_invalid(self):
        """测试非法 timeframe"""
        with pytest.raises(ValueError, match="不支持的 timeframe"):
            CryptoCalendar.from_timeframe("2h")

    def test_generate_1h(self):
        """测试 1 小时频率日历生成"""
        cal = CryptoCalendar(freq="1h")
        timestamps = cal.generate("2025-01-01", "2025-01-02")
        # 24 小时 + 起始点 = 25 个时间点
        assert len(timestamps) == 25
        assert timestamps[0] == pd.Timestamp("2025-01-01")
        assert timestamps[-1] == pd.Timestamp("2025-01-02")

    def test_generate_1d(self):
        """测试日频日历生成"""
        cal = CryptoCalendar(freq="1d")
        timestamps = cal.generate("2025-01-01", "2025-01-10")
        assert len(timestamps) == 10

    def test_generate_index(self):
        """测试生成 DatetimeIndex"""
        cal = CryptoCalendar(freq="1h")
        index = cal.generate_index("2025-01-01", "2025-01-01 05:00:00")
        assert isinstance(index, pd.DatetimeIndex)
        assert len(index) == 6
        assert index.name == "datetime"

    def test_get_trading_dates(self):
        """测试获取交易日期列表"""
        cal = CryptoCalendar(freq="1h")
        dates = cal.get_trading_dates("2025-01-01", "2025-01-03")
        assert dates == ["2025-01-01", "2025-01-02", "2025-01-03"]

    def test_generate_continuous(self):
        """测试 24/7 连续日历（无休市日）"""
        cal = CryptoCalendar(freq="1d")
        timestamps = cal.generate("2025-01-01", "2025-01-14")
        # 14 天包含两个周末，加密货币不休市
        assert len(timestamps) == 14
        # 检查是否连续无间断
        for i in range(1, len(timestamps)):
            assert timestamps[i] - timestamps[i - 1] == timedelta(days=1)


class TestPerpetualFactors:
    """永续合约因子定义测试"""

    def test_raw_columns(self):
        """测试原始列定义"""
        assert "funding_rate" in PERPETUAL_RAW_COLUMNS
        assert "open_interest" in PERPETUAL_RAW_COLUMNS
        assert "premium" in PERPETUAL_RAW_COLUMNS

    def test_feature_config_structure(self):
        """测试因子配置结构正确（表达式, 名称）"""
        for config_list in [
            KBAR_FEATURE_CONFIG,
            PRICE_FEATURE_CONFIG,
            MA_FEATURE_CONFIG,
            VOLATILITY_FEATURE_CONFIG,
            ROLLING_FEATURE_CONFIG,
            CORRELATION_FEATURE_CONFIG,
            PERPETUAL_FEATURE_CONFIG,
        ]:
            for item in config_list:
                assert isinstance(item, tuple)
                assert len(item) == 2
                assert isinstance(item[0], str)  # 表达式
                assert isinstance(item[1], str)  # 名称

    def test_kbar_feature_count(self):
        """测试 K 线因子数量"""
        assert len(KBAR_FEATURE_CONFIG) == 7

    def test_price_feature_count(self):
        """测试价格动量因子数量（8 个窗口）"""
        assert len(PRICE_FEATURE_CONFIG) == 8

    def test_get_all_feature_config(self):
        """测试获取全部因子配置"""
        all_with = get_all_feature_config(include_perpetual=True)
        all_without = get_all_feature_config(include_perpetual=False)
        assert len(all_with) > len(all_without)
        assert len(all_with) == len(all_without) + len(PERPETUAL_FEATURE_CONFIG)

    def test_get_feature_names(self):
        """测试获取因子名称列表"""
        names = get_feature_names(include_perpetual=True)
        assert isinstance(names, list)
        assert all(isinstance(n, str) for n in names)
        # 名称不重复
        assert len(names) == len(set(names))

    def test_get_feature_expressions(self):
        """测试获取因子表达式列表"""
        exprs = get_feature_expressions(include_perpetual=True)
        assert isinstance(exprs, list)
        assert len(exprs) == len(get_feature_names(include_perpetual=True))


class TestCryptoAlpha158:
    """加密货币因子处理器测试"""

    def test_init(self):
        """测试初始化"""
        handler = CryptoAlpha158()
        assert handler.include_perpetual is True
        assert handler.normalize is True
        assert handler.label_periods == 5
        assert len(handler.feature_names) > 50  # 应该有 60+ 个因子

    def test_calculate_features(self, sample_ohlcv_df):
        """测试基础因子计算"""
        handler = CryptoAlpha158(include_perpetual=False)
        features = handler.calculate_features(sample_ohlcv_df)

        assert isinstance(features, pd.DataFrame)
        assert len(features) == len(sample_ohlcv_df)
        # 应包含 K 线形态因子
        assert "KMID" in features.columns
        assert "KLEN" in features.columns
        # 应包含价格动量因子
        assert "ROC_1" in features.columns
        assert "ROC_20" in features.columns
        # 应包含均线偏离因子
        assert "MA_偏离_5" in features.columns
        # 应包含波动率因子
        assert "CV_20" in features.columns

    def test_calculate_features_with_perpetual(self, sample_ohlcv_with_perpetual):
        """测试包含永续合约因子的计算"""
        handler = CryptoAlpha158(include_perpetual=True)
        features = handler.calculate_features(sample_ohlcv_with_perpetual)

        assert "资金费率" in features.columns
        assert "资金费率_8期均值" in features.columns
        assert "未平仓量_对数" in features.columns
        assert "溢价率" in features.columns

    def test_calculate_features_without_perpetual_data(self, sample_ohlcv_df):
        """测试缺少永续合约数据时的兜底处理"""
        handler = CryptoAlpha158(include_perpetual=True)
        features = handler.calculate_features(sample_ohlcv_df)
        # 应该填充为 0 而不是报错
        assert "资金费率" in features.columns
        assert (features["资金费率"] == 0.0).all()

    def test_calculate_label(self, sample_ohlcv_df):
        """测试标签计算"""
        handler = CryptoAlpha158(label_periods=5)
        label = handler.calculate_label(sample_ohlcv_df)

        assert isinstance(label, pd.Series)
        assert label.name == "label_ret_5"
        # 最后 5 个值应该是 NaN
        assert label.iloc[-5:].isna().all()
        # 前面的值应该是有效数值
        assert label.iloc[:-5].notna().all()

    def test_fit_transform(self, sample_ohlcv_df):
        """测试拟合和转换"""
        handler = CryptoAlpha158(include_perpetual=False, normalize=True, fillna=True)
        features = handler.calculate_features(sample_ohlcv_df)
        transformed = handler.fit_transform(features)

        # 标准化后值应在 [-3, 3] 范围内
        assert transformed.max().max() <= 3.0
        assert transformed.min().min() >= -3.0
        # 不应有 NaN
        assert not transformed.isna().any().any()

    def test_transform_uses_fit_params(self, sample_ohlcv_df):
        """测试 transform 使用 fit 时的参数"""
        handler = CryptoAlpha158(include_perpetual=False, normalize=True)
        features = handler.calculate_features(sample_ohlcv_df)

        # 分割成两部分
        train = features.iloc[:150]
        test = features.iloc[150:]

        handler.fit(train)
        transformed_train = handler.transform(train)
        transformed_test = handler.transform(test)

        # 验证 fit_params 已保存
        assert "mean" in handler._fit_params
        assert "std" in handler._fit_params

        # 两次 transform 结果应该不同（因为标准化参数相同但数据不同）
        assert not transformed_train.equals(transformed_test)

    def test_process_dataset_multiindex(self, sample_multiindex_df):
        """测试 MultiIndex 数据处理"""
        handler = CryptoAlpha158(include_perpetual=False)
        result = handler.process_dataset(sample_multiindex_df)

        assert "features" in result
        assert "label" in result
        assert "feature_names" in result
        assert isinstance(result["features"].index, pd.MultiIndex)
        assert len(result["feature_names"]) > 30

    def test_process_dataset_single(self, sample_ohlcv_df):
        """测试单交易对数据处理"""
        handler = CryptoAlpha158(include_perpetual=False)
        result = handler.process_dataset(sample_ohlcv_df)

        assert "features" in result
        assert "label" in result
        assert len(result["features"]) == len(sample_ohlcv_df)


# ============================================================
# 二、模型层测试
# ============================================================


class TestQLibModelTrainer:
    """模型训练器测试"""

    def test_init(self, tmp_dir):
        """测试初始化"""
        trainer = QLibModelTrainer(model_dir=tmp_dir)
        assert trainer.model_dir == Path(tmp_dir)
        assert len(trainer.trained_models) == 0

    def test_supported_models(self):
        """测试支持的模型类型"""
        assert "lightgbm" in QLibModelTrainer.MODEL_CONFIGS
        assert "linear" in QLibModelTrainer.MODEL_CONFIGS
        assert "xgboost" in QLibModelTrainer.MODEL_CONFIGS

    def test_train_linear(self, sample_features_and_labels, tmp_dir):
        """测试线性模型训练"""
        X, y = sample_features_and_labels
        trainer = QLibModelTrainer(model_dir=tmp_dir)

        X_train, y_train = X.iloc[:200], y.iloc[:200]
        model = trainer.train("linear", X_train, y_train)

        assert model is not None
        assert "linear" in trainer.trained_models

    def test_train_invalid_model(self, sample_features_and_labels, tmp_dir):
        """测试不支持的模型类型"""
        X, y = sample_features_and_labels
        trainer = QLibModelTrainer(model_dir=tmp_dir)

        with pytest.raises(ValueError, match="不支持的模型类型"):
            trainer.train("invalid_model", X.iloc[:100], y.iloc[:100])

    def test_predict(self, sample_features_and_labels, tmp_dir):
        """测试模型预测"""
        X, y = sample_features_and_labels
        trainer = QLibModelTrainer(model_dir=tmp_dir)
        trainer.train("linear", X.iloc[:200], y.iloc[:200])

        pred = trainer.predict("linear", X.iloc[200:])
        assert isinstance(pred, pd.Series)
        assert len(pred) == 100
        assert pred.name == "score"

    def test_predict_untrained(self, sample_features_and_labels, tmp_dir):
        """测试未训练模型的预测"""
        X, _ = sample_features_and_labels
        trainer = QLibModelTrainer(model_dir=tmp_dir)

        with pytest.raises(ValueError, match="尚未训练"):
            trainer.predict("linear", X.iloc[:10])

    def test_train_all(self, sample_features_and_labels, tmp_dir):
        """测试训练所有模型（仅 linear 避免依赖问题）"""
        X, y = sample_features_and_labels
        trainer = QLibModelTrainer(model_dir=tmp_dir)

        results = trainer.train_all(
            X.iloc[:200],
            y.iloc[:200],
            model_types=["linear"],
        )
        assert "linear" in results

    def test_save_and_load_model(self, sample_features_and_labels, tmp_dir):
        """测试模型保存和加载"""
        X, y = sample_features_and_labels
        trainer = QLibModelTrainer(model_dir=tmp_dir)
        trainer.train("linear", X.iloc[:200], y.iloc[:200])

        # 保存
        path = trainer.save_model("linear", tag="test")
        assert path.exists()

        # 加载到新的 trainer
        new_trainer = QLibModelTrainer(model_dir=tmp_dir)
        new_trainer.load_model(path, model_type="linear")
        assert "linear" in new_trainer.trained_models

        # 预测结果应一致
        pred_old = trainer.predict("linear", X.iloc[200:])
        pred_new = new_trainer.predict("linear", X.iloc[200:])
        pd.testing.assert_series_equal(pred_old, pred_new)

    def test_clean_data(self, tmp_dir):
        """测试数据清理"""
        trainer = QLibModelTrainer(model_dir=tmp_dir)

        X = pd.DataFrame({"a": [1, 2, np.inf, 4], "b": [5, np.nan, 7, 8]})
        y = pd.Series([0.1, np.nan, 0.3, 0.4])

        X_clean, y_clean = trainer._fit_clean_params(X, y)
        # NaN 标签行应被移除
        assert len(y_clean) == 3
        # 无穷值应被替换为 0
        assert not np.isinf(X_clean.values).any()
        # 训练集中位数应被缓存
        assert trainer._train_medians is not None

    def test_get_feature_importance_linear(self, sample_features_and_labels, tmp_dir):
        """测试线性模型无 feature_importances_ 属性"""
        X, y = sample_features_and_labels
        trainer = QLibModelTrainer(model_dir=tmp_dir)
        trainer.train("linear", X.iloc[:200], y.iloc[:200])

        # Ridge 模型没有 feature_importances_，应返回 None
        importance = trainer.get_feature_importance("linear")
        assert importance is None


class TestModelEvaluator:
    """模型评估器测试"""

    @pytest.fixture
    def evaluator(self):
        return ModelEvaluator()

    def test_evaluate_basic(self, evaluator):
        """测试基础评估"""
        np.random.seed(42)
        n = 100
        pred = pd.Series(np.random.randn(n))
        # 标签与预测有一定正相关
        label = pred * 0.5 + np.random.randn(n) * 0.5

        result = evaluator.evaluate(pred, label, freq="1h")
        assert "IC" in result
        assert "Rank_IC" in result
        assert "ICIR" in result
        assert "样本数" in result
        assert result["IC"] > 0  # 应该有正向预测能力

    def test_evaluate_insufficient_samples(self, evaluator):
        """测试样本不足时的处理"""
        pred = pd.Series([0.1, 0.2, 0.3])
        label = pd.Series([0.15, 0.18, 0.35])
        result = evaluator.evaluate(pred, label)
        assert "error" in result

    def test_annualize_factor(self, evaluator):
        """测试年化因子（加密货币 365×24）"""
        factor_1h = evaluator._get_annualize_factor("1h")
        factor_1d = evaluator._get_annualize_factor("1d")

        assert factor_1h == 365 * 24  # 一年 8760 个小时
        assert factor_1d == 365  # 一年 365 天

    def test_max_drawdown(self, evaluator):
        """测试最大回撤计算"""
        # 先涨后跌再涨
        returns = pd.Series([0.10, 0.05, -0.20, -0.10, 0.15])
        mdd = evaluator._max_drawdown(returns)
        assert mdd < 0  # 回撤为负数

    def test_compare_models(self, evaluator):
        """测试模型对比"""
        results = {
            "model_a": {"IC": 0.05, "ICIR": 0.8, "夏普比率": 1.5},
            "model_b": {"IC": 0.03, "ICIR": 0.6, "夏普比率": 1.0},
        }
        comparison = evaluator.compare_models(results)
        assert isinstance(comparison, pd.DataFrame)
        # 按 ICIR 降序排列，model_a 应排第一
        assert comparison.index[0] == "model_a"

    def test_select_best_model(self, evaluator):
        """测试选择最优模型"""
        results = {
            "model_a": {"IC": 0.05, "ICIR": 0.8},
            "model_b": {"IC": 0.08, "ICIR": 0.6},
        }
        best = evaluator.select_best_model(results, metric="ICIR")
        assert best == "model_a"

        best_ic = evaluator.select_best_model(results, metric="IC")
        assert best_ic == "model_b"


class TestSignalPredictor:
    """信号预测器测试"""

    @pytest.fixture
    def predictor(self):
        return SignalPredictor(signal_threshold=0.3, strong_threshold=0.7)

    def test_init(self, predictor):
        """测试初始化"""
        assert predictor.signal_threshold == 0.3
        assert predictor.strong_threshold == 0.7

    def test_predict(self, predictor, sample_features_and_labels):
        """测试预测信号生成"""
        X, y = sample_features_and_labels
        # 训练一个简单模型
        from sklearn.linear_model import Ridge

        model = Ridge()
        model.fit(X.iloc[:200], y.iloc[:200])

        signal = predictor.predict(model, X.iloc[200:210], symbol="BTC", model_type="linear")

        assert isinstance(signal, TradingSignal)
        assert signal.symbol == "BTC"
        assert signal.model_type == "linear"
        assert -1 <= signal.normalized_score <= 1
        assert 0 <= signal.strength <= 1
        assert 0 <= signal.confidence <= 1
        assert 0 <= signal.percentile <= 1
        assert signal.feature_count == 20

    def test_signal_direction_neutral(self, predictor):
        """测试中性信号方向判定"""
        direction = predictor._determine_direction(0.1)
        assert direction == SignalDirection.NEUTRAL

    def test_signal_direction_long(self, predictor):
        """测试做多信号方向判定"""
        # 弱做多
        assert predictor._determine_direction(0.35) == SignalDirection.WEAK_LONG
        # 做多
        assert predictor._determine_direction(0.55) == SignalDirection.LONG
        # 强烈做多
        assert predictor._determine_direction(0.8) == SignalDirection.STRONG_LONG

    def test_signal_direction_short(self, predictor):
        """测试做空信号方向判定"""
        # 弱做空
        assert predictor._determine_direction(-0.35) == SignalDirection.WEAK_SHORT
        # 做空
        assert predictor._determine_direction(-0.55) == SignalDirection.SHORT
        # 强烈做空
        assert predictor._determine_direction(-0.8) == SignalDirection.STRONG_SHORT

    def test_normalize_score_insufficient_history(self, predictor):
        """测试历史数据不足时的标准化"""
        predictor._score_history["BTC"] = [0.01, 0.02]
        score = predictor._normalize_score(0.05, "BTC")
        # 使用 tanh 标准化
        assert -1 <= score <= 1

    def test_normalize_score_with_history(self, predictor):
        """测试有足够历史时的标准化"""
        predictor._score_history["BTC"] = list(np.random.randn(100) * 0.01)
        score = predictor._normalize_score(0.05, "BTC")
        assert -1 <= score <= 1

    def test_estimate_confidence_low_history(self, predictor):
        """测试低历史样本量下的置信度"""
        predictor._score_history["BTC"] = [0.01, 0.02, 0.03]
        conf = predictor._estimate_confidence(0.04, "BTC")
        # 低历史样本量时置信度较低，但不低于最低值
        assert conf >= predictor.MIN_CONFIDENCE
        assert conf < 0.5  # 历史不足时不应给出高置信度


class TestTradingSignal:
    """交易信号数据结构测试"""

    def test_is_actionable_neutral(self):
        """中性信号不可执行"""
        signal = TradingSignal(
            symbol="BTC",
            raw_score=0.0,
            normalized_score=0.1,
            direction=SignalDirection.NEUTRAL,
            strength=0.1,
            confidence=0.5,
            percentile=0.5,
            model_type="test",
            feature_count=10,
        )
        assert not signal.is_actionable

    def test_is_actionable_strong(self):
        """强信号可执行"""
        signal = TradingSignal(
            symbol="BTC",
            raw_score=0.05,
            normalized_score=0.8,
            direction=SignalDirection.STRONG_LONG,
            strength=0.8,
            confidence=0.7,
            percentile=0.9,
            model_type="test",
            feature_count=10,
        )
        assert signal.is_actionable
        assert signal.is_long
        assert not signal.is_short

    def test_is_short(self):
        """做空信号识别"""
        signal = TradingSignal(
            symbol="ETH",
            raw_score=-0.05,
            normalized_score=-0.6,
            direction=SignalDirection.SHORT,
            strength=0.6,
            confidence=0.6,
            percentile=0.2,
            model_type="test",
            feature_count=10,
        )
        assert signal.is_short
        assert not signal.is_long

    def test_to_dict(self):
        """测试序列化"""
        signal = TradingSignal(
            symbol="BTC",
            raw_score=0.01,
            normalized_score=0.5,
            direction=SignalDirection.LONG,
            strength=0.5,
            confidence=0.6,
            percentile=0.7,
            model_type="linear",
            feature_count=50,
        )
        d = signal.to_dict()
        assert d["symbol"] == "BTC"
        assert d["direction"] == "做多"
        assert d["is_actionable"] is True


# ============================================================
# 三、策略层测试
# ============================================================


class TestQLibSignalStrategy:
    """交易策略测试"""

    @pytest.fixture
    def strategy(self):
        return QLibSignalStrategy(
            signal_threshold=0.3,
            max_position_pct=0.3,
            default_stop_loss_pct=0.02,
            default_take_profit_pct=0.06,
            min_confidence=0.3,
        )

    @pytest.fixture
    def strong_long_signal(self):
        return TradingSignal(
            symbol="BTC",
            raw_score=0.05,
            normalized_score=0.8,
            direction=SignalDirection.STRONG_LONG,
            strength=0.8,
            confidence=0.7,
            percentile=0.9,
            model_type="lightgbm",
            feature_count=50,
        )

    @pytest.fixture
    def weak_neutral_signal(self):
        return TradingSignal(
            symbol="BTC",
            raw_score=0.001,
            normalized_score=0.1,
            direction=SignalDirection.NEUTRAL,
            strength=0.1,
            confidence=0.5,
            percentile=0.5,
            model_type="lightgbm",
            feature_count=50,
        )

    @pytest.fixture
    def short_signal(self):
        return TradingSignal(
            symbol="ETH",
            raw_score=-0.03,
            normalized_score=-0.6,
            direction=SignalDirection.SHORT,
            strength=0.6,
            confidence=0.6,
            percentile=0.2,
            model_type="lightgbm",
            feature_count=50,
        )

    def test_generate_decision_buy(self, strategy, strong_long_signal):
        """测试做多开仓决策"""
        decision = strategy.generate_decision(
            signal=strong_long_signal,
            current_position=None,
            account_balance=10000,
        )
        assert decision.action == "buy"
        assert decision.should_trade is True
        assert decision.direction == "long"
        assert decision.signal_strength == 0.8
        assert decision.suggested_size_pct > 0
        assert decision.stop_loss_pct > 0
        assert decision.take_profit_pct > 0

    def test_generate_decision_hold_neutral(self, strategy, weak_neutral_signal):
        """测试中性信号保持观望"""
        decision = strategy.generate_decision(
            signal=weak_neutral_signal,
            current_position=None,
        )
        assert decision.action == "hold"
        assert decision.should_trade is False

    def test_generate_decision_sell_short(self, strategy, short_signal):
        """测试做空开仓"""
        decision = strategy.generate_decision(
            signal=short_signal,
            current_position=None,
        )
        assert decision.action == "sell_short"
        assert decision.direction == "short"

    def test_generate_decision_close_opposite(self, strategy, strong_long_signal):
        """测试持有空仓时收到做多信号 → 平仓"""
        decision = strategy.generate_decision(
            signal=strong_long_signal,
            current_position={"side": "short", "size": 0.5, "entry_price": 50000},
        )
        assert decision.action == "buy_to_cover"

    def test_generate_decision_hold_same_direction(self, strategy, strong_long_signal):
        """测试持有同方向仓位 → 保持"""
        decision = strategy.generate_decision(
            signal=strong_long_signal,
            current_position={"side": "long", "size": 0.5, "entry_price": 50000},
        )
        assert decision.action == "hold"

    def test_position_size_calculation(self, strategy, strong_long_signal):
        """测试仓位计算"""
        size = strategy._calculate_position_size(strong_long_signal, 10000, None)
        # 基础 = 0.3 × 0.8 = 0.24, × confidence 0.7 = 0.168, × bonus 1.2 = 0.2016
        assert 0 < size <= 0.3

    def test_stop_levels_strong_signal(self, strategy, strong_long_signal):
        """测试强信号的止盈止损"""
        sl, tp = strategy._calculate_stop_levels(strong_long_signal, None)
        # 强信号：止损更紧，止盈更远
        assert sl < strategy.default_stop_loss_pct
        assert tp > strategy.default_take_profit_pct

    def test_risk_reward_ratio_minimum(self, strategy):
        """测试风险回报比最低限制"""
        weak_signal = TradingSignal(
            symbol="BTC",
            raw_score=0.01,
            normalized_score=0.35,
            direction=SignalDirection.WEAK_LONG,
            strength=0.35,
            confidence=0.4,
            percentile=0.6,
            model_type="test",
            feature_count=10,
        )
        sl, tp = strategy._calculate_stop_levels(weak_signal, None)
        assert tp / (sl + 1e-12) >= 1.5

    def test_trade_decision_to_dict(self):
        """测试 TradeDecision 序列化"""
        decision = TradeDecision(
            symbol="BTC",
            action="buy",
            should_trade=True,
            direction="long",
            signal_strength=0.7,
            confidence=0.6,
            suggested_size_pct=0.2,
            stop_loss_pct=0.02,
            take_profit_pct=0.06,
            reasoning=["测试理由"],
        )
        d = decision.to_dict()
        assert d["symbol"] == "BTC"
        assert d["action"] == "buy"
        assert d["reasoning"] == ["测试理由"]


class TestRiskIntegrator:
    """风控集成器测试"""

    @pytest.fixture
    def integrator(self):
        return RiskIntegrator(qlib_weight=0.7)

    @pytest.fixture
    def buy_decision(self):
        return TradeDecision(
            symbol="BTC",
            action="buy",
            should_trade=True,
            direction="long",
            signal_strength=0.7,
            confidence=0.6,
            suggested_size_pct=0.2,
            stop_loss_pct=0.02,
            take_profit_pct=0.06,
        )

    def test_no_risk_modules(self, integrator, buy_decision):
        """测试无风控模块时直接通过"""
        result = integrator.apply_risk_controls(buy_decision)
        assert result.should_trade is True
        assert result.action == "buy"

    def test_skip_non_trade_decision(self, integrator):
        """测试非交易决策跳过风控"""
        hold_decision = TradeDecision(
            symbol="BTC",
            action="hold",
            should_trade=False,
            direction="neutral",
            signal_strength=0,
            confidence=0,
            suggested_size_pct=0,
            stop_loss_pct=0,
            take_profit_pct=0,
        )
        result = integrator.apply_risk_controls(hold_decision)
        assert result.should_trade is False

    def test_account_protection_blocks(self):
        """测试账户保护阻止交易"""
        mock_protector = MagicMock()
        mock_protector.check_protection.return_value = {
            "action": "PAUSE_NEW_TRADES",
            "reason": "最大回撤超限",
        }

        integrator = RiskIntegrator(account_protector=mock_protector, qlib_weight=0.7)
        decision = TradeDecision(
            symbol="BTC",
            action="buy",
            should_trade=True,
            direction="long",
            signal_strength=0.7,
            confidence=0.6,
            suggested_size_pct=0.2,
            stop_loss_pct=0.02,
            take_profit_pct=0.06,
        )

        result = integrator.apply_risk_controls(
            decision,
            account_info={"equity": 9000, "balance": 10000, "daily_pnl": -500, "positions": []},
        )
        assert result.should_trade is False
        assert any("账户保护" in b for b in result.blockers)

    def test_position_size_blending(self):
        """测试仓位加权融合"""
        mock_sizer = MagicMock()
        mock_sizer.calculate_position_size.return_value = 0.1

        integrator = RiskIntegrator(position_sizer=mock_sizer, qlib_weight=0.7)
        decision = TradeDecision(
            symbol="BTC",
            action="buy",
            should_trade=True,
            direction="long",
            signal_strength=0.7,
            confidence=0.6,
            suggested_size_pct=0.2,
            stop_loss_pct=0.02,
            take_profit_pct=0.06,
        )

        result = integrator.apply_risk_controls(
            decision,
            account_info={"balance": 10000},
        )
        # 加权融合: 0.2 × 0.7 + 0.1 × 0.3 = 0.17
        expected = 0.2 * 0.7 + 0.1 * 0.3
        assert abs(result.suggested_size_pct - expected) < 1e-6


# ============================================================
# 四、引擎层测试
# ============================================================


class TestExperimentManager:
    """实验管理器测试"""

    @pytest.fixture
    def manager(self, tmp_dir):
        return ExperimentManager(experiment_dir=tmp_dir)

    def test_start_and_end_experiment(self, manager):
        """测试开始和结束实验"""
        record = manager.start_experiment(experiment_type="train")
        assert record.status == "running"
        assert record.experiment_type == "train"

        record.log_params({"learning_rate": 0.05})
        record.log_metrics({"IC": 0.08, "ICIR": 1.2})

        manager.end_experiment(status="completed")
        assert record.status == "completed"
        assert record.end_time is not None

    def test_save_and_load_record(self, manager, tmp_dir):
        """测试记录持久化"""
        record = manager.start_experiment(experiment_type="train")
        record.log_metrics({"IC": 0.05})
        manager.end_experiment()

        # 检查文件是否生成
        files = list(Path(tmp_dir).glob("*.json"))
        assert len(files) >= 1  # 至少有 record 和 index 文件

    def test_list_experiments(self, manager):
        """测试列出实验"""
        for i in range(3):
            record = manager.start_experiment(experiment_type="train")
            record.log_metrics({"IC": 0.01 * i})
            manager.end_experiment()

        experiments = manager.list_experiments()
        assert len(experiments) == 3

    def test_list_experiments_filter_type(self, manager):
        """测试按类型过滤实验"""
        manager.start_experiment(experiment_type="train")
        manager.end_experiment()
        manager.start_experiment(experiment_type="retrain")
        manager.end_experiment()

        train_exps = manager.list_experiments(experiment_type="train")
        assert len(train_exps) == 1

    def test_compare_experiments(self, manager):
        """测试实验对比"""
        r1 = manager.start_experiment(experiment_type="train")
        r1.log_metrics({"IC": 0.05, "ICIR": 1.0})
        manager.end_experiment()

        r2 = manager.start_experiment(experiment_type="train")
        r2.log_metrics({"IC": 0.08, "ICIR": 1.5})
        manager.end_experiment()

        comparison = manager.compare_experiments(
            [r1.experiment_id, r2.experiment_id],
            metrics=["IC", "ICIR"],
        )
        assert len(comparison) == 2
        assert comparison[r2.experiment_id]["IC"] == 0.08

    def test_get_best_experiment(self, manager):
        """测试获取最优实验"""
        r1 = manager.start_experiment(experiment_type="train")
        r1.log_metrics({"ICIR": 1.0})
        manager.end_experiment()

        r2 = manager.start_experiment(experiment_type="train")
        r2.log_metrics({"ICIR": 1.5})
        manager.end_experiment()

        best = manager.get_best_experiment(metric="ICIR")
        assert best.experiment_id == r2.experiment_id

    def test_experiment_record_to_dict(self):
        """测试 ExperimentRecord 序列化"""
        record = ExperimentRecord("test_001", "train")
        record.log_params({"lr": 0.01})
        record.log_metrics({"IC": 0.05})
        record.log_tag("model", "lightgbm")
        record.log_artifact("/models/test.pkl")

        d = record.to_dict()
        assert d["experiment_id"] == "test_001"
        assert d["params"]["lr"] == 0.01
        assert d["metrics"]["IC"] == 0.05
        assert d["tags"]["model"] == "lightgbm"
        assert "/models/test.pkl" in d["artifacts"]


class TestOnlineModelManager:
    """在线模型管理器测试"""

    @pytest.fixture
    def mock_components(self, tmp_dir):
        """创建模拟组件"""
        collector = MagicMock()
        handler = CryptoAlpha158(include_perpetual=False)
        trainer = QLibModelTrainer(model_dir=tmp_dir)
        evaluator = ModelEvaluator()
        return collector, handler, trainer, evaluator

    @pytest.fixture
    def online_manager(self, mock_components):
        """创建在线管理器"""
        from src.qlib_engine.engine.online import OnlineModelManager

        collector, handler, trainer, evaluator = mock_components
        return OnlineModelManager(
            collector=collector,
            handler=handler,
            trainer=trainer,
            evaluator=evaluator,
            config={"retrain_interval_hours": 24},
        )

    def test_should_retrain_initial(self, online_manager):
        """测试初始状态需要重训练"""
        assert online_manager.should_retrain() is True

    def test_should_retrain_after_recent_train(self, online_manager):
        """测试最近训练过不需要重训练"""
        online_manager._last_retrain_time = datetime.now()
        assert online_manager.should_retrain() is False

    def test_should_retrain_after_interval(self, online_manager):
        """测试超过间隔需要重训练"""
        online_manager._last_retrain_time = datetime.now() - timedelta(hours=25)
        assert online_manager.should_retrain() is True

    def test_data_cache(self, online_manager):
        """测试数据缓存"""
        df = pd.DataFrame({"a": [1, 2, 3]})
        online_manager.update_data_cache("BTC", df)

        cached = online_manager.get_cached_data("BTC")
        assert cached is not None
        pd.testing.assert_frame_equal(cached, df)

    def test_data_cache_expired(self, online_manager):
        """测试缓存过期"""
        df = pd.DataFrame({"a": [1, 2, 3]})
        online_manager.update_data_cache("BTC", df)
        online_manager._cache_timestamps["BTC"] = datetime.now() - timedelta(hours=2)

        cached = online_manager.get_cached_data("BTC")
        assert cached is None

    def test_data_cache_nonexistent(self, online_manager):
        """测试缓存不存在"""
        cached = online_manager.get_cached_data("SOL")
        assert cached is None

    def test_get_status(self, online_manager):
        """测试状态获取"""
        status = online_manager.get_status()
        assert status["retrain_count"] == 0
        assert status["current_version"] == 0
        assert status["should_retrain"] is True

    def test_should_switch_model_first_time(self, online_manager):
        """测试首次训练直接采用"""
        assert online_manager._should_switch_model({"ICIR": 0.5, "IC": 0.03}) is True

    def test_should_switch_model_bad_quality(self, online_manager):
        """测试质量不达标不切换"""
        online_manager._model_versions = [{"evaluation": {"ICIR": 1.0, "IC": 0.05}}]
        # IC 和 ICIR 都为负，不应切换
        assert online_manager._should_switch_model({"ICIR": -0.1, "IC": -0.01}) is False

    def test_get_performance_trend_empty(self, online_manager):
        """测试空性能趋势"""
        trend = online_manager.get_performance_trend()
        assert isinstance(trend, pd.DataFrame)
        assert len(trend) == 0

    def test_get_model_history(self, online_manager):
        """测试模型版本历史"""
        history = online_manager.get_model_history()
        assert isinstance(history, list)
        assert len(history) == 0


class TestQuantFlowQLibEngine:
    """QLib 核心引擎测试"""

    @pytest.fixture
    def engine(self):
        return QuantFlowQLibEngine(
            config={
                "data": {"freq": "1h", "include_perpetual": False, "label_periods": 5},
                "model": {"model_dir": tempfile.mkdtemp(), "candidates": ["linear"]},
                "strategy": {
                    "signal_threshold": 0.3,
                    "max_position_pct": 0.3,
                    "default_stop_loss_pct": 0.02,
                    "default_take_profit_pct": 0.06,
                    "min_confidence": 0.3,
                },
                "risk_integration": {"qlib_signal_weight": 0.7},
            }
        )

    def _manual_initialize(self, engine, **kwargs):
        """手动初始化引擎（跳过 HyperliquidDataCollector 依赖）"""
        config = engine.config
        data_config = config.get("data", {})
        model_config = config.get("model", {})
        strategy_config = config.get("strategy", {})
        risk_config = config.get("risk_integration", {})

        engine.collector = MagicMock()
        engine.handler = CryptoAlpha158(
            include_perpetual=data_config.get("include_perpetual", False),
            normalize=True,
            fillna=True,
            label_periods=data_config.get("label_periods", 5),
        )
        engine.trainer = QLibModelTrainer(
            model_dir=model_config.get("model_dir", "models"),
        )
        engine.evaluator = ModelEvaluator()
        engine.predictor = SignalPredictor(
            signal_threshold=strategy_config.get("signal_threshold", 0.3),
        )
        engine.strategy = QLibSignalStrategy(
            signal_threshold=strategy_config.get("signal_threshold", 0.3),
            max_position_pct=strategy_config.get("max_position_pct", 0.3),
            default_stop_loss_pct=strategy_config.get("default_stop_loss_pct", 0.02),
            default_take_profit_pct=strategy_config.get("default_take_profit_pct", 0.06),
            min_confidence=strategy_config.get("min_confidence", 0.3),
        )
        engine.risk_integrator = RiskIntegrator(
            decision_validator=kwargs.get("decision_validator"),
            position_sizer=kwargs.get("position_sizer"),
            risk_manager=kwargs.get("risk_manager"),
            account_protector=kwargs.get("account_protector"),
            qlib_weight=risk_config.get("qlib_signal_weight", 0.7),
        )
        engine.initialized = True

    def test_init(self, engine):
        """测试引擎初始化状态"""
        assert engine.initialized is False
        assert engine.model_trained is False

    def test_initialize(self, engine):
        """测试组件初始化（手动方式，跳过外部 SDK 依赖）"""
        self._manual_initialize(engine)

        assert engine.initialized is True
        assert engine.collector is not None
        assert engine.handler is not None
        assert engine.trainer is not None
        assert engine.evaluator is not None
        assert engine.predictor is not None
        assert engine.strategy is not None
        assert engine.risk_integrator is not None

    def test_prepare_and_train_not_initialized(self, engine):
        """测试未初始化时训练报错"""
        with pytest.raises(RuntimeError, match="引擎未初始化"):
            engine.prepare_and_train(["BTC"])

    def test_predict_not_trained(self, engine):
        """测试模型未训练时的预测"""
        self._manual_initialize(engine)
        result = engine.predict("BTC")
        assert "error" in result

    def test_generate_trade_decision_not_trained(self, engine):
        """测试模型未训练时的决策"""
        engine.model_trained = False
        decision = engine.generate_trade_decision("BTC")
        assert decision.action == "hold"
        assert decision.should_trade is False
        assert "QLib 模型尚未训练" in decision.reasoning

    def test_should_retrain_initial(self, engine):
        """测试初始状态需要重训练"""
        assert engine.should_retrain() is True

    def test_should_retrain_recently_trained(self, engine):
        """测试最近训练过"""
        engine.model_trained = True
        engine._last_train_time = datetime.now()
        assert engine.should_retrain() is False

    def test_get_status(self, engine):
        """测试状态查询"""
        status = engine.get_status()
        assert status["initialized"] is False
        assert status["model_trained"] is False
        assert status["best_model"] == "lightgbm"
        assert status["should_retrain"] is True

    def test_initialize_with_risk_modules(self, engine):
        """测试带风控模块的初始化"""
        mock_validator = MagicMock()
        mock_sizer = MagicMock()
        mock_risk = MagicMock()
        mock_protector = MagicMock()

        self._manual_initialize(
            engine,
            decision_validator=mock_validator,
            position_sizer=mock_sizer,
            risk_manager=mock_risk,
            account_protector=mock_protector,
        )

        assert engine.risk_integrator.decision_validator is mock_validator
        assert engine.risk_integrator.position_sizer is mock_sizer
        assert engine.risk_integrator.risk_manager is mock_risk
        assert engine.risk_integrator.account_protector is mock_protector


# ============================================================
# 五、集成测试
# ============================================================


class TestEndToEndPipeline:
    """端到端集成测试（不依赖外部 API）"""

    def test_data_to_features_to_model(self, sample_ohlcv_df, tmp_dir):
        """测试 数据 → 因子 → 模型训练 → 预测 完整流程"""
        # 1. 计算因子
        handler = CryptoAlpha158(include_perpetual=False, label_periods=5)
        features = handler.calculate_features(sample_ohlcv_df)
        label = handler.calculate_label(sample_ohlcv_df)

        # 2. 分割数据
        n = len(features)
        train_end = int(n * 0.7)
        valid_end = int(n * 0.85)

        X_train = handler.fit_transform(features.iloc[:train_end])
        y_train = label.iloc[:train_end]
        X_valid = handler.transform(features.iloc[train_end:valid_end])
        y_valid = label.iloc[train_end:valid_end]
        X_test = handler.transform(features.iloc[valid_end:])
        y_test = label.iloc[valid_end:]

        # 3. 训练模型
        trainer = QLibModelTrainer(model_dir=tmp_dir)
        trainer.train("linear", X_train, y_train, X_valid, y_valid)

        # 4. 预测
        pred = trainer.predict("linear", X_test)
        assert len(pred) == len(X_test)

        # 5. 评估
        evaluator = ModelEvaluator()
        eval_result = evaluator.evaluate(pred, y_test, freq="1h")
        assert "IC" in eval_result
        assert "样本数" in eval_result

    def test_signal_to_decision_pipeline(self, sample_ohlcv_df, tmp_dir):
        """测试 信号 → 策略决策 → 风控 完整流程"""
        # 1. 准备数据和模型
        handler = CryptoAlpha158(include_perpetual=False, label_periods=5)
        features = handler.calculate_features(sample_ohlcv_df)
        label = handler.calculate_label(sample_ohlcv_df)

        X_train = handler.fit_transform(features.iloc[:140])
        y_train = label.iloc[:140]

        trainer = QLibModelTrainer(model_dir=tmp_dir)
        trainer.train("linear", X_train, y_train)

        # 2. 生成信号
        predictor = SignalPredictor(signal_threshold=0.3)
        X_latest = handler.transform(features.iloc[140:])
        signal = predictor.predict(
            model=trainer.trained_models["linear"],
            features=X_latest,
            symbol="BTC",
            model_type="linear",
        )
        assert isinstance(signal, TradingSignal)

        # 3. 策略决策
        strategy = QLibSignalStrategy(signal_threshold=0.3, max_position_pct=0.3)
        decision = strategy.generate_decision(
            signal=signal,
            current_position=None,
            account_balance=10000,
        )
        assert isinstance(decision, TradeDecision)
        assert decision.action in ("buy", "sell_short", "hold")

        # 4. 风控
        integrator = RiskIntegrator(qlib_weight=0.7)
        final_decision = integrator.apply_risk_controls(decision)
        assert isinstance(final_decision, TradeDecision)

    def test_experiment_lifecycle(self, tmp_dir):
        """测试实验生命周期"""
        manager = ExperimentManager(experiment_dir=tmp_dir)

        # 开始实验
        record = manager.start_experiment(
            experiment_type="train",
            tags={"model": "lightgbm", "symbols": "BTC,ETH"},
        )

        # 记录参数
        record.log_params(
            {
                "learning_rate": 0.05,
                "num_leaves": 128,
                "n_estimators": 500,
            }
        )

        # 记录指标
        record.log_metrics(
            {
                "IC": 0.065,
                "ICIR": 1.23,
                "夏普比率": 2.1,
                "最大回撤": -0.08,
            }
        )

        # 结束
        manager.end_experiment(status="completed")

        # 验证可以读取
        loaded = manager.get_experiment(record.experiment_id)
        assert loaded is not None
        assert loaded.metrics["IC"] == 0.065
        assert loaded.tags["model"] == "lightgbm"
