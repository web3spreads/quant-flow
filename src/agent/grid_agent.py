"""
网格交易 Agent 模块 (架构进化版：AI 决策 + 数学引擎)
"""

import json
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from src.utils.grid_math import calculate_grid_config

DEFAULT_WIDTH_PCT_MIN = 0.02
DEFAULT_WIDTH_PCT_MAX = 0.15
DEFAULT_WIDTH_PCT_FALLBACK = 0.05
DEFAULT_AI_WIDTH_BLEND_WEIGHT = 0.35

# 合法的网格动作白名单。LLM 输出此集合之外的值（如线上出现过的 UPDATE_GRIDLE）
# 一律回退到 KEEP_GRID 保守处理，绝不透传到下游执行。
VALID_GRID_ACTIONS = {"UPDATE_GRID", "KEEP_GRID"}
VALID_GRID_MODES = {"LONG", "SHORT", "NEUTRAL"}


class GridAgent:
    def __init__(
        self,
        symbol,
        order_manager,
        logger,
        llm_manager,
        trade_amount,
        width_pct_min: float = DEFAULT_WIDTH_PCT_MIN,
        width_pct_max: float = DEFAULT_WIDTH_PCT_MAX,
        width_pct_fallback: float = DEFAULT_WIDTH_PCT_FALLBACK,
        ai_width_blend_weight: float = DEFAULT_AI_WIDTH_BLEND_WEIGHT,
    ):
        self.symbol = symbol
        self.order_manager = order_manager
        self.logger = logger
        self.trade_amount = trade_amount
        self.llm = llm_manager.get_client(temperature=0.1)
        self.width_pct_min = float(width_pct_min)
        self.width_pct_max = float(width_pct_max)
        self.width_pct_fallback = float(width_pct_fallback)
        self.ai_width_blend_weight = self._clamp(float(ai_width_blend_weight), 0.0, 1.0)

    def make_decision(self, market_data, multi_timeframe_trends, current_grid_summary):
        try:
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
                ai_decision = self._parse_decision_json(content)
            except Exception as parse_err:
                # 解析失败回退保守 KEEP_GRID（仅检查减仓保护单），而非全量重建。
                # 把 LLM 故障放大成撤换单动作是历史亏损来源之一（线上 19 次解析失败）。
                self.logger.print_warning(
                    f"[GridAgent] LLM 决策解析失败，回退 KEEP_GRID: {parse_err}"
                )
                return {
                    "action": "KEEP_GRID",
                    "mode": "NEUTRAL",
                    "confidence": 0.0,
                    "reason": f"LLM 输出解析失败，保守维持网格: {parse_err}",
                }

            action = str(ai_decision.get("action", "")).strip().upper()
            confidence = self._safe_float(ai_decision.get("confidence"), 0.0)

            # action 白名单校验：非法/未知值（如线上出现过的 UPDATE_GRIDLE）不透传，保守维持网格
            if action not in VALID_GRID_ACTIONS:
                self.logger.print_warning(
                    f"[GridAgent] LLM 返回非法 action={ai_decision.get('action')!r}，回退 KEEP_GRID"
                )
                return {
                    "action": "KEEP_GRID",
                    "mode": "NEUTRAL",
                    "confidence": confidence,
                    "reason": f"非法 action={ai_decision.get('action')!r}，保守维持网格",
                }

            if action == "UPDATE_GRID":
                current_price = float(market_data.get("current_price"))
                balance_info = self.order_manager.get_available_balance_info()
                # 余额接口失败时 available 会回退为 0，若继续 UPDATE_GRID 会以最小单格金额
                # 触发全量重建、撤光现有挂单却可能挂不出新单，导致网格被意外清空。
                # 因此余额获取失败时回退 KEEP_GRID，保护现有网格与持仓。
                if balance_info.get("status") != "ok":
                    self.logger.print_warning(
                        f"[GridAgent] 获取可用余额失败: {balance_info.get('message')}，回退 KEEP_GRID"
                    )
                    return {
                        "action": "KEEP_GRID",
                        "mode": "NEUTRAL",
                        "confidence": confidence,
                        "reason": f"获取可用余额失败: {balance_info.get('message')}，保守维持网格",
                    }
                available = float(balance_info.get("available", 0))
                mode = str(ai_decision.get("mode", "NEUTRAL")).strip().upper()
                if mode not in VALID_GRID_MODES:
                    mode = "NEUTRAL"
                dynamic_width_pct = self._calculate_dynamic_width_pct(
                    market_data=market_data,
                    ai_width_pct=ai_decision.get("width_pct"),
                    mode=mode,
                )

                math_config = calculate_grid_config(
                    current_price=current_price,
                    available_balance=min(available, self.trade_amount),
                    mode=mode,
                    width_pct=dynamic_width_pct,
                    grid_num=ai_decision.get("grid_num", 6),
                )
                math_config["reason"] = ai_decision.get("reason", "AI 触发数学引擎更新")
                math_config["width_pct"] = dynamic_width_pct
                # 透传置信度，避免云端监控指标恒为 0
                math_config["confidence"] = confidence
                return math_config

            # KEEP_GRID
            return {
                "action": "KEEP_GRID",
                "mode": str(ai_decision.get("mode", "NEUTRAL")).strip().upper(),
                "confidence": confidence,
                "reason": ai_decision.get("reason", "AI 维持当前网格"),
            }

        except Exception as e:
            return {"action": "ERROR", "reason": str(e)}

    @staticmethod
    def _extract_first_json_object(text: str) -> str:
        start = -1
        depth = 0
        in_string = False
        escape = False

        for idx, ch in enumerate(text):
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue

            if ch == '"':
                in_string = True
                continue

            if ch == "{":
                if depth == 0:
                    start = idx
                depth += 1
            elif ch == "}":
                if depth > 0:
                    depth -= 1
                    if depth == 0 and start >= 0:
                        return text[start : idx + 1]

        raise ValueError("未找到有效 JSON 对象")

    def _parse_decision_json(self, content: Any) -> dict[str, Any]:
        if isinstance(content, dict):
            return content

        text = str(content or "").strip()
        if not text:
            raise ValueError("AI 返回为空")

        fenced_match = re.search(r"```json\s*([\s\S]*?)```", text, re.IGNORECASE)
        if fenced_match:
            fenced_text = fenced_match.group(1).strip()
            if fenced_text:
                return json.loads(fenced_text)

        try:
            return json.loads(text)
        except Exception:
            pass

        candidate = self._extract_first_json_object(text)
        return json.loads(candidate)

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _clamp(value: float, lower: float, upper: float) -> float:
        return max(lower, min(value, upper))

    def _estimate_market_width_pct(self, market_data: dict[str, Any]) -> float:
        current_price = self._safe_float(market_data.get("current_price"), 0.0)
        if current_price <= 0:
            return self.width_pct_fallback

        bb_upper = self._safe_float(market_data.get("bb_upper"), 0.0)
        bb_lower = self._safe_float(market_data.get("bb_lower"), 0.0)
        high = self._safe_float(market_data.get("high"), 0.0)
        low = self._safe_float(market_data.get("low"), 0.0)
        volume_change = abs(self._safe_float(market_data.get("volume_change"), 0.0))

        bb_width_pct = 0.0
        if bb_upper > bb_lower > 0:
            bb_width_pct = (bb_upper - bb_lower) / current_price

        candle_range_pct = 0.0
        if high > low > 0:
            candle_range_pct = (high - low) / current_price

        if bb_width_pct > 0 and candle_range_pct > 0:
            base_width = 0.7 * bb_width_pct + 0.3 * (candle_range_pct * 2.2)
        elif bb_width_pct > 0:
            base_width = bb_width_pct
        elif candle_range_pct > 0:
            base_width = candle_range_pct * 2.5
        else:
            base_width = self.width_pct_fallback

        volume_boost = 1 + min(volume_change / 100.0, 1.5) * 0.15
        dynamic_width = base_width * volume_boost
        return self._clamp(dynamic_width, self.width_pct_min, self.width_pct_max)

    def _calculate_dynamic_width_pct(
        self,
        market_data: dict[str, Any],
        ai_width_pct: Any = None,
        mode: str = "NEUTRAL",
    ) -> float:
        market_width = self._estimate_market_width_pct(market_data)

        mode_upper = str(mode or "").upper()
        if mode_upper in {"LONG", "SHORT"}:
            market_width *= 1.10

        market_width = self._clamp(market_width, self.width_pct_min, self.width_pct_max)

        ai_width = self._safe_float(ai_width_pct, 0.0)
        if ai_width > 0:
            ai_width = self._clamp(ai_width, self.width_pct_min, self.width_pct_max)
            blended = (
                market_width * (1 - self.ai_width_blend_weight)
                + ai_width * self.ai_width_blend_weight
            )
            return self._clamp(blended, self.width_pct_min, self.width_pct_max)

        return market_width

    def _get_decision_system_prompt(self):
        return f"""你是网格交易决策器。只负责判断“是否需要更新网格形态”，不负责下单执行。

目标：
1) 让网格方向与市场状态一致；
2) 避免不必要的频繁重置；
3) 给出可执行、稳定的 JSON 决策。

动作定义：
- UPDATE_GRID: 需要重置网格区间/层数/方向
- KEEP_GRID: 维持当前网格，不重置

mode 定义：
- LONG: 预期反弹/上行，以当前价为上沿向下布买网格
- SHORT: 预期回落/下行，以当前价为下沿向上布卖网格
- NEUTRAL: 震荡双向网格

建议规则：
- 出现以下任一情况，优先 UPDATE_GRID：
  1) 当前网格待成交单明显不足或结构失衡
  2) 市场从震荡切换到单边趋势（多周期同向）
  3) 波动显著放大，原区间明显不匹配
- 其余情况下优先 KEEP_GRID，减少无效重置

grid_num 建议（可按波动调节）：
- 高波动：5-7
- 中波动：7-10
- 低波动：10-14

width_pct 约束：
- 必填，范围 {self.width_pct_min:.2f} ~ {self.width_pct_max:.2f}（{self.width_pct_min * 100:.0f}%~{self.width_pct_max * 100:.0f}%）
- 代表“区间宽度偏好”，系统会结合实时波动做动态融合

输出要求（必须严格遵守）：
- 只输出一个 JSON 对象
- 不要输出 markdown、解释性文字、代码块

JSON Schema:
{{
  "action": "UPDATE_GRID | KEEP_GRID",
  "mode": "LONG | SHORT | NEUTRAL",
  "width_pct": 0.06,
  "grid_num": 8,
  "confidence": 0.78,
  "reason": "一句话说明依据"
}}
"""

    def _format_prompt(self, market_data, trends, summary):
        current_price = self._safe_float(market_data.get("current_price"), 0.0)
        rsi = self._safe_float(market_data.get("rsi"), 50.0)
        macd_hist = self._safe_float(market_data.get("macd_hist"), 0.0)
        bb_upper = self._safe_float(market_data.get("bb_upper"), current_price)
        bb_lower = self._safe_float(market_data.get("bb_lower"), current_price)
        volume_change = self._safe_float(market_data.get("volume_change"), 0.0)
        bb_width_pct = ((bb_upper - bb_lower) / current_price) if current_price > 0 else 0.0

        return (
            f"symbol={self.symbol}\n"
            f"current_price={current_price:.4f}\n"
            f"rsi={rsi:.2f}\n"
            f"macd_hist={macd_hist:.6f}\n"
            f"bb_width_pct={bb_width_pct:.4f}\n"
            f"volume_change_pct={volume_change:.2f}\n"
            f"multi_timeframe_trends={json.dumps(trends, ensure_ascii=False)}\n"
            f"current_grid_summary={summary}\n"
            "请输出严格 JSON 决策。"
        )
