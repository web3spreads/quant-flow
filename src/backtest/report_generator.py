"""
回测报告生成器
生成详细的回测报告，包括统计指标和可视化
"""

import json
import csv
from typing import Dict, Any, Optional
from pathlib import Path
from datetime import datetime


class BacktestReportGenerator:
    """回测报告生成器"""

    def __init__(self, result: Dict[str, Any]):
        """
        初始化报告生成器
        
        Args:
            result: 回测结果字典
        """
        self.result = result

    def print_summary(self):
        """打印回测摘要到控制台"""
        print("\n" + "="*60)
        print("📊 回测报告摘要")
        print("="*60)
        
        print(f"\n💰 账户信息:")
        print(f"   初始余额: ${self.result['initial_balance']:.2f}")
        print(f"   最终余额: ${self.result['final_balance']:.2f}")
        print(f"   总盈亏: ${self.result['total_pnl']:+.2f}")
        print(f"   总收益率: {self.result['total_return']*100:+.2f}%")
        print(f"   总手续费: ${self.result['total_fee']:.2f}")
        
        print(f"\n📈 交易统计:")
        print(f"   总交易数: {self.result['total_trades']}")
        print(f"   盈利交易: {self.result['profitable_trades']}")
        print(f"   亏损交易: {self.result['losing_trades']}")
        print(f"   胜率: {self.result['win_rate']*100:.2f}%")
        
        if self.result['total_trades'] > 0:
            print(f"\n💵 盈亏分析:")
            print(f"   平均盈利: ${self.result['avg_profit']:.2f}")
            print(f"   平均亏损: ${self.result['avg_loss']:.2f}")
            print(f"   盈亏比: {self.result['profit_factor']:.2f}")
        
        print(f"\n📉 风险指标:")
        print(f"   最大回撤: {self.result['max_drawdown']*100:.2f}%")
        
        print("\n" + "="*60)

    def save_json(self, file_path: str):
        """
        保存JSON格式的详细报告
        
        Args:
            file_path: 文件路径
        """
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        
        # 准备JSON数据（处理datetime序列化）
        json_data = self.result.copy()
        
        # 转换交易记录中的datetime
        if 'trades' in json_data:
            trades = []
            for trade in json_data['trades']:
                trade_copy = trade.copy()
                if 'entry_time' in trade_copy and isinstance(trade_copy['entry_time'], datetime):
                    trade_copy['entry_time'] = trade_copy['entry_time'].isoformat()
                if 'exit_time' in trade_copy and isinstance(trade_copy['exit_time'], datetime):
                    trade_copy['exit_time'] = trade_copy['exit_time'].isoformat()
                trades.append(trade_copy)
            json_data['trades'] = trades
        
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, indent=2, ensure_ascii=False)
        
        print(f"✅ JSON报告已保存: {file_path}")

    def save_csv(self, file_path: str):
        """
        保存CSV格式的交易明细
        
        Args:
            file_path: 文件路径
        """
        if not self.result.get('trades'):
            print("⚠️ 没有交易记录，跳过CSV保存")
            return
        
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        
        trades = self.result['trades']
        
        # 准备CSV数据
        csv_data = []
        for trade in trades:
            row = {
                'symbol': trade.get('symbol', ''),
                'entry_time': trade.get('entry_time', ''),
                'exit_time': trade.get('exit_time', ''),
                'entry_price': trade.get('entry_price', 0),
                'exit_price': trade.get('exit_price', 0),
                'size': trade.get('size', 0),
                'leverage': trade.get('leverage', 1),
                'direction': 'LONG' if trade.get('is_long', True) else 'SHORT',
                'pnl': trade.get('pnl', 0),
                'fee': trade.get('fee', 0),
                'net_pnl': trade.get('net_pnl', 0),
                'return_pct': trade.get('return_pct', 0),
                'reason': trade.get('reason', '')
            }
            
            # 转换datetime为字符串
            if isinstance(row['entry_time'], datetime):
                row['entry_time'] = row['entry_time'].isoformat()
            if isinstance(row['exit_time'], datetime):
                row['exit_time'] = row['exit_time'].isoformat()
            
            csv_data.append(row)
        
        # 写入CSV
        if csv_data:
            fieldnames = csv_data[0].keys()
            with open(path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(csv_data)
            
            print(f"✅ CSV报告已保存: {file_path}")

    def generate_full_report(
        self,
        output_dir: str = "backtest_results",
        symbol: str = "BTC"
    ):
        """
        生成完整报告（JSON + CSV）
        
        Args:
            output_dir: 输出目录
            symbol: 交易对符号
        """
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        # 生成文件名（带时间戳）
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_file = output_path / f"backtest_{symbol}_{timestamp}.json"
        csv_file = output_path / f"backtest_{symbol}_{timestamp}.csv"
        
        # 保存报告
        self.save_json(str(json_file))
        self.save_csv(str(csv_file))
        
        # 打印摘要
        self.print_summary()
        
        return {
            'json_file': str(json_file),
            'csv_file': str(csv_file)
        }

