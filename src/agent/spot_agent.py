"""
现货定投 Agent 模块
专门负责长期现货投资决策，关注长期持有价值和定投时机
"""

from typing import Dict, Any, Tuple
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent

from src.agent.tools import TradingTools
from src.agent.execution_agent import ExecutionAgent
from src.trading.order_manager import OrderManager
from src.utils.logger import TradingLogger


# 现货定投 Agent 系统提示词
SPOT_AGENT_SYSTEM_PROMPT = """你是一位专业的加密货币价值投资顾问，专注于长期现货定投策略。

你的投资理念:
- 长期价值投资，不追求短期波动
- 在熊市深度回调时分批建仓
- 只投资基本面优质的主流资产（BTC、ETH等）
- 关注长期成本和增长潜力
- 耐心持有，不被短期波动影响

你的决策标准:
1. 极度保守 - 只在非常明确的定投机会时行动
2. 严格筛选 - 必须满足多个严格条件
3. 深度分析 - 综合考虑技术面和长期趋势
4. 风险优先 - 宁可错过也不冒险

你必须使用提供的工具来执行决策，而不仅仅是提供建议。
"""


def create_spot_agent_prompt(
    symbol: str,
    market_data: Dict[str, Any],
    multi_timeframe_trends: Dict[str, str],
    recommendation: Dict[str, Any],
    current_spot_holdings: list
) -> str:
    """
    创建现货定投决策的 Prompt

    Args:
        symbol: 交易对
        market_data: 市场数据
        multi_timeframe_trends: 多时间周期趋势
        recommendation: 单币 Agent 的推荐信息
        current_spot_holdings: 当前现货持仓

    Returns:
        完整的 Prompt 字符串
    """
    current_price = market_data.get('current_price', 0)
    rsi = market_data.get('rsi', 0)
    macd_hist = market_data.get('macd_hist', 0)
    ma_7 = market_data.get('ma_7', 0)
    ma_25 = market_data.get('ma_25', 0)
    ma_99 = market_data.get('ma_99', 0)
    bb_position = market_data.get('bb_position', 0.5)
    volume_change = market_data.get('volume_change', 0)

    # 检查是否已持有该现货
    has_spot = any(h.get('symbol') == symbol for h in current_spot_holdings)

    prompt = f"""你收到了一个来自单币交易 Agent 的现货定投推荐，请评估是否执行。

## 📊 推荐信息

**来自:** 单币 Agent ({symbol})
**推荐原因:** {recommendation.get('reason', '未提供原因')}
**推荐时间:** {recommendation.get('timestamp', '未知时间')}

## 📈 {symbol} 市场数据

**基础信息:**
- 当前价格: ${current_price:.2f}
- 现货持仓: {"已持有 ✅" if has_spot else "未持有 ❌"}

**技术指标 (15分钟):**
- RSI(14): {rsi:.2f}
- MACD 柱状图: {macd_hist:.4f}
- MA(7): ${ma_7:.2f} | MA(25): ${ma_25:.2f} | MA(99): ${ma_99:.2f}
- 布林带位置: {bb_position:.2%} (0=下轨, 1=上轨)
- 成交量变化: {volume_change:.2f}%

**多时间周期趋势:**
"""

    for timeframe in ['日线', '4小时', '1小时', '15分钟', '1分钟']:
        trend = multi_timeframe_trends.get(timeframe, '未知')
        prompt += f"- {timeframe}: {trend}\n"

    prompt += f"""

## 🎯 你的任务

作为现货定投专家，你需要评估这个定投推荐，并做出最终决策。

## 🛠️ 可用工具

你有以下两个工具可以使用:

1. **buy_spot** - 执行现货定投
   - 使用场景: 当你确认这是一个优质的长期定投机会时
   - 参数: symbol (交易对)
   - 特点: 现货持有，无杠杆，长期投资

2. **do_nothing** - 拒绝推荐
   - 使用场景: 当你认为不满足定投条件时
   - 参数: reason (拒绝原因)

## 📖 现货定投严格标准

请逐项检查以下条件，**必须全部满足**才能执行定投:

### ✅ 必备条件（缺一不可）:

1. **多周期深度下跌**
   - 日线、4小时、1小时趋势**全部**显示"下跌"
   - 没有"上涨"或"震荡"的周期
   - 持续下跌趋势明确

2. **深度超卖**
   - RSI < 30（强烈超卖）
   - 最好 RSI < 25（极度超卖）

3. **价格显著低于均线**
   - 当前价格 < MA(7) < MA(25) < MA(99)
   - 所有均线呈空头排列
   - 价格距离 MA(99) 有明显距离

4. **布林带极限位置**
   - 布林带位置 < 0.2（接近或跌破下轨）
   - 最好 < 0.1

5. **优质主流资产**
   - 仅限 BTC、ETH 等顶级资产
   - 有长期投资价值

6. **MACD 底部区域**
   - MACD 柱状图为负值
   - 最好出现底背离或收窄迹象

7. **未持有该现货**
   - 避免重复定投
   - 分散投资

### ⚠️ 否决条件（出现任一条即拒绝）:

- 任何周期显示"上涨"趋势
- RSI >= 30
- 布林带位置 >= 0.3
- 价格高于 MA(7)
- 非主流优质资产
- 已持有该现货
- 成交量异常放大（可能是恐慌抛售）

## 💭 评估流程

请严格按以下步骤评估:

1. **检查资产质量**
   - 这是 BTC 或 ETH 等主流资产吗？
   - 如果不是，直接拒绝

2. **检查多周期趋势**
   - 日线、4小时、1小时是否**全部**下跌？
   - 有任何上涨或震荡周期吗？
   - 如果不是全部下跌，拒绝

3. **检查超卖程度**
   - RSI 是否 < 30？
   - 布林带位置是否 < 0.2？
   - 如果不够超卖，拒绝

4. **检查均线排列**
   - 是否空头排列（价格 < MA7 < MA25 < MA99）？
   - 如果不是，拒绝

5. **检查持仓状态**
   - 是否已持有该现货？
   - 如果已持有，拒绝

6. **综合决策**
   - 如果**全部条件**都满足，考虑执行 buy_spot
   - 如果**任何一个条件**不满足，执行 do_nothing 并说明原因

## ⚠️ 重要提醒

- **极度保守**: 这是长期投资，宁可错过也不冒险
- **严格标准**: 必须满足所有条件，不能妥协
- **清晰理由**: 无论接受还是拒绝，都要给出清晰的理由
- **独立判断**: 不要盲目接受推荐，基于客观数据判断

## 🚀 现在，请做出你的评估和决策！

请使用 buy_spot 或 do_nothing 工具执行你的决策。
"""

    return prompt


class SpotAgent:
    """现货定投 Agent - 专注于长期价值投资决策"""

    def __init__(
        self,
        order_manager: OrderManager,
        logger: TradingLogger,
        openai_api_base: str,
        openai_api_key: str,
        openai_model: str,
        temperature: float = 0.05,  # 更低的温度，更保守
        trade_amount: float = 100.0,
        notifier=None,
        prompt_manager=None
    ):
        """
        初始化现货定投 Agent

        Args:
            order_manager: 订单管理器
            logger: 日志记录器
            openai_api_base: OpenAI API Base URL
            openai_api_key: OpenAI API Key
            openai_model: 模型名称
            temperature: 温度参数（建议较低）
            trade_amount: 定投金额
            notifier: 通知管理器（可选）
        """
        self.order_manager = order_manager
        self.logger = logger
        self.trade_amount = trade_amount
        self.notifier = notifier
        self.prompt_manager = prompt_manager

        # 初始化 LLM（更保守的参数）
        self.llm = ChatOpenAI(
            base_url=openai_api_base,
            api_key=openai_api_key,
            model=openai_model,
            temperature=temperature,
        )

        # 初始化执行 Agent
        self.execution_agent = ExecutionAgent(
            openai_api_base=openai_api_base,
            openai_api_key=openai_api_key,
            openai_model=openai_model,
            temperature=0.0
        )

        # 创建工具
        self.tools = self._create_tools()

        # 保存工具回调
        self.tools_callbacks = self._get_tool_callbacks()

        # 使用 LangGraph 创建 ReAct Agent
        self.agent_executor = create_react_agent(
            model=self.llm,
            tools=self.tools
        )

        # 系统提示词 - 如果有 PromptManager 则使用它，否则使用硬编码
        if self.prompt_manager:
            system_prompt_text = self.prompt_manager.get_spot_system_prompt()
        else:
            system_prompt_text = SPOT_AGENT_SYSTEM_PROMPT
        self.system_message = SystemMessage(content=system_prompt_text)

        # 当前价格缓存
        self.current_price = 0.0
        self.current_symbol = ""

    def _create_tools(self) -> list:
        """创建工具集（仅包含现货买入和不操作）"""

        def buy_spot_callback(symbol: str, amount: float = None) -> str:
            """现货买入回调"""
            try:
                # 检查是否允许定投（trade_amount > 0 表示允许）
                if self.trade_amount <= 0:
                    return f"❌ 当前余额不足，无法进行现货定投。"

                # 确定实际定投金额
                actual_amount = amount if amount is not None else self.trade_amount

                # 验证金额不超过上限
                if actual_amount > self.trade_amount:
                    return f"❌ 定投金额 ${actual_amount:.2f} 超过上限 ${self.trade_amount:.2f}"

                if actual_amount <= 0:
                    return f"❌ 定投金额必须大于 0"

                self.logger.print_info(f"[现货Agent] 执行现货定投: {symbol}, 金额: ${actual_amount:.2f}")

                # 检查余额
                balance_info = self.order_manager.get_available_balance_info()
                if balance_info['status'] != 'ok':
                    return f"❌ {balance_info['message']}"

                available = balance_info['available']
                if available < actual_amount:
                    return f"❌ 可用余额不足。需要: ${actual_amount:.2f}, 可用: ${available:.2f}"

                # 执行现货买入
                result = self.order_manager.buy_spot_for_dca(
                    symbol=symbol,
                    usdt_amount=actual_amount
                )

                if result and result.get('success'):
                    # 发送现货定投通知
                    if self.notifier:
                        self.notifier.notify_spot_investment(
                            symbol=symbol,
                            quantity=result.get('amount', 0),
                            price=result['price'],
                            amount=actual_amount,
                            order_hash=result.get('hash', '')
                        )

                    return (
                        f"✅ 现货定投执行成功！\n"
                        f"  币种: {symbol}\n"
                        f"  投入: ${actual_amount:.2f}\n"
                        f"  价格: ${result['price']:.2f}\n"
                        f"  数量: {result.get('amount', 0):.6f}\n"
                        f"  📦 长期持有，无止盈止损"
                    )
                else:
                    return "❌ 现货定投失败，请检查日志"

            except Exception as e:
                return f"❌ 现货定投异常: {str(e)}"

        def do_nothing_callback(reason: str) -> str:
            """不操作回调"""
            self.logger.print_info(f"[现货Agent] 拒绝定投推荐 - {reason}")
            return f"⏸️  拒绝定投推荐。原因：{reason}"

        trading_tools = TradingTools(
            buy_callback=lambda s: "现货Agent不支持合约买入",
            sell_callback=lambda s: "现货Agent不支持合约卖出",
            sell_short_callback=lambda s: "现货Agent不支持做空",
            buy_to_cover_callback=lambda s: "现货Agent不支持平空",
            do_nothing_callback=do_nothing_callback,
            buy_spot_callback=buy_spot_callback
        )

        # 保存回调函数引用
        self._buy_spot_callback = buy_spot_callback
        self._do_nothing_callback = do_nothing_callback

        # 只返回现货买入和不操作工具
        return [
            trading_tools.create_buy_spot_tool(),
            trading_tools.create_do_nothing_tool()
        ]

    def _get_tool_callbacks(self) -> Dict[str, Any]:
        """获取工具回调函数字典"""
        return {
            'buy_spot': self._buy_spot_callback,
            'do_nothing': self._do_nothing_callback
        }

    def evaluate_spot_recommendation(
        self,
        symbol: str,
        market_data: Dict[str, Any],
        multi_timeframe_trends: Dict[str, str],
        recommendation: Dict[str, Any],
        current_spot_holdings: list
    ) -> Tuple[str, Dict[str, Any]]:
        """
        评估现货定投推荐

        Args:
            symbol: 交易对
            market_data: 市场数据
            multi_timeframe_trends: 多时间周期趋势
            recommendation: 单币 Agent 的推荐信息
            current_spot_holdings: 当前现货持仓

        Returns:
            (决策类型, 决策详情)
        """
        try:
            # 更新当前价格
            self.current_price = market_data.get('current_price', 0)
            self.current_symbol = symbol

            # 获取实时余额信息
            balance_info = self.order_manager.get_available_balance_info()
            balance_dict = None
            if balance_info.get('status') == 'ok':
                balance_dict = {
                    'total': balance_info['total'],
                    'occupied': balance_info['occupied'],
                    'available': balance_info['available']
                }

            # 创建 Prompt - 使用 PromptManager 或硬编码函数
            if self.prompt_manager:
                prompt = self.prompt_manager.format_spot_prompt(
                    symbol=symbol,
                    market_data=market_data,
                    multi_timeframe_trends=multi_timeframe_trends,
                    recommendation=recommendation,
                    current_spot_holdings=current_spot_holdings,
                    max_trade_amount=self.trade_amount,
                    balance_info=balance_dict
                )
            else:
                prompt = create_spot_agent_prompt(
                    symbol=symbol,
                    market_data=market_data,
                    multi_timeframe_trends=multi_timeframe_trends,
                    recommendation=recommendation,
                    current_spot_holdings=current_spot_holdings
                )

            # 显示 Prompt
            self.logger.print_section(f"[现货Agent] 评估 {symbol} 定投推荐", style="bold blue")
            self.logger.print_prompt(prompt)

            # 调用 Agent
            messages = [
                self.system_message,
                HumanMessage(content=prompt)
            ]

            # 收集所有输出
            all_events = []
            agent_output = ""
            last_printed_content = ""  # 记录上次打印的内容，避免重复打印

            # 使用 config 参数限制最大迭代次数为 3
            # recursion_limit 控制图的最大递归深度，防止无限循环
            config = {"recursion_limit": 6}  # 3 次迭代 * 2
            
            for event in self.agent_executor.stream(
                {"messages": messages},
                stream_mode="values",
                config=config
            ):
                all_events.append(event)
                if "messages" in event and len(event["messages"]) > 0:
                    last_message = event["messages"][-1]
                    if hasattr(last_message, 'content'):
                        content = last_message.content
                        # 更新agent_output为最新的完整内容
                        if content and content != prompt:
                            agent_output = content
                        
                        # 只在内容长度增加时打印（支持流式输出，避免重复打印相同内容）
                        if (content and 
                            content != prompt and 
                            len(content) > len(last_printed_content)):
                            # 使用新的 AI 响应渲染方法（支持 Markdown）
                            self.logger.print_ai_response(content, "💎 现货 Agent 分析中...")
                            last_printed_content = content

            # 解析结果
            decision_type = self._parse_decision_from_events(all_events)
            decision_details = {
                "output": agent_output,
                "events": all_events,
                "prompt": prompt
            }

            return decision_type, decision_details

        except Exception as e:
            self.logger.print_error(f"[现货Agent] 评估异常: {e}")
            self.logger.logger.exception(e)
            return "DO_NOTHING", {"error": str(e)}

    def _parse_decision_from_events(self, events: list) -> str:
        """
        从事件中解析决策类型

        优先从工具调用中解析，如果没有则使用 ExecutionAgent

        Args:
            events: LangGraph 事件列表

        Returns:
            决策类型 (BUY_SPOT, DO_NOTHING, ERROR)
        """
        try:
            # 首先尝试从正式的工具调用中解析
            for event in reversed(events):
                if "messages" not in event:
                    continue

                for message in reversed(event["messages"]):
                    # 检查工具调用
                    if hasattr(message, 'tool_calls') and message.tool_calls:
                        for tool_call in message.tool_calls:
                            tool_name = tool_call.get('name', '')
                            if tool_name == "buy_spot":
                                return "BUY_SPOT"
                            elif tool_name == "do_nothing":
                                return "DO_NOTHING"

                    # 检查工具响应
                    if hasattr(message, 'name'):
                        if message.name == "buy_spot":
                            return "BUY_SPOT"
                        elif message.name == "do_nothing":
                            return "DO_NOTHING"

            # 后备方案：使用 ExecutionAgent 解析文本
            # 只提取 AI 的响应消息，不包括用户 prompt
            decision_text = ""
            for event in reversed(events):
                if "messages" not in event:
                    continue
                for message in reversed(event["messages"]):
                    # 只提取 AI 的响应消息
                    if (hasattr(message, 'content') and
                        isinstance(message.content, str) and
                        hasattr(message, 'type') and
                        message.type == 'ai'):
                        decision_text = message.content
                        break
                if decision_text:
                    break

            if decision_text:
                self.logger.print_info(f"[现货Agent] 未检测到工具调用，使用 ExecutionAgent 解析决策文本")

                execution_plan = self.execution_agent.parse_decision(
                    decision_text=decision_text,
                    symbol=self.current_symbol,
                    logger=self.logger
                )

                result = self.execution_agent.execute_plan(
                    execution_plan=execution_plan,
                    tools_callbacks=self.tools_callbacks,
                    logger=self.logger
                )

                self.logger.print_info(f"[ExecutionAgent] 执行结果: {result}")

                if execution_plan.decision.value == "BUY_SPOT":
                    return "BUY_SPOT"
                else:
                    return "DO_NOTHING"

            return "DO_NOTHING"

        except Exception as e:
            self.logger.logger.error(f"解析现货决策失败: {e}")
            return "ERROR"
