"""
交易工具定义

为 AI Agent 提供买入、卖出和不操作的工具。
这些工具可被多个 Agent 共享使用。
"""

from typing import Callable, Optional, List
from pydantic import BaseModel, Field, field_validator
from langchain_core.tools import Tool, StructuredTool


class BuyInput(BaseModel):
    """买入工具的输入模式"""
    symbol: str = Field(description="交易对符号，如 'BTC' 或 'ETH'")
    amount: Optional[float] = Field(
        default=None,
        description="交易金额（USD），不填则使用配置上限"
    )
    leverage: Optional[int] = Field(
        default=None,
        description="杠杆倍数，不填则使用配置最大杠杆"
    )

    @field_validator('leverage', mode='before')
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
    amount: Optional[float] = Field(
        default=None,
        description="交易金额（USD），不填则使用配置上限"
    )
    leverage: Optional[int] = Field(
        default=None,
        description="杠杆倍数，不填则使用配置最大杠杆"
    )

    @field_validator('leverage', mode='before')
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
    amount: Optional[float] = Field(
        default=None,
        description="定投金额（USD），不填则使用配置上限"
    )


# 工具描述常量
BUY_TOOL_DESCRIPTION = """执行买入开多操作。当市场出现明确的做多信号时使用此工具。

使用条件:
- 未持有该币种的多头仓位
- 未达到最大持仓数量
- 技术指标显示看涨信号

你的权限:
- 可以根据信号强度自主决定交易金额（不超过配置上限）
- 可以根据风险承受力选择杠杆倍数（1到配置最大值之间）

系统会自动:
- 设置止盈单（价格上涨 5%）
- 设置止损单（价格下跌 2%）"""

SELL_TOOL_DESCRIPTION = """执行卖出平多操作。当已持有该币种的多头仓位且出现卖出信号时使用此工具。

使用条件:
- 必须已持有该币种的多头仓位
- 技术指标显示卖出信号

参数:
- symbol: 交易对，如 'BTC/USDT'

系统会:
- 卖出所有持有的该币种（平多仓）
- 取消相关的止盈止损单

返回: 执行结果描述"""

SELL_SHORT_TOOL_DESCRIPTION = """执行卖空开空操作。当市场出现明确的做空信号时使用此工具。

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

注意: 现货账户模式下为模拟做空，仅记录持仓信息"""

BUY_TO_COVER_TOOL_DESCRIPTION = """执行买入平空操作。当已持有该币种的空头仓位且出现平仓信号时使用此工具。

使用条件:
- 必须已持有该币种的空头仓位
- 技术指标显示平仓信号（如价格反弹、止盈止损触发等）

参数:
- symbol: 交易对，如 'BTC/USDT'

系统会:
- 买入平仓所有该币种的空头仓位
- 取消相关的止盈止损单

注意: 现货账户模式下为模拟平仓，移除空头持仓记录

返回: 执行结果描述"""

DO_NOTHING_TOOL_DESCRIPTION = """不执行任何交易操作。当市场信号不明确或不满足交易条件时使用此工具。

使用场景:
- 技术指标相互矛盾
- 市场处于震荡状态
- 已达到最大持仓且无卖出信号
- RSI 在中性区域
- 成交量不足

参数:
- reason: 不操作的原因（必须提供）

返回: 确认信息"""

BUY_SPOT_TOOL_DESCRIPTION = """执行现货买入操作（长期持有策略）。当市场出现长期阴跌趋势，发现优质定投点位时使用此工具。

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
4. 资产基本面良好"""


class TradingToolFactory:
    """
    交易工具工厂

    创建标准化的交易工具集，供 Agent 使用。
    """

    def __init__(
        self,
        buy_callback: Callable[[str, Optional[float], Optional[int]], str],
        sell_callback: Callable[[str], str],
        sell_short_callback: Callable[[str, Optional[float], Optional[int]], str],
        buy_to_cover_callback: Callable[[str], str],
        do_nothing_callback: Callable[[str], str],
        buy_spot_callback: Optional[Callable[[str, Optional[float]], str]] = None
    ):
        """
        初始化工厂

        Args:
            buy_callback: 买入开多回调函数，接收 (symbol, amount, leverage)
            sell_callback: 卖出平多回调函数，接收 symbol
            sell_short_callback: 卖空开空回调函数，接收 (symbol, amount, leverage)
            buy_to_cover_callback: 买入平空回调函数，接收 symbol
            do_nothing_callback: 不操作回调函数，接收 reason
            buy_spot_callback: 现货买入回调函数（可选），接收 (symbol, amount)
        """
        self.buy_callback = buy_callback
        self.sell_callback = sell_callback
        self.sell_short_callback = sell_short_callback
        self.buy_to_cover_callback = buy_to_cover_callback
        self.do_nothing_callback = do_nothing_callback
        self.buy_spot_callback = buy_spot_callback

    def create_buy_tool(self) -> StructuredTool:
        """创建买入开多工具"""
        def buy_func(
            symbol: str,
            amount: Optional[float] = None,
            leverage: Optional[int] = None
        ) -> str:
            """执行买入开多操作"""
            return self.buy_callback(symbol, amount, leverage)

        return StructuredTool.from_function(
            func=buy_func,
            name="buy",
            description=BUY_TOOL_DESCRIPTION,
            args_schema=BuyInput
        )

    def create_sell_tool(self) -> Tool:
        """创建卖出平多工具"""
        return Tool(
            name="sell",
            description=SELL_TOOL_DESCRIPTION,
            func=self.sell_callback
        )

    def create_sell_short_tool(self) -> StructuredTool:
        """创建卖空开空工具"""
        def sell_short_func(
            symbol: str,
            amount: Optional[float] = None,
            leverage: Optional[int] = None
        ) -> str:
            """执行卖空开空操作"""
            return self.sell_short_callback(symbol, amount, leverage)

        return StructuredTool.from_function(
            func=sell_short_func,
            name="sell_short",
            description=SELL_SHORT_TOOL_DESCRIPTION,
            args_schema=SellShortInput
        )

    def create_buy_to_cover_tool(self) -> Tool:
        """创建买入平空工具"""
        return Tool(
            name="buy_to_cover",
            description=BUY_TO_COVER_TOOL_DESCRIPTION,
            func=self.buy_to_cover_callback
        )

    def create_do_nothing_tool(self) -> Tool:
        """创建不操作工具"""
        return Tool(
            name="do_nothing",
            description=DO_NOTHING_TOOL_DESCRIPTION,
            func=self.do_nothing_callback
        )

    def create_buy_spot_tool(self) -> StructuredTool:
        """创建现货买入工具"""
        if not self.buy_spot_callback:
            def disabled_func(symbol: str, amount: Optional[float] = None) -> str:
                return "现货买入功能未启用"

            return StructuredTool.from_function(
                func=disabled_func,
                name="buy_spot",
                description="现货买入功能未启用",
                args_schema=BuySpotInput
            )

        def buy_spot_func(symbol: str, amount: Optional[float] = None) -> str:
            """执行现货买入操作"""
            return self.buy_spot_callback(symbol, amount)

        return StructuredTool.from_function(
            func=buy_spot_func,
            name="buy_spot",
            description=BUY_SPOT_TOOL_DESCRIPTION,
            args_schema=BuySpotInput
        )

    def get_all_tools(self, include_spot: bool = True) -> List:
        """
        获取所有工具

        Args:
            include_spot: 是否包含现货工具

        Returns:
            工具列表
        """
        tools = [
            self.create_buy_tool(),
            self.create_sell_tool(),
            self.create_sell_short_tool(),
            self.create_buy_to_cover_tool(),
            self.create_do_nothing_tool(),
        ]

        if include_spot and self.buy_spot_callback:
            tools.append(self.create_buy_spot_tool())

        return tools

    def get_callbacks_dict(self) -> dict:
        """
        获取回调函数字典

        返回所有工具回调函数的字典映射，
        用于 ExecutionAgent 执行计划。

        Returns:
            工具回调函数字典
        """
        callbacks = {
            'buy': self.buy_callback,
            'sell': self.sell_callback,
            'sell_short': self.sell_short_callback,
            'buy_to_cover': self.buy_to_cover_callback,
            'do_nothing': self.do_nothing_callback,
        }
        if self.buy_spot_callback:
            callbacks['buy_spot'] = self.buy_spot_callback
        return callbacks


def create_mock_callbacks():
    """
    创建模拟回调函数（用于测试）

    Returns:
        (buy_callback, sell_callback, sell_short_callback,
         buy_to_cover_callback, do_nothing_callback, buy_spot_callback)
    """
    def buy_callback(
        symbol: str,
        amount: Optional[float] = None,
        leverage: Optional[int] = None
    ) -> str:
        amount_str = f"${amount}" if amount else "默认金额"
        leverage_str = f"{leverage}x" if leverage else "默认杠杆"
        return f"✅ [模拟] 已执行买入开多: {symbol} ({amount_str}, {leverage_str})"

    def sell_callback(symbol: str) -> str:
        return f"✅ [模拟] 已执行卖出平多: {symbol}"

    def sell_short_callback(
        symbol: str,
        amount: Optional[float] = None,
        leverage: Optional[int] = None
    ) -> str:
        amount_str = f"${amount}" if amount else "默认金额"
        leverage_str = f"{leverage}x" if leverage else "默认杠杆"
        return f"✅ [模拟] 已执行卖空开空: {symbol} ({amount_str}, {leverage_str})"

    def buy_to_cover_callback(symbol: str) -> str:
        return f"✅ [模拟] 已执行买入平空: {symbol}"

    def do_nothing_callback(reason: str) -> str:
        return f"⏸️ [模拟] 不执行操作。原因: {reason}"

    def buy_spot_callback(symbol: str, amount: Optional[float] = None) -> str:
        amount_str = f"${amount}" if amount else "默认金额"
        return f"✅ [模拟] 已执行现货买入: {symbol} ({amount_str})"

    return (
        buy_callback,
        sell_callback,
        sell_short_callback,
        buy_to_cover_callback,
        do_nothing_callback,
        buy_spot_callback,
    )
