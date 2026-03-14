import copy
import tempfile
import unittest
from pathlib import Path

from src.trading.grid_manager import GridManager


class DummyLogger:
    def print_section(self, *args, **kwargs):
        pass

    def print_info(self, *args, **kwargs):
        pass

    def print_warning(self, *args, **kwargs):
        pass

    def print_error(self, *args, **kwargs):
        pass


class FakeClient:
    def __init__(self):
        self.current_price = 100.0
        self.open_orders = []
        self.place_limit_calls = []
        self.cancel_calls = []

    def get_open_orders(self):
        return copy.deepcopy(self.open_orders)

    def get_current_price(self, symbol):
        return self.current_price

    def format_price(self, symbol, price):
        return round(float(price), 1)

    def get_asset_info(self, symbol):
        return {"szDecimals": 3}

    def place_limit_order(self, symbol, is_buy, size, price, reduce_only=False):
        self.place_limit_calls.append(
            {
                "symbol": symbol,
                "is_buy": is_buy,
                "size": size,
                "price": price,
                "reduce_only": reduce_only,
            }
        )
        oid = 9000 + len(self.place_limit_calls)
        self.open_orders.append(
            {
                "oid": oid,
                "coin": symbol,
                "side": "B" if is_buy else "A",
                "sz": str(size),
                "limitPx": str(price),
            }
        )
        return {
            "status": "ok",
            "response": {"data": {"statuses": [{"resting": {"oid": oid}}]}},
        }

    def cancel_order(self, symbol, oid):
        self.cancel_calls.append((symbol, oid))
        self.open_orders = [o for o in self.open_orders if o.get("oid") != oid]
        return {"status": "ok"}


class FakeOrderManager:
    def __init__(self, client, positions=None):
        self.client = client
        self.positions = positions or []
        self.long_calls = []
        self.short_calls = []
        self._oid_seed = 20000
        self.default_leverage = 10

    def get_current_positions(self):
        return copy.deepcopy(self.positions)

    def _next_oid(self):
        self._oid_seed += 1
        return self._oid_seed

    def execute_long_limit(self, symbol, usdt_amount, limit_price, tp_ratio=None, sl_ratio=None):
        self.long_calls.append((symbol, usdt_amount, limit_price))
        oid = self._next_oid()
        self.client.open_orders.append(
            {
                "oid": oid,
                "coin": symbol,
                "side": "B",
                "sz": "0.1",
                "limitPx": str(limit_price),
            }
        )
        return {
            "success": True,
            "limit_order": {"response": {"data": {"statuses": [{"resting": {"oid": oid}}]}}},
        }

    def execute_short_limit(self, symbol, usdt_amount, limit_price, tp_ratio=None, sl_ratio=None):
        self.short_calls.append((symbol, usdt_amount, limit_price))
        oid = self._next_oid()
        self.client.open_orders.append(
            {
                "oid": oid,
                "coin": symbol,
                "side": "A",
                "sz": "0.1",
                "limitPx": str(limit_price),
            }
        )
        return {
            "success": True,
            "limit_order": {"response": {"data": {"statuses": [{"resting": {"oid": oid}}]}}},
        }


class TestGridManagerExitOrders(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.state_file = str(Path(self.tmp_dir.name) / "grid_state.json")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_ensure_min_orders_adds_reduce_only_exit_orders_for_existing_long_position(self):
        client = FakeClient()
        client.open_orders = [
            {"oid": 1, "coin": "ETH", "side": "B", "sz": "0.1", "limitPx": "98.0"},
            {"oid": 2, "coin": "ETH", "side": "B", "sz": "0.1", "limitPx": "97.0"},
            {"oid": 3, "coin": "ETH", "side": "B", "sz": "0.1", "limitPx": "96.0"},
            {"oid": 4, "coin": "ETH", "side": "B", "sz": "0.1", "limitPx": "95.0"},
        ]
        order_manager = FakeOrderManager(client, positions=[{"coin": "ETH", "szi": "1.2"}])
        grid_manager = GridManager(
            order_manager=order_manager,
            logger=DummyLogger(),
            state_file=self.state_file,
        )

        grid_manager._ensure_min_orders(symbol="ETH", min_orders=4, amount_per_order=10.0)

        reduce_only_sell_calls = [
            c
            for c in client.place_limit_calls
            if c["reduce_only"] and c["symbol"] == "ETH" and not c["is_buy"]
        ]
        self.assertGreaterEqual(len(reduce_only_sell_calls), 1)
        self.assertEqual(len(order_manager.long_calls), 0)
        self.assertEqual(len(order_manager.short_calls), 0)
        self.assertGreaterEqual(len(grid_manager.state["active_grids"]["ETH"]["sell_orders"]), 1)

    def test_ensure_min_orders_no_position_does_not_add_base_open_orders(self):
        client = FakeClient()
        order_manager = FakeOrderManager(client, positions=[{"coin": "ETH", "szi": "0"}])
        grid_manager = GridManager(
            order_manager=order_manager,
            logger=DummyLogger(),
            state_file=self.state_file,
        )

        grid_manager._ensure_min_orders(symbol="ETH", min_orders=4, amount_per_order=10.0)

        self.assertEqual(len(client.place_limit_calls), 0)
        self.assertEqual(len(order_manager.long_calls), 0)
        self.assertEqual(len(order_manager.short_calls), 0)

    def test_cancel_all_orders_cancels_exchange_orders_even_without_local_state(self):
        client = FakeClient()
        client.open_orders = [
            {"oid": 101, "coin": "ETH", "side": "B", "sz": "0.1", "limitPx": "99.0"},
            {"oid": 102, "coin": "ETH", "side": "A", "sz": "0.1", "limitPx": "101.0"},
            {"oid": 103, "coin": "BTC", "side": "B", "sz": "0.1", "limitPx": "99000.0"},
        ]
        order_manager = FakeOrderManager(client, positions=[])
        grid_manager = GridManager(
            order_manager=order_manager,
            logger=DummyLogger(),
            state_file=self.state_file,
        )

        grid_manager._cancel_all_orders("ETH")

        canceled_ids = [oid for symbol, oid in client.cancel_calls if symbol == "ETH"]
        self.assertEqual(set(canceled_ids), {101, 102})

    def test_exit_orders_are_filled_by_coverage_ratio_not_only_count(self):
        client = FakeClient()
        client.open_orders = [
            {"oid": 1, "coin": "ETH", "side": "A", "sz": "0.1", "limitPx": "101.0"},
            {"oid": 2, "coin": "ETH", "side": "A", "sz": "0.1", "limitPx": "102.0"},
            {"oid": 3, "coin": "ETH", "side": "A", "sz": "0.1", "limitPx": "103.0"},
            {"oid": 4, "coin": "ETH", "side": "B", "sz": "0.1", "limitPx": "99.0"},
        ]
        # 多仓 1.0，但已有减仓卖单仅覆盖 0.3（层数够，覆盖不足）
        order_manager = FakeOrderManager(client, positions=[{"coin": "ETH", "szi": "1.0"}])
        grid_manager = GridManager(
            order_manager=order_manager,
            logger=DummyLogger(),
            state_file=self.state_file,
        )

        grid_manager._ensure_min_orders(symbol="ETH", min_orders=4, amount_per_order=10.0)

        reduce_only_sell_calls = [
            c
            for c in client.place_limit_calls
            if c["reduce_only"] and c["symbol"] == "ETH" and not c["is_buy"]
        ]
        self.assertGreaterEqual(len(reduce_only_sell_calls), 1)

    def test_with_position_only_adds_reduce_only_exit_orders(self):
        client = FakeClient()
        # 有多仓时，只应补 reduce_only 的减仓卖单
        order_manager = FakeOrderManager(client, positions=[{"coin": "ETH", "szi": "5.0"}])
        grid_manager = GridManager(
            order_manager=order_manager,
            logger=DummyLogger(),
            state_file=self.state_file,
        )

        grid_manager._ensure_min_orders(symbol="ETH", min_orders=4, amount_per_order=10.0)

        reduce_only_sell_calls = [
            c
            for c in client.place_limit_calls
            if c["reduce_only"] and c["symbol"] == "ETH" and not c["is_buy"]
        ]
        self.assertGreaterEqual(len(reduce_only_sell_calls), 1)
        self.assertEqual(len(order_manager.long_calls), 0)
        self.assertEqual(len(order_manager.short_calls), 0)


if __name__ == "__main__":
    unittest.main()
