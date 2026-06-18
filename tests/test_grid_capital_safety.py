"""网格资金安全改造的回归测试。

覆盖以下修复：
1. 杠杆平方修复：execute_long_limit/execute_short_limit 在 amount_is_notional=True 时
   合约数量 = 名义额 / 价格，不再额外乘杠杆。
2. 内层 statuses 校验：交易所外层返回 status=ok 但内层含 error 时判定为失败。
3. 减仓单最小名义额校验：低于 $10 名义额的减仓单不再下达（消灭灰尘单）。
4. 重建冷却：距上次重建不足冷却期时不全量重建，但挂单不足等安全触发仍立即重建。
5. GridAgent 输出加固：解析失败/非法 action 回退 KEEP_GRID，置信度透传。
"""

import inspect
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from src.agent.grid_agent import GridAgent
from src.backtest.mock_order_manager import MockOrderManager
from src.trading.client import HyperliquidClient
from src.trading.grid_manager import GridManager
from src.trading.order_manager import OrderManager


class DummyLogger:
    def print_section(self, *args, **kwargs):
        pass

    def print_header(self, *args, **kwargs):
        pass

    def print_info(self, *args, **kwargs):
        pass

    def print_warning(self, *args, **kwargs):
        pass

    def print_error(self, *args, **kwargs):
        pass

    def print_market_data(self, *args, **kwargs):
        pass

    def log_trade(self, *args, **kwargs):
        pass

    def log_decision(self, *args, **kwargs):
        pass


def _resting_response(oid):
    return {
        "status": "ok",
        "response": {"type": "order", "data": {"statuses": [{"resting": {"oid": oid}}]}},
    }


def _reject_response(msg="Order has invalid size"):
    """交易所拒单：外层仍为 status=ok，错误藏在内层 statuses[].error。"""
    return {"status": "ok", "response": {"type": "order", "data": {"statuses": [{"error": msg}]}}}


class OrderClient:
    """OrderManager 测试用的最小客户端。"""

    def __init__(self, sz_decimals=4, reject=False):
        self.sz_decimals = sz_decimals
        self.reject = reject
        self.place_calls = []
        self._oid = 5000

    def get_asset_info(self, symbol):
        return {"szDecimals": self.sz_decimals}

    def get_current_positions(self):
        return []

    def get_positions(self):
        return []

    def update_leverage(self, symbol, lev, is_cross=False):
        return {"status": "ok"}

    def format_price(self, symbol, price):
        return round(float(price), 1)

    def place_limit_order(self, symbol, is_buy, size, price, reduce_only=False):
        self.place_calls.append(
            {
                "symbol": symbol,
                "is_buy": is_buy,
                "size": size,
                "price": price,
                "reduce_only": reduce_only,
            }
        )
        if self.reject:
            return _reject_response()
        self._oid += 1
        return _resting_response(self._oid)

    # 直接复用真实客户端的静态校验逻辑，保证 mock 与生产行为一致。
    # 注意：此绑定依赖 check_order_success/get_order_fill_info 始终为 @staticmethod；
    # 若它们将来改为实例方法（依赖 self），此处需改为包装函数，否则会丢失 self 绑定。
    check_order_success = staticmethod(HyperliquidClient.check_order_success)
    get_order_fill_info = staticmethod(HyperliquidClient.get_order_fill_info)


class TestLeverageNotionalFix(unittest.TestCase):
    """杠杆平方修复：名义额口径下单不再重复乘杠杆。"""

    def _make_om(self, client, default_leverage=10):
        return OrderManager(
            client=client,
            default_leverage=default_leverage,
            enable_limit_order_monitor=False,
        )

    def test_long_notional_size_excludes_leverage(self):
        client = OrderClient(sz_decimals=4)
        om = self._make_om(client, default_leverage=10)

        # 名义额 $155，价格 $1550 -> 合约数量应为 0.1（=155/1550），与杠杆无关
        res = om.execute_long_limit(
            "ETH",
            155.0,
            1550.0,
            with_take_profit=False,
            with_stop_loss=False,
            amount_is_notional=True,
        )
        self.assertTrue(res["success"])
        self.assertAlmostEqual(client.place_calls[0]["size"], 0.1, places=6)

    def test_short_notional_size_excludes_leverage(self):
        client = OrderClient(sz_decimals=4)
        om = self._make_om(client, default_leverage=10)
        res = om.execute_short_limit(
            "ETH",
            155.0,
            1550.0,
            with_take_profit=False,
            with_stop_loss=False,
            amount_is_notional=True,
        )
        self.assertTrue(res["success"])
        self.assertAlmostEqual(client.place_calls[0]["size"], 0.1, places=6)

    def test_margin_path_still_multiplies_leverage(self):
        """非名义额口径（主策略路径）保持保证金 × 杠杆 的旧语义。"""
        client = OrderClient(sz_decimals=4)
        om = self._make_om(client, default_leverage=10)
        # 保证金 $15.5，10x，价格 $1550 -> 0.1（=15.5*10/1550）
        res = om.execute_long_limit(
            "ETH", 15.5, 1550.0, with_take_profit=False, with_stop_loss=False
        )
        self.assertTrue(res["success"])
        self.assertAlmostEqual(client.place_calls[0]["size"], 0.1, places=6)


class TestInnerStatusRejection(unittest.TestCase):
    """交易所内层拒单必须被识别为失败。"""

    def test_rejected_order_returns_failure(self):
        client = OrderClient(reject=True)
        om = OrderManager(client=client, default_leverage=5, enable_limit_order_monitor=False)
        res = om.execute_long_limit(
            "ETH",
            100.0,
            1500.0,
            with_take_profit=False,
            with_stop_loss=False,
            amount_is_notional=True,
        )
        self.assertIsNotNone(res)
        self.assertFalse(res["success"])

    def test_check_order_success_detects_inner_error(self):
        ok, err = HyperliquidClient.check_order_success(_reject_response("min size"))
        self.assertFalse(ok)
        self.assertIn("min size", err)
        ok2, _ = HyperliquidClient.check_order_success(_resting_response(1))
        self.assertTrue(ok2)


class TestSignatureAndGuards(unittest.TestCase):
    """接口契约与边界保护。"""

    def test_mock_order_manager_accepts_amount_is_notional(self):
        # 回测 mock 必须与生产 OrderManager 接口对齐，否则网格回测路径传入该参数会 TypeError
        for name in ("execute_long_limit", "execute_short_limit"):
            params = inspect.signature(getattr(MockOrderManager, name)).parameters
            self.assertIn("amount_is_notional", params, f"{name} 缺少 amount_is_notional 参数")

    def test_zero_price_rejected_without_exception(self):
        # limit_price=0 不得触发除零异常，应返回失败
        client = OrderClient()
        om = OrderManager(client=client, default_leverage=10, enable_limit_order_monitor=False)
        res = om.execute_long_limit(
            "ETH", 100.0, 0.0, with_take_profit=False, with_stop_loss=False, amount_is_notional=True
        )
        self.assertIsNotNone(res)
        self.assertFalse(res["success"])
        self.assertEqual(len(client.place_calls), 0)


# ───────────────────────── GridManager 测试用 mock ─────────────────────────


class GridFakeClient:
    def __init__(self, open_orders=None, sz_decimals=4):
        self.open_orders = open_orders or []
        self.sz_decimals = sz_decimals
        self.place_calls = []
        self._oid = 7000

    def get_open_orders(self, include_trigger=False):
        return [dict(o) for o in self.open_orders]

    def get_current_price(self, symbol):
        return 1600.0

    def get_asset_info(self, symbol):
        return {"szDecimals": self.sz_decimals}

    def format_price(self, symbol, price):
        return round(float(price), 1)

    def place_limit_order(self, symbol, is_buy, size, price, reduce_only=False):
        self.place_calls.append(
            {
                "symbol": symbol,
                "is_buy": is_buy,
                "size": size,
                "price": price,
                "reduce_only": reduce_only,
            }
        )
        self._oid += 1
        self.open_orders.append(
            {
                "oid": self._oid,
                "coin": symbol,
                "side": "B" if is_buy else "A",
                "sz": str(size),
                "limitPx": str(price),
            }
        )
        return _resting_response(self._oid)

    check_order_success = staticmethod(HyperliquidClient.check_order_success)


class GridFakeOM:
    def __init__(self, client, positions=None):
        self.client = client
        self.positions = positions or []
        self.default_leverage = 10

    def get_current_positions(self):
        return [dict(p) for p in self.positions]


def _make_grid_manager(client, positions=None, state_file=None):
    om = GridFakeOM(client, positions=positions)
    return GridManager(order_manager=om, logger=DummyLogger(), state_file=state_file)


class TestDustReduceOrders(unittest.TestCase):
    """减仓单最小名义额校验：不再产生 0.0001 量级灰尘虚单。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_file = str(Path(self.tmp.name) / "grid_state.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_reduce_orders_respect_min_notional(self):
        # 较大空头持仓（0.5 ETH @ $1600 ≈ $800），应布若干 ≥$10 的减仓买单
        client = GridFakeClient()
        gm = _make_grid_manager(
            client, positions=[{"coin": "ETH", "szi": "-0.5"}], state_file=self.state_file
        )
        gm._ensure_min_orders(symbol="ETH")

        reduce_calls = [c for c in client.place_calls if c["reduce_only"]]
        self.assertGreaterEqual(len(reduce_calls), 1)
        for c in reduce_calls:
            notional = c["size"] * c["price"]
            self.assertGreaterEqual(notional, 10.0, f"减仓单名义额 {notional} 低于 $10 最小额")

    def test_tiny_position_places_no_dust(self):
        # 极小持仓（0.0002 ETH ≈ $0.32），整笔都凑不到 $10，应一笔减仓单都不下
        client = GridFakeClient()
        gm = _make_grid_manager(
            client, positions=[{"coin": "ETH", "szi": "-0.0002"}], state_file=self.state_file
        )
        gm._ensure_min_orders(symbol="ETH")

        reduce_calls = [c for c in client.place_calls if c["reduce_only"]]
        self.assertEqual(len(reduce_calls), 0)

    def test_small_position_merges_into_single_order(self):
        # 持仓 0.01 ETH ≈ $16，只够一笔合法减仓单：应合并为 1 笔覆盖全仓，而非拆成多笔灰尘单
        client = GridFakeClient()
        gm = _make_grid_manager(
            client, positions=[{"coin": "ETH", "szi": "-0.01"}], state_file=self.state_file
        )
        gm._ensure_min_orders(symbol="ETH")

        reduce_calls = [c for c in client.place_calls if c["reduce_only"]]
        self.assertEqual(len(reduce_calls), 1)
        notional = reduce_calls[0]["size"] * reduce_calls[0]["price"]
        self.assertGreaterEqual(notional, 10.0)


class TestRebuildCooldown(unittest.TestCase):
    """重建冷却：抑制高频全量重建，但不阻断安全性触发。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_file = str(Path(self.tmp.name) / "grid_state.json")

    def tearDown(self):
        self.tmp.cleanup()

    def _seed_grid(self, gm, lower=1500.0, upper=1700.0, grid_num=8):
        config = {
            "lower_price": lower,
            "upper_price": upper,
            "grid_num": grid_num,
            "grid_type": "GEOMETRIC",
            "mode": "NEUTRAL",
            "amount_per_grid": 20.0,
        }
        gm.state["active_grids"]["ETH"] = {"config": config, "buy_orders": [], "sell_orders": []}

    def _new_config(self, lower=1400.0, upper=1600.0, grid_num=8):
        # 区间显著平移，正常情况下会触发价格变化重建
        return {
            "action": "UPDATE_GRID",
            "lower_price": lower,
            "upper_price": upper,
            "grid_num": grid_num,
            "grid_type": "GEOMETRIC",
            "mode": "NEUTRAL",
            "amount_per_grid": 20.0,
        }

    def test_within_cooldown_blocks_rebuild(self):
        # 挂单充足，避免触发"挂单不足"安全重建
        client = GridFakeClient(
            open_orders=[{"oid": i, "coin": "ETH", "side": "B"} for i in range(4)]
        )
        gm = _make_grid_manager(client, state_file=self.state_file)
        self._seed_grid(gm)
        gm._last_rebuild_ts["ETH"] = time.time()  # 刚刚重建过

        should, reason = gm._should_rebuild_grid("ETH", self._new_config())
        self.assertFalse(should, f"冷却期内不应重建，却返回: {reason}")
        self.assertIn("冷却", reason)

    def test_after_cooldown_allows_rebuild(self):
        client = GridFakeClient(
            open_orders=[{"oid": i, "coin": "ETH", "side": "B"} for i in range(4)]
        )
        gm = _make_grid_manager(client, state_file=self.state_file)
        self._seed_grid(gm)
        gm._last_rebuild_ts["ETH"] = time.time() - (gm.grid_rebuild_cooldown_seconds + 60)

        should, _ = gm._should_rebuild_grid("ETH", self._new_config())
        self.assertTrue(should)

    def test_insufficient_orders_overrides_cooldown(self):
        # 冷却期内但挂单不足：安全性触发必须立即重建
        client = GridFakeClient(open_orders=[])
        gm = _make_grid_manager(client, state_file=self.state_file)
        self._seed_grid(gm)
        gm._last_rebuild_ts["ETH"] = time.time()

        should, reason = gm._should_rebuild_grid("ETH", self._new_config())
        self.assertTrue(should)
        self.assertIn("挂单数量不足", reason)


# ───────────────────────── GridAgent 测试用 mock ─────────────────────────


class AgentFakeLLM:
    def __init__(self, content):
        self._content = content

    def invoke(self, messages):
        class _R:
            pass

        r = _R()
        r.content = self._content
        return r


class AgentFakeLLMManager:
    def __init__(self, content):
        self._content = content

    def get_client(self, temperature=0.1):
        return AgentFakeLLM(self._content)


class AgentFakeLLMFlaky:
    """前 fail_times 次 invoke 抛 400 异常，之后返回有效内容——验证有界重试自愈。

    模拟推理模型偶发空正文触发 Pydantic AI 回填空 assistant 消息、被 DeepSeek 以
    400 "Invalid assistant message" 拒绝的瞬时故障。
    """

    def __init__(self, content, fail_times):
        self._content = content
        self._fail_times = int(fail_times)
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise RuntimeError(
                "status_code: 400, model_name: deepseek-v4-pro, body: "
                "Invalid assistant message: content or tool_calls must be set"
            )

        class _R:
            pass

        r = _R()
        r.content = self._content
        return r


class AgentFakeLLMAlwaysFails:
    """每次 invoke 都抛 400——验证重试耗尽后保留 action=ERROR 语义（下游与 KEEP_GRID 同样仅维持网格）。"""

    def __init__(self):
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        raise RuntimeError(
            "status_code: 400, model_name: deepseek-v4-pro, body: "
            "Invalid assistant message: content or tool_calls must be set"
        )


class AgentFakeLLMManagerCustom:
    """返回预构造的有状态假 LLM 实例（保证跨多次 run_sync 重试时调用计数持续累加）。"""

    def __init__(self, client):
        self._client = client

    def get_client(self, temperature=0.1):
        return self._client


class AgentFakeOM:
    def __init__(self, balance_status="ok"):
        self._balance_status = balance_status

    def get_available_balance_info(self):
        if self._balance_status != "ok":
            return {"status": "error", "message": "余额接口失败", "available": 0}
        return {"status": "ok", "available": 100.0}


def _make_agent(content, balance_status="ok"):
    return GridAgent(
        symbol="ETH",
        order_manager=AgentFakeOM(balance_status=balance_status),
        logger=DummyLogger(),
        llm_manager=AgentFakeLLMManager(content),
        trade_amount=100.0,
    )


def _make_agent_with_client(client, balance_status="ok"):
    return GridAgent(
        symbol="ETH",
        order_manager=AgentFakeOM(balance_status=balance_status),
        logger=DummyLogger(),
        llm_manager=AgentFakeLLMManagerCustom(client),
        trade_amount=100.0,
    )


class TestGridAgentHardening(unittest.TestCase):
    """GridAgent 输出加固。"""

    MARKET = {
        "current_price": 1600.0,
        "rsi": 50.0,
        "macd_hist": 0.0,
        "bb_upper": 1650.0,
        "bb_lower": 1550.0,
        "high": 1620.0,
        "low": 1580.0,
        "volume_change": 0.0,
    }

    def test_parse_failure_falls_back_to_keep_grid(self):
        # 非 JSON 输出（模拟线上 "Expecting value: line 401..." 解析失败）
        agent = _make_agent("这是一段没有任何 JSON 的自然语言回复")
        decision = agent.make_decision(self.MARKET, {}, "无网格")
        self.assertEqual(decision["action"], "KEEP_GRID")
        self.assertEqual(decision["confidence"], 0.0)

    def test_invalid_action_falls_back_to_keep_grid(self):
        # 线上真实出现过的非法 action
        agent = _make_agent('{"action": "UPDATE_GRIDLE", "mode": "NEUTRAL", "confidence": 0.8}')
        decision = agent.make_decision(self.MARKET, {}, "无网格")
        self.assertEqual(decision["action"], "KEEP_GRID")

    def test_balance_api_failure_falls_back_to_keep_grid(self):
        # 余额接口失败时 UPDATE_GRID 应回退 KEEP_GRID，保护现有网格不被清空
        agent = _make_agent(
            '{"action": "UPDATE_GRID", "mode": "NEUTRAL", "width_pct": 0.05, "grid_num": 8, "confidence": 0.7}',
            balance_status="error",
        )
        decision = agent.make_decision(self.MARKET, {}, "运行中")
        self.assertEqual(decision["action"], "KEEP_GRID")

    def test_update_grid_carries_confidence(self):
        agent = _make_agent(
            '{"action": "UPDATE_GRID", "mode": "NEUTRAL", "width_pct": 0.05, "grid_num": 8, "confidence": 0.77}'
        )
        decision = agent.make_decision(self.MARKET, {}, "无网格")
        self.assertEqual(decision["action"], "UPDATE_GRID")
        self.assertAlmostEqual(decision["confidence"], 0.77, places=6)

    def test_keep_grid_passthrough(self):
        agent = _make_agent('{"action": "KEEP_GRID", "mode": "NEUTRAL", "confidence": 0.6}')
        decision = agent.make_decision(self.MARKET, {}, "运行中")
        self.assertEqual(decision["action"], "KEEP_GRID")
        self.assertAlmostEqual(decision["confidence"], 0.6, places=6)

    def test_transient_llm_400_recovers_via_retry(self):
        # 推理模型偶发 400（空 assistant 消息）应被有界重试自愈，而非丢失整轮决策。
        # 首次 invoke 抛 400、第二次成功返回 KEEP_GRID。
        client = AgentFakeLLMFlaky(
            '{"action": "KEEP_GRID", "mode": "NEUTRAL", "confidence": 0.6}',
            fail_times=1,
        )
        agent = _make_agent_with_client(client)
        with patch("src.agent.grid_agent.time.sleep"):  # 跳过退避，保持测试快速
            decision = agent.make_decision(self.MARKET, {}, "运行中")
        self.assertEqual(decision["action"], "KEEP_GRID")
        self.assertAlmostEqual(decision["confidence"], 0.6, places=6)
        self.assertEqual(client.calls, 2)  # 失败 1 次 + 成功 1 次

    def test_persistent_llm_failure_falls_back_to_error(self):
        # 重试耗尽（默认 3 次）后保留 action=ERROR 语义：下游与 KEEP_GRID 同样仅维持网格、
        # 检查减仓单，绝不把 LLM 故障放大成撤换单动作。
        client = AgentFakeLLMAlwaysFails()
        agent = _make_agent_with_client(client)
        with patch("src.agent.grid_agent.time.sleep"):
            decision = agent.make_decision(self.MARKET, {}, "运行中")
        self.assertEqual(decision["action"], "ERROR")
        self.assertEqual(client.calls, 3)  # 完整重试 max_llm_attempts 次
        self.assertIn("400", str(decision.get("reason", "")))

    def test_force_neutral_overrides_ai_direction(self):
        # 强制中性：AI 给 SHORT，UPDATE_GRID 仍以 NEUTRAL 建网格，从源头消除方向翻转 whipsaw
        agent = GridAgent(
            symbol="ETH",
            order_manager=AgentFakeOM(balance_status="ok"),
            logger=DummyLogger(),
            llm_manager=AgentFakeLLMManager(
                '{"action": "UPDATE_GRID", "mode": "SHORT", "width_pct": 0.05, "grid_num": 8, "confidence": 0.7}'
            ),
            trade_amount=100.0,
            force_neutral_mode=True,
        )
        decision = agent.make_decision(self.MARKET, {}, "运行中")
        self.assertEqual(decision["action"], "UPDATE_GRID")
        self.assertEqual(decision["mode"], "NEUTRAL")

    def test_default_preserves_ai_direction(self):
        # 默认（开关关闭）保持历史行为：AI 给 SHORT 就建 SHORT 网格
        agent = GridAgent(
            symbol="ETH",
            order_manager=AgentFakeOM(balance_status="ok"),
            logger=DummyLogger(),
            llm_manager=AgentFakeLLMManager(
                '{"action": "UPDATE_GRID", "mode": "SHORT", "width_pct": 0.05, "grid_num": 8, "confidence": 0.7}'
            ),
            trade_amount=100.0,
        )
        decision = agent.make_decision(self.MARKET, {}, "运行中")
        self.assertEqual(decision["action"], "UPDATE_GRID")
        self.assertEqual(decision["mode"], "SHORT")


if __name__ == "__main__":
    unittest.main()
