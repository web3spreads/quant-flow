"""
市场主动监控模块
在决策周期间隔内持续监控价格波动，检测到异常波动时主动触发决策循环。

设计思路：
- 轻量级监控：仅通过 all_mids() API 获取最新价格，不做完整的指标计算
- 自适应阈值：支持基于 ATR 的动态波动阈值，也支持固定百分比阈值
- 冷却机制：触发后进入冷却期，避免频繁触发
- 与主循环协调：通过回调函数触发决策，复用现有的交易锁机制
"""

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from hyperliquid.utils import constants

from src.utils.hyperliquid import create_info


class AlertLevel(StrEnum):
    """异常波动等级"""

    NORMAL = "normal"  # 正常波动
    ELEVATED = "elevated"  # 轻微异常（记录但不触发）
    HIGH = "high"  # 显著异常（触发决策）
    EXTREME = "extreme"  # 极端波动（触发决策 + 额外告警）


@dataclass
class PriceSnapshot:
    """价格快照"""

    symbol: str
    price: float
    timestamp: datetime


@dataclass
class VolatilityAlert:
    """波动告警"""

    symbol: str
    level: AlertLevel
    change_pct: float  # 价格变动百分比
    current_price: float
    reference_price: float  # 参考基准价格
    duration_seconds: float  # 从基准到当前的时间跨度
    timestamp: datetime
    message: str


@dataclass
class MonitorConfig:
    """监控配置"""

    enabled: bool = False  # 是否启用市场监控
    check_interval_seconds: int = 30  # 检查间隔（秒）
    # 波动阈值（百分比）
    alert_threshold_pct: float = 3.0  # 触发告警的波动阈值
    elevated_threshold_pct: float = 1.5  # 轻微异常阈值
    extreme_threshold_pct: float = 5.0  # 极端波动阈值
    # 冷却机制
    cooldown_minutes: int = 5  # 触发后冷却时间（分钟）
    # 价格基准窗口
    reference_window_minutes: int = 10  # 价格基准窗口（分钟），取窗口内的第一个价格作为基准
    # 自适应阈值（基于 ATR）
    adaptive_threshold: bool = False  # 是否启用自适应阈值
    atr_multiplier: float = 2.0  # ATR 乘数（用于计算动态阈值）


class MarketMonitor:
    """
    市场主动监控器

    在决策周期间隔内持续轻量级监控市场价格，
    检测到异常波动时通过回调函数触发主决策循环。
    """

    def __init__(
        self,
        symbols: list[str],
        testnet: bool = False,
        config: MonitorConfig | None = None,
        on_alert_callback=None,
        logger=None,
    ):
        """
        初始化市场监控器

        Args:
            symbols: 需要监控的交易对列表
            testnet: 是否使用测试网
            config: 监控配置
            on_alert_callback: 告警回调函数，接收 VolatilityAlert 参数
            logger: 日志记录器
        """
        self.symbols = symbols
        self.testnet = testnet
        self.config = config or MonitorConfig()
        self.on_alert_callback = on_alert_callback
        self.logger = logger

        # 初始化 Hyperliquid Info API（轻量级，仅用于获取价格）
        self.base_url = constants.TESTNET_API_URL if testnet else constants.MAINNET_API_URL
        self.info = create_info(self.base_url, skip_ws=True)

        # 价格历史记录（每个 symbol 保存最近 N 个快照）
        self._price_history: dict[str, list[PriceSnapshot]] = {s: [] for s in symbols}

        # 冷却状态（每个 symbol 独立冷却）
        self._cooldown_until: dict[str, datetime] = {
            s: datetime.min for s in symbols
        }

        # 最近一次常规决策周期的时间（用于判断是否需要监控）
        self._last_cycle_time: datetime = datetime.now()

        # 监控线程控制
        self._monitor_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._is_running = False

        # 统计信息
        self.stats = {
            "total_checks": 0,
            "total_alerts": 0,
            "alerts_by_symbol": {s: 0 for s in symbols},
            "alerts_by_level": {level.value: 0 for level in AlertLevel},
        }

    def start(self):
        """启动监控线程"""
        if not self.config.enabled:
            self._log_info("市场监控未启用，跳过启动")
            return

        if self._is_running:
            self._log_warning("市场监控已在运行中")
            return

        self._stop_event.clear()
        self._is_running = True

        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            name="market-monitor",
            daemon=True,  # 设为守护线程，主程序退出时自动终止
        )
        self._monitor_thread.start()

        self._log_info(
            f"市场监控已启动 | 监控 {len(self.symbols)} 个交易对 | "
            f"检查间隔 {self.config.check_interval_seconds}s | "
            f"波动阈值 {self.config.alert_threshold_pct}%"
        )

    def stop(self):
        """停止监控线程"""
        if not self._is_running:
            return

        self._stop_event.set()
        self._is_running = False

        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=10)

        self._log_info(
            f"市场监控已停止 | 总检查次数: {self.stats['total_checks']} | "
            f"总告警次数: {self.stats['total_alerts']}"
        )

    def notify_cycle_completed(self):
        """
        通知监控器常规决策周期已完成。
        更新基准时间，并清理过早的价格历史。
        """
        self._last_cycle_time = datetime.now()
        # 清理过旧的价格历史，只保留参考窗口内的数据
        self._cleanup_price_history()

    def get_latest_alert(self, symbol: str) -> VolatilityAlert | None:
        """获取某个交易对最近的告警信息（用于注入决策上下文）"""
        history = self._price_history.get(symbol, [])
        if len(history) < 2:
            return None

        reference = self._get_reference_price(symbol)
        if reference is None:
            return None

        latest = history[-1]
        change_pct = abs(latest.price - reference.price) / reference.price * 100

        if change_pct >= self.config.elevated_threshold_pct:
            level = self._classify_alert_level(change_pct)
            duration = (latest.timestamp - reference.timestamp).total_seconds()
            direction = "上涨" if latest.price > reference.price else "下跌"
            return VolatilityAlert(
                symbol=symbol,
                level=level,
                change_pct=change_pct if latest.price > reference.price else -change_pct,
                current_price=latest.price,
                reference_price=reference.price,
                duration_seconds=duration,
                timestamp=latest.timestamp,
                message=f"{symbol} {int(duration)}秒内{direction} {change_pct:.2f}%",
            )
        return None

    def format_alert_context(self, alert: VolatilityAlert) -> str:
        """
        将告警格式化为可注入 LLM 决策 Prompt 的上下文文本。

        Returns:
            格式化后的告警上下文字符串
        """
        direction = "上涨" if alert.change_pct > 0 else "下跌"
        abs_change = abs(alert.change_pct)
        duration_min = alert.duration_seconds / 60

        context = (
            f"⚠️ 异常波动告警 [{alert.level.value.upper()}]\n"
            f"  {alert.symbol} 在 {duration_min:.1f} 分钟内{direction} {abs_change:.2f}%\n"
            f"  基准价格: {alert.reference_price} → 当前价格: {alert.current_price}\n"
            f"  告警时间: {alert.timestamp.strftime('%H:%M:%S')}\n"
            f"  注意：此波动可能表明市场出现重大事件，请在决策时充分考虑突发波动风险。"
        )
        return context

    def _monitor_loop(self):
        """监控主循环（运行在独立线程中）"""
        self._log_info("监控线程已启动")

        while not self._stop_event.is_set():
            try:
                self._check_prices()
                self.stats["total_checks"] += 1
            except Exception as e:
                self._log_warning(f"价格检查异常: {e}")

            # 等待下一次检查（可中断的等待）
            self._stop_event.wait(timeout=self.config.check_interval_seconds)

    def _check_prices(self):
        """检查所有交易对的价格变动"""
        try:
            all_mids = self.info.all_mids()
        except Exception as e:
            self._log_warning(f"获取价格失败: {e}")
            return

        now = datetime.now()

        for symbol in self.symbols:
            if symbol not in all_mids:
                continue

            current_price = float(all_mids[symbol])
            snapshot = PriceSnapshot(
                symbol=symbol,
                price=current_price,
                timestamp=now,
            )

            # 记录价格快照
            self._price_history[symbol].append(snapshot)

            # 检查波动
            self._check_volatility(symbol, snapshot)

    def _check_volatility(self, symbol: str, current: PriceSnapshot):
        """检查单个交易对的价格波动"""
        reference = self._get_reference_price(symbol)
        if reference is None:
            return

        # 计算价格变动百分比
        change_pct = abs(current.price - reference.price) / reference.price * 100
        duration = (current.timestamp - reference.timestamp).total_seconds()

        # 判断波动等级
        level = self._classify_alert_level(change_pct)

        if level == AlertLevel.NORMAL:
            return

        # 检查冷却期
        if level in (AlertLevel.HIGH, AlertLevel.EXTREME):
            if current.timestamp < self._cooldown_until[symbol]:
                remaining = (self._cooldown_until[symbol] - current.timestamp).total_seconds()
                self._log_info(
                    f"{symbol} 波动 {change_pct:.2f}% 但仍在冷却期中"
                    f"（剩余 {remaining:.0f}s）"
                )
                return

        # 生成告警
        direction = "上涨" if current.price > reference.price else "下跌"
        signed_change = change_pct if current.price > reference.price else -change_pct
        alert = VolatilityAlert(
            symbol=symbol,
            level=level,
            change_pct=signed_change,
            current_price=current.price,
            reference_price=reference.price,
            duration_seconds=duration,
            timestamp=current.timestamp,
            message=f"{symbol} {int(duration)}秒内{direction} {change_pct:.2f}%",
        )

        # 更新统计
        self.stats["total_alerts"] += 1
        self.stats["alerts_by_symbol"][symbol] += 1
        self.stats["alerts_by_level"][level.value] += 1

        if level == AlertLevel.ELEVATED:
            self._log_info(f"📈 轻微波动: {alert.message}")
            return

        # HIGH 或 EXTREME 级别 → 触发决策
        self._log_warning(f"🚨 异常波动检测: {alert.message} [{level.value}]")

        # 进入冷却期
        self._cooldown_until[symbol] = current.timestamp + timedelta(
            minutes=self.config.cooldown_minutes
        )

        # 触发回调
        if self.on_alert_callback:
            try:
                self.on_alert_callback(alert)
            except Exception as e:
                self._log_warning(f"告警回调执行失败: {e}")

    def _get_reference_price(self, symbol: str) -> PriceSnapshot | None:
        """
        获取参考基准价格。
        取参考窗口内最早的价格快照作为基准。
        """
        history = self._price_history.get(symbol, [])
        if not history:
            return None

        now = datetime.now()
        window_start = now - timedelta(minutes=self.config.reference_window_minutes)

        # 查找窗口内最早的快照
        for snapshot in history:
            if snapshot.timestamp >= window_start:
                return snapshot

        # 如果窗口内没有数据，用最早的记录
        return history[0] if history else None

    def _classify_alert_level(self, change_pct: float) -> AlertLevel:
        """根据变动百分比分类告警等级"""
        if change_pct >= self.config.extreme_threshold_pct:
            return AlertLevel.EXTREME
        elif change_pct >= self.config.alert_threshold_pct:
            return AlertLevel.HIGH
        elif change_pct >= self.config.elevated_threshold_pct:
            return AlertLevel.ELEVATED
        else:
            return AlertLevel.NORMAL

    def _cleanup_price_history(self):
        """清理过旧的价格历史数据"""
        cutoff = datetime.now() - timedelta(
            minutes=self.config.reference_window_minutes * 2
        )
        for symbol in self.symbols:
            self._price_history[symbol] = [
                s for s in self._price_history[symbol] if s.timestamp >= cutoff
            ]

    def _log_info(self, message: str):
        """记录信息日志"""
        if self.logger:
            self.logger.print_info(f"[市场监控] {message}")
        else:
            print(f"[市场监控] {message}")

    def _log_warning(self, message: str):
        """记录警告日志"""
        if self.logger:
            self.logger.print_warning(f"[市场监控] {message}")
        else:
            print(f"⚠️ [市场监控] {message}")
