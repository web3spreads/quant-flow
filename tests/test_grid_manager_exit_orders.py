import copy
import tempfile
import time
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
        self.cancel_fail_oids = set()

    def get_open_orders(self, include_trigger=False):
        if include_trigger:
            return copy.deepcopy(self.open_orders)

        limit_orders = []
        for order in self.open_orders:
            order_type = order.get("orderType", {})
            if isinstance(order_type, dict) and "trigger" in order_type:
                continue
            limit_orders.append(order)
        return copy.deepcopy(limit_orders)

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
                "orderType": {"limit": {"tif": "Gtc"}},
            }
        )
        return {
            "status": "ok",
            "response": {"data": {"statuses": [{"resting": {"oid": oid}}]}},
        }

    def cancel_order(self, symbol, oid):
        self.cancel_calls.append((symbol, oid))
        if oid in self.cancel_fail_oids:
            return {"status": "error", "message": "simulated cancel failure"}
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

    def execute_long_limit(
        self,
        symbol,
        usdt_amount,
        limit_price,
        tp_ratio=None,
        sl_ratio=None,
        with_take_profit=True,
        with_stop_loss=True,
    ):
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

    def execute_short_limit(
        self,
        symbol,
        usdt_amount,
        limit_price,
        tp_ratio=None,
        sl_ratio=None,
        with_take_profit=True,
        with_stop_loss=True,
    ):
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
            {
                "oid": 1,
                "coin": "ETH",
                "side": "B",
                "sz": "0.1",
                "limitPx": "98.0",
                "orderType": {"limit": {"tif": "Gtc"}},
            },
            {
                "oid": 2,
                "coin": "ETH",
                "side": "B",
                "sz": "0.1",
                "limitPx": "97.0",
                "orderType": {"limit": {"tif": "Gtc"}},
            },
            {
                "oid": 3,
                "coin": "ETH",
                "side": "B",
                "sz": "0.1",
                "limitPx": "96.0",
                "orderType": {"limit": {"tif": "Gtc"}},
            },
            {
                "oid": 4,
                "coin": "ETH",
                "side": "B",
                "sz": "0.1",
                "limitPx": "95.0",
                "orderType": {"limit": {"tif": "Gtc"}},
            },
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
            {
                "oid": 101,
                "coin": "ETH",
                "side": "B",
                "sz": "0.1",
                "limitPx": "99.0",
                "orderType": {"limit": {"tif": "Gtc"}},
            },
            {
                "oid": 102,
                "coin": "ETH",
                "side": "A",
                "sz": "0.1",
                "limitPx": "101.0",
                "orderType": {"limit": {"tif": "Gtc"}},
            },
            {
                "oid": 103,
                "coin": "BTC",
                "side": "B",
                "sz": "0.1",
                "limitPx": "99000.0",
                "orderType": {"limit": {"tif": "Gtc"}},
            },
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

    def test_cancel_all_orders_also_cancels_trigger_orders(self):
        client = FakeClient()
        client.open_orders = [
            {
                "oid": 201,
                "coin": "ETH",
                "side": "A",
                "sz": "0.2",
                "limitPx": "101.0",
                "orderType": {"limit": {"tif": "Gtc"}},
            },
            {
                "oid": 202,
                "coin": "ETH",
                "side": "A",
                "sz": "0.2",
                "limitPx": "95.0",
                "orderType": {"trigger": {"tpsl": "sl", "triggerPx": 95.0}},
            },
            {
                "oid": 203,
                "coin": "BTC",
                "side": "A",
                "sz": "0.2",
                "limitPx": "95000.0",
                "orderType": {"trigger": {"tpsl": "sl", "triggerPx": 95000.0}},
            },
        ]
        order_manager = FakeOrderManager(client, positions=[{"coin": "ETH", "szi": "1.0"}])
        grid_manager = GridManager(
            order_manager=order_manager,
            logger=DummyLogger(),
            state_file=self.state_file,
        )

        grid_manager._cancel_all_orders("ETH")

        canceled_ids = [oid for symbol, oid in client.cancel_calls if symbol == "ETH"]
        self.assertEqual(set(canceled_ids), {201, 202})

    def test_cancel_all_orders_returns_false_when_cancel_failed(self):
        client = FakeClient()
        client.open_orders = [
            {
                "oid": 211,
                "coin": "ETH",
                "side": "A",
                "sz": "0.2",
                "limitPx": "101.0",
                "orderType": {"limit": {"tif": "Gtc"}},
            },
            {
                "oid": 212,
                "coin": "ETH",
                "side": "A",
                "sz": "0.2",
                "limitPx": "95.0",
                "orderType": {"trigger": {"tpsl": "sl", "triggerPx": 95.0}},
            },
        ]
        client.cancel_fail_oids.add(212)
        order_manager = FakeOrderManager(client, positions=[{"coin": "ETH", "szi": "1.0"}])
        grid_manager = GridManager(
            order_manager=order_manager,
            logger=DummyLogger(),
            state_file=self.state_file,
        )

        canceled_ok = grid_manager._cancel_all_orders("ETH")

        self.assertFalse(canceled_ok)
        self.assertTrue(any(oid == 212 for _, oid in client.cancel_calls))

    def test_cleanup_orphan_trigger_orders_cancels_trigger_when_no_position(self):
        client = FakeClient()
        client.open_orders = [
            {
                "oid": 301,
                "coin": "ETH",
                "side": "A",
                "sz": "0.2",
                "limitPx": "99.0",
                "orderType": {"trigger": {"tpsl": "sl", "triggerPx": 99.0}},
            },
            {
                "oid": 302,
                "coin": "ETH",
                "side": "A",
                "sz": "0.2",
                "limitPx": "101.0",
                "orderType": {"trigger": {"tpsl": "tp", "triggerPx": 101.0}},
            },
        ]
        order_manager = FakeOrderManager(client, positions=[{"coin": "ETH", "szi": "0"}])
        grid_manager = GridManager(
            order_manager=order_manager,
            logger=DummyLogger(),
            state_file=self.state_file,
        )

        grid_manager._cleanup_orphan_trigger_orders("ETH")

        canceled_ids = [oid for symbol, oid in client.cancel_calls if symbol == "ETH"]
        self.assertEqual(set(canceled_ids), {301, 302})

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

    def test_keep_grid_with_reduce_only_switch_off_does_not_add_exit_orders(self):
        client = FakeClient()
        order_manager = FakeOrderManager(client, positions=[{"coin": "ETH", "szi": "1.0"}])
        grid_manager = GridManager(
            order_manager=order_manager,
            logger=DummyLogger(),
            state_file=self.state_file,
            grid_limit_order_take_profit_enabled=False,
            grid_limit_order_stop_loss_enabled=True,
            grid_reduce_only_exit_orders_enabled=False,
        )

        grid_manager.sync_grid("ETH", {"action": "KEEP_GRID"})

        self.assertEqual(len(client.place_limit_calls), 0)

    def test_update_grid_skips_rebuild_when_delta_is_small(self):
        client = FakeClient()
        client.open_orders = [
            {"oid": 701, "coin": "ETH", "side": "B", "sz": "0.1", "limitPx": "98.0"},
            {"oid": 702, "coin": "ETH", "side": "A", "sz": "0.1", "limitPx": "102.0"},
        ]
        order_manager = FakeOrderManager(client, positions=[])
        grid_manager = GridManager(
            order_manager=order_manager,
            logger=DummyLogger(),
            state_file=self.state_file,
            grid_rebuild_cooldown_seconds=900,
            grid_rebuild_min_price_change_ratio=0.004,
        )
        grid_manager.state["active_grids"]["ETH"] = {
            "config": {
                "action": "UPDATE_GRID",
                "lower_price": 95.0,
                "upper_price": 105.0,
                "grid_num": 6,
                "amount_per_grid": 10.0,
                "mode": "NEUTRAL",
            },
            "buy_orders": [{"oid": 701, "px": 98.0}],
            "sell_orders": [{"oid": 702, "px": 102.0}],
            "last_sync": time.time(),
        }

        grid_manager.sync_grid(
            "ETH",
            {
                "action": "UPDATE_GRID",
                "lower_price": 95.2,
                "upper_price": 105.3,
                "grid_num": 6,
                "amount_per_grid": 10.0,
                "mode": "NEUTRAL",
            },
        )

        self.assertEqual(len(client.cancel_calls), 0)
        self.assertEqual(len(order_manager.long_calls), 0)
        self.assertEqual(len(order_manager.short_calls), 0)

    def test_update_grid_rebuilds_when_open_orders_are_insufficient(self):
        client = FakeClient()
        client.open_orders = [
            {"oid": 801, "coin": "ETH", "side": "B", "sz": "0.1", "limitPx": "98.0"},
        ]
        order_manager = FakeOrderManager(client, positions=[])
        grid_manager = GridManager(
            order_manager=order_manager,
            logger=DummyLogger(),
            state_file=self.state_file,
            grid_rebuild_cooldown_seconds=900,
            grid_rebuild_min_price_change_ratio=0.004,
        )
        grid_manager.state["active_grids"]["ETH"] = {
            "config": {
                "action": "UPDATE_GRID",
                "lower_price": 95.0,
                "upper_price": 105.0,
                "grid_num": 1,
                "amount_per_grid": 10.0,
                "mode": "NEUTRAL",
            },
            "buy_orders": [{"oid": 801, "px": 98.0}],
            "sell_orders": [],
            "last_sync": time.time(),
        }

        grid_manager.sync_grid(
            "ETH",
            {
                "action": "UPDATE_GRID",
                "lower_price": 95.2,
                "upper_price": 105.3,
                "grid_num": 1,
                "amount_per_grid": 10.0,
                "mode": "NEUTRAL",
            },
        )

        canceled_ids = [oid for symbol, oid in client.cancel_calls if symbol == "ETH"]
        self.assertIn(801, canceled_ids)
        self.assertGreaterEqual(len(order_manager.long_calls), 1)

    def test_extract_oid_supports_filled_status(self):
        client = FakeClient()
        order_manager = FakeOrderManager(client, positions=[])
        grid_manager = GridManager(
            order_manager=order_manager,
            logger=DummyLogger(),
            state_file=self.state_file,
        )

        oid = grid_manager._extract_oid(
            {
                "status": "ok",
                "response": {
                    "data": {
                        "statuses": [
                            {"filled": {"oid": 123456, "totalSz": "0.1", "avgPx": "100.0"}}
                        ]
                    }
                },
            }
        )
        self.assertEqual(oid, 123456)


if __name__ == "__main__":
    unittest.main()
