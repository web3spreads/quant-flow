"""网格防护体系重构测试（基于 12.5 天线上实证的缺陷修复）。

覆盖七项改造：
1. 趋势计票周期白名单（``detect_strong_trend`` 排除 1m 等噪声周期）
2. 趋势迟滞确认（``TrendConfirmTracker``：暂停先行、平仓靠后、翻转清零）
3. 自适应仓位（``calculate_grid_config``：减格数而非抬单格金额、INSUFFICIENT_CAPITAL、
   历史路径钳制行为保持不变）
4. 连亏熔断 forced 语义（强平净盈利不重置计数——线上 145 次强平 0 次熔断的根因）
5. 库存上限严格模式（计入同向挂单、取价失败 fail-closed；宽松模式行为不变）
6. 手术式减仓（只平超限逆势层级、保留网格状态、forced=True 上报）
7. KEEP_GRID 对账（撤无主残单、放过 reduce_only 与已知订单）
8. 回撤保护绝对额下限（min_drawdown_usd 防小账户噪声级触发）
"""

import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest import mock

from src.data.indicators import TrendConfirmTracker, detect_strong_trend
from src.plugins.protections.base import ProtectionContext
from src.plugins.protections.consecutive_loss import ConsecutiveLossProtection
from src.plugins.protections.drawdown import MaxDrawdownProtection
from src.trading.client import HyperliquidClient
from src.trading.grid_manager import GridManager
from src.utils.grid_math import GridLevel, GridLevelState, calculate_grid_config


class DummyLogger:
    def __init__(self):
        self.trades = []

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
        self.trades.append(k)

    def log_decision(self, *a, **k):
        pass


class _Info:
    def user_fills(self, addr):
        return []


class FakeClient:
    """支持部分平仓与可注入取价异常的假客户端。"""

    def __init__(self, price=1800.0):
        self.price = price
        self.open_orders = []
        self.closed = []  # [(symbol, size|None)]
        self.address = "0xTEST"
        self.info = _Info()
        self.price_error = False

    def get_current_price(self, symbol):
        if self.price_error:
            raise RuntimeError("模拟取价失败")
        return self.price

    def get_open_orders(self, include_trigger=False):
        return list(self.open_orders)

    def cancel_order(self, symbol, oid):
        self.open_orders = [o for o in self.open_orders if o.get("oid") != oid]
        return {"status": "ok"}

    def close_position(self, symbol, size=None):
        self.closed.append((symbol, size))
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

    def get_current_positions(self):
        return list(self.positions)


def _make_gm(
    positions=None,
    price=1800.0,
    cap=0.0,
    on_close=None,
    surgical=False,
    cap_strict=False,
    reconcile=False,
):
    tmp = tempfile.TemporaryDirectory()
    state_file = str(Path(tmp.name) / "grid_state.json")
    client = FakeClient(price=price)
    om = FakeOM(client, positions=positions)
    logger = DummyLogger()
    gm = GridManager(
        om,
        logger,
        state_file=state_file,
        max_position_notional_usd=cap,
        on_round_trip_close=on_close,
        trend_flatten_surgical=surgical,
        inventory_cap_strict=cap_strict,
        keep_grid_reconcile=reconcile,
    )
    gm._tmp = tmp  # 防止 GC 删除临时目录
    return gm, om, client, logger


def _filled_level(level_id, side, entry, amount_base, state=GridLevelState.OPEN_FILLED):
    level = GridLevel(
        id=level_id,
        price=Decimal(str(entry)),
        amount=Decimal(str(float(entry) * float(amount_base))),
        side=side,
        state=state,
    )
    level.open_fill_price = Decimal(str(entry))
    level.open_fill_amount = Decimal(str(amount_base))
    return level


class TestDetectStrongTrendWhitelist(unittest.TestCase):
    """趋势计票周期白名单（排除噪声周期）"""

    TRENDS = {
        "日线": "震荡",
        "4小时": "震荡",
        "1小时": "强势上涨",
        "15分钟": "强势上涨",
        "1分钟": "强势上涨",
    }

    def test_none_whitelist_keeps_history_behavior(self):
        # 全周期参与：1h/15m/1m 三票 → 触发（历史行为）
        self.assertEqual(detect_strong_trend(self.TRENDS, min_votes=3), 1)

    def test_exclude_1m_blocks_noise_trigger(self):
        # 排除 1m 后只剩两票 → 不触发
        result = detect_strong_trend(
            self.TRENDS, min_votes=3, allowed_timeframes=["15m", "1h", "4h", "1d"]
        )
        self.assertEqual(result, 0)

    def test_chinese_keys_accepted(self):
        result = detect_strong_trend(
            self.TRENDS, min_votes=2, allowed_timeframes=["1小时", "15分钟"]
        )
        self.assertEqual(result, 1)

    def test_empty_trends(self):
        self.assertEqual(detect_strong_trend({}, min_votes=3), 0)
        self.assertEqual(detect_strong_trend(None, min_votes=3), 0)


class TestTrendConfirmTracker(unittest.TestCase):
    """趋势迟滞确认：暂停先行、平仓靠后、翻转清零"""

    def test_default_immediate(self):
        # 默认 1/1 = 历史行为：首个周期即生效且允许平仓
        tracker = TrendConfirmTracker()
        self.assertEqual(tracker.update(1), (1, True))

    def test_confirm_cycles_delays_action(self):
        tracker = TrendConfirmTracker(confirm_cycles=2, flatten_min_cycles=3)
        self.assertEqual(tracker.update(1), (0, False))  # 第 1 次：未确认
        self.assertEqual(tracker.update(1), (1, False))  # 第 2 次：生效但不允许平仓
        self.assertEqual(tracker.update(1), (1, True))  # 第 3 次：允许平仓

    def test_direction_flip_resets(self):
        tracker = TrendConfirmTracker(confirm_cycles=2, flatten_min_cycles=2)
        tracker.update(1)
        tracker.update(1)
        self.assertEqual(tracker.update(-1), (0, False))  # 翻转：计数从 1 重来
        self.assertEqual(tracker.update(-1), (-1, True))

    def test_zero_resets(self):
        tracker = TrendConfirmTracker(confirm_cycles=2, flatten_min_cycles=2)
        tracker.update(1)
        tracker.update(0)
        self.assertEqual(tracker.update(1), (0, False))  # 中断后重新累计

    def test_flatten_min_not_below_confirm(self):
        tracker = TrendConfirmTracker(confirm_cycles=3, flatten_min_cycles=1)
        self.assertEqual(tracker.flatten_min_cycles, 3)


class TestAdaptiveSizing(unittest.TestCase):
    """自适应仓位：减格数而非抬单格金额"""

    def test_legacy_path_unchanged(self):
        # 历史路径：$7.71 账户单格被硬抬到 $15.5（缺陷行为，必须保持以兼容线上配置）
        cfg = calculate_grid_config(1800.0, 7.71, grid_num=8, leverage=10)
        self.assertEqual(cfg["action"], "UPDATE_GRID")
        self.assertEqual(cfg["amount_per_grid"], 15.5)

    def test_adaptive_reduces_grid_num(self):
        # $50 × 10 × 0.4 = $200 额度，8 格单格 $25 ≥ $11 → 格数不变
        cfg = calculate_grid_config(1800.0, 50.0, grid_num=8, leverage=10, adaptive_sizing=True)
        self.assertEqual(cfg["grid_num"], 8)
        self.assertAlmostEqual(cfg["amount_per_grid"], 25.0, places=1)
        # $20 × 10 × 0.4 = $80 额度，8 格单格 $10 < $11 → 减到 7 格（$80/$11）
        cfg = calculate_grid_config(1800.0, 20.0, grid_num=8, leverage=10, adaptive_sizing=True)
        self.assertEqual(cfg["grid_num"], 7)
        self.assertGreaterEqual(cfg["amount_per_grid"], 11.0)

    def test_adaptive_insufficient_capital(self):
        # $7.71 × 10 × 0.4 = $30.8 额度，只够 2 格 < 最少 3 格 → 拒绝布单
        cfg = calculate_grid_config(1800.0, 7.71, grid_num=8, leverage=10, adaptive_sizing=True)
        self.assertEqual(cfg["action"], "INSUFFICIENT_CAPITAL")
        self.assertIn("required_balance", cfg)
        self.assertGreater(cfg["required_balance"], 7.71)

    def test_adaptive_respects_leverage(self):
        # 同样余额、杠杆 5：$20 × 5 × 0.4 = $40 → 只够 3 格
        cfg = calculate_grid_config(1800.0, 20.0, grid_num=8, leverage=5, adaptive_sizing=True)
        self.assertEqual(cfg["action"], "UPDATE_GRID")
        self.assertEqual(cfg["grid_num"], 3)

    def test_adaptive_total_notional_bounded(self):
        # 任意输出必须满足 格数×单格 ≤ 余额×杠杆×0.4（历史缺陷：$7.71 被放大成 $124）
        for balance in (15.0, 20.0, 50.0, 100.0, 500.0):
            cfg = calculate_grid_config(
                1800.0, balance, grid_num=8, leverage=10, adaptive_sizing=True
            )
            if cfg["action"] != "UPDATE_GRID":
                continue
            total = cfg["grid_num"] * cfg["amount_per_grid"]
            self.assertLessEqual(total, balance * 10 * 0.4 + 0.01, f"balance={balance}")


class TestConsecutiveLossForced(unittest.TestCase):
    """连亏熔断 forced 语义"""

    def _make(self, forced_no_reset):
        tmp = tempfile.TemporaryDirectory()
        plugin = ConsecutiveLossProtection(
            config={
                "max_consecutive_losses": 3,
                "forced_close_no_reset": forced_no_reset,
            },
            data_dir=Path(tmp.name),
        )
        plugin._tmp = tmp
        return plugin

    def test_default_behavior_unchanged(self):
        # 开关关闭：forced 盈利仍重置（历史行为）
        plugin = self._make(forced_no_reset=False)
        plugin.on_trade_close("ETH", -1.0)
        plugin.on_trade_close("ETH", -1.0)
        plugin.on_trade_close("ETH", 0.5, forced=True)
        self.assertEqual(plugin._global_losses, 0)

    def test_forced_profit_does_not_reset(self):
        # 开关开启：强平净盈利不重置也不递增（线上 145 次强平 0 次熔断的根因）
        plugin = self._make(forced_no_reset=True)
        plugin.on_trade_close("ETH", -1.0)
        plugin.on_trade_close("ETH", -1.0)
        plugin.on_trade_close("ETH", 0.5, forced=True)
        self.assertEqual(plugin._global_losses, 2)

    def test_forced_loss_still_counts(self):
        plugin = self._make(forced_no_reset=True)
        plugin.on_trade_close("ETH", -1.0, forced=True)
        plugin.on_trade_close("ETH", -1.0, forced=True)
        self.assertEqual(plugin._global_losses, 2)

    def test_voluntary_profit_still_resets(self):
        plugin = self._make(forced_no_reset=True)
        plugin.on_trade_close("ETH", -1.0)
        plugin.on_trade_close("ETH", 0.5)  # 主动止盈
        self.assertEqual(plugin._global_losses, 0)


class TestInventoryCapStrict(unittest.TestCase):
    """库存上限严格模式：计入同向挂单 + fail-closed"""

    def test_loose_mode_ignores_resting_orders(self):
        # 宽松模式（历史行为）：持仓 $18 < $40 上限 → 放行，无视挂单
        gm, om, client, _ = _make_gm(
            positions=[{"coin": "ETH", "szi": "0.01"}], price=1800.0, cap=40.0
        )
        client.open_orders = [
            {"oid": 1, "coin": "ETH", "side": "B", "limitPx": "1790", "sz": "0.02"},
        ]
        self.assertFalse(gm._would_exceed_inventory_cap("ETH", is_buy_open=True))

    def test_strict_counts_resting_same_direction(self):
        # 严格模式：持仓 $18 + 挂单 $35.8 = $53.8 ≥ $40 → 拦截买开仓
        gm, om, client, _ = _make_gm(
            positions=[{"coin": "ETH", "szi": "0.01"}], price=1800.0, cap=40.0, cap_strict=True
        )
        client.open_orders = [
            {"oid": 1, "coin": "ETH", "side": "B", "limitPx": "1790", "sz": "0.02"},
        ]
        self.assertTrue(gm._would_exceed_inventory_cap("ETH", is_buy_open=True))
        # 反方向（卖开仓）：持仓抵扣后敞口为负 → 放行
        self.assertFalse(gm._would_exceed_inventory_cap("ETH", is_buy_open=False))

    def test_strict_skips_reduce_only_orders(self):
        gm, om, client, _ = _make_gm(
            positions=[{"coin": "ETH", "szi": "0.01"}], price=1800.0, cap=40.0, cap_strict=True
        )
        client.open_orders = [
            {"oid": 1, "coin": "ETH", "side": "B", "limitPx": "1790", "sz": "0.02",
             "reduceOnly": True},
        ]
        # reduce_only 不计入 → 敞口只有持仓 $18 < $40 → 放行
        self.assertFalse(gm._would_exceed_inventory_cap("ETH", is_buy_open=True))

    def test_strict_fail_closed_on_price_error(self):
        gm, om, client, _ = _make_gm(
            positions=[{"coin": "ETH", "szi": "0.01"}], price=1800.0, cap=40.0, cap_strict=True
        )
        client.price_error = True
        self.assertTrue(gm._would_exceed_inventory_cap("ETH", is_buy_open=True))

    def test_loose_fail_open_on_price_error(self):
        # 宽松模式保持历史行为：取价失败放行
        gm, om, client, _ = _make_gm(
            positions=[{"coin": "ETH", "szi": "0.01"}], price=1800.0, cap=40.0
        )
        client.price_error = True
        self.assertFalse(gm._would_exceed_inventory_cap("ETH", is_buy_open=True))

    def test_strict_zero_position_counts_pending(self):
        # 严格模式空仓：纯挂单敞口 $54 ≥ $40 → 拦截同向新增
        gm, om, client, _ = _make_gm(positions=[], price=1800.0, cap=40.0, cap_strict=True)
        client.open_orders = [
            {"oid": 1, "coin": "ETH", "side": "B", "limitPx": "1800", "sz": "0.03"},
        ]
        self.assertTrue(gm._would_exceed_inventory_cap("ETH", is_buy_open=True))
        self.assertFalse(gm._would_exceed_inventory_cap("ETH", is_buy_open=False))


class TestSurgicalReduce(unittest.TestCase):
    """手术式减仓：只平超限逆势层级，网格其余部分保留"""

    def _setup_long_inventory(self, cap, price=1700.0):
        """三个多头层级（下跌趋势里的逆势库存）：入场 1800/1780/1760，各 0.01 ETH。"""
        reports = []

        def on_close(symbol, pnl, forced=False):
            reports.append((symbol, pnl, forced))

        gm, om, client, logger = _make_gm(
            positions=[{"coin": "ETH", "szi": "0.03"}],
            price=price,
            cap=cap,
            on_close=on_close,
            surgical=True,
        )
        levels = [
            _filled_level("L0", "LONG", 1800, 0.01),
            _filled_level("L1", "LONG", 1780, 0.01),
            _filled_level("L2", "LONG", 1760, 0.01),
            # 一个顺势空头层级，必须原样保留
            _filled_level("L3", "SHORT", 1850, 0.01),
        ]
        gm.grid_levels["ETH"] = levels
        gm.state["active_grids"]["ETH"] = {"config": {}, "buy_orders": [], "sell_orders": []}
        return gm, client, levels, reports, logger

    def test_reduces_only_excess_worst_first(self):
        # 库存 3 × 0.01 × $1700 = $51，上限 $40 → 只需平 1 层（最差入场 1800）
        gm, client, levels, reports, _ = self._setup_long_inventory(cap=40.0)
        result = gm.flatten_adverse_inventory("ETH", trend_dir=-1)
        self.assertTrue(result)
        self.assertEqual(len(client.closed), 1)
        self.assertEqual(client.closed[0], ("ETH", 0.01))
        # 最差入场（1800）被平，其余保留
        self.assertEqual(levels[0].state, GridLevelState.IDLE)  # reset 后
        self.assertEqual(levels[1].state, GridLevelState.OPEN_FILLED)
        self.assertEqual(levels[2].state, GridLevelState.OPEN_FILLED)
        self.assertEqual(levels[3].state, GridLevelState.OPEN_FILLED)  # 顺势层不动
        # forced=True 上报，且亏损为负（1800 入场 1700 平出）
        self.assertEqual(len(reports), 1)
        self.assertTrue(reports[0][2])
        self.assertLess(reports[0][1], 0)
        # 网格状态未被删除（对比全量拆网会 del grid_levels）
        self.assertIn("ETH", gm.grid_levels)

    def test_within_cap_no_action(self):
        # 上限 $60 > 库存 $51 → 不动
        gm, client, levels, reports, _ = self._setup_long_inventory(cap=60.0)
        result = gm.flatten_adverse_inventory("ETH", trend_dir=-1)
        self.assertFalse(result)
        self.assertEqual(client.closed, [])
        self.assertEqual(reports, [])

    def test_cap_disabled_keeps_one_level(self):
        # 上限未启用（=0）：保留一层，平掉两层
        gm, client, levels, reports, _ = self._setup_long_inventory(cap=0.0)
        result = gm.flatten_adverse_inventory("ETH", trend_dir=-1)
        self.assertTrue(result)
        self.assertEqual(len(client.closed), 2)
        self.assertEqual(levels[2].state, GridLevelState.OPEN_FILLED)  # 最优入场保留

    def test_favorable_position_untouched(self):
        # 趋势向上 + 持多 = 顺势 → 不动
        gm, client, levels, reports, _ = self._setup_long_inventory(cap=40.0)
        result = gm.flatten_adverse_inventory("ETH", trend_dir=1)
        self.assertFalse(result)
        self.assertEqual(client.closed, [])

    def test_cancels_close_pending_order_first(self):
        # CLOSE_PENDING 层级：先撤平仓挂单再市价平，避免孤儿 reduce_only 单
        gm, client, levels, reports, _ = self._setup_long_inventory(cap=40.0)
        levels[0].state = GridLevelState.CLOSE_PENDING
        levels[0].close_order_id = 777
        client.open_orders = [{"oid": 777, "coin": "ETH", "side": "A", "limitPx": "1815"}]
        gm.flatten_adverse_inventory("ETH", trend_dir=-1)
        self.assertEqual(client.open_orders, [])  # 777 已撤

    def test_legacy_full_teardown_when_disabled(self):
        # 开关关闭：沿用全量拆网（close_position 无 size + 删层级）
        reports = []

        def on_close(symbol, pnl, forced=False):
            reports.append((symbol, pnl, forced))

        gm, om, client, _ = _make_gm(
            positions=[{"coin": "ETH", "szi": "0.03"}],
            price=1700.0,
            cap=40.0,
            on_close=on_close,
            surgical=False,
        )
        gm.grid_levels["ETH"] = [_filled_level("L0", "LONG", 1800, 0.01)]
        gm.pnl_trackers["ETH"] = gm.pnl_trackers.get("ETH") or __import__(
            "src.trading.grid_pnl", fromlist=["GridPnLTracker"]
        ).GridPnLTracker()
        result = gm.flatten_adverse_inventory("ETH", trend_dir=-1)
        self.assertTrue(result)
        self.assertEqual(client.closed, [("ETH", None)])  # 全平
        self.assertNotIn("ETH", gm.grid_levels)  # 层级被删
        # 全量拆网的强平上报也必须是 forced=True
        self.assertEqual(len(reports), 1)
        self.assertTrue(reports[0][2])


class TestKeepGridReconcile(unittest.TestCase):
    """KEEP_GRID 对账：撤无主残单"""

    def test_cancels_unknown_orders_keeps_known_and_reduce_only(self):
        gm, om, client, _ = _make_gm(reconcile=True)
        level = _filled_level("L0", "LONG", 1800, 0.01, state=GridLevelState.OPEN_PENDING)
        level.open_order_id = 100
        gm.grid_levels["ETH"] = [level]
        gm.state["active_grids"]["ETH"] = {
            "config": {},
            "buy_orders": [{"oid": 101, "px": 1790.0}],
            "sell_orders": [],
        }
        client.open_orders = [
            {"oid": 100, "coin": "ETH", "side": "B", "limitPx": "1800"},  # 层级已知
            {"oid": 101, "coin": "ETH", "side": "B", "limitPx": "1790"},  # state 已知
            {"oid": 102, "coin": "ETH", "side": "A", "limitPx": "1850"},  # 无主 → 撤
            {"oid": 103, "coin": "ETH", "side": "A", "limitPx": "1860",
             "reduceOnly": True},  # 减仓保护单 → 保留
        ]
        gm.sync_grid("ETH", {"action": "KEEP_GRID"})
        remaining = {o["oid"] for o in client.open_orders}
        self.assertEqual(remaining, {100, 101, 103})

    def test_disabled_leaves_orphans(self):
        gm, om, client, _ = _make_gm(reconcile=False)
        gm.grid_levels["ETH"] = []
        client.open_orders = [{"oid": 102, "coin": "ETH", "side": "A", "limitPx": "1850"}]
        gm.sync_grid("ETH", {"action": "KEEP_GRID"})
        self.assertEqual(len(client.open_orders), 1)  # 历史行为：不动


class TestDrawdownMinUsd(unittest.TestCase):
    """回撤保护绝对额下限"""

    def _make(self, min_usd):
        tmp = tempfile.TemporaryDirectory()
        plugin = MaxDrawdownProtection(
            config={"max_drawdown_pct": 0.10, "pause_hours": 4, "min_drawdown_usd": min_usd},
            data_dir=Path(tmp.name),
        )
        plugin._tmp = tmp
        return plugin

    def _ctx(self, equity):
        return ProtectionContext(
            balance=equity,
            equity=equity,
            unrealized_pnl=0.0,
            margin_used=0.0,
            current_positions=[],
            timestamp=datetime.now(),
        )

    def test_small_account_noise_not_triggered(self):
        # $8.61 → $7.71：回撤 10.5% 但绝对额 $0.90 < $5 下限 → 不触发
        plugin = self._make(min_usd=5.0)
        plugin.check(self._ctx(8.61))
        result = plugin.check(self._ctx(7.71))
        self.assertFalse(result.triggered)

    def test_large_drawdown_still_triggers(self):
        # $100 → $89：回撤 11% 且绝对额 $11 ≥ $5 → 触发
        plugin = self._make(min_usd=5.0)
        plugin.check(self._ctx(100.0))
        result = plugin.check(self._ctx(89.0))
        self.assertTrue(result.triggered)

    def test_default_zero_keeps_history_behavior(self):
        # 默认 0 = 关闭：纯百分比触发（历史行为）
        plugin = self._make(min_usd=0)
        plugin.check(self._ctx(8.61))
        result = plugin.check(self._ctx(7.71))
        self.assertTrue(result.triggered)


class TestRebuildInventoryBudget(unittest.TestCase):
    """严格模式批量重建额度制。

    线上实测缺陷：入口布尔检查时空头敞口 $14.6 < 上限 $40 放行，随后一轮
    批量挂出 4×$50 卖单，潜在同向敞口 $214（上限 5 倍）——循环自己挂出的
    单不会被入口检查计入。修复后本轮已挂同向名义额计入预算，耗尽即跳过。
    """

    def _run_sync(self, cap, positions=None, price=1800.0, amount=50.0):
        gm, om, client, logger = _make_gm(
            positions=positions, price=price, cap=cap, cap_strict=True
        )
        placed = {"buy": [], "sell": []}
        oid_iter = iter(range(1, 100))

        def _exec_factory(side):
            def _exec(symbol, amt, p, **kwargs):
                placed[side].append((p, amt))
                oid = next(oid_iter)
                return {
                    "success": True,
                    "limit_order": {
                        "response": {"data": {"statuses": [{"resting": {"oid": oid}}]}}
                    },
                }

            return _exec

        om.execute_long_limit = _exec_factory("buy")
        om.execute_short_limit = _exec_factory("sell")
        ai_config = {
            "action": "UPDATE_GRID",
            "lower_price": 1700.0,
            "upper_price": 1900.0,
            "grid_num": 9,  # 等差步长 25：4 买（1700-1775）+ 现价 1800 + 4 卖（1825-1900）
            "amount_per_grid": amount,
            "grid_type": "ARITHMETIC",
        }
        with mock.patch("src.trading.grid_manager.time.sleep"):
            gm.sync_grid("ETH", ai_config)
        return placed

    def test_budget_caps_batch_placement_per_side(self):
        # 上限 $120、单格 $50：每侧只放得下 2 格（3 格 $150 > $120）
        placed = self._run_sync(cap=120.0)
        self.assertEqual(len(placed["buy"]), 2)
        self.assertEqual(len(placed["sell"]), 2)

    def test_budget_not_binding_places_full_grid(self):
        # 上限充裕：整轮 4 买 4 卖全部挂出
        placed = self._run_sync(cap=500.0)
        self.assertEqual(len(placed["buy"]), 4)
        self.assertEqual(len(placed["sell"]), 4)

    def test_existing_position_consumes_same_side_budget(self):
        # 已有空头 0.01×1800=$18：卖侧余量 $102 → 2 格；
        # 买侧反向抵扣余量 $138 → 仍只放得下 2 格（$150 > $138）
        placed = self._run_sync(
            cap=120.0, positions=[{"coin": "ETH", "szi": "-0.01"}]
        )
        self.assertEqual(len(placed["sell"]), 2)
        self.assertEqual(len(placed["buy"]), 2)

    def test_zero_headroom_blocks_side_entirely(self):
        # 空头敞口 $180 已超上限 $120：卖侧整轮拦截，买侧靠反向抵扣全放行
        placed = self._run_sync(
            cap=120.0, positions=[{"coin": "ETH", "szi": "-0.1"}]
        )
        self.assertEqual(len(placed["sell"]), 0)
        self.assertEqual(len(placed["buy"]), 4)


if __name__ == "__main__":
    unittest.main()
