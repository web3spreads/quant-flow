"""
LangChain 工具定义
为 AI Agent 提供买入、卖出和不操作的工具
"""

import json
from collections.abc import Callable

from langchain_core.tools import StructuredTool, Tool
from pydantic import BaseModel, Field, field_validator


class BuyInput(BaseModel):
    """买入工具的输入模式"""

    symbol: str = Field(description="交易对符号，如 'BTC' 或 'ETH'")
    amount: float | None = Field(default=None, description="交易金额（USD），不填则使用配置上限")
    leverage: int | None = Field(default=None, description="杠杆倍数，不填则使用配置最大杠杆")

    @field_validator("leverage", mode="before")
    @classmethod
    def convert_leverage_to_int(cls, v):
        """将浮点数杠杆转换为整数"""
        if v is None:
            return v
        if isinstance(v, (int, float)):
            return int(v)
        return v


class SellShortInput(BaseModel):
    """卖空工具的输入模式"""

    symbol: str = Field(description="交易对符号，如 'BTC' 或 'ETH'")
    amount: float | None = Field(default=None, description="交易金额（USD），不填则使用配置上限")
    leverage: int | None = Field(default=None, description="杠杆倍数，不填则使用配置最大杠杆")

    @field_validator("leverage", mode="before")
    @classmethod
    def convert_leverage_to_int(cls, v):
        """将浮点数杠杆转换为整数"""
        if v is None:
            return v
        if isinstance(v, (int, float)):
            return int(v)
        return v


class BuySpotInput(BaseModel):
    """现货买入工具的输入模式"""

    symbol: str = Field(description="交易对符号，如 'BTC' 或 'ETH'")
    amount: float | None = Field(default=None, description="定投金额（USD），不填则使用配置上限")


class BuyLimitInput(BaseModel):
    """限价开多工具的输入模式"""

    symbol: str = Field(description="交易对符号，如 'BTC' 或 'ETH'")
    amount: float | None = Field(default=None, description="交易金额（USD），不填则使用配置上限")
    leverage: int | None = Field(default=None, description="杠杆倍数，不填则使用配置最大杠杆")
    price: float = Field(description="限价价格（USD），必须明确指定")

    @field_validator("leverage", mode="before")
    @classmethod
    def convert_leverage_to_int(cls, v):
        """将浮点数杠杆转换为整数"""
        if v is None:
            return v
        if isinstance(v, (int, float)):
            return int(v)
        return v


class SellShortLimitInput(BaseModel):
    """限价开空工具的输入模式"""

    symbol: str = Field(description="交易对符号，如 'BTC' 或 'ETH'")
    amount: float | None = Field(default=None, description="交易金额（USD），不填则使用配置上限")
    leverage: int | None = Field(default=None, description="杠杆倍数，不填则使用配置最大杠杆")
    price: float = Field(description="限价价格（USD），必须明确指定")

    @field_validator("leverage", mode="before")
    @classmethod
    def convert_leverage_to_int(cls, v):
        """将浮点数杠杆转换为整数"""
        if v is None:
            return v
        if isinstance(v, (int, float)):
            return int(v)
        return v


class CancelLimitOrderInput(BaseModel):
    """取消限价单工具的输入模式"""

    symbol: str = Field(description="交易对符号，如 'BTC' 或 'ETH'")
    order_id: int = Field(description="订单ID，必须明确指定")


class TradingTools:
    """交易工具集"""

    def __init__(
        self,
        buy_callback: Callable[[str, float | None, int | None], str],
        sell_callback: Callable[[str], str],
        sell_short_callback: Callable[[str, float | None, int | None], str],
        buy_to_cover_callback: Callable[[str], str],
        do_nothing_callback: Callable[[str], str],
        buy_spot_callback: Callable[[str, float | None], str] | None = None,
        buy_limit_callback: Callable[[str, float | None, int | None, float], str] | None = None,
        sell_short_limit_callback: Callable[[str, float | None, int | None, float], str]
        | None = None,
        cancel_limit_order_callback: Callable[[str, int], str] | None = None,
    ):
        """
        初始化交易工具

        Args:
            buy_callback: 买入开多回调函数，接收 (symbol, amount, leverage)，返回执行结果
            sell_callback: 卖出平多回调函数，接收 symbol，返回执行结果
            sell_short_callback: 卖空开空回调函数，接收 (symbol, amount, leverage)，返回执行结果
            buy_to_cover_callback: 买入平空回调函数，接收 symbol，返回执行结果
            do_nothing_callback: 不操作回调函数，接收 reason，返回确认信息
            buy_spot_callback: 现货买入回调函数（可选），接收 (symbol, amount)，返回执行结果
            buy_limit_callback: 限价开多回调函数（可选），接收 (symbol, amount, leverage, price)，返回执行结果
            sell_short_limit_callback: 限价开空回调函数（可选），接收 (symbol, amount, leverage, price)，返回执行结果
            cancel_limit_order_callback: 取消限价单回调函数（可选），接收 (symbol, order_id)，返回执行结果
        """
        self.buy_callback = buy_callback
        self.sell_callback = sell_callback
        self.sell_short_callback = sell_short_callback
        self.buy_to_cover_callback = buy_to_cover_callback
        self.do_nothing_callback = do_nothing_callback
        self.buy_spot_callback = buy_spot_callback
        self.buy_limit_callback = buy_limit_callback
        self.sell_short_limit_callback = sell_short_limit_callback
        self.cancel_limit_order_callback = cancel_limit_order_callback

    def _parse_tool_input(self, input_str: str) -> dict:
        """
        解析工具输入参数

        支持两种格式：
        1. JSON 格式: '{"symbol": "BTC", "amount": 50, "leverage": 5}'
        2. 简单字符串: 'BTC' (向后兼容)

        Args:
            input_str: 输入字符串

        Returns:
            解析后的参数字典
        """
        # 尝试解析 JSON
        try:
            params = json.loads(input_str)
            if isinstance(params, dict):
                return params
        except (json.JSONDecodeError, TypeError):
            pass

        # 向后兼容：如果不是 JSON，当作 symbol 处理
        return {"symbol": input_str.strip()}

    def create_buy_tool(self) -> StructuredTool:
        """
        创建买入开多工具

        Returns:
            LangChain StructuredTool 对象
        """

        def buy_func(symbol: str, amount: float | None = None, leverage: int | None = None) -> str:
            """执行买入开多操作"""
            return self.buy_callback(symbol, amount, leverage)

        return StructuredTool.from_function(
            func=buy_func,
            name="buy",
            description="""执行买入开多操作。当市场出现明确的做多信号时使用此工具。

使用条件:
- 未持有该币种的多头仓位
- 未达到最大持仓数量
- 技术指标显示看涨信号

你的权限:
- 可以根据信号强度自主决定交易金额（不超过配置上限）
- 可以根据风险承受力选择杠杆倍数（1到配置最大值之间）

系统会自动:
- 设置止盈单（价格上涨 5%）
- 设置止损单（价格下跌 2%）""",
            args_schema=BuyInput,
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
            func=self.sell_callback,
        )

    def create_sell_short_tool(self) -> StructuredTool:
        """
        创建卖空开空工具

        Returns:
            LangChain StructuredTool 对象
        """

        def sell_short_func(
            symbol: str, amount: float | None = None, leverage: int | None = None
        ) -> str:
            """执行卖空开空操作"""
            return self.sell_short_callback(symbol, amount, leverage)

        return StructuredTool.from_function(
            func=sell_short_func,
            name="sell_short",
            description="""执行卖空开空操作。当市场出现明确的做空信号时使用此工具。

使用条件:
- 未持有该币种的空头仓位
- 未达到最大持仓数量
- 技术指标显示看跌信号

你的权限:
- 可以根据信号强度自主决定交易金额（不超过配置上限）
- 可以根据风险承受力选择杠杆倍数（1到配置最大值之间）

系统会自动:
- 设置止盈单（价格下跌 5%）
- 设置止损单（价格上涨 2%）

注意: 现货账户模式下为模拟做空，仅记录持仓信息""",
            args_schema=SellShortInput,
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
            func=self.buy_to_cover_callback,
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
            func=self.do_nothing_callback,
        )

    def create_buy_spot_tool(self) -> StructuredTool:
        """
        创建现货买入工具（用于长期持有）

        Returns:
            LangChain StructuredTool 对象
        """
        if not self.buy_spot_callback:
            # 如果没有提供回调，返回一个空操作工具
            def disabled_func(symbol: str, amount: float | None = None) -> str:
                return "现货买入功能未启用"

            return StructuredTool.from_function(
                func=disabled_func,
                name="buy_spot",
                description="现货买入功能未启用",
                args_schema=BuySpotInput,
            )

        def buy_spot_func(symbol: str, amount: float | None = None) -> str:
            """执行现货买入操作"""
            return self.buy_spot_callback(symbol, amount)

        return StructuredTool.from_function(
            func=buy_spot_func,
            name="buy_spot",
            description="""执行现货买入操作（长期持有策略）。当市场出现长期阴跌趋势，发现优质定投点位时使用此工具。

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

你的权限:
- 可以根据市场恐慌程度自主决定定投金额（不超过配置上限）
- 极度恐慌时可以使用更大金额，一般恐慌时使用较小金额

系统会自动:
- 不设置杠杆（1x）
- 不设置止盈止损（长期持有）
- 记录为现货持仓

注意: 这是长期投资工具，请确保:
1. 市场处于明显的下跌趋势
2. 价格已有充分回撤
3. 技术指标显示超卖
4. 资产基本面良好""",
            args_schema=BuySpotInput,
        )

    def create_buy_limit_tool(self) -> StructuredTool:
        """
        创建限价开多工具

        Returns:
            LangChain StructuredTool 对象
        """
        if not self.buy_limit_callback:

            def disabled_func(
                symbol: str,
                amount: float | None = None,
                leverage: int | None = None,
                price: float = 0.0,
            ) -> str:
                return "限价单功能未启用"

            return StructuredTool.from_function(
                func=disabled_func,
                name="buy_limit",
                description="限价单功能未启用",
                args_schema=BuyLimitInput,
            )

        def buy_limit_func(
            symbol: str,
            amount: float | None = None,
            leverage: int | None = None,
            price: float = 0.0,
        ) -> str:
            """执行限价开多操作"""
            return self.buy_limit_callback(symbol, amount, leverage, price)

        return StructuredTool.from_function(
            func=buy_limit_func,
            name="buy_limit",
            description="""执行限价开多操作。当市场出现做多信号但希望以更好的价格成交时使用此工具。

使用条件:
- 未持有该币种的多头仓位（或允许加仓）
- 未达到最大持仓数量
- 技术指标显示看涨信号
- 希望以低于当前价格的价格买入（提前埋伏）

你的权限:
- 可以根据信号强度自主决定交易金额（不超过配置上限）
- 可以根据风险承受力选择杠杆倍数（1到配置最大值之间）
- 必须明确指定限价价格

系统会自动:
- 在限价单成交后设置止盈单（价格上涨 5%）
- 在限价单成交后设置止损单（价格下跌 2%）

注意:
- 限价单可能不会立即成交，需要等待价格回调到限价
- 如果价格已远离限价，成交可能性较低，可考虑取消限价单""",
            args_schema=BuyLimitInput,
        )

    def create_sell_short_limit_tool(self) -> StructuredTool:
        """
        创建限价开空工具

        Returns:
            LangChain StructuredTool 对象
        """
        if not self.sell_short_limit_callback:

            def disabled_func(
                symbol: str,
                amount: float | None = None,
                leverage: int | None = None,
                price: float = 0.0,
            ) -> str:
                return "限价单功能未启用"

            return StructuredTool.from_function(
                func=disabled_func,
                name="sell_short_limit",
                description="限价单功能未启用",
                args_schema=SellShortLimitInput,
            )

        def sell_short_limit_func(
            symbol: str,
            amount: float | None = None,
            leverage: int | None = None,
            price: float = 0.0,
        ) -> str:
            """执行限价开空操作"""
            return self.sell_short_limit_callback(symbol, amount, leverage, price)

        return StructuredTool.from_function(
            func=sell_short_limit_func,
            name="sell_short_limit",
            description="""执行限价开空操作。当市场出现做空信号但希望以更好的价格成交时使用此工具。

使用条件:
- 未持有该币种的空头仓位（或允许加仓）
- 未达到最大持仓数量
- 技术指标显示看跌信号
- 希望以高于当前价格的价格卖出（提前埋伏）

你的权限:
- 可以根据信号强度自主决定交易金额（不超过配置上限）
- 可以根据风险承受力选择杠杆倍数（1到配置最大值之间）
- 必须明确指定限价价格

系统会自动:
- 在限价单成交后设置止盈单（价格下跌 5%）
- 在限价单成交后设置止损单（价格上涨 2%）

注意:
- 限价单可能不会立即成交，需要等待价格回调到限价
- 如果价格已远离限价，成交可能性较低，可考虑取消限价单""",
            args_schema=SellShortLimitInput,
        )

    def create_cancel_limit_order_tool(self) -> StructuredTool:
        """
        创建取消限价单工具

        Returns:
            LangChain StructuredTool 对象
        """
        if not self.cancel_limit_order_callback:

            def disabled_func(symbol: str, order_id: int = 0) -> str:
                return "限价单功能未启用"

            return StructuredTool.from_function(
                func=disabled_func,
                name="cancel_limit_order",
                description="限价单功能未启用",
                args_schema=CancelLimitOrderInput,
            )

        def cancel_limit_order_func(symbol: str, order_id: int = 0) -> str:
            """执行取消限价单操作"""
            return self.cancel_limit_order_callback(symbol, order_id)

        return StructuredTool.from_function(
            func=cancel_limit_order_func,
            name="cancel_limit_order",
            description="""取消待处理的限价单。当市场条件变化，限价单不再合理时使用此工具。

使用场景:
- 市场趋势已改变，限价单价格不再合理
- 价格已远离限价单价格，成交可能性低
- 需要释放资金用于其他交易机会
- 限价单挂单时间过长，市场条件已变化

参数:
- symbol: 交易对符号
- order_id: 订单ID（从待处理限价单列表中获取）

返回: 取消结果描述""",
            args_schema=CancelLimitOrderInput,
        )

    def get_all_tools(self) -> list:
        """
        获取所有工具

        Returns:
            工具列表（包含做多、做空和现货工具）
        """
        tools = [
            self.create_buy_tool(),  # 买入开多
            self.create_sell_tool(),  # 卖出平多
            self.create_sell_short_tool(),  # 卖空开空
            self.create_buy_to_cover_tool(),  # 买入平空
            self.create_do_nothing_tool(),  # 不操作
        ]

        # 如果提供了现货买入回调，添加现货工具
        if self.buy_spot_callback:
            tools.append(self.create_buy_spot_tool())  # 现货买入

        # 如果提供了限价单回调，添加限价单工具
        if self.buy_limit_callback:
            tools.append(self.create_buy_limit_tool())  # 限价开多
        if self.sell_short_limit_callback:
            tools.append(self.create_sell_short_limit_tool())  # 限价开空
        if self.cancel_limit_order_callback:
            tools.append(self.create_cancel_limit_order_tool())  # 取消限价单

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

    return (
        buy_callback,
        sell_callback,
        sell_short_callback,
        buy_to_cover_callback,
        do_nothing_callback,
    )


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
