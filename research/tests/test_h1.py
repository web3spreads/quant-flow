"""H1 框架在合成数据上的行为：特征只用过去信息、前向变动只用未来信息；植入的依赖能被检出，纯噪声不会误报。"""
import json
from pathlib import Path

import numpy as np
import polars as pl

from hypotheses import h1
from pipeline import features

T0 = 1788480000000  # 2026-09-04T00:00:00Z


def synth_day(root: Path, coin: str, day: str, day_start: int, rng: np.random.Generator, effect_bp: float):
    """每 500ms 一个簿快照；L1 失衡以 effect_bp 的强度预测下一秒 mid 变动；成交随机。"""
    n = 86_400 * 2
    imb = rng.uniform(-1, 1, n)
    mid = np.empty(n)
    mid[0] = 1000.0
    noise = rng.normal(0, 0.02, n)
    for i in range(1, n):
        # 上一快照的失衡推动本快照的 mid（bp 量级），加噪声
        mid[i] = mid[i - 1] * (1 + effect_bp * imb[i - 1] / 1e4 / 2) + noise[i]
    bid = mid - 0.5
    ask = mid + 0.5
    bs = 1 + imb  # imb = (bs-as)/(bs+as) 当 as = 1 - imb
    as_ = 1 - imb
    t = T0 + np.arange(n) * 500 if day_start == T0 else day_start + np.arange(n) * 500
    cols = {"t": t.astype(np.int64), "r": (t + 40).astype(np.int64)}
    for i in range(1, 6):
        cols[f"bid_px_{i}"] = bid - (i - 1)
        cols[f"bid_sz_{i}"] = bs if i == 1 else np.full(n, 1.0)
        cols[f"bid_n_{i}"] = np.full(n, 1, dtype=np.int32)
        cols[f"ask_px_{i}"] = ask + (i - 1)
        cols[f"ask_sz_{i}"] = as_ if i == 1 else np.full(n, 1.0)
        cols[f"ask_n_{i}"] = np.full(n, 1, dtype=np.int32)
    d = root / coin / day
    d.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(cols).write_parquet(d / "l2book.parquet")
    m = 20_000
    tt = np.sort(rng.integers(day_start, day_start + 86_400_000, m))
    pl.DataFrame({
        "t": tt.astype(np.int64), "r": (tt + 30).astype(np.int64),
        "side": rng.choice(np.array([1, -1], dtype=np.int8), m), "px": np.full(m, 1000.0), "sz": rng.uniform(0.01, 1, m),
        "tid": np.arange(m, dtype=np.int64) + (0 if day_start == T0 else 10**6),
    }).write_parquet(d / "trades.parquet")


def test_features_and_h1_detect_planted_effect(tmp_path):
    rng = np.random.default_rng(7)
    synth_day(tmp_path, "BTC", "2026-09-04", T0, rng, effect_bp=6.0)
    feat = features.build(tmp_path, "BTC", ["2026-09-04"])
    assert feat.height > 86_000
    assert set(features.FEATURES).issubset(feat.columns) and {"fwd_1", "fwd_5", "fwd_30"}.issubset(feat.columns)
    # 特征在 [-1, 1]，成交流失衡有值，前向变动非空
    assert feat["imb_l1"].abs().max() <= 1.0 + 1e-9
    assert feat["tfi_30"].null_count() < feat.height * 0.1
    assert feat["fwd_1"].null_count() < 100

    res = h1.run(tmp_path, "BTC", ["2026-09-04"], tmp_path / "out")
    by = {(r["feature"], r["h"]): r for r in res}
    planted = by[("imb_l1", 1)]
    assert planted["in_sample"] is True and planted["pass"] is False  # 单周只能样本内，不允许 PASS
    # 最强分位可能在负侧（失衡为负 → mid 下行）：效应用绝对值判，符号须与分位位置一致
    assert abs(planted["t"]) > 3 and planted["accuracy"] > 0.55
    assert (planted["mean_bp"] > 0) == (planted["strongest_bin"] >= 5)
    # 成交流是纯噪声：不应出现显著效应
    noise = by[("tfi_5", 5)]
    assert abs(noise["t"]) < 3
    out = json.loads((tmp_path / "out" / "h1_BTC.json").read_text())
    assert out["n_tests"] == len(features.FEATURES) * len(features.HORIZONS)
    assert (tmp_path / "out" / "h1_BTC.csv").exists()


def test_h1_out_of_sample_uses_first_week_for_cuts(tmp_path):
    rng = np.random.default_rng(11)
    # 两个 ISO 周：09-04（周五，第 36 周）与 09-07（周一，第 37 周）
    synth_day(tmp_path, "ETH", "2026-09-04", T0, rng, effect_bp=12.0)
    synth_day(tmp_path, "ETH", "2026-09-07", T0 + 3 * 86_400_000, rng, effect_bp=12.0)
    res = h1.run(tmp_path, "ETH", ["2026-09-04", "2026-09-05", "2026-09-06", "2026-09-07"], tmp_path / "out")
    r = next(x for x in res if x["feature"] == "imb_l1" and x["h"] == 1)
    assert r["in_sample"] is False and r["train_week"] == 202636 and r["test_weeks"] == [202637]
    assert r["pass"] is True and r["weeks_same_sign"] == "1/1"
