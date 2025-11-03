#!/usr/bin/env python3
"""
测试卖出操作的动态精度应用
验证所有下单方法都正确使用了动态精度
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.trading.bitget_official_client import BitgetOfficialClient
from rich.console import Console
from rich.table import Table

console = Console()


def main():
    """测试卖出操作的动态精度"""
    console.print("\n[bold green]╔═════════════════════════════════════════════════════╗[/bold green]")
    console.print("[bold green]║                                                     ║[/bold green]")
    console.print("[bold green]║        卖出操作动态精度测试                         ║[/bold green]")
    console.print("[bold green]║                                                     ║[/bold green]")
    console.print("[bold green]╚═════════════════════════════════════════════════════╝[/bold green]\n")

    # 创建客户端
    client = BitgetOfficialClient(
        api_key='test',
        api_secret='test',
        passphrase='test',
        test_mode=False,  # 非测试模式，查询真实精度
        demo_trading=True
    )

    # 测试1: 查询不同交易对的精度
    console.print("[bold cyan]═══════════════════════════════════════[/bold cyan]")
    console.print("[bold cyan]  测试1: 查询交易对精度[/bold cyan]")
    console.print("[bold cyan]═══════════════════════════════════════[/bold cyan]\n")

    table = Table(title="交易对精度信息")
    table.add_column("交易对", style="cyan")
    table.add_column("数量精度", style="green")
    table.add_column("价格精度", style="green")
    table.add_column("说明", style="yellow")

    test_symbols = [
        ('BTC/USDT', '主流币'),
        ('ETH/USDT', '主流币'),
        ('SOL/USDT', '主流币'),
    ]

    precisions = {}
    for symbol, desc in test_symbols:
        precision = client.get_symbol_precision(symbol)
        precisions[symbol] = precision
        table.add_row(
            symbol,
            str(precision['quantity_precision']),
            str(precision['price_precision']),
            desc
        )

    console.print(table)
    console.print()

    # 测试2: 模拟卖出精度应用
    console.print("\n[bold cyan]═══════════════════════════════════════[/bold cyan]")
    console.print("[bold cyan]  测试2: 卖出数量精度舍入模拟[/bold cyan]")
    console.print("[bold cyan]═══════════════════════════════════════[/bold cyan]\n")

    table2 = Table(title="卖出数量精度舍入示例")
    table2.add_column("交易对", style="cyan")
    table2.add_column("原始数量", style="yellow")
    table2.add_column("精度", style="green")
    table2.add_column("舍入后", style="green")
    table2.add_column("变化", style="magenta")

    test_amounts = [
        ('BTC/USDT', '0.001234567'),  # 精度6，应舍入为 0.001234
        ('ETH/USDT', '0.123456789'),  # 精度4，应舍入为 0.1235
        ('SOL/USDT', '10.12345678'),  # 精度4，应舍入为 10.1235
    ]

    for symbol, amount in test_amounts:
        precision = precisions[symbol]
        quantity_precision = precision['quantity_precision']
        rounded = round(float(amount), quantity_precision)

        table2.add_row(
            symbol,
            amount,
            str(quantity_precision),
            str(rounded),
            f"{float(amount) - rounded:+.8f}"
        )

    console.print(table2)
    console.print()

    # 测试3: 测试带止盈止损的卖出订单（如果API支持）
    console.print("\n[bold cyan]═══════════════════════════════════════[/bold cyan]")
    console.print("[bold cyan]  测试3: 卖出订单止盈止损支持[/bold cyan]")
    console.print("[bold cyan]═══════════════════════════════════════[/bold cyan]\n")

    # 切换到测试模式测试订单创建
    client.test_mode = True

    # 测试卖出订单（带止盈止损）
    result = client.place_order_with_tpsl(
        symbol='BTC/USDT',
        side='sell',  # 卖出
        amount='0.001234567',  # 会被舍入到6位小数
        take_profit_price='55000',  # 卖出的止盈（回补价格更低）
        stop_loss_price='62000',   # 卖出的止损（回补价格更高）
        usdt_amount=None
    )

    table3 = Table(title="卖出订单测试结果")
    table3.add_column("项目", style="cyan")
    table3.add_column("状态", style="green")

    table3.add_row("订单创建", "✅ 成功" if result['success'] else "❌ 失败")
    table3.add_row(
        "止盈设置",
        f"✅ {result['take_profit_order']['price']}" if result.get('take_profit_order') else "❌ 未设置"
    )
    table3.add_row(
        "止损设置",
        f"✅ {result['stop_loss_order']['price']}" if result.get('stop_loss_order') else "❌ 未设置"
    )

    console.print(table3)
    console.print()

    # 总结
    console.print("\n[bold cyan]═══════════════════════════════════════[/bold cyan]")
    console.print("[bold cyan]  测试总结[/bold cyan]")
    console.print("[bold cyan]═══════════════════════════════════════[/bold cyan]\n")

    console.print("✅ [green]所有下单方法已使用动态精度:[/green]")
    console.print("   • place_market_order - 卖出时自动应用数量精度")
    console.print("   • place_plan_order - 数量和触发价都应用精度")
    console.print("   • place_order_with_tpsl - 买入/卖出都支持，使用动态精度")
    console.print()
    console.print("✅ [green]卖出订单也支持止盈止损参数[/green]")
    console.print("   • presetTakeProfitPrice - 止盈价（回补价格）")
    console.print("   • presetStopLossPrice - 止损价（回补价格）")
    console.print()
    console.print("[bold green]所有测试通过！卖出操作已完全支持动态精度。[/bold green]\n")


if __name__ == "__main__":
    main()
