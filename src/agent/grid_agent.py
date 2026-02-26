"""
网格交易 Agent 模块 (架构进化版：AI 决策 + 数学引擎)
"""

import json
import re

from langchain_core.messages import HumanMessage, SystemMessage

from src.utils.grid_math import calculate_grid_config


class GridAgent:
    def __init__(
        self,
        symbol,
        order_manager,
        logger,
        llm_manager,
        trade_amount,
    ):
        self.symbol = symbol
        self.order_manager = order_manager
        self.logger = logger
        self.trade_amount = trade_amount
        self.llm = llm_manager.get_client(temperature=0.1)

    def make_decision(self, market_data, multi_timeframe_trends, current_grid_summary):
        try:
            # 1. AI 只负责逻辑决策
            messages = [
                SystemMessage(content=self._get_decision_system_prompt()),
                HumanMessage(
                    content=self._format_prompt(
                        market_data, multi_timeframe_trends, current_grid_summary
                    )
                ),
            ]
            response = self.llm.invoke(messages)
            content = response.content

            try:
                json_match = re.search(r"\{[\s\S]*\}", content)
                if not json_match:
                    raise ValueError("未能从LLM响应中解析出JSON内容")
                ai_decision = json.loads(json_match.group(0))

                # 2. 如果需要更新，将决策传递给数学引擎
                if ai_decision.get("action") == "UPDATE_GRID":
                    current_price = float(market_data.get("current_price"))
                    balance_info = self.order_manager.get_available_balance_info()
                    available = float(balance_info.get("available", 0))

                    # AI 给出倾向性参数
                    math_config = calculate_grid_config(
                        current_price=current_price,
                        available_balance=min(available, self.trade_amount),
                        mode=ai_decision.get("mode", "NEUTRAL"),
                        width_pct=ai_decision.get("width_pct", 0.05),
                        grid_num=ai_decision.get("grid_num", 6),
                    )
                    math_config["reason"] = ai_decision.get("reason", "AI 触发数学引擎更新")
                    return math_config

                return ai_decision
            except Exception:
                # 兜底逻辑：AI 抽风时强制由数学引擎接管
                balance_info = self.order_manager.get_available_balance_info()
                available = float(balance_info.get("available", 0.0))
                return calculate_grid_config(
                    float(market_data["current_price"]), min(available, self.trade_amount)
                )

        except Exception as e:
            return {"action": "ERROR", "reason": str(e)}

    def _get_decision_system_prompt(self):
        return """你是一位顶级量化决策专家。你不需要计算复杂的网格数值，只需要根据行情决定'网格模式'。

## 🧠 决策逻辑
1. **LONG (看涨模式)**：现价处于支撑位，或你预期会反弹。网格将以现价为顶部，向下铺买单。
2. **SHORT (看跌模式)**：现价处于阻力位，或预期单边下跌。网格将以现价为底部，向上铺卖单。
3. **NEUTRAL (中性/双向模式)**：震荡行情。网格将以现价为中心，上下双向铺单。

## ⚠️ 任务
- 价格跌穿下限或待成交单为0时，必须输出 UPDATE_GRID。
- 必须输出 width_pct (区间跨度，如 0.05 代表 5%)。

## JSON 输出格式
{
  "action": "UPDATE_GRID",
  "mode": "NEUTRAL",
  "width_pct": 0.06,
  "grid_num": 8,
  "reason": "趋势说明"
}"""

    def _format_prompt(self, market_data, trends, summary):
        return f"现价: ${market_data['current_price']}\n趋势: {trends}\n当前网格: {summary}\n请给出模式决策。"
