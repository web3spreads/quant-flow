"""合成数据上的管线测试：多成员 gzip、乱序、重复、截断都必须被正确处理；质量表按覆盖率判纳入。"""
import gzip
import json
import zlib
from pathlib import Path

import polars as pl
import pytest

from pipeline import dataset, ingest, quality

DAY = "2026-09-04"
T0 = 1788480000000  # 2026-09-04T00:00:00Z


def gz(lines):
    return gzip.compress(("\n".join(json.dumps(x) for x in lines) + "\n").encode())


def make_raw(tmp: Path, coin="BTC", day=DAY, with_manifest=True, coverage_seconds=86000):
    d = tmp / "raw" / coin / day
    d.mkdir(parents=True)
    book = [{"t": T0 + i * 500, "r": T0 + i * 500 + 40, "b": [[100 - j, 1 + j, 2] for j in range(5)], "a": [[101 + j, 1 + j, 3] for j in range(5)]} for i in range(6)]
    # 两个 gzip 成员拼接（重启追加），第二成员重复了第一成员的最后一条快照，且乱序
    (d / "l2book.jsonl.gz").write_bytes(gz(book[:4]) + gz([book[3], book[5], book[4]]))
    trades = [{"t": T0 + i * 1000, "r": T0 + i * 1000 + 30, "side": "B" if i % 2 else "A", "px": 100.5, "sz": 0.1, "tid": 1000 + i} for i in range(5)]
    trades_dup = trades + [trades[2]]  # 同 tid 重复
    body = gz(trades_dup)
    (d / "trades.jsonl.gz").write_bytes(body[:-8])  # 缺 gzip 尾部：强杀形态
    (d / "bbo.jsonl.gz").write_bytes(gz([{"t": T0 + i * 250, "r": T0 + i * 250 + 20, "bid": [100, 1], "ask": [101, 2]} for i in range(4)] + [{"t": T0, "r": T0 + 20, "bid": None, "ask": [101, 2]}]))
    (d / "ctx.jsonl.gz").write_bytes(gz([{"t": T0 + 60000, "r": T0 + 60000, "funding": "0.0001", "oi": "1", "mark": "100.5", "oracle": "100.4", "mid": "100.5", "premium": "0.001", "vol24h": "5"}]))
    if with_manifest:
        (d / "manifest.json").write_text(json.dumps({
            "version": 1, "coin": coin, "date": day, "source": "rotation",
            "files": {"l2book": {"gzip_ok": True}, "trades": {"gzip_ok": False, "error": "Z_BUF_ERROR"}},
            "channels": {"l2book": {"count": 6, "gaps": 0, "max_gap_ms": 500, "seconds_with_data": coverage_seconds, "coverage": coverage_seconds / 86400, "latency_ms": {"p50": 40, "p90": 41, "p99": 42}},
                         "bbo": {"dropped": 3}},
            "rtt_ms": {"p50": 45, "p90": 50, "p99": 60, "n": 1400},
        }))
    return tmp / "raw"


def test_ingest_sorts_dedups_and_flattens(tmp_path):
    raw = make_raw(tmp_path)
    out = tmp_path / "pq"
    side = ingest.ingest_day(raw / "BTC" / DAY, out / "BTC" / DAY, log=lambda *_: None)
    book = pl.read_parquet(out / "BTC" / DAY / "l2book.parquet")
    assert book.height == 6 and side["l2book"]["duplicates"] == 1
    assert book["t"].to_list() == sorted(book["t"].to_list())
    assert book.columns[:4] == ["t", "r", "bid_px_1", "bid_sz_1"] and "ask_n_5" in book.columns
    assert book["bid_px_1"][0] == 100.0 and book["ask_px_5"][0] == 105.0
    trades = pl.read_parquet(out / "BTC" / DAY / "trades.parquet")
    # 截断尾部：已解出的行保留（含 5 条 + 1 条重复 → 去重后 5 条），并被标记
    assert trades.height == 5 and side["trades"]["duplicates"] == 1 and side["trades"]["truncated"] is True
    assert trades["side"].to_list() == [-1, 1, -1, 1, -1]
    bbo = pl.read_parquet(out / "BTC" / DAY / "bbo.parquet")
    assert bbo.height == 5 and bbo["bid_px"].null_count() == 1
    assert side["l2book"]["seconds"] == 3  # 6 条 × 0.5s → 3 个不同秒
    # 幂等：第二次不重做
    again = ingest.ingest_day(raw / "BTC" / DAY, out / "BTC" / DAY, log=lambda *_: pytest.fail("不该重做"))
    assert again == side


def test_quality_table_include_flag(tmp_path):
    raw = make_raw(tmp_path)
    make_raw(tmp_path / "low", coin="BTC", day="2026-09-05", coverage_seconds=80000)
    out = tmp_path / "pq"
    ingest.ingest_day(raw / "BTC" / DAY, out / "BTC" / DAY, log=lambda *_: None)
    good = quality.day_quality("BTC", DAY, raw / "BTC" / DAY, out / "BTC" / DAY, 0.95)
    assert good["include"] is False and "gzip 异常" in good["reason"]  # trades 截断 → gzip_ok False
    assert good["l2book_coverage"] == round(86000 / 86400, 4) and good["rtt_p50_ms"] == 45 and good["latency_p50_ms"] == 40
    low_raw = tmp_path / "low" / "raw" / "BTC" / "2026-09-05"
    ingest.ingest_day(low_raw, out / "BTC" / "2026-09-05", log=lambda *_: None)
    low = quality.day_quality("BTC", "2026-09-05", low_raw, out / "BTC" / "2026-09-05", 0.95)
    assert low["include"] is False and "覆盖率" in low["reason"]
    # 没有清单频道统计时按 parquet 重算覆盖率
    (low_raw / "manifest.json").write_text(json.dumps({"files": {}, "channels": None, "source": "catch-up"}))
    fallback = quality.day_quality("BTC", "2026-09-05", low_raw, out / "BTC" / "2026-09-05", 0.95)
    assert fallback["l2book_coverage"] == round(3 / 86400, 4)
    md = quality.to_markdown([good, low, fallback])
    assert "纳入 0/3" in md


def test_dataset_cross_day_stitch(tmp_path):
    raw = make_raw(tmp_path)
    make_raw(tmp_path / "d2", day="2026-09-05")
    out = tmp_path / "pq"
    ingest.ingest_day(raw / "BTC" / DAY, out / "BTC" / DAY, log=lambda *_: None)
    ingest.ingest_day(tmp_path / "d2" / "raw" / "BTC" / "2026-09-05", out / "BTC" / "2026-09-05", log=lambda *_: None)
    lf = dataset.load(out, "BTC", "l2book", dataset.date_range(DAY, "2026-09-05"), clip=False)
    df = dataset.with_mid(lf, "l2book").collect()
    assert df.height == 6  # 两天内容相同 t → 去重后仍 6 条
    assert df["mid"][0] == 100.5 and abs(df["microprice"][0] - 100.5) < 1e-9
    clipped = dataset.load(out, "BTC", "l2book", ["2026-09-05"]).collect()
    assert clipped.height == 0  # 所有 t 都在 09-04，裁剪到 09-05 为空
