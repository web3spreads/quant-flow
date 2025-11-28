#!/usr/bin/env python3
"""
回测主程序
使用真实历史数据测试交易模型的成功率
"""

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

from src.config import get_config
from src.utils.logger import TradingLogger
from src.prompt_manager import PromptManager
from src.backtest import BacktestEngine, BacktestDataLoader, BacktestReportGenerator


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="使用历史数据回测交易模型",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 使用API获取历史数据回测
  python backtest.py --symbol BTC --start-date 2024-01-01 --end-date 2024-12-01

  # 使用本地数据文件回测
  python backtest.py --symbol BTC --data-file data/btc_history.csv

  # 指定初始余额和决策间隔
  python backtest.py --symbol ETH --start-date 2024-06-01 --end-date 2024-12-01 \\
      --initial-balance 10000 --interval 15
        """
    )

    # 数据源参数（互斥）
    data_group = parser.add_mutually_exclusive_group(required=True)
    data_group.add_argument(
        '--data-file',
        type=str,
        help='本地数据文件路径（CSV或JSON格式）'
    )
    data_group.add_argument(
        '--start-date',
        type=str,
        help='开始日期（格式: YYYY-MM-DD），与--end-date一起使用从API获取数据'
    )

    parser.add_argument(
        '--end-date',
        type=str,
        help='结束日期（格式: YYYY-MM-DD），与--start-date一起使用从API获取数据'
    )

    # 交易参数
    parser.add_argument(
        '--symbol',
        type=str,
        required=True,
        help='交易对符号（如 BTC, ETH）'
    )

    parser.add_argument(
        '--timeframe',
        type=str,
        default='15m',
        help='K线时间周期（默认: 15m）'
    )

    parser.add_argument(
        '--initial-balance',
        type=float,
        default=10000.0,
        help='初始余额（USD，默认: 10000）'
    )

    parser.add_argument(
        '--interval',
        type=int,
        default=15,
        help='决策间隔（分钟，默认: 15）'
    )

    # 输出参数
    parser.add_argument(
        '--output-dir',
        type=str,
        default='backtest_results',
        help='报告输出目录（默认: backtest_results）'
    )

    # 配置参数
    parser.add_argument(
        '--config',
        type=str,
        default='config.yaml',
        help='配置文件路径（默认: config.yaml）'
    )

    parser.add_argument(
        '--testnet',
        action='store_true',
        help='使用测试网（仅用于API数据源）'
    )

    return parser.parse_args()


def main():
    """主函数"""
    args = parse_args()

    try:
        # 加载配置
        print("📋 加载配置...")
        config = get_config(args.config)
        
        # 初始化日志
        logger = TradingLogger(
            log_level=config.log_level,
            console_color=config.console_color,
            decision_log_format=config.decision_log_format
        )

        # 初始化Prompt管理器
        try:
            prompt_manager = PromptManager(
                config_file=config.prompt_config_file,
                prompt_set=config.prompt_set
            )
        except Exception as e:
            logger.print_warning(f"Prompt管理器初始化失败: {e}")
            prompt_manager = None

        # 加载历史数据
        print("\n📥 加载历史数据...")
        data_loader = BacktestDataLoader(testnet=args.testnet)

        if args.data_file:
            # 从本地文件加载
            historical_data = data_loader.load_from_file(args.data_file, args.symbol)
        else:
            # 从API加载
            if not args.end_date:
                print("❌ 使用API数据源时必须提供 --end-date")
                sys.exit(1)

            start_date = datetime.strptime(args.start_date, "%Y-%m-%d")
            end_date = datetime.strptime(args.end_date, "%Y-%m-%d")
            
            if start_date >= end_date:
                print("❌ 开始日期必须早于结束日期")
                sys.exit(1)

            historical_data = data_loader.load_from_api(
                symbol=args.symbol,
                timeframe=args.timeframe,
                start_date=start_date,
                end_date=end_date
            )

        if historical_data is None or historical_data.empty:
            print("❌ 无法加载历史数据")
            sys.exit(1)

        print(f"✅ 数据加载完成: {len(historical_data)} 条K线")
        print(f"   时间范围: {historical_data['timestamp'].min()} 至 {historical_data['timestamp'].max()}")

        # 初始化回测引擎
        print("\n🔧 初始化回测引擎...")
        engine = BacktestEngine(
            symbol=args.symbol,
            historical_data=historical_data,
            initial_balance=args.initial_balance,
            config=config,
            logger=logger,
            prompt_manager=prompt_manager
        )

        # 运行回测
        print("\n🚀 开始回测...")
        result = engine.run(decision_interval_minutes=args.interval)

        # 生成报告
        print("\n📊 生成回测报告...")
        report_generator = BacktestReportGenerator(result)
        report_files = report_generator.generate_full_report(
            output_dir=args.output_dir,
            symbol=args.symbol
        )

        print(f"\n✅ 回测完成！")
        print(f"   报告文件:")
        print(f"   - JSON: {report_files['json_file']}")
        if report_files.get('csv_file'):
            print(f"   - CSV: {report_files['csv_file']}")

    except KeyboardInterrupt:
        print("\n\n⚠️ 用户中断回测")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 回测失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

