"""永续策略测试：JSON 决策解析、边界校验与执行拦截。"""

import json

import pytest
from conftest import PROMPTS_DIR, QUIET_LOGGER, FakeLLM, FakeOrderManager

from src.config import TradingConfig
from src.llm import LLMError
from src.strategy.perp import PerpStrategy

MARKET_DATA = {
    "current_price": 100.0,
    "open": 99.0,
    "high": 101.0,
    "low": 98.0,
    "rsi": 60.0,
    "macd": 0.5,
    "macd_signal": 0.3,
    "macd_hist": 0.2,
    "bb_upper": 105.0,
    "bb_middle": 100.0,
    "bb_lower": 95.0,
    "bb_position": 0.5,
    "ma_7": 99.5,
    "volume": 1200.0,
    "volume_change": 10.0,
}
TRENDS = {"15分钟": "上涨", "1小时": "上涨"}

LONG_POSITION = {"coin": "BTC", "szi": "0.5", "entryPx": "100.0", "unrealizedPnl": "5.0"}
SHORT_POSITION = {"coin": "BTC", "szi": "-0.5", "entryPx": "100.0", "unrealizedPnl": "-2.0"}


def make_strategy(llm, order_manager=None, **overrides) -> tuple[PerpStrategy, FakeOrderManager]:
    om = order_manager or FakeOrderManager()
    trading = TradingConfig(**overrides) if overrides else TradingConfig()
    strategy = PerpStrategy(
        symbol="BTC",
        order_manager=om,
        llm=llm,
        logger=QUIET_LOGGER,
        trading=trading,
        prompts_dir=PROMPTS_DIR,
    )
    return strategy, om


def decision_json(**kwargs) -> str:
    base = {"action": "HOLD", "confidence": 0.8, "reason": "测试"}
    base.update(kwargs)
    return json.dumps(base, ensure_ascii=False)


def run(strategy, positions=None, open_allowed=True, balance=1000.0):
    return strategy.run_cycle(
        market_data=MARKET_DATA,
        trends=TRENDS,
        positions=positions or [],
        available_balance=balance,
        open_allowed=open_allowed,
    )


class TestDecisionParsing:
    def test_buy_executes(self):
        llm = FakeLLM([decision_json(action="BUY", amount_usd=50, leverage=3)])
        strategy, om = make_strategy(llm)
        record = run(strategy)
        assert record["executed"] is True
        assert record["is_long"] is True
        assert om.calls == [("execute_long", "BTC", 50.0, 3, True)]

    def test_sell_short_executes(self):
        llm = FakeLLM([decision_json(action="SELL_SHORT", amount_usd=50, leverage=2)])
        strategy, om = make_strategy(llm)
        record = run(strategy)
        assert record["executed"] is True
        assert record["is_long"] is False
        assert om.calls[0][0] == "execute_short"

    def test_invalid_action_degrades_to_hold(self):
        llm = FakeLLM([decision_json(action="MOON")])
        strategy, om = make_strategy(llm)
        record = run(strategy)
        assert record["action"] == "HOLD"
        assert record["llm_ok"] is False
        assert om.calls == []

    def test_llm_error_degrades_to_hold(self):
        strategy, om = make_strategy(FakeLLM(error=LLMError("端点不可用")))
        record = run(strategy)
        assert record["action"] == "HOLD"
        assert record["llm_ok"] is False
        assert om.calls == []

    def test_unparsable_output_degrades_to_hold(self):
        strategy, om = make_strategy(FakeLLM(["我觉得应该买入，但是我不会输出 JSON"]))
        record = run(strategy)
        assert record["action"] == "HOLD"
        assert record["llm_ok"] is False

    def test_amount_and_leverage_clamped(self):
        llm = FakeLLM([decision_json(action="BUY", amount_usd=999999, leverage=99)])
        strategy, om = make_strategy(llm)
        run(strategy)
        _, _, amount, leverage, _ = om.calls[0]
        assert amount == TradingConfig().max_trade_amount
        assert leverage == TradingConfig().max_leverage

    def test_missing_amount_degrades_to_hold(self):
        # 开仓决策缺 amount_usd/leverage 时必须降级 HOLD，
        # 绝不能缺省取配置上限（把 LLM 半格式故障放大为最激进开仓）
        llm = FakeLLM([decision_json(action="BUY", leverage=3)])
        strategy, om = make_strategy(llm)
        record = run(strategy)
        assert record["action"] == "HOLD"
        assert record["llm_ok"] is False
        assert om.calls == []

    def test_invalid_leverage_degrades_to_hold(self):
        llm = FakeLLM([decision_json(action="SELL_SHORT", amount_usd=50, leverage="高杠杆")])
        strategy, om = make_strategy(llm)
        record = run(strategy)
        assert record["action"] == "HOLD"
        assert record["llm_ok"] is False
        assert om.calls == []

    def test_close_without_amount_still_valid(self):
        # CLOSE/HOLD 不需要 amount_usd/leverage，缺失不应降级
        llm = FakeLLM([decision_json(action="CLOSE")])
        strategy, om = make_strategy(llm)
        record = run(strategy, positions=[LONG_POSITION])
        assert record["llm_ok"] is True
        assert record["executed"] is True


class TestOpenGuards:
    def test_low_confidence_blocked(self):
        llm = FakeLLM([decision_json(action="BUY", confidence=0.3, amount_usd=50, leverage=2)])
        strategy, om = make_strategy(llm)
        record = run(strategy)
        assert record["executed"] is False
        assert "置信度" in record["reason"]
        assert om.calls == []

    def test_open_not_allowed_blocked(self):
        llm = FakeLLM([decision_json(action="BUY", amount_usd=50, leverage=2)])
        strategy, om = make_strategy(llm)
        record = run(strategy, open_allowed=False)
        assert record["executed"] is False
        assert om.calls == []

    def test_duplicate_long_blocked(self):
        llm = FakeLLM([decision_json(action="BUY", amount_usd=50, leverage=2)])
        strategy, om = make_strategy(llm)
        record = run(strategy, positions=[LONG_POSITION])
        assert record["executed"] is False
        assert "同向" in record["reason"]

    def test_opposite_direction_blocked(self):
        # Hyperliquid 净头寸制：持空仓时 BUY 会净额抵消而非开多，
        # 记账/TP-SL 全部失真，必须拦截并要求先 CLOSE
        llm = FakeLLM([decision_json(action="BUY", amount_usd=50, leverage=2)])
        strategy, om = make_strategy(llm)
        record = run(strategy, positions=[SHORT_POSITION])
        assert record["executed"] is False
        assert "反向" in record["reason"]
        assert om.calls == []

    def test_max_positions_blocked(self):
        llm = FakeLLM([decision_json(action="BUY", amount_usd=50, leverage=2)])
        strategy, om = make_strategy(llm, max_positions=2)
        others = [
            {"coin": "ETH", "szi": "1", "entryPx": "50"},
            {"coin": "SOL", "szi": "1", "entryPx": "20"},
        ]
        record = run(strategy, positions=others)
        assert record["executed"] is False
        assert "上限" in record["reason"]

    def test_insufficient_balance_blocked(self):
        llm = FakeLLM([decision_json(action="BUY", amount_usd=100, leverage=2)])
        om = FakeOrderManager(available=10.0)
        strategy, om = make_strategy(llm, order_manager=om)
        record = run(strategy, balance=10.0)
        assert record["executed"] is False
        assert "余额" in record["reason"]

    def test_rollback_failure_reported_as_naked_position(self):
        # 止损失败且回滚失败：持仓真实存在，必须 executed=True 让风控看见
        llm = FakeLLM([decision_json(action="BUY", amount_usd=50, leverage=2)])
        strategy, om = make_strategy(llm)
        om.execute_result = {
            "success": False,
            "quantity": 0.5,
            "price": 100.0,
            "rollback_executed": True,
            "rollback_final_success": False,
        }
        record = run(strategy)
        assert record["executed"] is True
        assert "人工处理" in record["reason"]


class TestClose:
    def test_close_long_computes_pnl(self):
        llm = FakeLLM([decision_json(action="CLOSE")])
        strategy, om = make_strategy(llm)
        om.close_result = {"status": "ok", "fill_price": 110.0}
        record = run(strategy, positions=[LONG_POSITION])
        assert record["executed"] is True
        assert record["pnl"] == pytest.approx((110.0 - 100.0) * 0.5)

    def test_close_short_computes_pnl(self):
        llm = FakeLLM([decision_json(action="CLOSE")])
        strategy, om = make_strategy(llm)
        om.close_result = {"status": "ok", "fill_price": 90.0}
        record = run(strategy, positions=[SHORT_POSITION])
        assert record["executed"] is True
        assert record["pnl"] == pytest.approx((100.0 - 90.0) * 0.5)

    def test_close_without_position_blocked(self):
        llm = FakeLLM([decision_json(action="CLOSE")])
        strategy, om = make_strategy(llm)
        record = run(strategy, positions=[])
        assert record["executed"] is False
        assert "未持仓" in record["reason"]
        assert om.calls == []

    def test_close_failure_reported(self):
        llm = FakeLLM([decision_json(action="CLOSE")])
        strategy, om = make_strategy(llm)
        om.close_result = None
        record = run(strategy, positions=[LONG_POSITION])
        assert record["executed"] is False
        assert "未成功" in record["reason"]


class TestPrompt:
    def test_prompt_contains_key_context(self):
        llm = FakeLLM([decision_json()])
        strategy, _ = make_strategy(llm)
        record = run(strategy, positions=[LONG_POSITION], balance=888.0)
        prompt = record["prompt"]
        assert "BTC" in prompt
        assert "888.00" in prompt
        assert "多仓" in prompt

    def test_prompt_flags_open_forbidden(self):
        llm = FakeLLM([decision_json()])
        strategy, _ = make_strategy(llm)
        record = run(strategy, open_allowed=False)
        assert "禁止开新仓" in record["prompt"]
