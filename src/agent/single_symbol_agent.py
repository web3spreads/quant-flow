"""
单币种交易 Agent 模块
为每个交易对维护独立的上下文窗口和决策历史
"""

from typing import Dict, Any, Tuple, Optional
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent

from src.agent.tools import TradingTools
from src.agent.prompts import SYSTEM_PROMPT
from src.trading.order_manager import OrderManager
from src.utils.logger import TradingLogger
from src.prompt_manager import PromptManager


def create_single_symbol_prompt(
    symbol: str,
    market_data: Dict[str, Any],
    multi_timeframe_trends: Dict[str, str],
    current_positions: list,
    max_positions: int,
    historical_summary: Optional[str] = None
) -> str:
    """
    创建单个交易对的决策 Prompt

    Args:
        symbol: 交易对
        market_data: 市场数据
        multi_timeframe_trends: 多时间周期趋势
        current_positions: 当前持仓
        max_positions: 最大持仓数
        historical_summary: 历史决策汇总（可选）

    Returns:
        完整的 Prompt 字符串
    """
    current_price = market_data.get('current_price', 0)
    rsi = market_data.get('rsi', 0)
    macd = market_data.get('macd', 0)
    macd_signal = market_data.get('macd_signal', 0)
    macd_hist = market_data.get('macd_hist', 0)

    ma_7 = market_data.get('ma_7', 0)
    ma_25 = market_data.get('ma_25', 0)
    ma_99 = market_data.get('ma_99', 0)

    bb_upper = market_data.get('bb_upper', 0)
    bb_middle = market_data.get('bb_middle', 0)
    bb_lower = market_data.get('bb_lower', 0)
    bb_position = market_data.get('bb_position', 0.5)

    volume_change = market_data.get('volume_change', 0)

    # 判断持仓状态
    has_long = any(pos.get('coin') == symbol and pos.get('side', 'long') == 'long' for pos in current_positions)
    has_short = any(pos.get('coin') == symbol and pos.get('side') == 'short' for pos in current_positions)
    position_count = len(current_positions)

    prompt = f"""你是一位经验丰富的加密货币量化交易专家，专注于 {symbol} 的交易决策。

## 📊 当前市场数据 ({symbol})

**基础信息:**
- 当前价格: ${current_price:.2f}
- 交易对: {symbol}

**技术指标 (15分钟):**
- RSI(14): {rsi:.2f}
- MACD: {macd:.4f} | 信号线: {macd_signal:.4f} | 柱状图: {macd_hist:.4f}
- MA(7): ${ma_7:.2f} | MA(25): ${ma_25:.2f} | MA(99): ${ma_99:.2f}
- 布林带: 上轨 ${bb_upper:.2f} | 中轨 ${bb_middle:.2f} | 下轨 ${bb_lower:.2f}
- 价格位置: {bb_position:.2%} (0=下轨, 1=上轨)
- 成交量变化: {volume_change:.2f}%

**多时间周期趋势分析:**
"""

    for timeframe in ['日线', '4小时', '1小时', '15分钟', '1分钟']:
        trend = multi_timeframe_trends.get(timeframe, '未知')
        prompt += f"- {timeframe}: {trend}\n"

    prompt += f"""

## 📋 持仓状态
- 当前总持仓数量: {position_count}/{max_positions}
- {symbol} 多头持仓: {"是 ✅" if has_long else "否 ❌"}
- {symbol} 空头持仓: {"是 ✅" if has_short else "否 ❌"}
"""

    # 如果有历史汇总，添加到 prompt
    if historical_summary:
        prompt += f"""

## 📜 历史决策汇总

{historical_summary}

**提示:** 以上是你过去的决策记录汇总，可以帮助你理解市场演变和之前的策略。但请基于当前市场数据做出独立判断。
"""

    prompt += """

## 🎯 你的目标
你的目标是通过分析市场数据，为该交易对实现长期稳定盈利。你必须谨慎决策，避免过度交易。

## 🛠️ 可用工具
你有以下工具可以使用（必须选择其中一个）:

### 做多操作（Long Position - 杠杆合约）:

1. **buy** - 买入开多（合约）
   - 使用场景: 当市场出现明确的看涨信号时
   - 参数: symbol (交易对)
   - 注意: 系统会自动设置止盈（+5%）和止损（-2%）
   - 前提: 未持有该币种的多头仓位，且持仓数量未满

2. **sell** - 卖出平多（合约）
   - 使用场景: 当已持有多头仓位，且出现卖出信号时
   - 参数: symbol (交易对)
   - 前提: 必须已持有该币种的多头仓位

### 做空操作（Short Position - 杠杆合约）:

3. **sell_short** - 卖空开空（合约）
   - 使用场景: 当市场出现明确的看跌信号时
   - 参数: symbol (交易对)
   - 注意: 系统会自动设置止盈（-5%）和止损（+2%）
   - 前提: 未持有该币种的空头仓位，且持仓数量未满

4. **buy_to_cover** - 买入平空（合约）
   - 使用场景: 当已持有空头仓位，且出现平仓信号时
   - 参数: symbol (交易对)
   - 前提: 必须已持有该币种的空头仓位

### 现货定投操作（Spot DCA - 长期投资）:

5. **buy_spot** - 现货买入（定投）
   - 使用场景: 当检测到优质资产的长期定投机会时
   - 参数: symbol (交易对)
   - 特点:
     * 无杠杆，现货持有
     * 长期持有，无止盈止损
     * 适合熊市底部区域定投
   - ⚠️ 重要条件:
     * 多周期一致深度下跌（日线、4小时、1小时全部下跌）
     * RSI < 30（深度超卖）
     * 价格显著低于所有均线
     * 仅对 BTC、ETH 等主流资产使用
   - 🔔 决策流程:
     * 你只需要**推荐**这个操作
     * 推荐后会交给专门的现货 Agent 做最终决策
     * 现货 Agent 会更严格地评估长期持有价值

### 观望操作:

6. **do_nothing** - 不操作
   - 使用场景: 当市场信号不明确或不满足交易条件时
   - 参数: reason (不操作的原因)

## 📖 决策准则

### 买入开多信号（需同时满足多个条件）:
1. RSI < 40（超卖区域）或 40-50（中性偏弱）
2. MACD 柱状图由负转正，或 MACD 线向上穿越信号线
3. 价格接近或突破 MA(25) 向上，且 MA(7) > MA(25)
4. 价格接近或触及下轨，或从下轨反弹
5. 成交量放大（变化 > 20%）
6. 当前持仓数量 < 最大持仓数量
7. 未持有该币种的多头仓位
8. 多周期趋势: 至少2个以上时间周期显示上涨或转强趋势

### 卖出平多信号（需同时满足多个条件）:
1. 必须已持有该币种的多头仓位
2. RSI > 65（超买区域）
3. MACD 柱状图由正转负，或 MACD 线向下穿越信号线
4. 价格跌破 MA(7) 且 MA(7) < MA(25)
5. 价格接近或触及上轨

### 卖空开空信号（需同时满足多个条件）:
1. RSI > 60（超买区域）或 50-60（中性偏强）
2. MACD 柱状图由正转负，或 MACD 线向下穿越信号线
3. 价格跌破 MA(25) 向下，且 MA(7) < MA(25)
4. 价格接近或触及上轨，或从上轨回落
5. 成交量放大（变化 > 20%）
6. 当前持仓数量 < 最大持仓数量
7. 未持有该币种的空头仓位
8. 多周期趋势: 至少2个以上时间周期显示下跌或转弱趋势

### 买入平空信号（需同时满足多个条件）:
1. 必须已持有该币种的空头仓位
2. RSI < 35（超卖区域）
3. MACD 柱状图由负转正，或 MACD 线向上穿越信号线
4. 价格突破 MA(7) 且 MA(7) > MA(25)
5. 价格接近或触及下轨

### 现货定投推荐信号（极度谨慎，满足严格条件）:
⚠️ **这是给现货 Agent 的推荐，你不直接执行，而是传递推荐**

**推荐条件（必须全部满足）:**
1. **多周期一致深度下跌**:
   - 日线、4小时、1小时趋势**全部**显示下跌
   - 至少持续多个周期

2. **深度超卖**:
   - RSI < 30（深度超卖）
   - 最好 RSI < 25

3. **价格显著低于均线**:
   - 当前价格 < MA(7) < MA(25) < MA(99)
   - 价格距离 MA(99) 有明显距离

4. **布林带极限位置**:
   - 布林带位置 < 0.2

5. **优质主流资产**:
   - 仅限 BTC、ETH 等主流资产

6. **MACD 底部区域**:
   - MACD 柱状图为负值

**推荐方式:**
- 使用 buy_spot 工具进行推荐
- 说明推荐理由
- 现货 Agent 会进行更严格的评估

### 不操作的情况:
1. 市场信号不明确
2. RSI 在 45-55 之间
3. 价格在布林带中轨附近波动
4. 已达到最大持仓且无平仓信号
5. 成交量萎缩（< 10%）
6. 多周期趋势不一致

## ⚠️ 重要约束
1. 绝对不能在未持有多头仓位时执行 sell
2. 绝对不能在未持有空头仓位时执行 buy_to_cover
3. 绝对不能在已持有多头仓位时重复执行 buy
4. 绝对不能在已持有空头仓位时重复执行 sell_short
5. 绝对不能在持仓已满时执行 buy 或 sell_short
6. 绝对不能同时持有同一币种的多头和空头
7. buy_spot 用于推荐，不是直接执行
8. 必须提供清晰的决策理由
9. 必须基于技术指标和多周期趋势综合判断

## 💭 决策流程

1. **分析市场状态**:
   - 查看多周期趋势是否一致
   - 判断当前趋势方向

2. **评估持仓状态**:
   - 是否持有多头或空头仓位
   - 持仓数量是否已满

3. **检查开仓/平仓条件**:
   - 逐一检查各种信号的条件
   - 特别关注多周期趋势一致性

4. **做出决策**:
   - 优先平仓信号
   - 然后考虑开仓信号
   - 谨慎推荐现货定投
   - 其他情况 do_nothing

## 🚀 现在，请做出你的决策！

请使用你的工具来执行决策。记住，你必须调用其中一个工具！
"""

    return prompt


class SingleSymbolAgent:
    """单币种交易 Agent - 为每个交易对维护独立上下文"""

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
        notifier=None,
        prompt_manager: Optional[PromptManager] = None
    ):
        """
        初始化单币种交易 Agent

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
            notifier: 通知管理器（可选）
            prompt_manager: Prompt管理器（可选）
        """
        self.symbol = symbol
        self.order_manager = order_manager
        self.logger = logger
        self.trade_amount = trade_amount
        self.max_leverage = max_leverage
        self.current_price = 0.0
        self.max_iterations = max_iterations
        self.notifier = notifier
        self.prompt_manager = prompt_manager

        # 初始化 LLM
        self.llm = ChatOpenAI(
            base_url=openai_api_base,
            api_key=openai_api_key,
            model=openai_model,
            temperature=temperature,
        )

        # 创建工具
        self.tools = self._create_tools()

        # 使用 LangGraph 创建 ReAct Agent
        self.agent_executor = create_react_agent(
            model=self.llm,
            tools=self.tools
        )

        # 系统提示词 - 如果有 PromptManager 则使用它，否则使用硬编码
        if self.prompt_manager:
            system_prompt_text = self.prompt_manager.get_system_prompt()
        else:
            system_prompt_text = SYSTEM_PROMPT
        self.system_message = SystemMessage(content=system_prompt_text)

    def _create_tools(self) -> list:
        """创建工具集"""

        def buy_callback(symbol: str, amount: Optional[float] = None, leverage: Optional[int] = None) -> str:
            """买入开多回调"""
            try:
                # 使用 AI 指定的金额和杠杆，如果没有指定则使用默认上限
                actual_amount = amount if amount is not None else self.trade_amount
                actual_leverage = leverage if leverage is not None else self.max_leverage

                # 验证参数
                if actual_amount > self.trade_amount:
                    return f"❌ 交易金额 ${actual_amount} 超过上限 ${self.trade_amount}"
                if actual_leverage > self.max_leverage:
                    return f"❌ 杠杆倍数 {actual_leverage}x 超过上限 {self.max_leverage}x"

                self.logger.print_info(f"[{self.symbol}Agent] 执行买入开多 (金额: ${actual_amount}, 杠杆: {actual_leverage}x)")

                if not self.order_manager.check_sufficient_balance(actual_amount):
                    return f"❌ 余额不足，需要 {actual_amount} USDT"

                positions = self.order_manager.get_current_positions()
                has_long = any(p.get('coin') == self.symbol and p.get('side', 'long') == 'long' for p in positions)
                if has_long:
                    return f"❌ 已持有 {self.symbol} 的多头仓位"

                result = self.order_manager.execute_long(
                    symbol=self.symbol,
                    usdt_amount=actual_amount,
                    leverage=actual_leverage,
                    with_tpsl=True
                )

                if result and result.get('success'):
                    # 获取市场订单信息
                    market_order = result.get('market_order', {})
                    entry_price = self.current_price
                    tp_price = entry_price * 1.05  # 5% take profit
                    sl_price = entry_price * 0.98  # 2% stop loss
                    quantity = result.get('quantity', 0)
                    leverage_used = actual_leverage

                    # 发送开仓通知
                    if self.notifier:
                        self.notifier.notify_trade_opened(
                            symbol=self.symbol,
                            side="long",
                            quantity=quantity,
                            price=entry_price,
                            leverage=leverage_used,
                            stop_loss=sl_price,
                            take_profit=tp_price,
                            position_value=quantity * entry_price,
                            margin=actual_amount,
                            reason=f"AI 策略分析，多头信号确认 (金额: ${actual_amount}, 杠杆: {actual_leverage}x)",
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
                return "❌ 买入开多失败"

            except Exception as e:
                return f"❌ 买入开多异常: {str(e)}"

        def sell_callback(symbol: str) -> str:
            """卖出平多回调"""
            try:
                self.logger.print_info(f"[{self.symbol}Agent] 执行卖出平多")

                positions = self.order_manager.get_current_positions()
                position = next((p for p in positions if p.get('coin') == self.symbol and p.get('side', 'long') == 'long'), None)

                if not position:
                    return f"❌ 未持有 {self.symbol} 的多头仓位"

                result = self.order_manager.close_position(
                    symbol=self.symbol,
                    size=None  # Close entire position
                )

                if result and result.get('status') == 'ok':
                    # 发送平仓通知
                    if self.notifier:
                        # Hyperliquid API 字段说明：
                        # 'entryPx' - 入场价格 (entry price)
                        # 'szi' - 仓位大小 (position size)，正数表示多头，负数表示空头
                        entry_price = position.get('entryPx', 0)
                        exit_price = self.current_price
                        size = abs(position.get('szi', 0))
                        pnl = result.get('pnl', 0)
                        pnl_percent = (exit_price - entry_price) / entry_price * 100 if entry_price > 0 else 0

                        self.notifier.notify_trade_closed(
                            symbol=self.symbol,
                            side="long",
                            quantity=size,
                            entry_price=entry_price,
                            exit_price=exit_price,
                            pnl=pnl,
                            pnl_percent=pnl_percent
                        )

                    return f"✅ 卖出平多成功！"
                return "❌ 卖出平多失败"

            except Exception as e:
                return f"❌ 卖出平多异常: {str(e)}"

        def sell_short_callback(symbol: str, amount: Optional[float] = None, leverage: Optional[int] = None) -> str:
            """卖空开空回调"""
            try:
                # 使用 AI 指定的金额和杠杆，如果没有指定则使用默认上限
                actual_amount = amount if amount is not None else self.trade_amount
                actual_leverage = leverage if leverage is not None else self.max_leverage

                # 验证参数
                if actual_amount > self.trade_amount:
                    return f"❌ 交易金额 ${actual_amount} 超过上限 ${self.trade_amount}"
                if actual_leverage > self.max_leverage:
                    return f"❌ 杠杆倍数 {actual_leverage}x 超过上限 {self.max_leverage}x"

                self.logger.print_info(f"[{self.symbol}Agent] 执行卖空开空 (金额: ${actual_amount}, 杠杆: {actual_leverage}x)")

                if not self.order_manager.check_sufficient_balance(actual_amount):
                    return f"❌ 余额不足，需要 {actual_amount} USDT"

                positions = self.order_manager.get_current_positions()
                has_short = any(p.get('coin') == self.symbol and p.get('side') == 'short' for p in positions)
                if has_short:
                    return f"❌ 已持有 {self.symbol} 的空头仓位"

                result = self.order_manager.execute_short(
                    symbol=self.symbol,
                    usdt_amount=actual_amount,
                    leverage=actual_leverage,
                    with_tpsl=True
                )

                if result and result.get('success'):
                    # 获取市场订单信息
                    entry_price = self.current_price
                    tp_price = entry_price * 0.95  # 5% take profit (下跌)
                    sl_price = entry_price * 1.02  # 2% stop loss (上涨)
                    quantity = result.get('quantity', 0)
                    leverage_used = actual_leverage

                    # 发送开仓通知
                    if self.notifier:
                        self.notifier.notify_trade_opened(
                            symbol=self.symbol,
                            side="short",
                            quantity=quantity,
                            price=entry_price,
                            leverage=leverage_used,
                            stop_loss=sl_price,
                            take_profit=tp_price,
                            position_value=quantity * entry_price,
                            margin=actual_amount,
                            reason=f"AI 策略分析，空头信号确认 (金额: ${actual_amount}, 杠杆: {actual_leverage}x)",
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
                return "❌ 卖空开空失败"

            except Exception as e:
                return f"❌ 卖空开空异常: {str(e)}"

        def buy_to_cover_callback(symbol: str) -> str:
            """买入平空回调"""
            try:
                self.logger.print_info(f"[{self.symbol}Agent] 执行买入平空")

                positions = self.order_manager.get_current_positions()
                position = next((p for p in positions if p.get('coin') == self.symbol and p.get('side') == 'short'), None)

                if not position:
                    return f"❌ 未持有 {self.symbol} 的空头仓位"

                result = self.order_manager.close_position(
                    symbol=self.symbol,
                    size=None  # Close entire position
                )

                if result and result.get('status') == 'ok':
                    # 发送平仓通知
                    if self.notifier:
                        # Hyperliquid API 字段说明：
                        # 'entryPx' - 入场价格 (entry price)
                        # 'szi' - 仓位大小 (position size)，正数表示多头，负数表示空头
                        entry_price = position.get('entryPx', 0)
                        exit_price = self.current_price
                        size = abs(position.get('szi', 0))
                        pnl = result.get('pnl', 0)
                        # Use leverage from position if available, default to 1
                        leverage = position.get('leverage', 1)
                        pnl_percent = ((entry_price - exit_price) / entry_price * leverage * 100) if entry_price > 0 else 0

                        self.notifier.notify_trade_closed(
                            symbol=self.symbol,
                            side="short",
                            quantity=size,
                            entry_price=entry_price,
                            exit_price=exit_price,
                            pnl=pnl,
                            pnl_percent=pnl_percent
                        )

                    return f"✅ 买入平空成功！"
                return "❌ 买入平空失败"

            except Exception as e:
                return f"❌ 买入平空异常: {str(e)}"

        def do_nothing_callback(reason: str) -> str:
            """不操作回调"""
            self.logger.print_info(f"[{self.symbol}Agent] 不操作 - {reason}")
            return f"⏸️  确认：不执行操作。原因：{reason}"

        def buy_spot_callback(symbol: str, amount: Optional[float] = None) -> str:
            """现货定投推荐回调（仅推荐，不直接执行）"""
            actual_amount = amount if amount is not None else self.trade_amount
            if actual_amount > self.trade_amount:
                return f"❌ 定投金额 ${actual_amount} 超过上限 ${self.trade_amount}"
            self.logger.print_info(f"[{self.symbol}Agent] 推荐现货定投 (建议金额: ${actual_amount})，将交给现货 Agent 评估")
            return f"📝 已推荐 {symbol} 现货定投 (建议金额: ${actual_amount})，等待现货 Agent 评估"

        trading_tools = TradingTools(
            buy_callback,
            sell_callback,
            sell_short_callback,
            buy_to_cover_callback,
            do_nothing_callback,
            buy_spot_callback
        )
        return trading_tools.get_all_tools()

    def make_decision(
        self,
        market_data: Dict[str, Any],
        multi_timeframe_trends: Dict[str, str],
        current_positions: list,
        max_positions: int,
        historical_summary: Optional[str] = None
    ) -> Tuple[str, Dict[str, Any]]:
        """
        做出交易决策

        Args:
            market_data: 市场数据
            multi_timeframe_trends: 多时间周期趋势
            current_positions: 当前持仓
            max_positions: 最大持仓数
            historical_summary: 历史决策汇总（可选）

        Returns:
            (决策类型, 决策详情)
        """
        try:
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

            # 创建 Prompt - 如果有 PromptManager 则使用它，否则使用硬编码函数
            if self.prompt_manager:
                prompt = self.prompt_manager.format_trading_prompt(
                    symbol=self.symbol,
                    market_data=market_data,
                    multi_timeframe_trends=multi_timeframe_trends,
                    current_positions=current_positions,
                    max_positions=max_positions,
                    max_trade_amount=self.trade_amount,
                    max_leverage=self.max_leverage,
                    historical_summary=historical_summary,
                    balance_info=balance_dict
                )
            else:
                prompt = create_single_symbol_prompt(
                    symbol=self.symbol,
                    market_data=market_data,
                    multi_timeframe_trends=multi_timeframe_trends,
                    current_positions=current_positions,
                    max_positions=max_positions,
                    historical_summary=historical_summary
                )

            # 显示 Prompt
            self.logger.print_section(f"[{self.symbol}Agent] 独立决策分析", style="bold magenta")
            self.logger.print_prompt(prompt)

            # 调用 Agent
            messages = [
                self.system_message,
                HumanMessage(content=prompt)
            ]

            # 收集所有输出
            all_events = []
            agent_output = ""

            for event in self.agent_executor.stream(
                {"messages": messages},
                stream_mode="values"
            ):
                all_events.append(event)
                if "messages" in event and len(event["messages"]) > 0:
                    last_message = event["messages"][-1]
                    if hasattr(last_message, 'content'):
                        content = last_message.content
                        if content and content != prompt and content != agent_output:
                            # 使用新的 AI 响应渲染方法（支持 Markdown）
                            self.logger.print_ai_response(content, f"🎯 {self.symbol} Agent 分析中...")
                            agent_output = content

            # 解析结果
            decision_type = self._parse_decision_from_events(all_events)
            decision_details = {
                "output": agent_output,
                "events": all_events,
                "prompt": prompt,
                "symbol": self.symbol
            }

            return decision_type, decision_details

        except Exception as e:
            self.logger.print_error(f"[{self.symbol}Agent] 决策异常: {e}")
            self.logger.logger.exception(e)
            return "ERROR", {"error": str(e)}

    def _parse_decision_from_events(self, events: list) -> str:
        """
        从事件中解析决策类型

        Args:
            events: LangGraph 事件列表

        Returns:
            决策类型
        """
        try:
            for event in reversed(events):
                if "messages" not in event:
                    continue

                for message in reversed(event["messages"]):
                    if hasattr(message, 'tool_calls') and message.tool_calls:
                        for tool_call in message.tool_calls:
                            tool_name = tool_call.get('name', '')
                            if tool_name == "buy":
                                return "BUY"
                            elif tool_name == "sell":
                                return "SELL"
                            elif tool_name == "sell_short":
                                return "SELL_SHORT"
                            elif tool_name == "buy_to_cover":
                                return "BUY_TO_COVER"
                            elif tool_name == "buy_spot":
                                return "BUY_SPOT_RECOMMEND"
                            elif tool_name == "do_nothing":
                                return "DO_NOTHING"

                    if hasattr(message, 'name'):
                        if message.name == "buy":
                            return "BUY"
                        elif message.name == "sell":
                            return "SELL"
                        elif message.name == "sell_short":
                            return "SELL_SHORT"
                        elif message.name == "buy_to_cover":
                            return "BUY_TO_COVER"
                        elif message.name == "buy_spot":
                            return "BUY_SPOT_RECOMMEND"
                        elif message.name == "do_nothing":
                            return "DO_NOTHING"

            return "DO_NOTHING"

        except Exception as e:
            self.logger.logger.error(f"解析决策失败: {e}")
            return "ERROR"
