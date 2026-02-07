"""
账户保护管理器
实现最大回撤保护、最大持仓时间限制等风控机制
"""

import json
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any


class ProtectionAction(StrEnum):
    """保护动作类型"""
    NONE = "none"
    WARN = "warn"
    PAUSE_NEW_TRADES = "pause_new_trades"
    CLOSE_LOSING_POSITIONS = "close_losing_positions"
    CLOSE_ALL_POSITIONS = "close_all_positions"


@dataclass
class PositionRecord:
    """持仓记录"""
    symbol: str
    entry_time: datetime
    entry_price: float
    size: float
    is_long: bool
    leverage: int = 1
    max_profit_pct: float = 0.0  # 历史最高盈利百分比
    current_pnl_pct: float = 0.0

    def holding_hours(self) -> float:
        """持仓时长（小时）"""
        return (datetime.now() - self.entry_time).total_seconds() / 3600

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        data = asdict(self)
        data['entry_time'] = self.entry_time.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'PositionRecord':
        """从字典创建"""
        data['entry_time'] = datetime.fromisoformat(data['entry_time'])
        return cls(**data)


@dataclass
class AccountSnapshot:
    """账户快照"""
    timestamp: datetime
    balance: float
    equity: float  # 包含未实现盈亏的净值
    unrealized_pnl: float
    margin_used: float

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data['timestamp'] = self.timestamp.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'AccountSnapshot':
        data['timestamp'] = datetime.fromisoformat(data['timestamp'])
        return cls(**data)


class AccountProtector:
    """
    账户保护管理器

    功能：
    1. 最大回撤保护 - 净值回撤超过阈值时触发保护
    2. 最大持仓时间限制 - 持仓超时自动平仓
    3. 连续亏损保护 - 连续亏损次数过多时暂停交易
    4. 单日最大亏损保护 - 当日亏损达到阈值时停止交易
    """

    def __init__(
        self,
        max_drawdown_pct: float = 0.10,  # 最大回撤 10%
        max_daily_loss_pct: float = 0.05,  # 单日最大亏损 5%
        max_position_hours: float = 48.0,  # 最大持仓时间 48 小时
        max_consecutive_losses: int = 5,  # 最大连续亏损次数
        pause_hours_after_protection: float = 4.0,  # 触发保护后暂停时间
        data_dir: str | None = None,
        on_protection_triggered: Callable | None = None
    ):
        """
        初始化账户保护管理器

        Args:
            max_drawdown_pct: 最大回撤百分比（0.10 = 10%）
            max_daily_loss_pct: 单日最大亏损百分比
            max_position_hours: 最大持仓时间（小时）
            max_consecutive_losses: 最大连续亏损次数
            pause_hours_after_protection: 触发保护后暂停交易时间
            data_dir: 数据存储目录
            on_protection_triggered: 触发保护时的回调函数
        """
        self.max_drawdown_pct = max_drawdown_pct
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_position_hours = max_position_hours
        self.max_consecutive_losses = max_consecutive_losses
        self.pause_hours_after_protection = pause_hours_after_protection
        self.on_protection_triggered = on_protection_triggered

        # 状态跟踪
        self._peak_equity: float = 0.0  # 历史最高净值
        self._daily_start_equity: float = 0.0  # 当日开始净值
        self._daily_start_date: datetime | None = None
        self._consecutive_losses: int = 0
        self._last_protection_time: datetime | None = None
        self._is_trading_paused: bool = False
        self._pause_reason: str = ""

        # 持仓记录
        self._position_records: dict[str, PositionRecord] = {}
        self._lock = threading.Lock()

        # 账户快照历史
        self._snapshots: list[AccountSnapshot] = []
        self._max_snapshots = 1000  # 最多保留1000条快照

        # 数据持久化
        self.data_dir = Path(data_dir) if data_dir else Path("data/protection")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._load_state()

        print("🛡️ 账户保护管理器初始化完成")
        print(f"   最大回撤: {max_drawdown_pct*100}%")
        print(f"   单日最大亏损: {max_daily_loss_pct*100}%")
        print(f"   最大持仓时间: {max_position_hours} 小时")
        print(f"   最大连续亏损: {max_consecutive_losses} 次")

    def update_account_status(
        self,
        balance: float,
        equity: float,
        unrealized_pnl: float,
        margin_used: float
    ) -> dict[str, Any]:
        """
        更新账户状态，检查是否需要触发保护

        Args:
            balance: 账户余额
            equity: 账户净值（含未实现盈亏）
            unrealized_pnl: 未实现盈亏
            margin_used: 已用保证金

        Returns:
            {
                'action': ProtectionAction,
                'should_pause': bool,
                'drawdown_pct': float,
                'daily_loss_pct': float,
                'messages': List[str]
            }
        """
        now = datetime.now()
        messages = []

        with self._lock:
            # 记录快照
            snapshot = AccountSnapshot(
                timestamp=now,
                balance=balance,
                equity=equity,
                unrealized_pnl=unrealized_pnl,
                margin_used=margin_used
            )
            self._snapshots.append(snapshot)
            if len(self._snapshots) > self._max_snapshots:
                self._snapshots = self._snapshots[-self._max_snapshots:]

            # 更新峰值净值
            if equity > self._peak_equity:
                self._peak_equity = equity

            # 检查是否是新的一天
            if self._daily_start_date is None or now.date() != self._daily_start_date.date():
                self._daily_start_date = now
                self._daily_start_equity = equity
                messages.append(f"新交易日开始，初始净值: ${equity:.2f}")

            # 计算回撤
            drawdown_pct = 0.0
            if self._peak_equity > 0:
                drawdown_pct = (self._peak_equity - equity) / self._peak_equity

            # 计算当日亏损
            daily_loss_pct = 0.0
            if self._daily_start_equity > 0:
                daily_loss_pct = (self._daily_start_equity - equity) / self._daily_start_equity

            # 确定保护动作
            action = ProtectionAction.NONE
            should_pause = False

            # 检查是否仍在暂停期
            if self._is_trading_paused and self._last_protection_time:
                pause_end = self._last_protection_time + timedelta(hours=self.pause_hours_after_protection)
                if now < pause_end:
                    remaining = (pause_end - now).total_seconds() / 60
                    messages.append(f"交易暂停中，剩余 {remaining:.0f} 分钟 (原因: {self._pause_reason})")
                    should_pause = True
                else:
                    self._is_trading_paused = False
                    self._pause_reason = ""
                    messages.append("暂停期结束，恢复交易")

            # 检查最大回撤
            if drawdown_pct >= self.max_drawdown_pct:
                action = ProtectionAction.CLOSE_ALL_POSITIONS
                should_pause = True
                self._trigger_protection(f"最大回撤触发: {drawdown_pct*100:.1f}%")
                messages.append(f"⚠️ 【最大回撤保护】回撤 {drawdown_pct*100:.1f}% >= {self.max_drawdown_pct*100}%")

            # 检查单日最大亏损
            elif daily_loss_pct >= self.max_daily_loss_pct:
                action = ProtectionAction.PAUSE_NEW_TRADES
                should_pause = True
                self._trigger_protection(f"单日亏损触发: {daily_loss_pct*100:.1f}%")
                messages.append(f"⚠️ 【单日亏损保护】当日亏损 {daily_loss_pct*100:.1f}% >= {self.max_daily_loss_pct*100}%")

            # 检查连续亏损
            elif self._consecutive_losses >= self.max_consecutive_losses:
                action = ProtectionAction.PAUSE_NEW_TRADES
                should_pause = True
                self._trigger_protection(f"连续亏损触发: {self._consecutive_losses} 次")
                messages.append(f"⚠️ 【连续亏损保护】连续亏损 {self._consecutive_losses} 次 >= {self.max_consecutive_losses}")

            # 保存状态
            self._save_state()

        return {
            'action': action,
            'should_pause': should_pause,
            'drawdown_pct': drawdown_pct,
            'daily_loss_pct': daily_loss_pct,
            'peak_equity': self._peak_equity,
            'consecutive_losses': self._consecutive_losses,
            'messages': messages
        }

    def record_trade_result(self, is_profitable: bool, pnl: float = 0.0) -> None:
        """
        记录交易结果

        Args:
            is_profitable: 是否盈利
            pnl: 盈亏金额
        """
        with self._lock:
            if is_profitable:
                self._consecutive_losses = 0
            else:
                self._consecutive_losses += 1
            self._save_state()

    def record_position_open(
        self,
        symbol: str,
        entry_price: float,
        size: float,
        is_long: bool,
        leverage: int = 1
    ) -> None:
        """记录开仓"""
        with self._lock:
            self._position_records[symbol] = PositionRecord(
                symbol=symbol,
                entry_time=datetime.now(),
                entry_price=entry_price,
                size=size,
                is_long=is_long,
                leverage=leverage
            )
            self._save_state()

    def record_position_close(self, symbol: str) -> PositionRecord | None:
        """记录平仓，返回持仓记录"""
        with self._lock:
            record = self._position_records.pop(symbol, None)
            self._save_state()
            return record

    def check_position_timeout(self, symbol: str) -> dict[str, Any]:
        """
        检查持仓是否超时

        Returns:
            {
                'is_timeout': bool,
                'holding_hours': float,
                'max_hours': float,
                'should_close': bool
            }
        """
        with self._lock:
            record = self._position_records.get(symbol)
            if not record:
                return {
                    'is_timeout': False,
                    'holding_hours': 0,
                    'max_hours': self.max_position_hours,
                    'should_close': False
                }

            holding_hours = record.holding_hours()
            is_timeout = holding_hours >= self.max_position_hours

            return {
                'is_timeout': is_timeout,
                'holding_hours': holding_hours,
                'max_hours': self.max_position_hours,
                'should_close': is_timeout,
                'entry_time': record.entry_time.isoformat(),
                'entry_price': record.entry_price
            }

    def get_timeout_positions(self) -> list[str]:
        """获取所有超时的持仓符号"""
        timeout_symbols = []
        with self._lock:
            for symbol, record in self._position_records.items():
                if record.holding_hours() >= self.max_position_hours:
                    timeout_symbols.append(symbol)
        return timeout_symbols

    def is_trading_allowed(self) -> dict[str, Any]:
        """
        检查是否允许开新仓

        Returns:
            {
                'allowed': bool,
                'reason': str
            }
        """
        with self._lock:
            if self._is_trading_paused:
                return {
                    'allowed': False,
                    'reason': self._pause_reason
                }
            return {
                'allowed': True,
                'reason': ''
            }

    def reset_daily_stats(self) -> None:
        """重置每日统计（通常在每日开盘时调用）"""
        with self._lock:
            self._daily_start_date = datetime.now()
            # 使用最近的净值作为当日开始净值
            if self._snapshots:
                self._daily_start_equity = self._snapshots[-1].equity
            self._consecutive_losses = 0
            self._save_state()

    def force_resume_trading(self) -> None:
        """强制恢复交易（人工干预）"""
        with self._lock:
            self._is_trading_paused = False
            self._pause_reason = ""
            self._last_protection_time = None
            self._save_state()
            print("✅ 交易已手动恢复")

    def get_status(self) -> dict[str, Any]:
        """获取当前保护状态"""
        with self._lock:
            return {
                'is_paused': self._is_trading_paused,
                'pause_reason': self._pause_reason,
                'peak_equity': self._peak_equity,
                'daily_start_equity': self._daily_start_equity,
                'consecutive_losses': self._consecutive_losses,
                'active_positions': len(self._position_records),
                'position_symbols': list(self._position_records.keys())
            }

    def _trigger_protection(self, reason: str) -> None:
        """触发保护机制"""
        self._is_trading_paused = True
        self._pause_reason = reason
        self._last_protection_time = datetime.now()

        print(f"🛡️ 【保护机制触发】{reason}")

        if self.on_protection_triggered:
            try:
                self.on_protection_triggered(reason)
            except Exception as e:
                print(f"⚠️ 保护回调执行失败: {e}")

    def _save_state(self) -> None:
        """保存状态到文件"""
        try:
            state = {
                'peak_equity': self._peak_equity,
                'daily_start_equity': self._daily_start_equity,
                'daily_start_date': self._daily_start_date.isoformat() if self._daily_start_date else None,
                'consecutive_losses': self._consecutive_losses,
                'is_trading_paused': self._is_trading_paused,
                'pause_reason': self._pause_reason,
                'last_protection_time': self._last_protection_time.isoformat() if self._last_protection_time else None,
                'position_records': {k: v.to_dict() for k, v in self._position_records.items()},
                'updated_at': datetime.now().isoformat()
            }
            state_file = self.data_dir / "protection_state.json"
            with open(state_file, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            print(f"⚠️ 保存保护状态失败: {e}")

    def _load_state(self) -> None:
        """从文件加载状态"""
        try:
            state_file = self.data_dir / "protection_state.json"
            if state_file.exists():
                with open(state_file) as f:
                    state = json.load(f)

                self._peak_equity = state.get('peak_equity', 0.0)
                self._daily_start_equity = state.get('daily_start_equity', 0.0)

                if state.get('daily_start_date'):
                    self._daily_start_date = datetime.fromisoformat(state['daily_start_date'])

                self._consecutive_losses = state.get('consecutive_losses', 0)
                self._is_trading_paused = state.get('is_trading_paused', False)
                self._pause_reason = state.get('pause_reason', '')

                if state.get('last_protection_time'):
                    self._last_protection_time = datetime.fromisoformat(state['last_protection_time'])

                # 加载持仓记录
                for symbol, record_dict in state.get('position_records', {}).items():
                    self._position_records[symbol] = PositionRecord.from_dict(record_dict)

                print(f"📂 已加载保护状态: 峰值净值=${self._peak_equity:.2f}, 连续亏损={self._consecutive_losses}")
        except Exception as e:
            print(f"⚠️ 加载保护状态失败: {e}")
