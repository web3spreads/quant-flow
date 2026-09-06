"""
跨日读取：把若干天的 parquet 拼成一个按 `t` 排序、去重的 LazyFrame。

录制器按本机接收时间切日，所以某天的文件里可能带有邻日 `t` 的记录；
拼接相邻日期后再按 `t` 排序、去重即可，不需要任何插补。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import polars as pl


def date_range(start: str, end: str) -> list[str]:
    a = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    b = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return [(a + timedelta(days=i)).strftime("%Y-%m-%d") for i in range((b - a).days + 1)]


def day_ms(date: str) -> tuple[int, int]:
    start = int(datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
    return start, start + 86_400_000


def load(parquet_root: Path, coin: str, channel: str, dates: list[str], clip: bool = True) -> pl.LazyFrame:
    """读取 dates 内某标的某频道；clip=True 时按交易所时间裁到 [首日 0 点, 末日 24 点)。"""
    files = [parquet_root / coin / d / f"{channel}.parquet" for d in dates]
    files = [f for f in files if f.exists()]
    if not files:
        raise FileNotFoundError(f"{coin}/{channel} 在 {dates[0]}..{dates[-1]} 没有 parquet")
    lf = pl.concat([pl.scan_parquet(f) for f in files])
    if channel == "trades":
        lf = lf.unique(subset=["tid"], keep="first")
    else:
        lf = lf.unique(subset=[c for c in lf.collect_schema().names() if c != "r"], keep="first")
    lf = lf.sort(["t", "r"])
    if clip:
        lo, _ = day_ms(dates[0])
        _, hi = day_ms(dates[-1])
        lf = lf.filter((pl.col("t") >= lo) & (pl.col("t") < hi))
    return lf


def with_mid(lf: pl.LazyFrame, channel: str) -> pl.LazyFrame:
    """给簿/bbo 加中间价与 L1 失衡列（研究特征的最小公分母）。"""
    if channel == "bbo":
        bid_px, bid_sz, ask_px, ask_sz = "bid_px", "bid_sz", "ask_px", "ask_sz"
    else:
        bid_px, bid_sz, ask_px, ask_sz = "bid_px_1", "bid_sz_1", "ask_px_1", "ask_sz_1"
    return lf.with_columns(
        ((pl.col(bid_px) + pl.col(ask_px)) / 2).alias("mid"),
        ((pl.col(bid_sz) - pl.col(ask_sz)) / (pl.col(bid_sz) + pl.col(ask_sz))).alias("imb_l1"),
        (((pl.col(bid_px) * pl.col(ask_sz)) + (pl.col(ask_px) * pl.col(bid_sz))) / (pl.col(bid_sz) + pl.col(ask_sz))).alias("microprice"),
    )
