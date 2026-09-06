"""
jsonl.gz → parquet：录制器原始文件转列式中间层。

每个 `<COIN>/<日>/<channel>.jsonl.gz` 转成 `<COIN>/<日>/<channel>.parquet`：
- 多成员 gzip（进程重启后追加写入的形态）由 gzip 模块原生支持；损坏尾部只丢最后一段
- 按交易所时间 `t` 排序（同 `t` 按本机时间 `r`），全表去重：trades 按 tid，其余按除 `r` 外的全部列
  （重连后交易所会重推同一快照）
- 簿快照压平为宽表：bid_px_1..N / bid_sz_1..N / bid_n_1..N / ask_*，l2book N=5，l2full N=20
- 写 `_ingest.json`：每频道输入行数、输出行数、重复数、解析失败数，供质量表引用

幂等：parquet 比原始文件新且未 --force 时跳过。
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import time
import zlib
from pathlib import Path

import polars as pl

CHANNELS = ("l2book", "l2full", "trades", "bbo", "ctx")
BOOK_LEVELS = {"l2book": 5, "l2full": 20}
INGEST_SIDE = "_ingest.json"


def _f(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _i(x) -> int | None:
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def iter_lines(path: Path):
    """流式读多成员 gzip 的每一行；尾部损坏（强杀）只丢最后一段并计数。"""
    bad = 0
    try:
        with gzip.open(path, "rb") as fh:
            for raw in fh:
                yield raw
    except (EOFError, zlib.error, OSError):
        bad += 1
    if bad:
        yield None  # 哨兵：告诉调用方文件尾部不完整


def parse_book(path: Path, levels: int) -> tuple[pl.DataFrame, dict]:
    cols: dict[str, list] = {"t": [], "r": []}
    for side in ("bid", "ask"):
        for i in range(1, levels + 1):
            cols[f"{side}_px_{i}"] = []
            cols[f"{side}_sz_{i}"] = []
            cols[f"{side}_n_{i}"] = []
    n_in = n_bad = 0
    truncated = False
    for raw in iter_lines(path):
        if raw is None:
            truncated = True
            continue
        n_in += 1
        try:
            o = json.loads(raw)
            t = _i(o.get("t"))
            r = _i(o.get("r"))
            if t is None or r is None:
                raise ValueError("t/r 缺失")
            b, a = o.get("b") or [], o.get("a") or []
        except Exception:
            n_bad += 1
            continue
        cols["t"].append(t)
        cols["r"].append(r)
        for side, lv in (("bid", b), ("ask", a)):
            for i in range(1, levels + 1):
                row = lv[i - 1] if i - 1 < len(lv) else None
                cols[f"{side}_px_{i}"].append(_f(row[0]) if row else None)
                cols[f"{side}_sz_{i}"].append(_f(row[1]) if row else None)
                cols[f"{side}_n_{i}"].append(_i(row[2]) if row and len(row) > 2 else None)
    schema = {"t": pl.Int64, "r": pl.Int64}
    for side in ("bid", "ask"):
        for i in range(1, levels + 1):
            schema[f"{side}_px_{i}"] = pl.Float64
            schema[f"{side}_sz_{i}"] = pl.Float64
            schema[f"{side}_n_{i}"] = pl.Int32
    df = pl.DataFrame(cols, schema=schema)
    return df, {"rows_in": n_in, "parse_errors": n_bad, "truncated": truncated}


def parse_trades(path: Path) -> tuple[pl.DataFrame, dict]:
    cols = {"t": [], "r": [], "side": [], "px": [], "sz": [], "tid": []}
    n_in = n_bad = 0
    truncated = False
    for raw in iter_lines(path):
        if raw is None:
            truncated = True
            continue
        n_in += 1
        try:
            o = json.loads(raw)
            t, r, tid = _i(o.get("t")), _i(o.get("r")), _i(o.get("tid"))
            px, sz = _f(o.get("px")), _f(o.get("sz"))
            side = o.get("side")
            if None in (t, r, tid, px, sz) or side not in ("B", "A"):
                raise ValueError("字段缺失")
        except Exception:
            n_bad += 1
            continue
        cols["t"].append(t)
        cols["r"].append(r)
        cols["side"].append(1 if side == "B" else -1)  # +1 主动买（taker buy），-1 主动卖
        cols["px"].append(px)
        cols["sz"].append(sz)
        cols["tid"].append(tid)
    df = pl.DataFrame(cols, schema={"t": pl.Int64, "r": pl.Int64, "side": pl.Int8, "px": pl.Float64, "sz": pl.Float64, "tid": pl.Int64})
    return df, {"rows_in": n_in, "parse_errors": n_bad, "truncated": truncated}


def parse_bbo(path: Path) -> tuple[pl.DataFrame, dict]:
    cols = {"t": [], "r": [], "bid_px": [], "bid_sz": [], "ask_px": [], "ask_sz": []}
    n_in = n_bad = 0
    truncated = False
    for raw in iter_lines(path):
        if raw is None:
            truncated = True
            continue
        n_in += 1
        try:
            o = json.loads(raw)
            t, r = _i(o.get("t")), _i(o.get("r"))
            if t is None or r is None:
                raise ValueError("t/r 缺失")
            bid, ask = o.get("bid"), o.get("ask")
        except Exception:
            n_bad += 1
            continue
        cols["t"].append(t)
        cols["r"].append(r)
        cols["bid_px"].append(_f(bid[0]) if bid else None)
        cols["bid_sz"].append(_f(bid[1]) if bid else None)
        cols["ask_px"].append(_f(ask[0]) if ask else None)
        cols["ask_sz"].append(_f(ask[1]) if ask else None)
    df = pl.DataFrame(cols, schema={"t": pl.Int64, "r": pl.Int64, "bid_px": pl.Float64, "bid_sz": pl.Float64, "ask_px": pl.Float64, "ask_sz": pl.Float64})
    return df, {"rows_in": n_in, "parse_errors": n_bad, "truncated": truncated}


def parse_ctx(path: Path) -> tuple[pl.DataFrame, dict]:
    keys = ("funding", "oi", "mark", "oracle", "mid", "premium", "vol24h")
    cols: dict[str, list] = {"t": [], "r": [], **{k: [] for k in keys}}
    n_in = n_bad = 0
    truncated = False
    for raw in iter_lines(path):
        if raw is None:
            truncated = True
            continue
        n_in += 1
        try:
            o = json.loads(raw)
            t, r = _i(o.get("t")), _i(o.get("r"))
            if t is None or r is None:
                raise ValueError("t/r 缺失")
        except Exception:
            n_bad += 1
            continue
        cols["t"].append(t)
        cols["r"].append(r)
        for k in keys:
            cols[k].append(_f(o.get(k)))
    df = pl.DataFrame(cols, schema={"t": pl.Int64, "r": pl.Int64, **{k: pl.Float64 for k in keys}})
    return df, {"rows_in": n_in, "parse_errors": n_bad, "truncated": truncated}


def normalize(df: pl.DataFrame, channel: str) -> tuple[pl.DataFrame, int]:
    """排序 + 去重；返回 (df, 重复行数)。"""
    before = df.height
    if channel == "trades":
        df = df.unique(subset=["tid"], keep="first", maintain_order=True)
    else:
        df = df.unique(subset=[c for c in df.columns if c != "r"], keep="first", maintain_order=True)
    df = df.sort(["t", "r"])
    return df, before - df.height


PARSERS = {
    "l2book": lambda p: parse_book(p, BOOK_LEVELS["l2book"]),
    "l2full": lambda p: parse_book(p, BOOK_LEVELS["l2full"]),
    "trades": parse_trades,
    "bbo": parse_bbo,
    "ctx": parse_ctx,
}


def ingest_day(raw_day: Path, out_day: Path, force: bool = False, log=print) -> dict:
    out_day.mkdir(parents=True, exist_ok=True)
    side_path = out_day / INGEST_SIDE
    side = json.loads(side_path.read_text()) if side_path.exists() else {}
    changed = False
    for channel in CHANNELS:
        src = raw_day / f"{channel}.jsonl.gz"
        dst = out_day / f"{channel}.parquet"
        if not src.exists():
            continue
        if dst.exists() and not force and dst.stat().st_mtime >= src.stat().st_mtime and channel in side:
            continue
        t0 = time.time()
        df, stats = PARSERS[channel](src)
        df, dups = normalize(df, channel)
        df.write_parquet(dst, compression="zstd")
        side[channel] = {**stats, "rows_out": df.height, "duplicates": dups, "seconds": elapsed_seconds(df),
                         "t_first": int(df["t"].min()) if df.height else None, "t_last": int(df["t"].max()) if df.height else None}
        changed = True
        log(f"  {raw_day.parent.name}/{raw_day.name}/{channel}: {stats['rows_in']} → {df.height} 行"
            f"（重复 {dups}，解析失败 {stats['parse_errors']}{'，尾部截断' if stats['truncated'] else ''}）{time.time() - t0:.1f}s")
    if changed or not side_path.exists():
        side_path.write_text(json.dumps(side, ensure_ascii=False, indent=1))
    return side


def elapsed_seconds(df: pl.DataFrame) -> int:
    """有数据的秒数（按本机接收时间 r 计，与录制器清单口径一致）。"""
    if df.height == 0:
        return 0
    return int(df.select((pl.col("r") // 1000).n_unique()).item())


def list_days(raw: Path, coin: str | None, date: str | None):
    for coin_dir in sorted(p for p in raw.iterdir() if p.is_dir()):
        if coin and coin_dir.name != coin:
            continue
        for day_dir in sorted(p for p in coin_dir.iterdir() if p.is_dir()):
            if date and day_dir.name != date:
                continue
            if len(day_dir.name) == 10 and day_dir.name[4] == "-":
                yield coin_dir.name, day_dir.name, day_dir


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="录制器 jsonl.gz → parquet")
    ap.add_argument("--raw", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--coin")
    ap.add_argument("--date")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    n = 0
    for coin, day, day_dir in list_days(args.raw, args.coin, args.date):
        ingest_day(day_dir, args.out / coin / day, force=args.force)
        n += 1
    print(f"ingest 完成：{n} 个标的日", file=sys.stderr)
    return 0


if __name__ == "__main__":
    os.environ.setdefault("POLARS_MAX_THREADS", "2")
    sys.exit(main())
