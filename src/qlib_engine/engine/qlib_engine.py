"""
QLib 核心引擎

系统的中枢控制器，负责初始化和协调数据层、模型层、策略层。
提供统一的接口供 main.py 调用。
"""

import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

from ..data.collector import HyperliquidDataCollector
from ..data.handler import CryptoAlpha158
from ..model.evaluator import ModelEvaluator
from ..model.predictor import SignalPredictor
from ..model.trainer import QLibModelTrainer
from ..strategy.risk_integrator import RiskIntegrator
from ..strategy.signal_strategy import QLibSignalStrategy, TradeDecision

logger = logging.getLogger("QuantFlow.QLib")


class QuantFlowQLibEngine:
    """
    QLib 核心引擎

    完整的量化决策链路：
    数据收集 → 因子计算 → 模型预测 → 信号生成 → 策略决策 → 风控验证

    使用方式：
    ```python
    engine = QuantFlowQLibEngine(config)
    engine.initialize()
    engine.prepare_and_train(symbols)

    # 交易循环中
    decision = engine.generate_trade_decision(symbol, current_position, balance)
    if decision.should_trade:
        order_manager.execute(decision)
    ```
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
        self._raw_data: dict[str, pd.DataFrame] = {}  # 每个交易对的原始数据
        self._features: dict[str, pd.DataFrame] = {}   # 每个交易对的因子数据
        self._best_model_type: str = "lightgbm"        # 当前最优模型
        self._last_train_time: datetime | None = None   # 上次训练时间

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
        # 解析配置
        data_config = self.config.get("data", {})
        model_config = self.config.get("model", {})
        strategy_config = self.config.get("strategy", {})
        risk_config = self.config.get("risk_integration", {})

        # 初始化数据层
        self.collector = HyperliquidDataCollector(testnet=testnet)
        self.handler = CryptoAlpha158(
            include_perpetual=data_config.get("include_perpetual", True),
            normalize=True,
            fillna=True,
            label_periods=data_config.get("label_periods", 5),
        )

        # 初始化模型层
        model_dir = model_config.get("model_dir", "models")
        custom_params = {}
        for model_type in ["lightgbm", "xgboost", "linear"]:
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
        logger.info("QLib 引擎初始化完成")

    def prepare_and_train(
        self,
        symbols: list[str],
        freq: str = "1h",
        limit: int = 500,
        model_types: list[str] | None = None,
    ) -> dict:
        """
        准备数据并训练模型

        完整流程：
        1. 收集历史数据
        2. 计算因子
        3. 数据标准化
        4. 训练多个模型
        5. 评估并选择最优模型

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
            model_types = self.config.get("model", {}).get(
                "candidates", ["lightgbm"]
            )

        logger.info(f"开始准备数据和训练模型: 交易对={symbols}, 频率={freq}")

        # 1. 收集数据
        raw_data = self.collector.collect_full_dataset(symbols, freq=freq, limit=limit)
        if raw_data.empty:
            logger.error("数据收集失败，无法训练模型")
            return {"error": "数据收集失败"}

        # 2. 计算因子和标签
        processed = self.handler.process_dataset(raw_data)
        features = processed["features"]
        label = processed["label"]
        feature_names = processed["feature_names"]

        # 3. 数据分割（按时间顺序）
        timestamps = features.index.get_level_values("datetime").unique().sort_values() \
            if isinstance(features.index, pd.MultiIndex) \
            else features.index.sort_values()

        n_total = len(timestamps)
        n_train = int(n_total * 0.7)
        n_valid = int(n_total * 0.15)

        train_end = timestamps[n_train - 1]
        valid_end = timestamps[n_train + n_valid - 1]

        if isinstance(features.index, pd.MultiIndex):
            train_mask = features.index.get_level_values("datetime") <= train_end
            valid_mask = (
                (features.index.get_level_values("datetime") > train_end)
                & (features.index.get_level_values("datetime") <= valid_end)
            )
            test_mask = features.index.get_level_values("datetime") > valid_end
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

        # 4. 拟合标准化参数（在训练集上）
        X_train = self.handler.fit_transform(X_train)
        X_valid = self.handler.transform(X_valid)
        X_test = self.handler.transform(X_test)

        logger.info(
            f"数据分割: 训练={len(X_train)}, 验证={len(X_valid)}, 测试={len(X_test)}"
        )

        # 5. 训练模型
        models = self.trainer.train_all(
            X_train, y_train, X_valid, y_valid,
            model_types=model_types,
        )

        # 6. 评估模型
        evaluation_results = {}
        for model_type, model in models.items():
            pred = self.trainer.predict(model_type, X_test)
            eval_result = self.evaluator.evaluate(pred, y_test, freq=freq)
            evaluation_results[model_type] = eval_result
            logger.info(
                f"模型 {model_type}: IC={eval_result.get('IC', 0):.4f}, "
                f"ICIR={eval_result.get('ICIR', 0):.4f}, "
                f"夏普={eval_result.get('夏普比率', 0):.4f}"
            )

        # 7. 选择最优模型
        if evaluation_results:
            self._best_model_type = self.evaluator.select_best_model(evaluation_results)

            # 保存最优模型
            self.trainer.save_model(self._best_model_type, tag="best")

            # 获取特征重要性
            importance = self.trainer.get_feature_importance(self._best_model_type)
            if importance is not None:
                logger.info(f"特征重要性 Top10:\n{importance.head(10).to_string()}")

        # 缓存原始数据和因子
        for symbol in symbols:
            if isinstance(raw_data.index, pd.MultiIndex) and symbol in raw_data.index.get_level_values("instrument"):
                self._raw_data[symbol] = raw_data.xs(symbol, level="instrument")
                if symbol in features.index.get_level_values("instrument"):
                    self._features[symbol] = features.xs(symbol, level="instrument")

        self.model_trained = True
        self._last_train_time = datetime.now()

        result = {
            "models_trained": list(models.keys()),
            "best_model": self._best_model_type,
            "evaluation": evaluation_results,
            "feature_count": len(feature_names),
            "train_samples": len(X_train),
            "test_samples": len(X_test),
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

        # 获取最新数据
        if latest_data is None:
            freq = self.config.get("data", {}).get("freq", "1h")
            raw = self.collector.collect_ohlcv([symbol], freq=freq, limit=100)
            if raw.empty:
                return {"error": "数据获取失败"}
            latest_data = raw.xs(symbol, level="instrument") \
                if isinstance(raw.index, pd.MultiIndex) else raw

        # 计算因子
        features = self.handler.calculate_features(latest_data)
        features = self.handler.transform(features)

        # 生成预测信号
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

        这是外部调用的主要接口。

        Args:
            symbol: 交易对
            current_position: 当前持仓信息
            account_balance: 账户余额
            account_info: 账户详细信息（用于风控）
            latest_data: 最新市场数据
            market_data: 市场上下文（用于风控）

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

        # 获取最新数据
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
            latest_data = raw.xs(symbol, level="instrument") \
                if isinstance(raw.index, pd.MultiIndex) else raw

        # 计算因子
        features = self.handler.calculate_features(latest_data)
        features = self.handler.transform(features)

        # 生成预测信号
        signal = self.predictor.predict(
            model=self.trainer.trained_models[self._best_model_type],
            features=features,
            symbol=symbol,
            model_type=self._best_model_type,
        )

        # 生成交易决策
        decision = self.strategy.generate_decision(
            signal=signal,
            current_position=current_position,
            account_balance=account_balance,
        )

        # 应用风控
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
        判断是否需要重新训练模型

        Returns:
            True 如果需要重训练
        """
        if not self.model_trained or self._last_train_time is None:
            return True

        retrain_hours = self.config.get("online", {}).get("retrain_interval_hours", 168)
        elapsed = (datetime.now() - self._last_train_time).total_seconds() / 3600
        return elapsed >= retrain_hours

    def get_status(self) -> dict:
        """
        获取引擎状态

        Returns:
            状态字典
        """
        return {
            "initialized": self.initialized,
            "model_trained": self.model_trained,
            "best_model": self._best_model_type,
            "last_train_time": self._last_train_time.isoformat() if self._last_train_time else None,
            "cached_symbols": list(self._raw_data.keys()),
            "should_retrain": self.should_retrain(),
        }
