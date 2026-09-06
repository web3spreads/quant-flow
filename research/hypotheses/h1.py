"""
H1：簿失衡与成交流对 1/5/30 秒中间价变动的预测力。

评估协议（与预注册一致，代码里不改门槛，门槛作为参数传入并原样写进结果）：
- 每个 (特征, 期限 h) 一次检验；为避免重叠样本的自相关，期限 h 的样本只取每 h 秒一个网格点；
- 按 ISO 周切分：训练周只用来定十分位切点，检验周计算各分位的平均前向 Δmid（bp）、
  方向准确率（观测符号与该分位均值符号一致的比例）、t 值（均值 / 标准误）；
- 报告「最强分位」（|均值| 最大的那个）及其按周的符号一致性；
- 多重比较：Bonferroni 按 (特征数 × 期限数) 校正，给出校正后 p 值；预注册门槛 t≥3 另行判定。
只有一个 ISO 周时退化为样本内评估并明确标注 in_sample=True（开发切片用）。
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

import numpy as np
import polars as pl
from scipy import stats

from pipeline import dataset, features

DEFAULT_THRESHOLDS = {"min_abs_bp": 3.0, "min_accuracy": 0.55, "min_t": 3.0}


def week_id(col: pl.Expr) -> pl.Expr:
    dt = pl.from_epoch(col, time_unit="ms")
    return (dt.dt.iso_year() * 100 + dt.dt.week()).alias("week")


def evaluate(feat: pl.DataFrame, feature: str, h: int, n_bins: int = 10, seed: int = 20261003) -> dict:
    day0 = int(feat["t"].min()) // 86_400_000 * 86_400_000
    sub = feat.filter(((pl.col("t") - day0) // 1000 % h == 0) & pl.col(feature).is_not_null() & pl.col(f"fwd_{h}").is_not_null())
    sub = sub.with_columns(week_id(pl.col("t")))
    weeks = sorted(sub["week"].unique().to_list())
    in_sample = len(weeks) < 2
    train = sub if in_sample else sub.filter(pl.col("week") == weeks[0])
    test = sub if in_sample else sub.filter(pl.col("week") != weeks[0])
    if train.height < n_bins * 20 or test.height < n_bins * 20:
        return {"feature": feature, "h": h, "n": int(test.height), "skipped": "样本不足"}
    x = train[feature].to_numpy()
    cuts = np.quantile(x, np.linspace(0, 1, n_bins + 1)[1:-1])
    xt = test[feature].to_numpy()
    y = test[f"fwd_{h}"].to_numpy()
    w = test["week"].to_numpy()
    bins = np.searchsorted(cuts, xt, side="right")
    rows = []
    for b in range(n_bins):
        m = bins == b
        n = int(m.sum())
        if n < 20:
            continue
        yy = y[m]
        mean = float(yy.mean())
        se = float(yy.std(ddof=1) / math.sqrt(n)) if n > 1 else float("nan")
        t = mean / se if se and se > 0 else float("nan")
        sign = 1 if mean >= 0 else -1
        acc = float((np.sign(yy) == sign).mean())
        by_week = {}
        for wk in np.unique(w[m]):
            yw = yy[w[m] == wk]
            by_week[int(wk)] = float(yw.mean()) if len(yw) else float("nan")
        rows.append({"bin": b, "n": n, "mean_bp": mean, "t": t, "accuracy": acc, "by_week": by_week})
    if not rows:
        return {"feature": feature, "h": h, "n": int(test.height), "skipped": "分位样本不足"}
    strongest = max(rows, key=lambda r: abs(r["mean_bp"]))
    sign = 1 if strongest["mean_bp"] >= 0 else -1
    week_means = [v for v in strongest["by_week"].values() if not math.isnan(v)]
    weeks_same_sign = sum(1 for v in week_means if (v >= 0) == (sign >= 0))
    return {
        "feature": feature, "h": h, "n": int(test.height), "in_sample": in_sample,
        "train_week": weeks[0], "test_weeks": weeks if in_sample else weeks[1:],
        "strongest_bin": strongest["bin"], "mean_bp": strongest["mean_bp"], "t": strongest["t"],
        "accuracy": strongest["accuracy"], "bin_n": strongest["n"],
        "weeks_same_sign": f"{weeks_same_sign}/{len(week_means)}",
        "p_raw": float(2 * stats.t.sf(abs(strongest["t"]), df=max(1, strongest["n"] - 1))) if not math.isnan(strongest["t"]) else float("nan"),
        "bins": rows,
    }


def judge(row: dict, thresholds: dict, n_tests: int) -> dict:
    if row.get("skipped"):
        return {**row, "pass": False, "p_bonferroni": None}
    p_adj = min(1.0, row["p_raw"] * n_tests) if row["p_raw"] == row["p_raw"] else None
    # 门槛作用在「最强分位」的效应绝对值上：负侧分位（失衡为负 → mid 下行）同样算预测力
    ok = (abs(row["mean_bp"]) >= thresholds["min_abs_bp"] and row["accuracy"] >= thresholds["min_accuracy"]
          and abs(row["t"]) >= thresholds["min_t"] and not row["in_sample"])
    return {**row, "pass": bool(ok), "p_bonferroni": p_adj}


def run(parquet_root: Path, coin: str, dates: list[str], out_dir: Path, thresholds=DEFAULT_THRESHOLDS, seed: int = 20261003) -> list[dict]:
    feat = features.build(parquet_root, coin, dates)
    results = []
    tests = [(f, h) for f in features.FEATURES for h in features.HORIZONS]
    for f, h in tests:
        results.append(judge(evaluate(feat, f, h, seed=seed), thresholds, len(tests)))
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"h1_{coin}.json").write_text(json.dumps({"coin": coin, "dates": dates, "thresholds": thresholds, "n_tests": len(tests), "results": results}, ensure_ascii=False, indent=1))
    with (out_dir / f"h1_{coin}.csv").open("w", newline="") as fh:
        fields = ["feature", "h", "n", "in_sample", "strongest_bin", "mean_bp", "t", "accuracy", "weeks_same_sign", "p_bonferroni", "pass", "skipped"]
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in results:
            w.writerow({k: r.get(k) for k in fields})
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="H1：簿失衡/成交流 → 前向 Δmid")
    ap.add_argument("--parquet", required=True, type=Path)
    ap.add_argument("--coin", required=True)
    ap.add_argument("--dates", required=True, help="逗号分隔的纳入日期，或 start:end")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--seed", type=int, default=20261003)
    args = ap.parse_args(argv)
    dates = dataset.date_range(*args.dates.split(":")) if ":" in args.dates else args.dates.split(",")
    results = run(args.parquet, args.coin.upper(), dates, args.out, seed=args.seed)
    for r in results:
        if r.get("skipped"):
            print(f"{r['feature']:>10} h={r['h']:<3} 跳过：{r['skipped']}", file=sys.stderr)
        else:
            print(f"{r['feature']:>10} h={r['h']:<3} n={r['n']:<7} bin={r['strongest_bin']} mean={r['mean_bp']:+.2f}bp t={r['t']:+.2f} acc={r['accuracy']:.3f} weeks={r['weeks_same_sign']} {'in-sample' if r['in_sample'] else ''} {'PASS' if r['pass'] else ''}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
