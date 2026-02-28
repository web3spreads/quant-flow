"""
模型评估和信号分析（v2 重构版）

v2 改进：
- 改进 IC/ICIR 计算：时序 IC 替代截面 IC（品种过少时）
- 过拟合检测：对比训练/测试 IC 差异
- 分组回测：按预测分数分组统计实际收益
- 模型选择改用综合评分（IC + ICIR + 稳定性）
"""

import logging
import warnings

import numpy as np
import pandas as pd

logger = logging.getLogger("QuantFlow.QLib")


class ModelEvaluator:
    """
    模型评估器（v2 版本）

    提供量化投资领域标准的模型评估指标：
    - IC（信息系数）：预测值与实际收益的相关系数
    - Rank IC：基于排名的 IC，更加鲁棒
    - ICIR：IC 的信息比率，衡量 IC 的稳定性
    - 过拟合度：训练/测试性能差异
    - 分组回测：按预测分数分层统计
    """

    def evaluate(
        self,
        predictions: pd.Series,
        labels: pd.Series,
        freq: str = "1h",
        train_predictions: pd.Series | None = None,
        train_labels: pd.Series | None = None,
    ) -> dict:
        """
        全面评估模型性能

        Args:
            predictions: 测试集预测分数
            labels: 测试集实际标签（收益率）
            freq: 数据频率（用于年化计算）
            train_predictions: 训练集预测（用于过拟合检测）
            train_labels: 训练集标签（用于过拟合检测）

        Returns:
            评估指标字典
        """
        # 对齐索引
        common_idx = predictions.index.intersection(labels.index)
        pred = predictions.loc[common_idx].dropna()
        label = labels.loc[common_idx].dropna()
        common_idx = pred.index.intersection(label.index)
        pred = pred.loc[common_idx]
        label = label.loc[common_idx]

        if len(pred) < 10:
            logger.warning(f"有效样本太少: {len(pred)}，无法评估")
            return {"error": "样本不足"}

        results = {}

        # 预测值为常数时，corr/qcut 都会出问题，提前返回降级结果
        if pred.std() == 0:
            logger.warning("预测值为常数（标准差=0），返回降级评估结果")
            results["IC"] = float("nan")
            results["Rank_IC"] = float("nan")
            results["IC_均值"] = float("nan")
            results["IC_标准差"] = 0
            results["ICIR"] = float("nan")
            results["分组单调性"] = 0
            results["多头组均值"] = 0
            results["空头组均值"] = 0
            results["多空收益差"] = 0
            results["年化收益率"] = 0
            results["夏普比率"] = 0
            results["最大回撤"] = 0
            results["样本数"] = len(pred)
            results["预测均值"] = pred.mean()
            results["预测标准差"] = 0.0
            return results

        # IC 和 Rank IC（抑制极端情况下的 numpy 除零警告）
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            results["IC"] = pred.corr(label)
            results["Rank_IC"] = pred.rank().corr(label.rank())

        # 时序滚动 IC（更适合少量品种的情况）
        ic_series = self._calculate_rolling_ic(pred, label, window=min(20, len(pred) // 3))
        if len(ic_series) > 1:
            results["IC_均值"] = ic_series.mean()
            results["IC_标准差"] = ic_series.std()
            results["ICIR"] = ic_series.mean() / (ic_series.std() + 1e-12)
        else:
            results["IC_均值"] = results["IC"]
            results["IC_标准差"] = 0
            results["ICIR"] = 0

        # 分组回测（预测分数分3层）
        group_returns = self._grouped_backtest(pred, label, n_groups=3)
        if group_returns is not None:
            results["分组单调性"] = group_returns.get("monotonicity", 0)
            results["多头组均值"] = group_returns.get("long_mean", 0)
            results["空头组均值"] = group_returns.get("short_mean", 0)
            results["多空收益差"] = group_returns.get("long_short_spread", 0)

        # 多空策略收益评估
        long_short_returns = self._simulate_long_short(pred, label)
        if long_short_returns is not None and len(long_short_returns) > 0:
            annualize_factor = self._get_annualize_factor(freq)
            results["年化收益率"] = long_short_returns.mean() * annualize_factor
            results["年化波动率"] = long_short_returns.std() * np.sqrt(annualize_factor)
            results["夏普比率"] = results["年化收益率"] / (results["年化波动率"] + 1e-12)
            results["最大回撤"] = self._max_drawdown(long_short_returns)
            results["卡尔马比率"] = results["年化收益率"] / (abs(results["最大回撤"]) + 1e-12)
            results["胜率"] = (long_short_returns > 0).mean()
        else:
            results["年化收益率"] = 0
            results["夏普比率"] = 0
            results["最大回撤"] = 0

        # 过拟合检测
        if train_predictions is not None and train_labels is not None:
            overfit_metrics = self._detect_overfit(train_predictions, train_labels, pred, label)
            results.update(overfit_metrics)

        # 样本统计
        results["样本数"] = len(pred)
        results["预测均值"] = pred.mean()
        results["预测标准差"] = pred.std()

        return results

    def _detect_overfit(
        self,
        train_pred: pd.Series,
        train_label: pd.Series,
        test_pred: pd.Series,
        test_label: pd.Series,
    ) -> dict:
        """
        检测过拟合程度

        通过对比训练集和测试集的 IC 差异来判断过拟合程度。
        overfit_ratio > 2 表示严重过拟合。

        Returns:
            {"train_IC": float, "test_IC": float, "overfit_ratio": float}
        """
        # 训练集 IC
        common = train_pred.index.intersection(train_label.index)
        tp = train_pred.loc[common].dropna()
        tl = train_label.loc[common].dropna()
        common = tp.index.intersection(tl.index)
        train_ic = tp.loc[common].corr(tl.loc[common]) if len(common) > 10 else 0

        # 测试集 IC
        common = test_pred.index.intersection(test_label.index)
        sp = test_pred.loc[common].dropna()
        sl = test_label.loc[common].dropna()
        common = sp.index.intersection(sl.index)
        test_ic = sp.loc[common].corr(sl.loc[common]) if len(common) > 10 else 0

        # 过拟合比率：训练IC / 测试IC
        if abs(test_ic) > 1e-6:
            overfit_ratio = abs(train_ic) / abs(test_ic)
        else:
            overfit_ratio = float("inf") if abs(train_ic) > 0.01 else 1.0

        logger.info(
            f"过拟合检测: train_IC={train_ic:.4f}, test_IC={test_ic:.4f}, "
            f"过拟合比率={overfit_ratio:.2f}"
        )

        return {
            "train_IC": train_ic,
            "test_IC": test_ic,
            "过拟合比率": overfit_ratio,
        }

    def _grouped_backtest(
        self,
        predictions: pd.Series,
        labels: pd.Series,
        n_groups: int = 3,
    ) -> dict | None:
        """
        分组回测：按预测分数分层统计实际收益

        理想情况下，预测分数高的组应有更高的实际收益（单调递增）。

        Args:
            predictions: 预测分数
            labels: 实际收益
            n_groups: 分组数

        Returns:
            {"group_means": list, "monotonicity": float, ...}
        """
        if len(predictions) < n_groups * 5:
            return None

        combined = pd.DataFrame({"pred": predictions, "label": labels})
        combined = combined.dropna()

        if len(combined) < n_groups * 5:
            return None

        # 按预测分数分组
        combined["group"] = pd.qcut(combined["pred"], n_groups, labels=False, duplicates="drop")
        group_means = combined.groupby("group")["label"].mean()

        # 单调性检测：计算排列的 Spearman 相关
        if len(group_means) >= 2:
            monotonicity = group_means.corr(
                pd.Series(range(len(group_means)), index=group_means.index)
            )
        else:
            monotonicity = 0

        return {
            "group_means": group_means.tolist(),
            "monotonicity": monotonicity if not np.isnan(monotonicity) else 0,
            "long_mean": group_means.iloc[-1] if len(group_means) > 0 else 0,
            "short_mean": group_means.iloc[0] if len(group_means) > 0 else 0,
            "long_short_spread": (
                group_means.iloc[-1] - group_means.iloc[0] if len(group_means) > 1 else 0
            ),
        }

    def _calculate_rolling_ic(
        self,
        predictions: pd.Series,
        labels: pd.Series,
        window: int = 20,
    ) -> pd.Series:
        """
        计算滚动 IC

        Args:
            predictions: 预测值
            labels: 实际标签
            window: 滚动窗口大小

        Returns:
            滚动 IC Series
        """
        window = max(5, min(window, len(predictions) // 2))
        combined = pd.DataFrame({"pred": predictions, "label": labels})
        ic_series = combined["pred"].rolling(window).corr(combined["label"])
        return ic_series.dropna()

    def _simulate_long_short(
        self,
        predictions: pd.Series,
        labels: pd.Series,
        quantile: float = 0.2,
    ) -> pd.Series | None:
        """
        模拟多空策略收益

        Args:
            predictions: 预测分数
            labels: 实际收益
            quantile: 分位数阈值

        Returns:
            多空策略的收益 Series
        """
        if isinstance(predictions.index, pd.MultiIndex):
            return self._simulate_long_short_cross_section(predictions, labels, quantile)

        long_threshold = predictions.quantile(1 - quantile)
        short_threshold = predictions.quantile(quantile)

        returns = pd.Series(0.0, index=predictions.index)
        returns[predictions >= long_threshold] = labels[predictions >= long_threshold]
        returns[predictions <= short_threshold] = -labels[predictions <= short_threshold]

        return returns

    def _simulate_long_short_cross_section(
        self,
        predictions: pd.Series,
        labels: pd.Series,
        quantile: float,
    ) -> pd.Series | None:
        """截面多空策略（多个交易对时使用）"""
        combined = pd.DataFrame({"pred": predictions, "label": labels})

        if not isinstance(combined.index, pd.MultiIndex):
            return None

        returns = []
        for dt in combined.index.get_level_values(0).unique():
            dt_data = combined.xs(dt, level=0)
            if len(dt_data) < 3:
                continue
            sorted_data = dt_data.sort_values("pred")
            n_long = max(1, int(len(sorted_data) * quantile))
            long_ret = sorted_data["label"].tail(n_long).mean()
            short_ret = sorted_data["label"].head(n_long).mean()
            returns.append({"datetime": dt, "return": long_ret - short_ret})

        if not returns:
            return None

        return pd.DataFrame(returns).set_index("datetime")["return"]

    def _max_drawdown(self, returns: pd.Series) -> float:
        """计算最大回撤"""
        cumulative = (1 + returns).cumprod()
        peak = cumulative.expanding().max()
        drawdown = (cumulative - peak) / peak
        return drawdown.min()

    def _get_annualize_factor(self, freq: str) -> float:
        """获取年化因子（加密货币 24/7 交易）"""
        freq_hours = {
            "1min": 1 / 60,
            "5min": 5 / 60,
            "15min": 15 / 60,
            "30min": 30 / 60,
            "1h": 1,
            "4h": 4,
            "1d": 24,
        }
        hours = freq_hours.get(freq, 1)
        periods_per_year = (365 * 24) / hours
        return periods_per_year

    def compare_models(
        self,
        model_results: dict[str, dict],
    ) -> pd.DataFrame:
        """
        对比多个模型的评估结果

        Args:
            model_results: {模型名: 评估结果字典}

        Returns:
            对比结果 DataFrame
        """
        comparison = pd.DataFrame(model_results).T
        comparison = comparison.sort_values("ICIR", ascending=False)

        logger.info(f"模型对比结果:\n{comparison.to_string()}")
        return comparison

    def select_best_model(
        self,
        model_results: dict[str, dict],
        metric: str = "ICIR",
        cv_results: dict | None = None,
    ) -> str:
        """
        根据综合评分选择最优模型

        评分 = ICIR * 0.4 + IC * 0.3 + 分组单调性 * 0.2 + CV_ICIR * 0.1

        NaN 指标的模型会被自动排除。

        Args:
            model_results: {模型名: 评估结果字典}
            metric: 主要评选指标
            cv_results: Purged K-Fold 交叉验证结果（可选）

        Returns:
            最优模型名称
        """

        def _safe_float(val, default=0.0):
            """安全转换为 float，兼容 np.float64/None/NaN/Inf"""
            if val is None or pd.isna(val):
                return None
            v = float(val)
            if np.isinf(v):
                return None
            return v

        def _composite_score(k):
            """计算综合评分"""
            result = model_results[k]

            # 基础检查：预测常数的模型直接排除
            pred_std = _safe_float(result.get("预测标准差", -1), -1)
            if pred_std is not None and pred_std == 0:
                logger.warning(f"模型 {k} 预测标准差为 0（输出常数），排除")
                return float("-inf")

            icir = _safe_float(result.get("ICIR", 0))
            ic = _safe_float(result.get("IC", 0))

            # 检查 NaN/Inf
            for val, name in [(icir, "ICIR"), (ic, "IC")]:
                if val is None:
                    logger.warning(f"模型 {k} 的 {name} 为 NaN/Inf，排除")
                    return float("-inf")

            # 综合评分：不取绝对值，保持方向一致性
            # IC < 0 说明预测方向反了，应该给惩罚而非奖励
            ic_contrib = ic * 1.5 if ic > 0 else ic * 0.3
            icir_contrib = icir  # ICIR 也保留原值方向
            score = icir_contrib * 0.4 + ic_contrib * 0.3

            # 分组单调性加分
            monotonicity = _safe_float(result.get("分组单调性", 0))
            if monotonicity is not None:
                score += monotonicity * 0.2

            # CV 结果加分
            if cv_results and k in cv_results:
                cv_icir = _safe_float(cv_results[k].get("icir_cv", 0))
                if cv_icir is not None:
                    score += cv_icir * 0.1

            # 过拟合惩罚
            overfit_ratio = _safe_float(result.get("过拟合比率", 1.0))
            if overfit_ratio is not None:
                if overfit_ratio > 3:
                    score *= 0.5  # 严重过拟合，减半
                elif overfit_ratio > 2:
                    score *= 0.7  # 中度过拟合

            return score

        best_model = max(model_results, key=_composite_score)
        best_score = _composite_score(best_model)
        if best_score == float("-inf"):
            best_model = next(iter(model_results))
            logger.warning(f"所有模型指标均无效，默认选择: {best_model}")
        else:
            logger.info(
                f"最优模型: {best_model} "
                f"(综合评分={best_score:.4f}, "
                f"IC={model_results[best_model].get('IC', 0):.4f}, "
                f"ICIR={model_results[best_model].get('ICIR', 0):.4f})"
            )
        return best_model
