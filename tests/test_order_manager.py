"""OrderManager / LimitOrderMonitor 测试：查询失败语义与交易锁互斥。"""

import threading

from src.trading.order_manager import LimitOrderMonitor


class FakeMonitorClient:
    """LimitOrderMonitor 依赖面的最小桩。"""

    def __init__(self):
        self.open_orders: list[dict] | None = []
        self.positions: list[dict] | None = []
        self.tpsl_calls: list[dict] = []
        self.close_calls: list[str] = []

    def get_open_orders(self, include_trigger=False):
        return None if self.open_orders is None else list(self.open_orders)

    def get_positions(self):
        return None if self.positions is None else list(self.positions)

    def place_tpsl_order(self, symbol, trigger_price, is_buy, size, is_tp=True, **kwargs):
        self.tpsl_calls.append({"symbol": symbol, "is_tp": is_tp, "size": size})
        return {
            "status": "ok",
            "response": {"type": "order", "data": {"statuses": [{"resting": {"oid": 77}}]}},
        }

    @staticmethod
    def check_order_success(result):
        from src.trading.client import HyperliquidClient

        return HyperliquidClient.check_order_success(result)

    def emergency_close_with_retry(self, symbol, size=None, *, reason, max_retries=3):
        self.close_calls.append(symbol)
        return True, {"status": "ok"}


def make_monitor(client=None, lock=None) -> tuple[LimitOrderMonitor, FakeMonitorClient]:
    client = client or FakeMonitorClient()
    monitor = LimitOrderMonitor(client, check_interval=0.01, trading_lock=lock)
    return monitor, client


def register_order(monitor: LimitOrderMonitor, order_id: int = 111) -> None:
    # 直接写入待监控表（绕过 add_order 以免启动后台线程干扰断言）
    monitor._pending_orders[order_id] = {
        "symbol": "ETH",
        "is_buy": True,
        "size": 0.5,
        "entry_price": 100.0,
        "take_profit_price": 105.0,
        "stop_loss_price": 98.0,
        "created_at": __import__("datetime").datetime.now(),
        "on_tpsl_set": None,
        "tpsl_attempts": 0,
    }


class TestQueryFailureSemantics:
    def test_orders_query_failure_keeps_order_monitored(self):
        # 挂单查询失败：绝不能把订单误判为「已成交/已取消」并移出监控
        monitor, client = make_monitor()
        register_order(monitor)
        client.open_orders = None
        monitor._check_orders()
        assert 111 in monitor._pending_orders
        assert client.tpsl_calls == []

    def test_positions_query_failure_keeps_order_monitored(self):
        monitor, client = make_monitor()
        register_order(monitor)
        client.open_orders = []  # 订单已不在挂单列表
        client.positions = None  # 但持仓查询失败 → 无法判定，保留监控
        monitor._check_orders()
        assert 111 in monitor._pending_orders

    def test_filled_order_gets_tpsl(self):
        monitor, client = make_monitor()
        register_order(monitor)
        client.open_orders = []
        client.positions = [{"coin": "ETH", "szi": "0.5"}]
        monitor._check_orders()
        assert len(client.tpsl_calls) == 2  # SL + TP
        assert 111 not in monitor._pending_orders

    def test_empty_queue_makes_no_api_calls(self):
        calls = []
        monitor, client = make_monitor()
        client.get_open_orders = lambda include_trigger=False: calls.append(1) or []
        monitor._check_orders()
        assert calls == []  # 空队列直接返回，不打 API


class TestTradingLockMutex:
    def test_lock_held_during_account_operations(self):
        # 监控线程操作账户时必须持有引擎交易锁
        lock = threading.Lock()
        held_during_query: list[bool] = []
        monitor, client = make_monitor(lock=lock)
        register_order(monitor)

        original = client.get_open_orders
        client.get_open_orders = lambda include_trigger=False: (
            held_during_query.append(lock.locked()),
            original(include_trigger),
        )[1]
        client.open_orders = []
        client.positions = [{"coin": "ETH", "szi": "0.5"}]
        monitor._check_orders()
        assert held_during_query == [True]
        assert not lock.locked()  # 结束后释放

    def test_busy_lock_skips_round(self):
        # 交易周期持锁期间：监控线程拿不到锁必须整轮跳过，不碰账户
        class NeverLock:
            def acquire(self, timeout=None):
                return False

            def release(self):
                raise AssertionError("未获取锁不应释放")

        monitor, client = make_monitor(lock=NeverLock())
        register_order(monitor)
        client.open_orders = []
        client.positions = [{"coin": "ETH", "szi": "0.5"}]
        monitor._check_orders()
        assert client.tpsl_calls == []
        assert 111 in monitor._pending_orders
