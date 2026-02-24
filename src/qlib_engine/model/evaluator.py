"""
模型评估和信号分析

提供 IC/ICIR/夏普/回撤等多维度的模型评估指标，
帮助选择最优模型和验证信号有效性。
"""

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger("QuantFlow.QLib")


class ModelEvaluator:
    """
    模型评估器

    提供量化投资领域标准的模型评估指标：
    - IC（信息系数）：预测值与实际收益的相关系数
    - Rank IC：基于排名的 IC，更加鲁棒
    - ICIR：IC 的信息比率，衡量 IC 的稳定性
    - 年化收益率、夏普比率、最大回撤等
    """

    def evaluate(
        self,
        predictions: pd.Series,
        labels: pd.Series,
        freq: str = "1h",
    ) -> dict:
        """
        全面评估模型性能

        Args:
            predictions: 预测分数
            labels: 实际标签（收益率）
            freq: 数据频率（用于年化计算）

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

        # IC 和 Rank IC
        results["IC"] = pred.corr(label)
        results["Rank_IC"] = pred.rank().corr(label.rank())

        # 分期计算 IC（用于 ICIR）
        ic_series = self._calculate_rolling_ic(pred, label)
        if len(ic_series) > 1:
            results["IC_均值"] = ic_series.mean()
            results["IC_标准差"] = ic_series.std()
            results["ICIR"] = ic_series.mean() / (ic_series.std() + 1e-12)
        else:
            results["IC_均值"] = results["IC"]
            results["IC_标准差"] = 0
            results["ICIR"] = 0

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

        # 样本统计
        results["样本数"] = len(pred)
        results["预测均值"] = pred.mean()
        results["预测标准差"] = pred.std()

        return results

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

        做多预测分数最高的 quantile，做空最低的 quantile

        Args:
            predictions: 预测分数
            labels: 实际收益
            quantile: 分位数阈值

        Returns:
            多空策略的收益 Series
        """
        if isinstance(predictions.index, pd.MultiIndex):
            # 截面数据：在每个时间点上选择多空
            return self._simulate_long_short_cross_section(predictions, labels, quantile)

        # 时间序列：根据预测方向计算收益
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
            # 做多评分最高的，做空评分最低的
            sorted_data = dt_data.sort_values("pred")
            n_long = max(1, int(len(sorted_data) * quantile))
            long_ret = sorted_data["label"].tail(n_long).mean()
            short_ret = sorted_data["label"].head(n_long).mean()
            returns.append({"datetime": dt, "return": long_ret - short_ret})

        if not returns:
            return None

        return pd.DataFrame(returns).set_index("datetime")["return"]

    def _max_drawdown(self, returns: pd.Series) -> float:
        """
        计算最大回撤

        Args:
            returns: 收益序列

        Returns:
            最大回撤（负数）
        """
        cumulative = (1 + returns).cumprod()
        peak = cumulative.expanding().max()
        drawdown = (cumulative - peak) / peak
        return drawdown.min()

    def _get_annualize_factor(self, freq: str) -> float:
        """
        获取年化因子

        加密货币 24/7 交易，一年 = 365 天

        Args:
            freq: 数据频率

        Returns:
            年化因子
        """
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
        # 加密货币：365 天 × 24 小时
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
    ) -> str:
        """
        根据指定指标选择最优模型

        Args:
            model_results: {模型名: 评估结果字典}
            metric: 评选指标

        Returns:
            最优模型名称
        """
        best_model = max(model_results, key=lambda k: model_results[k].get(metric, 0))
        best_score = model_results[best_model].get(metric, 0)
        logger.info(f"最优模型: {best_model} ({metric}={best_score:.4f})")
        return best_model
