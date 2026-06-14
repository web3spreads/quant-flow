"""
交易工具链测试

此前测试套件中 ``MockModelAdapter`` 永不产生 ``ToolCallPart``，导致 buy / sell 等下单
工具在测试里从不被真实调用 —— 这正是「重复下单」「参数错配」类 bug 测试抓不到的根因
（套件全绿但下单路径一行没测到）。

本测试用 pydantic-ai 的 ``FunctionModel`` 构造真实工具调用，覆盖：

1. ``buy`` 工具确实触发 ``order_manager.execute_long``（下单路径可达性，此前 0 覆盖）；
2. 同一决策周期内重复 ``buy`` → ``execute_long`` 只被调用一次（幂等去重，防真实资金重复下单）；
3. ``buy_limit`` 限价开多重复挂单同样被幂等拦截。
"""

from typing import Any
from unittest.mock import MagicMock

from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from src.agent.single_symbol_agent import SingleSymbolAgent
from src.fees import FeeRates


def _make_function_model(responses: list[ModelResponse]) -> FunctionModel:
    """
    构造一个按顺序返回给定 ``ModelResponse`` 的 FunctionModel。

    模型按顺序吐出预设响应（工具调用或最终文本），用以精确模拟「模型在一轮内重复
    调用同一下单工具」的场景。超出预设次数后返回终止文本，避免死循环。
    """
    state = {"i": 0}

    def gen(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        # 每次 run_sync 的新一轮会从仅含 user prompt 的历史开始 → 重置游标，
        # 使同一个 FunctionModel 可在多个决策周期内复用
        if len(messages) == 1:
            state["i"] = 0
        i = state["i"]
        state["i"] += 1
        if i < len(responses):
            return responses[i]
        return ModelResponse(parts=[TextPart(content="决策完成")])

    return FunctionModel(gen)


def _build_agent(
    func_model: FunctionModel,
    *,
    limit_order_enabled: bool = False,
) -> tuple[SingleSymbolAgent, MagicMock]:
    """构造一个注入 FunctionModel 的 SingleSymbolAgent，返回 (agent, order_manager)。"""
    llm_manager = MagicMock()
    llm_manager.get_client.return_value = func_model

    order_manager = MagicMock()
    order_manager.check_sufficient_balance.return_value = True
    order_manager.execute_long.return_value = {
        "success": True,
        "quantity": 0.1,
        "fill_price": 100.0,
        "hash": "0xabc",
    }
    order_manager.execute_long_limit.return_value = {
        "success": True,
        "quantity": 0.1,
        "take_profit_price": 105.0,
        "stop_loss_price": 98.0,
    }
    order_manager.get_available_balance_info.return_value = {
        "status": "ok",
        "total": 1000.0,
        "occupied": 0.0,
        "available": 1000.0,
    }
    order_manager.get_current_positions.return_value = []
    order_manager.get_open_limit_orders.return_value = []

    prompt_manager = MagicMock()
    prompt_manager.get_system_prompt.return_value = "test system prompt"
    prompt_manager.format_trading_prompt.return_value = "test user prompt"

    logger = MagicMock()

    # 注入极小费率，确保手续费守卫(_check_fee_guard)不会拦截，使下单路径可达
    tiny_fee = FeeRates(maker_rate=0.0001, taker_rate=0.0001)

    agent = SingleSymbolAgent(
        symbol="BTC",
        order_manager=order_manager,
        logger=logger,
        llm_manager=llm_manager,
        temperature=0.0,
        max_iterations=5,
        trade_amount=100.0,
        max_leverage=10,
        take_profit_ratio=0.05,
        stop_loss_ratio=0.02,
        notifier=None,
        prompt_manager=prompt_manager,
        fee_rates=tiny_fee,
        limit_order_enabled=limit_order_enabled,
    )
    return agent, order_manager


def _market_data() -> dict[str, Any]:
    return {"current_price": 100.0, "rsi": 55.0, "macd": 0.5}


def _buy_call() -> ModelResponse:
    return ModelResponse(
        parts=[
            ToolCallPart(tool_name="buy", args={"symbol": "BTC", "amount": 100.0, "leverage": 5})
        ]
    )


def _final_text() -> ModelResponse:
    return ModelResponse(parts=[TextPart(content="决策完成")])


class TestTradingToolIdempotency:
    """下单工具幂等去重测试"""

    def test_buy_tool_actually_executes(self):
        """buy 工具确实触发 execute_long（下单路径可达性，此前 0 覆盖）"""
        model = _make_function_model([_buy_call(), _final_text()])
        agent, order_manager = _build_agent(model)

        decision, details = agent.make_decision(
            market_data=_market_data(),
            multi_timeframe_trends={"1h": "up"},
            current_positions=[],
            max_positions=5,
        )

        assert decision == "BUY"
        # 关键断言：下单路径确实被调用（此前 MockModelAdapter 路径下从不触发）
        assert order_manager.execute_long.call_count == 1
        # 成交参数经回调写回 details，供风控/反思等下游消费
        assert details.get("entry_price") == 100.0
        assert details.get("leverage") == 5

    def test_duplicate_buy_is_deduped(self):
        """同一决策周期内重复 buy → execute_long 只调用一次（幂等防重复下单）"""
        model = _make_function_model(
            [
                _buy_call(),
                _buy_call(),  # 第二次 buy：应被幂等守卫拒绝，不再下单
                _final_text(),
            ]
        )
        agent, order_manager = _build_agent(model)

        decision, _ = agent.make_decision(
            market_data=_market_data(),
            multi_timeframe_trends={"1h": "up"},
            current_positions=[],
            max_positions=5,
        )

        assert order_manager.execute_long.call_count == 1, (
            "重复 buy 未被幂等去重，会导致真实资金重复下单"
        )
        assert decision == "BUY"

    def test_limit_buy_is_deduped(self):
        """限价开多重复挂单同样被幂等拦截（防双重敞口）"""
        limit_call = ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="buy_limit",
                    args={"symbol": "BTC", "amount": 100.0, "leverage": 5, "price": 99.0},
                )
            ]
        )
        model = _make_function_model([limit_call, limit_call, _final_text()])
        agent, order_manager = _build_agent(model, limit_order_enabled=True)

        agent.make_decision(
            market_data=_market_data(),
            multi_timeframe_trends={"1h": "up"},
            current_positions=[],
            max_positions=5,
        )

        assert order_manager.execute_long_limit.call_count == 1, "重复 buy_limit 未被幂等去重"

    def test_new_cycle_resets_idempotency(self):
        """跨决策周期幂等状态应重置：两个周期各下一次单是允许的"""
        model = _make_function_model([_buy_call(), _final_text()])
        agent, order_manager = _build_agent(model)

        for _ in range(2):
            agent.make_decision(
                market_data=_market_data(),
                multi_timeframe_trends={"1h": "up"},
                current_positions=[],
                max_positions=5,
            )

        # 两个独立周期各下一次单 = 共两次（幂等不应跨周期累积）
        assert order_manager.execute_long.call_count == 2
