#!/usr/bin/env python3
"""
测试现货交易功能
验证 Hyperliquid 现货买入、卖出和余额查询
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.trading.client import HyperliquidClient
from src.trading.order_manager import OrderManager


def test_spot_balance():
    """测试现货余额查询"""
    print("=" * 80)
    print("🔍 测试现货余额查询")
    print("=" * 80)

    # 初始化客户端
    private_key = os.getenv("HYPERLIQUID_PRIVATE_KEY", "")
    account_address = os.getenv("HYPERLIQUID_ACCOUNT_ADDRESS", "")
    testnet = os.getenv("HYPERLIQUID_TESTNET", "true").lower() == "true"

    client = HyperliquidClient(
        private_key=private_key,
        account_address=account_address if account_address else None,
        testnet=testnet
    )

    # 查询现货余额
    print("\n📦 查询现货余额...")
    spot_balances = client.get_spot_balances()

    if spot_balances:
        print(f"\n✅ 找到 {len(spot_balances)} 个现货持仓:")
        for balance in spot_balances:
            coin = balance['coin']
            total = balance['total']
            available = balance['available']
            hold = balance['hold']
            print(f"\n  {coin}:")
            print(f"    总量: {total}")
            print(f"    可用: {available}")
            print(f"    冻结: {hold}")
    else:
        print("\n✅ 当前无现货持仓")

    print("\n" + "=" * 80)


def test_spot_buy():
    """测试现货买入"""
    print("=" * 80)
    print("💰 测试现货买入")
    print("=" * 80)

    # 初始化客户端
    private_key = os.getenv("HYPERLIQUID_PRIVATE_KEY", "")
    account_address = os.getenv("HYPERLIQUID_ACCOUNT_ADDRESS", "")
    testnet = os.getenv("HYPERLIQUID_TESTNET", "true").lower() == "true"

    client = HyperliquidClient(
        private_key=private_key,
        account_address=account_address if account_address else None,
        testnet=testnet
    )

    # 初始化 OrderManager
    order_manager = OrderManager(
        client=client,
        take_profit_ratio=0.05,
        stop_loss_ratio=0.02,
        default_leverage=10
    )

    # 测试参数
    symbol = "ETH"  # 测试币种
    usdt_amount = 10.0  # 投入金额

    print(f"\n准备买入 {symbol} 现货")
    print(f"投入金额: ${usdt_amount:.2f}")

    # 询问用户确认
    confirm = input(f"\n⚠️  确认要在{'测试网' if testnet else '主网'}买入 ${usdt_amount:.2f} 的 {symbol} 现货吗？(yes/no): ").strip().lower()

    if confirm != "yes":
        print("\n⏭️  跳过现货买入测试")
        return

    # 执行现货买入
    print(f"\n📦 执行现货定投...")
    result = order_manager.buy_spot_for_dca(
        symbol=symbol,
        usdt_amount=usdt_amount
    )

    if result and result.get('success'):
        print(f"\n✅ 现货买入成功！")
        print(f"   订单数据: {result}")
    else:
        print(f"\n❌ 现货买入失败")
        if result:
            print(f"   错误信息: {result}")

    print("\n" + "=" * 80)


def test_order_manager_integration():
    """测试 OrderManager 的现货功能集成"""
    print("=" * 80)
    print("🧪 测试 OrderManager 现货功能集成")
    print("=" * 80)

    # 初始化客户端
    private_key = os.getenv("HYPERLIQUID_PRIVATE_KEY", "")
    account_address = os.getenv("HYPERLIQUID_ACCOUNT_ADDRESS", "")
    testnet = os.getenv("HYPERLIQUID_TESTNET", "true").lower() == "true"

    client = HyperliquidClient(
        private_key=private_key,
        account_address=account_address if account_address else None,
        testnet=testnet
    )

    order_manager = OrderManager(
        client=client,
        take_profit_ratio=0.05,
        stop_loss_ratio=0.02,
        default_leverage=10
    )

    # 测试获取现货持仓
    print("\n📊 获取现货持仓...")
    holdings = order_manager.get_spot_holdings()

    if holdings:
        print(f"\n当前现货持仓 ({len(holdings)} 个):")
        for holding in holdings:
            print(f"  {holding['coin']}: {holding['total']} (可用: {holding['available']})")
    else:
        print("\n当前无现货持仓")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    print("\n🤖 Hyperliquid 现货交易功能测试\n")

    # 测试1: 余额查询
    test_spot_balance()

    # 测试2: OrderManager 集成
    test_order_manager_integration()

    # 测试3: 现货买入（需要用户确认）
    print("\n⚠️  以下测试会实际下单，请谨慎操作！\n")
    do_buy_test = input("是否测试现货买入功能？(yes/no): ").strip().lower()
    if do_buy_test == "yes":
        test_spot_buy()

    print("\n✅ 现货交易功能测试完成！\n")
