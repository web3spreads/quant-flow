"""
回测报告生成器
生成详细的回测报告，包括统计指标和可视化
"""

import csv
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from src.utils.logger import CustomJSONEncoder


class BacktestReportGenerator:
    """回测报告生成器"""

    def __init__(self, result: dict[str, Any]):
        """
        初始化报告生成器

        Args:
            result: 回测结果字典
        """
        self.result = result

    def print_summary(self):
        """打印回测摘要到控制台"""
        print("\n" + "=" * 60)
        print("📊 回测报告摘要")
        print("=" * 60)

        print("\n💰 账户信息:")
        print(f"   初始余额: ${self.result['initial_balance']:.2f}")
        print(f"   最终余额: ${self.result['final_balance']:.2f}")
        print(f"   总盈亏: ${self.result['total_pnl']:+.2f}")
        print(f"   总收益率: {self.result['total_return'] * 100:+.2f}%")
        print(f"   总手续费: ${self.result['total_fee']:.2f}")

        print("\n📈 交易统计:")
        print(f"   总交易数: {self.result['total_trades']}")
        print(f"   盈利交易: {self.result['profitable_trades']}")
        print(f"   亏损交易: {self.result['losing_trades']}")
        print(f"   胜率: {self.result['win_rate'] * 100:.2f}%")

        if self.result["total_trades"] > 0:
            print("\n💵 盈亏分析:")
            print(f"   平均盈利: ${self.result['avg_profit']:.2f}")
            print(f"   平均亏损: ${self.result['avg_loss']:.2f}")
            print(f"   盈亏比: {self.result['profit_factor']:.2f}")

        print("\n📉 风险指标:")
        print(f"   最大回撤: {self.result['max_drawdown'] * 100:.2f}%")

        print("\n" + "=" * 60)

    def save_json(self, file_path: str):
        """
        保存JSON格式的详细报告

        Args:
            file_path: 文件路径
        """
        self._write_json(file_path)

    def save_csv(self, file_path: str):
        """
        保存CSV格式的交易明细

        Args:
            file_path: 文件路径
        """
        if not self.result.get("trades"):
            print("⚠️ 没有交易记录，跳过CSV保存")
            return

        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        trades = self.result["trades"]

        # 准备CSV数据
        csv_data = []
        for trade in trades:
            row = {
                "symbol": trade.get("symbol", ""),
                "entry_time": trade.get("entry_time", ""),
                "exit_time": trade.get("exit_time", ""),
                "entry_price": trade.get("entry_price", 0),
                "exit_price": trade.get("exit_price", 0),
                "size": trade.get("size", 0),
                "leverage": trade.get("leverage", 1),
                "direction": "LONG" if trade.get("is_long", True) else "SHORT",
                "pnl": trade.get("pnl", 0),
                "fee": trade.get("fee", 0),
                "net_pnl": trade.get("net_pnl", 0),
                "return_pct": trade.get("return_pct", 0),
                "reason": trade.get("reason", ""),
            }

            # 转换datetime为字符串
            if isinstance(row["entry_time"], datetime):
                row["entry_time"] = row["entry_time"].isoformat()
            if isinstance(row["exit_time"], datetime):
                row["exit_time"] = row["exit_time"].isoformat()

            csv_data.append(row)

        # 写入CSV
        if csv_data:
            fieldnames = csv_data[0].keys()
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(csv_data)

            print(f"✅ CSV报告已保存: {file_path}")

    def save_partial(
        self, file_path: str, extra_data: dict[str, Any] | None = None, quiet: bool = False
    ):
        """
        以JSON格式保存实时报告快照

        Args:
            file_path: 文件路径
            extra_data: 需要合并到结果中的额外字段
            quiet: 如果为True，则不输出成功提示信息
        """
        data = self.result.copy()
        if extra_data:
            data.update(extra_data)

        success_message = None if quiet else f"📝 实时报告已刷新: {file_path}"
        self._write_json(file_path, data, success_message=success_message)

    def generate_full_report(
        self,
        output_dir: str = "backtest_results",
        symbol: str = "BTC",
        backtest_params: dict[str, Any] | None = None,
        config: Any | None = None,
    ):
        """
        生成完整报告（JSON + CSV + 配置文件夹）

        Args:
            output_dir: 输出目录
            symbol: 交易对符号
            backtest_params: 回测运行参数
            config: 配置对象
        """
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        # 如果输出目录已经是回测工作空间目录（包含 backtest_ 前缀且包含 symbol），直接使用
        # 否则创建新的报告子目录
        is_workspace = (
            output_path.name.startswith("backtest_") and symbol.upper() in output_path.name.upper()
        )
        if is_workspace:
            report_dir = output_path
        else:
            # 生成文件名（带时间戳）
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            report_dir = output_path / f"backtest_{symbol}_{timestamp}"
            report_dir.mkdir(parents=True, exist_ok=True)

        json_file = report_dir / "report.json"
        csv_file = report_dir / "trades.csv"
        pnl_file = report_dir / "pnl_history.csv"

        # 保存报告
        self.save_json(str(json_file))
        self.save_csv(str(csv_file))

        # 生成盈亏历史记录（用于图表展示）
        self._save_pnl_history(str(pnl_file))

        # 保存回测参数和配置信息
        if backtest_params or config:
            self._save_backtest_metadata(report_dir, backtest_params, config)

        # 打印摘要
        self.print_summary()

        return {
            "report_dir": str(report_dir),
            "json_file": str(json_file),
            "csv_file": str(csv_file),
            "pnl_file": str(pnl_file),
        }

    def _write_json(
        self, file_path: str, data: dict[str, Any] | None = None, success_message: str | None = None
    ):
        """
        写入JSON文件，自动处理datetime
        """
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        json_data = (data or self.result).copy()

        # 转换交易记录中的datetime
        if "trades" in json_data:
            trades = []
            for trade in json_data["trades"]:
                trade_copy = trade.copy()
                if "entry_time" in trade_copy and isinstance(trade_copy["entry_time"], datetime):
                    trade_copy["entry_time"] = trade_copy["entry_time"].isoformat()
                if "exit_time" in trade_copy and isinstance(trade_copy["exit_time"], datetime):
                    trade_copy["exit_time"] = trade_copy["exit_time"].isoformat()
                trades.append(trade_copy)
            json_data["trades"] = trades

        with open(path, "w", encoding="utf-8") as f:
            # 使用自定义编码器处理 LangChain 消息、numpy 等特殊类型，避免序列化报错
            json.dump(json_data, f, indent=2, ensure_ascii=False, cls=CustomJSONEncoder)

        message = success_message or f"✅ JSON报告已保存: {file_path}"
        print(message)

    def _save_pnl_history(self, file_path: str):
        """
        保存盈亏历史记录（用于图表展示）
        每笔交易记录：时间、盈亏金额、累计盈亏

        Args:
            file_path: 文件路径
        """
        if not self.result.get("trades"):
            return

        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        trades = self.result["trades"]
        cumulative_pnl = self.result["initial_balance"]

        # 按时间排序
        def get_exit_time(trade):
            exit_time = trade.get("exit_time")
            if exit_time is None:
                return datetime.now()
            if isinstance(exit_time, datetime):
                return exit_time
            if isinstance(exit_time, str):
                try:
                    return datetime.fromisoformat(exit_time.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    return datetime.now()
            return datetime.now()

        sorted_trades = sorted(trades, key=get_exit_time)

        csv_data = []
        for trade in sorted_trades:
            exit_time = trade.get("exit_time", "")
            if isinstance(exit_time, datetime):
                exit_time = exit_time.isoformat()

            net_pnl = trade.get("net_pnl", 0)
            cumulative_pnl += net_pnl

            row = {
                "timestamp": exit_time,
                "trade_id": len(csv_data) + 1,
                "pnl": net_pnl,
                "cumulative_pnl": cumulative_pnl,
                "is_profitable": "Yes" if net_pnl > 0 else "No",
                "symbol": trade.get("symbol", ""),
                "direction": "LONG" if trade.get("is_long", True) else "SHORT",
                "entry_price": trade.get("entry_price", 0),
                "exit_price": trade.get("exit_price", 0),
                "return_pct": trade.get("return_pct", 0),
            }
            csv_data.append(row)

        # 写入CSV
        if csv_data:
            fieldnames = csv_data[0].keys()
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(csv_data)

            print(f"✅ 盈亏历史记录已保存: {file_path}")

    def _save_backtest_metadata(
        self, report_dir: Path, backtest_params: dict[str, Any] | None, config: Any | None
    ):
        """
        保存回测元数据（参数、配置、模型信息等）

        Args:
            report_dir: 报告目录
            backtest_params: 回测参数
            config: 配置对象
        """
        metadata = {
            "backtest_timestamp": datetime.now().isoformat(),
            "backtest_params": backtest_params or {},
            "model_info": {},
            "config_info": {},
        }

        # 保存模型信息
        if config:
            metadata["model_info"] = {
                "openai_api_base": getattr(config, "openai_api_base", ""),
                "openai_model": getattr(config, "openai_model", ""),
                "prompt_set": getattr(config, "prompt_set", ""),
                "agent_temperature": getattr(config, "agent_temperature", 0),
                "agent_max_iterations": getattr(config, "agent_max_iterations", 0),
            }

            metadata["config_info"] = {
                "config_path": str(getattr(config, "config_path", "")),
                "env_file": getattr(config, "_env_file", ""),
                "max_trade_amount": getattr(config, "max_trade_amount", 0),
                "max_leverage": getattr(config, "max_leverage", 0),
                "take_profit_ratio": getattr(config, "take_profit_ratio", 0),
                "stop_loss_ratio": getattr(config, "stop_loss_ratio", 0),
                "timeframe": getattr(config, "timeframe", ""),
                "candles_limit": getattr(config, "candles_limit", 0),
            }

            # 保存配置文件副本（不包含敏感信息）
            if hasattr(config, "config_path"):
                config_path = Path(config.config_path)
                if config_path.exists():
                    config_copy = report_dir / "config.yaml"
                    try:
                        shutil.copy2(config_path, config_copy)
                        print(f"✅ 配置文件副本已保存: {config_copy}")
                    except Exception as e:
                        print(f"⚠️ 保存配置文件副本失败: {e}")
                else:
                    print(f"⚠️ 配置文件不存在: {config_path}，跳过保存副本")

        # 保存元数据JSON
        metadata_file = report_dir / "metadata.json"
        with open(metadata_file, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False, cls=CustomJSONEncoder)

        # 生成README文件
        self._generate_readme(report_dir, metadata)

        print(f"✅ 回测元数据已保存: {metadata_file}")

    def _generate_readme(self, report_dir: Path, metadata: dict[str, Any]):
        """
        生成报告README文件

        Args:
            report_dir: 报告目录
            metadata: 元数据字典
        """
        readme_content = f"""# 回测报告

## 报告信息

- **生成时间**: {metadata.get("backtest_timestamp", "")}
- **交易对**: {self.result.get("symbol", "N/A")}
- **初始余额**: ${self.result.get("initial_balance", 0):.2f}
- **最终余额**: ${self.result.get("final_balance", 0):.2f}
- **总盈亏**: ${self.result.get("total_pnl", 0):+.2f}
- **总收益率**: {self.result.get("total_return", 0) * 100:+.2f}%
- **胜率**: {self.result.get("win_rate", 0) * 100:.2f}%

## 文件说明

- `report.json`: 完整的回测结果（JSON格式）
- `trades.csv`: 所有交易的明细记录
- `pnl_history.csv`: 盈亏历史记录（按时间排序，包含累计盈亏，可用于图表展示）
- `metadata.json`: 回测参数、配置和模型信息
- `config.yaml`: 使用的配置文件副本
- `README.md`: 本文件

## 回测参数

"""

        params = metadata.get("backtest_params", {})
        for key, value in params.items():
            readme_content += f"- **{key}**: {value}\n"

        readme_content += "\n## 模型信息\n\n"
        model_info = metadata.get("model_info", {})
        for key, value in model_info.items():
            readme_content += f"- **{key}**: {value}\n"

        readme_content += "\n## 交易统计\n\n"
        readme_content += f"- **总交易数**: {self.result.get('total_trades', 0)}\n"
        readme_content += f"- **盈利交易**: {self.result.get('profitable_trades', 0)}\n"
        readme_content += f"- **亏损交易**: {self.result.get('losing_trades', 0)}\n"
        readme_content += f"- **平均盈利**: ${self.result.get('avg_profit', 0):.2f}\n"
        readme_content += f"- **平均亏损**: ${self.result.get('avg_loss', 0):.2f}\n"
        readme_content += f"- **盈亏比**: {self.result.get('profit_factor', 0):.2f}\n"
        readme_content += f"- **最大回撤**: {self.result.get('max_drawdown', 0) * 100:.2f}%\n"

        readme_file = report_dir / "README.md"
        with open(readme_file, "w", encoding="utf-8") as f:
            f.write(readme_content)

        print(f"✅ README文件已生成: {readme_file}")
