#!/usr/bin/env python3
"""
Bitget 交易功能测试脚本

测试分为两部分：
1. 现货交易：买多、卖出（平多）
2. 合约交易：做多、做空、平多、平空（支持杠杆）

使用方法：
1. 在 .env 中配置你的 API 密钥
2. 运行: python tests/test_trading_functions.py
3. 按照提示选择要测试的功能
"""

import sys
import os
from datetime import datetime
from dotenv import load_dotenv
from src.trading.bitget_client import BitgetClient
from src.trading.bitget_contract_client import BitgetContractClient
from src.trading.order_manager import OrderManager
from src.data.market_data import MarketDataFetcher
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, Confirm

console = Console()


def load_env_config():
    """从 .env 文件加载配置"""
    load_dotenv()

    class Config:
        def __init__(self):
            self.bitget_api_key = os.getenv('BITGET_API_KEY', '')
            self.bitget_api_secret = os.getenv('BITGET_API_SECRET', '')
            self.bitget_passphrase = os.getenv('BITGET_PASSPHRASE', '')
            self.demo_trading = os.getenv('DEMO_TRADING', 'true').lower() == 'true'
            self.trade_amount = float(os.getenv('TRADE_AMOUNT', '100.0'))
            self.take_profit_ratio = float(os.getenv('TAKE_PROFIT_RATIO', '0.05'))
            self.stop_loss_ratio = float(os.getenv('STOP_LOSS_RATIO', '0.02'))

    return Config()


def print_header(title: str):
    """打印标题"""
    console.print()
    console.print(Panel.fit(f"[bold cyan]{title}[/bold cyan]", border_style="cyan"))
    console.print()


def print_success(message: str):
    console.print(f"[green]✅ {message}[/green]")


def print_error(message: str):
    console.print(f"[red]❌ {message}[/red]")


def print_info(message: str):
    console.print(f"[blue]ℹ️  {message}[/blue]")


def print_warning(message: str):
    console.print(f"[yellow]⚠️  {message}[/yellow]")


def normalize_symbol(symbol: str) -> str:
    """
    标准化交易对格式
    
    将 'BTCUSDT' 转换为 'BTC/USDT'
    如果已经是 'BTC/USDT' 格式，则不变
    
    Args:
        symbol: 交易对字符串
        
    Returns:
        标准化后的交易对（带斜杠）
    """
    # 如果已经包含斜杠，直接返回
    if '/' in symbol:
        return symbol.upper()
    
    # 处理 BTCUSDT 格式
    symbol = symbol.upper()
    
    # 常见的交易对列表
    base_currencies = ['BTC', 'ETH', 'BNB', 'XRP', 'ADA', 'SOL', 'DOGE', 'DOT', 'MATIC', 'AVAX', 'LINK', 'UNI', 'LTC']
    
    # 尝试匹配基础货币
    for base in base_currencies:
        if symbol.startswith(base) and symbol.endswith('USDT'):
            return f"{base}/USDT"
    
    # 如果没有匹配到，尝试按照 USDT 分割
    if symbol.endswith('USDT'):
        base = symbol[:-4]  # 移除 'USDT'
        return f"{base}/USDT"
    
    # 默认返回原值（可能已经是正确格式）
    return symbol


# ==================== 现货交易测试 ====================

def test_spot_account_info(manager: OrderManager):
    """测试现货账户信息查询"""
    print_header("📊 现货账户信息查询")

    try:
        print_info("1. 查询 USDT 余额...")
        usdt_balance = manager.get_balance('USDT')
        if usdt_balance is not None:
            print_success(f"USDT 余额: {usdt_balance:.2f}")
        else:
            print_error("无法获取 USDT 余额")
            return False

        print_info("2. 查询详细余额信息...")
        balance_info = manager.get_available_balance_info('USDT')
        if balance_info['status'] == 'ok':
            table = Table(title="现货余额详情")
            table.add_column("项目", style="cyan")
            table.add_column("金额 (USDT)", style="green")

            table.add_row("总余额", f"{balance_info['total']:.2f}")
            table.add_row("占用资金", f"{balance_info['occupied']:.2f}")
            table.add_row("可用余额", f"{balance_info['available']:.2f}")

            console.print(table)
            print_success("现货账户信息查询完成")
            return True
        else:
            print_error(balance_info['message'])
            return False

    except Exception as e:
        print_error(f"账户信息查询失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_spot_buy(manager: OrderManager, market_fetcher: MarketDataFetcher, symbol: str = "BTC/USDT", amount: float = 20.0):
    """测试现货买入（开多）"""
    # 标准化交易对格式
    symbol = normalize_symbol(symbol)
    print_header(f"📈 现货买入（开多）- {symbol}")

    try:
        print_info(f"1. 检查余额是否足够 {amount} USDT...")
        has_balance = manager.check_sufficient_balance(amount, 'USDT')
        if not has_balance:
            print_error(f"余额不足，需要至少 {amount} USDT")
            return False
        print_success(f"余额充足")

        print_info(f"2. 获取 {symbol} 当前价格...")
        current_price = market_fetcher.fetch_current_price(symbol)
        if current_price is None:
            print_error(f"无法获取 {symbol} 的当前价格")
            return False
        print_info(f"当前价格: ${current_price:.2f}")

        console.print()
        console.print(Panel(
            f"[bold]现货买入信息[/bold]\n"
            f"交易对: {symbol}\n"
            f"买入金额: {amount} USDT\n"
            f"当前价格: ${current_price:.2f}\n"
            f"预计数量: {amount/current_price:.6f}\n"
            f"止盈: +{manager.take_profit_ratio*100}% (${current_price*(1+manager.take_profit_ratio):.2f})\n"
            f"止损: -{manager.stop_loss_ratio*100}% (${current_price*(1-manager.stop_loss_ratio):.2f})",
            title="确认现货买入",
            border_style="green"
        ))

        if not Confirm.ask("是否确认买入？"):
            print_warning("取消买入")
            return False

        print_info("3. 执行现货买入...")
        order_info = manager.execute_buy_with_protection(
            symbol=symbol,
            usdt_amount=amount,
            current_price=current_price
        )

        if order_info:
            table = Table(title="现货买入成功")
            table.add_column("项目", style="cyan")
            table.add_column("信息", style="green")

            table.add_row("交易对", symbol)
            table.add_row("入场价", f"${order_info['entry_price']:.2f}")
            table.add_row("买入数量", f"{order_info['amount']:.6f}")
            table.add_row("止盈价", f"${order_info['take_profit_price']:.2f}")
            table.add_row("止损价", f"${order_info['stop_loss_price']:.2f}")

            console.print(table)
            print_success("现货买入完成")
            return True
        else:
            print_error("买入失败")
            return False

    except Exception as e:
        print_error(f"现货买入失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def query_open_orders(manager: OrderManager, symbol: str) -> list:
    """查询指定交易对的挂单"""
    print_header(f"📋 查询挂单 - {symbol}")
    
    try:
        print_info("正在查询挂单...")
        open_orders = manager.client.get_open_orders(symbol)
        
        if not open_orders:
            print_info(f"{symbol} 没有挂单")
            return []
        
        # 显示挂单列表
        table = Table(title=f"{symbol} 挂单列表")
        table.add_column("序号", style="cyan", width=6)
        table.add_column("订单ID", style="yellow", width=20)
        table.add_column("类型", style="green", width=10)
        table.add_column("方向", style="blue", width=8)
        table.add_column("数量", style="magenta", width=15)
        table.add_column("价格", style="white", width=15)
        table.add_column("状态", style="cyan", width=10)
        
        for idx, order in enumerate(open_orders, 1):
            order_id = str(order.get('orderId', 'N/A'))
            order_type = order.get('orderType', 'N/A')
            side = order.get('side', 'N/A')
            size = order.get('size', '0')
            price = order.get('price', order.get('triggerPrice', 'N/A'))
            status = order.get('status', 'N/A')
            
            table.add_row(
                str(idx),
                order_id[:20],
                order_type,
                side,
                size,
                str(price),
                status
            )
        
        console.print(table)
        print_success(f"找到 {len(open_orders)} 个挂单")
        return open_orders
        
    except Exception as e:
        print_error(f"查询挂单失败: {e}")
        import traceback
        traceback.print_exc()
        return []


def cancel_orders(manager: OrderManager, symbol: str, orders: list) -> int:
    """撤销指定的挂单"""
    print_header(f"🗑️  撤销挂单 - {symbol}")
    
    if not orders:
        print_warning("没有可撤销的挂单")
        return 0
    
    # 让用户选择撤销方式
    console.print()
    console.print("[bold]撤销选项:[/bold]")
    console.print("  [cyan]1[/cyan] - 撤销所有挂单")
    console.print("  [cyan]2[/cyan] - 选择性撤销")
    console.print("  [cyan]0[/cyan] - 取消操作")
    console.print()
    
    choice = Prompt.ask("请选择", choices=["0", "1", "2"], default="1")
    
    if choice == "0":
        print_info("取消撤销操作")
        return 0
    
    orders_to_cancel = []
    
    if choice == "1":
        # 撤销所有
        orders_to_cancel = orders
    else:
        # 选择性撤销
        console.print()
        console.print("[yellow]请输入要撤销的订单序号，多个订单用逗号分隔（如: 1,2,3）[/yellow]")
        indices_str = Prompt.ask("订单序号")
        
        try:
            indices = [int(i.strip()) for i in indices_str.split(',')]
            orders_to_cancel = [orders[i-1] for i in indices if 0 < i <= len(orders)]
        except:
            print_error("输入格式错误")
            return 0
    
    if not orders_to_cancel:
        print_warning("没有选择要撤销的订单")
        return 0
    
    # 确认撤销
    console.print()
    if not Confirm.ask(f"确认撤销 {len(orders_to_cancel)} 个挂单？"):
        print_info("取消撤销操作")
        return 0
    
    # 执行撤销
    cancelled_count = 0
    for order in orders_to_cancel:
        order_id = str(order.get('orderId'))
        
        # 判断是普通订单还是计划单
        # 计划单有 planType 字段，或者有 triggerPrice 字段
        is_plan_order = 'planType' in order or 'triggerPrice' in order
        
        try:
            if is_plan_order:
                # 计划单（止盈止损）
                success = manager.client.cancel_plan_order(order_id, symbol)
            else:
                # 普通订单
                success = manager.client.cancel_order(order_id, symbol)
            
            if success:
                order_type = "计划单" if is_plan_order else "普通订单"
                print_success(f"已撤销{order_type}: {order_id[:20]}")
                cancelled_count += 1
            else:
                print_error(f"撤销失败: {order_id[:20]}")
        except Exception as e:
            print_error(f"撤销订单 {order_id[:20]} 失败: {e}")
    
    console.print()
    print_success(f"成功撤销 {cancelled_count}/{len(orders_to_cancel)} 个挂单")
    return cancelled_count


def test_spot_sell(manager: OrderManager, symbol: str = "BTC/USDT"):
    """测试现货卖出（平多）"""
    # 标准化交易对格式
    symbol = normalize_symbol(symbol)
    print_header(f"📉 现货卖出（平多）- {symbol}")

    try:
        print_info(f"1. 检查是否持有 {symbol}...")
        positions = manager.get_current_positions()
        position = next((p for p in positions if p['symbol'] == symbol and p.get('side', 'long') == 'long'), None)

        if not position:
            print_warning(f"未持有 {symbol} 的多头仓位，无法卖出")
            return False

        # 检查是否有冻结余额
        has_frozen = position.get('frozen', 0) > 0
        available = position.get('available', position['amount'])
        frozen = position.get('frozen', 0)
        total = position.get('total', position['amount'])
        
        if has_frozen:
            print_warning(f"检测到冻结余额!")
            console.print()
            
            table = Table(title="余额详情")
            table.add_column("项目", style="cyan")
            table.add_column("数量", style="yellow")
            
            table.add_row("可用余额", f"{available:.8f}")
            table.add_row("冻结余额", f"[red]{frozen:.8f}[/red]")
            table.add_row("总余额", f"{total:.8f}")
            
            console.print(table)
            console.print()
            console.print("[yellow]💡 冻结余额通常是挂单（止盈止损单）占用的[/yellow]")
            console.print()
            
            # 询问是否查询和撤销挂单
            if Confirm.ask("是否查询并撤销挂单以释放冻结余额？", default=True):
                # 查询挂单
                open_orders = query_open_orders(manager, symbol)
                
                if open_orders:
                    # 撤销挂单
                    cancelled = cancel_orders(manager, symbol, open_orders)
                    
                    if cancelled > 0:
                        print_info("挂单已撤销，请稍等片刻让余额更新...")
                        import time
                        time.sleep(2)
                        
                        # 重新查询持仓
                        print_info("重新查询持仓...")
                        positions = manager.get_current_positions()
                        position = next((p for p in positions if p['symbol'] == symbol and p.get('side', 'long') == 'long'), None)
                        
                        if position:
                            available = position.get('available', position['amount'])
                            frozen = position.get('frozen', 0)
                            print_success(f"更新后 - 可用: {available:.8f}, 冻结: {frozen:.8f}")
                        else:
                            print_error("重新查询持仓失败")
                            return False
                    else:
                        print_warning("未能撤销挂单，继续使用当前可用余额")
                else:
                    print_info("没有找到挂单，可能已自动更新")
        
        print_success(f"持有 {symbol}: {position['amount']:.6f}")

        console.print()
        console.print(Panel(
            f"[bold]现货卖出信息[/bold]\n"
            f"交易对: {symbol}\n"
            f"持有数量: {position['amount']:.6f}\n"
            f"操作: 平多仓",
            title="确认现货卖出",
            border_style="yellow"
        ))

        if not Confirm.ask("是否确认卖出？"):
            print_warning("取消卖出")
            return False

        print_info("2. 执行现货卖出...")
        sell_order = manager.execute_sell(
            symbol=symbol,
            amount=position['amount']
        )

        if sell_order:
            table = Table(title="现货卖出成功")
            table.add_column("项目", style="cyan")
            table.add_column("信息", style="green")

            table.add_row("交易对", symbol)
            table.add_row("卖出数量", f"{position['amount']:.6f}")
            table.add_row("订单ID", str(sell_order.get('orderId', 'N/A')))

            console.print(table)
            print_success("现货卖出完成")
            return True
        else:
            print_error("卖出失败")
            return False

    except Exception as e:
        print_error(f"现货卖出失败: {e}")
        import traceback
        traceback.print_exc()
        return False


# ==================== 合约交易测试 ====================

def test_contract_account_info(contract_client: BitgetContractClient):
    """测试合约账户信息查询"""
    print_header("📊 合约账户信息查询")

    try:
        print_info("查询合约账户 USDT 余额...")
        balance = contract_client.get_balance('USDT')
        if balance is not None:
            print_success(f"合约账户 USDT 余额: {balance:.2f}")
            return True
        else:
            print_error("无法获取合约账户余额")
            return False

    except Exception as e:
        print_error(f"合约账户信息查询失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_contract_long(contract_client: BitgetContractClient, market_fetcher: MarketDataFetcher,
                       symbol: str = "BTC/USDT", amount: float = 20.0, leverage: int = 10):
    """测试合约做多（开多）"""
    # 标准化交易对格式
    symbol = normalize_symbol(symbol)
    print_header(f"📈 合约做多（开多）- {symbol} - {leverage}x 杠杆")

    try:
        print_info(f"1. 检查合约账户余额...")
        balance = contract_client.get_balance('USDT')
        if balance is None or balance < amount / leverage:
            print_error(f"保证金不足，需要至少 {amount/leverage:.2f} USDT")
            return False
        print_success(f"保证金充足")

        print_info(f"2. 获取 {symbol} 当前价格...")
        current_price = market_fetcher.fetch_current_price(symbol)
        if current_price is None:
            print_error(f"无法获取 {symbol} 的当前价格")
            return False
        print_info(f"当前价格: ${current_price:.2f}")

        # 计算数量
        quantity = amount / current_price
        tp_price = current_price * 1.05
        sl_price = current_price * 0.98

        console.print()
        console.print(Panel(
            f"[bold]合约做多信息[/bold]\n"
            f"交易对: {symbol}\n"
            f"名义价值: {amount} USDT\n"
            f"杠杆: {leverage}x\n"
            f"保证金: {amount/leverage:.2f} USDT\n"
            f"当前价格: ${current_price:.2f}\n"
            f"数量: {quantity:.6f}\n"
            f"止盈价: ${tp_price:.2f} (+5%)\n"
            f"止损价: ${sl_price:.2f} (-2%)",
            title="确认合约做多",
            border_style="green"
        ))

        if not Confirm.ask("是否确认做多？"):
            print_warning("取消做多")
            return False

        print_info("3. 执行合约做多...")
        order = contract_client.open_long(
            symbol=symbol,
            size=str(quantity),
            leverage=leverage,
            take_profit_price=str(tp_price),
            stop_loss_price=str(sl_price)
        )

        if order:
            table = Table(title="合约做多成功")
            table.add_column("项目", style="cyan")
            table.add_column("信息", style="green")

            table.add_row("交易对", symbol)
            table.add_row("订单ID", order.get('orderId', 'N/A'))
            table.add_row("杠杆", f"{leverage}x")
            table.add_row("数量", f"{quantity:.6f}")
            table.add_row("止盈价", f"${tp_price:.2f}")
            table.add_row("止损价", f"${sl_price:.2f}")

            console.print(table)
            print_success("合约做多完成")
            return True
        else:
            print_error("做多失败")
            return False

    except Exception as e:
        print_error(f"合约做多失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_contract_short(contract_client: BitgetContractClient, market_fetcher: MarketDataFetcher,
                        symbol: str = "ETH/USDT", amount: float = 20.0, leverage: int = 10):
    """测试合约做空（开空）"""
    # 标准化交易对格式
    symbol = normalize_symbol(symbol)
    print_header(f"📉 合约做空（开空）- {symbol} - {leverage}x 杠杆")

    try:
        print_info(f"1. 检查合约账户余额...")
        balance = contract_client.get_balance('USDT')
        if balance is None or balance < amount / leverage:
            print_error(f"保证金不足，需要至少 {amount/leverage:.2f} USDT")
            return False
        print_success(f"保证金充足")

        print_info(f"2. 获取 {symbol} 当前价格...")
        current_price = market_fetcher.fetch_current_price(symbol)
        if current_price is None:
            print_error(f"无法获取 {symbol} 的当前价格")
            return False
        print_info(f"当前价格: ${current_price:.2f}")

        # 计算数量
        quantity = amount / current_price
        tp_price = current_price * 0.95  # 做空止盈是价格下跌
        sl_price = current_price * 1.02  # 做空止损是价格上涨

        console.print()
        console.print(Panel(
            f"[bold]合约做空信息[/bold]\n"
            f"交易对: {symbol}\n"
            f"名义价值: {amount} USDT\n"
            f"杠杆: {leverage}x\n"
            f"保证金: {amount/leverage:.2f} USDT\n"
            f"当前价格: ${current_price:.2f}\n"
            f"数量: {quantity:.6f}\n"
            f"止盈价: ${tp_price:.2f} (-5%)\n"
            f"止损价: ${sl_price:.2f} (+2%)\n"
            f"[green]真实合约做空，盈利随价格下跌增加[/green]",
            title="确认合约做空",
            border_style="red"
        ))

        if not Confirm.ask("是否确认做空？"):
            print_warning("取消做空")
            return False

        print_info("3. 执行合约做空...")
        order = contract_client.open_short(
            symbol=symbol,
            size=str(quantity),
            leverage=leverage,
            take_profit_price=str(tp_price),
            stop_loss_price=str(sl_price)
        )

        if order:
            table = Table(title="合约做空成功")
            table.add_column("项目", style="cyan")
            table.add_column("信息", style="green")

            table.add_row("交易对", symbol)
            table.add_row("订单ID", order.get('orderId', 'N/A'))
            table.add_row("杠杆", f"{leverage}x")
            table.add_row("数量", f"{quantity:.6f}")
            table.add_row("止盈价", f"${tp_price:.2f}")
            table.add_row("止损价", f"${sl_price:.2f}")

            console.print(table)
            print_success("合约做空完成")
            return True
        else:
            print_error("做空失败")
            return False

    except Exception as e:
        print_error(f"合约做空失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_contract_close_long(contract_client: BitgetContractClient, symbol: str = "BTC/USDT"):
    """测试合约平多"""
    # 标准化交易对格式
    symbol = normalize_symbol(symbol)
    print_header(f"📉 合约平多 - {symbol}")

    try:
        print_info(f"1. 查询 {symbol} 的多头持仓...")
        positions = contract_client.get_positions(symbol)

        # 查找多头持仓
        long_position = None
        for pos in positions:
            if pos.get('holdSide') == 'long' and float(pos.get('total', 0)) > 0:
                long_position = pos
                break

        if not long_position:
            print_warning(f"未持有 {symbol} 的多头合约仓位")
            return False

        quantity = long_position['total']
        print_success(f"持有多头: {quantity}")

        console.print()
        console.print(Panel(
            f"[bold]合约平多信息[/bold]\n"
            f"交易对: {symbol}\n"
            f"持仓数量: {quantity}\n"
            f"操作: 平多仓",
            title="确认合约平多",
            border_style="yellow"
        ))

        if not Confirm.ask("是否确认平多？"):
            print_warning("取消平多")
            return False

        print_info("2. 执行合约平多...")
        order = contract_client.close_long(symbol, quantity)

        if order:
            print_success(f"合约平多成功，订单ID: {order.get('orderId', 'N/A')}")
            return True
        else:
            print_error("平多失败")
            return False

    except Exception as e:
        print_error(f"合约平多失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_contract_close_short(contract_client: BitgetContractClient, symbol: str = "ETH/USDT"):
    """测试合约平空"""
    # 标准化交易对格式
    symbol = normalize_symbol(symbol)
    print_header(f"📈 合约平空 - {symbol}")

    try:
        print_info(f"1. 查询 {symbol} 的空头持仓...")
        positions = contract_client.get_positions(symbol)

        # 查找空头持仓
        short_position = None
        for pos in positions:
            if pos.get('holdSide') == 'short' and float(pos.get('total', 0)) > 0:
                short_position = pos
                break

        if not short_position:
            print_warning(f"未持有 {symbol} 的空头合约仓位")
            return False

        quantity = short_position['total']
        print_success(f"持有空头: {quantity}")

        console.print()
        console.print(Panel(
            f"[bold]合约平空信息[/bold]\n"
            f"交易对: {symbol}\n"
            f"持仓数量: {quantity}\n"
            f"操作: 平空仓",
            title="确认合约平空",
            border_style="green"
        ))

        if not Confirm.ask("是否确认平空？"):
            print_warning("取消平空")
            return False

        print_info("2. 执行合约平空...")
        order = contract_client.close_short(symbol, quantity)

        if order:
            print_success(f"合约平空成功，订单ID: {order.get('orderId', 'N/A')}")
            return True
        else:
            print_error("平空失败")
            return False

    except Exception as e:
        print_error(f"合约平空失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def run_interactive_test():
    """运行交互式测试"""
    console.print("""
    [bold cyan]╔═══════════════════════════════════════════════════════════╗[/bold cyan]
    [bold cyan]║                                                           ║[/bold cyan]
    [bold cyan]║         🤖 Bitget 交易功能测试脚本 🤖                    ║[/bold cyan]
    [bold cyan]║                                                           ║[/bold cyan]
    [bold cyan]║         Interactive Trading Function Tester               ║[/bold cyan]
    [bold cyan]║                                                           ║[/bold cyan]
    [bold cyan]╚═══════════════════════════════════════════════════════════╝[/bold cyan]
    """)

    # 1. 加载配置
    print_header("📋 加载配置")
    try:
        config = load_env_config()
        print_success("配置加载成功（从 .env 文件）")

        table = Table(title="配置信息")
        table.add_column("项目", style="cyan")
        table.add_column("值", style="green")

        table.add_row("运行模式", "Bitget 模拟盘 🧪" if config.demo_trading else "真实交易 ⚠️")
        table.add_row("交易金额", f"{config.trade_amount} USDT")
        table.add_row("止盈比例", f"{config.take_profit_ratio*100}%")
        table.add_row("止损比例", f"{config.stop_loss_ratio*100}%")

        console.print(table)

        if not config.demo_trading:
            print_warning("⚠️  警告: 您正在使用真实交易模式！")
            if not Confirm.ask("确认要在真实环境中测试吗？"):
                print_info("已取消")
                return

    except Exception as e:
        print_error(f"配置加载失败: {e}")
        return

    # 2. 初始化客户端
    print_header("🔧 初始化交易客户端")
    try:
        # 现货客户端
        spot_client = BitgetClient(
            api_key=config.bitget_api_key,
            api_secret=config.bitget_api_secret,
            passphrase=config.bitget_passphrase,
            demo_trading=config.demo_trading
        )

        # 合约客户端
        contract_client = BitgetContractClient(
            api_key=config.bitget_api_key,
            api_secret=config.bitget_api_secret,
            passphrase=config.bitget_passphrase,
            demo_trading=config.demo_trading,
            product_type="USDT-FUTURES"
        )

        # 订单管理器（现货）
        spot_manager = OrderManager(
            client=spot_client,
            take_profit_ratio=config.take_profit_ratio,
            stop_loss_ratio=config.stop_loss_ratio
        )

        # 市场数据获取器
        market_fetcher = MarketDataFetcher(
            exchange_id='bitget',
        )

        print_success("现货客户端初始化成功")
        print_success("合约客户端初始化成功")

    except Exception as e:
        print_error(f"初始化失败: {e}")
        return

    # 3. 主菜单循环
    while True:
        console.print()
        console.print("[bold cyan]═" * 40 + "[/bold cyan]")
        console.print("[bold]请选择要测试的功能:[/bold]")
        console.print()
        console.print("[bold yellow]【现货交易】[/bold yellow]")
        console.print("  [cyan]1[/cyan] - 📊 查询现货账户信息")
        console.print("  [cyan]2[/cyan] - 📈 现货买入（开多）")
        console.print("  [cyan]3[/cyan] - 📉 现货卖出（平多）")
        console.print()
        console.print("[bold yellow]【合约交易】[/bold yellow]")
        console.print("  [cyan]4[/cyan] - 📊 查询合约账户信息")
        console.print("  [cyan]5[/cyan] - 📈 合约做多（开多）")
        console.print("  [cyan]6[/cyan] - 📉 合约做空（开空）")
        console.print("  [cyan]7[/cyan] - 📉 合约平多")
        console.print("  [cyan]8[/cyan] - 📈 合约平空")
        console.print()
        console.print("  [cyan]0[/cyan] - 🚪 退出")
        console.print()

        choice = Prompt.ask("请输入选项", choices=["0", "1", "2", "3", "4", "5", "6", "7", "8"])

        if choice == "0":
            print_info("感谢使用，再见！")
            break

        # 现货测试
        elif choice == "1":
            test_spot_account_info(spot_manager)

        elif choice == "2":
            symbol = Prompt.ask("请输入交易对", default="BTC/USDT")
            amount = float(Prompt.ask("请输入买入金额 (USDT)", default="20"))
            test_spot_buy(spot_manager, market_fetcher, symbol, amount)

        elif choice == "3":
            symbol = Prompt.ask("请输入交易对", default="BTC/USDT")
            test_spot_sell(spot_manager, symbol)

        # 合约测试
        elif choice == "4":
            test_contract_account_info(contract_client)

        elif choice == "5":
            symbol = Prompt.ask("请输入交易对", default="BTC/USDT")
            amount = float(Prompt.ask("请输入名义价值 (USDT)", default="20"))
            leverage = int(Prompt.ask("请输入杠杆倍数", default="10"))
            test_contract_long(contract_client, market_fetcher, symbol, amount, leverage)

        elif choice == "6":
            symbol = Prompt.ask("请输入交易对", default="ETH/USDT")
            amount = float(Prompt.ask("请输入名义价值 (USDT)", default="20"))
            leverage = int(Prompt.ask("请输入杠杆倍数", default="10"))
            test_contract_short(contract_client, market_fetcher, symbol, amount, leverage)

        elif choice == "7":
            symbol = Prompt.ask("请输入交易对", default="BTC/USDT")
            test_contract_close_long(contract_client, symbol)

        elif choice == "8":
            symbol = Prompt.ask("请输入交易对", default="ETH/USDT")
            test_contract_close_short(contract_client, symbol)

        input("\n按 Enter 继续...")


if __name__ == "__main__":
    try:
        run_interactive_test()
    except KeyboardInterrupt:
        console.print("\n\n[yellow]⚠️  测试已中断[/yellow]")
        sys.exit(0)
    except Exception as e:
        console.print(f"\n\n[red]❌ 发生错误: {e}[/red]")
        import traceback
        traceback.print_exc()
        sys.exit(1)
