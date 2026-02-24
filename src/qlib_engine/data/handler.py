"""
加密货币专用 DataHandler

基于 QLib Alpha158 的设计理念，为加密货币永续合约定制的数据处理器。
由于 QLib 原生 DataHandler 依赖 QLib 的二进制数据存储，
本实现采用 pandas 原生方式处理数据，同时保持与 QLib 因子体系兼容。
"""

import logging

import numpy as np
import pandas as pd

from .perpetual import (
    get_feature_names,
)

logger = logging.getLogger("QuantFlow.QLib")


class CryptoAlpha158:
    """
    加密货币永续合约专用 DataHandler

    基于 Alpha158 因子集的设计思路，使用 pandas 原生计算因子。
    支持：
    - Alpha158 标准因子（K线形态、价格动量、均线偏离、波动率、滚动统计等）
    - 永续合约特有因子（资金费率、未平仓量、溢价率衍生因子）
    - 数据标准化（Z-Score、截面排名等）
    - 缺失值处理
    """

    def __init__(
        self,
        include_perpetual: bool = True,
        normalize: bool = True,
        fillna: bool = True,
        label_periods: int = 5,
    ):
        """
        初始化 DataHandler

        Args:
            include_perpetual: 是否包含永续合约特有因子
            normalize: 是否对特征进行标准化
            fillna: 是否填充缺失值
            label_periods: 标签的未来期数（预测未来 N 期收益率）
        """
        self.include_perpetual = include_perpetual
        self.normalize = normalize
        self.fillna = fillna
        self.label_periods = label_periods
        self.feature_names = get_feature_names(include_perpetual)
        self._fit_params = {}  # 存储标准化参数

        logger.info(
            f"CryptoAlpha158 初始化: "
            f"{len(self.feature_names)} 个因子, "
            f"永续因子={'开启' if include_perpetual else '关闭'}, "
            f"标签=未来{label_periods}期收益率"
        )

    def calculate_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        计算所有 Alpha 因子

        Args:
            df: 原始 OHLCV 数据，必须包含列: $open, $high, $low, $close, $volume
                如果包含永续合约数据，还需要: $funding_rate, $open_interest, $premium

        Returns:
            包含所有计算因子的 DataFrame
        """
        result = pd.DataFrame(index=df.index)

        # 提取原始列（去掉 $ 前缀用于计算）
        close = df["$close"]
        open_ = df["$open"]
        high = df["$high"]
        low = df["$low"]
        volume = df["$volume"]

        # --- K线形态因子 ---
        result["KMID"] = (close - open_) / open_
        hl_range = high - low
        result["KLEN"] = hl_range / open_
        result["KSFT"] = (close - open_) / (hl_range + 1e-12)
        result["KUP"] = (high - np.maximum(open_, close)) / open_
        result["KLOW"] = (np.minimum(open_, close) - low) / open_
        result["KSHUP"] = (high - np.maximum(open_, close)) / (hl_range + 1e-12)
        result["KSHDN"] = (np.minimum(open_, close) - low) / (hl_range + 1e-12)

        # --- 价格动量因子（ROC）---
        for window in [1, 2, 3, 5, 10, 20, 30, 60]:
            if len(close) > window:
                result[f"ROC_{window}"] = close.shift(window) / close - 1
            else:
                result[f"ROC_{window}"] = np.nan

        # --- 均线偏离因子 ---
        for window in [5, 10, 20, 30, 60]:
            if len(close) > window:
                result[f"MA_偏离_{window}"] = close.rolling(window).mean() / close - 1
            else:
                result[f"MA_偏离_{window}"] = np.nan

        # --- 波动率因子（变异系数）---
        for window in [5, 10, 20, 30, 60]:
            if len(close) > window:
                ma = close.rolling(window).mean()
                std = close.rolling(window).std()
                result[f"CV_{window}"] = std / ma
            else:
                result[f"CV_{window}"] = np.nan

        # --- 滚动统计因子 ---
        returns = close.pct_change()
        log_volume = np.log(volume + 1)

        for window in [5, 10, 20, 30, 60]:
            if len(close) > window:
                # 收益率波动率
                result[f"VSTD_{window}"] = returns.rolling(window).std()
                # 成交量波动率
                vol_mean = volume.rolling(window).mean()
                vol_std = volume.rolling(window).std()
                result[f"VWSTD_{window}"] = vol_std / (vol_mean + 1e-12)
                # 价格在区间内的位置
                rolling_high = high.rolling(window).max()
                rolling_low = low.rolling(window).min()
                result[f"POSITION_{window}"] = (close - rolling_low) / (
                    rolling_high - rolling_low + 1e-12
                )
            else:
                result[f"VSTD_{window}"] = np.nan
                result[f"VWSTD_{window}"] = np.nan
                result[f"POSITION_{window}"] = np.nan

        # --- 价量相关性因子 ---
        for window in [5, 10, 20, 60]:
            if len(close) > window:
                result[f"价量相关性_{window}"] = close.rolling(window).corr(log_volume)
            else:
                result[f"价量相关性_{window}"] = np.nan

        # --- 永续合约特有因子 ---
        if self.include_perpetual:
            self._calculate_perpetual_features(df, result)

        return result

    def _calculate_perpetual_features(self, df: pd.DataFrame, result: pd.DataFrame) -> None:
        """
        计算永续合约特有因子

        Args:
            df: 原始数据
            result: 存放计算结果的 DataFrame（就地修改）
        """
        # 资金费率因子
        if "$funding_rate" in df.columns:
            fr = df["$funding_rate"]
            result["资金费率"] = fr
            result["资金费率_8期均值"] = fr.rolling(8, min_periods=1).mean()
            result["资金费率_24期均值"] = fr.rolling(24, min_periods=1).mean()
            result["资金费率_24期标准差"] = fr.rolling(24, min_periods=1).std()
            result["资金费率_1期变化"] = fr.diff(1)
            result["资金费率_偏离均值"] = fr - fr.rolling(24, min_periods=1).mean()
        else:
            # 如果没有资金费率数据，填充为 0
            for col_name in [
                "资金费率",
                "资金费率_8期均值",
                "资金费率_24期均值",
                "资金费率_24期标准差",
                "资金费率_1期变化",
                "资金费率_偏离均值",
            ]:
                result[col_name] = 0.0

        # 未平仓量因子
        if "$open_interest" in df.columns:
            oi = df["$open_interest"]
            result["未平仓量_对数"] = np.log(oi + 1)
            result["未平仓量_1期变化率"] = oi.pct_change(1)
            result["未平仓量_24期变化率"] = oi.pct_change(24)
            vol = df["$volume"]
            result["未平仓量_成交量比"] = oi / (vol + 1)
            result["未平仓量_24期均值"] = oi.rolling(24, min_periods=1).mean()
            result["未平仓量_偏离均值"] = oi / oi.rolling(24, min_periods=1).mean() - 1
        else:
            for col_name in [
                "未平仓量_对数",
                "未平仓量_1期变化率",
                "未平仓量_24期变化率",
                "未平仓量_成交量比",
                "未平仓量_24期均值",
                "未平仓量_偏离均值",
            ]:
                result[col_name] = 0.0

        # 溢价率因子
        if "$premium" in df.columns:
            prem = df["$premium"]
            result["溢价率"] = prem
            result["溢价率_24期均值"] = prem.rolling(24, min_periods=1).mean()
            result["溢价率_24期标准差"] = prem.rolling(24, min_periods=1).std()
        else:
            for col_name in ["溢价率", "溢价率_24期均值", "溢价率_24期标准差"]:
                result[col_name] = 0.0

        # 增强成交量因子
        vol = df["$volume"]
        result["成交量_偏离24期均值"] = vol / vol.rolling(24, min_periods=1).mean() - 1
        result["成交量_偏离168期均值"] = vol / vol.rolling(168, min_periods=1).mean() - 1

        # 价量相关性（24期）
        result["价量相关性_24期"] = df["$close"].rolling(24, min_periods=5).corr(np.log(vol + 1))

    def calculate_label(self, df: pd.DataFrame) -> pd.Series:
        """
        计算标签（未来 N 期收益率）

        Args:
            df: 包含 $close 列的 DataFrame

        Returns:
            标签 Series
        """
        close = df["$close"]
        # 未来 N 期收益率
        label = close.shift(-self.label_periods) / close - 1
        label.name = f"label_ret_{self.label_periods}"
        return label

    def fit(self, features: pd.DataFrame) -> "CryptoAlpha158":
        """
        拟合标准化参数（在训练集上计算均值和标准差）

        Args:
            features: 训练集特征 DataFrame

        Returns:
            self
        """
        if self.normalize:
            self._fit_params["mean"] = features.mean()
            self._fit_params["std"] = features.std()
            # 避免除以 0
            self._fit_params["std"] = self._fit_params["std"].replace(0, 1)
            logger.info(f"标准化参数拟合完成: {len(features.columns)} 个特征")
        return self

    def transform(self, features: pd.DataFrame) -> pd.DataFrame:
        """
        应用数据处理（标准化 + 缺失值填充）

        Args:
            features: 特征 DataFrame

        Returns:
            处理后的 DataFrame
        """
        result = features.copy()

        # 标准化（使用 fit 时计算的参数）
        if self.normalize and "mean" in self._fit_params:
            mean = self._fit_params["mean"]
            std = self._fit_params["std"]
            # 只对训练时见过的列进行标准化
            common_cols = result.columns.intersection(mean.index)
            result[common_cols] = (result[common_cols] - mean[common_cols]) / std[common_cols]
            # 裁剪异常值（鲁棒 Z-Score）
            result = result.clip(-3, 3)

        # 填充缺失值
        if self.fillna:
            result = result.fillna(0)

        return result

    def fit_transform(self, features: pd.DataFrame) -> pd.DataFrame:
        """
        拟合并转换

        Args:
            features: 训练集特征

        Returns:
            处理后的 DataFrame
        """
        return self.fit(features).transform(features)

    def process_dataset(
        self,
        raw_data: pd.DataFrame,
    ) -> dict:
        """
        完整的数据处理管线：原始数据 → 因子计算 → 标准化 → 标签生成

        对于 MultiIndex DataFrame（多个交易对），逐个处理后合并。

        Args:
            raw_data: 原始 MultiIndex DataFrame (datetime × instrument)

        Returns:
            {
                "features": 处理后的特征 DataFrame,
                "label": 标签 Series,
                "feature_names": 特征名称列表,
            }
        """
        is_multi = isinstance(raw_data.index, pd.MultiIndex)

        if is_multi:
            # 多交易对：逐个计算因子
            all_features = []
            all_labels = []
            instruments = raw_data.index.get_level_values("instrument").unique()

            for instrument in instruments:
                inst_data = raw_data.xs(instrument, level="instrument")

                # 计算因子
                features = self.calculate_features(inst_data)
                features["instrument"] = instrument

                # 计算标签
                label = self.calculate_label(inst_data)
                label = label.to_frame()
                label["instrument"] = instrument

                all_features.append(features.reset_index())
                all_labels.append(label.reset_index())

            # 合并
            features_df = pd.concat(all_features, ignore_index=True)
            features_df = features_df.set_index(["datetime", "instrument"])

            labels_df = pd.concat(all_labels, ignore_index=True)
            labels_df = labels_df.set_index(["datetime", "instrument"])
            label_series = labels_df.iloc[:, 0]
        else:
            # 单交易对
            features_df = self.calculate_features(raw_data)
            label_series = self.calculate_label(raw_data)

        feature_names = [col for col in features_df.columns if col != "instrument"]

        logger.info(f"数据处理完成: {features_df.shape[0]} 行, {len(feature_names)} 个特征")

        return {
            "features": features_df,
            "label": label_series,
            "feature_names": feature_names,
        }
