"""网格在 LLM 故障下的韧性与净额平仓归因测试

覆盖四项修复（全部由 config 开关控制，默认关闭 = 历史行为）：

1. ``GridAgent`` 给每份决策打 ``llm_ok`` 标：把「LLM 自身不可用的兜底 KEEP_GRID」
   与「AI 真实判断的 KEEP_GRID」区分开——两者返回值同形，此前无从分辨。
2. ``GridAgent.build_fallback_config``：不经 LLM、纯用市场数据建中性网格。
3. ``GridManager.reconcile_netting_closes``：以链上成交为准补齐被层级状态机漏掉的
   平仓盈亏（中性网格靠对侧格子净额对冲平仓，走不到 CLOSE_PENDING→COMPLETED）。
4. ``QuantFlowBot`` 的连续故障告警升级与空转自愈。

线上背景：供应商下线 deepseek-chat 后，每轮决策 400 失败 → 网格被清空后再也建不起来，
连续 13 小时零挂单零成交且无任何告警；同期归因覆盖率仅 2.3%，连亏熔断形同虚设。
"""

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.agent.grid_agent import GridAgent
from src.trading.grid_manager import GridManager
from src.utils.grid_math import GridLevel, GridLevelState


class _DummyLogger:
    def __init__(self):
        self.trades = []

    def print_info(self, *a, **k):
        pass

    def print_warning(self, *a, **k):
        pass

    def print_error(self, *a, **k):
        pass

    def print_header(self, *a, **k):
        pass

    def print_section(self, *a, **k):
        pass

    def log_trade(self, **kwargs):
        self.trades.append(kwargs)


def _fill(tid, ts, closed_pnl="0", fee="0", coin="ETH", dir_="Close Long", oid=1):
    """构造一条 Hyperliquid userFills 记录（字段名与真实接口一致，数值均为字符串）。"""
    return {
        "tid": tid,
        "time": ts,
        "coin": coin,
        "dir": dir_,
        "closedPnl": closed_pnl,
        "fee": fee,
        "sz": "0.01",
        "px": "1800.0",
        "oid": oid,
    }


class _GridManagerTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state_file = str(Path(self._tmp.name) / "grid_state.json")
        self.logger = _DummyLogger()
        self.reported = []

    def tearDown(self):
        self._tmp.cleanup()

    def _make_gm(self, netting_enabled=True, order_manager=None) -> GridManager:
        return GridManager(
            order_manager=order_manager or MagicMock(),
            logger=self.logger,
            state_file=self.state_file,
            on_round_trip_close=lambda s, p, forced=False: self.reported.append((s, p, forced)),
            netting_attribution_enabled=netting_enabled,
        )


class TestNettingAttribution(_GridManagerTestBase):
    """净额对冲平仓归因：链上成交 → 连亏熔断 + trades 日志"""

    def test_disabled_by_default_is_noop(self):
        """未开启时不得动状态、不得上报——保持历史行为"""
        gm = self._make_gm(netting_enabled=False)
        res = gm.reconcile_netting_closes("ETH", fills=[_fill("t1", 1000, closed_pnl="5")])

        self.assertEqual(res["skipped"], "disabled")
        self.assertEqual(self.reported, [])
        self.assertEqual(self.logger.trades, [])

    def test_first_run_primes_cursor_without_backfilling(self):
        """首次启用只锚定游标，绝不回溯历史成交。

        否则历史上几百笔亏损腿会在一个周期内全部灌进连亏熔断，瞬间误锁交易对。
        """
        gm = self._make_gm()
        fills = [
            _fill("t1", 1000, closed_pnl="-3"),
            _fill("t2", 2000, closed_pnl="-4"),
        ]
        res = gm.reconcile_netting_closes("ETH", fills=fills)

        self.assertEqual(res["skipped"], "primed")
        self.assertEqual(self.reported, [], "首次启用不得上报任何历史盈亏")
        self.assertEqual(gm.state["netting_attribution"]["ETH"]["cursor_ms"], 2000)

    def test_no_fills_does_not_persist_cursor(self):
        """该交易对还没有成交时不落盘，等有成交那轮再锚定游标"""
        gm = self._make_gm()
        res = gm.reconcile_netting_closes("ETH", fills=[_fill("x", 999, coin="BTC")])

        self.assertEqual(res["skipped"], "no_fills")
        self.assertEqual(gm.state.get("netting_attribution", {}).get("ETH", {}), {})

    def test_new_closes_are_attributed_net_of_fees(self):
        """游标之后的平仓成交被归因，且按「已扣手续费」的净额上报。

        与 GridPnLTracker.record_round_trip 口径一致——网格的小额止盈常被手续费
        吃穿，连亏熔断必须看净额才有意义。
        """
        gm = self._make_gm()
        gm.reconcile_netting_closes("ETH", fills=[_fill("t0", 1000)])  # 锚定游标

        res = gm.reconcile_netting_closes(
            "ETH",
            fills=[
                _fill("t0", 1000),
                _fill("t1", 2000, closed_pnl="1.00", fee="0.20"),
                _fill("t2", 3000, closed_pnl="-0.50", fee="0.10"),
            ],
        )

        self.assertEqual(res["processed"], 2)
        self.assertAlmostEqual(res["pnl"], 0.80 - 0.60, places=6)
        self.assertEqual([r[1] for r in self.reported], [0.80, -0.60])
        # 净额对冲是正常成交，不是风控强平
        self.assertTrue(all(r[2] is False for r in self.reported))

    def test_zero_pnl_fills_are_skipped(self):
        """纯开仓腿（closedPnl=0）不产生归因记录"""
        gm = self._make_gm()
        gm.reconcile_netting_closes("ETH", fills=[_fill("t0", 1000)])

        res = gm.reconcile_netting_closes(
            "ETH",
            fills=[_fill("t0", 1000), _fill("t1", 2000, closed_pnl="0", fee="0.05")],
        )

        self.assertEqual(res["processed"], 0)
        self.assertEqual(self.reported, [])

    def test_repeated_calls_do_not_double_count(self):
        """同一批成交重复调用只归因一次——游标 + tid 去重"""
        gm = self._make_gm()
        gm.reconcile_netting_closes("ETH", fills=[_fill("t0", 1000)])
        fills = [_fill("t0", 1000), _fill("t1", 2000, closed_pnl="2.0")]

        gm.reconcile_netting_closes("ETH", fills=fills)
        gm.reconcile_netting_closes("ETH", fills=fills)
        gm.reconcile_netting_closes("ETH", fills=fills)

        self.assertEqual(len(self.reported), 1, "重复调用不得重复上报")

    def test_same_millisecond_fills_are_deduped_individually(self):
        """同一毫秒内的多笔成交：已归因的不重复，新出现的仍要归因"""
        gm = self._make_gm()
        gm.reconcile_netting_closes("ETH", fills=[_fill("t0", 1000)])

        gm.reconcile_netting_closes("ETH", fills=[_fill("ta", 2000, closed_pnl="1.0")])
        self.assertEqual(len(self.reported), 1)

        # 同一毫秒又来一笔新的（交易所分页/延迟很常见）
        gm.reconcile_netting_closes(
            "ETH",
            fills=[
                _fill("ta", 2000, closed_pnl="1.0"),
                _fill("tb", 2000, closed_pnl="3.0"),
            ],
        )
        self.assertEqual(len(self.reported), 2)
        self.assertAlmostEqual(self.reported[1][1], 3.0)

    def test_other_symbols_are_ignored(self):
        """只归因本交易对的成交"""
        gm = self._make_gm()
        gm.reconcile_netting_closes("ETH", fills=[_fill("t0", 1000)])

        res = gm.reconcile_netting_closes(
            "ETH",
            fills=[_fill("t0", 1000), _fill("b1", 2000, closed_pnl="9.0", coin="BTC")],
        )

        self.assertEqual(res["processed"], 0)
        self.assertEqual(self.reported, [])

    def test_attribution_writes_pnl_and_reason_to_trade_log(self):
        """归因必须把 pnl / reason 落进 trades 日志——历史上这两个字段恒为 null"""
        gm = self._make_gm()
        gm.reconcile_netting_closes("ETH", fills=[_fill("t0", 1000)])

        gm.reconcile_netting_closes(
            "ETH",
            fills=[
                _fill("t0", 1000),
                _fill("t1", 2000, closed_pnl="1.5", fee="0.1", dir_="Close Short"),
            ],
        )

        self.assertEqual(len(self.logger.trades), 1)
        record = self.logger.trades[0]
        self.assertEqual(record["action"], "GRID_NET_CLOSE")
        self.assertAlmostEqual(record["pnl"], 1.4)
        self.assertIn("GRID_NETTING", record["reason"])
        self.assertIn("Close Short", record["reason"])

    def test_forced_close_semantics_survive_attribution(self):
        """风控强平的成交必须以 forced=True 上报。

        consecutive_loss 的 forced_close_no_reset 依赖这个区分（强平的净盈利不得
        重置连亏计数）。归因以链上成交为准，本身分不清强平与正常止盈，故靠强平
        下单处登记的 oid 精确匹配——taker/maker 不是可靠判据，网格限价单穿价成交
        同样是 taker。
        """
        gm = self._make_gm()
        gm.reconcile_netting_closes("ETH", fills=[_fill("t0", 1000)])
        gm._mark_forced_close_oid(
            "ETH", {"status": "ok", "response": {"data": {"statuses": [{"filled": {"oid": 777}}]}}}
        )

        gm.reconcile_netting_closes(
            "ETH",
            fills=[
                _fill("t0", 1000),
                _fill("t1", 2000, closed_pnl="1.0", oid=777),  # 强平成交
                _fill("t2", 3000, closed_pnl="2.0", oid=888),  # 普通网格成交
            ],
        )

        self.assertEqual([r[2] for r in self.reported], [True, False])
        reasons = [t["reason"] for t in self.logger.trades]
        self.assertTrue(reasons[0].startswith("GRID_FORCED"))
        self.assertTrue(reasons[1].startswith("GRID_NETTING"))

    def test_consumed_forced_oid_is_cleared(self):
        """强平 oid 归因后即从状态移除，不得污染后续同号成交"""
        gm = self._make_gm()
        gm.reconcile_netting_closes("ETH", fills=[_fill("t0", 1000)])
        gm._mark_forced_close_oid(
            "ETH", {"status": "ok", "response": {"data": {"statuses": [{"filled": {"oid": 777}}]}}}
        )
        gm.reconcile_netting_closes(
            "ETH", fills=[_fill("t0", 1000), _fill("t1", 2000, closed_pnl="1.0", oid=777)]
        )

        self.assertEqual(gm.state["netting_attribution"]["ETH"]["forced_oids"], [])

    def test_forced_oid_not_registered_when_disabled(self):
        """未开启净额归因时不写 forced_oids——历史路径自己带 forced 语义"""
        gm = self._make_gm(netting_enabled=False)
        gm._mark_forced_close_oid(
            "ETH", {"status": "ok", "response": {"data": {"statuses": [{"filled": {"oid": 777}}]}}}
        )
        self.assertEqual(gm.state.get("netting_attribution", {}), {})

    def test_unextractable_oid_is_ignored(self):
        """平仓返回里取不到 oid 时静默跳过，不得抛出打断强平流程"""
        gm = self._make_gm()
        gm._mark_forced_close_oid("ETH", {"status": "err"})
        gm._mark_forced_close_oid("ETH", None)
        self.assertEqual(
            gm.state.get("netting_attribution", {}).get("ETH", {}).get("forced_oids"), None
        )

    def test_forced_oid_list_is_bounded(self):
        """强平 oid 集合有上限，不得让状态文件无限膨胀"""
        from src.trading.grid_manager import MAX_FORCED_OIDS

        gm = self._make_gm()
        for i in range(MAX_FORCED_OIDS + 20):
            gm._mark_forced_close_oid(
                "ETH",
                {"status": "ok", "response": {"data": {"statuses": [{"filled": {"oid": i}}]}}},
            )

        stored = gm.state["netting_attribution"]["ETH"]["forced_oids"]
        self.assertEqual(len(stored), MAX_FORCED_OIDS)
        self.assertIn(str(MAX_FORCED_OIDS + 19), stored, "应保留最近的 oid")

    def test_trade_log_failure_still_advances_cursor(self):
        """写交易日志失败不得中断归因——否则游标停滞，下轮把同一批盈亏再喂一遍风控

        这比丢一条归因日志危险得多：重复上报会凭空制造连亏，误触发熔断锁仓。
        """
        gm = self._make_gm()
        gm.reconcile_netting_closes("ETH", fills=[_fill("t0", 1000)])

        def _boom(**kwargs):
            raise RuntimeError("磁盘满了")

        gm.logger.log_trade = _boom
        fills = [
            _fill("t0", 1000),
            _fill("t1", 2000, closed_pnl="1.0"),
            _fill("t2", 3000, closed_pnl="-2.0"),
        ]
        res = gm.reconcile_netting_closes("ETH", fills=fills)

        self.assertEqual(res["processed"], 2, "日志失败不应中断后续归因")
        self.assertEqual(len(self.reported), 2, "风控上报不受日志失败影响")
        self.assertEqual(gm.state["netting_attribution"]["ETH"]["cursor_ms"], 3000)

        # 关键：下一轮不得重复上报
        gm.logger.log_trade = lambda **kw: None
        gm.reconcile_netting_closes("ETH", fills=fills)
        self.assertEqual(len(self.reported), 2, "游标已推进，不得重复上报")

    def test_fetch_failure_is_contained(self):
        """取成交失败不得抛出，跳过本轮即可"""
        om = MagicMock()
        om.client.info.user_fills.side_effect = RuntimeError("网络炸了")
        gm = self._make_gm(order_manager=om)

        res = gm.reconcile_netting_closes("ETH")

        self.assertEqual(res["skipped"], "fetch_failed")
        self.assertEqual(self.reported, [])

    def test_cursor_survives_restart(self):
        """游标持久化：重启后不重复归因已处理过的成交"""
        fills = [_fill("t0", 1000), _fill("t1", 2000, closed_pnl="2.0")]
        gm = self._make_gm()
        gm.reconcile_netting_closes("ETH", fills=[_fill("t0", 1000)])
        gm.reconcile_netting_closes("ETH", fills=fills)
        self.assertEqual(len(self.reported), 1)

        # 新实例从同一状态文件恢复
        gm2 = self._make_gm()
        gm2.reconcile_netting_closes("ETH", fills=fills)

        self.assertEqual(len(self.reported), 1, "重启后不得重复归因")


class TestAttributionOwnershipIsExclusive(_GridManagerTestBase):
    """归因源互斥：开启净额归因后层级状态机不得再上报，避免双重计数"""

    @staticmethod
    def _completed_level() -> GridLevel:
        level = GridLevel(
            id="L0",
            price=Decimal("2000"),
            amount=Decimal("25"),
            side="LONG",
            state=GridLevelState.COMPLETED,
        )
        level.open_fill_price = Decimal("2000")
        level.open_fill_amount = Decimal("0.01")
        level.close_fill_price = Decimal("2010")
        return level

    def test_level_state_machine_yields_when_netting_enabled(self):
        gm = self._make_gm(netting_enabled=True)
        gm._record_round_trip("ETH", self._completed_level())

        self.assertEqual(self.reported, [], "净额归因接管时层级状态机不得上报")
        # 但日志侧职责保留，归因记录不能丢
        self.assertTrue(any(t["action"] == "GRID_ROUND_TRIP" for t in self.logger.trades))

    def test_level_state_machine_still_reports_when_disabled(self):
        """未开启净额归因时保持历史行为：层级状态机照常上报"""
        gm = self._make_gm(netting_enabled=False)
        gm._record_round_trip("ETH", self._completed_level())

        self.assertEqual(len(self.reported), 1)
        self.assertGreater(self.reported[0][1], 0)


class TestIsGridIdle(_GridManagerTestBase):
    """空转判定：无层级、无持仓，且交易所上也没有活跃网格挂单"""

    def _make_idle_gm(self, orders=None, position=0.0):
        gm = self._make_gm()
        gm._get_symbol_position_size = lambda s: position
        gm._get_symbol_open_orders = lambda s, include_trigger=False: list(orders or [])
        return gm

    def test_idle_when_nothing_is_working(self):
        self.assertTrue(self._make_idle_gm().is_grid_idle("ETH"))

    def test_not_idle_with_levels(self):
        gm = self._make_idle_gm()
        gm.grid_levels["ETH"] = [GridLevel(id="L0", price=2000, amount=25, side="LONG")]
        self.assertFalse(gm.is_grid_idle("ETH"))

    def test_not_idle_with_position(self):
        self.assertFalse(self._make_idle_gm(position=-0.05).is_grid_idle("ETH"))

    def test_not_idle_when_exchange_still_has_grid_orders(self):
        """本地层级为空不代表网格没在工作。

        全量重建路径只把订单快照写进状态文件，levels 要等下一轮增量同步才建立。
        线上实测过这个窗口：交易所挂着完整的 12 个网格单、本地层级却是空的，
        只看层级会误判成空转，触发一次没必要的撤单重布。
        """
        gm = self._make_idle_gm(
            orders=[
                {"oid": 1, "coin": "ETH", "side": "B", "limitPx": "1837.9"},
                {"oid": 2, "coin": "ETH", "side": "A", "limitPx": "1881.7"},
            ]
        )
        self.assertFalse(gm.is_grid_idle("ETH"))

    def test_reduce_only_orders_do_not_count_as_working(self):
        """减仓保护单在持仓清零后可能残留，不代表网格在做市"""
        gm = self._make_idle_gm(
            orders=[{"oid": 1, "coin": "ETH", "side": "A", "reduceOnly": True}]
        )
        self.assertTrue(gm.is_grid_idle("ETH"))

    def test_query_failure_is_conservative(self):
        """查不到持仓/挂单时按「非空转」处理：宁可不动，也不误建网格"""

        def _boom(*a, **k):
            raise RuntimeError("查询失败")

        gm = self._make_idle_gm()
        gm._get_symbol_position_size = _boom
        self.assertFalse(gm.is_grid_idle("ETH"))

        gm2 = self._make_idle_gm()
        gm2._get_symbol_open_orders = _boom
        self.assertFalse(gm2.is_grid_idle("ETH"))

    def test_sync_grid_idle_warning_shares_the_same_judgement(self):
        """KEEP_GRID 周期的空转告警必须与 is_grid_idle 同判据，否则日志误导人。

        线上就是被这条误报坑过：交易所挂着 12 个网格单，日志连刷 4 个周期「空转」。
        """
        warnings = []
        gm = self._make_idle_gm(
            orders=[{"oid": 1, "coin": "ETH", "side": "B", "limitPx": "1837.9"}]
        )
        gm.logger.print_warning = lambda *a, **k: warnings.append(" ".join(str(x) for x in a))
        gm._cleanup_orphan_trigger_orders = lambda s: None
        gm._ensure_min_orders = lambda symbol: None

        gm.sync_grid("ETH", {"action": "KEEP_GRID"})
        self.assertFalse(
            any("空转" in w for w in warnings), "交易所还有网格挂单时不得报空转"
        )

        gm2 = self._make_idle_gm()  # 真空转：无层级、无持仓、无挂单
        warnings2 = []
        gm2.logger.print_warning = lambda *a, **k: warnings2.append(" ".join(str(x) for x in a))
        gm2._cleanup_orphan_trigger_orders = lambda s: None
        gm2._ensure_min_orders = lambda symbol: None

        gm2.sync_grid("ETH", {"action": "KEEP_GRID"})
        self.assertTrue(any("空转" in w for w in warnings2), "真空转时仍须告警")


class TestRoundTripCallbackCompat(_GridManagerTestBase):
    """round-trip 回调的签名兼容：探测而非 try/except TypeError"""

    def test_three_arg_callback_receives_forced(self):
        got = []
        gm = self._make_gm(netting_enabled=False)
        gm.on_round_trip_close = lambda s, p, forced=False: got.append((s, p, forced))
        gm._dispatch_round_trip_close("ETH", -1.0, forced=True)
        self.assertEqual(got, [("ETH", -1.0, True)])

    def test_two_arg_callback_still_supported(self):
        """未升级的两参回调（旧回测桩/自定义接线）仍可用"""
        got = []
        gm = self._make_gm(netting_enabled=False)
        gm.on_round_trip_close = lambda s, p: got.append((s, p))
        gm._dispatch_round_trip_close("ETH", -1.0, forced=True)
        self.assertEqual(got, [("ETH", -1.0)])

    def test_callback_raising_typeerror_is_not_retried(self):
        """回调内部抛 TypeError 不得被误判成「签名不兼容」而重调。

        旧实现用 try/except TypeError 降级重调，回调内部自己抛 TypeError 时会把
        同一笔盈亏上报两次，凭空制造连亏。
        """
        calls = []

        def _cb(symbol, pnl, forced=False):
            calls.append((symbol, pnl))
            raise TypeError("回调内部自己炸了，不是签名问题")

        gm = self._make_gm(netting_enabled=False)
        gm.on_round_trip_close = _cb
        gm._dispatch_round_trip_close("ETH", -1.0)  # 异常被吞，不抛出

        self.assertEqual(len(calls), 1, "同一笔盈亏不得上报两次")

    def test_probe_is_cached_and_refreshed_on_swap(self):
        """签名探测结果被缓存；换了回调要重新探测"""
        gm = self._make_gm(netting_enabled=False)
        three = []
        gm.on_round_trip_close = lambda s, p, forced=False: three.append(forced)
        gm._dispatch_round_trip_close("ETH", 1.0, forced=True)
        gm._dispatch_round_trip_close("ETH", 1.0, forced=True)
        self.assertEqual(three, [True, True])

        two = []
        gm.on_round_trip_close = lambda s, p: two.append((s, p))
        gm._dispatch_round_trip_close("ETH", 2.0, forced=True)
        self.assertEqual(two, [("ETH", 2.0)], "换成两参回调后应重新探测")


def _make_agent(available=1000.0, balance_status="ok", adaptive_sizing=False) -> GridAgent:
    om = MagicMock()
    om.get_available_balance_info.return_value = {
        "status": balance_status,
        "available": available,
        "message": "余额接口超时",
    }
    llm_manager = MagicMock()
    return GridAgent(
        symbol="ETH",
        order_manager=om,
        logger=_DummyLogger(),
        llm_manager=llm_manager,
        trade_amount=100.0,
        force_neutral_mode=True,
        max_leverage=5,
        adaptive_sizing=adaptive_sizing,
    )


_MARKET_DATA = {
    "current_price": 1800.0,
    "ma_7": 1800.0,
    "ma_25": 1801.0,
    "rsi": 45.0,
    "bb_upper": 1820.0,
    "bb_lower": 1780.0,
    "volume_change": 10.0,
}


class TestGridAgentLlmOkFlag(unittest.TestCase):
    """llm_ok 标：把 LLM 故障与 AI 真实判断区分开"""

    @staticmethod
    def _run_with_llm_output(agent: GridAgent, output):
        """patch 掉 pydantic_ai.Agent，让 run_sync 返回指定输出"""
        fake_agent = MagicMock()
        fake_agent.run_sync.return_value = SimpleNamespace(output=output)
        with patch("pydantic_ai.Agent", return_value=fake_agent):
            return agent.make_decision(_MARKET_DATA, {}, "")

    def test_empty_output_marks_llm_failed(self):
        decision = self._run_with_llm_output(_make_agent(), "")
        self.assertEqual(decision["action"], "KEEP_GRID")
        self.assertIs(decision["llm_ok"], False)

    def test_unparseable_output_marks_llm_failed(self):
        decision = self._run_with_llm_output(_make_agent(), "这不是 JSON，模型在胡说")
        self.assertEqual(decision["action"], "KEEP_GRID")
        self.assertIs(decision["llm_ok"], False)

    def test_invalid_action_marks_llm_failed(self):
        decision = self._run_with_llm_output(
            _make_agent(), '{"action": "UPDATE_GRIDLE", "mode": "NEUTRAL"}'
        )
        self.assertEqual(decision["action"], "KEEP_GRID")
        self.assertIs(decision["llm_ok"], False)

    def test_call_exception_marks_llm_failed(self):
        """三次重试全失败（如模型名被供应商下线，每次都 400）"""
        agent = _make_agent()
        fake_agent = MagicMock()
        fake_agent.run_sync.side_effect = RuntimeError("status_code: 400, model not supported")
        with patch("pydantic_ai.Agent", return_value=fake_agent), patch(
            "src.agent.grid_agent.time.sleep"
        ):
            decision = agent.make_decision(_MARKET_DATA, {}, "")

        self.assertEqual(decision["action"], "ERROR")
        self.assertIs(decision["llm_ok"], False)
        self.assertEqual(fake_agent.run_sync.call_count, 3, "应重试 3 次")

    def test_genuine_keep_grid_is_marked_ok(self):
        """AI 真实判断的 KEEP_GRID 不能被误判为故障"""
        decision = self._run_with_llm_output(
            _make_agent(),
            '{"action": "KEEP_GRID", "mode": "NEUTRAL", "confidence": 0.8, "reason": "盘整"}',
        )
        self.assertEqual(decision["action"], "KEEP_GRID")
        self.assertIs(decision["llm_ok"], True)

    def test_update_grid_is_marked_ok(self):
        decision = self._run_with_llm_output(
            _make_agent(),
            '{"action": "UPDATE_GRID", "mode": "NEUTRAL", "width_pct": 0.05,'
            ' "grid_num": 6, "confidence": 0.7, "reason": "重建"}',
        )
        self.assertEqual(decision["action"], "UPDATE_GRID")
        self.assertIs(decision["llm_ok"], True)

    def test_balance_failure_is_not_an_llm_failure(self):
        """余额接口故障不该计入 LLM 连续失败告警"""
        decision = self._run_with_llm_output(
            _make_agent(balance_status="error"),
            '{"action": "UPDATE_GRID", "mode": "NEUTRAL", "width_pct": 0.05,'
            ' "grid_num": 6, "confidence": 0.7, "reason": "重建"}',
        )
        self.assertEqual(decision["action"], "KEEP_GRID")
        self.assertIs(decision["llm_ok"], True)


class TestBuildFallbackConfig(unittest.TestCase):
    """不经 LLM 的兜底建网格"""

    def test_builds_neutral_grid_from_market_data(self):
        decision = _make_agent().build_fallback_config(_MARKET_DATA)

        self.assertEqual(decision["action"], "UPDATE_GRID")
        self.assertEqual(decision["mode"], "NEUTRAL")
        self.assertIs(decision["fallback"], True)
        self.assertIs(decision["llm_ok"], False, "兜底不是 LLM 恢复，不得清零故障计数")
        self.assertGreater(decision["width_pct"], 0)

    def test_never_guesses_direction(self):
        """LLM 不可用时不猜方向，只做对称中性网格"""
        agent = _make_agent()
        agent.force_neutral_mode = False  # 即便未强制中性，兜底也只出 NEUTRAL
        self.assertEqual(agent.build_fallback_config(_MARKET_DATA)["mode"], "NEUTRAL")

    def test_missing_price_falls_back_to_keep_grid(self):
        decision = _make_agent().build_fallback_config({"current_price": 0})
        self.assertEqual(decision["action"], "KEEP_GRID")

    def test_balance_failure_falls_back_to_keep_grid(self):
        """余额取不到时不重建：会撤光旧单又挂不出新单"""
        decision = _make_agent(balance_status="error").build_fallback_config(_MARKET_DATA)
        self.assertEqual(decision["action"], "KEEP_GRID")
        self.assertIs(decision["llm_ok"], True)

    def test_insufficient_capital_is_surfaced(self):
        """自适应仓位下资金撑不起最小格数时拒绝布单，而非抬高单格金额硬建"""
        agent = _make_agent(available=1.0, adaptive_sizing=True)
        decision = agent.build_fallback_config(_MARKET_DATA)
        self.assertEqual(decision["action"], "INSUFFICIENT_CAPITAL")


def _make_bot(alert_cycles=3, rebuild_cycles=4):
    """构造未初始化的 QuantFlowBot，只挂上被测方法所需的最小依赖。"""
    from main import QuantFlowBot

    bot = QuantFlowBot.__new__(QuantFlowBot)
    bot.config = SimpleNamespace(
        grid_llm_failure_alert_cycles=alert_cycles,
        grid_llm_fallback_rebuild_cycles=rebuild_cycles,
    )
    bot.logger = _DummyLogger()
    bot.notifier = SimpleNamespace(enabled=True, errors=[])
    bot.notifier.notify_error = lambda **kw: bot.notifier.errors.append(kw)
    bot._grid_llm_failure_streak = {}
    bot._grid_idle_streak = {}
    bot._grid_llm_alert_sent = {}
    bot.grid_agent = MagicMock()
    bot.grid_manager = MagicMock()
    bot.grid_manager.is_grid_idle.return_value = True
    return bot


class TestLlmFailureAlerting(unittest.TestCase):
    """LLM 连续故障 → 升级告警"""

    def test_alerts_once_after_threshold(self):
        bot = _make_bot(alert_cycles=3)
        failed = {"action": "ERROR", "reason": "400 model not supported", "llm_ok": False}

        for _ in range(2):
            bot._track_grid_llm_health("ETH", failed)
        self.assertEqual(bot.notifier.errors, [], "未达阈值不得告警")

        bot._track_grid_llm_health("ETH", failed)
        self.assertEqual(len(bot.notifier.errors), 1)
        self.assertIn("连续 3 个周期失败", bot.notifier.errors[0]["error_message"])

        # 故障持续期间不重复刷屏
        for _ in range(10):
            bot._track_grid_llm_health("ETH", failed)
        self.assertEqual(len(bot.notifier.errors), 1, "故障期间只告警一次")

    def test_recovery_resets_and_rearms(self):
        """恢复后计数清零，下次故障可再次告警"""
        bot = _make_bot(alert_cycles=2)
        failed = {"action": "ERROR", "llm_ok": False}
        healthy = {"action": "KEEP_GRID", "llm_ok": True}

        bot._track_grid_llm_health("ETH", failed)
        bot._track_grid_llm_health("ETH", failed)
        self.assertEqual(len(bot.notifier.errors), 1)

        bot._track_grid_llm_health("ETH", healthy)
        self.assertEqual(bot._grid_llm_failure_streak["ETH"], 0)

        bot._track_grid_llm_health("ETH", failed)
        bot._track_grid_llm_health("ETH", failed)
        self.assertEqual(len(bot.notifier.errors), 2, "恢复后应重新武装告警")

    def test_disabled_by_default(self):
        bot = _make_bot(alert_cycles=0)
        for _ in range(50):
            bot._track_grid_llm_health("ETH", {"action": "ERROR", "llm_ok": False})
        self.assertEqual(bot.notifier.errors, [])

    def test_missing_flag_is_treated_as_healthy(self):
        """老调用方/回测桩不带 llm_ok 时不得误报故障"""
        bot = _make_bot(alert_cycles=1)
        bot._track_grid_llm_health("ETH", {"action": "KEEP_GRID"})
        self.assertEqual(bot.notifier.errors, [])

    def test_notifier_failure_does_not_propagate(self):
        """通知渠道自身故障不得拖垮交易周期"""
        bot = _make_bot(alert_cycles=1)

        def _boom(**kw):
            raise RuntimeError("钉钉挂了")

        bot.notifier.notify_error = _boom
        bot._track_grid_llm_health("ETH", {"action": "ERROR", "llm_ok": False})  # 不抛即通过


class TestIdleSelfHealing(unittest.TestCase):
    """空转自愈：连续 N 周期空转后用市场数据兜底重建"""

    def test_rebuilds_after_threshold(self):
        bot = _make_bot(rebuild_cycles=3)
        rebuilt = {"action": "UPDATE_GRID", "mode": "NEUTRAL", "reason": "兜底"}
        bot.grid_agent.build_fallback_config.return_value = rebuilt
        idle = {"action": "KEEP_GRID", "llm_ok": False}

        for _ in range(2):
            out = bot._maybe_fallback_rebuild("ETH", idle, _MARKET_DATA)
            self.assertEqual(out["action"], "KEEP_GRID", "未达阈值不重建")

        out = bot._maybe_fallback_rebuild("ETH", idle, _MARKET_DATA)
        self.assertEqual(out["action"], "UPDATE_GRID")
        self.assertEqual(bot._grid_idle_streak["ETH"], 0, "重建后计数清零")

    def test_trend_paused_cycles_never_trigger_rebuild(self):
        """强趋势暂停期间建网格，正是趋势过滤要阻止的事"""
        bot = _make_bot(rebuild_cycles=1)
        paused = {"action": "KEEP_GRID", "llm_ok": True, "trend_paused": True}

        for _ in range(10):
            out = bot._maybe_fallback_rebuild("ETH", paused, _MARKET_DATA)
            self.assertEqual(out["action"], "KEEP_GRID")
        bot.grid_agent.build_fallback_config.assert_not_called()

    def test_non_idle_grid_never_triggers_rebuild(self):
        """网格还有层级/持仓时不算空转"""
        bot = _make_bot(rebuild_cycles=1)
        bot.grid_manager.is_grid_idle.return_value = False

        for _ in range(10):
            bot._maybe_fallback_rebuild("ETH", {"action": "KEEP_GRID", "llm_ok": False}, _MARKET_DATA)
        bot.grid_agent.build_fallback_config.assert_not_called()

    def test_update_grid_resets_streak(self):
        """LLM 恢复并给出 UPDATE_GRID 时，空转计数清零"""
        bot = _make_bot(rebuild_cycles=3)
        bot._maybe_fallback_rebuild("ETH", {"action": "KEEP_GRID", "llm_ok": False}, _MARKET_DATA)
        self.assertEqual(bot._grid_idle_streak["ETH"], 1)

        bot._maybe_fallback_rebuild("ETH", {"action": "UPDATE_GRID", "llm_ok": True}, _MARKET_DATA)
        self.assertEqual(bot._grid_idle_streak["ETH"], 0)

    def test_disabled_by_default(self):
        bot = _make_bot(rebuild_cycles=0)
        for _ in range(50):
            bot._maybe_fallback_rebuild("ETH", {"action": "KEEP_GRID", "llm_ok": False}, _MARKET_DATA)
        bot.grid_agent.build_fallback_config.assert_not_called()

    def test_failed_rebuild_backs_off_instead_of_retrying_every_cycle(self):
        """兜底失败（如资金不足）后重新攒周期，不得每轮重试刷屏"""
        bot = _make_bot(rebuild_cycles=2)
        bot.grid_agent.build_fallback_config.return_value = {
            "action": "INSUFFICIENT_CAPITAL",
            "reason": "资金不足",
        }
        idle = {"action": "KEEP_GRID", "llm_ok": False}

        bot._maybe_fallback_rebuild("ETH", idle, _MARKET_DATA)
        bot._maybe_fallback_rebuild("ETH", idle, _MARKET_DATA)
        self.assertEqual(bot.grid_agent.build_fallback_config.call_count, 1)

        bot._maybe_fallback_rebuild("ETH", idle, _MARKET_DATA)
        self.assertEqual(bot.grid_agent.build_fallback_config.call_count, 1, "应退避而非每轮重试")

        bot._maybe_fallback_rebuild("ETH", idle, _MARKET_DATA)
        self.assertEqual(bot.grid_agent.build_fallback_config.call_count, 2)

    def test_rebuild_exception_is_contained(self):
        bot = _make_bot(rebuild_cycles=1)
        bot.grid_agent.build_fallback_config.side_effect = RuntimeError("建网格炸了")

        out = bot._maybe_fallback_rebuild("ETH", {"action": "KEEP_GRID", "llm_ok": False}, _MARKET_DATA)
        self.assertEqual(out["action"], "KEEP_GRID", "异常时返回原决策，不得中断周期")


if __name__ == "__main__":
    unittest.main()
