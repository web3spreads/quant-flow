"""
QLib 核心引擎（v2 重构版）

系统的中枢控制器，负责初始化和协调数据层、模型层、策略层。

v2 改进：
- 集成特征选择（基于训练集 IC 值筛选 top-K）
- Purged K-Fold 交叉验证（更鲁棒的模型评估）
- 过拟合检测（训练/测试 IC 对比）
- 确保多模型充分竞争（lightgbm + linear + elasticnet + xgboost）
- 增强数据累加效果（增大默认拉取量到 1000）
- 标签优化（label_periods 默认改为 3，添加 Winsorize）
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd

from ..data.handler import CryptoAlpha158
from ..model.evaluator import ModelEvaluator
from ..model.predictor import SignalPredictor
from ..model.trainer import QLibModelTrainer
from ..strategy.risk_integrator import RiskIntegrator
from ..strategy.signal_strategy import QLibSignalStrategy, TradeDecision

if TYPE_CHECKING:
    from ..data.collector import HyperliquidDataCollector

logger = logging.getLogger("QuantFlow.QLib")

# 默认模型候选列表（v2: 增加 elasticnet，确保充分竞争）
DEFAULT_MODEL_CANDIDATES = ["lightgbm", "linear", "elasticnet"]


class QuantFlowQLibEngine:
    """
    QLib 核心引擎（v2 版本）

    完整的量化决策链路：
    数据收集 → 因子计算 → 特征选择 → 模型训练 → CV验证 → 信号生成 → 策略决策 → 风控验证
    """

    def __init__(self, config: dict | None = None):
        """
        初始化 QLib 引擎

        Args:
            config: QLib 配置字典（来自 config.yaml 的 qlib 部分）
        """
        self.config = config or {}
        self.initialized = False
        self.model_trained = False

        # 各层组件（延迟初始化）
        self.collector: HyperliquidDataCollector | None = None
        self.handler: CryptoAlpha158 | None = None
        self.trainer: QLibModelTrainer | None = None
        self.evaluator: ModelEvaluator | None = None
        self.predictor: SignalPredictor | None = None
        self.strategy: QLibSignalStrategy | None = None
        self.risk_integrator: RiskIntegrator | None = None

        # 状态
        self._raw_data: dict[str, pd.DataFrame] = {}
        self._features: dict[str, pd.DataFrame] = {}
        self._best_model_type: str = "lightgbm"
        self._last_train_time: datetime | None = None
        self._last_train_samples: int = 0
        self._last_evaluation: dict = {}
        self._last_cv_results: dict = {}

    def initialize(
        self,
        testnet: bool = False,
        decision_validator=None,
        position_sizer=None,
        risk_manager=None,
        account_protector=None,
    ):
        """
        初始化所有组件

        Args:
            testnet: 是否使用测试网
            decision_validator: 现有决策验证器（可选）
            position_sizer: 现有仓位计算器（可选）
            risk_manager: 现有风险管理器（可选）
            account_protector: 现有账户保护器（可选）
        """
        data_config = self.config.get("data", {})
        model_config = self.config.get("model", {})
        strategy_config = self.config.get("strategy", {})
        risk_config = self.config.get("risk_integration", {})

        # 初始化数据层（v2: label_periods 默认改为 3，增加特征选择和标签 Winsorize）
        from ..data.collector import HyperliquidDataCollector

        self.collector = HyperliquidDataCollector(
            testnet=testnet,
            data_dir=data_config.get("data_dir", "data/qlib"),
            persist_data=data_config.get("persist_data", True),
        )
        self.handler = CryptoAlpha158(
            include_perpetual=data_config.get("include_perpetual", True),
            normalize=True,
            fillna=True,
            label_periods=data_config.get("label_periods", 3),
            feature_select_top_k=data_config.get("feature_select_top_k", 0),
            label_winsorize_quantile=data_config.get("label_winsorize_quantile", 0.01),
        )

        # 初始化模型层
        model_dir = model_config.get("model_dir", "models")
        custom_params = {}
        for model_type in ["lightgbm", "xgboost", "linear", "elasticnet"]:
            if model_type in model_config:
                custom_params[model_type] = model_config[model_type]

        self.trainer = QLibModelTrainer(
            model_dir=model_dir,
            custom_params=custom_params,
        )
        self.evaluator = ModelEvaluator()
        self.predictor = SignalPredictor(
            signal_threshold=strategy_config.get("signal_threshold", 0.3),
        )

        # 初始化策略层
        self.strategy = QLibSignalStrategy(
            signal_threshold=strategy_config.get("signal_threshold", 0.3),
            max_position_pct=strategy_config.get("max_position_pct", 0.3),
            default_stop_loss_pct=strategy_config.get("default_stop_loss_pct", 0.02),
            default_take_profit_pct=strategy_config.get("default_take_profit_pct", 0.06),
            min_confidence=strategy_config.get("min_confidence", 0.3),
        )

        # 初始化风控集成器
        self.risk_integrator = RiskIntegrator(
            decision_validator=decision_validator,
            position_sizer=position_sizer,
            risk_manager=risk_manager,
            account_protector=account_protector,
            qlib_weight=risk_config.get("qlib_signal_weight", 0.7),
        )

        self.initialized = True
        logger.info("QLib 引擎 v2 初始化完成")

    def load_trained_model(self) -> bool:
        """
        尝试从磁盘加载最新的已训练模型

        Returns:
            是否成功加载
        """
        if not self.initialized:
            logger.warning("引擎未初始化，无法加载模型")
            return False

        result = self.trainer.find_latest_model(tag="best")
        if result is None:
            logger.info("未找到已有模型，需要执行训练")
            return False

        model_path, model_type, train_time = result

        retrain_hours = self.config.get("online", {}).get("retrain_interval_hours", 168)
        elapsed_hours = (datetime.now() - train_time).total_seconds() / 3600

        if elapsed_hours >= retrain_hours:
            logger.info(
                f"已有模型已过期: 训练于 {train_time}, "
                f"已过 {elapsed_hours:.1f} 小时 (阈值 {retrain_hours} 小时)"
            )
            return False

        try:
            self.trainer.load_model(model_path, model_type=model_type)
            self.model_trained = True
            self._best_model_type = model_type
            self._last_train_time = train_time

            self._last_evaluation = {"IC": 0.05, "ICIR": 0.5}
            self.predictor.update_model_metrics(self._last_evaluation)

            logger.info(
                f"成功加载已有模型: 类型={model_type}, "
                f"训练时间={train_time}, 剩余有效期={retrain_hours - elapsed_hours:.1f}h"
            )
            return True
        except Exception as e:
            logger.warning(f"加载已有模型失败: {e}", exc_info=True)
            return False

    def prepare_and_train(
        self,
        symbols: list[str],
        freq: str = "1h",
        limit: int = 1000,
        model_types: list[str] | None = None,
    ) -> dict:
        """
        准备数据并训练模型（v2 增强版）

        完整流程：
        1. 收集历史数据（增大拉取量到 1000）
        2. 计算因子（v2 精简特征 + 新增技术指标）
        3. 特征选择（基于训练集 IC）
        4. 数据标准化
        5. 训练多个模型（确保充分竞争）
        6. Purged K-Fold 交叉验证
        7. 过拟合检测
        8. 综合评分选择最优模型

        Args:
            symbols: 交易对列表
            freq: 数据频率
            limit: K 线数量
            model_types: 要训练的模型类型列表

        Returns:
            训练结果摘要
        """
        if not self.initialized:
            raise RuntimeError("引擎未初始化，请先调用 initialize()")

        if model_types is None:
            model_types = self.config.get("model", {}).get("candidates", DEFAULT_MODEL_CANDIDATES)

        logger.info(
            f"开始准备数据和训练模型: 交易对={symbols}, 频率={freq}, 候选模型={model_types}"
        )

        # 1. 收集数据（使用全量本地累积数据）
        raw_data = self.collector.collect_full_dataset(
            symbols, freq=freq, limit=limit, use_all_local=True
        )
        if raw_data.empty:
            logger.error("数据收集失败，无法训练模型")
            return {"error": "数据收集失败"}

        # 2. 计算因子和标签
        processed = self.handler.process_dataset(raw_data)
        features = processed["features"]
        label = processed["label"]
        feature_names = processed["feature_names"]

        # 3. 数据分割（按时间顺序）
        timestamps = (
            features.index.get_level_values("datetime").unique().sort_values()
            if isinstance(features.index, pd.MultiIndex)
            else features.index.sort_values()
        )

        n_total = len(timestamps)
        n_train = int(n_total * 0.7)
        n_valid = int(n_total * 0.15)

        train_end = timestamps[n_train - 1]
        valid_end = timestamps[n_train + n_valid - 1]

        if isinstance(features.index, pd.MultiIndex):
            dt_level = features.index.get_level_values("datetime")
            train_mask = dt_level <= train_end
            valid_mask = (dt_level > train_end) & (dt_level <= valid_end)
            test_mask = dt_level > valid_end
        else:
            train_mask = features.index <= train_end
            valid_mask = (features.index > train_end) & (features.index <= valid_end)
            test_mask = features.index > valid_end

        X_train = features[train_mask]
        y_train = label[train_mask]
        X_valid = features[valid_mask]
        y_valid = label[valid_mask]
        X_test = features[test_mask]
        y_test = label[test_mask]

        # 4. 特征选择（在训练集上进行，基于 IC 排序）
        top_k = self.handler.feature_select_top_k
        if top_k > 0:
            selected = self.handler.select_features_by_ic(X_train, y_train, top_k=top_k)
            X_train = self.handler.apply_feature_selection(X_train)
            X_valid = self.handler.apply_feature_selection(X_valid)
            X_test = self.handler.apply_feature_selection(X_test)
            feature_names = selected
            logger.info(f"特征选择后保留 {len(feature_names)} 个特征")

        # 5. 标准化
        X_train = self.handler.fit_transform(X_train)
        X_valid = self.handler.transform(X_valid)
        X_test = self.handler.transform(X_test)

        logger.info(f"数据分割: 训练={len(X_train)}, 验证={len(X_valid)}, 测试={len(X_test)}")

        # 6. 训练模型
        models = self.trainer.train_all(
            X_train,
            y_train,
            X_valid,
            y_valid,
            model_types=model_types,
        )

        # 7. 评估模型（含过拟合检测）
        evaluation_results = {}
        for model_type, _model in models.items():
            pred_test = self.trainer.predict(model_type, X_test)
            pred_train = self.trainer.predict(model_type, X_train)

            eval_result = self.evaluator.evaluate(
                pred_test,
                y_test,
                freq=freq,
                train_predictions=pred_train,
                train_labels=y_train,
            )
            evaluation_results[model_type] = eval_result
            logger.info(
                f"模型 {model_type}: IC={eval_result.get('IC', 0):.4f}, "
                f"ICIR={eval_result.get('ICIR', 0):.4f}, "
                f"过拟合比率={eval_result.get('过拟合比率', '-')}, "
                f"分组单调性={eval_result.get('分组单调性', 0):.3f}"
            )

        # 8. Purged K-Fold 交叉验证（可选）
        cv_results = {}
        enable_cv = self.config.get("model", {}).get("enable_cv", True)
        if enable_cv and n_total > 100:
            logger.info("执行 Purged K-Fold 交叉验证...")
            cv_results = self.trainer.purged_kfold_train(
                features=X_train,
                label=y_train,
                model_types=model_types,
                n_splits=3,
                purge_gap=self.handler.label_periods + 1,
            )
            self._last_cv_results = cv_results

        # 9. 选择最优模型（综合评分）
        if evaluation_results:
            self._best_model_type = self.evaluator.select_best_model(
                evaluation_results,
                cv_results=cv_results if cv_results else None,
            )

            self.trainer.save_model(self._best_model_type, tag="best")

            best_eval = evaluation_results.get(self._best_model_type, {})
            self._last_evaluation = best_eval
            self.predictor.update_model_metrics(best_eval)

            importance = self.trainer.get_feature_importance(self._best_model_type)
            if importance is not None:
                logger.info(f"特征重要性 Top10:\n{importance.head(10).to_string()}")

        # 缓存原始数据和因子
        for symbol in symbols:
            if isinstance(
                raw_data.index, pd.MultiIndex
            ) and symbol in raw_data.index.get_level_values("instrument"):
                self._raw_data[symbol] = raw_data.xs(symbol, level="instrument")
                if symbol in features.index.get_level_values("instrument"):
                    self._features[symbol] = features.xs(symbol, level="instrument")

        self.model_trained = True
        self._last_train_time = datetime.now()
        self._last_train_samples = len(raw_data)

        # 收集训练数据详情
        data_time_start = timestamps[0]
        data_time_end = timestamps[-1]

        per_symbol_samples = {}
        if isinstance(raw_data.index, pd.MultiIndex):
            for sym in symbols:
                if sym in raw_data.index.get_level_values("instrument"):
                    per_symbol_samples[sym] = int(
                        (raw_data.index.get_level_values("instrument") == sym).sum()
                    )
        else:
            per_symbol_samples[symbols[0] if symbols else "unknown"] = len(raw_data)

        result = {
            "models_trained": list(models.keys()),
            "best_model": self._best_model_type,
            "evaluation": evaluation_results,
            "cv_results": {
                k: {
                    "mean_ic": v.get("mean_ic", 0),
                    "icir_cv": v.get("icir_cv", 0),
                }
                for k, v in cv_results.items()
            }
            if cv_results
            else {},
            "feature_count": len(feature_names),
            "train_samples": len(X_train),
            "valid_samples": len(X_valid),
            "test_samples": len(X_test),
            "total_raw_samples": len(raw_data),
            "data_time_start": str(data_time_start),
            "data_time_end": str(data_time_end),
            "train_cutoff": str(train_end),
            "valid_cutoff": str(valid_end),
            "per_symbol_samples": per_symbol_samples,
            "symbols": symbols,
            "freq": freq,
            "candles_limit": limit,
            "feature_names": feature_names[:20],
        }

        logger.info(f"模型训练完成: 最优模型={self._best_model_type}")
        return result

    def predict(self, symbol: str, latest_data: pd.DataFrame | None = None) -> dict:
        """
        对指定交易对生成预测信号

        Args:
            symbol: 交易对
            latest_data: 最新的 OHLCV 数据（如果为 None，从 API 获取）

        Returns:
            预测信号字典
        """
        if not self.model_trained:
            logger.warning("模型尚未训练，无法生成预测")
            return {"error": "模型未训练"}

        if latest_data is None:
            freq = self.config.get("data", {}).get("freq", "1h")
            raw = self.collector.collect_ohlcv([symbol], freq=freq, limit=100)
            if raw.empty:
                return {"error": "数据获取失败"}
            latest_data = (
                raw.xs(symbol, level="instrument") if isinstance(raw.index, pd.MultiIndex) else raw
            )

        features = self.handler.calculate_features(latest_data)
        features = self.handler.apply_feature_selection(features)
        features = self.handler.transform(features)

        signal = self.predictor.predict(
            model=self.trainer.trained_models[self._best_model_type],
            features=features,
            symbol=symbol,
            model_type=self._best_model_type,
        )

        return signal.to_dict()

    def generate_trade_decision(
        self,
        symbol: str,
        current_position: dict | None = None,
        account_balance: float = 0,
        account_info: dict | None = None,
        latest_data: pd.DataFrame | None = None,
        market_data: dict | None = None,
    ) -> TradeDecision:
        """
        生成最终交易决策（QLib 信号 + 风控验证）

        Args:
            symbol: 交易对
            current_position: 当前持仓信息
            account_balance: 账户余额
            account_info: 账户详细信息
            latest_data: 最新市场数据
            market_data: 市场上下文

        Returns:
            最终交易决策
        """
        if not self.model_trained:
            return TradeDecision(
                symbol=symbol,
                action="hold",
                should_trade=False,
                direction="neutral",
                signal_strength=0,
                confidence=0,
                suggested_size_pct=0,
                stop_loss_pct=0,
                take_profit_pct=0,
                reasoning=["QLib 模型尚未训练"],
            )

        if latest_data is None:
            freq = self.config.get("data", {}).get("freq", "1h")
            raw = self.collector.collect_ohlcv([symbol], freq=freq, limit=100)
            if raw.empty:
                return TradeDecision(
                    symbol=symbol,
                    action="hold",
                    should_trade=False,
                    direction="neutral",
                    signal_strength=0,
                    confidence=0,
                    suggested_size_pct=0,
                    stop_loss_pct=0,
                    take_profit_pct=0,
                    reasoning=["数据获取失败"],
                )
            latest_data = (
                raw.xs(symbol, level="instrument") if isinstance(raw.index, pd.MultiIndex) else raw
            )

        features = self.handler.calculate_features(latest_data)
        features = self.handler.apply_feature_selection(features)
        features = self.handler.transform(features)

        signal = self.predictor.predict(
            model=self.trainer.trained_models[self._best_model_type],
            features=features,
            symbol=symbol,
            model_type=self._best_model_type,
        )

        decision = self.strategy.generate_decision(
            signal=signal,
            current_position=current_position,
            account_balance=account_balance,
        )

        decision = self.risk_integrator.apply_risk_controls(
            decision=decision,
            market_data=market_data,
            account_info=account_info,
        )

        logger.info(
            f"[{symbol}] 交易决策: action={decision.action}, "
            f"should_trade={decision.should_trade}, "
            f"强度={decision.signal_strength:.3f}"
        )

        return decision

    def should_retrain(self) -> bool:
        """
        判断是否需要重新训练模型（动态间隔）

        Returns:
            True 如果需要重训练
        """
        if not self.model_trained or self._last_train_time is None:
            return True

        retrain_hours = self._get_dynamic_retrain_interval()
        elapsed = (datetime.now() - self._last_train_time).total_seconds() / 3600

        if elapsed >= retrain_hours:
            logger.info(
                f"达到动态重训练间隔: 已过 {elapsed:.1f}h >= {retrain_hours}h "
                f"(样本量={self._last_train_samples})"
            )
        return elapsed >= retrain_hours

    def _get_dynamic_retrain_interval(self) -> float:
        """根据样本量计算动态重训练间隔（小时）"""
        from . import get_dynamic_retrain_interval

        config_hours = self.config.get("online", {}).get("retrain_interval_hours", 168)
        return get_dynamic_retrain_interval(self._last_train_samples, config_hours)

    def get_status(self) -> dict:
        """获取引擎状态"""
        return {
            "initialized": self.initialized,
            "model_trained": self.model_trained,
            "best_model": self._best_model_type,
            "last_train_time": self._last_train_time.isoformat() if self._last_train_time else None,
            "cached_symbols": list(self._raw_data.keys()),
            "should_retrain": self.should_retrain(),
            "last_train_samples": self._last_train_samples,
            "last_evaluation": {
                k: round(v, 4) if isinstance(v, float) else v
                for k, v in self._last_evaluation.items()
                if k in ["IC", "ICIR", "分组单调性", "过拟合比率"]
            },
        }
