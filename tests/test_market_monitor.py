"""
市场主动监控模块测试
测试 MarketMonitor 的核心功能：价格监控、波动检测、告警分级、冷却机制
"""

import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.data.market_monitor import (
    AlertLevel,
    MarketMonitor,
    MonitorConfig,
    PriceSnapshot,
    VolatilityAlert,
)


@pytest.fixture
def monitor_config():
    """创建测试用监控配置"""
    return MonitorConfig(
        enabled=True,
        check_interval_seconds=1,  # 测试用快速间隔
        alert_threshold_pct=3.0,
        elevated_threshold_pct=1.5,
        extreme_threshold_pct=5.0,
        cooldown_minutes=1,
        reference_window_minutes=5,
    )


@pytest.fixture
def mock_callback():
    """创建模拟回调函数"""
    return MagicMock()


@pytest.fixture
def monitor(monitor_config, mock_callback):
    """创建 MarketMonitor 实例（不启动线程）"""
    with patch("src.data.market_monitor.create_info") as mock_create_info:
        mock_info = MagicMock()
        mock_info.all_mids.return_value = {"BTC": "50000.0", "ETH": "3000.0"}
        mock_create_info.return_value = mock_info

        m = MarketMonitor(
            symbols=["BTC", "ETH"],
            testnet=True,
            config=monitor_config,
            on_alert_callback=mock_callback,
        )
        yield m


class TestAlertLevelClassification:
    """测试告警等级分类"""

    def test_normal_level(self, monitor):
        """正常波动应返回 NORMAL"""
        assert monitor._classify_alert_level(0.5) == AlertLevel.NORMAL
        assert monitor._classify_alert_level(1.0) == AlertLevel.NORMAL

    def test_elevated_level(self, monitor):
        """轻微异常应返回 ELEVATED"""
        assert monitor._classify_alert_level(1.5) == AlertLevel.ELEVATED
        assert monitor._classify_alert_level(2.5) == AlertLevel.ELEVATED

    def test_high_level(self, monitor):
        """显著异常应返回 HIGH"""
        assert monitor._classify_alert_level(3.0) == AlertLevel.HIGH
        assert monitor._classify_alert_level(4.5) == AlertLevel.HIGH

    def test_extreme_level(self, monitor):
        """极端波动应返回 EXTREME"""
        assert monitor._classify_alert_level(5.0) == AlertLevel.EXTREME
        assert monitor._classify_alert_level(10.0) == AlertLevel.EXTREME


class TestPriceHistory:
    """测试价格历史管理"""

    def test_initial_price_history_empty(self, monitor):
        """初始状态下价格历史应为空"""
        assert len(monitor._price_history["BTC"]) == 0
        assert len(monitor._price_history["ETH"]) == 0

    def test_cleanup_price_history(self, monitor):
        """清理过旧的价格历史"""
        now = datetime.now()
        old_time = now - timedelta(minutes=20)  # 远超参考窗口的 2 倍

        # 添加旧数据和新数据
        monitor._price_history["BTC"] = [
            PriceSnapshot("BTC", 50000.0, old_time),
            PriceSnapshot("BTC", 51000.0, now),
        ]

        monitor._cleanup_price_history()

        # 旧数据应被清理
        assert len(monitor._price_history["BTC"]) == 1
        assert monitor._price_history["BTC"][0].price == 51000.0


class TestVolatilityDetection:
    """测试波动检测"""

    def test_no_alert_for_small_change(self, monitor, mock_callback):
        """小幅波动不应触发告警"""
        now = datetime.now()
        # 模拟价格从 50000 到 50500（+1%，低于阈值）
        monitor._price_history["BTC"] = [
            PriceSnapshot("BTC", 50000.0, now - timedelta(minutes=2)),
        ]

        current = PriceSnapshot("BTC", 50500.0, now)
        monitor._check_volatility("BTC", current)

        mock_callback.assert_not_called()

    def test_alert_triggered_for_large_change(self, monitor, mock_callback):
        """大幅波动应触发告警回调"""
        now = datetime.now()
        # 模拟价格从 50000 到 52000（+4%，超过 3% 阈值）
        monitor._price_history["BTC"] = [
            PriceSnapshot("BTC", 50000.0, now - timedelta(minutes=2)),
        ]

        current = PriceSnapshot("BTC", 52000.0, now)
        monitor._check_volatility("BTC", current)

        mock_callback.assert_called_once()
        alert = mock_callback.call_args[0][0]
        assert isinstance(alert, VolatilityAlert)
        assert alert.symbol == "BTC"
        assert alert.level == AlertLevel.HIGH
        assert alert.change_pct > 0  # 上涨

    def test_alert_for_price_drop(self, monitor, mock_callback):
        """价格下跌也应触发告警"""
        now = datetime.now()
        # 模拟价格从 50000 到 47000（-6%，极端波动）
        monitor._price_history["BTC"] = [
            PriceSnapshot("BTC", 50000.0, now - timedelta(minutes=2)),
        ]

        current = PriceSnapshot("BTC", 47000.0, now)
        monitor._check_volatility("BTC", current)

        mock_callback.assert_called_once()
        alert = mock_callback.call_args[0][0]
        assert alert.level == AlertLevel.EXTREME
        assert alert.change_pct < 0  # 下跌

    def test_elevated_not_trigger_callback(self, monitor, mock_callback):
        """ELEVATED 级别应记录但不触发回调"""
        now = datetime.now()
        # 模拟价格从 50000 到 51000（+2%，轻微异常）
        monitor._price_history["BTC"] = [
            PriceSnapshot("BTC", 50000.0, now - timedelta(minutes=2)),
        ]

        current = PriceSnapshot("BTC", 51000.0, now)
        monitor._check_volatility("BTC", current)

        mock_callback.assert_not_called()


class TestCooldownMechanism:
    """测试冷却机制"""

    def test_cooldown_prevents_repeated_alerts(self, monitor, mock_callback):
        """冷却期内不应重复触发告警"""
        now = datetime.now()
        base_price = 50000.0

        # 第一次触发
        monitor._price_history["BTC"] = [
            PriceSnapshot("BTC", base_price, now - timedelta(minutes=2)),
        ]
        current1 = PriceSnapshot("BTC", 52000.0, now)
        monitor._check_volatility("BTC", current1)
        assert mock_callback.call_count == 1

        # 冷却期内再次波动，不应触发
        monitor._price_history["BTC"] = [
            PriceSnapshot("BTC", base_price, now - timedelta(minutes=1)),
        ]
        current2 = PriceSnapshot("BTC", 53000.0, now + timedelta(seconds=30))
        monitor._check_volatility("BTC", current2)
        assert mock_callback.call_count == 1  # 仍然是 1

    def test_cooldown_per_symbol(self, monitor, mock_callback):
        """冷却期应按交易对独立"""
        now = datetime.now()

        # BTC 触发告警
        monitor._price_history["BTC"] = [
            PriceSnapshot("BTC", 50000.0, now - timedelta(minutes=2)),
        ]
        monitor._check_volatility(
            "BTC", PriceSnapshot("BTC", 52000.0, now)
        )
        assert mock_callback.call_count == 1

        # ETH 应该仍然可以触发（独立冷却）
        monitor._price_history["ETH"] = [
            PriceSnapshot("ETH", 3000.0, now - timedelta(minutes=2)),
        ]
        monitor._check_volatility(
            "ETH", PriceSnapshot("ETH", 3200.0, now)
        )
        assert mock_callback.call_count == 2


class TestReferencePrice:
    """测试参考基准价格"""

    def test_reference_from_window(self, monitor):
        """参考价格应取窗口内最早的快照"""
        now = datetime.now()
        history = [
            PriceSnapshot("BTC", 49000.0, now - timedelta(minutes=10)),  # 窗口外
            PriceSnapshot("BTC", 50000.0, now - timedelta(minutes=3)),  # 窗口内最早
            PriceSnapshot("BTC", 51000.0, now - timedelta(minutes=1)),
            PriceSnapshot("BTC", 52000.0, now),
        ]

        ref = monitor._get_reference_price_from(history)
        assert ref is not None
        # 窗口为 5 分钟，应取 3 分钟前的价格（窗口内最早的）
        assert ref.price == 50000.0

    def test_reference_none_for_empty_history(self, monitor):
        """空历史应返回 None"""
        ref = monitor._get_reference_price_from([])
        assert ref is None

    def test_reference_none_when_all_data_outside_window(self, monitor):
        """窗口外的数据不应被用作基准（预热期）"""
        now = datetime.now()
        history = [
            PriceSnapshot("BTC", 49000.0, now - timedelta(minutes=20)),
        ]
        ref = monitor._get_reference_price_from(history)
        assert ref is None


class TestAlertFormatting:
    """测试告警格式化"""

    def test_format_alert_context(self, monitor):
        """格式化告警上下文应包含关键信息"""
        alert = VolatilityAlert(
            symbol="BTC",
            level=AlertLevel.HIGH,
            change_pct=3.5,
            current_price=51750.0,
            reference_price=50000.0,
            duration_seconds=180.0,
            timestamp=datetime.now(),
            message="BTC 180秒内上涨 3.50%",
        )

        context = monitor.format_alert_context(alert)

        assert "BTC" in context
        assert "上涨" in context
        assert "3.50%" in context
        assert "51750" in context
        assert "50000" in context
        assert "HIGH" in context

    def test_format_downward_alert(self, monitor):
        """下跌告警应显示下跌"""
        alert = VolatilityAlert(
            symbol="ETH",
            level=AlertLevel.EXTREME,
            change_pct=-6.0,
            current_price=2820.0,
            reference_price=3000.0,
            duration_seconds=120.0,
            timestamp=datetime.now(),
            message="ETH 120秒内下跌 6.00%",
        )

        context = monitor.format_alert_context(alert)
        assert "下跌" in context
        assert "6.00%" in context


class TestGetLatestAlert:
    """测试获取最新告警"""

    def test_get_latest_alert_with_significant_change(self, monitor):
        """有显著波动时应返回告警"""
        now = datetime.now()
        monitor._price_history["BTC"] = [
            PriceSnapshot("BTC", 50000.0, now - timedelta(minutes=2)),
            PriceSnapshot("BTC", 52000.0, now),
        ]

        alert = monitor.get_latest_alert("BTC")
        assert alert is not None
        assert alert.level == AlertLevel.HIGH

    def test_get_latest_alert_no_change(self, monitor):
        """无显著波动时应返回 None"""
        now = datetime.now()
        monitor._price_history["BTC"] = [
            PriceSnapshot("BTC", 50000.0, now - timedelta(minutes=2)),
            PriceSnapshot("BTC", 50100.0, now),  # +0.2%，低于 elevated 阈值
        ]

        alert = monitor.get_latest_alert("BTC")
        assert alert is None


class TestNotifyCycleCompleted:
    """测试决策周期完成通知"""

    def test_notify_updates_last_cycle_time(self, monitor):
        """通知应更新最近周期时间"""
        old_time = monitor._last_cycle_time
        time.sleep(0.01)
        monitor.notify_cycle_completed()
        assert monitor._last_cycle_time > old_time

    def test_notify_cleans_old_history(self, monitor):
        """通知应清理过旧的价格历史"""
        old_time = datetime.now() - timedelta(minutes=30)
        monitor._price_history["BTC"] = [
            PriceSnapshot("BTC", 50000.0, old_time),
        ]

        monitor.notify_cycle_completed()
        assert len(monitor._price_history["BTC"]) == 0


class TestMonitorStats:
    """测试监控统计"""

    def test_stats_updated_on_alert(self, monitor, mock_callback):
        """告警时统计应更新"""
        now = datetime.now()
        monitor._price_history["BTC"] = [
            PriceSnapshot("BTC", 50000.0, now - timedelta(minutes=2)),
        ]

        monitor._check_volatility(
            "BTC", PriceSnapshot("BTC", 52000.0, now)
        )

        assert monitor.stats["total_alerts"] == 1
        assert monitor.stats["alerts_by_symbol"]["BTC"] == 1
        assert monitor.stats["alerts_by_level"]["high"] == 1


class TestMonitorStartStop:
    """测试监控启停"""

    def test_start_disabled(self, mock_callback):
        """未启用时 start 应跳过"""
        config = MonitorConfig(enabled=False)
        with patch("src.data.market_monitor.create_info"):
            m = MarketMonitor(
                symbols=["BTC"],
                config=config,
                on_alert_callback=mock_callback,
            )
            m.start()
            assert not m._is_running

    def test_start_and_stop(self, monitor):
        """启动和停止应正常工作"""
        monitor.start()
        assert monitor._is_running
        assert monitor._monitor_thread is not None
        assert monitor._monitor_thread.is_alive()

        monitor.stop()
        assert not monitor._is_running
        time.sleep(0.1)
        assert not monitor._monitor_thread.is_alive()

    def test_double_start(self, monitor):
        """重复启动不应创建新线程"""
        monitor.start()
        first_thread = monitor._monitor_thread

        monitor.start()  # 第二次启动
        assert monitor._monitor_thread is first_thread

        monitor.stop()


class TestEdgeCases:
    """测试边界情况和容错"""

    def test_all_mids_returns_none(self, monitor, mock_callback):
        """当 API 返回 None 时应优雅处理，不触发告警"""
        monitor.info.all_mids.return_value = None
        monitor._check_prices()
        mock_callback.assert_not_called()

    def test_all_mids_returns_empty_dict(self, monitor, mock_callback):
        """当 API 返回空字典时应优雅处理"""
        monitor.info.all_mids.return_value = {}
        monitor._check_prices()
        mock_callback.assert_not_called()

    def test_callback_exception_does_not_crash_monitor(self, monitor, mock_callback):
        """回调函数抛出异常不应导致监控崩溃"""
        now = datetime.now()
        mock_callback.side_effect = RuntimeError("回调异常")
        monitor._price_history["BTC"] = [
            PriceSnapshot("BTC", 50000.0, now - timedelta(minutes=2)),
        ]
        # 不应抛出异常
        monitor._check_volatility("BTC", PriceSnapshot("BTC", 52000.0, now))

    def test_concurrent_price_history_access(self, monitor):
        """并发读写 _price_history 不应引发异常"""
        import concurrent.futures

        now = datetime.now()

        def write_prices():
            for i in range(100):
                with monitor._history_lock:
                    monitor._price_history["BTC"].append(
                        PriceSnapshot("BTC", 50000.0 + i, now + timedelta(seconds=i))
                    )

        def read_prices():
            for _ in range(100):
                with monitor._history_lock:
                    list(monitor._price_history["BTC"])

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [
                executor.submit(write_prices),
                executor.submit(read_prices),
                executor.submit(write_prices),
                executor.submit(read_prices),
            ]
            for f in concurrent.futures.as_completed(futures):
                f.result()  # 如果有异常会在此抛出

    def test_elevated_log_throttling(self, monitor, mock_callback):
        """ELEVATED 级别日志应被节流，避免洪泛"""
        now = datetime.now()
        monitor._price_history["BTC"] = [
            PriceSnapshot("BTC", 50000.0, now - timedelta(minutes=2)),
        ]

        # 连续触发多次 ELEVATED（2% 波动）
        for i in range(5):
            current = PriceSnapshot(
                "BTC", 51000.0, now + timedelta(seconds=i * 10)
            )
            monitor._check_volatility("BTC", current)

        # 回调不应被触发（ELEVATED 不触发决策）
        mock_callback.assert_not_called()
        # 统计应记录所有告警
        assert monitor.stats["alerts_by_level"]["elevated"] == 5
