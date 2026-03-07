"""
模型训练管线（v2 重构版）

v2 改进：
- 新增 ElasticNet 模型（L1+L2 正则化线性模型）
- 实现 Purged K-Fold 时间序列交叉验证
- LightGBM 参数增强正则化防过拟合
- 过拟合检测（训练/测试 IC 对比）
- 支持多模型类型确保充分竞争
"""

import hashlib
import hmac
import logging
import os
import pickle
import secrets
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger("QuantFlow.QLib")


class QLibModelTrainer:
    """
    多模型训练管线（v2 版本）

    职责：
    1. 训练多种候选模型（LightGBM、Ridge、ElasticNet、XGBoost）
    2. 支持 Purged K-Fold 时间序列交叉验证
    3. 过拟合检测和模型健康度评估
    4. 模型持久化（保存/加载）
    """

    # 支持的模型类型及其默认参数
    MODEL_CONFIGS = {
        "lightgbm": {
            "class": "LGBModel",
            "default_params": {
                "objective": "mse",
                "learning_rate": 0.02,
                "num_leaves": 15,
                "max_depth": 4,
                "colsample_bytree": 0.6,
                "subsample": 0.6,
                "lambda_l1": 50,
                "lambda_l2": 50,
                "min_child_samples": 50,
                "num_threads": 4,
                "n_estimators": 300,
                "early_stopping_rounds": 30,
                "verbose": -1,
            },
        },
        "linear": {
            "class": "RidgeModel",
            "default_params": {
                "alpha": 10.0,
            },
        },
        "elasticnet": {
            "class": "ElasticNetModel",
            "default_params": {
                "alpha": 1.0,
                "l1_ratio": 0.5,
                "max_iter": 2000,
            },
        },
        "xgboost": {
            "class": "XGBModel",
            "default_params": {
                "learning_rate": 0.02,
                "max_depth": 3,
                "n_estimators": 300,
                "colsample_bytree": 0.6,
                "subsample": 0.6,
                "reg_alpha": 10,
                "reg_lambda": 10,
                "min_child_weight": 50,
                "eval_metric": "rmse",
                "early_stopping_rounds": 30,
                "verbosity": 0,
            },
        },
    }

    # LightGBM 跳过的最小样本量阈值
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
            min_samples_lightgbm: LightGBM 最小样本量阈值
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

        # 模型文件签名密钥（优先从环境变量读取，否则生成随机密钥并持久化）
        self._signing_key = self._load_or_create_signing_key()

    def _load_or_create_signing_key(self) -> bytes:
        """
        加载或创建模型签名密钥

        优先级：
        1. 环境变量 QLIB_MODEL_SIGNING_KEY（hex 编码）
        2. 模型目录下的 .signing_key 文件（自动生成的随机密钥）
        """
        # 优先从环境变量读取
        env_key = os.environ.get("QLIB_MODEL_SIGNING_KEY")
        if env_key:
            return bytes.fromhex(env_key)

        # 从文件加载或生成随机密钥
        key_file = self.model_dir / ".signing_key"
        if key_file.exists():
            return key_file.read_bytes()

        # 生成 32 字节随机密钥并持久化
        key = secrets.token_bytes(32)
        key_file.write_bytes(key)
        logger.info(f"已生成模型签名密钥: {key_file}")
        return key

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
            model_type: 模型类型（lightgbm/linear/elasticnet/xgboost）
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
        # 复制参数字典，避免 pop() 修改调用者传入的原始字典（CV 多次调用时会出 bug）
        params = params.copy()
        if model_type == "lightgbm":
            return self._train_lightgbm(params, X_train, y_train, X_valid, y_valid)
        elif model_type == "linear":
            return self._train_linear(params, X_train, y_train)
        elif model_type == "elasticnet":
            return self._train_elasticnet(params, X_train, y_train)
        elif model_type == "xgboost":
            return self._train_xgboost(params, X_train, y_train, X_valid, y_valid)
        else:
            raise ValueError(f"未实现的模型类型: {model_type}")

    # early stopping 要求的最小验证集样本量
    MIN_VALID_FOR_EARLY_STOPPING = 50

    def _train_lightgbm(self, params, X_train, y_train, X_valid, y_valid):
        """训练 LightGBM 模型"""
        try:
            import lightgbm as lgb
        except ImportError:
            raise ImportError("请安装 lightgbm: pip install lightgbm")

        early_stopping_rounds = params.pop("early_stopping_rounds", 30)
        n_estimators = params.pop("n_estimators", 300)

        model = lgb.LGBMRegressor(n_estimators=n_estimators, **params)

        fit_params = {}
        # 验证集样本量充足时才启用 early stopping，过小时不可靠
        if (
            X_valid is not None
            and y_valid is not None
            and len(X_valid) >= self.MIN_VALID_FOR_EARLY_STOPPING
        ):
            fit_params["eval_set"] = [(X_valid, y_valid)]
            fit_params["callbacks"] = [
                lgb.early_stopping(stopping_rounds=early_stopping_rounds),
                lgb.log_evaluation(period=0),
            ]
        elif X_valid is not None and y_valid is not None:
            logger.warning(
                f"验证集样本不足 ({len(X_valid)} < {self.MIN_VALID_FOR_EARLY_STOPPING})，"
                f"跳过 early stopping，使用全部 {n_estimators} 轮训练"
            )

        model.fit(X_train, y_train, **fit_params)
        return model

    def _train_linear(self, params, X_train, y_train):
        """训练 Ridge 线性回归模型"""
        from sklearn.linear_model import Ridge

        alpha = params.pop("alpha", 10.0)
        model = Ridge(alpha=alpha, **params)
        model.fit(X_train, y_train)
        return model

    def _train_elasticnet(self, params, X_train, y_train):
        """训练 ElasticNet 模型（L1+L2 正则化）"""
        from sklearn.linear_model import ElasticNet

        model = ElasticNet(**params)
        model.fit(X_train, y_train)
        return model

    def _train_xgboost(self, params, X_train, y_train, X_valid, y_valid):
        """训练 XGBoost 模型"""
        try:
            import xgboost as xgb
        except ImportError:
            raise ImportError("请安装 xgboost: pip install xgboost")

        early_stopping_rounds = params.pop("early_stopping_rounds", 30)
        n_estimators = params.pop("n_estimators", 300)

        # 新版 XGBoost (>=2.0) 要求 early_stopping_rounds 在构造器中传入
        # 仅在有验证集且验证集样本充足时启用 early stopping
        init_params = {"n_estimators": n_estimators}
        if (
            X_valid is not None
            and y_valid is not None
            and len(X_valid) >= self.MIN_VALID_FOR_EARLY_STOPPING
            and early_stopping_rounds
            and early_stopping_rounds > 0
        ):
            init_params["early_stopping_rounds"] = early_stopping_rounds
        elif X_valid is not None and y_valid is not None:
            logger.warning(
                f"验证集样本不足 ({len(X_valid)} < {self.MIN_VALID_FOR_EARLY_STOPPING})，"
                f"跳过 early stopping，使用全部 {n_estimators} 轮训练"
            )

        model = xgb.XGBRegressor(**init_params, **params)

        fit_params = {}
        if X_valid is not None and y_valid is not None:
            fit_params["eval_set"] = [(X_valid, y_valid)]
            fit_params["verbose"] = False

        model.fit(X_train, y_train, **fit_params)
        return model

    def _fit_clean_params(
        self,
        X: pd.DataFrame,
        y: pd.Series,
    ) -> tuple[pd.DataFrame, pd.Series]:
        """
        在训练集上拟合清洗参数并清理数据（fit + transform）

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

        # 删除 NaN 比例超过阈值的特征列
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
                # 训练样本不足时跳过 LightGBM
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
        cols_to_drop = [c for c in self._dropped_columns if c in X_clean.columns]
        if cols_to_drop:
            X_clean = X_clean.drop(columns=cols_to_drop)
        if self._train_medians is not None:
            fill_values = self._train_medians.reindex(X_clean.columns, fill_value=0)
            X_clean = X_clean.fillna(fill_values)
        else:
            X_clean = X_clean.fillna(0)

        predictions = model.predict(X_clean)
        return pd.Series(predictions, index=X.index, name="score")

    # ============================================================
    # Purged K-Fold 时间序列交叉验证
    # ============================================================

    def purged_kfold_train(
        self,
        features: pd.DataFrame,
        label: pd.Series,
        model_types: list[str] | None = None,
        n_splits: int = 5,
        purge_gap: int = 5,
    ) -> dict:
        """
        Purged K-Fold 时间序列交叉验证训练

        与普通 K-Fold 不同，Purged K-Fold：
        1. 按时间顺序分割（不随机打乱）
        2. 训练集和测试集之间留出 purge_gap 个时间步的间隔，防止标签泄漏
        3. 多折评估取平均，更鲁棒

        Args:
            features: 完整特征 DataFrame
            label: 完整标签 Series
            model_types: 模型类型列表
            n_splits: 折数
            purge_gap: 训练/测试间隔期数（防止标签泄漏）

        Returns:
            {model_type: {"mean_ic": float, "std_ic": float, "fold_ics": list}}
        """
        if model_types is None:
            model_types = list(self.MODEL_CONFIGS.keys())

        # 获取唯一时间戳
        if isinstance(features.index, pd.MultiIndex):
            timestamps = features.index.get_level_values("datetime").unique().sort_values()
        else:
            timestamps = features.index.sort_values()

        n_total = len(timestamps)
        fold_size = n_total // (n_splits + 1)  # 预留最后一折作为测试

        if fold_size < 30:
            logger.warning(f"数据量不足以进行 {n_splits}-Fold CV（每折 {fold_size} 个时间步）")
            return {}

        cv_results = {mt: {"fold_ics": [], "fold_rank_ics": []} for mt in model_types}

        for fold in range(n_splits):
            # 训练集: [0, train_end]
            train_end_idx = fold_size * (fold + 1)
            # 间隔: [train_end, train_end + purge_gap]
            test_start_idx = train_end_idx + purge_gap
            test_end_idx = test_start_idx + fold_size

            if test_end_idx > n_total:
                break

            train_end = timestamps[train_end_idx - 1]
            test_start = timestamps[test_start_idx]
            test_end = timestamps[min(test_end_idx - 1, n_total - 1)]

            # 分割数据
            if isinstance(features.index, pd.MultiIndex):
                dt_level = features.index.get_level_values("datetime")
                train_mask = dt_level <= train_end
                test_mask = (dt_level >= test_start) & (dt_level <= test_end)
            else:
                train_mask = features.index <= train_end
                test_mask = (features.index >= test_start) & (features.index <= test_end)

            X_train_fold = features[train_mask]
            y_train_fold = label[train_mask]
            X_test_fold = features[test_mask]
            y_test_fold = label[test_mask]

            if len(X_train_fold) < 50 or len(X_test_fold) < 10:
                continue

            # 训练并评估每个模型
            for model_type in model_types:
                try:
                    if model_type == "lightgbm" and len(X_train_fold) < self.MIN_SAMPLES_LIGHTGBM:
                        continue

                    # 创建临时训练器避免污染主状态
                    temp_trainer = QLibModelTrainer(
                        model_dir=str(self.model_dir),
                        custom_params=self.custom_params,
                        min_samples_lightgbm=self.MIN_SAMPLES_LIGHTGBM,
                    )

                    temp_trainer.train(model_type, X_train_fold, y_train_fold)
                    pred = temp_trainer.predict(model_type, X_test_fold)

                    # 计算 IC（抑制预测值方差为 0 时 numpy 的除零警告）
                    common_idx = pred.index.intersection(y_test_fold.dropna().index)
                    if len(common_idx) < 10:
                        continue

                    # 预测值为常数时 corr 会产生 NaN 并触发 RuntimeWarning，这里抑制
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", RuntimeWarning)
                        ic = pred.loc[common_idx].corr(y_test_fold.loc[common_idx])
                        rank_ic = (
                            pred.loc[common_idx].rank().corr(y_test_fold.loc[common_idx].rank())
                        )

                    if not np.isnan(ic):
                        cv_results[model_type]["fold_ics"].append(ic)
                    if not np.isnan(rank_ic):
                        cv_results[model_type]["fold_rank_ics"].append(rank_ic)

                    logger.debug(
                        f"CV Fold {fold + 1}/{n_splits} [{model_type}]: "
                        f"IC={ic:.4f}, Rank_IC={rank_ic:.4f}"
                    )
                except Exception as e:
                    logger.warning(f"CV Fold {fold + 1} [{model_type}] 失败: {e}")

        # 汇总结果
        for model_type in model_types:
            ics = cv_results[model_type]["fold_ics"]
            rank_ics = cv_results[model_type]["fold_rank_ics"]
            if ics:
                cv_results[model_type]["mean_ic"] = np.mean(ics)
                cv_results[model_type]["std_ic"] = np.std(ics)
                cv_results[model_type]["icir_cv"] = np.mean(ics) / (np.std(ics) + 1e-12)
            else:
                cv_results[model_type]["mean_ic"] = 0
                cv_results[model_type]["std_ic"] = 0
                cv_results[model_type]["icir_cv"] = 0
            if rank_ics:
                cv_results[model_type]["mean_rank_ic"] = np.mean(rank_ics)
            else:
                cv_results[model_type]["mean_rank_ic"] = 0

            logger.info(
                f"CV 汇总 [{model_type}]: "
                f"mean_IC={cv_results[model_type]['mean_ic']:.4f}, "
                f"std_IC={cv_results[model_type]['std_ic']:.4f}, "
                f"ICIR_CV={cv_results[model_type]['icir_cv']:.4f}, "
                f"mean_Rank_IC={cv_results[model_type]['mean_rank_ic']:.4f}"
            )

        return cv_results

    # ============================================================
    # 模型持久化
    # ============================================================

    def find_latest_model(self, tag: str = "best") -> tuple[Path, str, datetime] | None:
        """
        查找指定 tag 的最新模型文件

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
            stem = path.stem
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

    def save_model(self, model_type: str, tag: str = "", train_samples: int = 0) -> Path:
        """
        保存模型到磁盘

        Args:
            model_type: 模型类型
            tag: 模型标签
            train_samples: 训练时的数据总量（用于后续数据增量检测）

        Returns:
            保存路径
        """
        if model_type not in self.trained_models:
            raise ValueError(f"模型 {model_type} 尚未训练")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{model_type}_{tag}_{timestamp}.pkl" if tag else f"{model_type}_{timestamp}.pkl"
        path = self.model_dir / filename

        artifact = {
            "model": self.trained_models[model_type],
            "train_medians": self._train_medians,
            "dropped_columns": self._dropped_columns,
            "train_samples": train_samples,
        }
        payload = pickle.dumps(artifact)

        # 使用 HMAC 签名防止模型文件被篡改
        signature = hmac.new(self._signing_key, payload, hashlib.sha256).digest()
        with open(path, "wb") as f:
            f.write(signature)
            f.write(payload)

        logger.info(f"模型已保存（含清洗参数，训练样本={train_samples}）: {path}")
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
            raw = f.read()

        # 验证 HMAC 签名（新格式：前 32 字节为签名）
        if len(raw) > 32:
            stored_sig = raw[:32]
            payload = raw[32:]
            expected_sig = hmac.new(self._signing_key, payload, hashlib.sha256).digest()
            if hmac.compare_digest(stored_sig, expected_sig):
                data = pickle.loads(payload)  # noqa: S301
            else:
                # 签名不匹配，尝试作为旧格式无签名文件加载
                try:
                    data = pickle.loads(raw)  # noqa: S301
                    logger.warning(f"模型文件无有效签名，以旧格式加载: {path}")
                except Exception:
                    raise ValueError(f"模型文件签名验证失败且无法解析: {path}")
        else:
            raise ValueError(f"模型文件格式无效（文件过小）: {path}")

        if isinstance(data, dict) and "model" in data:
            model = data["model"]
            self._train_medians = data.get("train_medians")
            self._dropped_columns = data.get("dropped_columns", [])
            self._loaded_train_samples = data.get("train_samples", 0)
            logger.info(f"模型已加载（含清洗参数，训练样本={self._loaded_train_samples}）: {path}")
        else:
            model = data
            self._train_medians = None
            self._dropped_columns = []
            self._loaded_train_samples = 0
            logger.info(f"模型已加载（旧格式，无清洗参数）: {path}")

        self.trained_models[model_type] = model
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
