"""
云端日志模块测试
测试 CloudLogger 的核心功能：日志入队、批量发送、失败容错、单例管理
"""

import json
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import MagicMock, patch

import pytest

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

    def test_url_construction(self, cloud_logger):
        """端点 URL 应正确构建"""
        assert cloud_logger._ingest_url == "http://localhost:9999/v1/test-project/test-store/ingest"
        assert cloud_logger._log_url == "http://localhost:9999/v1/test-project/test-store/log"

    def test_trailing_slash_removed(self):
        """base_url 末尾斜杠应被移除"""
        logger = CloudLogger(
            base_url="http://localhost:9999/",
            token="t",
            flush_interval=60.0,
        )
        assert logger.base_url == "http://localhost:9999"
        logger._stop_event.set()


# ── 日志入队测试 ──────────────────────────────────────────────


class TestEnqueue:
    """日志入队行为测试"""

    def test_send_log_enqueues(self, cloud_logger):
        """send_log 应将消息放入日志队列"""
        cloud_logger.send_log("测试消息", level="info")
        assert cloud_logger._log_queue.qsize() == 1

        item = cloud_logger._log_queue.get_nowait()
        assert item["message"] == "测试消息"
        assert item["level"] == "info"
        assert "timestamp" in item

    def test_send_log_extra_fields(self, cloud_logger):
        """send_log 的额外字段应被保留"""
        cloud_logger.send_log("测试", level="error", user_id="u-1", ip="1.2.3.4")
        item = cloud_logger._log_queue.get_nowait()
        assert item["user_id"] == "u-1"
        assert item["ip"] == "1.2.3.4"

    def test_send_event_enqueues(self, cloud_logger):
        """send_event 应将事件放入结构化队列"""
        cloud_logger.send_event(
            event="test_event",
            level="info",
            blobs=["a", "b"],
            doubles=[1.0, 2.5],
        )
        assert cloud_logger._ingest_queue.qsize() == 1

        item = cloud_logger._ingest_queue.get_nowait()
        assert item["event"] == "test_event"
        assert item["blobs"] == ["a", "b"]
        assert item["doubles"] == [1.0, 2.5]

    def test_send_decision_enqueues(self, cloud_logger):
        """send_decision 应生成结构化事件"""
        cloud_logger.send_decision(
            symbol="BTC",
            decision="BUY",
            status="SUCCESS",
            ai_response="看多信号明确",
            confidence=0.85,
            current_price=50000.0,
        )
        assert cloud_logger._ingest_queue.qsize() == 1

        item = cloud_logger._ingest_queue.get_nowait()
        assert item["event"] == "decision"
        assert "BTC" in item["blobs"]
        assert "BUY" in item["blobs"]
        assert 0.85 in item["doubles"]

    def test_send_trade_enqueues(self, cloud_logger):
        """send_trade 应生成结构化事件"""
        cloud_logger.send_trade(
            symbol="ETH",
            action="SELL",
            amount=0.5,
            price=3000.0,
            order_id="ord-123",
            pnl=50.0,
        )
        assert cloud_logger._ingest_queue.qsize() == 1

        item = cloud_logger._ingest_queue.get_nowait()
        assert item["event"] == "trade"
        assert "ETH" in item["blobs"]
        assert 0.5 in item["doubles"]

    def test_disabled_logger_ignores_all(self, disabled_logger):
        """禁用状态下所有发送操作应被忽略"""
        disabled_logger.send_log("test")
        disabled_logger.send_event(event="test")
        disabled_logger.send_decision(symbol="BTC", decision="BUY", status="SUCCESS")
        disabled_logger.send_trade(symbol="ETH", action="BUY", amount=1, price=100)
        # 没有异常即为通过


# ── blobs/doubles 截断测试 ───────────────────────────────────


class TestFieldLimits:
    """字段限制测试"""

    def test_blobs_max_16(self, cloud_logger):
        """blobs 应被截断为最多 16 个"""
        blobs = [f"b{i}" for i in range(20)]
        cloud_logger.send_event(event="test", blobs=blobs)
        item = cloud_logger._ingest_queue.get_nowait()
        assert len(item["blobs"]) == 16

    def test_doubles_max_20(self, cloud_logger):
        """doubles 应被截断为最多 20 个"""
        doubles = [float(i) for i in range(25)]
        cloud_logger.send_event(event="test", doubles=doubles)
        item = cloud_logger._ingest_queue.get_nowait()
        assert len(item["doubles"]) == 20

    def test_ai_response_truncated(self, cloud_logger):
        """AI 响应应被截取前 500 字符"""
        long_response = "x" * 1000
        cloud_logger.send_decision(
            symbol="BTC", decision="HOLD", status="SUCCESS", ai_response=long_response
        )
        item = cloud_logger._ingest_queue.get_nowait()
        # blobs[3] 是 ai_response
        assert len(item["blobs"][3]) == 500


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
        logger._stop_event.set()


# ── HTTP 发送测试 ──────────────────────────────────────────────


class TestFlush:
    """刷新发送测试"""

    @patch("src.utils.cloud_logger.urllib.request.urlopen")
    def test_flush_sends_log_batch(self, mock_urlopen, cloud_logger):
        """flush 应批量发送日志队列中的消息"""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        cloud_logger.send_log("消息1")
        cloud_logger.send_log("消息2")
        cloud_logger.flush()

        # 应发送一次 HTTP 请求（包含 2 条日志）
        assert mock_urlopen.called
        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        body = json.loads(req.data.decode("utf-8"))
        assert "logs" in body
        assert len(body["logs"]) == 2

    @patch("src.utils.cloud_logger.urllib.request.urlopen")
    def test_flush_sends_ingest_batch(self, mock_urlopen, cloud_logger):
        """flush 应批量发送结构化事件"""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        cloud_logger.send_event(event="e1")
        cloud_logger.send_event(event="e2")
        cloud_logger.flush()

        assert mock_urlopen.called
        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        body = json.loads(req.data.decode("utf-8"))
        assert "points" in body
        assert len(body["points"]) == 2

    @patch("src.utils.cloud_logger.urllib.request.urlopen")
    def test_auth_header_set(self, mock_urlopen, cloud_logger):
        """请求应包含 Bearer 认证头"""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        cloud_logger.send_log("test")
        cloud_logger.flush()

        req = mock_urlopen.call_args[0][0]
        assert req.get_header("Authorization") == "Bearer test-token"
        assert req.get_header("Content-type") == "application/json"

    @patch("src.utils.cloud_logger.urllib.request.urlopen")
    def test_flush_handles_error_gracefully(self, mock_urlopen, cloud_logger):
        """发送失败不应抛出异常"""
        import urllib.error

        mock_urlopen.side_effect = urllib.error.URLError("连接失败")

        cloud_logger.send_log("test")
        # 不应抛异常
        cloud_logger.flush()

    @patch("src.utils.cloud_logger.urllib.request.urlopen")
    def test_flush_empty_queue_no_request(self, mock_urlopen, cloud_logger):
        """空队列时不应发送请求"""
        cloud_logger.flush()
        assert not mock_urlopen.called


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


# ── 关闭测试 ──────────────────────────────────────────────────


class TestShutdown:
    """关闭行为测试"""

    @patch("src.utils.cloud_logger.urllib.request.urlopen")
    def test_shutdown_flushes_remaining(self, mock_urlopen, cloud_logger):
        """关闭时应刷新剩余日志"""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        cloud_logger.send_log("最后一条")
        cloud_logger.shutdown()

        assert mock_urlopen.called

    def test_shutdown_disabled_no_error(self, disabled_logger):
        """禁用状态关闭不应报错"""
        disabled_logger.shutdown()
