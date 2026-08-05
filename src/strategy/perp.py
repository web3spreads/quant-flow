"""
永续合约策略：LLM 结构化决策 → 边界校验 → 订单执行。

数据流（与网格策略同构——LLM 只产出 JSON 决策，执行永远在交易层）：

    市场数据 + 持仓 → Prompt → LLM → JSON 决策 → 校验 → OrderManager

安全边界：
- LLM 故障/输出不可解析 → 一律降级为 HOLD，绝不把故障放大成交易动作；
- 金额与杠杆按配置上限截断，置信度不足不开仓；
- 重复开仓/无仓平仓在执行前拦截；
- 止盈止损挂单与「止损失败自动回滚平仓」由 OrderManager/Client 保证，
  本模块不重复实现。
"""

from pathlib import Path
from typing import Any

from jinja2 import Template

from src.config import TradingConfig
from src.llm import LLMClient, LLMError, extract_json
from src.trading.order_manager import OrderManager
from src.utils.logger import TradingLogger

# 合法动作白名单：LLM 输出此集合之外的值一律回退 HOLD，绝不透传到执行层
VALID_ACTIONS = {"BUY", "SELL_SHORT", "CLOSE", "HOLD"}

# 默认 Prompt 目录：相对仓库根目录解析，与启动时的工作目录无关
DEFAULT_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


class PerpStrategy:
    """单交易对永续策略。

    职责单一：渲染 Prompt → 请求 LLM → 校验决策 → 执行。
    每个交易对一个实例，互不共享状态。
    """

    def __init__(
        self,
        symbol: str,
        order_manager: OrderManager,
        llm: LLMClient,
        logger: TradingLogger,
        trading: TradingConfig,
        prompts_dir: str | Path = DEFAULT_PROMPTS_DIR,
    ):
        """
        Args:
            symbol: 交易对符号（如 "BTC"）
            order_manager: 订单管理器（执行层）
            llm: LLM 客户端
            logger: 日志器
            trading: 交易配置（金额/杠杆/置信度等上限）
            prompts_dir: Prompt 模板目录
        """
        self.symbol = symbol
        self.order_manager = order_manager
        self.llm = llm
        self.logger = logger
        self.trading = trading

        prompts = Path(prompts_dir)
        self._system_prompt = (prompts / "perp_system.md").read_text(encoding="utf-8")
        self._template = Template((prompts / "perp_decision.md").read_text(encoding="utf-8"))

        # LLM 连续故障计数（llm_ok=False 的周期数），达阈值升级告警——
        # 模型 ID 下线/密钥失效这类持续性故障若只有逐轮 WARNING，极易被淹没
        self._llm_failure_streak = 0
        self._llm_alert_sent = False

    # ── 主流程 ────────────────────────────────────────────────────────────

    def run_cycle(
        self,
        market_data: dict[str, Any],
        trends: dict[str, str],
        positions: list[dict[str, Any]],
        available_balance: float,
        open_allowed: bool,
    ) -> dict[str, Any]:
        """执行一轮决策与执行，返回执行记录（本方法不抛出异常）。

        Args:
            market_data: 最新技术指标字典（TechnicalIndicators.get_latest_indicators）
            trends: 多周期趋势映射（如 {"15m": "上涨", ...}）
            positions: 当前全部持仓（Hyperliquid 原始持仓字典列表）
            available_balance: 可用余额（USD）
            open_allowed: 本轮是否允许开新仓（余额/风控综合判定）

        Returns:
            执行记录字典：action / confidence / reason / llm_ok / executed /
            size / entry_price / leverage / is_long / pnl / prompt / raw_response
        """
        prompt = self._render_prompt(
            market_data, trends, positions, available_balance, open_allowed
        )
        decision = self._decide(prompt)
        self._track_llm_health(decision)
        record = self._execute(decision, positions, open_allowed)
        record["prompt"] = prompt
        return record

    # ── 决策 ──────────────────────────────────────────────────────────────

    def _decide(self, prompt: str) -> dict[str, Any]:
        """请求 LLM 并把回复归一为合法决策；任何故障降级为保守 HOLD。"""
        try:
            reply = self.llm.chat(self._system_prompt, prompt)
        except LLMError as e:
            self.logger.print_warning(f"[{self.symbol}] LLM 调用失败，保守观望: {e}")
            return self._hold(f"LLM 不可用，保守观望: {e}", llm_ok=False)

        try:
            data = extract_json(reply)
        except ValueError as e:
            self.logger.print_warning(f"[{self.symbol}] LLM 输出解析失败，保守观望: {e}")
            return self._hold(f"LLM 输出解析失败: {e}", llm_ok=False, raw=reply)

        action = str(data.get("action", "")).strip().upper()
        if action not in VALID_ACTIONS:
            self.logger.print_warning(
                f"[{self.symbol}] LLM 返回非法 action={data.get('action')!r}，回退 HOLD"
            )
            return self._hold(f"非法 action={data.get('action')!r}", llm_ok=False, raw=reply)

        amount = _parse_positive(data.get("amount_usd"))
        leverage = _parse_positive(data.get("leverage"))
        if action in ("BUY", "SELL_SHORT") and (amount is None or leverage is None):
            # 开仓类决策缺失/非法 amount_usd 或 leverage 时必须降级 HOLD：
            # 历史行为是缺省取配置上限——把 LLM 的半格式故障放大成最激进开仓，
            # 与「故障一律降级为保守动作」的原则背道而驰。
            self.logger.print_warning(
                f"[{self.symbol}] LLM 决策缺少有效 amount_usd/leverage"
                f"（amount_usd={data.get('amount_usd')!r}, leverage={data.get('leverage')!r}），"
                f"回退 HOLD"
            )
            return self._hold(
                f"决策缺少有效 amount_usd/leverage（{data.get('amount_usd')!r}/"
                f"{data.get('leverage')!r}），保守观望",
                llm_ok=False,
                raw=reply,
            )

        return {
            "action": action,
            "confidence": min(max(_safe_float(data.get("confidence")), 0.0), 1.0),
            "amount_usd": min(max(amount or 0.0, 0.0), self.trading.max_trade_amount),
            "leverage": min(max(int(leverage or 1), 1), self.trading.max_leverage),
            "reason": str(data.get("reason", "")),
            "llm_ok": True,
            "raw_response": reply,
        }

    def _track_llm_health(self, decision: dict[str, Any]) -> None:
        """跟踪 LLM 连续故障并在达阈值时升级告警（故障期间只告警一次）。"""
        threshold = self.trading.llm_failure_alert_cycles
        if threshold <= 0:
            return
        if decision.get("llm_ok", True):
            if self._llm_failure_streak:
                self.logger.print_info(
                    f"[{self.symbol}] ✅ 永续 LLM 决策已恢复正常"
                    f"（此前连续失败 {self._llm_failure_streak} 周期）"
                )
            self._llm_failure_streak = 0
            self._llm_alert_sent = False
            return
        self._llm_failure_streak += 1
        if self._llm_failure_streak < threshold or self._llm_alert_sent:
            return
        self.logger.print_error(
            f"🚨 {self.symbol} 永续 LLM 决策连续 {self._llm_failure_streak} 个周期失败，"
            f"策略已持续保守观望。最近一次原因: {str(decision.get('reason', ''))[:300]}。"
            f"请检查 LLM 供应商模型名是否已下线、API 余额与网络连通性"
        )
        self._llm_alert_sent = True

    def _hold(self, reason: str, llm_ok: bool, raw: str = "") -> dict[str, Any]:
        """构造保守 HOLD 决策（llm_ok=False 表示 LLM 自身不可用，供健康跟踪）。"""
        return {
            "action": "HOLD",
            "confidence": 0.0,
            "amount_usd": 0.0,
            "leverage": 1,
            "reason": reason,
            "llm_ok": llm_ok,
            "raw_response": raw,
        }

    # ── 执行 ──────────────────────────────────────────────────────────────

    def _execute(
        self,
        decision: dict[str, Any],
        positions: list[dict[str, Any]],
        open_allowed: bool,
    ) -> dict[str, Any]:
        """按决策执行交易，返回带执行结果的记录。"""
        record = {**decision, "executed": False, "size": 0.0, "pnl": None}
        action = decision["action"]

        if action == "HOLD":
            return record

        if action == "CLOSE":
            return self._execute_close(record, positions)

        # 开仓类动作（BUY / SELL_SHORT）的前置校验
        if not open_allowed:
            record["reason"] += "（本轮禁止开新仓，已拦截）"
            return record
        if decision["confidence"] < self.trading.min_confidence:
            record["reason"] += (
                f"（置信度 {decision['confidence']:.2f} 低于门槛 "
                f"{self.trading.min_confidence:.2f}，已拦截）"
            )
            return record

        is_long = action == "BUY"
        if self._find_position(positions, is_long=is_long) is not None:
            record["reason"] += "（已持有同向仓位，已拦截）"
            return record
        if self._find_position(positions, is_long=not is_long) is not None:
            # Hyperliquid 净头寸制：持空时 BUY 不是开多而是净额抵消平空，
            # 记账/TP-SL/风控上报会全部失真。反向意图必须先 CLOSE 再开仓。
            record["reason"] += "（持有反向仓位，请先 CLOSE 平仓，已拦截）"
            return record
        held_symbols = {p.get("coin") for p in positions}
        if self.symbol not in held_symbols and len(held_symbols) >= self.trading.max_positions:
            record["reason"] += f"（持仓数已达上限 {self.trading.max_positions}，已拦截）"
            return record

        amount = decision["amount_usd"]
        if amount <= 0 or not self.order_manager.check_sufficient_balance(amount):
            record["reason"] += f"（余额不足以投入 ${amount:.2f}，已拦截）"
            return record

        execute = self.order_manager.execute_long if is_long else self.order_manager.execute_short
        result = execute(self.symbol, amount, decision["leverage"], with_tpsl=True)
        if result and result.get("success"):
            record.update(
                executed=True,
                is_long=is_long,
                size=_safe_float(result.get("quantity")),
                entry_price=_safe_float(result.get("fill_price")),
            )
        elif (
            result
            and result.get("rollback_executed")
            and not result.get("rollback_final_success", True)
        ):
            # 止损设置失败且回滚平仓也失败：持仓真实存在但没有交易所侧止损。
            # 必须如实上报 executed=True，让账户级保护（超时/回撤）接管该仓位，
            # 否则它对整个风控体系不可见。
            record.update(
                executed=True,
                is_long=is_long,
                size=_safe_float(result.get("quantity")),
                entry_price=_safe_float(result.get("fill_price") or result.get("price")),
            )
            record["reason"] += (
                "（严重：止损设置失败且回滚平仓失败，持仓无交易所侧止损，请立即人工处理）"
            )
            self.logger.print_error(
                f"🚨 [{self.symbol}] 开仓后止损设置失败且自动回滚失败，"
                f"持仓 {record['size']} 张无交易所侧止损保护，请立即人工处理！"
            )
        else:
            record["reason"] += "（下单未成功）"
        return record

    def _execute_close(
        self, record: dict[str, Any], positions: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """平掉当前持仓并计算已实现盈亏（多空方向自适应）。"""
        position = self._find_position(positions)
        if position is None:
            record["reason"] += "（未持仓，无可平，已拦截）"
            return record

        szi = _safe_float(position.get("szi"))
        entry_price = _safe_float(position.get("entryPx"))
        result = self.order_manager.close_position(self.symbol)
        if not result or result.get("status") != "ok":
            record["reason"] += "（平仓未成功）"
            return record

        exit_price = _safe_float(result.get("fill_price"), entry_price)
        size = abs(szi)
        pnl = (exit_price - entry_price) * size if szi > 0 else (entry_price - exit_price) * size
        record.update(executed=True, is_long=szi > 0, size=size, pnl=pnl)
        return record

    # ── 辅助 ──────────────────────────────────────────────────────────────

    def _find_position(
        self, positions: list[dict[str, Any]], is_long: bool | None = None
    ) -> dict[str, Any] | None:
        """查找本交易对的持仓；is_long=None 表示任意方向。"""
        for p in positions:
            if p.get("coin") != self.symbol:
                continue
            szi = _safe_float(p.get("szi"))
            if szi == 0:
                continue
            if is_long is None or (szi > 0) == is_long:
                return p
        return None

    def _render_prompt(
        self,
        market_data: dict[str, Any],
        trends: dict[str, str],
        positions: list[dict[str, Any]],
        available_balance: float,
        open_allowed: bool,
    ) -> str:
        """渲染决策 Prompt（缺失指标以安全默认值兜底）。"""
        current_price = _safe_float(market_data.get("current_price"))
        ma_text = "  ".join(
            f"{k.upper()}: {_safe_float(v):.4f}"
            for k, v in sorted(market_data.items())
            if k.startswith("ma_")
        )
        position = self._find_position(positions)
        if position is not None:
            szi = _safe_float(position.get("szi"))
            entry = _safe_float(position.get("entryPx"))
            upnl = _safe_float(position.get("unrealizedPnl"))
            side = "多" if szi > 0 else "空"
            position_text = (
                f"{self.symbol} {side}仓 {abs(szi)} 张 | 入场 {entry:.4f} | 未实现盈亏 ${upnl:.2f}"
            )
        else:
            position_text = "（当前无持仓）"

        return self._template.render(
            symbol=self.symbol,
            timeframe=self.trading.timeframe,
            current_price=current_price,
            open=_safe_float(market_data.get("open"), current_price),
            high=_safe_float(market_data.get("high"), current_price),
            low=_safe_float(market_data.get("low"), current_price),
            rsi=_safe_float(market_data.get("rsi"), 50.0),
            macd=_safe_float(market_data.get("macd")),
            macd_signal=_safe_float(market_data.get("macd_signal")),
            macd_hist=_safe_float(market_data.get("macd_hist")),
            bb_upper=_safe_float(market_data.get("bb_upper"), current_price),
            bb_middle=_safe_float(market_data.get("bb_middle"), current_price),
            bb_lower=_safe_float(market_data.get("bb_lower"), current_price),
            bb_position=_safe_float(market_data.get("bb_position"), 0.5),
            ma_text=ma_text or "（无）",
            volume=_safe_float(market_data.get("volume")),
            volume_change=_safe_float(market_data.get("volume_change")),
            trends_text="\n".join(f"{tf}: {trend}" for tf, trend in trends.items()) or "（无）",
            available_balance=available_balance,
            position_count=len({p.get("coin") for p in positions}),
            max_positions=self.trading.max_positions,
            max_trade_amount=self.trading.max_trade_amount,
            max_leverage=self.trading.max_leverage,
            take_profit_ratio=self.trading.take_profit_ratio,
            stop_loss_ratio=self.trading.stop_loss_ratio,
            position_text=position_text,
            open_allowed=open_allowed,
        )


def _safe_float(value: Any, default: float = 0.0) -> float:
    """安全转 float：None/非数值返回默认值。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_positive(value: Any) -> float | None:
    """解析必填正数字段：缺失/非数值/非正数一律返回 None（供调用方拒绝决策）。"""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
