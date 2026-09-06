"""
1 秒网格上的微观结构特征与前向中间价变动。

对每个标的、每段日期：
- 从 l2book 取每秒最后一个快照（asof 向后对齐），算 mid、L1/L5 簿失衡、微价格偏离（bp）；
- 从 trades 按秒聚合带符号名义额，再滚动求过去 1/5/30 秒的成交流失衡 tfi_h = Σ signed / Σ |notional|；
- 前向 Δmid（bp）= (mid(t+h) − mid(t)) / mid(t) × 1e4，h ∈ {1,5,30}，同样 asof 向后对齐。

所有列都是「t 时刻只用 ≤ t 的信息」算出来的，前向变动只用 > t 的信息；没有任何插补。
"""
from __future__ import annotations

from pathlib import Path

import polars as pl

from . import dataset

HORIZONS = (1, 5, 30)
FEATURES = ("imb_l1", "imb_l5", "mp_dev_bp", "tfi_1", "tfi_5", "tfi_30")


def _book_features(lf: pl.LazyFrame) -> pl.LazyFrame:
    bid5 = sum(pl.col(f"bid_sz_{i}") for i in range(1, 6))
    ask5 = sum(pl.col(f"ask_sz_{i}") for i in range(1, 6))
    b1, a1, bs, as_ = pl.col("bid_px_1"), pl.col("ask_px_1"), pl.col("bid_sz_1"), pl.col("ask_sz_1")
    mid = (b1 + a1) / 2
    micro = (b1 * as_ + a1 * bs) / (bs + as_)
    return lf.filter(b1.is_not_null() & a1.is_not_null() & (bs + as_ > 0)).select(
        pl.col("t"),
        mid.alias("mid"),
        ((bs - as_) / (bs + as_)).alias("imb_l1"),
        ((bid5 - ask5) / (bid5 + ask5)).alias("imb_l5"),
        ((micro - mid) / mid * 1e4).alias("mp_dev_bp"),
    )


def _trade_flow(lf: pl.LazyFrame) -> pl.LazyFrame:
    """按秒聚合：signed = Σ side·px·sz，gross = Σ px·sz。"""
    return (
        lf.select(
            (pl.col("t") // 1000 * 1000).alias("sec"),
            (pl.col("side").cast(pl.Float64) * pl.col("px") * pl.col("sz")).alias("signed"),
            (pl.col("px") * pl.col("sz")).alias("gross"),
        )
        .group_by("sec")
        .agg(pl.col("signed").sum(), pl.col("gross").sum())
    )


def build(parquet_root: Path, coin: str, dates: list[str], horizons=HORIZONS) -> pl.DataFrame:
    """返回 1 秒网格特征表：t, mid, 特征列, fwd_<h>（bp）。"""
    lo, _ = dataset.day_ms(dates[0])
    _, hi = dataset.day_ms(dates[-1])
    grid = pl.DataFrame({"t": pl.int_range(lo, hi, 1000, eager=True, dtype=pl.Int64)})

    book = _book_features(dataset.load(parquet_root, coin, "l2book", dates, clip=False)).collect().sort("t")
    # 每秒取 ≤ t 的最后一个快照；超过 5 秒没有快照就视为无数据（避免用陈旧簿）
    feat = grid.join_asof(book, on="t", strategy="backward", tolerance=5_000)

    flow = _trade_flow(dataset.load(parquet_root, coin, "trades", dates, clip=False)).collect()
    flow = (
        grid.join(flow.rename({"sec": "t"}), on="t", how="left")
        .with_columns(pl.col("signed").fill_null(0.0), pl.col("gross").fill_null(0.0))  # 只填成交列，别把 t 提升成浮点
        .sort("t")
    )
    for h in (1, 5, 30):
        signed = pl.col("signed").rolling_sum(window_size=h, min_samples=1)
        gross = pl.col("gross").rolling_sum(window_size=h, min_samples=1)
        flow = flow.with_columns(pl.when(gross > 0).then(signed / gross).otherwise(None).alias(f"tfi_{h}"))
    feat = feat.join(flow.select(["t", "tfi_1", "tfi_5", "tfi_30"]), on="t", how="left")

    # 前向 mid：t+h 时刻 ≤ 的最后快照
    fwd_src = book.select(["t", "mid"]).rename({"mid": "mid_fwd"})
    for h in horizons:
        shifted = feat.select((pl.col("t") + h * 1000).alias("t_fwd"), pl.col("t"))
        matched = shifted.sort("t_fwd").join_asof(fwd_src, left_on="t_fwd", right_on="t", strategy="backward", tolerance=5_000)
        feat = feat.join(matched.select(["t", "mid_fwd"]).rename({"mid_fwd": f"mid_fwd_{h}"}), on="t", how="left")
        feat = feat.with_columns(((pl.col(f"mid_fwd_{h}") - pl.col("mid")) / pl.col("mid") * 1e4).alias(f"fwd_{h}")).drop(f"mid_fwd_{h}")
    return feat.filter(pl.col("mid").is_not_null())
