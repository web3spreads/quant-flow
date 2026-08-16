"""
日志工具：控制台 + 文件日志与结构化 JSONL 记录。

三类输出，各司其职：
- ``logs/main.log``            运行日志（人读，含控制台同步输出）
- ``logs/decisions/*.jsonl``   决策记录（含 prompt/回复/执行细节，事后审计）
- ``logs/trades/*.jsonl``      成交记录（含 pnl/reason 归因，用 jq/pandas 直接分析）
- ``logs/equity/*.jsonl``      净值快照（画净值曲线）

JSONL 写入失败只告警不抛出——日志永远不能拖垮交易主流程。
"""

import json
import logging
from datetime import datetime
from decimal import Decimal
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

_LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(message)s"

# main.log 轮转参数：单文件 50MB × 3 备份（线上曾累积 364MB 单文件无轮转）
_LOG_MAX_BYTES = 50 * 1024 * 1024
_LOG_BACKUP_COUNT = 3


def _json_default(value: Any) -> Any:
    """JSONL 序列化兜底：Decimal/日期/numpy 标量安全降级。"""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "item"):  # numpy 标量
        try:
            return value.item()
        except Exception:
            pass
    return str(value)


class TradingLogger:
    """交易日志器：运行日志 + 决策/成交/净值三路结构化记录。"""

    def __init__(self, log_level: str = "INFO", log_dir: str = "logs"):
        self.log_dir = Path(log_dir)
        self.decisions_dir = self.log_dir / "decisions"
        self.trades_dir = self.log_dir / "trades"
        self.equity_dir = self.log_dir / "equity"
        for d in (self.decisions_dir, self.trades_dir, self.equity_dir):
            d.mkdir(parents=True, exist_ok=True)

        self.logger = logging.getLogger("quantflow")
        self.logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
        if not self.logger.handlers:
            formatter = logging.Formatter(_LOG_FORMAT)
            console = logging.StreamHandler()
            console.setFormatter(formatter)
            file_handler = RotatingFileHandler(
                self.log_dir / "main.log",
                maxBytes=_LOG_MAX_BYTES,
                backupCount=_LOG_BACKUP_COUNT,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            self.logger.addHandler(console)
            self.logger.addHandler(file_handler)

    # ── 运行日志 ──────────────────────────────────────────────────────────

    def print_header(self, text: str) -> None:
        """周期级标题。"""
        self.logger.info("═" * 8 + " " + text)

    def print_section(self, title: str, content: str | None = None, style: str = "") -> None:
        """小节标题（style 参数仅为兼容旧调用，无渲染含义）。"""
        self.logger.info("── " + title)
        if content:
            self.logger.info(content)

    def print_info(self, message: str) -> None:
        self.logger.info(message)

    def print_warning(self, message: str) -> None:
        self.logger.warning(message)

    def print_error(self, message: str) -> None:
        self.logger.error(message)

    def print_market_data(self, symbol: str, data: dict[str, Any]) -> None:
        """行情摘要一行输出。"""
        try:
            self.logger.info(
                f"[{symbol}] 价格 {float(data.get('current_price', 0)):.4f} | "
                f"RSI {float(data.get('rsi', 0)):.1f} | "
                f"MACD {float(data.get('macd_hist', 0)):.5f} | "
                f"量变 {float(data.get('volume_change', 0)):.1f}%"
            )
        except (TypeError, ValueError):
            self.logger.info(f"[{symbol}] 行情: {data}")

    # ── 结构化记录 ────────────────────────────────────────────────────────

    def log_decision(
        self,
        symbol: str,
        market_data: dict[str, Any],
        prompt: str,
        ai_response: str,
        decision: str,
        action_details: dict[str, Any] | None = None,
        status: str = "SUCCESS",
        error_message: str | None = None,
        confidence: float = 0.0,
    ) -> None:
        """记录一次决策（含 prompt 与 AI 原始回复，供事后审计）。"""
        self._append_jsonl(
            self.decisions_dir / f"decisions_{datetime.now():%Y%m%d}.jsonl",
            {
                "timestamp": datetime.now().isoformat(),
                "symbol": symbol,
                "decision": decision,
                "confidence": confidence,
                "status": status,
                "error_message": error_message,
                "market_data": market_data,
                "prompt": prompt,
                "ai_response": ai_response,
                "action_details": action_details or {},
            },
        )

    def log_trade(
        self,
        symbol: str,
        action: str,
        amount: float,
        price: float,
        order_id: str,
        take_profit_price: float | None = None,
        stop_loss_price: float | None = None,
        status: str = "FILLED",
        pnl: float | None = None,
        reason: str | None = None,
    ) -> None:
        """记录一笔成交（reason 为盈亏归因标签，如 GRID_TP / Triple Barrier）。"""
        self._append_jsonl(
            self.trades_dir / f"trades_{datetime.now():%Y%m%d}.jsonl",
            {
                "timestamp": datetime.now().isoformat(),
                "symbol": symbol,
                "action": action,
                "amount": amount,
                "price": price,
                "order_id": order_id,
                "take_profit_price": take_profit_price,
                "stop_loss_price": stop_loss_price,
                "status": status,
                "pnl": pnl,
                "reason": reason,
            },
        )
        self.logger.info(f"交易记录: {action} {amount} {symbol} @ {price}")

    def log_equity_snapshot(
        self,
        equity: float,
        available: float,
        unrealized_pnl: float = 0.0,
        position_notional: float = 0.0,
        symbol: str = "",
    ) -> None:
        """记录净值快照（每周期一行，每天一个文件）。"""
        self._append_jsonl(
            self.equity_dir / f"equity_{datetime.now():%Y%m%d}.jsonl",
            {
                "timestamp": datetime.now().isoformat(),
                "equity": float(equity),
                "available": float(available),
                "unrealized_pnl": float(unrealized_pnl),
                "position_notional": float(position_notional),
                "symbol": symbol,
            },
        )

    def _append_jsonl(self, path: Path, entry: dict[str, Any]) -> None:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, default=_json_default) + "\n")
        except OSError as e:
            self.logger.warning(f"结构化日志写入失败 {path.name}: {e}")


_logger: TradingLogger | None = None


def get_logger(log_level: str = "INFO") -> TradingLogger:
    """获取全局日志实例（单例）。"""
    global _logger
    if _logger is None:
        _logger = TradingLogger(log_level)
    return _logger
