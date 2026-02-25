"""
模型训练管线

支持多种 ML 模型的训练、评估和选择。
采用 scikit-learn 兼容接口，可灵活扩展。
"""

import logging
import pickle
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger("QuantFlow.QLib")


class QLibModelTrainer:
    """
    多模型训练管线

    职责：
    1. 训练多种候选模型（LightGBM、Linear、XGBoost 等）
    2. 在验证集上评估模型性能
    3. 选择最优模型
    4. 模型持久化（保存/加载）
    """

    # 支持的模型类型及其默认参数
    MODEL_CONFIGS = {
        "lightgbm": {
            "class": "LGBModel",
            "default_params": {
                "loss": "mse",
                "learning_rate": 0.05,
                "num_leaves": 31,
                "max_depth": 6,
                "colsample_bytree": 0.85,
                "subsample": 0.85,
                "lambda_l1": 10,
                "lambda_l2": 10,
                "min_child_samples": 20,
                "num_threads": 4,
                "n_estimators": 500,
                "early_stopping_rounds": 50,
                "verbose": -1,
            },
        },
        "linear": {
            "class": "LinearModel",
            "default_params": {},
        },
        "xgboost": {
            "class": "XGBModel",
            "default_params": {
                "learning_rate": 0.05,
                "max_depth": 5,
                "n_estimators": 500,
                "colsample_bytree": 0.85,
                "subsample": 0.85,
                "reg_alpha": 1,
                "reg_lambda": 1,
                "eval_metric": "rmse",
                "early_stopping_rounds": 50,
                "verbosity": 0,
            },
        },
    }

    # LightGBM 跳过的最小样本量阈值（树模型在小数据集上易过拟合）
    MIN_SAMPLES_LIGHTGBM = 300

    # 高 NaN 列删除阈值
    HIGH_NAN_RATIO = 0.5

    def __init__(
        self,
        model_dir: str = "models",
        custom_params: dict | None = None,
        min_samples_lightgbm: int | None = None,
    ):
        """
        初始化模型训练器

        Args:
            model_dir: 模型保存目录
            custom_params: 自定义模型参数（覆盖默认值）
            min_samples_lightgbm: LightGBM 最小样本量阈值（可选，覆盖默认值）
        """
        self.model_dir = Path(model_dir)
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self.custom_params = custom_params or {}
        self.trained_models = {}
        self.evaluation_results = {}

        if min_samples_lightgbm is not None:
            self.MIN_SAMPLES_LIGHTGBM = min_samples_lightgbm

        # 训练集清洗参数（fit 后缓存，供 predict/transform 复用）
        self._train_medians: pd.Series | None = None
        self._dropped_columns: list[str] = []

    def train(
        self,
        model_type: str,
        X_train: pd.DataFrame,
        y_train: pd.Series,
        X_valid: pd.DataFrame | None = None,
        y_valid: pd.Series | None = None,
        params: dict | None = None,
    ):
        """
        训练单个模型

        Args:
            model_type: 模型类型（lightgbm/linear/xgboost）
            X_train: 训练特征
            y_train: 训练标签
            X_valid: 验证特征（用于早停）
            y_valid: 验证标签
            params: 模型参数（覆盖默认值）

        Returns:
            训练好的模型对象
        """
        if model_type not in self.MODEL_CONFIGS:
            raise ValueError(
                f"不支持的模型类型: {model_type}，支持: {list(self.MODEL_CONFIGS.keys())}"
            )

        # 合并参数：默认 → 自定义 → 传入
        model_params = {**self.MODEL_CONFIGS[model_type]["default_params"]}
        if model_type in self.custom_params:
            model_params.update(self.custom_params[model_type])
        if params:
            model_params.update(params)

        logger.info(f"开始训练模型: {model_type}, 训练样本={len(X_train)}")

        # 清理数据（在训练集上 fit，验证集上 transform）
        X_train_clean, y_train_clean = self._fit_clean_params(X_train, y_train)
        if X_valid is not None and y_valid is not None:
            X_valid_clean, y_valid_clean = self._apply_clean_params(X_valid, y_valid)
        else:
            X_valid_clean, y_valid_clean = None, None

        model = self._create_and_fit(
            model_type,
            model_params,
            X_train_clean,
            y_train_clean,
            X_valid_clean,
            y_valid_clean,
        )

        self.trained_models[model_type] = model
        logger.info(f"模型训练完成: {model_type}")
        return model

    def _create_and_fit(
        self,
        model_type,
        params,
        X_train,
        y_train,
        X_valid,
        y_valid,
    ):
        """创建模型实例并拟合"""
        if model_type == "lightgbm":
            return self._train_lightgbm(params, X_train, y_train, X_valid, y_valid)
        elif model_type == "linear":
            return self._train_linear(params, X_train, y_train)
        elif model_type == "xgboost":
            return self._train_xgboost(params, X_train, y_train, X_valid, y_valid)
        else:
            raise ValueError(f"未实现的模型类型: {model_type}")

    def _train_lightgbm(self, params, X_train, y_train, X_valid, y_valid):
        """训练 LightGBM 模型"""
        try:
            import lightgbm as lgb
        except ImportError:
            raise ImportError("请安装 lightgbm: pip install lightgbm")

        early_stopping_rounds = params.pop("early_stopping_rounds", 50)
        n_estimators = params.pop("n_estimators", 500)

        model = lgb.LGBMRegressor(n_estimators=n_estimators, **params)

        fit_params = {}
        if X_valid is not None and y_valid is not None:
            fit_params["eval_set"] = [(X_valid, y_valid)]
            fit_params["callbacks"] = [
                lgb.early_stopping(stopping_rounds=early_stopping_rounds),
                lgb.log_evaluation(period=0),
            ]

        model.fit(X_train, y_train, **fit_params)
        return model

    def _train_linear(self, params, X_train, y_train):
        """训练线性回归模型"""
        from sklearn.linear_model import Ridge

        model = Ridge(alpha=1.0, **params)
        model.fit(X_train, y_train)
        return model

    def _train_xgboost(self, params, X_train, y_train, X_valid, y_valid):
        """训练 XGBoost 模型"""
        try:
            import xgboost as xgb
        except ImportError:
            raise ImportError("请安装 xgboost: pip install xgboost")

        early_stopping_rounds = params.pop("early_stopping_rounds", 50)
        n_estimators = params.pop("n_estimators", 500)

        model = xgb.XGBRegressor(n_estimators=n_estimators, **params)

        fit_params = {}
        if X_valid is not None and y_valid is not None:
            fit_params["eval_set"] = [(X_valid, y_valid)]
            fit_params["verbose"] = False
            # 将 early_stopping_rounds 传递给 fit，启用早停防止过拟合
            if early_stopping_rounds and early_stopping_rounds > 0:
                fit_params["early_stopping_rounds"] = early_stopping_rounds

        model.fit(X_train, y_train, **fit_params)
        return model

    def _fit_clean_params(
        self,
        X: pd.DataFrame,
        y: pd.Series,
    ) -> tuple[pd.DataFrame, pd.Series]:
        """
        在训练集上拟合清洗参数并清理数据（fit + transform）

        计算中位数和需要删除的列，缓存到实例上，
        供 _apply_clean_params 和 predict 复用，防止数据泄露。

        Args:
            X: 训练特征
            y: 训练标签

        Returns:
            (清理后的特征, 清理后的标签)
        """
        # 对齐索引
        common_index = X.index.intersection(y.index)
        X = X.loc[common_index]
        y = y.loc[common_index]

        # 移除标签为 NaN 的样本
        valid_mask = y.notna()
        X = X[valid_mask]
        y = y[valid_mask]

        # 处理特征中的无穷值
        X = X.replace([np.inf, -np.inf], np.nan)

        # 删除 NaN 比例超过阈值的特征列（仅在训练集上决定）
        nan_ratio = X.isna().mean()
        self._dropped_columns = nan_ratio[nan_ratio > self.HIGH_NAN_RATIO].index.tolist()
        if self._dropped_columns:
            logger.warning(
                f"删除 {len(self._dropped_columns)} 个高 NaN 列 "
                f"(>{self.HIGH_NAN_RATIO:.0%}): {self._dropped_columns[:5]}"
            )
            X = X.drop(columns=self._dropped_columns)

        # 在训练集上计算中位数并缓存
        self._train_medians = X.median()
        X = X.fillna(self._train_medians)

        logger.debug(
            f"数据清理(fit): {valid_mask.sum()}/{len(valid_mask)} 个有效样本, "
            f"{len(X.columns)} 个特征列"
        )
        return X, y

    def _apply_clean_params(
        self,
        X: pd.DataFrame,
        y: pd.Series | None = None,
    ) -> tuple[pd.DataFrame, pd.Series | None]:
        """
        使用已拟合的训练集参数清理数据（仅 transform）

        复用 _fit_clean_params 缓存的中位数和删除列，
        确保验证集/测试集使用训练集的统计量，防止数据泄露。

        Args:
            X: 特征
            y: 标签（可选）

        Returns:
            (清理后的特征, 清理后的标签)
        """
        if y is not None:
            common_index = X.index.intersection(y.index)
            X = X.loc[common_index]
            y = y.loc[common_index]

            valid_mask = y.notna()
            X = X[valid_mask]
            y = y[valid_mask]

        X = X.replace([np.inf, -np.inf], np.nan)

        # 删除训练时确定的列
        cols_to_drop = [c for c in self._dropped_columns if c in X.columns]
        if cols_to_drop:
            X = X.drop(columns=cols_to_drop)

        # 使用训练集的中位数填充
        if self._train_medians is not None:
            # 只使用当前列存在的中位数
            fill_values = self._train_medians.reindex(X.columns, fill_value=0)
            X = X.fillna(fill_values)
        else:
            X = X.fillna(0)

        return X, y

    def train_all(
        self,
        X_train: pd.DataFrame,
        y_train: pd.Series,
        X_valid: pd.DataFrame | None = None,
        y_valid: pd.Series | None = None,
        model_types: list[str] | None = None,
    ) -> dict:
        """
        训练所有候选模型

        Args:
            X_train: 训练特征
            y_train: 训练标签
            X_valid: 验证特征
            y_valid: 验证标签
            model_types: 要训练的模型类型列表

        Returns:
            {model_type: model} 字典
        """
        if model_types is None:
            model_types = list(self.MODEL_CONFIGS.keys())

        results = {}
        for model_type in model_types:
            try:
                # 训练样本不足时跳过 LightGBM（树模型易过拟合小数据集）
                if model_type == "lightgbm" and len(X_train) < self.MIN_SAMPLES_LIGHTGBM:
                    logger.warning(
                        f"训练样本不足 ({len(X_train)} < {self.MIN_SAMPLES_LIGHTGBM})，跳过 LightGBM"
                    )
                    continue

                model = self.train(model_type, X_train, y_train, X_valid, y_valid)
                results[model_type] = model
            except Exception as e:
                logger.error(f"训练 {model_type} 失败: {e}")

        return results

    def predict(
        self,
        model_type: str,
        X: pd.DataFrame,
    ) -> pd.Series:
        """
        使用指定模型生成预测

        Args:
            model_type: 模型类型
            X: 特征 DataFrame

        Returns:
            预测分数 Series
        """
        if model_type not in self.trained_models:
            raise ValueError(f"模型 {model_type} 尚未训练")

        model = self.trained_models[model_type]

        # 清理输入（使用训练集的统计量，防止数据泄露）
        X_clean = X.replace([np.inf, -np.inf], np.nan)
        # 删除训练时确定的列
        cols_to_drop = [c for c in self._dropped_columns if c in X_clean.columns]
        if cols_to_drop:
            X_clean = X_clean.drop(columns=cols_to_drop)
        # 使用训练集的中位数填充（而非当前数据的中位数）
        if self._train_medians is not None:
            fill_values = self._train_medians.reindex(X_clean.columns, fill_value=0)
            X_clean = X_clean.fillna(fill_values)
        else:
            X_clean = X_clean.fillna(0)

        predictions = model.predict(X_clean)
        return pd.Series(predictions, index=X.index, name="score")

    def find_latest_model(self, tag: str = "best") -> tuple[Path, str, datetime] | None:
        """
        查找指定 tag 的最新模型文件

        从 model_dir 中扫描匹配 *_{tag}_*.pkl 的文件，
        根据文件名中的时间戳找到最新的模型。

        Args:
            tag: 模型标签（默认 "best"）

        Returns:
            (文件路径, 模型类型, 训练时间) 或 None（未找到）
        """
        pattern = f"*_{tag}_*.pkl"
        candidates = list(self.model_dir.glob(pattern))

        if not candidates:
            logger.info(f"未找到匹配 '{pattern}' 的模型文件")
            return None

        best_match = None
        latest_time = None

        for path in candidates:
            # 文件名格式: {model_type}_{tag}_{YYYYmmdd_HHMMSS}.pkl
            stem = path.stem  # 去掉 .pkl
            parts = stem.split(f"_{tag}_")
            if len(parts) != 2:
                continue

            model_type = parts[0]
            timestamp_str = parts[1]

            try:
                train_time = datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
            except ValueError:
                logger.warning(f"无法解析模型文件时间戳: {path.name}")
                continue

            if latest_time is None or train_time > latest_time:
                latest_time = train_time
                best_match = (path, model_type, train_time)

        if best_match:
            logger.info(
                f"找到最新模型: {best_match[0].name}, "
                f"类型={best_match[1]}, 训练时间={best_match[2]}"
            )
        return best_match

    def save_model(self, model_type: str, tag: str = "") -> Path:
        """
        保存模型到磁盘

        Args:
            model_type: 模型类型
            tag: 模型标签（用于区分不同版本）

        Returns:
            保存路径
        """
        if model_type not in self.trained_models:
            raise ValueError(f"模型 {model_type} 尚未训练")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{model_type}_{tag}_{timestamp}.pkl" if tag else f"{model_type}_{timestamp}.pkl"
        path = self.model_dir / filename

        with open(path, "wb") as f:
            pickle.dump(self.trained_models[model_type], f)

        logger.info(f"模型已保存: {path}")
        return path

    def load_model(self, path: str | Path, model_type: str = "loaded") -> object:
        """
        从磁盘加载模型

        Args:
            path: 模型文件路径
            model_type: 模型类型标识

        Returns:
            加载的模型对象
        """
        with open(path, "rb") as f:
            model = pickle.load(f)  # noqa: S301

        self.trained_models[model_type] = model
        logger.info(f"模型已加载: {path}")
        return model

    def get_feature_importance(self, model_type: str = "lightgbm") -> pd.Series | None:
        """
        获取特征重要性（仅树模型支持）

        Args:
            model_type: 模型类型

        Returns:
            特征重要性 Series（按重要性降序排列）
        """
        if model_type not in self.trained_models:
            return None

        model = self.trained_models[model_type]

        if hasattr(model, "feature_importances_"):
            importance = pd.Series(
                model.feature_importances_,
                index=getattr(model, "feature_names_in_", range(len(model.feature_importances_))),
                name="importance",
            )
            return importance.sort_values(ascending=False)

        return None
