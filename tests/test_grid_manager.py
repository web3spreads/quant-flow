"""GridManager 资金安全路径测试：紧急平仓校验、增量同步防误判、手术式减仓、
重建闸门与开平仓闭环保全。

全部离线：交易所行为由 FakeGridClient 模拟，check_order_success 复用真实实现
保证内层 statuses 校验语义与线上一致。
"""

import time
from decimal import Decimal

from conftest import QUIET_LOGGER

from src.trading.client import HyperliquidClient
from src.trading.grid_manager import GridManager
from src.trading.grid_pnl import GridPnLTracker
from src.utils.grid_math import GridLevel, GridLevelState
from src.utils.precision import to_decimal

OK_ORDER = {
    "status": "ok",
    "response": {"type": "order", "data": {"statuses": [{"resting": {"oid": 900}}]}},
}
FILLED_ORDER = {
    "status": "ok",
    "response": {
        "type": "order",
        "data": {"statuses": [{"filled": {"avgPx": "100.0", "totalSz": "0.5", "oid": 901}}]},
    },
}
REJECTED_ORDER = {
    "status": "ok",
    "response": {"type": "order", "data": {"statuses": [{"error": "Insufficient margin"}]}},
}
# Hyperliquid 对失去持仓支撑的 reduce_only 单的真实拒单文案（净额对冲后的幻影层级场景）
RO_NETTED_REJECTED_ORDER = {
    "status": "ok",
    "response": {
        "type": "order",
        "data": {"statuses": [{"error": "Reduce only order would increase position. asset=4"}]},
    },
}


class FakeGridClient:
    """HyperliquidClient 桩：行为可配置，校验逻辑复用真实实现。"""

    check_order_success = staticmethod(HyperliquidClient.check_order_success)
    get_order_fill_info = staticmethod(HyperliquidClient.get_order_fill_info)

    def __init__(self):
        self.address = "0xtest"
        self.open_orders: list[dict] | None = []
        self.positions: list[dict] | None = []
        self.price = 100.0
        self.fills: list[dict] = []
        self.close_results: list[dict] = []
        self.close_calls: list[tuple] = []
        self.emergency_results: list[tuple[bool, dict | None]] = []
        self.emergency_calls: list[tuple] = []
        self.cancel_calls: list[int] = []
        self.limit_orders: list[dict] = []
        self.limit_order_results: list[dict] = []
        self.info = self

    # ── Info 接口 ──
    def user_fills(self, address):
        if self.fills is None:
            raise RuntimeError("fills 接口故障")
        return list(self.fills)

    # ── 行情/元数据 ──
    def get_current_price(self, symbol):
        return self.price

    def get_asset_info(self, symbol):
        return {"szDecimals": 3}

    def format_price(self, symbol, price):
        return round(float(price), 3)

    # ── 账户查询 ──
    def get_open_orders(self, include_trigger=False):
        return None if self.open_orders is None else list(self.open_orders)

    def get_positions(self):
        return None if self.positions is None else list(self.positions)

    # ── 交易 ──
    def cancel_order(self, symbol, oid):
        self.cancel_calls.append(oid)
        return {"status": "ok"}

    def close_position(self, symbol, size=None):
        self.close_calls.append((symbol, size))
        return self.close_results.pop(0) if self.close_results else REJECTED_ORDER

    def emergency_close_with_retry(self, symbol, size=None, *, reason, max_retries=3):
        self.emergency_calls.append((symbol, size, reason))
        if self.emergency_results:
            return self.emergency_results.pop(0)
        return False, {"status": "error", "message": "桩默认失败"}

    def place_limit_order(self, symbol, is_buy, size, price, reduce_only=False):
        self.limit_orders.append(
            {"symbol": symbol, "is_buy": is_buy, "size": size, "price": price, "ro": reduce_only}
        )
        return self.limit_order_results.pop(0) if self.limit_order_results else OK_ORDER


class FakeGridOrderManager:
    def __init__(self, client):
        self.client = client
        self.long_limits: list[tuple] = []
        self.short_limits: list[tuple] = []

    def get_current_positions(self):
        return self.client.get_positions()

    def execute_long_limit(self, symbol, amount, price, **kwargs):
        self.long_limits.append((symbol, amount, price))
        return {"success": True, "limit_order": OK_ORDER}

    def execute_short_limit(self, symbol, amount, price, **kwargs):
        self.short_limits.append((symbol, amount, price))
        return {"success": True, "limit_order": OK_ORDER}


def make_manager(tmp_path, client=None, netting_attribution_enabled=True):
    client = client or FakeGridClient()
    om = FakeGridOrderManager(client)
    manager = GridManager(
        om,
        QUIET_LOGGER,
        state_file=str(tmp_path / "grid_state.json"),
        netting_attribution_enabled=netting_attribution_enabled,
    )
    return manager, client, om


class RecordingLogger:
    """只记录 log_trade 调用、其余日志方法静默的桩（断言 trades 落盘口径用）。"""

    def __init__(self):
        self.trades: list[dict] = []

    def log_trade(self, **kwargs):
        self.trades.append(kwargs)

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def make_filled_level(level_id="L0", side="LONG", price="100", amount="0.5") -> GridLevel:
    level = GridLevel(
        id=level_id,
        price=to_decimal(price),
        amount=to_decimal("50"),
        side=side,
        state=GridLevelState.OPEN_FILLED,
    )
    level.open_fill_price = to_decimal(price)
    level.open_fill_amount = to_decimal(amount)
    level.open_fill_time = 1000.0
    return level


class TestEmergencyCloseVerification:
    def test_failure_keeps_risk_state_and_marks_pending(self, tmp_path):
        # 平仓失败：层级/屏障/PnL 必须保留，待重试标记落盘，绝不谎报成功
        manager, client, _ = make_manager(tmp_path)
        manager.grid_levels["ETH"] = [make_filled_level()]
        manager.pnl_trackers["ETH"] = (
            manager.pnl_trackers.get("ETH")
            or __import__("src.trading.grid_pnl", fromlist=["GridPnLTracker"]).GridPnLTracker()
        )
        client.positions = [{"coin": "ETH", "szi": "0.5"}]
        client.emergency_results = [(False, {"status": "error", "message": "限流"})]

        ok = manager._emergency_close_all("ETH", reason="STOP_LOSS 测试")
        assert ok is False
        assert "ETH" in manager.grid_levels  # 风控状态保留
        assert manager.state["pending_emergency_close"]["ETH"] == "STOP_LOSS 测试"

    def test_success_clears_state(self, tmp_path):
        manager, client, _ = make_manager(tmp_path)
        manager.grid_levels["ETH"] = [make_filled_level()]
        client.positions = [{"coin": "ETH", "szi": "0.5"}]
        client.emergency_results = [(True, FILLED_ORDER)]

        ok = manager._emergency_close_all("ETH", reason="STOP_LOSS 测试")
        assert ok is True
        assert "ETH" not in manager.grid_levels
        assert "ETH" not in (manager.state.get("pending_emergency_close") or {})

    def test_retry_clears_when_position_gone(self, tmp_path):
        # 待重试期间持仓消失（交易所侧成交/人工处理）：只做状态收尾，不再下单
        manager, client, _ = make_manager(tmp_path)
        manager.grid_levels["ETH"] = [make_filled_level()]
        manager.state["pending_emergency_close"] = {"ETH": "上轮失败"}
        client.positions = []

        manager.retry_pending_emergency_close("ETH")
        assert "ETH" not in manager.grid_levels
        assert "ETH" not in (manager.state.get("pending_emergency_close") or {})
        assert client.emergency_calls == []

    def test_retry_recloses_when_position_remains(self, tmp_path):
        manager, client, _ = make_manager(tmp_path)
        manager.state["pending_emergency_close"] = {"ETH": "上轮失败"}
        client.positions = [{"coin": "ETH", "szi": "0.5"}]
        client.emergency_results = [(True, FILLED_ORDER)]

        manager.retry_pending_emergency_close("ETH")
        assert len(client.emergency_calls) == 1
        assert "ETH" not in (manager.state.get("pending_emergency_close") or {})


class TestIncrementalSyncGuards:
    def _pending_level(self) -> GridLevel:
        level = GridLevel(
            id="L0",
            price=to_decimal("99"),
            amount=to_decimal("50"),
            side="LONG",
            state=GridLevelState.OPEN_PENDING,
        )
        level.open_order_id = 12345
        return level

    def test_orders_query_failure_skips_sync(self, tmp_path):
        # 挂单查询失败（None）：整轮跳过，层级状态不得变化、不得重新挂单
        manager, client, om = make_manager(tmp_path)
        level = self._pending_level()
        manager.grid_levels["ETH"] = [level]
        client.open_orders = None

        manager.sync_grid_incremental("ETH")
        assert level.state == GridLevelState.OPEN_PENDING
        assert om.long_limits == []

    def test_fills_query_failure_skips_sync(self, tmp_path):
        # 成交记录查询失败：不得把「已成交」误判为「被撤销」打回 IDLE 重挂
        manager, client, om = make_manager(tmp_path)
        level = self._pending_level()
        manager.grid_levels["ETH"] = [level]
        client.open_orders = []  # 订单已不在挂单列表（实际已成交）
        client.fills = None  # 但 fills 接口故障

        manager.sync_grid_incremental("ETH")
        assert level.state == GridLevelState.OPEN_PENDING
        assert om.long_limits == []

    def test_passive_sync_confirms_fill_without_new_opens(self, tmp_path):
        # 被动同步（KEEP_GRID）：确认成交、挂平仓单，但 IDLE 层不挂新开仓单
        manager, client, om = make_manager(tmp_path)
        pending = self._pending_level()
        idle = GridLevel(
            id="L1",
            price=to_decimal("98"),
            amount=to_decimal("50"),
            side="LONG",
            state=GridLevelState.IDLE,
        )
        manager.grid_levels["ETH"] = [pending, idle]
        client.open_orders = []
        client.fills = [{"oid": 12345, "px": "99.0", "sz": "0.505", "time": 1000}]

        manager.sync_grid_incremental("ETH", allow_open=False)
        # 同轮确认成交并立即挂平仓单：不让持仓在整个调度间隔里没有退出单保护
        assert pending.state == GridLevelState.CLOSE_PENDING
        assert pending.open_fill_amount == Decimal("0.505")
        assert client.limit_orders and client.limit_orders[-1]["ro"] is True
        assert idle.state == GridLevelState.IDLE  # 未挂新开仓单
        assert om.long_limits == []

    def test_partial_fills_aggregated(self, tmp_path):
        # 同一 oid 多笔部分成交必须聚合（量求和、价加权），只取首笔会低记库存
        manager, client, _ = make_manager(tmp_path)
        level = self._pending_level()
        fills = [
            {"oid": 12345, "px": "99.0", "sz": "0.3", "time": 1000},
            {"oid": 12345, "px": "98.0", "sz": "0.2", "time": 1001},
        ]
        assert manager._confirm_fill("ETH", level, "open", fills) is True
        assert level.open_fill_amount == Decimal("0.5")
        expected_px = (
            Decimal("99.0") * Decimal("0.3") + Decimal("98.0") * Decimal("0.2")
        ) / Decimal("0.5")
        assert level.open_fill_price == expected_px


class TestSurgicalReduce:
    def test_inner_rejection_not_recorded_as_closed(self, tmp_path):
        # HL 拒单外层仍是 status=ok：内层 error 必须让层级保持原状（不得假关闭）
        manager, client, _ = make_manager(tmp_path)
        level = make_filled_level(amount="1.0")
        manager.grid_levels["ETH"] = [level]
        manager.max_position_notional_usd = 10.0  # 逆势名义额 100 > 上限 10 → 触发削减
        client.positions = [{"coin": "ETH", "szi": "1.0"}]
        client.close_results = [REJECTED_ORDER]

        reduced = manager._surgical_reduce_adverse("ETH", trend_dir=-1, position_size=1.0)
        assert reduced is False
        assert level.state == GridLevelState.OPEN_FILLED  # 未被 reset

    def test_successful_reduce_records_round_trip(self, tmp_path):
        manager, client, _ = make_manager(tmp_path)
        level = make_filled_level(amount="1.0")
        manager.grid_levels["ETH"] = [level]
        manager.max_position_notional_usd = 10.0
        client.positions = [{"coin": "ETH", "szi": "1.0"}]
        client.close_results = [FILLED_ORDER]

        reduced = manager._surgical_reduce_adverse("ETH", trend_dir=-1, position_size=1.0)
        assert reduced is True
        assert level.state == GridLevelState.IDLE  # reset 回收复用


class TestGridPriceFormatting:
    def test_low_price_symbol_not_collapsed(self, tmp_path):
        # 低价交易对：历史 0.1 tick 硬编码会把所有格子拍扁到同一价位
        manager, _, _ = make_manager(tmp_path)
        prices = manager._calculate_grid_prices(0.10, 0.14, 5, "ARITHMETIC")
        formatted = manager._format_grid_prices("DOGE", prices)
        assert len(formatted) == 5
        assert formatted == sorted(set(formatted))

    def test_duplicate_prices_deduped(self, tmp_path):
        manager, client, _ = make_manager(tmp_path)
        client.format_price = lambda symbol, price: round(float(price), 0)  # 粗精度
        formatted = manager._format_grid_prices("ETH", [100.1, 100.2, 101.4])
        assert formatted == [100.0, 101.0]


class TestNettedLevelReconciliation:
    """幻影层级收尾：库存被净额对冲后，平仓单被拒应关闭层级而非无限重试。

    线上实测（测试网 32 小时）：中性网格空头库存被买开仓单净额平掉后，
    层级停在 OPEN_FILLED，每周期挂 reduce_only 平仓单被拒 136 次。
    """

    def _closable_level(self, side="LONG"):
        return make_filled_level(level_id="L4", side=side, price="100", amount="0.5")

    def test_reduce_only_rejection_with_no_exposure_resets_level(self, tmp_path):
        # LONG 层挂卖出减仓单被拒 + 实际持仓为 0：库存已被净额对冲，层级收尾复用
        manager, client, _ = make_manager(tmp_path)
        level = self._closable_level(side="LONG")
        client.positions = []  # 无持仓
        client.limit_order_results = [RO_NETTED_REJECTED_ORDER]

        manager._place_close_order("ETH", level)
        assert level.state == GridLevelState.IDLE
        assert level.open_fill_price is None  # 幻影库存清空，不再污染未实现盈亏

    def test_reduce_only_rejection_with_opposite_exposure_resets_level(self, tmp_path):
        # SHORT 层挂买入减仓单被拒 + 实际净持仓为多头：空头敞口已消失，同样收尾
        manager, client, _ = make_manager(tmp_path)
        level = self._closable_level(side="SHORT")
        client.positions = [{"coin": "ETH", "szi": "0.5"}]
        client.limit_order_results = [RO_NETTED_REJECTED_ORDER]

        manager._place_close_order("ETH", level)
        assert level.state == GridLevelState.IDLE

    def test_reduce_only_rejection_with_matching_exposure_keeps_level(self, tmp_path):
        # 拒单文案匹配但同向持仓仍在：不满足双重确认，保留层级下轮重试
        manager, client, _ = make_manager(tmp_path)
        level = self._closable_level(side="LONG")
        client.positions = [{"coin": "ETH", "szi": "0.5"}]
        client.limit_order_results = [RO_NETTED_REJECTED_ORDER]

        manager._place_close_order("ETH", level)
        assert level.state == GridLevelState.OPEN_FILLED
        assert level.open_fill_price is not None

    def test_other_rejection_keeps_level(self, tmp_path):
        # 其他拒因（保证金不足）：与净额对冲无关，必须保留重试
        manager, client, _ = make_manager(tmp_path)
        level = self._closable_level(side="LONG")
        client.positions = []
        client.limit_order_results = [REJECTED_ORDER]

        manager._place_close_order("ETH", level)
        assert level.state == GridLevelState.OPEN_FILLED

    def test_position_query_failure_keeps_level(self, tmp_path):
        # 持仓查询失败（None）：未知状态不收尾，保留层级下轮重试
        manager, client, _ = make_manager(tmp_path)
        level = self._closable_level(side="LONG")
        client.positions = None
        client.limit_order_results = [RO_NETTED_REJECTED_ORDER]

        manager._place_close_order("ETH", level)
        assert level.state == GridLevelState.OPEN_FILLED


class TestEmergencyClosePnlLabeling:
    """紧急平仓 trades 落盘口径：净额归因启用时预估盈亏只留痕不写 pnl 字段。

    线上实测预估 -2.43 vs 链上实际 -0.30：预估把幻影层级的未实现盈亏也算进去，
    与下一周期 GRID_NET_CLOSE 的实际盈亏并存会让下游统计双算且失真。
    """

    def _setup(self, tmp_path, netting_enabled):
        manager, client, _ = make_manager(tmp_path, netting_attribution_enabled=netting_enabled)
        recorder = RecordingLogger()
        manager.logger = recorder
        level = make_filled_level(price="100", amount="0.5")
        manager.grid_levels["ETH"] = [level]
        manager.pnl_trackers["ETH"] = GridPnLTracker()
        client.price = 90.0  # 产生非零未实现盈亏预估
        client.positions = [{"coin": "ETH", "szi": "0.5"}]
        client.emergency_results = [(True, FILLED_ORDER)]
        return manager, recorder

    def test_netting_enabled_logs_estimate_in_reason_only(self, tmp_path):
        manager, recorder = self._setup(tmp_path, netting_enabled=True)
        assert manager._emergency_close_all("ETH", reason="TIME_LIMIT 测试") is True

        records = [t for t in recorder.trades if t["action"] == "GRID_EMERGENCY_CLOSE"]
        assert len(records) == 1
        assert records[0]["pnl"] is None  # 实际盈亏由 GRID_NET_CLOSE 记录，不双算
        assert "预估盈亏" in records[0]["reason"]
        assert "TIME_LIMIT 测试" in records[0]["reason"]

    def test_netting_disabled_keeps_estimate_pnl(self, tmp_path):
        # 归因关闭时预估是唯一记录，保留原行为
        manager, recorder = self._setup(tmp_path, netting_enabled=False)
        assert manager._emergency_close_all("ETH", reason="TIME_LIMIT 测试") is True

        records = [t for t in recorder.trades if t["action"] == "GRID_EMERGENCY_CLOSE"]
        assert len(records) == 1
        assert records[0]["pnl"] is not None
        assert records[0]["reason"] == "TIME_LIMIT 测试"


class TestRebuildGating:
    """重建闸门：冷却、真突破逃生口、层数抖动不触发全量撤换单。

    线上根因：重建判定过松导致 84.6% 周期全量重建，挂单活不过 5 分钟，
    网格永远走不完一轮开平仓闭环。
    """

    def _seed(self, manager, *, lower=95.0, upper=105.0, grid_num=6, mode="NEUTRAL"):
        manager.state["active_grids"]["ETH"] = {
            "config": {
                "action": "UPDATE_GRID",
                "lower_price": lower,
                "upper_price": upper,
                "grid_num": grid_num,
                "amount_per_grid": 10.0,
                "mode": mode,
            },
            "buy_orders": [{"oid": 801, "px": 98.0}],
            "sell_orders": [{"oid": 802, "px": 102.0}],
        }

    def _new_config(self, **overrides):
        cfg = {
            "action": "UPDATE_GRID",
            "lower_price": 95.0,
            "upper_price": 105.0,
            "grid_num": 6,
            "amount_per_grid": 10.0,
            "mode": "NEUTRAL",
        }
        cfg.update(overrides)
        return cfg

    def _manager(self, tmp_path, price=100.0):
        client = FakeGridClient()
        client.price = price
        client.open_orders = [
            {"oid": 801, "coin": "ETH", "side": "B", "sz": "0.1", "limitPx": "98.0"},
            {"oid": 802, "coin": "ETH", "side": "A", "sz": "0.1", "limitPx": "102.0"},
        ]
        manager, client, _ = make_manager(tmp_path, client=client)
        self._seed(manager)
        return manager, client

    def test_cooldown_blocks_rebuild(self, tmp_path):
        # 冷却期内即便区间大改也不重建——这是抑制高频撤换单的主闸
        manager, _ = self._manager(tmp_path)
        manager._last_rebuild_ts["ETH"] = time.time()

        should, reason = manager._should_rebuild_grid("ETH", self._new_config(lower=80.0))
        assert should is False
        assert "冷却" in reason

    def test_breakout_bypasses_cooldown(self, tmp_path):
        # 价格走出旧区间即说明这张网失效，不能干等冷却把网格空挂在够不着的价位
        manager, _ = self._manager(tmp_path, price=106.0)
        manager._last_rebuild_ts["ETH"] = time.time()

        should, reason = manager._should_rebuild_grid("ETH", self._new_config(upper=110.0))
        assert should is True
        assert "提前解除重建冷却" in reason

    def test_inside_band_does_not_count_as_breakout(self, tmp_path):
        # 仅贴近边界（<0.5%）不算突破，避免边界抖动把冷却形同虚设
        manager, _ = self._manager(tmp_path, price=105.4)
        manager._last_rebuild_ts["ETH"] = time.time()

        should, reason = manager._should_rebuild_grid("ETH", self._new_config(upper=110.0))
        assert should is False
        assert "冷却" in reason

    def test_price_query_failure_does_not_bypass_cooldown(self, tmp_path):
        # 取价失败 fail-safe 偏向不重建：绝不因 API 抖动触发全量撤换单
        manager, client = self._manager(tmp_path)
        manager._last_rebuild_ts["ETH"] = time.time()
        client.price = None

        should, reason = manager._should_rebuild_grid("ETH", self._new_config(lower=80.0))
        assert should is False
        assert "冷却" in reason

    def test_grid_num_change_alone_does_not_rebuild(self, tmp_path):
        # LLM 每轮抖动的层数不改变覆盖区间，为它全撤全建纯属自伤
        manager, _ = self._manager(tmp_path)
        manager._last_rebuild_ts["ETH"] = time.time() - 7200  # 冷却已过

        should, reason = manager._should_rebuild_grid("ETH", self._new_config(grid_num=12))
        assert should is False
        assert "层数变化=True" in reason

    def test_mode_change_rebuilds_after_cooldown(self, tmp_path):
        # 方向变化仍是结构性变化，冷却过后照常重建
        manager, _ = self._manager(tmp_path)
        manager._last_rebuild_ts["ETH"] = time.time() - 7200

        should, reason = manager._should_rebuild_grid("ETH", self._new_config(mode="LONG"))
        assert should is True
        assert "类型/方向" in reason


class TestRebuildPreservesLifecycle:
    """全量重建必须保住在途层级的开平仓闭环。

    线上根因：重建撤掉 reduce_only 平仓单又整体覆盖 grid_levels，持仓变成
    无人认领的库存——几百笔开仓成交只对应个位数被识别的平仓成交。
    """

    def _close_pending_level(self, level_id="L0", close_oid=901):
        level = make_filled_level(level_id=level_id, price="100", amount="0.5")
        level.state = GridLevelState.CLOSE_PENDING
        level.close_order_id = close_oid
        return level

    def test_carried_level_and_its_close_order_survive_rebuild(self, tmp_path):
        client = FakeGridClient()
        client.price = 100.0
        # 901 是在途层级的 reduce_only 平仓单，802 是普通网格单
        client.open_orders = [
            {"oid": 802, "coin": "ETH", "side": "A", "sz": "0.1", "limitPx": "102.0"},
            {"oid": 901, "coin": "ETH", "side": "A", "sz": "0.5", "limitPx": "100.5"},
        ]
        manager, client, _ = make_manager(tmp_path, client=client)

        carried = self._close_pending_level()
        manager.grid_levels["ETH"] = [carried]
        manager.state["active_grids"]["ETH"] = {
            "config": {"lower_price": 95.0, "upper_price": 105.0, "grid_num": 2},
            "buy_orders": [],
            "sell_orders": [{"oid": 802, "px": 102.0}],
        }

        # 撤单后交易所只剩被保留的平仓单
        def _get_open_orders(include_trigger=False):
            return [o for o in client.open_orders if o["oid"] == 901]

        client.cancel_calls = []
        original = client.get_open_orders
        client.get_open_orders = _get_open_orders

        manager.sync_grid(
            "ETH",
            {
                "action": "UPDATE_GRID",
                "lower_price": 90.0,
                "upper_price": 110.0,
                "grid_num": 2,
                "amount_per_grid": 10.0,
                "mode": "NEUTRAL",
            },
        )
        client.get_open_orders = original

        # 平仓单没被撤，且没被当成「撤单未净」挡下重建
        assert 901 not in client.cancel_calls
        levels = manager.grid_levels["ETH"]
        assert carried in levels, "在途层级必须并入新一代，不能被整体覆盖丢弃"
        assert carried.state == GridLevelState.CLOSE_PENDING
        assert carried.close_order_id == 901
        assert carried.id.startswith("K"), "带过来的层级用 K 前缀标识"
        assert carried.open_fill_price == to_decimal("100")

    def test_public_cancel_all_still_cancels_everything(self, tmp_path):
        # 账户级熔断走公共入口，绝不能因为在途层级白名单漏撤单
        client = FakeGridClient()
        client.open_orders = [
            {"oid": 901, "coin": "ETH", "side": "A", "sz": "0.5", "limitPx": "100.5"},
        ]
        manager, client, _ = make_manager(tmp_path, client=client)
        manager.grid_levels["ETH"] = [self._close_pending_level()]

        assert manager.cancel_all_orders("ETH") is True
        assert 901 in client.cancel_calls


class TestSyncGridClaimsFillsFirst:
    """重建判定之前必须先认领成交，否则刚成交的层级会被当成挂单撤掉。"""

    def test_fill_claimed_before_rebuild_decision(self, tmp_path):
        client = FakeGridClient()
        client.price = 100.0
        client.open_orders = []  # 开仓单已不在挂单列表 = 已成交
        client.fills = [{"oid": 12345, "px": "99.0", "sz": "0.505", "time": 1000}]
        manager, client, _ = make_manager(tmp_path, client=client)

        level = GridLevel(
            id="L0",
            price=to_decimal("99"),
            amount=to_decimal("50"),
            side="LONG",
            state=GridLevelState.OPEN_PENDING,
        )
        level.open_order_id = 12345
        manager.grid_levels["ETH"] = [level]
        manager.state["active_grids"]["ETH"] = {
            "config": {"lower_price": 95.0, "upper_price": 105.0, "grid_num": 2},
            "buy_orders": [{"oid": 12345, "px": 99.0}],
            "sell_orders": [],
        }
        manager._last_rebuild_ts["ETH"] = time.time()  # 冷却中，本轮不重建

        manager.sync_grid(
            "ETH",
            {
                "action": "UPDATE_GRID",
                "lower_price": 95.0,
                "upper_price": 105.0,
                "grid_num": 2,
                "amount_per_grid": 10.0,
                "mode": "NEUTRAL",
            },
        )

        # 成交被认领，并同轮挂上了 reduce_only 平仓单
        assert level.state == GridLevelState.CLOSE_PENDING
        assert level.open_fill_amount == Decimal("0.505")
        assert client.limit_orders and client.limit_orders[-1]["ro"] is True

    def test_keep_grid_does_not_double_sync(self, tmp_path):
        # 入口已统一被动同步，KEEP_GRID 分支不得再整轮重跑一次
        client = FakeGridClient()
        client.price = 100.0
        client.open_orders = []
        manager, client, _ = make_manager(tmp_path, client=client)
        manager.grid_levels["ETH"] = [make_filled_level()]

        calls: list[bool] = []
        original = manager.sync_grid_incremental

        def _spy(symbol, allow_open=True):
            calls.append(allow_open)
            return original(symbol, allow_open=allow_open)

        manager.sync_grid_incremental = _spy
        manager.sync_grid("ETH", {"action": "KEEP_GRID"})

        assert calls == [False], f"KEEP_GRID 周期应只被动同步一次，实际 {calls}"
