#!/usr/bin/env python3
"""
A/B 回测对比工具
对同一段历史数据，用不同策略配置跑回测，对比结果

用法:
    # 对比所有 P0-P2 配置
    uv run python backtest_comparison.py --symbol BTC --start-date 2025-01-01 --end-date 2025-06-01

    # 仅对比特定功能
    uv run python backtest_comparison.py --symbol BTC --compare debate

    # 使用本地数据文件
    uv run python backtest_comparison.py --symbol BTC --data-file data/BTC_4h.parquet
"""

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from src.backtest import BacktestDataLoader, BacktestEngine
from src.config import get_config
from src.prompt_manager import PromptManager
from src.utils.logger import CustomJSONEncoder, TradingLogger

# ---------------------------------------------------------------------------
# 数据结构定义
# ---------------------------------------------------------------------------


@dataclass
class ComparisonConfig:
    """单个对比配置"""

    name: str  # 配置名称（英文标识）
    description: str  # 配置描述（中文）
    enhanced_config: dict = field(default_factory=dict)  # 传给 BacktestEngine 的额外配置
    use_enhanced_agent: bool = False  # 是否使用增强版 Agent
    prompt_set: str | None = None  # 指定 prompt 策略集（None 表示使用默认）


@dataclass
class ComparisonResult:
    """对比回测结果"""

    group_name: str  # 对比组名称
    symbol: str  # 交易对
    configs: list[ComparisonConfig] = field(default_factory=list)
    results: list[dict] = field(default_factory=list)  # BacktestEngine.run() 的返回值列表
    run_timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


# ---------------------------------------------------------------------------
# 预定义对比组
# ---------------------------------------------------------------------------

COMPARISON_GROUPS: dict[str, list[ComparisonConfig]] = {
    "fincot": [
        ComparisonConfig(
            name="baseline",
            description="基线（无 FinCoT）",
            prompt_set="default",
            use_enhanced_agent=False,
        ),
        ComparisonConfig(
            name="fincot",
            description="FinCoT 6步推理",
            prompt_set="nof1-improved",
            use_enhanced_agent=False,
        ),
    ],
    "debate": [
        ComparisonConfig(
            name="no_debate",
            description="无辩论",
            enhanced_config={"debate": {"enabled": False}},
            use_enhanced_agent=True,
        ),
        ComparisonConfig(
            name="with_debate",
            description="多空辩论",
            enhanced_config={"debate": {"enabled": True}},
            use_enhanced_agent=True,
        ),
    ],
    "onchain": [
        ComparisonConfig(
            name="no_onchain",
            description="无链上数据",
            enhanced_config={"onchain": {"enabled": False}},
            use_enhanced_agent=False,
        ),
        ComparisonConfig(
            name="with_onchain",
            description="含链上数据",
            enhanced_config={"onchain": {"enabled": True}},
            use_enhanced_agent=False,
        ),
    ],
    "regime": [
        ComparisonConfig(
            name="fixed_params",
            description="固定参数",
            enhanced_config={"regime_adaptive": {"enabled": False}},
            use_enhanced_agent=True,
        ),
        ComparisonConfig(
            name="regime_adaptive",
            description="Regime 自适应",
            enhanced_config={"regime_adaptive": {"enabled": True}},
            use_enhanced_agent=True,
        ),
    ],
}

# "all" 组：合并所有对比组的配置（去重）
_all_configs: list[ComparisonConfig] = []
_seen_names: set[str] = set()
for _group_configs in COMPARISON_GROUPS.values():
    for _cfg in _group_configs:
        if _cfg.name not in _seen_names:
            _all_configs.append(_cfg)
            _seen_names.add(_cfg.name)
COMPARISON_GROUPS["all"] = _all_configs


# ---------------------------------------------------------------------------
# 数据加载
# ---------------------------------------------------------------------------


def load_historical_data(
    symbol: str,
    data_file: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    timeframe: str = "15m",
    testnet: bool = False,
) -> pd.DataFrame:
    """
    加载历史数据，支持 Parquet / CSV / JSON 文件以及 API

    Args:
        symbol: 交易对符号
        data_file: 本地数据文件路径
        start_date: 开始日期（YYYY-MM-DD）
        end_date: 结束日期（YYYY-MM-DD）
        timeframe: K线时间周期
        testnet: 是否使用测试网

    Returns:
        包含 timestamp/open/high/low/close/volume 列的 DataFrame
    """
    if data_file:
        path = Path(data_file)
        if not path.exists():
            print(f"数据文件不存在: {data_file}")
            sys.exit(1)

        print(f"从文件加载历史数据: {data_file}")

        # 支持 Parquet 格式
        if path.suffix.lower() == ".parquet":
            df = pd.read_parquet(data_file)
        elif path.suffix.lower() == ".csv":
            df = pd.read_csv(data_file)
        elif path.suffix.lower() == ".json":
            df = pd.read_json(data_file)
        else:
            print(f"不支持的文件格式: {path.suffix}")
            sys.exit(1)

        # 按时间范围过滤（如果指定了日期）
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            if start_date:
                df = df[df["timestamp"] >= pd.to_datetime(start_date)]
            if end_date:
                df = df[df["timestamp"] <= pd.to_datetime(end_date)]

        # 确保数值列类型正确
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        print(f"数据加载完成: {len(df)} 条K线")
        return df

    # 从 API 加载
    if not start_date or not end_date:
        print("使用 API 数据源时必须同时提供 --start-date 和 --end-date")
        sys.exit(1)

    loader = BacktestDataLoader(testnet=testnet)
    sd = datetime.strptime(start_date, "%Y-%m-%d")
    ed = datetime.strptime(end_date, "%Y-%m-%d")

    if sd >= ed:
        print("开始日期必须早于结束日期")
        sys.exit(1)

    df = loader.load_from_api(symbol=symbol, timeframe=timeframe, start_date=sd, end_date=ed)
    if df is None or df.empty:
        print("无法从 API 加载历史数据")
        sys.exit(1)

    return df


# ---------------------------------------------------------------------------
# 核心回测对比逻辑
# ---------------------------------------------------------------------------


def run_single_backtest(
    symbol: str,
    historical_data: pd.DataFrame,
    comp_config: ComparisonConfig,
    initial_balance: float,
    interval: int,
    base_config_path: str = "config.yaml",
    env_file: str | None = None,
) -> dict[str, Any]:
    """
    使用指定的 ComparisonConfig 运行单次回测

    Args:
        symbol: 交易对符号
        historical_data: 历史数据
        comp_config: 对比配置
        initial_balance: 初始余额
        interval: 决策间隔（分钟）
        base_config_path: 基础配置文件路径
        env_file: 环境变量文件路径

    Returns:
        BacktestEngine.run() 的结果字典
    """
    # 加载基础配置
    config = get_config(base_config_path, require_api_credentials=False, env_file=env_file)

    # 初始化日志（抑制冗余输出）
    logger = TradingLogger(
        log_level=config.log_level,
        console_color=config.console_color,
        decision_log_format=config.decision_log_format,
    )

    # 初始化 Prompt 管理器（可能使用不同的 prompt_set）
    prompt_set = comp_config.prompt_set or config.prompt_set
    try:
        prompt_manager = PromptManager(
            config_file=config.prompt_config_file,
            prompt_set=prompt_set,
        )
    except Exception as e:
        print(f"  Prompt 管理器初始化失败（{comp_config.name}）: {e}")
        prompt_manager = None

    # 初始化回测引擎（传入增强配置）
    engine = BacktestEngine(
        symbol=symbol,
        historical_data=historical_data,
        initial_balance=initial_balance,
        config=config,
        logger=logger,
        prompt_manager=prompt_manager,
        use_enhanced_agent=comp_config.use_enhanced_agent,
        enhanced_config=comp_config.enhanced_config,
    )

    # 运行回测
    result = engine.run(decision_interval_minutes=interval)

    return result


def run_comparison(
    symbol: str,
    historical_data: pd.DataFrame,
    group_name: str,
    initial_balance: float = 10000.0,
    interval: int = 60,
    base_config_path: str = "config.yaml",
    env_file: str | None = None,
) -> ComparisonResult:
    """
    对指定对比组执行 A/B 回测

    Args:
        symbol: 交易对符号
        historical_data: 历史数据
        group_name: 对比组名称
        initial_balance: 初始余额
        interval: 决策间隔（分钟）
        base_config_path: 基础配置文件路径
        env_file: 环境变量文件路径

    Returns:
        ComparisonResult 对比结果
    """
    if group_name not in COMPARISON_GROUPS:
        available = ", ".join(COMPARISON_GROUPS.keys())
        print(f"未知对比组: {group_name}，可用组: {available}")
        sys.exit(1)

    configs = COMPARISON_GROUPS[group_name]
    comparison = ComparisonResult(group_name=group_name, symbol=symbol, configs=list(configs))

    print(f"\n{'=' * 70}")
    print(f"开始 A/B 对比回测 — 对比组: {group_name}")
    print(f"交易对: {symbol} | 初始余额: ${initial_balance:.2f} | 决策间隔: {interval} 分钟")
    print(f"数据量: {len(historical_data)} 条K线")
    print(f"对比配置数: {len(configs)}")
    print(f"{'=' * 70}")

    for i, cfg in enumerate(configs, 1):
        print(f"\n{'─' * 50}")
        print(f"[{i}/{len(configs)}] 运行配置: {cfg.name} — {cfg.description}")
        print(f"{'─' * 50}")

        try:
            result = run_single_backtest(
                symbol=symbol,
                historical_data=historical_data,
                comp_config=cfg,
                initial_balance=initial_balance,
                interval=interval,
                base_config_path=base_config_path,
                env_file=env_file,
            )
            comparison.results.append(result)
            print(
                f"  配置 [{cfg.name}] 回测完成: "
                f"收益率={result.get('total_return', 0) * 100:+.2f}%, "
                f"胜率={result.get('win_rate', 0) * 100:.1f}%, "
                f"交易数={result.get('total_trades', 0)}"
            )
        except Exception as e:
            print(f"  配置 [{cfg.name}] 回测失败: {e}")
            # 记录失败结果，以 None 标记
            comparison.results.append({"error": str(e), "config_name": cfg.name})

    return comparison


# ---------------------------------------------------------------------------
# 结果展示
# ---------------------------------------------------------------------------

# 用于对比表格的关键指标
_METRICS = [
    ("total_return", "总收益率", lambda v: f"{v * 100:+.2f}%"),
    ("win_rate", "胜率", lambda v: f"{v * 100:.1f}%"),
    ("total_trades", "总交易数", lambda v: f"{v}"),
    ("max_drawdown", "最大回撤", lambda v: f"{v * 100:.2f}%"),
    ("profit_factor", "盈亏比", lambda v: f"{v:.2f}"),
    ("total_pnl", "总盈亏", lambda v: f"${v:+.2f}"),
    ("profitable_trades", "盈利笔数", lambda v: f"{v}"),
    ("losing_trades", "亏损笔数", lambda v: f"{v}"),
    ("avg_profit", "平均盈利", lambda v: f"${v:.2f}"),
    ("avg_loss", "平均亏损", lambda v: f"${v:.2f}"),
    ("total_fee", "总手续费", lambda v: f"${v:.2f}"),
]


def print_comparison_table(comparison: ComparisonResult) -> None:
    """
    以表格形式打印对比结果

    Args:
        comparison: 对比回测结果
    """
    configs = comparison.configs
    results = comparison.results

    if not configs or not results:
        print("无对比结果可显示")
        return

    print(f"\n{'=' * 80}")
    print(f"A/B 对比结果 — 组: {comparison.group_name} | 交易对: {comparison.symbol}")
    print(f"{'=' * 80}")

    # 计算列宽
    name_col_width = max(12, max(len(c.description) for c in configs) + 2)
    metric_col_width = 14

    # 表头
    header = f"{'指标':<{metric_col_width}}"
    for cfg in configs:
        header += f" | {cfg.description:^{name_col_width}}"
    print(header)
    print("-" * len(header))

    # 各指标行
    for key, label, fmt in _METRICS:
        row = f"{label:<{metric_col_width}}"
        for result in results:
            if "error" in result:
                row += f" | {'错误':^{name_col_width}}"
            else:
                value = result.get(key, 0)
                try:
                    formatted = fmt(value)
                except (TypeError, ValueError):
                    formatted = str(value)
                row += f" | {formatted:^{name_col_width}}"
        print(row)

    print(f"{'=' * 80}")

    # 简要结论
    _print_summary_conclusion(configs, results)


def _print_summary_conclusion(configs: list[ComparisonConfig], results: list[dict]) -> None:
    """打印简要结论，标注哪个配置在关键指标上更优"""
    # 找收益率最高的配置
    valid_pairs = [(cfg, res) for cfg, res in zip(configs, results) if "error" not in res]
    if len(valid_pairs) < 2:
        return

    print("\n简要结论:")

    # 比较总收益率
    best_return = max(valid_pairs, key=lambda p: p[1].get("total_return", 0))
    print(
        f"  最高收益率: {best_return[0].description} "
        f"({best_return[1].get('total_return', 0) * 100:+.2f}%)"
    )

    # 比较胜率
    best_wr = max(valid_pairs, key=lambda p: p[1].get("win_rate", 0))
    print(f"  最高胜率:   {best_wr[0].description} ({best_wr[1].get('win_rate', 0) * 100:.1f}%)")

    # 比较最大回撤（越小越好）
    best_dd = min(valid_pairs, key=lambda p: p[1].get("max_drawdown", 1))
    print(
        f"  最低回撤:   {best_dd[0].description} ({best_dd[1].get('max_drawdown', 0) * 100:.2f}%)"
    )

    # 比较盈亏比
    best_pf = max(valid_pairs, key=lambda p: p[1].get("profit_factor", 0))
    print(f"  最高盈亏比: {best_pf[0].description} ({best_pf[1].get('profit_factor', 0):.2f})")

    print()


# ---------------------------------------------------------------------------
# 报告保存
# ---------------------------------------------------------------------------


def save_comparison_report(comparison: ComparisonResult, output_dir: str) -> str:
    """
    保存 JSON 格式的对比报告

    Args:
        comparison: 对比结果
        output_dir: 输出目录

    Returns:
        报告文件路径
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = Path(output_dir) / f"comparison_{comparison.group_name}_{timestamp}"
    report_dir.mkdir(parents=True, exist_ok=True)

    # 构建报告数据
    report_data = {
        "group_name": comparison.group_name,
        "symbol": comparison.symbol,
        "run_timestamp": comparison.run_timestamp,
        "configs": [asdict(c) for c in comparison.configs],
        "results": [],
    }

    for cfg, result in zip(comparison.configs, comparison.results):
        entry: dict[str, Any] = {
            "config_name": cfg.name,
            "config_description": cfg.description,
        }
        if "error" in result:
            entry["error"] = result["error"]
        else:
            # 提取关键指标（排除 trades 详情以减小文件体积）
            for key, _, _ in _METRICS:
                entry[key] = result.get(key, 0)
            entry["initial_balance"] = result.get("initial_balance", 0)
            entry["final_balance"] = result.get("final_balance", 0)
        report_data["results"].append(entry)

    # 保存 JSON 报告
    report_file = report_dir / "comparison_report.json"
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2, ensure_ascii=False, cls=CustomJSONEncoder)
    print(f"对比报告已保存: {report_file}")

    # 同时保存各配置的完整回测结果
    for cfg, result in zip(comparison.configs, comparison.results):
        if "error" not in result:
            detail_file = report_dir / f"{cfg.name}_detail.json"
            with open(detail_file, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False, cls=CustomJSONEncoder)

    return str(report_dir)


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="A/B 回测对比工具 — 对比不同策略配置的回测结果",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 对比所有配置组
  uv run python backtest_comparison.py --symbol BTC --start-date 2025-01-01 --end-date 2025-06-01

  # 仅对比辩论功能
  uv run python backtest_comparison.py --symbol BTC --compare debate --data-file data/BTC_4h.parquet

  # 使用本地 Parquet 数据
  uv run python backtest_comparison.py --symbol BTC --data-file data/BTC_4h.parquet --compare regime
        """,
    )

    # 交易对
    parser.add_argument(
        "--symbol",
        type=str,
        default="BTC",
        help="交易对符号（默认: BTC）",
    )

    # 数据源
    parser.add_argument(
        "--data-file",
        type=str,
        default=None,
        help="本地数据文件路径（支持 Parquet/CSV/JSON）",
    )
    parser.add_argument(
        "--start-date",
        type=str,
        default=None,
        help="开始日期（格式: YYYY-MM-DD）",
    )
    parser.add_argument(
        "--end-date",
        type=str,
        default=None,
        help="结束日期（格式: YYYY-MM-DD）",
    )
    parser.add_argument(
        "--timeframe",
        type=str,
        default="15m",
        help="K线时间周期，仅 API 数据源有效（默认: 15m）",
    )

    # 对比组
    available_groups = ", ".join(COMPARISON_GROUPS.keys())
    parser.add_argument(
        "--compare",
        type=str,
        default="all",
        help=f"选择对比组（可选: {available_groups}，默认: all）",
    )

    # 回测参数
    parser.add_argument(
        "--initial-balance",
        type=float,
        default=10000.0,
        help="初始余额（USD，默认: 10000）",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="决策间隔（分钟，默认: 60）",
    )

    # 配置
    parser.add_argument(
        "--config",
        type=str,
        default="config.yaml",
        help="基础配置文件路径（默认: config.yaml）",
    )
    parser.add_argument(
        "--env-file",
        type=str,
        default=None,
        help="环境变量文件路径（默认: .env）",
    )

    # 输出
    parser.add_argument(
        "--output-dir",
        type=str,
        default="backtest_results",
        help="输出目录（默认: backtest_results）",
    )

    parser.add_argument(
        "--testnet",
        action="store_true",
        help="使用测试网（仅用于 API 数据源）",
    )

    return parser.parse_args()


def main() -> None:
    """主函数"""
    args = parse_args()

    # 参数校验
    if not args.data_file and not args.start_date:
        print("请提供 --data-file 或 --start-date/--end-date 指定数据源")
        sys.exit(1)

    try:
        # 1. 加载历史数据
        print("加载历史数据...")
        historical_data = load_historical_data(
            symbol=args.symbol,
            data_file=args.data_file,
            start_date=args.start_date,
            end_date=args.end_date,
            timeframe=args.timeframe,
            testnet=args.testnet,
        )

        if historical_data is None or historical_data.empty:
            print("历史数据为空，无法进行对比回测")
            sys.exit(1)

        print(f"数据就绪: {len(historical_data)} 条K线")
        if "timestamp" in historical_data.columns:
            print(
                f"时间范围: {historical_data['timestamp'].min()} 至 {historical_data['timestamp'].max()}"
            )

        # 2. 运行对比回测
        comparison = run_comparison(
            symbol=args.symbol,
            historical_data=historical_data,
            group_name=args.compare,
            initial_balance=args.initial_balance,
            interval=args.interval,
            base_config_path=args.config,
            env_file=args.env_file,
        )

        # 3. 打印对比表格
        print_comparison_table(comparison)

        # 4. 保存对比报告
        report_dir = save_comparison_report(comparison, args.output_dir)
        print(f"\n对比回测完成！报告目录: {report_dir}")

    except KeyboardInterrupt:
        print("\n\n用户中断对比回测")
        sys.exit(1)
    except Exception as e:
        print(f"\n对比回测失败: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
