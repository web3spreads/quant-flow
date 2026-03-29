"""
云端日志同步模块（v2 — 基于 aepipe-sdk 0.1.1）

核心升级：
- 使用官方 aepipe-sdk，客户端防截断校验，拒绝静默丢数据
- 利用 D1 payload 存储完整 AI 响应、Prompt、市场数据等长文本，突破 16KB blob 限制
- 新增丰富事件类型：系统生命周期、交易周期、风控、网格、账户保护等
- blobs 仅放轻量索引字段（symbol/action/status），大数据全走 payload

aepipe 项目: https://github.com/loadchange/aepipe
"""

import contextlib
import logging
import queue
import threading
from datetime import datetime
from typing import Any

from aepipe import Aepipe, AepipeError, DataPoint, LogEntry, ValidationError

logger = logging.getLogger("QuantFlow.CloudLogger")

# 单批次最大条目数（aepipe 限制 250，留余量）
_MAX_BATCH_SIZE = 200

# 发送失败最大重试次数
_MAX_RETRIES = 2

# 队列最大容量（防止内存溢出）
_MAX_QUEUE_SIZE = 5000

# D1 payload 默认 TTL（秒）：90 天，与 Analytics Engine 保留期一致
_DEFAULT_PAYLOAD_TTL = 90 * 24 * 3600


class CloudLogger:
    """
    云端日志发送器（v2）

    通过后台线程异步批量发送日志到 aepipe 服务，
    确保日志上传不阻塞主交易流程。

    v2 升级要点：
    - 使用官方 aepipe-sdk，带客户端 blob 大小校验，防止 Cloudflare 静默截断
    - 长数据通过 D1 payload 存储（AI 全文响应、完整 Prompt、市场快照等）
    - blobs 仅存放轻量索引字段，doubles 存放数值指标
    - 新增丰富事件类型覆盖完整交易生命周期
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        project: str = "quant-flow",
        logstore: str = "trading",
        flush_interval: float = 5.0,
        payload_ttl: int = _DEFAULT_PAYLOAD_TTL,
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
            payload_ttl: D1 payload 过期时间（秒），默认 90 天
            enabled: 是否启用云端日志
        """
        self.enabled = enabled
        if not enabled:
            return

        self.project = project
        self.logstore = logstore
        self.flush_interval = flush_interval
        self.payload_ttl = payload_ttl

        # 初始化官方 SDK 客户端
        self._client = Aepipe(base_url=base_url, token=token)

        # 异步队列：分别存放结构化数据和原始日志
        self._ingest_queue: queue.Queue[DataPoint] = queue.Queue(maxsize=_MAX_QUEUE_SIZE)
        self._log_queue: queue.Queue[LogEntry] = queue.Queue(maxsize=_MAX_QUEUE_SIZE)

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
            "云端日志已启用 (项目=%s, 日志存储=%s, 发送间隔=%.1fs, payload_ttl=%d天)",
            project,
            logstore,
            flush_interval,
            payload_ttl // 86400,
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

        entry = LogEntry(
            message=message,
            level=level,
            extra={
                "timestamp": datetime.now().isoformat(),
                **extra,
            },
        )
        self._enqueue(self._log_queue, entry)

    def send_event(
        self,
        event: str,
        level: str = "info",
        blobs: list[str] | None = None,
        doubles: list[float] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """
        发送结构化事件到云端（写入 Analytics Engine + 可选 D1）

        blobs 仅存放轻量索引字段（每项尽量短），大数据走 payload。

        Args:
            event: 事件名称（如 "decision", "trade", "error"）
            level: 日志级别
            blobs: 字符串索引列表（最多 15 个，总和 ≤16KB）
            doubles: 数值数据列表（最多 20 个）
            payload: 完整数据载荷（存入 D1，无大小限制）
        """
        if not self.enabled:
            return

        point = DataPoint(
            event=event,
            level=level,
            blobs=blobs[:15] if blobs else [],
            doubles=doubles[:20] if doubles else [],
            payload=payload,
            ttl=self.payload_ttl if payload else None,
        )
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
        prompt: str = "",
        market_data: dict[str, Any] | None = None,
        action_details: dict[str, Any] | None = None,
        debate_summary: str = "",
        regime: str = "",
        validation_result: str = "",
    ) -> None:
        """
        发送交易决策记录到云端（完整数据，不截断）

        blobs 存放索引字段便于 SQL 过滤，
        payload 存放完整 AI 响应、Prompt、市场快照等大数据。

        Args:
            symbol: 交易对
            decision: 决策类型 (BUY/SELL/DO_NOTHING)
            status: 执行状态 (SUCCESS/ERROR)
            ai_response: AI 完整响应（通过 D1 payload 存储，不截断）
            confidence: 置信度
            current_price: 当前价格
            error_message: 错误信息
            prompt: 发送给 AI 的完整 Prompt
            market_data: 完整市场数据快照
            action_details: 执行计划详情
            debate_summary: 多空辩论结果摘要
            regime: 当前市场 Regime
            validation_result: 决策验证结果
        """
        if not self.enabled:
            return

        # blobs：轻量索引字段，用于 SQL 过滤查询
        blobs = [
            symbol,
            decision,
            status,
            error_message[:200] if error_message else "",
            regime or "unknown",
        ]
        doubles = [confidence, current_price]

        # payload：完整数据载荷，通过 D1 存储，无大小限制
        payload = {
            "type": "decision",
            "timestamp": datetime.now().isoformat(),
            "symbol": symbol,
            "decision": decision,
            "status": status,
            "confidence": confidence,
            "current_price": current_price,
            "regime": regime,
            "ai_response": ai_response,  # 完整 AI 响应，不截断
            "prompt": prompt,  # 完整 Prompt
            "market_data": market_data or {},
            "action_details": action_details or {},
            "debate_summary": debate_summary,
            "validation_result": validation_result,
            "error_message": error_message,
        }

        self.send_event(
            event="decision",
            level="error" if status != "SUCCESS" else "info",
            blobs=blobs,
            doubles=doubles,
            payload=payload,
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
        leverage: float = 0.0,
        take_profit_price: float = 0.0,
        stop_loss_price: float = 0.0,
        order_type: str = "market",
        fee: float = 0.0,
    ) -> None:
        """
        发送交易执行记录到云端

        Args:
            symbol: 交易对
            action: 交易动作 (BUY/SELL/CLOSE_LONG/CLOSE_SHORT)
            amount: 交易数量
            price: 交易价格
            order_id: 订单 ID
            pnl: 盈亏
            status: 订单状态
            leverage: 杠杆倍数
            take_profit_price: 止盈价
            stop_loss_price: 止损价
            order_type: 订单类型 (market/limit)
            fee: 手续费
        """
        if not self.enabled:
            return

        blobs = [symbol, action, order_id or "", status, order_type]
        doubles = [amount, price, pnl, leverage, take_profit_price, stop_loss_price, fee]

        payload = {
            "type": "trade",
            "timestamp": datetime.now().isoformat(),
            "symbol": symbol,
            "action": action,
            "amount": amount,
            "price": price,
            "order_id": order_id,
            "pnl": pnl,
            "status": status,
            "leverage": leverage,
            "take_profit_price": take_profit_price,
            "stop_loss_price": stop_loss_price,
            "order_type": order_type,
            "fee": fee,
        }

        self.send_event(
            event="trade",
            level="info",
            blobs=blobs,
            doubles=doubles,
            payload=payload,
        )

    def send_system_event(
        self,
        action: str,
        details: dict[str, Any] | None = None,
        level: str = "info",
    ) -> None:
        """
        发送系统生命周期事件（启动/关闭/配置变更等）

        Args:
            action: 事件动作 (startup/shutdown/config_change/error 等)
            details: 详情数据
            level: 日志级别
        """
        if not self.enabled:
            return

        payload = {
            "type": "system",
            "timestamp": datetime.now().isoformat(),
            "action": action,
            **(details or {}),
        }

        self.send_event(
            event="system",
            level=level,
            blobs=[action],
            payload=payload,
        )

    def send_risk_event(
        self,
        symbol: str,
        risk_type: str,
        details: dict[str, Any] | None = None,
        level: str = "warn",
    ) -> None:
        """
        发送风控事件（账户保护、止损触发、回撤预警等）

        Args:
            symbol: 交易对
            risk_type: 风控类型 (drawdown/daily_loss/position_timeout/stop_loss_triggered 等)
            details: 详情数据
            level: 日志级别
        """
        if not self.enabled:
            return

        payload = {
            "type": "risk",
            "timestamp": datetime.now().isoformat(),
            "symbol": symbol,
            "risk_type": risk_type,
            **(details or {}),
        }

        self.send_event(
            event="risk",
            level=level,
            blobs=[symbol, risk_type],
            payload=payload,
        )

    def send_grid_event(
        self,
        symbol: str,
        action: str,
        details: dict[str, Any] | None = None,
        level: str = "info",
    ) -> None:
        """
        发送网格交易事件（同步/重建/撤单等）

        Args:
            symbol: 交易对
            action: 网格动作 (sync/rebuild/cancel/place_orders 等)
            details: 详情数据
            level: 日志级别
        """
        if not self.enabled:
            return

        payload = {
            "type": "grid",
            "timestamp": datetime.now().isoformat(),
            "symbol": symbol,
            "action": action,
            **(details or {}),
        }

        self.send_event(
            event="grid",
            level=level,
            blobs=[symbol, action],
            payload=payload,
        )

    def send_cycle_event(
        self,
        symbol: str,
        phase: str,
        details: dict[str, Any] | None = None,
        level: str = "info",
    ) -> None:
        """
        发送交易周期事件（周期开始/结束/跳过等）

        Args:
            symbol: 交易对
            phase: 周期阶段 (start/end/skip/error)
            details: 详情数据
            level: 日志级别
        """
        if not self.enabled:
            return

        payload = {
            "type": "cycle",
            "timestamp": datetime.now().isoformat(),
            "symbol": symbol,
            "phase": phase,
            **(details or {}),
        }

        self.send_event(
            event="cycle",
            level=level,
            blobs=[symbol, phase],
            payload=payload,
        )

    def send_account_snapshot(
        self,
        balance: float,
        equity: float,
        unrealized_pnl: float = 0.0,
        positions: list[dict[str, Any]] | None = None,
        drawdown_pct: float = 0.0,
        daily_pnl: float = 0.0,
    ) -> None:
        """
        发送账户快照（定期记录账户状态，便于复盘分析）

        Args:
            balance: 账户余额
            equity: 账户权益
            unrealized_pnl: 未实现盈亏
            positions: 当前持仓列表
            drawdown_pct: 当前回撤百分比
            daily_pnl: 当日累计盈亏
        """
        if not self.enabled:
            return

        payload = {
            "type": "account_snapshot",
            "timestamp": datetime.now().isoformat(),
            "balance": balance,
            "equity": equity,
            "unrealized_pnl": unrealized_pnl,
            "positions": positions or [],
            "drawdown_pct": drawdown_pct,
            "daily_pnl": daily_pnl,
        }

        self.send_event(
            event="account",
            level="info",
            blobs=["snapshot"],
            doubles=[balance, equity, unrealized_pnl, drawdown_pct, daily_pnl],
            payload=payload,
        )

    def send_alert(
        self,
        symbol: str,
        alert_type: str,
        severity: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """
        发送告警事件（异常波动、API 故障等）

        Args:
            symbol: 交易对
            alert_type: 告警类型 (volatility/api_error/order_failed 等)
            severity: 严重程度 (elevated/high/extreme)
            message: 告警消息
            details: 详情数据
        """
        if not self.enabled:
            return

        level = "error" if severity in ("high", "extreme") else "warn"

        payload = {
            "type": "alert",
            "timestamp": datetime.now().isoformat(),
            "symbol": symbol,
            "alert_type": alert_type,
            "severity": severity,
            "message": message,
            **(details or {}),
        }

        self.send_event(
            event="alert",
            level=level,
            blobs=[symbol, alert_type, severity],
            payload=payload,
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

    def _enqueue(self, q: queue.Queue, item: Any) -> None:
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

    def _drain_queue(self, q: queue.Queue, max_items: int) -> list[Any]:
        """从队列中取出最多 max_items 条数据"""
        items: list[Any] = []
        while len(items) < max_items:
            try:
                items.append(q.get_nowait())
            except queue.Empty:
                break
        return items

    def _flush_ingest_queue(self) -> None:
        """刷新结构化事件队列（使用官方 SDK）"""
        while not self._ingest_queue.empty():
            points = self._drain_queue(self._ingest_queue, _MAX_BATCH_SIZE)
            if points:
                self._send_ingest(points)

    def _flush_log_queue(self) -> None:
        """刷新原始日志队列（使用官方 SDK）"""
        while not self._log_queue.empty():
            logs = self._drain_queue(self._log_queue, _MAX_BATCH_SIZE)
            if logs:
                self._send_logs(logs)

    def _send_ingest(self, points: list[DataPoint]) -> None:
        """发送结构化事件批次到 aepipe（带重试）"""
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                self._client.ingest(self.project, self.logstore, points)
                return  # 成功
            except ValidationError as e:
                # 客户端校验失败（如 blob 超限），记录后跳过不重试
                logger.warning("云端日志校验失败（数据问题，跳过）: %s", e)
                return
            except AepipeError as e:
                logger.warning(
                    "云端日志发送失败 (HTTP %d), 尝试 %d/%d: %s",
                    e.status,
                    attempt,
                    _MAX_RETRIES,
                    e.message,
                )
            except OSError as e:
                logger.debug(
                    "云端日志发送异常 (尝试 %d/%d): %s",
                    attempt,
                    _MAX_RETRIES,
                    e,
                )
        # 所有重试失败，静默放弃（不影响主流程）

    def _send_logs(self, logs: list[LogEntry]) -> None:
        """发送原始日志批次到 aepipe（带重试）"""
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                self._client.log(self.project, self.logstore, logs)
                return  # 成功
            except ValidationError as e:
                logger.warning("云端日志校验失败（数据问题，跳过）: %s", e)
                return
            except AepipeError as e:
                logger.warning(
                    "云端原始日志发送失败 (HTTP %d), 尝试 %d/%d: %s",
                    e.status,
                    attempt,
                    _MAX_RETRIES,
                    e.message,
                )
            except OSError as e:
                logger.debug(
                    "云端原始日志发送异常 (尝试 %d/%d): %s",
                    attempt,
                    _MAX_RETRIES,
                    e,
                )
        # 所有重试失败，静默放弃


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
    payload_ttl: int = _DEFAULT_PAYLOAD_TTL,
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
        payload_ttl: D1 payload 过期时间（秒）
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
                    payload_ttl=payload_ttl,
                    enabled=enabled,
                )
    return _cloud_logger
