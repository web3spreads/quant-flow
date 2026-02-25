"""
在线模型管理

实现滚动训练、增量更新、模型版本管理等在线服务能力。
确保模型随市场变化持续适应，避免模型老化。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd

from ..data.handler import CryptoAlpha158
from ..model.evaluator import ModelEvaluator
from ..model.trainer import QLibModelTrainer

if TYPE_CHECKING:
    from ..data.collector import HyperliquidDataCollector

logger = logging.getLogger("QuantFlow.QLib")


class OnlineModelManager:
    """
    在线模型管理器

    职责：
    1. 滚动训练：定期用最新数据重新训练模型
    2. 增量更新：用新数据微调现有模型
    3. 模型版本管理：维护模型历史记录
    4. 数据缓存：缓存最近的数据减少 API 调用
    5. 自动切换：当新模型表现更好时自动切换
    """

    def __init__(
        self,
        collector: HyperliquidDataCollector,
        handler: CryptoAlpha158,
        trainer: QLibModelTrainer,
        evaluator: ModelEvaluator,
        config: dict | None = None,
    ):
        """
        初始化在线模型管理器

        Args:
            collector: 数据收集器
            handler: 数据处理器
            trainer: 模型训练器
            evaluator: 模型评估器
            config: 在线学习配置
        """
        self.collector = collector
        self.handler = handler
        self.trainer = trainer
        self.evaluator = evaluator
        self.config = config or {}

        # 滚动训练配置
        self.retrain_interval_hours = self.config.get("retrain_interval_hours", 168)
        self.min_retrain_samples = self.config.get("min_retrain_samples", 200)
        self.rolling_window = self.config.get("rolling_window", 2000)
        self.model_candidates = self.config.get("model_candidates", ["lightgbm"])

        # 数据缓存
        self._data_cache: dict[str, pd.DataFrame] = {}
        self._cache_timestamps: dict[str, datetime] = {}
        self._cache_max_age_hours = self.config.get("cache_max_age_hours", 1)

        # 模型版本管理
        self._model_versions: list[dict] = []
        self._current_version: int = 0
        self._max_versions = self.config.get("max_model_versions", 10)

        # 状态
        self._last_retrain_time: datetime | None = None
        self._retrain_count = 0
        self._performance_history: list[dict] = []

    def should_retrain(self) -> bool:
        """
        判断是否需要重新训练

        Returns:
            是否需要重训练
        """
        if self._last_retrain_time is None:
            return True

        elapsed = (datetime.now() - self._last_retrain_time).total_seconds() / 3600
        return elapsed >= self.retrain_interval_hours

    def rolling_retrain(
        self,
        symbols: list[str],
        freq: str = "1h",
        limit: int | None = None,
    ) -> dict:
        """
        滚动重训练

        使用最新数据重新训练模型，如果新模型表现更好则自动切换。

        Args:
            symbols: 交易对列表
            freq: 数据频率
            limit: 数据量（默认使用滚动窗口配置）

        Returns:
            重训练结果
        """
        if limit is None:
            limit = self.rolling_window

        logger.info(f"开始滚动重训练: 交易对={symbols}, 数据量={limit}")

        # 1. 收集最新数据（使用全量本地累积数据，以获得更多样本）
        raw_data = self.collector.collect_full_dataset(
            symbols, freq=freq, limit=limit, use_all_local=True
        )
        if raw_data.empty:
            logger.error("数据收集失败，跳过重训练")
            return {"error": "数据收集失败"}

        # 2. 计算因子和标签
        processed = self.handler.process_dataset(raw_data)
        features = processed["features"]
        label = processed["label"]

        # 3. 数据分割（滚动窗口方式）
        timestamps = (
            features.index.get_level_values("datetime").unique().sort_values()
            if isinstance(features.index, pd.MultiIndex)
            else features.index.sort_values()
        )

        n_total = len(timestamps)
        if n_total < self.min_retrain_samples:
            logger.warning(f"数据量不足: {n_total} < {self.min_retrain_samples}，跳过重训练")
            return {"error": "数据量不足", "samples": n_total}

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

        X_train = self.handler.fit_transform(features[train_mask])
        y_train = label[train_mask]
        X_valid = self.handler.transform(features[valid_mask])
        y_valid = label[valid_mask]
        X_test = self.handler.transform(features[test_mask])
        y_test = label[test_mask]

        # 4. 训练新模型
        new_models = self.trainer.train_all(
            X_train,
            y_train,
            X_valid,
            y_valid,
            model_types=self.model_candidates,
        )

        # 5. 评估新模型
        evaluation_results = {}
        for model_type, _model in new_models.items():
            pred = self.trainer.predict(model_type, X_test)
            eval_result = self.evaluator.evaluate(pred, y_test, freq=freq)
            evaluation_results[model_type] = eval_result

        # 6. 选择最优模型
        best_model = self.evaluator.select_best_model(evaluation_results)

        # 7. 记录版本
        version_info = {
            "version": self._current_version + 1,
            "timestamp": datetime.now().isoformat(),
            "best_model": best_model,
            "evaluation": evaluation_results.get(best_model, {}),
            "train_samples": int(train_mask.sum()),
            "test_samples": int(test_mask.sum()),
        }

        # 8. 判断是否切换到新模型
        should_switch = self._should_switch_model(
            new_eval=evaluation_results.get(best_model, {}),
        )

        if should_switch:
            self._current_version += 1
            self._model_versions.append(version_info)
            # 保持版本数量限制
            if len(self._model_versions) > self._max_versions:
                self._model_versions = self._model_versions[-self._max_versions :]

            self.trainer.save_model(best_model, tag=f"v{self._current_version}")
            logger.info(
                f"模型已切换到新版本 v{self._current_version}: "
                f"{best_model} (ICIR={evaluation_results[best_model].get('ICIR', 0):.4f})"
            )
        else:
            logger.info("新模型未通过性能验证，保持当前模型")

        # 更新状态
        self._last_retrain_time = datetime.now()
        self._retrain_count += 1
        self._performance_history.append(
            {
                "timestamp": datetime.now().isoformat(),
                "evaluation": evaluation_results,
                "switched": should_switch,
            }
        )

        # 更新数据缓存
        for symbol in symbols:
            self._data_cache[symbol] = raw_data
            self._cache_timestamps[symbol] = datetime.now()

        return {
            "models_trained": list(new_models.keys()),
            "best_model": best_model,
            "evaluation": evaluation_results,
            "switched": should_switch,
            "version": self._current_version,
            "retrain_count": self._retrain_count,
        }

    def _should_switch_model(self, new_eval: dict) -> bool:
        """
        判断是否应该切换到新模型

        条件：
        1. 当前无模型时必须切换
        2. 新模型 ICIR > 0（正向预测能力）
        3. 新模型 IC > 0（基本预测方向正确）

        Args:
            new_eval: 新模型的评估结果

        Returns:
            是否切换
        """
        # 首次训练，直接采用
        if not self._model_versions:
            return True

        # 检查基本质量
        new_icir = new_eval.get("ICIR", 0)
        new_ic = new_eval.get("IC", 0)

        if new_icir <= 0 or new_ic <= 0:
            logger.info(f"新模型预测质量不足: IC={new_ic:.4f}, ICIR={new_icir:.4f}")
            return False

        # 对比上一版本
        prev_eval = self._model_versions[-1].get("evaluation", {})
        prev_icir = prev_eval.get("ICIR", 0)

        # 新模型 ICIR 不低于旧模型的 80%
        threshold = self.config.get("switch_threshold", 0.8)
        if new_icir < prev_icir * threshold:
            logger.info(
                f"新模型 ICIR ({new_icir:.4f}) 不及旧模型 ({prev_icir:.4f}) 的 {threshold * 100}%"
            )
            return False

        return True

    def get_cached_data(
        self,
        symbol: str,
        max_age_hours: float | None = None,
    ) -> pd.DataFrame | None:
        """
        获取缓存的数据

        Args:
            symbol: 交易对
            max_age_hours: 最大缓存时间（小时），None 使用默认配置

        Returns:
            缓存的 DataFrame 或 None（缓存过期/不存在）
        """
        if symbol not in self._data_cache:
            return None

        if max_age_hours is None:
            max_age_hours = self._cache_max_age_hours

        cache_time = self._cache_timestamps.get(symbol)
        if cache_time is None:
            return None

        elapsed = (datetime.now() - cache_time).total_seconds() / 3600
        if elapsed > max_age_hours:
            return None

        return self._data_cache[symbol]

    def update_data_cache(
        self,
        symbol: str,
        data: pd.DataFrame,
    ) -> None:
        """
        更新数据缓存

        Args:
            symbol: 交易对
            data: 最新数据
        """
        self._data_cache[symbol] = data
        self._cache_timestamps[symbol] = datetime.now()

    def get_model_history(self) -> list[dict]:
        """获取模型版本历史"""
        return self._model_versions.copy()

    def get_performance_trend(self) -> pd.DataFrame:
        """
        获取模型性能趋势

        Returns:
            包含每次重训练评估结果的 DataFrame
        """
        if not self._performance_history:
            return pd.DataFrame()

        records = []
        for entry in self._performance_history:
            for model_type, eval_result in entry.get("evaluation", {}).items():
                records.append(
                    {
                        "timestamp": entry["timestamp"],
                        "model": model_type,
                        "IC": eval_result.get("IC", 0),
                        "ICIR": eval_result.get("ICIR", 0),
                        "夏普比率": eval_result.get("夏普比率", 0),
                        "switched": entry["switched"],
                    }
                )

        return pd.DataFrame(records)

    def get_status(self) -> dict:
        """获取在线管理器状态"""
        return {
            "last_retrain_time": (
                self._last_retrain_time.isoformat() if self._last_retrain_time else None
            ),
            "retrain_count": self._retrain_count,
            "current_version": self._current_version,
            "model_versions": len(self._model_versions),
            "cached_symbols": list(self._data_cache.keys()),
            "should_retrain": self.should_retrain(),
        }
