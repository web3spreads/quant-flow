"""
日志和监控模块
提供结构化日志记录和美化的控制台输出
"""

import csv
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from rich import box
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table


class CustomJSONEncoder(json.JSONEncoder):
    """自定义 JSON 编码器，处理 pandas、numpy 和 LangChain 类型"""

    def default(self, obj):
        """
        转换特殊类型为可 JSON 序列化的格式

        Args:
            obj: 待序列化的对象

        Returns:
            可序列化的 Python 对象
        """
        # 处理 pandas Timestamp
        if isinstance(obj, pd.Timestamp):
            return obj.isoformat()

        # 处理 pandas NaT (Not a Time)
        if pd.isna(obj):
            return None

        # 处理 numpy 整数类型
        if isinstance(obj, (np.integer, np.int64, np.int32, np.int16, np.int8)):
            return int(obj)

        # 处理 numpy 浮点类型
        if isinstance(obj, (np.floating, np.float64, np.float32, np.float16)):
            return float(obj)

        # 处理 numpy 布尔类型
        if isinstance(obj, np.bool_):
            return bool(obj)

        # 处理 numpy 数组
        if isinstance(obj, np.ndarray):
            return obj.tolist()

        # 处理 datetime
        if isinstance(obj, datetime):
            return obj.isoformat()

        # 处理 LangChain 消息对象 (SystemMessage, HumanMessage, AIMessage, ToolMessage, 等)
        if hasattr(obj, 'content') and hasattr(obj, 'type'):
            return {
                'type': obj.type if hasattr(obj, 'type') else obj.__class__.__name__,
                'content': str(obj.content)[:500] if obj.content else ''  # 限制内容长度
            }

        # 其他情况调用父类方法
        return super().default(obj)


class TradingLogger:
    """交易日志记录器"""

    def __init__(
        self,
        log_level: str = "INFO",
        console_color: bool = True,
        decision_log_format: str = "json"
    ):
        """
        初始化日志记录器

        Args:
            log_level: 日志级别
            console_color: 是否启用彩色控制台输出
            decision_log_format: 决策日志格式 (json 或 csv)
        """
        self.console = Console() if console_color else Console(color_system=None)
        self.decision_log_format = decision_log_format

        # 设置标准日志
        self.logger = logging.getLogger("QuantFlow")
        self.logger.setLevel(getattr(logging, log_level.upper()))

        # 日志目录
        self.log_dir = Path("logs")
        self.decisions_dir = self.log_dir / "decisions"
        self.trades_dir = self.log_dir / "trades"

        # 确保目录存在
        self.decisions_dir.mkdir(parents=True, exist_ok=True)
        self.trades_dir.mkdir(parents=True, exist_ok=True)

        # 设置文件处理器
        self._setup_file_handlers()

    def _setup_file_handlers(self):
        """设置文件日志处理器"""
        # 主日志文件
        main_log_file = self.log_dir / f"trading_{datetime.now().strftime('%Y%m%d')}.log"
        file_handler = logging.FileHandler(main_log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)

        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)

    def print_header(self, text: str):
        """打印标题"""
        self.console.print()  # 空行
        self.console.rule(text, style="bold cyan")
        self.console.print()  # 空行

    def print_section(self, title: str, content: str = None, style: str = "bold yellow"):
        """打印章节"""
        self.console.print()  # 空行
        self.console.rule(title, style=style, align="left")
        if content:
            self.console.print(content)

    def print_market_data(self, symbol: str, data: dict[str, Any]):
        """
        打印市场数据

        Args:
            symbol: 交易对
            data: 市场数据字典
        """
        table = Table(title=f"📊 市场数据 - {symbol}", box=box.ROUNDED)
        table.add_column("指标", style="cyan", justify="left")
        table.add_column("值", style="green", justify="right")

        # 添加数据行
        for key, value in data.items():
            if isinstance(value, float):
                table.add_row(key, f"{value:.4f}")
            else:
                table.add_row(key, str(value))

        self.console.print(table)

    def print_prompt(self, prompt: str):
        """
        打印 AI Prompt

        Args:
            prompt: Prompt 内容
        """
        self.console.print(Panel(
            prompt,
            title="🤖 AI Agent Prompt",
            border_style="blue",
            padding=(1, 2),
            box=box.ROUNDED
        ))

    def print_agent_thought(self, thought: str):
        """
        打印 Agent 思考过程

        Args:
            thought: 思考内容
        """
        self.console.print(Panel(
            thought,
            title="💭 Agent 思考链",
            border_style="magenta",
            padding=(1, 2),
            box=box.ROUNDED
        ))

    def print_ai_response(self, response: str, title: str = "🤖 AI 回复"):
        """
        打印 AI 响应内容（支持 Markdown 渲染）

        Args:
            response: AI 响应内容（Markdown 格式）
            title: 面板标题
        """
        # 如果响应内容看起来像 Markdown，则渲染为 Markdown
        # 否则作为普通文本显示
        if self._is_likely_markdown(response):
            content = Markdown(response)
        else:
            content = response

        self.console.print(Panel(
            content,
            title=title,
            border_style="cyan",
            padding=(1, 2),
            box=box.DOUBLE
        ))

    def _is_likely_markdown(self, text: str) -> bool:
        """
        检测文本是否可能是 Markdown 格式

        Args:
            text: 待检测文本

        Returns:
            是否可能是 Markdown
        """
        # 简单检测：包含常见 Markdown 标记
        markdown_indicators = [
            '# ', '## ', '### ',  # 标题
            '- ', '* ', '+ ',     # 列表
            '```', '`',           # 代码
            '**', '__',           # 粗体
            '*', '_',             # 斜体
            '[', '](', '![',      # 链接和图片
            '>', '|'              # 引用和表格
        ]
        return any(indicator in text for indicator in markdown_indicators)

    def print_decision(self, decision: str, details: dict[str, Any] = None):
        """
        打印决策结果

        Args:
            decision: 决策类型 (BUY, SELL, DO_NOTHING)
            details: 决策详情
        """
        # 决策颜色映射
        color_map = {
            "BUY": "green",
            "SELL": "red",
            "DO_NOTHING": "yellow"
        }
        color = color_map.get(decision, "white")

        # 创建决策面板
        content = f"[bold {color}]决策: {decision}[/bold {color}]\n"

        if details:
            content += "\n详情:\n"
            for key, value in details.items():
                content += f"  • {key}: {value}\n"

        self.console.print(Panel(
            content,
            title="⚡ 决策结果",
            border_style=color,
            padding=(1, 2),
            box=box.HEAVY
        ))

    def print_execution_result(self, success: bool, message: str, order_id: str = None):
        """
        打印执行结果

        Args:
            success: 是否成功
            message: 结果消息
            order_id: 订单ID（如果有）
        """
        style = "green" if success else "red"
        icon = "✅" if success else "❌"

        content = f"{icon} {message}"
        if order_id:
            content += f"\n订单ID: {order_id}"

        self.console.print(Panel(
            content,
            title="📋 执行结果",
            border_style=style,
            padding=(1, 2),
            box=box.HEAVY
        ))

    def print_error(self, error: str):
        """
        打印错误信息

        Args:
            error: 错误内容
        """
        self.console.print(f"[bold red]❌ 错误: {error}[/bold red]")

    def print_info(self, message: str):
        """
        打印信息

        Args:
            message: 信息内容
        """
        self.console.print(f"[cyan]ℹ️  {message}[/cyan]")

    def print_warning(self, message: str):
        """
        打印警告

        Args:
            message: 警告内容
        """
        self.console.print(f"[yellow]⚠️  {message}[/yellow]")

    def log_decision(
        self,
        symbol: str,
        market_data: dict[str, Any],
        prompt: str,
        ai_response: str,
        decision: str,
        action_details: dict[str, Any] = None,
        status: str = "SUCCESS",
        error_message: str = None
    ):
        """
        记录决策日志到文件

        Args:
            symbol: 交易对
            market_data: 市场数据
            prompt: 发送给AI的Prompt
            ai_response: AI的原始回复
            decision: 决策类型
            action_details: 执行细节
            status: 执行状态
            error_message: 错误信息（如果有）
        """
        timestamp = datetime.now()
        log_entry = {
            "timestamp": timestamp.isoformat(),
            "symbol": symbol,
            "market_data": market_data,
            "prompt": prompt,
            "ai_response": ai_response,
            "decision": decision,
            "action_details": action_details or {},
            "status": status,
            "error_message": error_message
        }

        # 根据格式保存
        if self.decision_log_format == "json":
            self._save_decision_json(timestamp, log_entry)
        else:
            self._save_decision_csv(timestamp, log_entry)

    def _save_decision_json(self, timestamp: datetime, log_entry: dict[str, Any]):
        """保存决策日志为 JSON 格式"""
        filename = self.decisions_dir / f"decisions_{timestamp.strftime('%Y%m%d')}.jsonl"

        with open(filename, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False, cls=CustomJSONEncoder) + "\n")

    def _save_decision_csv(self, timestamp: datetime, log_entry: dict[str, Any]):
        """保存决策日志为 CSV 格式"""
        filename = self.decisions_dir / f"decisions_{timestamp.strftime('%Y%m%d')}.csv"

        file_exists = filename.exists()

        with open(filename, "a", newline="", encoding="utf-8") as f:
            fieldnames = [
                "timestamp", "symbol", "decision", "status",
                "current_price", "rsi", "error_message"
            ]
            writer = csv.DictWriter(f, fieldnames=fieldnames)

            if not file_exists:
                writer.writeheader()

            # 简化的 CSV 记录
            writer.writerow({
                "timestamp": log_entry["timestamp"],
                "symbol": log_entry["symbol"],
                "decision": log_entry["decision"],
                "status": log_entry["status"],
                "current_price": log_entry["market_data"].get("current_price", ""),
                "rsi": log_entry["market_data"].get("rsi", ""),
                "error_message": log_entry.get("error_message", "")
            })

    def log_trade(
        self,
        symbol: str,
        action: str,
        amount: float,
        price: float,
        order_id: str,
        take_profit_price: float = None,
        stop_loss_price: float = None,
        status: str = "FILLED",
        pnl: float = None
    ):
        """
        记录交易日志

        Args:
            symbol: 交易对
            action: 交易动作 (BUY/SELL)
            amount: 交易数量
            price: 交易价格
            order_id: 订单ID
            take_profit_price: 止盈价格
            stop_loss_price: 止损价格
            status: 订单状态
            pnl: 盈亏（如果是平仓）
        """
        timestamp = datetime.now()
        trade_entry = {
            "timestamp": timestamp.isoformat(),
            "symbol": symbol,
            "action": action,
            "amount": amount,
            "price": price,
            "order_id": order_id,
            "take_profit_price": take_profit_price,
            "stop_loss_price": stop_loss_price,
            "status": status,
            "pnl": pnl
        }

        # 保存为 JSON 格式
        filename = self.trades_dir / f"trades_{timestamp.strftime('%Y%m%d')}.jsonl"

        with open(filename, "a", encoding="utf-8") as f:
            f.write(json.dumps(trade_entry, ensure_ascii=False, cls=CustomJSONEncoder) + "\n")

        self.logger.info(f"交易记录: {action} {amount} {symbol} @ {price}")


# 全局日志实例
_logger: TradingLogger | None = None


def get_logger(
    log_level: str = "INFO",
    console_color: bool = True,
    decision_log_format: str = "json"
) -> TradingLogger:
    """
    获取全局日志实例（单例模式）

    Args:
        log_level: 日志级别
        console_color: 是否启用彩色输出
        decision_log_format: 决策日志格式

    Returns:
        TradingLogger 实例
    """
    global _logger
    if _logger is None:
        _logger = TradingLogger(log_level, console_color, decision_log_format)
    return _logger
