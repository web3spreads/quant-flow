"""
QLib 信号 Walk-Forward 回测评估脚本

目标：评估 QLib 模型信号作为 LLM 决策辅助输入的有效性
重点关注方向准确率（>50% 即有价值）、信号过滤后策略收益、夏普比率、最大回撤

测试策略：
a. 信号阈值过滤：只在 |prediction| > threshold 时交易
b. 信号强度仓位：prediction 值越大，仓位越大
c. 交易成本：0.05% 单边手续费
d. 方向准确率追踪
e. 不同持仓周期（1期、2期、3期）
"""

import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# 导入项目模块
from src.qlib_engine.data.handler import CryptoAlpha158
from src.qlib_engine.model.trainer import QLibModelTrainer

# ============================================================
# 配置
# ============================================================

# 各币种最佳配置
# top_k=0 表示不做特征选择（使用全部特征），避免特征过少导致模型退化
SYMBOL_CONFIGS = {
    "BTC": [
        {"freq": "4h", "label_periods": 3, "top_k": 10, "tag": "4h_l3_k10"},
        {"freq": "4h", "label_periods": 3, "top_k": 0, "tag": "4h_l3_all"},
        {"freq": "4h", "label_periods": 3, "top_k": 20, "tag": "4h_l3_k20"},
    ],
    "ETH": [
        {"freq": "4h", "label_periods": 3, "top_k": 10, "tag": "4h_l3_k10"},
        {"freq": "4h", "label_periods": 3, "top_k": 0, "tag": "4h_l3_all"},
        {"freq": "1h", "label_periods": 12, "top_k": 10, "tag": "1h_l12_k10"},
        {"freq": "1h", "label_periods": 12, "top_k": 0, "tag": "1h_l12_all"},
    ],
    "SOL": [
        {"freq": "4h", "label_periods": 5, "top_k": 10, "tag": "4h_l5_k10"},
        {"freq": "4h", "label_periods": 5, "top_k": 0, "tag": "4h_l5_all"},
        {"freq": "4h", "label_periods": 5, "top_k": 20, "tag": "4h_l5_k20"},
    ],
}

# 交易成本（单边）
FEE_RATE = 0.0005  # 0.05%

# 信号阈值分位数
THRESHOLD_QUANTILES = [0.3, 0.5, 0.7]

# 持仓周期
HOLD_PERIODS = [1, 2, 3]

# Walk-forward 参数
TRAIN_RATIO = 0.7  # 训练集比例
VALID_RATIO = 0.15  # 验证集比例（剩余为测试集）

# 候选模型
MODEL_CANDIDATES = ["lightgbm", "linear", "elasticnet"]


# ============================================================
# 数据加载
# ============================================================

@dataclass
class BacktestResult:
    """回测结果"""
    symbol: str
    config_tag: str
    hold_period: int
    threshold_quantile: float
    threshold_value: float
    # 方向指标
    direction_accuracy: float  # 方向准确率
    direction_accuracy_filtered: float  # 过滤后方向准确率
    total_signals: int  # 总信号数
    filtered_signals: int  # 过滤后信号数
    trade_ratio: float  # 交易比例
    # 策略指标（sign策略）
    sign_total_return: float
    sign_annual_return: float
    sign_sharpe: float
    sign_max_drawdown: float
    # 策略指标（信号强度仓位）
    weighted_total_return: float
    weighted_annual_return: float
    weighted_sharpe: float
    weighted_max_drawdown: float
    # 模型信息
    best_model: str
    ic: float
    rank_ic: float


def load_data(symbol: str, freq: str) -> pd.DataFrame:
    """加载数据并转换为 handler 需要的格式"""
    path = f"data/qlib/{symbol}_{freq}.parquet"
    df = pd.read_parquet(path)

    # 设置时间索引
    df["datetime"] = pd.to_datetime(df["timestamp"])
    df = df.set_index("datetime")
    df = df.drop(columns=["timestamp"], errors="ignore")

    # 重命名为 $close 格式（handler 要求）
    rename_map = {
        "open": "$open",
        "high": "$high",
        "low": "$low",
        "close": "$close",
        "volume": "$volume",
    }
    df = df.rename(columns=rename_map)

    return df


# ============================================================
# Walk-Forward 训练和预测
# ============================================================

def walk_forward_train_predict(
    df: pd.DataFrame,
    label_periods: int,
    top_k: int,
) -> tuple[pd.Series, pd.Series, pd.DataFrame, str, dict]:
    """
    Walk-forward 训练：在训练集上训练，在测试集上预测

    返回: (predictions, labels, price_data, best_model_type, eval_metrics)
    """
    # 创建 handler
    handler = CryptoAlpha158(
        include_perpetual=True,
        normalize=True,
        fillna=True,
        label_periods=label_periods,
        feature_select_top_k=top_k,
        label_winsorize_quantile=0.01,
    )

    # 计算特征和标签
    features = handler.calculate_features(df)
    label = handler.calculate_label(df)

    # 时间分割
    timestamps = features.index.sort_values()
    n_total = len(timestamps)
    n_train = int(n_total * TRAIN_RATIO)
    n_valid = int(n_total * VALID_RATIO)

    train_end = timestamps[n_train - 1]
    valid_end = timestamps[n_train + n_valid - 1]

    train_mask = features.index <= train_end
    valid_mask = (features.index > train_end) & (features.index <= valid_end)
    test_mask = features.index > valid_end

    X_train = features[train_mask]
    y_train = label[train_mask]
    X_valid = features[valid_mask]
    y_valid = label[valid_mask]
    X_test = features[test_mask]
    y_test = label[test_mask]

    # 特征选择（在训练集上）
    if top_k > 0:
        handler.select_features_by_ic(X_train, y_train, top_k=top_k)
        X_train = handler.apply_feature_selection(X_train)
        X_valid = handler.apply_feature_selection(X_valid)
        X_test = handler.apply_feature_selection(X_test)

    # 标准化
    X_train = handler.fit_transform(X_train)
    X_valid = handler.transform(X_valid)
    X_test = handler.transform(X_test)

    # 训练模型（使用较轻的正则化，默认参数正则化过强导致模型退化为常数）
    custom_params = {
        "lightgbm": {
            "objective": "mse",
            "learning_rate": 0.05,
            "num_leaves": 31,
            "max_depth": 5,
            "colsample_bytree": 0.7,
            "subsample": 0.7,
            "lambda_l1": 1.0,
            "lambda_l2": 1.0,
            "min_child_samples": 20,
            "num_threads": 4,
            "n_estimators": 500,
            "early_stopping_rounds": 50,
            "verbose": -1,
        },
        "linear": {
            "alpha": 1.0,  # 默认 10.0 太强
        },
        "elasticnet": {
            "alpha": 0.1,  # 默认 1.0 太强
            "l1_ratio": 0.5,
            "max_iter": 2000,
        },
    }
    trainer = QLibModelTrainer(model_dir="models/qlib_test", custom_params=custom_params)
    models = trainer.train_all(X_train, y_train, X_valid, y_valid, model_types=MODEL_CANDIDATES)

    # 选择最优模型（在验证集上比较 IC）
    best_model_type = None
    best_ic = -np.inf
    eval_metrics = {}

    for model_type in models:
        pred_valid = trainer.predict(model_type, X_valid)
        common_idx = pred_valid.index.intersection(y_valid.dropna().index)
        if len(common_idx) < 10:
            continue
        ic = pred_valid.loc[common_idx].corr(y_valid.loc[common_idx])
        rank_ic = pred_valid.loc[common_idx].rank().corr(y_valid.loc[common_idx].rank())
        if np.isnan(ic):
            ic = 0
        if np.isnan(rank_ic):
            rank_ic = 0
        eval_metrics[model_type] = {"IC": ic, "Rank_IC": rank_ic}
        if ic > best_ic:
            best_ic = ic
            best_model_type = model_type

    if best_model_type is None:
        best_model_type = list(models.keys())[0]

    # 在测试集上预测
    predictions = trainer.predict(best_model_type, X_test)

    # 获取测试集对应的价格数据
    test_prices = df.loc[X_test.index, ["$close", "$open", "$high", "$low"]]

    # 对齐 predictions 和 y_test
    common_idx = predictions.index.intersection(y_test.dropna().index)
    predictions = predictions.loc[common_idx]
    y_test_aligned = y_test.loc[common_idx]

    print(f"    训练集: {len(X_train)} 条 ({timestamps[0].strftime('%Y-%m-%d')} ~ {train_end.strftime('%Y-%m-%d')})")
    print(f"    验证集: {len(X_valid)} 条")
    print(f"    测试集: {len(X_test)} 条 ({(valid_end + pd.Timedelta(hours=1)).strftime('%Y-%m-%d')} ~ {timestamps[-1].strftime('%Y-%m-%d')})")
    print(f"    最优模型: {best_model_type} (验证集 IC={best_ic:.4f})")

    return predictions, y_test_aligned, test_prices, best_model_type, eval_metrics


# ============================================================
# 回测核心
# ============================================================

def calculate_metrics(returns: pd.Series, freq_hours: int) -> dict:
    """计算策略指标"""
    if len(returns) == 0 or returns.std() == 0:
        return {
            "total_return": 0.0,
            "annual_return": 0.0,
            "sharpe": 0.0,
            "max_drawdown": 0.0,
        }

    # 累计收益
    cum_returns = (1 + returns).cumprod()
    total_return = cum_returns.iloc[-1] - 1

    # 年化收益（按小时计算）
    total_hours = len(returns) * freq_hours
    years = total_hours / (365.25 * 24)
    if years > 0 and total_return > -1:
        annual_return = (1 + total_return) ** (1 / years) - 1
    else:
        annual_return = -1.0

    # 夏普比率（年化）
    periods_per_year = (365.25 * 24) / freq_hours
    sharpe = returns.mean() / returns.std() * np.sqrt(periods_per_year) if returns.std() > 0 else 0

    # 最大回撤
    rolling_max = cum_returns.cummax()
    drawdown = (cum_returns - rolling_max) / rolling_max
    max_drawdown = drawdown.min()

    return {
        "total_return": total_return,
        "annual_return": annual_return,
        "sharpe": sharpe,
        "max_drawdown": max_drawdown,
    }


def run_backtest(
    predictions: pd.Series,
    labels: pd.Series,
    prices: pd.DataFrame,
    hold_period: int,
    threshold_quantile: float,
    freq_hours: int,
) -> dict:
    """
    运行单次回测

    策略逻辑：
    - sign策略：按 sign(prediction) 做多/做空，过滤低信号
    - weighted策略：按 prediction 绝对值调整仓位大小
    """
    # 计算阈值
    abs_pred = predictions.abs()
    threshold = abs_pred.quantile(threshold_quantile)

    # 方向准确率（全量）
    actual_direction = np.sign(labels)
    pred_direction = np.sign(predictions)
    valid_mask = (actual_direction != 0) & (pred_direction != 0)
    if valid_mask.sum() > 0:
        direction_accuracy = (pred_direction[valid_mask] == actual_direction[valid_mask]).mean()
    else:
        direction_accuracy = 0.5

    # 信号过滤
    signal_mask = abs_pred > threshold
    filtered_preds = predictions[signal_mask]
    filtered_labels = labels[signal_mask]
    filtered_prices = prices.loc[signal_mask.index[signal_mask]] if len(filtered_preds) > 0 else prices.iloc[:0]

    # 过滤后方向准确率
    if len(filtered_preds) > 0:
        filtered_actual_dir = np.sign(filtered_labels)
        filtered_pred_dir = np.sign(filtered_preds)
        valid_f = (filtered_actual_dir != 0) & (filtered_pred_dir != 0)
        if valid_f.sum() > 0:
            direction_accuracy_filtered = (
                filtered_pred_dir[valid_f] == filtered_actual_dir[valid_f]
            ).mean()
        else:
            direction_accuracy_filtered = 0.5
    else:
        direction_accuracy_filtered = 0.5

    # ---- 策略收益计算 ----
    # 使用实际的 N 期收益率（labels 就是未来 N 期收益率）
    # 但我们需要考虑 hold_period：每 hold_period 根 K 线才换仓一次

    # 构建持仓信号（每 hold_period 期换仓一次）
    filtered_indices = filtered_preds.index
    if len(filtered_indices) == 0:
        return {
            "direction_accuracy": direction_accuracy,
            "direction_accuracy_filtered": direction_accuracy_filtered,
            "total_signals": len(predictions),
            "filtered_signals": 0,
            "trade_ratio": 0.0,
            "sign_metrics": calculate_metrics(pd.Series(dtype=float), freq_hours),
            "weighted_metrics": calculate_metrics(pd.Series(dtype=float), freq_hours),
        }

    # 逐期计算收益
    close_prices = prices["$close"]
    all_indices = predictions.index

    # sign 策略：每个时间点的收益 = sign(pred) * 单期收益 - 交易成本
    single_period_returns = close_prices.pct_change().shift(-1)  # 下一期收益

    # 构建持仓：每 hold_period 期检查一次信号
    positions_sign = pd.Series(0.0, index=all_indices)
    positions_weighted = pd.Series(0.0, index=all_indices)

    # 信号强度归一化（用于 weighted 策略）
    if abs_pred.max() > 0:
        normalized_strength = abs_pred / abs_pred.max()
    else:
        normalized_strength = abs_pred * 0

    # 每 hold_period 期决策一次
    decision_points = list(range(0, len(all_indices), hold_period))
    prev_sign_pos = 0.0
    prev_weighted_pos = 0.0

    for dp_idx in decision_points:
        idx = all_indices[dp_idx]

        if idx in filtered_indices:
            # 有过滤后的信号
            sign_pos = float(np.sign(predictions.loc[idx]))
            weight_pos = float(np.sign(predictions.loc[idx])) * float(normalized_strength.loc[idx])
        else:
            # 无信号，平仓
            sign_pos = 0.0
            weight_pos = 0.0

        # 填充从当前决策点到下一个决策点
        end_dp_idx = min(dp_idx + hold_period, len(all_indices))
        for fill_idx in range(dp_idx, end_dp_idx):
            positions_sign.iloc[fill_idx] = sign_pos
            positions_weighted.iloc[fill_idx] = weight_pos

        prev_sign_pos = sign_pos
        prev_weighted_pos = weight_pos

    # 计算策略收益
    single_ret = close_prices.reindex(all_indices).pct_change().shift(-1)
    single_ret = single_ret.iloc[:-1]  # 去掉最后一个（无法获得未来收益）

    # sign 策略收益
    sign_returns = positions_sign.iloc[:-1] * single_ret

    # 换仓时扣除交易成本（双边）
    sign_position_changes = positions_sign.diff().abs()
    sign_cost = sign_position_changes.iloc[:-1] * FEE_RATE * 2
    sign_returns = sign_returns - sign_cost

    # weighted 策略收益
    weighted_returns = positions_weighted.iloc[:-1] * single_ret
    weighted_position_changes = positions_weighted.diff().abs()
    weighted_cost = weighted_position_changes.iloc[:-1] * FEE_RATE * 2
    weighted_returns = weighted_returns - weighted_cost

    # 去除 NaN
    sign_returns = sign_returns.dropna()
    weighted_returns = weighted_returns.dropna()

    return {
        "direction_accuracy": direction_accuracy,
        "direction_accuracy_filtered": direction_accuracy_filtered,
        "total_signals": len(predictions),
        "filtered_signals": len(filtered_preds),
        "trade_ratio": len(filtered_preds) / len(predictions) if len(predictions) > 0 else 0,
        "sign_metrics": calculate_metrics(sign_returns, freq_hours),
        "weighted_metrics": calculate_metrics(weighted_returns, freq_hours),
        "threshold_value": threshold,
    }


# ============================================================
# IC / Rank IC 计算
# ============================================================

def calculate_ic_metrics(predictions: pd.Series, labels: pd.Series) -> dict:
    """计算 IC 和 Rank IC"""
    common_idx = predictions.index.intersection(labels.dropna().index)
    if len(common_idx) < 10:
        return {"IC": 0.0, "Rank_IC": 0.0}

    p = predictions.loc[common_idx]
    l = labels.loc[common_idx]

    ic = p.corr(l)
    rank_ic = p.rank().corr(l.rank())

    return {
        "IC": ic if not np.isnan(ic) else 0.0,
        "Rank_IC": rank_ic if not np.isnan(rank_ic) else 0.0,
    }


# ============================================================
# 主流程
# ============================================================

def evaluate_symbol_config(symbol: str, config: dict) -> list[BacktestResult]:
    """评估单个币种的单个配置"""
    freq = config["freq"]
    label_periods = config["label_periods"]
    top_k = config["top_k"]
    tag = config["tag"]
    freq_hours = int(freq.replace("h", ""))

    print(f"\n{'='*60}")
    print(f"  {symbol} | 配置: {tag} (频率={freq}, 标签期数={label_periods}, top_k={top_k})")
    print(f"{'='*60}")

    # 加载数据
    df = load_data(symbol, freq)
    print(f"  数据: {len(df)} 条 K线 ({df.index[0].strftime('%Y-%m-%d')} ~ {df.index[-1].strftime('%Y-%m-%d')})")

    # Walk-forward 训练和预测
    predictions, labels, prices, best_model, eval_metrics = walk_forward_train_predict(
        df, label_periods, top_k
    )

    # IC 指标
    ic_metrics = calculate_ic_metrics(predictions, labels)
    print(f"    测试集 IC={ic_metrics['IC']:.4f}, Rank_IC={ic_metrics['Rank_IC']:.4f}")

    # 遍历所有参数组合
    results = []
    for hold_period in HOLD_PERIODS:
        for tq in THRESHOLD_QUANTILES:
            bt = run_backtest(predictions, labels, prices, hold_period, tq, freq_hours)

            result = BacktestResult(
                symbol=symbol,
                config_tag=tag,
                hold_period=hold_period,
                threshold_quantile=tq,
                threshold_value=bt.get("threshold_value", 0),
                direction_accuracy=bt["direction_accuracy"],
                direction_accuracy_filtered=bt["direction_accuracy_filtered"],
                total_signals=bt["total_signals"],
                filtered_signals=bt["filtered_signals"],
                trade_ratio=bt["trade_ratio"],
                sign_total_return=bt["sign_metrics"]["total_return"],
                sign_annual_return=bt["sign_metrics"]["annual_return"],
                sign_sharpe=bt["sign_metrics"]["sharpe"],
                sign_max_drawdown=bt["sign_metrics"]["max_drawdown"],
                weighted_total_return=bt["weighted_metrics"]["total_return"],
                weighted_annual_return=bt["weighted_metrics"]["annual_return"],
                weighted_sharpe=bt["weighted_metrics"]["sharpe"],
                weighted_max_drawdown=bt["weighted_metrics"]["max_drawdown"],
                best_model=best_model,
                ic=ic_metrics["IC"],
                rank_ic=ic_metrics["Rank_IC"],
            )
            results.append(result)

    return results


def print_results_table(results: list[BacktestResult]):
    """打印结果汇总表"""
    print(f"\n{'='*120}")
    print("  回测结果汇总")
    print(f"{'='*120}")

    # 按币种和配置分组
    groups = {}
    for r in results:
        key = f"{r.symbol}_{r.config_tag}"
        if key not in groups:
            groups[key] = []
        groups[key].append(r)

    for group_key, group_results in groups.items():
        symbol = group_results[0].symbol
        tag = group_results[0].config_tag
        ic = group_results[0].ic
        rank_ic = group_results[0].rank_ic
        best_model = group_results[0].best_model

        print(f"\n--- {symbol} | {tag} | 模型={best_model} | IC={ic:.4f} | Rank_IC={rank_ic:.4f} ---")
        print(f"{'持仓期':>6} {'阈值Q':>6} {'阈值':>8} {'方向准确':>8} {'过滤准确':>8} "
              f"{'交易比':>6} {'信号数':>6} "
              f"{'sign收益':>10} {'sign夏普':>8} {'sign回撤':>8} "
              f"{'wt收益':>10} {'wt夏普':>8} {'wt回撤':>8}")
        print("-" * 120)

        for r in sorted(group_results, key=lambda x: (x.hold_period, x.threshold_quantile)):
            print(
                f"{r.hold_period:>6d} "
                f"{r.threshold_quantile:>6.1f} "
                f"{r.threshold_value:>8.4f} "
                f"{r.direction_accuracy:>7.1%} "
                f"{r.direction_accuracy_filtered:>7.1%} "
                f"{r.trade_ratio:>5.1%} "
                f"{r.filtered_signals:>6d} "
                f"{r.sign_total_return:>9.1%} "
                f"{r.sign_sharpe:>8.2f} "
                f"{r.sign_max_drawdown:>7.1%} "
                f"{r.weighted_total_return:>9.1%} "
                f"{r.weighted_sharpe:>8.2f} "
                f"{r.weighted_max_drawdown:>7.1%}"
            )


def print_best_configs(results: list[BacktestResult]):
    """打印每个币种的最佳配置"""
    print(f"\n{'='*80}")
    print("  各币种最佳配置（按夏普比率排序）")
    print(f"{'='*80}")

    # 按币种分组
    by_symbol = {}
    for r in results:
        if r.symbol not in by_symbol:
            by_symbol[r.symbol] = []
        by_symbol[r.symbol].append(r)

    for symbol, symbol_results in by_symbol.items():
        # 按 sign 策略夏普排序
        best_sign = sorted(symbol_results, key=lambda x: x.sign_sharpe, reverse=True)[:3]
        best_weighted = sorted(symbol_results, key=lambda x: x.weighted_sharpe, reverse=True)[:3]

        print(f"\n--- {symbol} ---")
        print(f"  [Sign 策略 Top3]")
        for i, r in enumerate(best_sign):
            print(
                f"    #{i+1}: {r.config_tag} | 持仓{r.hold_period}期 | 阈值Q{r.threshold_quantile} "
                f"| 方向准确={r.direction_accuracy_filtered:.1%} "
                f"| 收益={r.sign_total_return:.1%} | 夏普={r.sign_sharpe:.2f} "
                f"| 回撤={r.sign_max_drawdown:.1%}"
            )
        print(f"  [Weighted 策略 Top3]")
        for i, r in enumerate(best_weighted):
            print(
                f"    #{i+1}: {r.config_tag} | 持仓{r.hold_period}期 | 阈值Q{r.threshold_quantile} "
                f"| 方向准确={r.direction_accuracy_filtered:.1%} "
                f"| 收益={r.weighted_total_return:.1%} | 夏普={r.weighted_sharpe:.2f} "
                f"| 回撤={r.weighted_max_drawdown:.1%}"
            )


def print_direction_accuracy_summary(results: list[BacktestResult]):
    """打印方向准确率汇总（作为 LLM 辅助信号的核心指标）"""
    print(f"\n{'='*80}")
    print("  方向准确率汇总（LLM 辅助信号核心指标，>50% 即有价值）")
    print(f"{'='*80}")

    # 按币种+配置分组，取全量（无过滤）的方向准确率
    seen = set()
    for r in results:
        key = f"{r.symbol}_{r.config_tag}"
        if key in seen:
            continue
        seen.add(key)

        status = "有价值" if r.direction_accuracy > 0.5 else "无价值"
        arrow = ">>>" if r.direction_accuracy > 0.55 else ">>" if r.direction_accuracy > 0.5 else "  "
        print(f"  {arrow} {r.symbol:>4} | {r.config_tag:<12} | 全量方向准确率={r.direction_accuracy:.1%} | IC={r.ic:.4f} | {status}")

    # 过滤后的准确率（取 Q0.5 阈值、持仓1期的结果）
    print(f"\n  过滤后方向准确率（Q0.5 阈值，持仓1期）:")
    seen = set()
    for r in results:
        key = f"{r.symbol}_{r.config_tag}"
        if key in seen:
            continue
        if r.threshold_quantile == 0.5 and r.hold_period == 1:
            seen.add(key)
            improvement = r.direction_accuracy_filtered - r.direction_accuracy
            imp_str = f"+{improvement:.1%}" if improvement > 0 else f"{improvement:.1%}"
            print(f"      {r.symbol:>4} | {r.config_tag:<12} | 过滤后={r.direction_accuracy_filtered:.1%} (vs 全量{r.direction_accuracy:.1%}, 变化{imp_str})")


def main():
    print("=" * 80)
    print("  QLib 信号 Walk-Forward 回测评估")
    print("  目标：评估信号作为 LLM 决策辅助的有效性")
    print(f"  交易成本: {FEE_RATE*100:.2f}% 单边 | 阈值分位数: {THRESHOLD_QUANTILES}")
    print(f"  持仓周期: {HOLD_PERIODS} | 训练/验证/测试 = {TRAIN_RATIO}/{VALID_RATIO}/{1-TRAIN_RATIO-VALID_RATIO:.2f}")
    print("=" * 80)

    all_results = []

    for symbol, configs in SYMBOL_CONFIGS.items():
        for config in configs:
            try:
                results = evaluate_symbol_config(symbol, config)
                all_results.extend(results)
            except Exception as e:
                print(f"\n  错误: {symbol} {config['tag']} 评估失败: {e}")
                import traceback
                traceback.print_exc()

    if not all_results:
        print("\n没有成功的回测结果！")
        return

    # 打印汇总
    print_results_table(all_results)
    print_best_configs(all_results)
    print_direction_accuracy_summary(all_results)

    # 保存详细结果到 CSV
    rows = []
    for r in all_results:
        rows.append({
            "币种": r.symbol,
            "配置": r.config_tag,
            "持仓期": r.hold_period,
            "阈值分位": r.threshold_quantile,
            "阈值": round(r.threshold_value, 6),
            "方向准确率": round(r.direction_accuracy, 4),
            "过滤方向准确率": round(r.direction_accuracy_filtered, 4),
            "总信号": r.total_signals,
            "过滤信号": r.filtered_signals,
            "交易比例": round(r.trade_ratio, 4),
            "sign总收益": round(r.sign_total_return, 4),
            "sign年化": round(r.sign_annual_return, 4),
            "sign夏普": round(r.sign_sharpe, 4),
            "sign最大回撤": round(r.sign_max_drawdown, 4),
            "wt总收益": round(r.weighted_total_return, 4),
            "wt年化": round(r.weighted_annual_return, 4),
            "wt夏普": round(r.weighted_sharpe, 4),
            "wt最大回撤": round(r.weighted_max_drawdown, 4),
            "最优模型": r.best_model,
            "IC": round(r.ic, 4),
            "Rank_IC": round(r.rank_ic, 4),
        })

    df_results = pd.DataFrame(rows)
    output_path = "qlib_signal_evaluation_results.csv"
    df_results.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(f"\n详细结果已保存到: {output_path}")

    # 打印关键结论
    print(f"\n{'='*80}")
    print("  关键结论")
    print(f"{'='*80}")

    for symbol in SYMBOL_CONFIGS:
        symbol_results = [r for r in all_results if r.symbol == symbol]
        if not symbol_results:
            continue
        best_acc = max(r.direction_accuracy for r in symbol_results)
        best_filtered_acc = max(r.direction_accuracy_filtered for r in symbol_results)
        best_sharpe_sign = max(r.sign_sharpe for r in symbol_results)
        best_sharpe_wt = max(r.weighted_sharpe for r in symbol_results)

        print(f"\n  {symbol}:")
        print(f"    最高全量方向准确率: {best_acc:.1%}")
        print(f"    最高过滤方向准确率: {best_filtered_acc:.1%}")
        print(f"    最高 Sign 夏普: {best_sharpe_sign:.2f}")
        print(f"    最高 Weighted 夏普: {best_sharpe_wt:.2f}")
        if best_acc > 0.5:
            print(f"    >>> 方向准确率 > 50%，信号可作为 LLM 辅助输入")
        else:
            print(f"    方向准确率 <= 50%，信号质量不足")


if __name__ == "__main__":
    main()
