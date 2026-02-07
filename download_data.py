#!/usr/bin/env python3
"""
Hyperliquid历史数据下载工具
用于拉取历史K线数据并保存到本地，方便回测使用
"""

import argparse
import sys
import json
from pathlib import Path
from datetime import datetime
import pandas as pd

from src.backtest.data_loader import BacktestDataLoader


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="从Hyperliquid API下载历史K线数据并保存到本地",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 下载单个交易对
  python download_data.py --symbol BTC --start-date 2025-11-01 --end-date 2025-12-01

  # 下载多个交易对
  python download_data.py --symbol BTC ETH --start-date 2025-11-01 --end-date 2025-12-01

  # 指定时间周期和输出格式
  python download_data.py --symbol BTC --start-date 2025-11-01 --end-date 2025-12-01 \\
      --timeframe 1h --format json

  # 指定输出目录
  python download_data.py --symbol BTC --start-date 2025-11-01 --end-date 2025-12-01 \\
      --output-dir data/historical
        """
    )

    parser.add_argument(
        '--symbol',
        type=str,
        nargs='+',
        required=True,
        help='交易对符号（如 BTC, ETH），支持多个'
    )

    parser.add_argument(
        '--start-date',
        type=str,
        required=True,
        help='开始日期（格式: YYYY-MM-DD）'
    )

    parser.add_argument(
        '--end-date',
        type=str,
        required=True,
        help='结束日期（格式: YYYY-MM-DD）'
    )

    parser.add_argument(
        '--timeframe',
        type=str,
        default='15m',
        help='K线时间周期（默认: 15m，可选: 1m, 5m, 15m, 1h, 4h, 1d）'
    )

    parser.add_argument(
        '--output-dir',
        type=str,
        default='data',
        help='输出目录（默认: data/）'
    )

    parser.add_argument(
        '--format',
        type=str,
        choices=['csv', 'json'],
        default='csv',
        help='输出格式（默认: csv）'
    )

    parser.add_argument(
        '--testnet',
        action='store_true',
        help='使用测试网'
    )

    parser.add_argument(
        '--overwrite',
        action='store_true',
        help='如果文件已存在则覆盖（默认: 跳过已存在的文件）'
    )

    return parser.parse_args()


def build_filename(symbol: str, timeframe: str, start_date: str, end_date: str, format: str) -> str:
    """
    构建输出文件名

    Args:
        symbol: 交易对符号
        timeframe: 时间周期
        start_date: 开始日期
        end_date: 结束日期
        format: 文件格式

    Returns:
        文件名
    """
    ext = 'csv' if format == 'csv' else 'json'
    return f"{symbol}_{timeframe}_{start_date}_to_{end_date}.{ext}"


def save_data(df: pd.DataFrame, file_path: Path, format: str):
    """
    保存数据到文件

    Args:
        df: 数据DataFrame
        file_path: 文件路径
        format: 文件格式（csv/json）
    """
    if format == 'csv':
        df.to_csv(file_path, index=False)
    else:  # json
        # 转换为字典列表格式
        data = df.to_dict('records')
        # 将timestamp转换为字符串
        for record in data:
            if isinstance(record['timestamp'], pd.Timestamp):
                record['timestamp'] = record['timestamp'].isoformat()

        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)


def download_symbol(
    loader: BacktestDataLoader,
    symbol: str,
    timeframe: str,
    start_date: datetime,
    end_date: datetime,
    output_dir: Path,
    format: str,
    overwrite: bool
) -> bool:
    """
    下载单个交易对的数据

    Args:
        loader: 数据加载器
        symbol: 交易对符号
        timeframe: 时间周期
        start_date: 开始日期
        end_date: 结束日期
        output_dir: 输出目录
        format: 文件格式
        overwrite: 是否覆盖已存在的文件

    Returns:
        是否成功
    """
    try:
        # 构建文件名
        start_date_str = start_date.strftime('%Y-%m-%d')
        end_date_str = end_date.strftime('%Y-%m-%d')
        filename = build_filename(symbol, timeframe, start_date_str, end_date_str, format)
        file_path = output_dir / filename

        # 检查文件是否已存在
        if file_path.exists() and not overwrite:
            print(f"⏭️  {symbol}: 文件已存在，跳过 ({filename})")
            return True

        # 下载数据
        print(f"\n📥 正在下载 {symbol} 数据...")
        df = loader.load_from_api(symbol, timeframe, start_date, end_date)

        if df is None or df.empty:
            print(f"❌ {symbol}: 下载失败或数据为空")
            return False

        # 保存文件
        save_data(df, file_path, format)

        # 显示统计信息
        print(f"✅ {symbol}: 下载完成")
        print(f"   文件: {filename}")
        print(f"   数据量: {len(df)} 条K线")
        print(f"   时间范围: {df['timestamp'].min()} 至 {df['timestamp'].max()}")
        print(f"   价格范围: ${df['low'].min():.2f} - ${df['high'].max():.2f}")

        return True

    except Exception as e:
        print(f"❌ {symbol}: 下载失败 - {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """主函数"""
    args = parse_args()

    # 解析日期
    try:
        start_date = datetime.strptime(args.start_date, "%Y-%m-%d")
        end_date = datetime.strptime(args.end_date, "%Y-%m-%d")
    except ValueError as e:
        print(f"❌ 日期格式错误: {e}")
        print("   请使用格式: YYYY-MM-DD (例如: 2025-11-01)")
        sys.exit(1)

    if start_date >= end_date:
        print("❌ 开始日期必须早于结束日期")
        sys.exit(1)

    # 创建输出目录
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"📁 输出目录: {output_dir.absolute()}")

    # 初始化数据加载器
    print(f"🔧 初始化数据加载器 ({'测试网' if args.testnet else '主网'})...")
    loader = BacktestDataLoader(testnet=args.testnet)

    # 下载每个交易对
    print(f"\n🚀 开始下载数据...")
    print(f"   交易对: {', '.join(args.symbol)}")
    print(f"   时间周期: {args.timeframe}")
    print(f"   时间范围: {args.start_date} 至 {args.end_date}")
    print(f"   输出格式: {args.format}")

    success_count = 0
    failed_symbols = []

    for symbol in args.symbol:
        success = download_symbol(
            loader=loader,
            symbol=symbol.upper(),
            timeframe=args.timeframe,
            start_date=start_date,
            end_date=end_date,
            output_dir=output_dir,
            format=args.format,
            overwrite=args.overwrite
        )

        if success:
            success_count += 1
        else:
            failed_symbols.append(symbol)

    # 显示总结
    print(f"\n{'='*60}")
    print(f"📊 下载完成")
    print(f"   成功: {success_count}/{len(args.symbol)}")
    if failed_symbols:
        print(f"   失败: {', '.join(failed_symbols)}")
    print(f"   输出目录: {output_dir.absolute()}")
    print(f"{'='*60}")

    if success_count == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
