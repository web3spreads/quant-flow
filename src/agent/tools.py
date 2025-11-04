"""
LangChain 工具定义
为 AI Agent 提供买入、卖出和不操作的工具
"""

from langchain_core.tools import Tool
from typing import Callable, Optional


class TradingTools:
    """交易工具集"""

    def __init__(
        self,
        buy_callback: Callable[[str], str],
        sell_callback: Callable[[str], str],
        sell_short_callback: Callable[[str], str],
        buy_to_cover_callback: Callable[[str], str],
        do_nothing_callback: Callable[[str], str],
        buy_spot_callback: Optional[Callable[[str], str]] = None
    ):
        """
        初始化交易工具

        Args:
            buy_callback: 买入开多回调函数，接收 symbol，返回执行结果
            sell_callback: 卖出平多回调函数，接收 symbol，返回执行结果
            sell_short_callback: 卖空开空回调函数，接收 symbol，返回执行结果
            buy_to_cover_callback: 买入平空回调函数，接收 symbol，返回执行结果
            do_nothing_callback: 不操作回调函数，接收 reason，返回确认信息
            buy_spot_callback: 现货买入回调函数（可选），接收 symbol，返回执行结果
        """
        self.buy_callback = buy_callback
        self.sell_callback = sell_callback
        self.sell_short_callback = sell_short_callback
        self.buy_to_cover_callback = buy_to_cover_callback
        self.do_nothing_callback = do_nothing_callback
        self.buy_spot_callback = buy_spot_callback

    def create_buy_tool(self) -> Tool:
        """
        创建买入开多工具

        Returns:
            LangChain Tool 对象
        """
        return Tool(
            name="buy",
            description="""
            执行买入开多操作。当市场出现明确的做多信号时使用此工具。

            使用条件:
            - 未持有该币种的多头仓位
            - 未达到最大持仓数量
            - 技术指标显示看涨信号

            参数:
            - symbol: 交易对，如 'BTC/USDT'

            系统会自动:
            - 使用配置的金额买入（开多仓）
            - 设置止盈单（价格上涨 5%）
            - 设置止损单（价格下跌 2%）

            返回: 执行结果描述
            """,
            func=self.buy_callback
        )

    def create_sell_tool(self) -> Tool:
        """
        创建卖出平多工具

        Returns:
            LangChain Tool 对象
        """
        return Tool(
            name="sell",
            description="""
            执行卖出平多操作。当已持有该币种的多头仓位且出现卖出信号时使用此工具。

            使用条件:
            - 必须已持有该币种的多头仓位
            - 技术指标显示卖出信号

            参数:
            - symbol: 交易对，如 'BTC/USDT'

            系统会:
            - 卖出所有持有的该币种（平多仓）
            - 取消相关的止盈止损单

            返回: 执行结果描述
            """,
            func=self.sell_callback
        )

    def create_sell_short_tool(self) -> Tool:
        """
        创建卖空开空工具

        Returns:
            LangChain Tool 对象
        """
        return Tool(
            name="sell_short",
            description="""
            执行卖空开空操作。当市场出现明确的做空信号时使用此工具。

            使用条件:
            - 未持有该币种的空头仓位
            - 未达到最大持仓数量
            - 技术指标显示看跌信号

            参数:
            - symbol: 交易对，如 'BTC/USDT'

            系统会自动:
            - 使用配置的金额卖空（开空仓）
            - 设置止盈单（价格下跌 5%）
            - 设置止损单（价格上涨 2%）

            注意: 现货账户模式下为模拟做空，仅记录持仓信息

            返回: 执行结果描述
            """,
            func=self.sell_short_callback
        )

    def create_buy_to_cover_tool(self) -> Tool:
        """
        创建买入平空工具

        Returns:
            LangChain Tool 对象
        """
        return Tool(
            name="buy_to_cover",
            description="""
            执行买入平空操作。当已持有该币种的空头仓位且出现平仓信号时使用此工具。

            使用条件:
            - 必须已持有该币种的空头仓位
            - 技术指标显示平仓信号（如价格反弹、止盈止损触发等）

            参数:
            - symbol: 交易对，如 'BTC/USDT'

            系统会:
            - 买入平仓所有该币种的空头仓位
            - 取消相关的止盈止损单

            注意: 现货账户模式下为模拟平仓，移除空头持仓记录

            返回: 执行结果描述
            """,
            func=self.buy_to_cover_callback
        )

    def create_do_nothing_tool(self) -> Tool:
        """
        创建不操作工具

        Returns:
            LangChain Tool 对象
        """
        return Tool(
            name="do_nothing",
            description="""
            不执行任何交易操作。当市场信号不明确或不满足交易条件时使用此工具。

            使用场景:
            - 技术指标相互矛盾
            - 市场处于震荡状态
            - 已达到最大持仓且无卖出信号
            - RSI 在中性区域
            - 成交量不足

            参数:
            - reason: 不操作的原因（必须提供）

            返回: 确认信息
            """,
            func=self.do_nothing_callback
        )

    def create_buy_spot_tool(self) -> Tool:
        """
        创建现货买入工具（用于长期持有）

        Returns:
            LangChain Tool 对象
        """
        if not self.buy_spot_callback:
            # 如果没有提供回调，返回一个空操作工具
            return Tool(
                name="buy_spot",
                description="现货买入功能未启用",
                func=lambda x: "现货买入功能未启用"
            )

        return Tool(
            name="buy_spot",
            description="""
            执行现货买入操作（长期持有策略）。当市场出现长期阴跌趋势，发现优质定投点位时使用此工具。

            使用条件:
            - 检测到市场处于持续阴跌趋势（多周期下跌）
            - 资产价格已从高点回撤显著（建议 20%+）
            - RSI 处于超卖区域（< 30）
            - 多个时间周期趋势一致向下，但出现企稳迹象
            - 成交量开始萎缩或出现底部放量

            定投策略特点:
            - 现货买入，无杠杆风险
            - 用于长期持有，不设置止盈止损
            - 适合优质资产的长期配置
            - 分散风险的定投点位

            参数:
            - symbol: 交易对，如 'BTC', 'ETH'

            系统会自动:
            - 使用配置的金额买入现货
            - 不设置杠杆（1x）
            - 不设置止盈止损（长期持有）
            - 记录为现货持仓

            注意: 这是长期投资工具，请确保:
            1. 市场处于明显的下跌趋势
            2. 价格已有充分回撤
            3. 技术指标显示超卖
            4. 资产基本面良好

            返回: 执行结果描述
            """,
            func=self.buy_spot_callback
        )

    def get_all_tools(self) -> list:
        """
        获取所有工具

        Returns:
            工具列表（包含做多、做空和现货工具）
        """
        tools = [
            self.create_buy_tool(),          # 买入开多
            self.create_sell_tool(),         # 卖出平多
            self.create_sell_short_tool(),   # 卖空开空
            self.create_buy_to_cover_tool(), # 买入平空
            self.create_do_nothing_tool()    # 不操作
        ]

        # 如果提供了现货买入回调，添加现货工具
        if self.buy_spot_callback:
            tools.append(self.create_buy_spot_tool())  # 现货买入

        return tools


def create_mock_callbacks():
    """
    创建模拟回调函数（用于测试）

    Returns:
        (buy_callback, sell_callback, sell_short_callback, buy_to_cover_callback, do_nothing_callback)
    """
    def buy_callback(symbol: str) -> str:
        return f"✅ [模拟] 已执行买入开多操作: {symbol}"

    def sell_callback(symbol: str) -> str:
        return f"✅ [模拟] 已执行卖出平多操作: {symbol}"

    def sell_short_callback(symbol: str) -> str:
        return f"✅ [模拟] 已执行卖空开空操作: {symbol}"

    def buy_to_cover_callback(symbol: str) -> str:
        return f"✅ [模拟] 已执行买入平空操作: {symbol}"

    def do_nothing_callback(reason: str) -> str:
        return f"⏸️  [模拟] 不执行操作。原因: {reason}"

    return buy_callback, sell_callback, sell_short_callback, buy_to_cover_callback, do_nothing_callback


def test_tools():
    """测试工具定义"""
    print("=== 测试交易工具 ===\n")

    # 创建模拟回调
    buy_cb, sell_cb, sell_short_cb, buy_to_cover_cb, nothing_cb = create_mock_callbacks()

    # 创建工具集
    tools = TradingTools(buy_cb, sell_cb, sell_short_cb, buy_to_cover_cb, nothing_cb)
    all_tools = tools.get_all_tools()

    print(f"创建了 {len(all_tools)} 个工具:\n")

    for tool in all_tools:
        print(f"工具名称: {tool.name}")
        print(f"描述: {tool.description[:100]}...")
        print()

    # 测试调用
    print("测试工具调用:\n")

    buy_tool = tools.create_buy_tool()
    result = buy_tool.run("BTC/USDT")
    print(f"1. {result}")

    sell_tool = tools.create_sell_tool()
    result = sell_tool.run("BTC/USDT")
    print(f"2. {result}")

    sell_short_tool = tools.create_sell_short_tool()
    result = sell_short_tool.run("BTC/USDT")
    print(f"3. {result}")

    buy_to_cover_tool = tools.create_buy_to_cover_tool()
    result = buy_to_cover_tool.run("BTC/USDT")
    print(f"4. {result}")

    nothing_tool = tools.create_do_nothing_tool()
    result = nothing_tool.run("市场信号不明确")
    print(f"5. {result}")


if __name__ == "__main__":
    test_tools()
