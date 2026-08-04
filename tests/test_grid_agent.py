"""网格决策 Agent 测试：动作白名单、故障兜底与数学引擎衔接。"""

import json

from conftest import QUIET_LOGGER, FakeLLM, FakeOrderManager

from src.llm import LLMError
from src.strategy.grid_agent import GridAgent

MARKET_DATA = {
    "current_price": 100.0,
    "rsi": 55.0,
    "macd_hist": 0.1,
    "bb_upper": 104.0,
    "bb_lower": 96.0,
    "high": 101.0,
    "low": 99.0,
    "volume_change": 5.0,
}
TRENDS = {"1小时": "震荡整理"}


def make_agent(llm, om=None, **overrides) -> GridAgent:
    defaults = {
        "symbol": "ETH",
        "order_manager": om or FakeOrderManager(available=1000.0),
        "logger": QUIET_LOGGER,
        "llm": llm,
        "trade_amount": 100.0,
        "force_neutral_mode": False,
        "adaptive_sizing": True,
    }
    defaults.update(overrides)
    return GridAgent(**defaults)


def grid_json(**kwargs) -> str:
    base = {"action": "KEEP_GRID", "mode": "NEUTRAL", "confidence": 0.7, "reason": "测试"}
    base.update(kwargs)
    return json.dumps(base, ensure_ascii=False)


def decide(agent) -> dict:
    return agent.make_decision(MARKET_DATA, TRENDS, "无网格")


class TestKeepGrid:
    def test_keep_grid_passthrough(self):
        decision = decide(make_agent(FakeLLM([grid_json()])))
        assert decision["action"] == "KEEP_GRID"
        assert decision["llm_ok"] is True
        assert decision["confidence"] == 0.7


class TestFailureFallbacks:
    def test_llm_error_degrades(self):
        decision = decide(make_agent(FakeLLM(error=LLMError("模型下线"))))
        assert decision["action"] == "KEEP_GRID"
        assert decision["llm_ok"] is False

    def test_unparsable_degrades(self):
        decision = decide(make_agent(FakeLLM(["不是 JSON 的回复"])))
        assert decision["action"] == "KEEP_GRID"
        assert decision["llm_ok"] is False

    def test_invalid_action_degrades(self):
        # 线上真实出现过的畸形 action
        decision = decide(make_agent(FakeLLM([grid_json(action="UPDATE_GRIDLE")])))
        assert decision["action"] == "KEEP_GRID"
        assert decision["llm_ok"] is False

    def test_balance_failure_keeps_grid_with_llm_ok(self):
        om = FakeOrderManager()
        om.balance_ok = False
        agent = make_agent(FakeLLM([grid_json(action="UPDATE_GRID")]), om=om)
        decision = decide(agent)
        assert decision["action"] == "KEEP_GRID"
        # 故障在交易所余额接口而非 LLM，不得计入 LLM 连续失败
        assert decision["llm_ok"] is True


class TestUpdateGrid:
    def test_update_grid_produces_math_config(self):
        agent = make_agent(FakeLLM([grid_json(action="UPDATE_GRID", width_pct=0.06, grid_num=6)]))
        decision = decide(agent)
        assert decision["action"] == "UPDATE_GRID"
        assert decision["lower_price"] < 100.0 < decision["upper_price"]
        assert decision["llm_ok"] is True
        assert decision["confidence"] == 0.7

    def test_force_neutral_overrides_direction(self):
        agent = make_agent(
            FakeLLM([grid_json(action="UPDATE_GRID", mode="LONG")]), force_neutral_mode=True
        )
        decision = decide(agent)
        assert decision["mode"] == "NEUTRAL"

    def test_insufficient_capital_rejected(self):
        om = FakeOrderManager(available=7.71)
        agent = make_agent(FakeLLM([grid_json(action="UPDATE_GRID")]), om=om, max_leverage=5)
        decision = decide(agent)
        assert decision["action"] == "INSUFFICIENT_CAPITAL"
        assert decision["llm_ok"] is True


class TestFallbackBuild:
    def test_fallback_builds_neutral_grid_without_llm(self):
        agent = make_agent(FakeLLM(error=LLMError("不该被调用")))
        config = agent.build_fallback_config(MARKET_DATA)
        assert config["action"] == "UPDATE_GRID"
        assert config["mode"] == "NEUTRAL"
        assert config["fallback"] is True
        assert config["llm_ok"] is False
        # 全程未调用 LLM
        assert agent.llm.calls == []

    def test_fallback_without_price_keeps_grid(self):
        agent = make_agent(FakeLLM())
        config = agent.build_fallback_config({"current_price": 0})
        assert config["action"] == "KEEP_GRID"
