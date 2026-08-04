"""
网格决策 Agent：AI 判断方向与宽度，数学引擎计算参数。

分工边界：LLM 只回答「是否需要更新网格形态、往哪个方向、多宽」，
布单数量与价位由 ``calculate_grid_config``（纯数学）推导——AI 的模糊判断
永远不直接决定资金敞口。

故障语义（``llm_ok`` 标记）：LLM 调用异常 / 输出不可解析 / action 非法这些
兜底路径返回与 AI 真实 KEEP_GRID 同形的决策，但打 ``llm_ok=False`` 标，
供上层做连续故障告警与空转自愈（历史教训：模型下线后连续 13 小时决策
失败却无任何告警）。余额接口故障标 ``llm_ok=True``——那不是 LLM 的问题。
"""

from typing import Any

from src.llm import LLMClient, LLMError, extract_json
from src.trading.order_manager import OrderManager
from src.utils.grid_math import calculate_grid_config
from src.utils.logger import TradingLogger

DEFAULT_WIDTH_PCT_MIN = 0.02
DEFAULT_WIDTH_PCT_MAX = 0.15
DEFAULT_WIDTH_PCT_FALLBACK = 0.05
DEFAULT_AI_WIDTH_BLEND_WEIGHT = 0.35
# LLM 未给出格数时的默认层数（正常决策与兜底建网格共用，保证两条路径形态一致）
DEFAULT_GRID_NUM = 6

# 合法的网格动作白名单。LLM 输出此集合之外的值（如线上出现过的 UPDATE_GRIDLE）
# 一律回退到 KEEP_GRID 保守处理，绝不透传到下游执行。
VALID_GRID_ACTIONS = {"UPDATE_GRID", "KEEP_GRID"}
VALID_GRID_MODES = {"LONG", "SHORT", "NEUTRAL"}


class GridAgent:
    """网格 AI 决策引擎（单交易对）。"""

    def __init__(
        self,
        symbol: str,
        order_manager: OrderManager,
        logger: TradingLogger,
        llm: LLMClient,
        trade_amount: float,
        width_pct_min: float = DEFAULT_WIDTH_PCT_MIN,
        width_pct_max: float = DEFAULT_WIDTH_PCT_MAX,
        width_pct_fallback: float = DEFAULT_WIDTH_PCT_FALLBACK,
        ai_width_blend_weight: float = DEFAULT_AI_WIDTH_BLEND_WEIGHT,
        force_neutral_mode: bool = True,
        max_leverage: int = 10,
        adaptive_sizing: bool = True,
        min_grid_num: int = 3,
    ):
        """
        Args:
            symbol: 交易对符号
            order_manager: 订单管理器（查询余额用）
            logger: 日志器
            llm: LLM 客户端
            trade_amount: 单次网格投入上限（USD）
            width_pct_min / width_pct_max: 网格宽度上下限
            width_pct_fallback: 市场数据异常时的回退宽度
            ai_width_blend_weight: AI 宽度与市场数据的融合权重（0-1）
            force_neutral_mode: 强制中性网格（忽略 AI 方向，消除反手亏损）
            max_leverage: 数学引擎的真实杠杆上限
            adaptive_sizing: 单格金额与净值挂钩（钱不够减格数而非抬单格金额）
            min_grid_num: 自适应仓位最少格数（低于则拒绝布单）
        """
        self.symbol = symbol
        self.order_manager = order_manager
        self.logger = logger
        self.llm = llm
        self.trade_amount = trade_amount
        self.width_pct_min = float(width_pct_min)
        self.width_pct_max = float(width_pct_max)
        self.width_pct_fallback = float(width_pct_fallback)
        self.ai_width_blend_weight = self._clamp(float(ai_width_blend_weight), 0.0, 1.0)
        self.force_neutral_mode = bool(force_neutral_mode)
        self.max_leverage = max(1, int(max_leverage))
        self.adaptive_sizing = bool(adaptive_sizing)
        self.min_grid_num = max(2, int(min_grid_num))

    # ── 决策 ──────────────────────────────────────────────────────────────

    def make_decision(
        self,
        market_data: dict[str, Any],
        multi_timeframe_trends: dict[str, str],
        current_grid_summary: str,
    ) -> dict[str, Any]:
        """产出一份网格决策（本方法不抛出异常，故障归一为保守兜底）。"""
        try:
            prompt = self._format_prompt(market_data, multi_timeframe_trends, current_grid_summary)

            try:
                content = self.llm.chat(self._get_decision_system_prompt(), prompt, temperature=0.1)
            except LLMError as e:
                # LLM 故障绝不放大成撤换单动作：保守维持网格（历史亏损来源之一）
                self.logger.print_warning(f"[GridAgent] LLM 调用失败，回退 KEEP_GRID: {e}")
                return self._degraded_keep_grid(f"LLM 调用失败，保守维持网格: {e}")

            try:
                ai_decision = extract_json(content)
            except ValueError as parse_err:
                # 解析失败回退保守 KEEP_GRID（仅检查减仓保护单），而非全量重建
                self.logger.print_warning(
                    f"[GridAgent] LLM 决策解析失败，回退 KEEP_GRID: {parse_err}"
                )
                return self._degraded_keep_grid(f"LLM 输出解析失败，保守维持网格: {parse_err}")

            action = str(ai_decision.get("action", "")).strip().upper()
            confidence = self._safe_float(ai_decision.get("confidence"), 0.0)

            if action not in VALID_GRID_ACTIONS:
                self.logger.print_warning(
                    f"[GridAgent] LLM 返回非法 action={ai_decision.get('action')!r}，回退 KEEP_GRID"
                )
                return self._degraded_keep_grid(
                    f"非法 action={ai_decision.get('action')!r}，保守维持网格",
                    confidence=confidence,
                )

            if action == "UPDATE_GRID":
                return self._build_update_config(ai_decision, market_data, confidence)

            # KEEP_GRID
            return {
                "action": "KEEP_GRID",
                "mode": str(ai_decision.get("mode", "NEUTRAL")).strip().upper(),
                "confidence": confidence,
                "reason": ai_decision.get("reason", "AI 维持当前网格"),
                "llm_ok": True,
            }

        except Exception as e:
            return {"action": "ERROR", "reason": str(e), "llm_ok": False}

    def _build_update_config(
        self, ai_decision: dict[str, Any], market_data: dict[str, Any], confidence: float
    ) -> dict[str, Any]:
        """把 AI 的 UPDATE_GRID 意图交给数学引擎，产出可执行网格配置。"""
        current_price = float(market_data.get("current_price"))
        balance_info = self.order_manager.get_available_balance_info()
        # 余额接口失败时 available 会回退为 0，若继续 UPDATE_GRID 会以最小单格金额
        # 触发全量重建、撤光现有挂单却可能挂不出新单，导致网格被意外清空。
        if balance_info.get("status") != "ok":
            self.logger.print_warning(
                f"[GridAgent] 获取可用余额失败: {balance_info.get('message')}，回退 KEEP_GRID"
            )
            # llm_ok=True：LLM 本身正常，故障在交易所余额接口，不计入 LLM 连续失败告警
            return self._degraded_keep_grid(
                f"获取可用余额失败: {balance_info.get('message')}，保守维持网格",
                confidence=confidence,
                llm_ok=True,
            )

        available = float(balance_info.get("available", 0))
        mode = str(ai_decision.get("mode", "NEUTRAL")).strip().upper()
        if mode not in VALID_GRID_MODES:
            mode = "NEUTRAL"
        # 强制中性：忽略 AI 的 LONG/SHORT 方向，网格只做对称做市。
        # 线上验证 24h 亏损几乎全部来自方向翻转的 taker 反手（whipsaw），
        # 中性网格不主动建/反方向头寸，从源头消除该亏损与反手手续费。
        if self.force_neutral_mode and mode != "NEUTRAL":
            self.logger.print_info(f"[GridAgent] 强制中性模式：忽略 AI 方向 {mode}，覆盖为 NEUTRAL")
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
            grid_num=ai_decision.get("grid_num", DEFAULT_GRID_NUM),
            leverage=self.max_leverage,
            adaptive_sizing=self.adaptive_sizing,
            min_grid_num=self.min_grid_num,
        )
        if math_config.get("action") == "INSUFFICIENT_CAPITAL":
            # 资金撑不起最小网格：拒绝布单并醒目告警，避免小账户被最小单格金额
            # 反向放大成超额敞口（线上 $7.71 账户 16 倍名义敞口的直接根因）。
            self.logger.print_error(
                f"[GridAgent] 💸 资金不足拒绝布单: {math_config.get('reason', '')}"
            )
            math_config["confidence"] = confidence
            math_config["llm_ok"] = True
            return math_config

        math_config["reason"] = ai_decision.get("reason", "AI 触发数学引擎更新")
        math_config["width_pct"] = dynamic_width_pct
        math_config["confidence"] = confidence
        math_config["llm_ok"] = True
        return math_config

    def _degraded_keep_grid(
        self,
        reason: str,
        confidence: float = 0.0,
        llm_ok: bool = False,
    ) -> dict[str, Any]:
        """构造「保守维持网格」的兜底决策（llm_ok 标记故障归属）。"""
        return {
            "action": "KEEP_GRID",
            "mode": "NEUTRAL",
            "confidence": confidence,
            "reason": reason,
            "llm_ok": llm_ok,
        }

    # ── 兜底重建（不经 LLM）───────────────────────────────────────────────

    def build_fallback_config(self, market_data: dict[str, Any]) -> dict[str, Any]:
        """不经 LLM，纯用市场数据 + 数学引擎生成一份中性网格配置。

        用途：LLM 持续不可用时把网格从「空转」中救出。空转死锁的根源是只有
        UPDATE_GRID 会重建网格，而 LLM 故障期间每轮只能产出 ERROR 或兜底
        KEEP_GRID——层级已被清空时「维持现有网格」等于永远维持一片空白。

        只产出 NEUTRAL 对称网格：判断方向是 LLM 的职责，LLM 不可用时不猜方向。
        宽度与格数全部由市场波动率推导，结果完全可复现、与 LLM 无关。
        """
        current_price = self._safe_float(market_data.get("current_price"), 0.0)
        if current_price <= 0:
            return self._degraded_keep_grid("兜底建网格失败：当前价不可用，保守维持网格")

        balance_info = self.order_manager.get_available_balance_info()
        if balance_info.get("status") != "ok":
            # 与 UPDATE_GRID 同样的理由：余额取不到时重建会撤光旧单又挂不出新单
            return self._degraded_keep_grid(
                f"兜底建网格失败：获取可用余额失败({balance_info.get('message')})，保守维持网格",
                llm_ok=True,
            )

        available = self._safe_float(balance_info.get("available"), 0.0)
        width_pct = self._calculate_dynamic_width_pct(
            market_data=market_data, ai_width_pct=None, mode="NEUTRAL"
        )
        math_config = calculate_grid_config(
            current_price=current_price,
            available_balance=min(available, self.trade_amount),
            mode="NEUTRAL",
            width_pct=width_pct,
            grid_num=DEFAULT_GRID_NUM,
            leverage=self.max_leverage,
            adaptive_sizing=self.adaptive_sizing,
            min_grid_num=self.min_grid_num,
        )
        if math_config.get("action") == "INSUFFICIENT_CAPITAL":
            self.logger.print_error(
                f"[GridAgent] 💸 兜底建网格资金不足，拒绝布单: {math_config.get('reason', '')}"
            )
        else:
            self.logger.print_warning(
                f"[GridAgent] 🛟 LLM 持续不可用，按市场数据兜底重建中性网格 "
                f"(宽度 {width_pct:.2%}，{DEFAULT_GRID_NUM} 格)"
            )
            math_config["reason"] = (
                f"LLM 持续不可用，按市场数据兜底重建中性网格(宽度{width_pct:.2%})"
            )
            math_config["width_pct"] = width_pct
        math_config["confidence"] = 0.0
        math_config["llm_ok"] = False
        math_config["fallback"] = True
        return math_config

    # ── 宽度计算 ──────────────────────────────────────────────────────────

    def _estimate_market_width_pct(self, market_data: dict[str, Any]) -> float:
        """从布林带宽度 + K 线振幅 + 量能推导市场建议宽度。"""
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
        return self._clamp(base_width * volume_boost, self.width_pct_min, self.width_pct_max)

    def _calculate_dynamic_width_pct(
        self,
        market_data: dict[str, Any],
        ai_width_pct: Any = None,
        mode: str = "NEUTRAL",
    ) -> float:
        """市场宽度与 AI 宽度按权重融合（AI 缺席时纯市场数据）。"""
        market_width = self._estimate_market_width_pct(market_data)

        if str(mode or "").upper() in {"LONG", "SHORT"}:
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

    # ── Prompt ────────────────────────────────────────────────────────────

    def _get_decision_system_prompt(self) -> str:
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

    def _format_prompt(
        self, market_data: dict[str, Any], trends: dict[str, str], summary: str
    ) -> str:
        import json

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

    # ── 辅助 ──────────────────────────────────────────────────────────────

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _clamp(value: float, lower: float, upper: float) -> float:
        return max(lower, min(value, upper))
