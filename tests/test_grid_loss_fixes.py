"""网格趋势亏损根因修复测试。

覆盖四项修复：
1. 库存硬上限（``_would_exceed_inventory_cap`` + ``_place_open_order`` 拦截）
2. 趋势过滤平逆势库存（``flatten_adverse_inventory``）
3. Triple Barrier 每轮必查（``check_barrier`` 独立方法触发紧急平仓）
4. 市价/紧急平仓上报连亏熔断（``_emergency_close_all`` → ``on_round_trip_close``）
5. 强趋势识别（``QuantFlowBot._detect_strong_trend`` 票数逻辑）
"""

import tempfile
import time
import types
import unittest
from decimal import Decimal
from pathlib import Path

from src.trading.client import HyperliquidClient
from src.trading.grid_barrier import GridBarrierMonitor, TripleBarrierConfig
from src.trading.grid_manager import GridManager
from src.trading.grid_pnl import GridPnLTracker
from src.utils.grid_math import GridLevel, GridLevelState


class DummyLogger:
    def print_section(self, *a, **k):
        pass

    def print_info(self, *a, **k):
        pass

    def print_warning(self, *a, **k):
        pass

    def print_error(self, *a, **k):
        pass

    def print_header(self, *a, **k):
        pass

    def log_trade(self, *a, **k):
        pass

    def log_decision(self, *a, **k):
        pass


class _Info:
    def user_fills(self, addr):
        return []


class FakeClient:
    def __init__(self, price=1800.0):
        self.price = price
        self.open_orders = []
        self.closed = []
        self.address = "0xTEST"
        self.info = _Info()

    def get_current_price(self, symbol):
        return self.price

    def get_open_orders(self, include_trigger=False):
        return list(self.open_orders)

    def cancel_order(self, symbol, oid):
        self.open_orders = [o for o in self.open_orders if o.get("oid") != oid]
        return {"status": "ok"}

    def close_position(self, symbol):
        self.closed.append(symbol)
        return {"status": "ok"}

    def format_price(self, symbol, price):
        return round(float(price), 1)

    def get_asset_info(self, symbol):
        return {"szDecimals": 3}

    check_order_success = staticmethod(HyperliquidClient.check_order_success)


class FakeOM:
    def __init__(self, client, positions=None):
        self.client = client
        self.positions = positions or []
        self.long_calls = []
        self.short_calls = []

    def get_current_positions(self):
        return list(self.positions)

    def execute_long_limit(self, symbol, amount, price, **kwargs):
        self.long_calls.append((symbol, amount, price))
        return {
            "success": True,
            "limit_order": {"response": {"data": {"statuses": [{"resting": {"oid": 1}}]}}},
        }

    def execute_short_limit(self, symbol, amount, price, **kwargs):
        self.short_calls.append((symbol, amount, price))
        return {
            "success": True,
            "limit_order": {"response": {"data": {"statuses": [{"resting": {"oid": 2}}]}}},
        }


def _make_gm(positions=None, price=1800.0, cap=0.0, on_close=None):
    tmp = tempfile.TemporaryDirectory()
    state_file = str(Path(tmp.name) / "grid_state.json")
    client = FakeClient(price=price)
    om = FakeOM(client, positions=positions)
    gm = GridManager(
        om,
        DummyLogger(),
        state_file=state_file,
        max_position_notional_usd=cap,
        on_round_trip_close=on_close,
    )
    gm._tmp = tmp  # 防止 GC 删除临时目录
    return gm, om, client


class TestInventoryCap(unittest.TestCase):
    """库存硬上限守卫"""

    def test_disabled_when_cap_zero(self):
        gm, _, _ = _make_gm(positions=[{"coin": "ETH", "szi": -1.0}], price=1800, cap=0.0)
        # cap=0 关闭：任何方向都不拦
        self.assertFalse(gm._would_exceed_inventory_cap("ETH", is_buy_open=True))
        self.assertFalse(gm._would_exceed_inventory_cap("ETH", is_buy_open=False))

    def test_no_block_when_flat(self):
        gm, _, _ = _make_gm(positions=[], price=1800, cap=30.0)
        self.assertFalse(gm._would_exceed_inventory_cap("ETH", is_buy_open=False))

    def test_blocks_same_direction_when_over_cap(self):
        # 净空头 0.02 * 1800 = $36 名义额 > cap $30
        gm, _, _ = _make_gm(positions=[{"coin": "ETH", "szi": -0.02}], price=1800, cap=30.0)
        # 卖开仓=加空 → 同向加仓，拦截
        self.assertTrue(gm._would_exceed_inventory_cap("ETH", is_buy_open=False))
        # 买开仓=减空 → 放行
        self.assertFalse(gm._would_exceed_inventory_cap("ETH", is_buy_open=True))

    def test_allows_when_under_cap(self):
        # 净空头 0.01 * 1800 = $18 < cap $30
        gm, _, _ = _make_gm(positions=[{"coin": "ETH", "szi": -0.01}], price=1800, cap=30.0)
        self.assertFalse(gm._would_exceed_inventory_cap("ETH", is_buy_open=False))

    def test_blocks_long_over_cap(self):
        # 净多头超限：买开仓加多被拦，卖开仓减多放行
        gm, _, _ = _make_gm(positions=[{"coin": "ETH", "szi": 0.05}], price=1800, cap=30.0)
        self.assertTrue(gm._would_exceed_inventory_cap("ETH", is_buy_open=True))
        self.assertFalse(gm._would_exceed_inventory_cap("ETH", is_buy_open=False))

    def test_place_open_order_skips_when_over_cap(self):
        gm, om, _ = _make_gm(positions=[{"coin": "ETH", "szi": -0.02}], price=1800, cap=30.0)
        # 卖开仓层（SHORT），价格高于市价以通过价格守卫
        level = GridLevel(
            id="L0",
            price=Decimal("1850"),
            amount=Decimal("20"),
            side="SHORT",
            state=GridLevelState.IDLE,
        )
        gm.grid_levels["ETH"] = [level]
        gm.state["active_grids"]["ETH"] = {
            "config": {"parameters": {"tp_ratio": 0.005, "sl_ratio": 0.01}}
        }
        gm._place_open_order("ETH", level)
        # 库存超限 → 不应挂卖开仓单，层级仍为 IDLE
        self.assertEqual(len(om.short_calls), 0)
        self.assertEqual(level.state, GridLevelState.IDLE)


class TestFlattenAdverse(unittest.TestCase):
    """趋势过滤：平逆势库存"""

    def test_flattens_short_in_uptrend(self):
        gm, _, client = _make_gm(positions=[{"coin": "ETH", "szi": -0.02}], price=1900)
        # 给一个 OPEN_FILLED 空头层，让 _emergency_close_all 有数据
        gm.grid_levels["ETH"] = [_filled_short_level()]
        gm.pnl_trackers["ETH"] = GridPnLTracker()
        flattened = gm.flatten_adverse_inventory("ETH", trend_dir=1)  # 上涨 + 持空 = 逆势
        self.assertTrue(flattened)
        self.assertIn("ETH", client.closed)  # 市价平仓被调用

    def test_no_flatten_when_aligned(self):
        gm, _, client = _make_gm(positions=[{"coin": "ETH", "szi": -0.02}], price=1700)
        # 下跌 + 持空 = 顺势，不平
        self.assertFalse(gm.flatten_adverse_inventory("ETH", trend_dir=-1))
        self.assertEqual(client.closed, [])

    def test_no_flatten_when_flat(self):
        gm, _, client = _make_gm(positions=[], price=1800)
        self.assertFalse(gm.flatten_adverse_inventory("ETH", trend_dir=1))
        self.assertEqual(client.closed, [])


class TestCheckBarrier(unittest.TestCase):
    """Triple Barrier 每轮必查 + 紧急平仓上报连亏熔断"""

    def test_barrier_triggers_and_reports_loss(self):
        reported = []
        gm, _, client = _make_gm(
            positions=[{"coin": "ETH", "szi": -0.02}],
            price=1900,
            on_close=lambda sym, pnl: reported.append((sym, pnl)),
        )
        # 空头 @1800，现价 1900 → 浮亏；总投入 $36，亏 ~$2 → -5.6% <= -5% 触发 STOP_LOSS
        gm.grid_levels["ETH"] = [_filled_short_level()]
        gm.pnl_trackers["ETH"] = GridPnLTracker()
        gm.barrier_monitors["ETH"] = GridBarrierMonitor(
            config=TripleBarrierConfig(stop_loss_pct=Decimal("0.05")),
            start_time=time.time(),
        )
        triggered = gm.check_barrier("ETH")
        self.assertTrue(triggered)
        self.assertIn("ETH", client.closed)
        # 紧急平仓的亏损应上报连亏熔断
        self.assertEqual(len(reported), 1)
        self.assertLess(reported[0][1], 0)  # 上报的是负盈亏

    def test_barrier_no_trigger_when_safe(self):
        gm, _, client = _make_gm(positions=[{"coin": "ETH", "szi": -0.02}], price=1801)
        gm.grid_levels["ETH"] = [_filled_short_level()]  # 空头 @1800，现价 1801 几乎无亏
        gm.pnl_trackers["ETH"] = GridPnLTracker()
        gm.barrier_monitors["ETH"] = GridBarrierMonitor(
            config=TripleBarrierConfig(
                stop_loss_pct=Decimal("0.05"), take_profit_pct=None, time_limit_seconds=None
            ),
            start_time=time.time(),
        )
        self.assertFalse(gm.check_barrier("ETH"))
        self.assertEqual(client.closed, [])

    def test_barrier_noop_without_levels(self):
        gm, _, _ = _make_gm(positions=[], price=1800)
        self.assertFalse(gm.check_barrier("ETH"))


class TestDetectStrongTrend(unittest.TestCase):
    """强趋势识别票数逻辑（QuantFlowBot._detect_strong_trend）"""

    def _detect(self, trends, votes=3):
        from main import QuantFlowBot

        stub = types.SimpleNamespace(
            config=types.SimpleNamespace(
                grid_trend_filter_min_votes=votes,
                # None = 全部周期参与计票（历史行为）
                grid_trend_filter_timeframes=None,
            )
        )
        return QuantFlowBot._detect_strong_trend(stub, trends)

    def test_strong_up(self):
        trends = {"日线": "强势上涨", "4小时": "强势上涨", "1小时": "强势上涨", "15分钟": "震荡"}
        self.assertEqual(self._detect(trends), 1)

    def test_strong_down(self):
        trends = {
            "日线": "强势下跌",
            "4小时": "强势下跌",
            "1小时": "强势下跌",
            "15分钟": "下跌转强",
        }
        self.assertEqual(self._detect(trends), -1)

    def test_no_trend_when_choppy(self):
        trends = {"日线": "震荡", "4小时": "上涨转弱", "1小时": "强势上涨", "15分钟": "强势下跌"}
        self.assertEqual(self._detect(trends), 0)

    def test_below_vote_threshold(self):
        trends = {"日线": "强势上涨", "4小时": "强势上涨", "1小时": "震荡"}
        self.assertEqual(self._detect(trends, votes=3), 0)

    def test_empty(self):
        self.assertEqual(self._detect({}), 0)


def _filled_short_level():
    level = GridLevel(
        id="L0",
        price=Decimal("1850"),
        amount=Decimal("36"),
        side="SHORT",
        state=GridLevelState.OPEN_FILLED,
    )
    level.open_fill_price = Decimal("1800")
    level.open_fill_amount = Decimal("0.02")
    return level


if __name__ == "__main__":
    unittest.main()
