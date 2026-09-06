"""
质量表：每个「标的 × 日」的覆盖率、缺口、延迟、RTT 与纳入判定。

覆盖率口径 = l2book 有数据的秒数 / 86400（l2book 约 2 Hz，是最连续的主数据）。
优先用录制器清单里的实时统计（`channels.l2book.seconds_with_data`）；清单没有频道统计
（补验生成的）时按 parquet 的 `_ingest.json` 重算。未达 `--min-coverage` 的日子标为不纳入并给出原因。
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

FIELDS = ["coin", "date", "include", "reason", "l2book_coverage", "l2book_rows", "trades_rows", "bbo_rows",
          "gaps", "max_gap_s", "dropped", "latency_p50_ms", "latency_p99_ms", "rtt_p50_ms", "rtt_p99_ms",
          "gzip_ok", "manifest_source"]


def day_quality(coin: str, date: str, raw_day: Path, parquet_day: Path, min_coverage: float) -> dict:
    manifest = _read_json(raw_day / "manifest.json")
    side = _read_json(parquet_day / "_ingest.json")
    ch = (manifest or {}).get("channels") or {}
    l2 = ch.get("l2book") or {}
    files = (manifest or {}).get("files") or {}
    gzip_ok = all(f.get("gzip_ok") for f in files.values()) if files else None
    seconds = l2.get("seconds_with_data")
    source = "manifest" if seconds is not None else ("parquet" if side.get("l2book") else "none")
    if seconds is None and side.get("l2book"):
        seconds = side["l2book"].get("seconds")
    coverage = (seconds / 86400) if seconds is not None else None
    lat = l2.get("latency_ms") or {}
    rtt = (manifest or {}).get("rtt_ms") or {}
    reasons = []
    if coverage is None:
        reasons.append("无覆盖率数据")
    elif coverage < min_coverage:
        reasons.append(f"覆盖率 {coverage:.3f} < {min_coverage}")
    if gzip_ok is False:
        reasons.append("gzip 异常")
    if not side.get("l2book"):
        reasons.append("未转换")
    return {
        "coin": coin, "date": date,
        "include": not reasons, "reason": "；".join(reasons),
        "l2book_coverage": round(coverage, 4) if coverage is not None else None,
        "l2book_rows": (side.get("l2book") or {}).get("rows_out"),
        "trades_rows": (side.get("trades") or {}).get("rows_out"),
        "bbo_rows": (side.get("bbo") or {}).get("rows_out"),
        "gaps": l2.get("gaps"), "max_gap_s": round(l2["max_gap_ms"] / 1000, 1) if l2.get("max_gap_ms") is not None else None,
        "dropped": (ch.get("bbo") or {}).get("dropped"),
        "latency_p50_ms": lat.get("p50"), "latency_p99_ms": lat.get("p99"),
        "rtt_p50_ms": rtt.get("p50"), "rtt_p99_ms": rtt.get("p99"),
        "gzip_ok": gzip_ok, "manifest_source": (manifest or {}).get("source") if manifest else None,
    }


def _read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def build(raw: Path, parquet: Path, min_coverage: float) -> list[dict]:
    rows = []
    for coin_dir in sorted(p for p in raw.iterdir() if p.is_dir()):
        for day_dir in sorted(p for p in coin_dir.iterdir() if p.is_dir()):
            if len(day_dir.name) != 10:
                continue
            rows.append(day_quality(coin_dir.name, day_dir.name, day_dir, parquet / coin_dir.name / day_dir.name, min_coverage))
    return rows


def to_markdown(rows: list[dict]) -> str:
    head = "| 标的 | 日期 | 纳入 | l2book 覆盖 | l2book 行 | trades 行 | 缺口 | 最大间隔 s | 延迟 p50/p99 ms | RTT p50/p99 ms | 原因 |\n|---|---|---|---|---|---|---|---|---|---|---|\n"
    body = ""
    for r in rows:
        f = lambda v: "-" if v is None else v  # noqa: E731
        body += (f"| {r['coin']} | {r['date']} | {'✓' if r['include'] else '✗'} | {f(r['l2book_coverage'])} | {f(r['l2book_rows'])} | {f(r['trades_rows'])} "
                 f"| {f(r['gaps'])} | {f(r['max_gap_s'])} | {f(r['latency_p50_ms'])}/{f(r['latency_p99_ms'])} | {f(r['rtt_p50_ms'])}/{f(r['rtt_p99_ms'])} | {r['reason']} |\n")
    included = sum(1 for r in rows if r["include"])
    by_coin = {}
    for r in rows:
        by_coin.setdefault(r["coin"], [0, 0])
        by_coin[r["coin"]][1] += 1
        if r["include"]:
            by_coin[r["coin"]][0] += 1
    summary = "、".join(f"{c} {a}/{b}" for c, (a, b) in sorted(by_coin.items()))
    return f"# 数据质量表\n\n纳入 {included}/{len(rows)} 个标的日（{summary}）。覆盖率 = l2book 有数据秒数 / 86400。\n\n{head}{body}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="盘口数据质量表")
    ap.add_argument("--raw", required=True, type=Path)
    ap.add_argument("--parquet", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--markdown", type=Path)
    ap.add_argument("--min-coverage", type=float, default=0.95)
    args = ap.parse_args(argv)
    rows = build(args.raw, args.parquet, args.min_coverage)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    if args.markdown:
        args.markdown.write_text(to_markdown(rows))
    print(f"质量表：{sum(1 for r in rows if r['include'])}/{len(rows)} 纳入 → {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
