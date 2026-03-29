"""
云端日志模块测试（v2 — 基于 aepipe-sdk 0.1.1）
测试 CloudLogger 的核心功能：SDK 集成、D1 payload、事件类型、批量发送、失败容错、单例管理
"""

import queue
from unittest.mock import MagicMock

import pytest
from aepipe import DataPoint, LogEntry

from src.utils.cloud_logger import CloudLogger, get_cloud_logger, init_cloud_logger

# ── Fixture ──────────────────────────────────────────────────


@pytest.fixture
def cloud_logger():
    """创建测试用云端日志实例（启用状态）"""
    logger = CloudLogger(
        base_url="http://localhost:9999",
        token="test-token",
        project="test-project",
        logstore="test-store",
        flush_interval=60.0,  # 手动刷新，避免自动触发
        payload_ttl=7776000,
        enabled=True,
    )
    yield logger
    logger._stop_event.set()


@pytest.fixture
def disabled_logger():
    """创建禁用状态的云端日志实例"""
    return CloudLogger(
        base_url="http://localhost:9999",
        token="test-token",
        enabled=False,
    )


# ── 基础功能测试 ──────────────────────────────────────────────


class TestCloudLoggerInit:
    """初始化测试"""

    def test_enabled_logger_starts_worker(self, cloud_logger):
        """启用时后台线程应启动"""
        assert cloud_logger.enabled is True
        assert cloud_logger._worker.is_alive()

    def test_disabled_logger_no_worker(self, disabled_logger):
        """禁用时不应创建后台线程"""
        assert disabled_logger.enabled is False
        assert not hasattr(disabled_logger, "_worker") or disabled_logger._worker is None

    def test_sdk_client_initialized(self, cloud_logger):
        """应使用官方 SDK 客户端"""
        assert cloud_logger._client is not None
        assert cloud_logger.project == "test-project"
        assert cloud_logger.logstore == "test-store"

    def test_payload_ttl_configured(self, cloud_logger):
        """payload TTL 应正确配置"""
        assert cloud_logger.payload_ttl == 7776000


# ── 日志入队测试 ──────────────────────────────────────────────


class TestEnqueue:
    """日志入队行为测试"""

    def test_send_log_enqueues(self, cloud_logger):
        """send_log 应将 LogEntry 放入日志队列"""
        cloud_logger.send_log("测试消息", level="info")
        assert cloud_logger._log_queue.qsize() == 1

        item = cloud_logger._log_queue.get_nowait()
        assert isinstance(item, LogEntry)
        assert item.message == "测试消息"
        assert item.level == "info"
        assert "timestamp" in item.extra

    def test_send_log_extra_fields(self, cloud_logger):
        """send_log 的额外字段应被保留"""
        cloud_logger.send_log("测试", level="error", user_id="u-1", ip="1.2.3.4")
        item = cloud_logger._log_queue.get_nowait()
        assert item.extra["user_id"] == "u-1"
        assert item.extra["ip"] == "1.2.3.4"

    def test_send_event_enqueues(self, cloud_logger):
        """send_event 应将 DataPoint 放入结构化队列"""
        cloud_logger.send_event(
            event="test_event",
            level="info",
            blobs=["a", "b"],
            doubles=[1.0, 2.5],
        )
        assert cloud_logger._ingest_queue.qsize() == 1

        item = cloud_logger._ingest_queue.get_nowait()
        assert isinstance(item, DataPoint)
        assert item.event == "test_event"
        assert item.blobs == ["a", "b"]
        assert item.doubles == [1.0, 2.5]

    def test_send_event_with_payload(self, cloud_logger):
        """send_event 应支持 D1 payload"""
        cloud_logger.send_event(
            event="test_payload",
            payload={"full_data": "很长的内容" * 1000},
        )
        item = cloud_logger._ingest_queue.get_nowait()
        assert item.payload is not None
        assert "full_data" in item.payload
        assert item.ttl == 7776000  # 与 payload_ttl 一致

    def test_send_event_without_payload_no_ttl(self, cloud_logger):
        """无 payload 时不应设置 ttl"""
        cloud_logger.send_event(event="no_payload")
        item = cloud_logger._ingest_queue.get_nowait()
        assert item.payload is None
        assert item.ttl is None

    def test_disabled_logger_ignores_all(self, disabled_logger):
        """禁用状态下所有发送操作应被忽略"""
        disabled_logger.send_log("test")
        disabled_logger.send_event(event="test")
        disabled_logger.send_decision(symbol="BTC", decision="BUY", status="SUCCESS")
        disabled_logger.send_trade(symbol="ETH", action="BUY", amount=1, price=100)
        disabled_logger.send_system_event("startup")
        disabled_logger.send_risk_event(symbol="BTC", risk_type="drawdown")
        disabled_logger.send_grid_event(symbol="ETH", action="rebuild")
        disabled_logger.send_cycle_event(symbol="BTC", phase="start")
        disabled_logger.send_account_snapshot(balance=1000, equity=1000)
        disabled_logger.send_alert(symbol="BTC", alert_type="test", severity="high", message="test")
        # 没有异常即为通过


# ── 决策完整性测试 ─────────────────────────────────────────────


class TestDecisionCompleteness:
    """决策日志完整性测试（核心：不截断，D1 payload 存储全量数据）"""

    def test_ai_response_not_truncated(self, cloud_logger):
        """AI 响应不应被截断（通过 payload 完整存储）"""
        long_response = "分析结论：" + "x" * 10000
        cloud_logger.send_decision(
            symbol="BTC",
            decision="BUY",
            status="SUCCESS",
            ai_response=long_response,
            confidence=0.85,
            current_price=50000.0,
        )
        item = cloud_logger._ingest_queue.get_nowait()
        assert item.payload is not None
        # 完整 AI 响应在 payload 中，不截断
        assert item.payload["ai_response"] == long_response
        assert len(item.payload["ai_response"]) == 10005  # "分析结论："(5字符) + 10000

    def test_prompt_stored_in_payload(self, cloud_logger):
        """完整 Prompt 应通过 payload 存储"""
        long_prompt = "你是一个交易 Agent..." + "指标数据 " * 2000
        cloud_logger.send_decision(
            symbol="ETH",
            decision="SELL",
            status="SUCCESS",
            prompt=long_prompt,
        )
        item = cloud_logger._ingest_queue.get_nowait()
        assert item.payload["prompt"] == long_prompt

    def test_market_data_stored_in_payload(self, cloud_logger):
        """完整市场数据应通过 payload 存储"""
        market_data = {
            "current_price": 50000.0,
            "rsi": 65.3,
            "macd": 120.5,
            "bollinger_upper": 52000.0,
            "bollinger_lower": 48000.0,
            "volume_24h": 1234567890.0,
        }
        cloud_logger.send_decision(
            symbol="BTC",
            decision="HOLD",
            status="SUCCESS",
            market_data=market_data,
        )
        item = cloud_logger._ingest_queue.get_nowait()
        assert item.payload["market_data"] == market_data

    def test_decision_blobs_are_lightweight(self, cloud_logger):
        """blobs 应仅包含轻量索引字段（不含长数据）"""
        cloud_logger.send_decision(
            symbol="BTC",
            decision="BUY",
            status="SUCCESS",
            regime="trending",
        )
        item = cloud_logger._ingest_queue.get_nowait()
        # blobs 应为：[symbol, decision, status, error_msg, regime]
        assert item.blobs == ["BTC", "BUY", "SUCCESS", "", "trending"]
        # 每个 blob 都很短
        for blob in item.blobs:
            assert len(blob) < 300

    def test_decision_error_message_truncated_in_blobs(self, cloud_logger):
        """blobs 中的错误信息应截断（完整版在 payload）"""
        long_error = "E" * 500
        cloud_logger.send_decision(
            symbol="BTC",
            decision="ERROR",
            status="ERROR",
            error_message=long_error,
        )
        item = cloud_logger._ingest_queue.get_nowait()
        # blobs 中截断到 200 字符
        assert len(item.blobs[3]) == 200
        # payload 中保留完整
        assert len(item.payload["error_message"]) == 500


# ── 交易记录测试 ──────────────────────────────────────────────


class TestTradeEvent:
    """交易记录事件测试"""

    def test_trade_full_data(self, cloud_logger):
        """交易记录应包含完整数据"""
        cloud_logger.send_trade(
            symbol="ETH",
            action="SELL",
            amount=0.5,
            price=3000.0,
            order_id="ord-123",
            pnl=50.0,
            leverage=5.0,
            take_profit_price=3200.0,
            stop_loss_price=2800.0,
            order_type="limit",
            fee=1.35,
        )
        item = cloud_logger._ingest_queue.get_nowait()
        assert item.event == "trade"
        assert item.blobs == ["ETH", "SELL", "ord-123", "FILLED", "limit"]
        assert item.doubles == [0.5, 3000.0, 50.0, 5.0, 3200.0, 2800.0, 1.35]
        # payload 中有完整信息
        assert item.payload["symbol"] == "ETH"
        assert item.payload["leverage"] == 5.0


# ── 新增事件类型测试 ─────────────────────────────────────────


class TestNewEventTypes:
    """新增事件类型测试"""

    def test_system_event(self, cloud_logger):
        """系统事件应正确入队"""
        cloud_logger.send_system_event(
            "startup",
            details={"symbols": ["BTC", "ETH"], "run_mode": "main"},
        )
        item = cloud_logger._ingest_queue.get_nowait()
        assert item.event == "system"
        assert item.blobs == ["startup"]
        assert item.payload["action"] == "startup"
        assert item.payload["symbols"] == ["BTC", "ETH"]

    def test_risk_event(self, cloud_logger):
        """风控事件应正确入队"""
        cloud_logger.send_risk_event(
            symbol="BTC",
            risk_type="drawdown",
            details={"current_pct": 8.5, "threshold": 10.0},
            level="warn",
        )
        item = cloud_logger._ingest_queue.get_nowait()
        assert item.event == "risk"
        assert item.level == "warn"
        assert item.blobs == ["BTC", "drawdown"]
        assert item.payload["current_pct"] == 8.5

    def test_grid_event(self, cloud_logger):
        """网格事件应正确入队"""
        cloud_logger.send_grid_event(
            symbol="ETH",
            action="rebuild",
            details={"buy_count": 5, "sell_count": 4},
        )
        item = cloud_logger._ingest_queue.get_nowait()
        assert item.event == "grid"
        assert item.blobs == ["ETH", "rebuild"]
        assert item.payload["buy_count"] == 5

    def test_cycle_event(self, cloud_logger):
        """交易周期事件应正确入队"""
        cloud_logger.send_cycle_event(
            symbol="BTC",
            phase="start",
            details={"triggered_by": "alert"},
        )
        item = cloud_logger._ingest_queue.get_nowait()
        assert item.event == "cycle"
        assert item.blobs == ["BTC", "start"]

    def test_account_snapshot(self, cloud_logger):
        """账户快照应正确入队"""
        cloud_logger.send_account_snapshot(
            balance=10000.0,
            equity=10500.0,
            unrealized_pnl=500.0,
            drawdown_pct=2.5,
            daily_pnl=150.0,
            positions=[{"symbol": "BTC", "size": 0.1}],
        )
        item = cloud_logger._ingest_queue.get_nowait()
        assert item.event == "account"
        assert item.doubles == [10000.0, 10500.0, 500.0, 2.5, 150.0]
        assert len(item.payload["positions"]) == 1

    def test_alert_event(self, cloud_logger):
        """告警事件应正确入队"""
        cloud_logger.send_alert(
            symbol="BTC",
            alert_type="volatility",
            severity="extreme",
            message="5 分钟内价格暴跌 6%",
            details={"change_pct": -6.0},
        )
        item = cloud_logger._ingest_queue.get_nowait()
        assert item.event == "alert"
        assert item.level == "error"  # extreme → error
        assert item.blobs == ["BTC", "volatility", "extreme"]
        assert item.payload["change_pct"] == -6.0

    def test_alert_elevated_level(self, cloud_logger):
        """非严重告警应为 warn 级别"""
        cloud_logger.send_alert(
            symbol="ETH",
            alert_type="volatility",
            severity="elevated",
            message="轻微波动",
        )
        item = cloud_logger._ingest_queue.get_nowait()
        assert item.level == "warn"


# ── blobs/doubles 截断测试 ───────────────────────────────────


class TestFieldLimits:
    """字段限制测试"""

    def test_blobs_max_15(self, cloud_logger):
        """blobs 应被截断为最多 15 个（aepipe-sdk 0.1.1 限制）"""
        blobs = [f"b{i}" for i in range(20)]
        cloud_logger.send_event(event="test", blobs=blobs)
        item = cloud_logger._ingest_queue.get_nowait()
        assert len(item.blobs) == 15

    def test_doubles_max_20(self, cloud_logger):
        """doubles 应被截断为最多 20 个"""
        doubles = [float(i) for i in range(25)]
        cloud_logger.send_event(event="test", doubles=doubles)
        item = cloud_logger._ingest_queue.get_nowait()
        assert len(item.doubles) == 20


# ── 队列溢出测试 ──────────────────────────────────────────────


class TestQueueOverflow:
    """队列满时的行为测试"""

    def test_enqueue_when_full_discards_oldest(self):
        """队列满时应丢弃最旧消息"""
        logger = CloudLogger(
            base_url="http://localhost:9999",
            token="t",
            flush_interval=60.0,
        )
        # 手动设置一个小队列
        logger._log_queue = queue.Queue(maxsize=2)
        logger.send_log("第一条")
        logger.send_log("第二条")
        logger.send_log("第三条")  # 应丢弃第一条

        assert logger._log_queue.qsize() == 2
        item1 = logger._log_queue.get_nowait()
        item2 = logger._log_queue.get_nowait()
        assert item1.message == "第二条"
        assert item2.message == "第三条"
        logger._stop_event.set()


# ── SDK 集成发送测试 ────────────────────────────────────────


class TestFlush:
    """刷新发送测试（验证 SDK 调用）"""

    def test_flush_calls_sdk_log(self, cloud_logger):
        """flush 应调用 SDK 的 log 方法批量发送"""
        cloud_logger._client.log = MagicMock(
            return_value=MagicMock(ok=True, written=2)
        )

        cloud_logger.send_log("消息1")
        cloud_logger.send_log("消息2")
        cloud_logger.flush()

        cloud_logger._client.log.assert_called_once()
        call_args = cloud_logger._client.log.call_args
        assert call_args[0][0] == "test-project"  # project
        assert call_args[0][1] == "test-store"  # logstore
        assert len(call_args[0][2]) == 2  # 2 条日志
        assert all(isinstance(e, LogEntry) for e in call_args[0][2])

    def test_flush_calls_sdk_ingest(self, cloud_logger):
        """flush 应调用 SDK 的 ingest 方法批量发送"""
        cloud_logger._client.ingest = MagicMock(
            return_value=MagicMock(ok=True, written=2)
        )

        cloud_logger.send_event(event="e1")
        cloud_logger.send_event(event="e2")
        cloud_logger.flush()

        cloud_logger._client.ingest.assert_called_once()
        call_args = cloud_logger._client.ingest.call_args
        assert call_args[0][0] == "test-project"
        assert call_args[0][1] == "test-store"
        assert len(call_args[0][2]) == 2
        assert all(isinstance(p, DataPoint) for p in call_args[0][2])

    def test_flush_with_payload_points(self, cloud_logger):
        """带 payload 的事件应通过 SDK ingest 发送"""
        cloud_logger._client.ingest = MagicMock(
            return_value=MagicMock(ok=True, written=1)
        )

        cloud_logger.send_decision(
            symbol="BTC",
            decision="BUY",
            status="SUCCESS",
            ai_response="这是完整的 AI 分析..." * 100,
            prompt="完整 Prompt 内容..." * 100,
        )
        cloud_logger.flush()

        cloud_logger._client.ingest.assert_called_once()
        points = cloud_logger._client.ingest.call_args[0][2]
        assert len(points) == 1
        assert points[0].payload is not None
        assert "ai_response" in points[0].payload

    def test_flush_handles_aepipe_error_gracefully(self, cloud_logger):
        """AepipeError 不应抛出异常"""
        from aepipe import AepipeError

        cloud_logger._client.log = MagicMock(
            side_effect=AepipeError(500, "服务器错误")
        )

        cloud_logger.send_log("test")
        # 不应抛异常
        cloud_logger.flush()

    def test_flush_handles_validation_error_gracefully(self, cloud_logger):
        """ValidationError 不应抛出异常（跳过不重试）"""
        from aepipe import ValidationError

        cloud_logger._client.ingest = MagicMock(
            side_effect=ValidationError("blob 超限")
        )

        cloud_logger.send_event(event="test")
        # 不应抛异常
        cloud_logger.flush()

    def test_flush_handles_network_error_gracefully(self, cloud_logger):
        """网络错误不应抛出异常"""
        cloud_logger._client.log = MagicMock(
            side_effect=OSError("连接失败")
        )

        cloud_logger.send_log("test")
        cloud_logger.flush()

    def test_flush_empty_queue_no_request(self, cloud_logger):
        """空队列时不应发送请求"""
        cloud_logger._client.log = MagicMock()
        cloud_logger._client.ingest = MagicMock()

        cloud_logger.flush()

        cloud_logger._client.log.assert_not_called()
        cloud_logger._client.ingest.assert_not_called()


# ── 单例管理测试 ──────────────────────────────────────────────


class TestSingleton:
    """单例管理测试"""

    def test_get_cloud_logger_before_init(self):
        """未初始化时应返回 None"""
        import src.utils.cloud_logger as mod

        original = mod._cloud_logger
        mod._cloud_logger = None
        try:
            assert get_cloud_logger() is None
        finally:
            mod._cloud_logger = original

    def test_init_creates_singleton(self):
        """init_cloud_logger 应创建全局单例"""
        import src.utils.cloud_logger as mod

        original = mod._cloud_logger
        mod._cloud_logger = None
        try:
            instance = init_cloud_logger(
                base_url="http://localhost:9999",
                token="t",
                flush_interval=60.0,
            )
            assert get_cloud_logger() is instance
            instance._stop_event.set()
        finally:
            mod._cloud_logger = original

    def test_init_returns_existing(self):
        """重复调用 init_cloud_logger 应返回同一实例"""
        import src.utils.cloud_logger as mod

        original = mod._cloud_logger
        mod._cloud_logger = None
        try:
            first = init_cloud_logger(
                base_url="http://localhost:9999",
                token="t1",
                flush_interval=60.0,
            )
            second = init_cloud_logger(
                base_url="http://localhost:8888",
                token="t2",
                flush_interval=60.0,
            )
            assert first is second
            first._stop_event.set()
        finally:
            mod._cloud_logger = original

    def test_init_with_payload_ttl(self):
        """init_cloud_logger 应传递 payload_ttl 参数"""
        import src.utils.cloud_logger as mod

        original = mod._cloud_logger
        mod._cloud_logger = None
        try:
            instance = init_cloud_logger(
                base_url="http://localhost:9999",
                token="t",
                flush_interval=60.0,
                payload_ttl=86400,  # 1 天
            )
            assert instance.payload_ttl == 86400
            instance._stop_event.set()
        finally:
            mod._cloud_logger = original


# ── 关闭测试 ──────────────────────────────────────────────────


class TestShutdown:
    """关闭行为测试"""

    def test_shutdown_flushes_remaining(self, cloud_logger):
        """关闭时应刷新剩余日志"""
        cloud_logger._client.log = MagicMock(
            return_value=MagicMock(ok=True, written=1)
        )

        cloud_logger.send_log("最后一条")
        cloud_logger.shutdown()

        cloud_logger._client.log.assert_called()

    def test_shutdown_disabled_no_error(self, disabled_logger):
        """禁用状态关闭不应报错"""
        disabled_logger.shutdown()
