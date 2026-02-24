"""
Hyperliquid 数据收集器

从 Hyperliquid DEX 收集永续合约数据，转换为 QLib 可用的 DataFrame 格式。
复用现有的 MarketDataFetcher 进行底层数据获取。
支持本地 parquet 文件持久化，实现数据累加存储和增量拉取。
"""

import logging
from pathlib import Path

import pandas as pd

from src.data.market_data import MarketDataFetcher

from .perpetual import PERPETUAL_RAW_COLUMNS

logger = logging.getLogger("QuantFlow.QLib")


class HyperliquidDataCollector:
    """
    Hyperliquid 数据收集器

    职责：
    1. 从 Hyperliquid API 收集 OHLCV + 永续合约特有数据
    2. 转换为 QLib 标准的 MultiIndex DataFrame 格式
    3. 处理数据质量问题（缺失值、异常值等）
    4. 本地持久化历史数据，支持增量拉取和数据累加
    """

    def __init__(
        self,
        testnet: bool = False,
        data_dir: str = "data/qlib",
        persist_data: bool = True,
    ):
        """
        初始化数据收集器

        Args:
            testnet: 是否使用测试网
            data_dir: 本地数据存储目录
            persist_data: 是否启用数据持久化
        """
        self.fetcher = MarketDataFetcher(testnet=testnet)
        self.testnet = testnet
        self.persist_data = persist_data
        self.data_dir = Path(data_dir)

        if self.persist_data:
            self.data_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            f"数据收集器初始化完成 ({'测试网' if testnet else '主网'}"
            f"{', 数据持久化=' + str(self.data_dir) if self.persist_data else ''})"
        )

    def _get_data_path(self, symbol: str, freq: str) -> Path:
        """
        返回本地数据文件路径

        Args:
            symbol: 交易对
            freq: 数据频率

        Returns:
            本地 parquet 文件路径
        """
        return self.data_dir / f"{symbol}_{freq}.parquet"

    def _load_local_data(self, symbol: str, freq: str) -> pd.DataFrame:
        """
        从本地加载已有数据

        Args:
            symbol: 交易对
            freq: 数据频率

        Returns:
            本地数据 DataFrame，不存在则返回空 DataFrame
        """
        path = self._get_data_path(symbol, freq)
        if not path.exists():
            return pd.DataFrame()

        try:
            df = pd.read_parquet(path)
            logger.info(f"加载本地数据: {symbol}_{freq}, {len(df)} 行")
            return df
        except Exception:
            logger.warning(f"加载本地数据失败 ({path})", exc_info=True)
            return pd.DataFrame()

    def _save_local_data(self, symbol: str, freq: str, df: pd.DataFrame) -> None:
        """
        保存数据到本地（去重 + 按时间排序）

        Args:
            symbol: 交易对
            freq: 数据频率
            df: 要保存的 DataFrame（需包含 timestamp 列）
        """
        if df.empty:
            return

        path = self._get_data_path(symbol, freq)
        try:
            # 按 timestamp 去重并排序
            df = df.drop_duplicates(subset=["timestamp"], keep="last")
            df = df.sort_values("timestamp").reset_index(drop=True)
            df.to_parquet(path, index=False)
            logger.info(f"保存本地数据: {symbol}_{freq}, {len(df)} 行 → {path}")
        except Exception:
            logger.warning(f"保存本地数据失败 ({path})", exc_info=True)

    def collect_ohlcv(
        self,
        symbols: list[str],
        freq: str = "1h",
        limit: int = 500,
    ) -> pd.DataFrame:
        """
        收集多个交易对的 OHLCV 数据

        启用数据持久化后，会先加载本地已有数据，仅从 API 增量拉取新数据，
        合并后保存到本地。返回最近 limit 条数据（保持接口兼容）。

        Args:
            symbols: 交易对列表（如 ['BTC', 'ETH', 'SOL']）
            freq: 时间频率（1m/5m/15m/1h/4h/1d）
            limit: 每个交易对获取的 K 线数量

        Returns:
            MultiIndex DataFrame，索引为 (datetime, instrument)
            列包含: $open, $high, $low, $close, $volume
        """
        all_data = []

        for symbol in symbols:
            # 尝试加载本地数据并增量拉取
            if self.persist_data:
                local_df = self._load_local_data(symbol, freq)
            else:
                local_df = pd.DataFrame()

            # 从 API 拉取数据
            df = self.fetcher.fetch_ohlcv(symbol, timeframe=freq, limit=limit)
            if df is None or df.empty:
                if local_df.empty:
                    logger.warning(f"跳过 {symbol}: 无法获取数据且无本地数据")
                    continue
                else:
                    # API 失败但有本地数据，使用本地数据
                    logger.warning(f"{symbol}: API 获取失败，使用本地缓存数据")
                    df = local_df
            else:
                # 合并本地 + 新数据
                if not local_df.empty and self.persist_data:
                    merged = pd.concat([local_df, df], ignore_index=True)
                    # 按 timestamp 去重，保留最新的
                    merged = merged.drop_duplicates(subset=["timestamp"], keep="last")
                    merged = merged.sort_values("timestamp").reset_index(drop=True)

                    incremental_count = len(merged) - len(local_df)
                    logger.info(
                        f"{symbol}: 增量拉取 {incremental_count} 条新数据, "
                        f"合并后总计 {len(merged)} 条"
                    )

                    # 保存合并后的全量数据
                    self._save_local_data(symbol, freq, merged)

                    # 返回最近 limit 条（保持接口兼容）
                    df = merged.tail(limit).reset_index(drop=True)
                elif self.persist_data:
                    # 首次拉取，直接保存
                    self._save_local_data(symbol, freq, df)

            # 添加 instrument 列
            df["instrument"] = symbol

            # 重命名为 QLib 标准格式（$ 前缀）
            df = df.rename(
                columns={
                    "open": "$open",
                    "high": "$high",
                    "low": "$low",
                    "close": "$close",
                    "volume": "$volume",
                }
            )

            all_data.append(df)

        if not all_data:
            logger.error("所有交易对数据收集失败")
            return pd.DataFrame()

        # 合并所有交易对数据
        combined = pd.concat(all_data, ignore_index=True)

        # 设置 MultiIndex
        combined = combined.set_index(["timestamp", "instrument"])
        combined.index.names = ["datetime", "instrument"]
        combined = combined.sort_index()

        logger.info(f"收集完成: {len(symbols)} 个交易对, {len(combined)} 行数据")
        return combined

    def collect_perpetual_features(
        self,
        symbols: list[str],
    ) -> pd.DataFrame | None:
        """
        收集永续合约特有特征

        注意：Hyperliquid API 提供的实时数据有限，
        部分历史数据（如历史资金费率）需要通过其他方式获取。
        当前实现收集最新的快照数据。

        Args:
            symbols: 交易对列表

        Returns:
            包含永续合约特征的 DataFrame，索引为 instrument
        """
        features = []

        for symbol in symbols:
            feature_data = {"instrument": symbol}

            # 获取资金费率
            funding_rate = self.fetcher.get_funding_rate(symbol)
            feature_data["funding_rate"] = funding_rate if funding_rate is not None else 0.0

            # 获取 Ticker 信息
            ticker = self.fetcher.get_ticker(symbol)
            if ticker:
                feature_data["premium"] = 0.0  # 溢价率需要指数价格对比

            # 未平仓量暂设为 0（需要扩展 API）
            feature_data["open_interest"] = 0.0

            features.append(feature_data)

        if not features:
            return None

        df = pd.DataFrame(features)
        df = df.set_index("instrument")

        logger.info(f"收集永续合约特征: {len(df)} 个交易对")
        return df

    def collect_full_dataset(
        self,
        symbols: list[str],
        freq: str = "1h",
        limit: int = 500,
    ) -> pd.DataFrame:
        """
        收集完整数据集（OHLCV + 永续合约特征）

        Args:
            symbols: 交易对列表
            freq: 时间频率
            limit: K 线数量

        Returns:
            包含所有特征的 MultiIndex DataFrame
        """
        # 收集 OHLCV 基础数据
        ohlcv_df = self.collect_ohlcv(symbols, freq=freq, limit=limit)
        if ohlcv_df.empty:
            return ohlcv_df

        # 收集永续合约特征（当前时刻快照）
        # 快照数据只能代表当前时刻，不可广播到历史时间点（否则会导致数据泄漏）
        # 因此只将快照赋值给每个交易对的最新一条记录，历史记录填 NaN
        perp_features = self.collect_perpetual_features(symbols)
        if perp_features is not None:
            for col in PERPETUAL_RAW_COLUMNS:
                if col in perp_features.columns:
                    ohlcv_df[f"${col}"] = float("nan")  # 历史时间点无数据，填 NaN
                    for symbol in symbols:
                        if symbol in perp_features.index:
                            sym_mask = ohlcv_df.index.get_level_values("instrument") == symbol
                            sym_rows = ohlcv_df.loc[sym_mask]
                            if not sym_rows.empty:
                                # 只赋值给最新一条记录
                                latest_idx = sym_rows.index[-1]
                                ohlcv_df.loc[latest_idx, f"${col}"] = perp_features.loc[symbol, col]

        logger.info(f"完整数据集收集完成: {ohlcv_df.shape}")
        return ohlcv_df

    def prepare_qlib_dataset(
        self,
        symbols: list[str],
        freq: str = "1h",
        limit: int = 500,
        label_rule: str = "5",
        train_ratio: float = 0.7,
        valid_ratio: float = 0.15,
    ) -> dict:
        """
        准备 QLib 格式的完整数据集，包含训练/验证/测试分割

        Args:
            symbols: 交易对列表
            freq: 时间频率
            limit: K 线数量
            label_rule: 标签规则，N 表示未来 N 期收益率
            train_ratio: 训练集比例
            valid_ratio: 验证集比例

        Returns:
            {
                "data": MultiIndex DataFrame,
                "features": 特征列列表,
                "label_col": 标签列名,
                "segments": {"train": (start, end), "valid": ..., "test": ...},
            }
        """
        # 收集数据
        df = self.collect_full_dataset(symbols, freq=freq, limit=limit)
        if df.empty:
            return {"data": df, "features": [], "label_col": None, "segments": {}}

        # 计算标签：未来 N 期收益率
        label_periods = int(label_rule)
        label_col = f"label_ret_{label_periods}"

        # 对每个交易对分别计算标签
        labels = []
        for symbol in symbols:
            if symbol not in df.index.get_level_values("instrument"):
                continue
            symbol_data = df.xs(symbol, level="instrument")
            # 未来 N 期收益率 = close[t+N] / close[t] - 1
            future_return = symbol_data["$close"].shift(-label_periods) / symbol_data["$close"] - 1
            future_return.name = label_col
            future_return = future_return.to_frame()
            future_return["instrument"] = symbol
            future_return = future_return.reset_index()
            labels.append(future_return)

        if labels:
            label_df = pd.concat(labels, ignore_index=True)
            label_df = label_df.set_index(["datetime", "instrument"])
            df = df.join(label_df, how="left")

        # 获取特征列（以 $ 开头的列）
        feature_cols = [col for col in df.columns if col.startswith("$")]

        # 获取时间范围并分割
        timestamps = df.index.get_level_values("datetime").unique().sort_values()
        n_total = len(timestamps)
        n_train = int(n_total * train_ratio)
        n_valid = int(n_total * valid_ratio)

        segments = {
            "train": (str(timestamps[0]), str(timestamps[n_train - 1])),
            "valid": (str(timestamps[n_train]), str(timestamps[n_train + n_valid - 1])),
            "test": (str(timestamps[n_train + n_valid]), str(timestamps[-1])),
        }

        logger.info(
            f"数据集准备完成: "
            f"特征数={len(feature_cols)}, 标签={label_col}, "
            f"训练={n_train}, 验证={n_valid}, 测试={n_total - n_train - n_valid}"
        )

        return {
            "data": df,
            "features": feature_cols,
            "label_col": label_col,
            "segments": segments,
        }
