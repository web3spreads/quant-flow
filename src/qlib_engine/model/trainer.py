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
                "num_leaves": 128,
                "max_depth": 6,
                "colsample_bytree": 0.85,
                "subsample": 0.85,
                "lambda_l1": 10,
                "lambda_l2": 10,
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
                "max_depth": 6,
                "n_estimators": 500,
                "colsample_bytree": 0.85,
                "subsample": 0.85,
                "reg_alpha": 10,
                "reg_lambda": 10,
                "early_stopping_rounds": 50,
                "verbosity": 0,
            },
        },
    }

    def __init__(
        self,
        model_dir: str = "models",
        custom_params: dict | None = None,
    ):
        """
        初始化模型训练器

        Args:
            model_dir: 模型保存目录
            custom_params: 自定义模型参数（覆盖默认值）
        """
        self.model_dir = Path(model_dir)
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self.custom_params = custom_params or {}
        self.trained_models = {}
        self.evaluation_results = {}

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

        # 清理数据
        X_train_clean, y_train_clean = self._clean_data(X_train, y_train)
        if X_valid is not None and y_valid is not None:
            X_valid_clean, y_valid_clean = self._clean_data(X_valid, y_valid)
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

        params.pop("early_stopping_rounds", None)
        n_estimators = params.pop("n_estimators", 500)

        model = xgb.XGBRegressor(n_estimators=n_estimators, **params)

        fit_params = {}
        if X_valid is not None and y_valid is not None:
            fit_params["eval_set"] = [(X_valid, y_valid)]
            fit_params["verbose"] = False

        model.fit(X_train, y_train, **fit_params)
        return model

    def _clean_data(
        self,
        X: pd.DataFrame,
        y: pd.Series,
    ) -> tuple[pd.DataFrame, pd.Series]:
        """
        清理训练数据：移除标签为 NaN 的样本，处理特征中的无穷值

        Args:
            X: 特征
            y: 标签

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
        X = X.fillna(0)

        logger.debug(f"数据清理: {valid_mask.sum()}/{len(valid_mask)} 个有效样本")
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

        # 清理输入
        X_clean = X.replace([np.inf, -np.inf], np.nan).fillna(0)

        predictions = model.predict(X_clean)
        return pd.Series(predictions, index=X.index, name="score")

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
