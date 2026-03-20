"""
云端日志同步模块
基于 aepipe 服务（Cloudflare Workers + Analytics Engine）实现日志云端同步。
本模块作为独立抽象层，不侵入现有日志逻辑，仅在现有日志流程中透明转发。

aepipe 项目: https://github.com/loadchange/aepipe
"""

import contextlib
import json
import logging
import queue
import threading
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any

logger = logging.getLogger("QuantFlow.CloudLogger")

# 单批次最大条目数（aepipe 限制 250）
_MAX_BATCH_SIZE = 200

# 发送失败最大重试次数
_MAX_RETRIES = 2

# HTTP 请求超时（秒）
_HTTP_TIMEOUT = 10

# 队列最大容量（防止内存溢出）
_MAX_QUEUE_SIZE = 5000


class CloudLogger:
    """
    云端日志发送器

    通过后台线程异步批量发送日志到 aepipe 服务，
    确保日志上传不阻塞主交易流程。

    支持两种 aepipe 端点：
    - ingest: 结构化数据（决策、交易记录等），写入 Analytics Engine，保留 92 天
    - log: 原始日志消息（控制台输出等），写入 Workers Logs，保留 7-30 天
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        project: str = "quant-flow",
        logstore: str = "trading",
        flush_interval: float = 5.0,
        enabled: bool = True,
    ):
        """
        初始化云端日志发送器

        Args:
            base_url: aepipe 服务地址（如 https://your-worker.workers.dev）
            token: aepipe ADMIN_TOKEN 认证令牌
            project: aepipe 项目名称
            logstore: aepipe 日志存储名称
            flush_interval: 批量发送间隔（秒）
            enabled: 是否启用云端日志
        """
        self.enabled = enabled
        if not enabled:
            return

        # 去除末尾斜杠
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.project = project
        self.logstore = logstore
        self.flush_interval = flush_interval

        # 构建端点 URL
        self._ingest_url = f"{self.base_url}/v1/{self.project}/{self.logstore}/ingest"
        self._log_url = f"{self.base_url}/v1/{self.project}/{self.logstore}/log"

        # 异步队列：分别存放结构化数据和原始日志
        self._ingest_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=_MAX_QUEUE_SIZE)
        self._log_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=_MAX_QUEUE_SIZE)

        # 入队操作锁（保护队列满时的丢弃+放入原子性）
        self._enqueue_lock = threading.Lock()

        # 停止信号
        self._stop_event = threading.Event()

        # 启动后台发送线程
        self._worker = threading.Thread(
            target=self._flush_loop,
            name="cloud-logger-worker",
            daemon=True,
        )
        self._worker.start()
        logger.info(
            "云端日志已启用 (项目=%s, 日志存储=%s, 发送间隔=%.1fs)",
            project,
            logstore,
            flush_interval,
        )

    # ── 公开 API ──────────────────────────────────────────────

    def send_log(
        self,
        message: str,
        level: str = "info",
        **extra: Any,
    ) -> None:
        """
        发送原始日志到云端（写入 Workers Logs）

        适用于一般日志消息、信息、警告、错误等。

        Args:
            message: 日志消息内容
            level: 日志级别 (debug/info/warn/error)
            **extra: 额外字段，将随日志一起保存
        """
        if not self.enabled:
            return

        entry = {
            "message": message,
            "level": level,
            "timestamp": datetime.now().isoformat(),
            **extra,
        }
        self._enqueue(self._log_queue, entry)

    def send_event(
        self,
        event: str,
        level: str = "info",
        blobs: list[str] | None = None,
        doubles: list[float] | None = None,
    ) -> None:
        """
        发送结构化事件到云端（写入 Analytics Engine）

        适用于决策记录、交易记录等需要长期查询的结构化数据。

        Args:
            event: 事件名称（如 "decision", "trade", "error"）
            level: 日志级别
            blobs: 字符串数据列表（最多 16 个，每个 ≤16KB）
            doubles: 数值数据列表（最多 20 个）
        """
        if not self.enabled:
            return

        point = {"event": event, "level": level}
        if blobs:
            point["blobs"] = blobs[:16]
        if doubles:
            point["doubles"] = doubles[:20]
        self._enqueue(self._ingest_queue, point)

    def send_decision(
        self,
        symbol: str,
        decision: str,
        status: str,
        ai_response: str = "",
        confidence: float = 0.0,
        current_price: float = 0.0,
        error_message: str = "",
    ) -> None:
        """
        发送交易决策记录到云端

        Args:
            symbol: 交易对
            decision: 决策类型 (BUY/SELL/DO_NOTHING)
            status: 执行状态
            ai_response: AI 响应摘要（截取前 500 字符）
            confidence: 置信度
            current_price: 当前价格
            error_message: 错误信息
        """
        if not self.enabled:
            return

        blobs = [
            symbol,
            decision,
            status,
            ai_response[:500] if ai_response else "",
            error_message or "",
        ]
        doubles = [confidence, current_price]

        self.send_event(
            event="decision",
            level="error" if status != "SUCCESS" else "info",
            blobs=blobs,
            doubles=doubles,
        )

    def send_trade(
        self,
        symbol: str,
        action: str,
        amount: float,
        price: float,
        order_id: str = "",
        pnl: float = 0.0,
        status: str = "FILLED",
    ) -> None:
        """
        发送交易执行记录到云端

        Args:
            symbol: 交易对
            action: 交易动作 (BUY/SELL)
            amount: 交易数量
            price: 交易价格
            order_id: 订单 ID
            pnl: 盈亏
            status: 订单状态
        """
        if not self.enabled:
            return

        blobs = [symbol, action, order_id, status]
        doubles = [amount, price, pnl]

        self.send_event(
            event="trade",
            level="info",
            blobs=blobs,
            doubles=doubles,
        )

    def flush(self) -> None:
        """立即刷新队列中的所有待发送日志"""
        if not self.enabled:
            return
        self._flush_ingest_queue()
        self._flush_log_queue()

    def shutdown(self) -> None:
        """关闭云端日志（刷新剩余数据并停止后台线程）"""
        if not self.enabled:
            return
        logger.info("正在关闭云端日志...")
        self._stop_event.set()
        self._worker.join(timeout=15)
        # 最后一次刷新
        self.flush()
        logger.info("云端日志已关闭")

    # ── 内部实现 ──────────────────────────────────────────────

    def _enqueue(self, q: queue.Queue, item: dict) -> None:
        """安全入队，队列满时丢弃最旧消息"""
        with self._enqueue_lock:
            if q.full():
                with contextlib.suppress(queue.Empty):
                    q.get_nowait()  # 丢弃队首（最旧）

            try:
                q.put_nowait(item)
            except queue.Full:
                logger.warning("云端日志队列已满，丢弃新消息")

    def _flush_loop(self) -> None:
        """后台线程主循环：定时批量发送"""
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=self.flush_interval)
            try:
                self._flush_ingest_queue()
                self._flush_log_queue()
            except Exception as e:
                logger.debug("云端日志发送异常: %s", e)

    def _drain_queue(self, q: queue.Queue, max_items: int) -> list[dict]:
        """从队列中取出最多 max_items 条数据"""
        items = []
        while len(items) < max_items:
            try:
                items.append(q.get_nowait())
            except queue.Empty:
                break
        return items

    def _flush_ingest_queue(self) -> None:
        """刷新结构化事件队列"""
        while not self._ingest_queue.empty():
            points = self._drain_queue(self._ingest_queue, _MAX_BATCH_SIZE)
            if points:
                self._post(self._ingest_url, {"points": points})

    def _flush_log_queue(self) -> None:
        """刷新原始日志队列"""
        while not self._log_queue.empty():
            logs = self._drain_queue(self._log_queue, _MAX_BATCH_SIZE)
            if logs:
                self._post(self._log_url, {"logs": logs})

    def _post(self, url: str, payload: dict) -> None:
        """
        发送 POST 请求到 aepipe

        Args:
            url: 端点 URL
            payload: 请求体
        """
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
        }

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                req = urllib.request.Request(url, data=data, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
                    if 200 <= resp.status < 300:
                        return  # 成功
                    logger.warning(
                        "云端日志发送收到非成功状态码 (HTTP %d), 尝试 %d/%d",
                        resp.status,
                        attempt,
                        _MAX_RETRIES,
                    )
            except urllib.error.HTTPError as e:
                logger.warning(
                    "云端日志发送失败 (HTTP %d), 尝试 %d/%d",
                    e.code,
                    attempt,
                    _MAX_RETRIES,
                )
            except (urllib.error.URLError, OSError) as e:
                logger.debug(
                    "云端日志发送异常 (尝试 %d/%d): %s",
                    attempt,
                    _MAX_RETRIES,
                    e,
                )
        # 所有重试失败，静默放弃（不影响主流程）


# ── 单例管理 ──────────────────────────────────────────────────

_cloud_logger: CloudLogger | None = None
_cloud_logger_lock = threading.Lock()


def get_cloud_logger() -> CloudLogger | None:
    """获取全局云端日志实例（未初始化时返回 None）"""
    return _cloud_logger


def init_cloud_logger(
    base_url: str,
    token: str,
    project: str = "quant-flow",
    logstore: str = "trading",
    flush_interval: float = 5.0,
    enabled: bool = True,
) -> CloudLogger:
    """
    初始化全局云端日志实例（线程安全，双重检查锁定）

    Args:
        base_url: aepipe 服务地址
        token: aepipe ADMIN_TOKEN
        project: 项目名称
        logstore: 日志存储名称
        flush_interval: 发送间隔（秒）
        enabled: 是否启用

    Returns:
        CloudLogger 实例
    """
    global _cloud_logger
    if _cloud_logger is None:
        with _cloud_logger_lock:
            if _cloud_logger is None:
                _cloud_logger = CloudLogger(
                    base_url=base_url,
                    token=token,
                    project=project,
                    logstore=logstore,
                    flush_interval=flush_interval,
                    enabled=enabled,
                )
    return _cloud_logger
