#!/usr/bin/env python3
"""
QLib 历史数据回填工具

独立脚本，用于从 Hyperliquid API 批量拉取历史 K 线数据并灌入 QLib 数据目录。
与实盘程序的数据存储格式完全一致（parquet），支持自动去重合并。

使用方式:
  # 回填最近 90 天数据（默认）
  uv run python backfill_qlib_data.py

  # 回填指定天数
  uv run python backfill_qlib_data.py --days 180

  # 回填指定日期范围
  uv run python backfill_qlib_data.py --start-date 2025-09-01 --end-date 2026-03-06

  # 指定交易对和频率
  uv run python backfill_qlib_data.py --symbols BTC ETH SOL --freq 1h

  # 只预览不写入
  uv run python backfill_qlib_data.py --dry-run

注意:
  - 本脚本只负责灌入历史数据，不触发模型训练
  - 训练仍按原有节奏（retrain_interval_hours）自动执行
  - API 调用频率已做限制（默认每次请求间隔 2 秒），避免被封禁
  - 数据合并时按 timestamp 去重并排序，不会产生重复
"""

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from hyperliquid.info import Info
from hyperliquid.utils import constants

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("QLibBackfill")

# 每次 API 请求获取的最大 K 线数量
MAX_CANDLES_PER_REQUEST = 500

# 频率到分钟数的映射
FREQ_MINUTES = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "1h": 60,
    "4h": 240,
    "1d": 1440,
}


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="QLib 历史数据回填工具 - 从 Hyperliquid API 批量拉取历史数据",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=["BTC", "ETH", "SOL"],
        help="交易对列表（默认: BTC ETH SOL）",
    )
    parser.add_argument(
        "--freq",
        default="1h",
        choices=["1m", "5m", "15m", "1h", "4h", "1d"],
        help="K 线频率（默认: 1h）",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=90,
        help="回填最近多少天的数据（默认: 90 天，约 2160 条 1h 数据）",
    )
    parser.add_argument(
        "--start-date",
        type=str,
        default=None,
        help="开始日期（格式: YYYY-MM-DD），优先于 --days",
    )
    parser.add_argument(
        "--end-date",
        type=str,
        default=None,
        help="结束日期（格式: YYYY-MM-DD），默认为当前时间",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default="data/qlib",
        help="数据存储目录（默认: data/qlib）",
    )
    parser.add_argument(
        "--request-interval",
        type=float,
        default=2.0,
        help="API 请求间隔秒数（默认: 2.0 秒），避免被限流",
    )
    parser.add_argument(
        "--testnet",
        action="store_true",
        help="使用测试网（默认: 主网）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只预览不写入",
    )
    return parser.parse_args()


def fetch_candles_batch(
    info: Info,
    symbol: str,
    freq: str,
    start_ms: int,
    end_ms: int,
    request_interval: float,
) -> pd.DataFrame:
    """
    分批获取历史 K 线数据

    Hyperliquid API 单次最多返回约 500 条数据，
    对于较长的时间范围需要分段请求。

    Args:
        info: Hyperliquid Info 实例
        symbol: 交易对
        freq: K 线频率
        start_ms: 开始时间戳（毫秒）
        end_ms: 结束时间戳（毫秒）
        request_interval: 请求间隔秒数

    Returns:
        合并后的 DataFrame
    """
    freq_minutes = FREQ_MINUTES.get(freq, 60)
    candle_duration_ms = freq_minutes * 60 * 1000
    # 每批覆盖的时间范围
    batch_duration_ms = MAX_CANDLES_PER_REQUEST * candle_duration_ms

    all_candles = []
    current_start = start_ms
    batch_num = 0
    total_batches = max(1, int((end_ms - start_ms) / batch_duration_ms) + 1)

    while current_start < end_ms:
        batch_num += 1
        current_end = min(current_start + batch_duration_ms, end_ms)

        start_dt = datetime.fromtimestamp(current_start / 1000)
        end_dt = datetime.fromtimestamp(current_end / 1000)
        logger.info(
            f"  [{batch_num}/{total_batches}] 请求 {symbol} "
            f"{start_dt.strftime('%Y-%m-%d %H:%M')} ~ "
            f"{end_dt.strftime('%Y-%m-%d %H:%M')}"
        )

        for attempt in range(2):  # 0: 首次尝试, 1: 重试
            try:
                candles = info.candles_snapshot(
                    name=symbol,
                    interval=freq,
                    startTime=current_start,
                    endTime=current_end,
                )
                if candles:
                    all_candles.extend(candles)
                    if attempt == 0:
                        logger.info(f"    获取 {len(candles)} 条 K 线")
                    else:
                        logger.info(f"    重试成功，获取 {len(candles)} 条")
                else:
                    logger.warning("    该时间段无数据")
                break  # 成功则跳出重试循环
            except Exception as e:
                if attempt == 0:
                    logger.error(f"    请求失败: {e}，等待后重试...")
                    time.sleep(request_interval * 3)
                else:
                    logger.error(f"    重试也失败: {e}，跳过此时间段")

        current_start = current_end

        # 限制请求频率
        if current_start < end_ms:
            time.sleep(request_interval)

    if not all_candles:
        return pd.DataFrame()

    # 转换为 DataFrame
    df = pd.DataFrame(all_candles)
    column_mapping = {
        "t": "timestamp",
        "o": "open",
        "h": "high",
        "l": "low",
        "c": "close",
        "v": "volume",
    }
    df = df[[col for col in column_mapping if col in df.columns]]
    df = df.rename(columns=column_mapping)

    # 转换数据类型
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # 去重排序
    df = df.drop_duplicates(subset=["timestamp"], keep="last")
    df = df.sort_values("timestamp").reset_index(drop=True)

    return df


def merge_and_save(
    new_df: pd.DataFrame,
    symbol: str,
    freq: str,
    data_dir: Path,
    dry_run: bool = False,
) -> int:
    """
    将新数据与本地已有数据合并保存

    合并策略：
    1. 加载本地已有的 parquet 文件
    2. 拼接新旧数据
    3. 按 timestamp 去重（保留最新的记录）
    4. 排序后保存

    Args:
        new_df: 新获取的数据
        symbol: 交易对
        freq: 频率
        data_dir: 数据目录
        dry_run: 是否只预览

    Returns:
        合并后的总数据量
    """
    file_path = data_dir / f"{symbol}_{freq}.parquet"

    # 加载已有数据
    local_df = pd.DataFrame()
    if file_path.exists():
        try:
            local_df = pd.read_parquet(file_path)
            logger.info(f"  本地已有数据: {len(local_df)} 条")
        except Exception as e:
            logger.warning(f"  读取本地数据失败: {e}，将创建新文件")

    # 合并
    if not local_df.empty:
        merged = pd.concat([local_df, new_df], ignore_index=True)
    else:
        merged = new_df.copy()

    # 去重排序
    merged = merged.drop_duplicates(subset=["timestamp"], keep="last")
    merged = merged.sort_values("timestamp").reset_index(drop=True)

    incremental = len(merged) - len(local_df)
    logger.info(
        f"  合并结果: 新增 {incremental} 条，总计 {len(merged)} 条 "
        f"({merged['timestamp'].min()} ~ {merged['timestamp'].max()})"
    )

    if dry_run:
        logger.info("  [预览模式] 不写入文件")
    else:
        data_dir.mkdir(parents=True, exist_ok=True)
        merged.to_parquet(file_path, index=False)
        logger.info(f"  已保存到 {file_path}")

    return len(merged)


def main():
    """主函数"""
    args = parse_args()

    # 确定时间范围
    if args.end_date:
        end_time = datetime.strptime(args.end_date, "%Y-%m-%d")
    else:
        end_time = datetime.now()

    if args.start_date:
        start_time = datetime.strptime(args.start_date, "%Y-%m-%d")
    else:
        start_time = end_time - timedelta(days=args.days)

    if start_time >= end_time:
        logger.error("开始日期必须早于结束日期")
        sys.exit(1)

    # 计算预期数据量
    freq_minutes = FREQ_MINUTES.get(args.freq, 60)
    total_minutes = (end_time - start_time).total_seconds() / 60
    expected_candles = int(total_minutes / freq_minutes)

    logger.info("=" * 60)
    logger.info("QLib 历史数据回填工具")
    logger.info("=" * 60)
    logger.info(f"交易对: {', '.join(args.symbols)}")
    logger.info(f"K 线频率: {args.freq}")
    logger.info(f"时间范围: {start_time.strftime('%Y-%m-%d')} ~ {end_time.strftime('%Y-%m-%d')}")
    logger.info(f"预期数据量: 每个交易对约 {expected_candles} 条")
    logger.info(f"数据目录: {args.data_dir}")
    logger.info(f"请求间隔: {args.request_interval} 秒")
    logger.info(f"网络: {'测试网' if args.testnet else '主网'}")
    if args.dry_run:
        logger.info("[预览模式] 不会写入任何文件")
    logger.info("=" * 60)

    # 初始化 API
    base_url = constants.TESTNET_API_URL if args.testnet else constants.MAINNET_API_URL
    info = Info(base_url, skip_ws=True)

    start_ms = int(start_time.timestamp() * 1000)
    end_ms = int(end_time.timestamp() * 1000)

    data_dir = Path(args.data_dir)
    results = {}

    for i, symbol in enumerate(args.symbols):
        logger.info(f"\n{'─' * 40}")
        logger.info(f"[{i + 1}/{len(args.symbols)}] 处理 {symbol}")
        logger.info(f"{'─' * 40}")

        # 分批获取数据
        df = fetch_candles_batch(
            info=info,
            symbol=symbol,
            freq=args.freq,
            start_ms=start_ms,
            end_ms=end_ms,
            request_interval=args.request_interval,
        )

        if df.empty:
            logger.warning(f"{symbol}: 未获取到任何数据")
            results[symbol] = 0
            continue

        logger.info(f"{symbol}: 共获取 {len(df)} 条 K 线数据")

        # 合并保存
        total = merge_and_save(
            new_df=df,
            symbol=symbol,
            freq=args.freq,
            data_dir=data_dir,
            dry_run=args.dry_run,
        )
        results[symbol] = total

        # 交易对之间也保持间隔
        if i < len(args.symbols) - 1:
            time.sleep(args.request_interval)

    # 汇总
    logger.info(f"\n{'=' * 60}")
    logger.info("回填完成汇总")
    logger.info(f"{'=' * 60}")
    for symbol, total in results.items():
        status = f"{total} 条" if total > 0 else "失败"
        logger.info(f"  {symbol}: {status}")

    total_all = sum(results.values())
    logger.info(f"  合计: {total_all} 条")

    if total_all > 0 and not args.dry_run:
        logger.info(f"\n数据已保存到 {data_dir}/")
        logger.info("下次 QLib 训练时将自动使用全量本地数据（use_all_local=True）")
    elif total_all == 0:
        logger.error("所有交易对数据获取失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
