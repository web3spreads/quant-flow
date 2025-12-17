"""
交易 Agent 主类

提供与原有 SingleSymbolAgent 兼容的接口，
内部使用 LangGraph 工作流实现。
"""

from typing import Dict, Any, Tuple, Optional
from langchain_core.messages import SystemMessage

from src.agents.trading.state import TradingAgentState, create_initial_state
from src.agents.trading.workflow import TradingAgentWorkflow
from src.agents.common.tools.trading import TradingToolFactory
from src.agents.common.utils.llm import LLMConfig, create_llm
from src.agents.common.utils.helpers import safe_float, safe_leverage
from src.trading.order_manager import OrderManager
from src.utils.logger import TradingLogger
from src.prompt_manager import PromptManager
from src.config import FEE_RATE_PER_SIDE, MAKER_FEE_RATE_PER_SIDE
from src.fees import FeeRates


class TradingAgent:
    """
    交易 Agent

    为每个交易对维护独立的上下文窗口和决策历史。
    内部使用 LangGraph StateGraph 实现工作流。

    与原有 SingleSymbolAgent 保持接口兼容。
    """

    MIN_PROFIT_TO_FEE_RATIO = 4.0

    def __init__(
        self,
        symbol: str,
        order_manager: OrderManager,
        logger: TradingLogger,
        openai_api_base: str,
        openai_api_key: str,
        openai_model: str,
        temperature: float = 0.1,
        max_iterations: int = 5,
        trade_amount: float = 100.0,
        max_leverage: int = 10,
        take_profit_ratio: float = 0.05,
        stop_loss_ratio: float = 0.02,
        notifier=None,
        prompt_manager: Optional[PromptManager] = None,
        fee_rates: Optional[FeeRates] = None,
    ):
        """
        初始化交易 Agent

        Args:
            symbol: 交易对
            order_manager: 订单管理器
            logger: 日志记录器
            openai_api_base: OpenAI API Base URL
            openai_api_key: OpenAI API Key
            openai_model: 模型名称
            temperature: 温度参数
            max_iterations: 最大迭代次数
            trade_amount: 单笔交易金额上限
            max_leverage: 最大杠杆倍数
            take_profit_ratio: 止盈比例
            stop_loss_ratio: 止损比例
            notifier: 通知管理器（可选）
            prompt_manager: Prompt 管理器（可选）
            fee_rates: 手续费率配置
        """
        self.symbol = symbol
        self.order_manager = order_manager
        self.logger = logger
        self.trade_amount = trade_amount
        self.max_leverage = max_leverage
        self.take_profit_ratio = take_profit_ratio
        self.stop_loss_ratio = stop_loss_ratio
        self.current_price = 0.0
        self.max_iterations = max_iterations
        self.notifier = notifier
        self.prompt_manager = prompt_manager
        self.fee_rates = fee_rates or FeeRates(
            maker_rate=MAKER_FEE_RATE_PER_SIDE,
            taker_rate=FEE_RATE_PER_SIDE
        )

        # 用于去重：记录本次决策周期中已执行的工具调用
        self._executed_callbacks = set()

        # 创建 LLM 配置
        self.llm_config = LLMConfig(
            api_base=openai_api_base,
            api_key=openai_api_key,
            model=openai_model,
            temperature=temperature,
        )

        # 创建工具和回调
        self.tool_factory = self._create_tool_factory()
        self.tools = self.tool_factory.get_all_tools()
        self.tools_callbacks = self.tool_factory.get_callbacks_dict()

        # 创建工作流
        if prompt_manager:
            self.workflow = TradingAgentWorkflow(
                llm_config=self.llm_config,
                prompt_manager=prompt_manager,
                tools=self.tools,
                tools_callbacks=self.tools_callbacks,
                logger=logger,
                max_iterations=max_iterations,
            )
        else:
            self.workflow = None

    def _create_tool_factory(self) -> TradingToolFactory:
        """
        创建工具工厂

        定义所有交易工具的回调函数。
        """
        return TradingToolFactory(
            buy_callback=self._buy_callback,
            sell_callback=self._sell_callback,
            sell_short_callback=self._sell_short_callback,
            buy_to_cover_callback=self._buy_to_cover_callback,
            do_nothing_callback=self._do_nothing_callback,
            buy_spot_callback=self._buy_spot_callback,
        )

    def _check_fee_guard(self) -> Optional[str]:
        """
        确保当前止盈目标足以覆盖手续费
        """
        total_fee_rate = self.fee_rates.taker_rate * 2
        profit_to_fee_ratio = self.take_profit_ratio / total_fee_rate

        if profit_to_fee_ratio < self.MIN_PROFIT_TO_FEE_RATIO:
            return (
                f"❌ 当前止盈比例 {self.take_profit_ratio:.2%} "
                f"仅为手续费的 {profit_to_fee_ratio:.1f} 倍，"
                f"低于最低要求 {self.MIN_PROFIT_TO_FEE_RATIO}x"
            )
        return None

    def _buy_callback(
        self,
        symbol: str,
        amount: Optional[float] = None,
        leverage: Optional[int] = None
    ) -> str:
        """买入开多回调"""
        try:
            if self.trade_amount <= 0:
                return "❌ 当前余额不足，无法开新仓。请专注于管理现有持仓。"

            fee_guard_msg = self._check_fee_guard()
            if fee_guard_msg:
                return fee_guard_msg

            actual_amount = amount if amount is not None else self.trade_amount
            actual_leverage = leverage if leverage is not None else self.max_leverage

            if actual_amount > self.trade_amount:
                return f"❌ 交易金额 ${actual_amount} 超过上限 ${self.trade_amount}"
            if actual_leverage > self.max_leverage:
                return f"❌ 杠杆倍数 {actual_leverage}x 超过上限 {self.max_leverage}x"

            self.logger.print_info(
                f"[{self.symbol}Agent] 执行买入开多 "
                f"(金额: ${actual_amount}, 杠杆: {actual_leverage}x)"
            )

            if not self.order_manager.check_sufficient_balance(actual_amount):
                return f"❌ 余额不足，需要 {actual_amount} USDT"

            result = self.order_manager.execute_long(
                symbol=self.symbol,
                usdt_amount=actual_amount,
                leverage=actual_leverage,
                with_tpsl=True
            )

            if result and result.get('success'):
                entry_price = self.current_price
                tp_price = entry_price * (1 + self.take_profit_ratio)
                sl_price = entry_price * (1 - self.stop_loss_ratio)
                quantity = result.get('quantity', 0)

                if self.notifier:
                    self.notifier.notify_trade_opened(
                        symbol=self.symbol,
                        side="long",
                        quantity=quantity,
                        price=entry_price,
                        leverage=actual_leverage,
                        stop_loss=sl_price,
                        take_profit=tp_price,
                        position_value=quantity * entry_price,
                        margin=actual_amount,
                        reason=f"AI 策略分析，多头信号确认",
                        order_hash=result.get('hash', '')
                    )

                return (
                    f"✅ 买入开多成功！\n"
                    f"  金额: ${actual_amount} USD\n"
                    f"  杠杆: {actual_leverage}x\n"
                    f"  入场价: ${entry_price:.2f}\n"
                    f"  止盈价: ${tp_price:.2f}\n"
                    f"  止损价: ${sl_price:.2f}"
                )

            error_msg = f"❌ 买入开多失败: {result}"
            self.logger.print_error(f"[{self.symbol}Agent] {error_msg}")
            return error_msg

        except Exception as e:
            error_msg = f"❌ 买入开多异常: {str(e)}"
            self.logger.print_error(f"[{self.symbol}Agent] {error_msg}")
            return error_msg

    def _sell_callback(self, symbol: str) -> str:
        """卖出平多回调"""
        try:
            self.logger.print_info(f"[{self.symbol}Agent] 执行卖出平多")

            positions = self.order_manager.get_current_positions()
            position = next(
                (p for p in positions
                 if p.get('coin') == self.symbol and safe_float(p.get('szi', 0)) > 0),
                None
            )

            if not position:
                return f"❌ 未持有 {self.symbol} 的多头仓位"

            result = self.order_manager.close_position(symbol=self.symbol, size=None)

            if result and result.get('status') == 'ok':
                if self.notifier:
                    entry_price = float(position.get('entryPx', 0))
                    exit_price = self.current_price
                    size = abs(float(position.get('szi', 0)))
                    pnl = result.get('pnl', 0)
                    leverage = safe_leverage(position.get('leverage'), 1)
                    pnl_percent = (
                        (exit_price - entry_price) / entry_price * leverage * 100
                        if entry_price > 0 else 0
                    )

                    self.notifier.notify_trade_closed(
                        symbol=self.symbol,
                        side="long",
                        quantity=size,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        pnl=pnl,
                        pnl_percent=pnl_percent,
                        order_hash=result.get('hash', '')
                    )

                return "✅ 卖出平多成功！"

            return f"❌ 卖出平多失败: {result}"

        except Exception as e:
            error_msg = f"❌ 卖出平多异常: {str(e)}"
            self.logger.print_error(f"[{self.symbol}Agent] {error_msg}")
            return error_msg

    def _sell_short_callback(
        self,
        symbol: str,
        amount: Optional[float] = None,
        leverage: Optional[int] = None
    ) -> str:
        """卖空开空回调"""
        try:
            if self.trade_amount <= 0:
                return "❌ 当前余额不足，无法开新仓。请专注于管理现有持仓。"

            fee_guard_msg = self._check_fee_guard()
            if fee_guard_msg:
                return fee_guard_msg

            actual_amount = amount if amount is not None else self.trade_amount
            actual_leverage = leverage if leverage is not None else self.max_leverage

            if actual_amount > self.trade_amount:
                return f"❌ 交易金额 ${actual_amount} 超过上限 ${self.trade_amount}"
            if actual_leverage > self.max_leverage:
                return f"❌ 杠杆倍数 {actual_leverage}x 超过上限 {self.max_leverage}x"

            self.logger.print_info(
                f"[{self.symbol}Agent] 执行卖空开空 "
                f"(金额: ${actual_amount}, 杠杆: {actual_leverage}x)"
            )

            if not self.order_manager.check_sufficient_balance(actual_amount):
                return f"❌ 余额不足，需要 {actual_amount} USDT"

            result = self.order_manager.execute_short(
                symbol=self.symbol,
                usdt_amount=actual_amount,
                leverage=actual_leverage,
                with_tpsl=True
            )

            if result and result.get('success'):
                entry_price = self.current_price
                tp_price = entry_price * (1 - self.take_profit_ratio)
                sl_price = entry_price * (1 + self.stop_loss_ratio)
                quantity = result.get('quantity', 0)

                if self.notifier:
                    self.notifier.notify_trade_opened(
                        symbol=self.symbol,
                        side="short",
                        quantity=quantity,
                        price=entry_price,
                        leverage=actual_leverage,
                        stop_loss=sl_price,
                        take_profit=tp_price,
                        position_value=quantity * entry_price,
                        margin=actual_amount,
                        reason=f"AI 策略分析，空头信号确认",
                        order_hash=result.get('hash', '')
                    )

                return (
                    f"✅ 卖空开空成功！\n"
                    f"  金额: ${actual_amount} USD\n"
                    f"  杠杆: {actual_leverage}x\n"
                    f"  入场价: ${entry_price:.2f}\n"
                    f"  止盈价: ${tp_price:.2f}\n"
                    f"  止损价: ${sl_price:.2f}"
                )

            error_msg = f"❌ 卖空开空失败: {result}"
            self.logger.print_error(f"[{self.symbol}Agent] {error_msg}")
            return error_msg

        except Exception as e:
            error_msg = f"❌ 卖空开空异常: {str(e)}"
            self.logger.print_error(f"[{self.symbol}Agent] {error_msg}")
            return error_msg

    def _buy_to_cover_callback(self, symbol: str) -> str:
        """买入平空回调"""
        try:
            self.logger.print_info(f"[{self.symbol}Agent] 执行买入平空")

            positions = self.order_manager.get_current_positions()
            position = next(
                (p for p in positions
                 if p.get('coin') == self.symbol and safe_float(p.get('szi', 0)) < 0),
                None
            )

            if not position:
                return f"❌ 未持有 {self.symbol} 的空头仓位"

            result = self.order_manager.close_position(symbol=self.symbol, size=None)

            if result and result.get('status') == 'ok':
                if self.notifier:
                    entry_price = float(position.get('entryPx', 0))
                    exit_price = self.current_price
                    size = abs(float(position.get('szi', 0)))
                    pnl = result.get('pnl', 0)
                    leverage = safe_leverage(position.get('leverage'), 1)
                    pnl_percent = (
                        (entry_price - exit_price) / entry_price * leverage * 100
                        if entry_price > 0 else 0
                    )

                    self.notifier.notify_trade_closed(
                        symbol=self.symbol,
                        side="short",
                        quantity=size,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        pnl=pnl,
                        pnl_percent=pnl_percent,
                        order_hash=result.get('hash', '')
                    )

                return "✅ 买入平空成功！"

            return f"❌ 买入平空失败: {result}"

        except Exception as e:
            error_msg = f"❌ 买入平空异常: {str(e)}"
            self.logger.print_error(f"[{self.symbol}Agent] {error_msg}")
            return error_msg

    def _do_nothing_callback(self, reason: str) -> str:
        """不操作回调"""
        callback_key = f"do_nothing:{reason}"
        if callback_key not in self._executed_callbacks:
            self.logger.print_info(f"[{self.symbol}Agent] 不操作 - {reason}")
            self._executed_callbacks.add(callback_key)
        return f"⏸️ 确认：不执行操作。原因：{reason}"

    def _buy_spot_callback(
        self,
        symbol: str,
        amount: Optional[float] = None
    ) -> str:
        """现货定投推荐回调"""
        if self.trade_amount <= 0:
            return "❌ 当前余额不足，无法进行现货定投。"

        actual_amount = amount if amount is not None else self.trade_amount
        if actual_amount > self.trade_amount:
            return f"❌ 定投金额 ${actual_amount} 超过上限 ${self.trade_amount}"

        self.logger.print_info(
            f"[{self.symbol}Agent] 推荐现货定投 (建议金额: ${actual_amount})"
        )
        return f"📝 已推荐 {symbol} 现货定投 (建议金额: ${actual_amount})"

    def make_decision(
        self,
        market_data: Dict[str, Any],
        multi_timeframe_trends: Dict[str, str],
        current_positions: list,
        max_positions: int,
        historical_summary: Optional[str] = None,
        enriched_data: Optional[Dict[str, Any]] = None
    ) -> Tuple[str, Dict[str, Any]]:
        """
        做出交易决策

        Args:
            market_data: 市场数据
            multi_timeframe_trends: 多时间周期趋势
            current_positions: 当前持仓
            max_positions: 最大持仓数
            historical_summary: 历史决策汇总（可选）
            enriched_data: 增强数据（可选）

        Returns:
            (决策类型, 决策详情)
        """
        try:
            # 重置去重状态
            self._executed_callbacks.clear()

            # 更新当前价格
            self.current_price = market_data.get('current_price', 0)

            # 获取实时余额信息
            balance_info = self.order_manager.get_available_balance_info()
            balance_dict = None
            if balance_info.get('status') == 'ok':
                balance_dict = {
                    'total': balance_info['total'],
                    'occupied': balance_info['occupied'],
                    'available': balance_info['available']
                }

            if not self.prompt_manager:
                raise ValueError("PromptManager 是必需的，但未提供")

            if not self.workflow:
                raise ValueError("工作流未初始化")

            # 创建初始状态
            initial_state = create_initial_state(
                symbol=self.symbol,
                market_data=market_data,
                multi_timeframe_trends=multi_timeframe_trends,
                current_positions=current_positions,
                max_positions=max_positions,
                trade_amount=self.trade_amount,
                max_leverage=self.max_leverage,
                take_profit_ratio=self.take_profit_ratio,
                stop_loss_ratio=self.stop_loss_ratio,
                historical_summary=historical_summary,
                balance_info=balance_dict,
                enriched_data=enriched_data,
            )

            # 运行工作流
            final_state = self.workflow.run(initial_state)

            # 提取结果
            decision_type = final_state.get('decision_type', 'DO_NOTHING')
            decision_details = {
                "output": final_state.get('decision_details', {}).get('output', ''),
                "events": final_state.get('decision_details', {}).get('events', []),
                "prompt": final_state.get('prompt', ''),
                "symbol": self.symbol,
                "execution_result": final_state.get('execution_result'),
            }

            return decision_type, decision_details

        except Exception as e:
            self.logger.print_error(f"[{self.symbol}Agent] 决策异常: {e}")
            self.logger.logger.exception(e)
            return "ERROR", {"error": str(e)}
