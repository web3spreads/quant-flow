"""
加密货币专用 DataHandler（v2 重构版）

基于 QLib Alpha158 的设计理念，为加密货币永续合约定制的数据处理器。
v2 改进点：
- 精简特征集：去除无效的永续合约零值特征，缩短窗口周期
- 新增技术指标：RSI、MACD、布林带、ATR、OBV 变化率
- 特征选择机制：基于训练集 IC 值自动筛选 top-K 特征
- 标签优化：支持多标签周期、Winsorize 处理
"""

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger("QuantFlow.QLib")

# ============================================================
# 特征窗口配置（精简版，去掉 30/60 期长窗口）
# ============================================================
SHORT_WINDOWS = [5, 10, 20]
ROC_WINDOWS = [1, 2, 3, 5, 10, 20]
CORR_WINDOWS = [5, 10, 20]


class CryptoAlpha158:
    """
    加密货币永续合约专用 DataHandler（v2 重构版）

    核心改进：
    - 精简 Alpha158 因子（去掉长窗口，减少噪声）
    - 新增 RSI/MACD/BB/ATR 等经典技术指标
    - 内置特征选择：训练前按 IC 排序筛选 top-K
    - 标签 Winsorize：抑制极端值影响
    - 永续合约因子仅在数据真正可用时才生成
    """

    def __init__(
        self,
        include_perpetual: bool = True,
        normalize: bool = True,
        fillna: bool = True,
        label_periods: int = 3,
        feature_select_top_k: int = 0,
        label_winsorize_quantile: float = 0.01,
    ):
        """
        初始化 DataHandler

        Args:
            include_perpetual: 是否包含永续合约特有因子（仅在数据有效时才生成）
            normalize: 是否对特征进行标准化
            fillna: 是否填充缺失值
            label_periods: 标签的未来期数（预测未来 N 期收益率）
            feature_select_top_k: 特征选择保留数量（0=不做选择）
            label_winsorize_quantile: 标签 Winsorize 分位数（0=不处理）
        """
        self.include_perpetual = include_perpetual
        self.normalize = normalize
        self.fillna = fillna
        self.label_periods = label_periods
        self.feature_select_top_k = feature_select_top_k
        self.label_winsorize_quantile = label_winsorize_quantile
        self._fit_params = {}  # 存储标准化参数
        self._selected_features: list[str] | None = None  # 选定的特征列表
        # feature_names 延迟设置（在第一次计算时确定）
        self.feature_names: list[str] = []

        logger.info(
            f"CryptoAlpha158 v2 初始化: "
            f"永续因子={'开启' if include_perpetual else '关闭'}, "
            f"标签=未来{label_periods}期收益率, "
            f"特征选择top_k={feature_select_top_k}, "
            f"标签Winsorize={label_winsorize_quantile}"
        )

    def calculate_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        计算所有 Alpha 因子（v2 精简版 + 新增技术指标）

        Args:
            df: 原始 OHLCV 数据，必须包含列: $open, $high, $low, $close, $volume

        Returns:
            包含所有计算因子的 DataFrame
        """
        result = pd.DataFrame(index=df.index)

        close = df["$close"]
        open_ = df["$open"]
        high = df["$high"]
        low = df["$low"]
        volume = df["$volume"]

        # === 1. K线形态因子（7个）===
        hl_range = high - low
        result["KMID"] = (close - open_) / open_
        result["KLEN"] = hl_range / open_
        result["KSFT"] = (close - open_) / (hl_range + 1e-12)
        result["KUP"] = (high - np.maximum(open_, close)) / open_
        result["KLOW"] = (np.minimum(open_, close) - low) / open_
        result["KSHUP"] = (high - np.maximum(open_, close)) / (hl_range + 1e-12)
        result["KSHDN"] = (np.minimum(open_, close) - low) / (hl_range + 1e-12)

        # === 2. 价格动量因子 ROC（6个，去掉30/60）===
        for window in ROC_WINDOWS:
            if len(close) > window:
                result[f"ROC_{window}"] = close.shift(window) / close - 1
            else:
                result[f"ROC_{window}"] = np.nan

        # === 3. 均线偏离因子（3个，去掉30/60）===
        for window in SHORT_WINDOWS:
            if len(close) > window:
                result[f"MA_BIAS_{window}"] = close.rolling(window).mean() / close - 1
            else:
                result[f"MA_BIAS_{window}"] = np.nan

        # === 4. 波动率因子 CV（3个）===
        for window in SHORT_WINDOWS:
            if len(close) > window:
                ma = close.rolling(window).mean()
                std = close.rolling(window).std()
                result[f"CV_{window}"] = std / (ma + 1e-12)
            else:
                result[f"CV_{window}"] = np.nan

        # === 5. 滚动统计因子（9个）===
        returns = close.pct_change()

        for window in SHORT_WINDOWS:
            if len(close) > window:
                result[f"VSTD_{window}"] = returns.rolling(window).std()
                vol_mean = volume.rolling(window).mean()
                vol_std = volume.rolling(window).std()
                result[f"VWSTD_{window}"] = vol_std / (vol_mean + 1e-12)
                rolling_high = high.rolling(window).max()
                rolling_low = low.rolling(window).min()
                result[f"POSITION_{window}"] = (close - rolling_low) / (
                    rolling_high - rolling_low + 1e-12
                )
            else:
                result[f"VSTD_{window}"] = np.nan
                result[f"VWSTD_{window}"] = np.nan
                result[f"POSITION_{window}"] = np.nan

        # === 6. 价量相关性因子（3个）===
        log_volume = np.log(volume + 1)
        for window in CORR_WINDOWS:
            if len(close) > window:
                result[f"CORR_PV_{window}"] = close.rolling(window).corr(log_volume)
            else:
                result[f"CORR_PV_{window}"] = np.nan

        # === 7. 新增技术指标 ===

        # RSI (14期)
        result["RSI_14"] = self._calculate_rsi(close, 14)

        # MACD (12, 26, 9) → 3个特征
        macd_line, signal_line, histogram = self._calculate_macd(close)
        result["MACD_LINE"] = macd_line / (close + 1e-12)  # 归一化
        result["MACD_SIGNAL"] = signal_line / (close + 1e-12)
        result["MACD_HIST"] = histogram / (close + 1e-12)

        # 布林带 (20, 2) → 2个特征
        bb_pctb, bb_width = self._calculate_bollinger(close, 20, 2)
        result["BB_PCTB"] = bb_pctb
        result["BB_WIDTH"] = bb_width

        # ATR (14期，归一化)
        result["ATR_14"] = self._calculate_atr(high, low, close, 14) / (close + 1e-12)

        # OBV 变化率
        obv = self._calculate_obv(close, volume)
        if len(obv) > 10:
            obv_ma = obv.rolling(10).mean()
            result["OBV_ROC_10"] = (obv - obv_ma) / (obv_ma.abs() + 1e-12)
        else:
            result["OBV_ROC_10"] = np.nan

        # 成交量偏离（保留2个有效的）
        if len(volume) > 24:
            result["VOL_BIAS_24"] = volume / volume.rolling(24, min_periods=1).mean() - 1
        else:
            result["VOL_BIAS_24"] = np.nan

        # === 8. 永续合约因子（仅在数据真正有效时生成）===
        if self.include_perpetual:
            self._calculate_perpetual_features(df, result)

        # 更新特征名列表
        self.feature_names = list(result.columns)

        return result

    # ============================================================
    # 技术指标计算方法
    # ============================================================

    @staticmethod
    def _calculate_rsi(close: pd.Series, period: int = 14) -> pd.Series:
        """计算 RSI 指标"""
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)

        avg_gain = gain.rolling(window=period, min_periods=period).mean()
        avg_loss = loss.rolling(window=period, min_periods=period).mean()

        rs = avg_gain / (avg_loss + 1e-12)
        rsi = 100 - (100 / (1 + rs))
        # 归一化到 [0, 1]
        return rsi / 100.0

    @staticmethod
    def _calculate_macd(
        close: pd.Series,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
    ) -> tuple[pd.Series, pd.Series, pd.Series]:
        """计算 MACD 指标"""
        ema_fast = close.ewm(span=fast, adjust=False).mean()
        ema_slow = close.ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram

    @staticmethod
    def _calculate_bollinger(
        close: pd.Series, period: int = 20, std_dev: float = 2.0
    ) -> tuple[pd.Series, pd.Series]:
        """计算布林带 %B 和带宽"""
        middle = close.rolling(period).mean()
        std = close.rolling(period).std()
        upper = middle + std_dev * std
        lower = middle - std_dev * std
        # %B: 价格在布林带中的位置 [0, 1]
        pctb = (close - lower) / (upper - lower + 1e-12)
        # 带宽: 布林带宽度归一化
        width = (upper - lower) / (middle + 1e-12)
        return pctb, width

    @staticmethod
    def _calculate_atr(
        high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
    ) -> pd.Series:
        """计算 ATR"""
        prev_close = close.shift(1)
        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return true_range.rolling(period).mean()

    @staticmethod
    def _calculate_obv(close: pd.Series, volume: pd.Series) -> pd.Series:
        """计算 OBV（能量潮）"""
        direction = np.sign(close.diff())
        obv = (volume * direction).cumsum()
        return obv

    def _calculate_perpetual_features(self, df: pd.DataFrame, result: pd.DataFrame) -> None:
        """
        计算永续合约特有因子

        v2 改进：仅在数据真正可用时生成，避免生成全零噪声特征。
        检测有效数据比例 > 10% 才生成对应因子。
        """
        min_valid_ratio = 0.1  # 至少 10% 的数据有效才生成特征

        # 资金费率因子
        if "$funding_rate" in df.columns:
            fr = df["$funding_rate"]
            valid_ratio = fr.notna().mean()
            if valid_ratio >= min_valid_ratio and (fr != 0).any():
                result["FR"] = fr
                result["FR_MA8"] = fr.rolling(8, min_periods=1).mean()
                result["FR_MA24"] = fr.rolling(24, min_periods=1).mean()
                result["FR_STD24"] = fr.rolling(24, min_periods=1).std()
                result["FR_DIFF1"] = fr.diff(1)
                result["FR_DEVMA"] = fr - fr.rolling(24, min_periods=1).mean()
                logger.debug(f"资金费率因子已生成（有效比例={valid_ratio:.1%}）")
            else:
                logger.debug(f"资金费率数据无效（有效比例={valid_ratio:.1%}），跳过")

        # 未平仓量因子
        if "$open_interest" in df.columns:
            oi = df["$open_interest"]
            valid_ratio = oi.notna().mean()
            if valid_ratio >= min_valid_ratio and (oi != 0).any():
                result["OI_LOG"] = np.log(oi + 1)
                result["OI_ROC1"] = oi.pct_change(1)
                result["OI_ROC24"] = oi.pct_change(24)
                vol = df["$volume"]
                result["OI_VOL_RATIO"] = oi / (vol + 1)
                result["OI_DEVMA"] = oi / oi.rolling(24, min_periods=1).mean() - 1
                logger.debug(f"未平仓量因子已生成（有效比例={valid_ratio:.1%}）")
            else:
                logger.debug(f"未平仓量数据无效（有效比例={valid_ratio:.1%}），跳过")

        # 溢价率因子
        if "$premium" in df.columns:
            prem = df["$premium"]
            valid_ratio = prem.notna().mean()
            if valid_ratio >= min_valid_ratio and (prem != 0).any():
                result["PREM"] = prem
                result["PREM_MA24"] = prem.rolling(24, min_periods=1).mean()
                result["PREM_STD24"] = prem.rolling(24, min_periods=1).std()
                logger.debug(f"溢价率因子已生成（有效比例={valid_ratio:.1%}）")
            else:
                logger.debug(f"溢价率数据无效（有效比例={valid_ratio:.1%}），跳过")

    # ============================================================
    # 标签计算
    # ============================================================

    def calculate_label(self, df: pd.DataFrame) -> pd.Series:
        """
        计算标签（未来 N 期收益率），带 Winsorize 处理

        Args:
            df: 包含 $close 列的 DataFrame

        Returns:
            标签 Series
        """
        close = df["$close"]
        label = close.shift(-self.label_periods) / close - 1
        label.name = f"label_ret_{self.label_periods}"

        # Winsorize 处理：抑制极端值
        if self.label_winsorize_quantile > 0:
            label = self._winsorize(label, self.label_winsorize_quantile)

        return label

    @staticmethod
    def _winsorize(series: pd.Series, quantile: float) -> pd.Series:
        """Winsorize 处理：将超出分位数的值裁剪到分位数边界"""
        lower = series.quantile(quantile)
        upper = series.quantile(1 - quantile)
        return series.clip(lower, upper)

    # ============================================================
    # 特征选择
    # ============================================================

    def select_features_by_ic(
        self,
        features: pd.DataFrame,
        label: pd.Series,
        top_k: int = 0,
    ) -> list[str]:
        """
        基于训练集 IC 值选择 top-K 特征

        Args:
            features: 特征 DataFrame
            label: 标签 Series
            top_k: 保留的特征数量（0=不做选择，返回全部）

        Returns:
            选中的特征名列表
        """
        if top_k <= 0:
            self._selected_features = list(features.columns)
            return self._selected_features

        # 对齐索引
        common_idx = features.index.intersection(label.index)
        feat_aligned = features.loc[common_idx]
        label_aligned = label.loc[common_idx].dropna()
        common_idx = feat_aligned.index.intersection(label_aligned.index)
        feat_aligned = feat_aligned.loc[common_idx]
        label_aligned = label_aligned.loc[common_idx]

        # 计算每个特征与标签的 IC（绝对值）
        ic_scores = {}
        for col in feat_aligned.columns:
            col_data = feat_aligned[col].dropna()
            common = col_data.index.intersection(label_aligned.index)
            if len(common) < 30:
                ic_scores[col] = 0.0
                continue
            ic = col_data.loc[common].corr(label_aligned.loc[common])
            ic_scores[col] = abs(ic) if not np.isnan(ic) else 0.0

        # 按 IC 绝对值降序排列，取 top_k
        sorted_features = sorted(ic_scores.items(), key=lambda x: x[1], reverse=True)
        selected = [name for name, _ in sorted_features[:top_k]]

        # 记录选择结果
        logger.info(
            f"特征选择: 从 {len(features.columns)} 个中选出 {len(selected)} 个\n"
            f"  Top5: {', '.join(f'{n}(IC={ic_scores[n]:.4f})' for n in selected[:5])}"
        )

        self._selected_features = selected
        return selected

    def apply_feature_selection(self, features: pd.DataFrame) -> pd.DataFrame:
        """
        应用已选择的特征列表

        Args:
            features: 完整特征 DataFrame

        Returns:
            筛选后的 DataFrame
        """
        if self._selected_features is None:
            return features

        available = [col for col in self._selected_features if col in features.columns]
        return features[available]

    # ============================================================
    # 标准化和数据处理
    # ============================================================

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

        if self.normalize and "mean" in self._fit_params:
            mean = self._fit_params["mean"]
            std = self._fit_params["std"]
            common_cols = result.columns.intersection(mean.index)
            result[common_cols] = (result[common_cols] - mean[common_cols]) / std[common_cols]
            result = result.clip(-3, 3)

        if self.fillna:
            result = result.fillna(0)

        return result

    def fit_transform(self, features: pd.DataFrame) -> pd.DataFrame:
        """拟合并转换"""
        return self.fit(features).transform(features)

    def process_dataset(
        self,
        raw_data: pd.DataFrame,
    ) -> dict:
        """
        完整的数据处理管线：原始数据 → 因子计算 → 标签生成

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
            all_features = []
            all_labels = []
            instruments = raw_data.index.get_level_values("instrument").unique()

            for instrument in instruments:
                inst_data = raw_data.xs(instrument, level="instrument")

                features = self.calculate_features(inst_data)
                features["instrument"] = instrument

                label = self.calculate_label(inst_data)
                label = label.to_frame()
                label["instrument"] = instrument

                all_features.append(features.reset_index())
                all_labels.append(label.reset_index())

            features_df = pd.concat(all_features, ignore_index=True)
            features_df = features_df.set_index(["datetime", "instrument"])

            labels_df = pd.concat(all_labels, ignore_index=True)
            labels_df = labels_df.set_index(["datetime", "instrument"])
            label_series = labels_df.iloc[:, 0]
        else:
            features_df = self.calculate_features(raw_data)
            label_series = self.calculate_label(raw_data)

        feature_names = [col for col in features_df.columns if col != "instrument"]

        logger.info(f"数据处理完成: {features_df.shape[0]} 行, {len(feature_names)} 个特征")

        return {
            "features": features_df,
            "label": label_series,
            "feature_names": feature_names,
        }
