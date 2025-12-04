#!/usr/bin/env python3
"""
回测主程序
使用真实历史数据测试交易模型的成功率
"""

import argparse
import sys
import re
import json
from datetime import datetime
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
    parser.add_argument(
        '--live-report-interval',
        type=int,
        default=1,
        help='实时报告刷新频率（按决策点数量，默认: 每次刷新，设为0可禁用）'
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

    parser.add_argument(
        '--env-file',
        type=str,
        default=None,
        help='环境变量文件路径（默认: .env，可通过环境变量 DOTENV_PATH 覆盖）'
    )

    parser.add_argument(
        '--resume-from',
        type=str,
        default=None,
        help='从 live.json 文件恢复并继续回测（提供文件路径）'
    )

    return parser.parse_args()


def _build_live_report_filename(args) -> str:
    """基于输入参数构建唯一的实时报告文件名"""
    parts = [args.symbol.upper()]

    if args.data_file:
        parts.append(Path(args.data_file).stem or "data")
    elif args.start_date and args.end_date:
        parts.append(f"{args.start_date}_to_{args.end_date}")
    elif args.start_date:
        parts.append(args.start_date)
    else:
        parts.append("api")

    parts.append(args.timeframe)
    parts.append(f"interval{args.interval}m")

    raw_name = "_".join(parts)
    safe_name = re.sub(r'[^A-Za-z0-9._-]', '_', raw_name)
    return f"{safe_name}_live.json"


def _load_resume_info(resume_from_path: str) -> dict:
    """
    从 live.json 文件加载恢复信息
    
    Args:
        resume_from_path: live.json 文件路径
        
    Returns:
        包含恢复信息的字典
    """
    resume_path = Path(resume_from_path)
    if not resume_path.exists():
        raise FileNotFoundError(f"恢复文件不存在: {resume_from_path}")
    
    try:
        with open(resume_path, 'r', encoding='utf-8') as f:
            resume_data = json.load(f)
        
        # 提取关键信息
        info = {
            'symbol': resume_data.get('symbol'),
            'initial_balance': resume_data.get('initial_balance', 1000.0),
            'resume_file': str(resume_path),
            'progress': resume_data.get('progress', {}),
            'current_balance': resume_data.get('current_balance', {}),
            'trades': resume_data.get('trades', []),
            'open_positions': resume_data.get('open_positions', []),
            'last_decision': resume_data.get('last_decision')
        }
        
        # 从文件名推断 interval（如果可能）
        filename = resume_path.stem
        interval_match = re.search(r'interval(\d+)m', filename)
        if interval_match:
            info['interval'] = int(interval_match.group(1))
        else:
            info['interval'] = None
        
        return info
    except json.JSONDecodeError as e:
        raise ValueError(f"恢复文件格式错误: {e}")
    except Exception as e:
        raise ValueError(f"读取恢复文件失败: {e}")


def main():
    """主函数"""
    args = parse_args()

    try:
        # 如果提供了 --resume-from，从文件恢复信息
        resume_info = None
        if args.resume_from:
            print("📂 从恢复文件加载信息...")
            try:
                resume_info = _load_resume_info(args.resume_from)
                print(f"✅ 恢复信息加载成功")
                print(f"   交易对: {resume_info['symbol']}")
                print(f"   初始余额: ${resume_info['initial_balance']:.2f}")
                print(f"   已处理决策点: {resume_info['progress'].get('processed_decisions', 0)}/{resume_info['progress'].get('total_decisions', 0)}")
                
                # 使用恢复的信息覆盖参数
                if resume_info['symbol']:
                    args.symbol = resume_info['symbol']
                if resume_info['initial_balance']:
                    args.initial_balance = resume_info['initial_balance']
                if resume_info.get('interval'):
                    args.interval = resume_info['interval']
            except Exception as e:
                print(f"⚠️ 恢复文件加载失败: {e}")
                print("   将从头开始回测")
                resume_info = None

        # 加载配置
        print("📋 加载配置...")
        config = get_config(args.config, require_api_credentials=False, env_file=args.env_file)
        
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
            if not args.start_date or not args.end_date:
                print("❌ 使用API数据源时必须同时提供 --start-date 和 --end-date")
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
        live_report_file = None
        if args.live_report_interval != 0:
            live_report_file = Path(args.output_dir) / _build_live_report_filename(args)

        run_kwargs = {
            'decision_interval_minutes': args.interval
        }
        if live_report_file:
            run_kwargs.update({
                'live_report_path': str(live_report_file),
                'live_report_interval': max(1, args.live_report_interval)
            })
        
        # 如果提供了恢复信息，传递给引擎
        if resume_info:
            run_kwargs['resume_from'] = resume_info

        result = engine.run(**run_kwargs)

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
