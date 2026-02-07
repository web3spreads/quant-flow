"""
BTC 止盈止损问题调试测试

验证是否是精度问题导致 BTC 无法设置止盈止损
"""

import os
import sys
from pathlib import Path

import pytest


# 添加项目根目录到路径
def find_project_root(marker="pyproject.toml"):
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / marker).is_file():
            return current
        current = current.parent
    raise FileNotFoundError(f"Could not find {marker} in any parent directory of {__file__}")

project_root = find_project_root()
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv

load_dotenv()

from src.trading.client import HyperliquidClient

# 兼容环境变量命名
PRIVATE_KEY = os.getenv("HYPERLIQUID_PRIVATE_KEY") or os.getenv("PRIVATE_KEY")
ACCOUNT_ADDRESS = os.getenv("HYPERLIQUID_ACCOUNT_ADDRESS") or os.getenv("ACCOUNT_ADDRESS")
TESTNET = os.getenv("HYPERLIQUID_TESTNET", "false").lower() == "true"

# 跳过条件：没有私钥时跳过需要真实 API 的测试
requires_private_key = pytest.mark.skipif(
    not PRIVATE_KEY,
    reason="需要 HYPERLIQUID_PRIVATE_KEY 环境变量"
)


@requires_private_key
def test_asset_info_comparison():
    """对比 BTC 和其他币种的 asset info"""
    print("=" * 60)
    print("测试 1: 对比不同币种的 Asset Info")
    print("=" * 60)

    client = HyperliquidClient(
        private_key=PRIVATE_KEY,
        account_address=ACCOUNT_ADDRESS,
        testnet=TESTNET
    )

    symbols = ["BTC", "ETH", "SOL", "DOGE"]

    for symbol in symbols:
        info = client.get_asset_info(symbol)
        if info:
            print(f"\n{symbol}:")
            print(f"  szDecimals: {info.get('szDecimals')}")
            print(f"  maxLeverage: {info.get('maxLeverage')}")
            print(f"  onlyIsolated: {info.get('onlyIsolated')}")
            # 打印完整信息以便调试
            print(f"  完整信息: {info}")
        else:
            print(f"\n{symbol}: 获取失败")


@requires_private_key
def test_price_formatting():
    """测试价格格式化"""
    print("\n" + "=" * 60)
    print("测试 2: 价格格式化对比")
    print("=" * 60)

    client = HyperliquidClient(
        private_key=PRIVATE_KEY,
        account_address=ACCOUNT_ADDRESS,
        testnet=TESTNET
    )

    symbols = ["BTC", "ETH", "SOL"]
    take_profit_ratio = 0.02  # 2%
    stop_loss_ratio = 0.01    # 1%

    for symbol in symbols:
        price = client.get_current_price(symbol)
        if price:
            tp_price = price * (1 + take_profit_ratio)
            sl_price = price * (1 - stop_loss_ratio)

            tp_formatted = client.format_price(symbol, tp_price)
            sl_formatted = client.format_price(symbol, sl_price)

            print(f"\n{symbol}:")
            print(f"  当前价格: ${price}")
            print(f"  止盈价格 (原始): ${tp_price}")
            print(f"  止盈价格 (格式化): ${tp_formatted}")
            print(f"  止损价格 (原始): ${sl_price}")
            print(f"  止损价格 (格式化): ${sl_formatted}")


@requires_private_key
def test_size_calculation():
    """测试仓位大小计算"""
    print("\n" + "=" * 60)
    print("测试 3: 仓位大小计算对比")
    print("=" * 60)

    from src.trading.order_manager import OrderManager

    client = HyperliquidClient(
        private_key=PRIVATE_KEY,
        account_address=ACCOUNT_ADDRESS,
        testnet=TESTNET
    )

    order_manager = OrderManager(client)

    symbols = ["BTC", "ETH", "SOL"]
    usdt_amount = 10  # 用 $10 测试
    leverage = 3

    for symbol in symbols:
        size = order_manager.calculate_position_size(symbol, usdt_amount, leverage)
        asset_info = client.get_asset_info(symbol)
        sz_decimals = asset_info.get('szDecimals') if asset_info else 'N/A'

        print(f"\n{symbol}:")
        print(f"  szDecimals: {sz_decimals}")
        print(f"  计算的 size: {size}")
        print(f"  size 类型: {type(size)}")

        # 模拟格式化后的 size
        if asset_info and 'szDecimals' in asset_info:
            decimals = asset_info['szDecimals']
            formatted_size = float(round(size, decimals)) if size else None
            print(f"  格式化后 size: {formatted_size}")


@requires_private_key
def test_btc_tpsl_dry_run():
    """
    模拟 BTC 止盈止损下单流程（不实际下单）
    打印所有参数，看哪里可能有问题
    """
    print("\n" + "=" * 60)
    print("测试 4: BTC 止盈止损参数模拟 (Dry Run)")
    print("=" * 60)

    from src.trading.order_manager import OrderManager

    client = HyperliquidClient(
        private_key=PRIVATE_KEY,
        account_address=ACCOUNT_ADDRESS,
        testnet=TESTNET
    )

    order_manager = OrderManager(client)

    symbol = "BTC"
    usdt_amount = 10
    leverage = 3
    take_profit_ratio = 0.02
    stop_loss_ratio = 0.01

    # 1. 获取价格
    current_price = client.get_current_price(symbol)
    print(f"\n当前价格: ${current_price}")

    # 2. 计算仓位
    size = order_manager.calculate_position_size(symbol, usdt_amount, leverage)
    print(f"计算的仓位: {size}")

    # 3. 获取 asset info
    asset_info = client.get_asset_info(symbol)
    sz_decimals = asset_info.get('szDecimals') if asset_info else 3
    print(f"szDecimals: {sz_decimals}")

    # 4. 格式化 size
    formatted_size = float(round(size, sz_decimals)) if size else None
    print(f"格式化后的 size: {formatted_size}")

    # 5. 计算止盈止损价格
    tp_price = current_price * (1 + take_profit_ratio)
    sl_price = current_price * (1 - stop_loss_ratio)
    print(f"\n止盈价格 (原始): ${tp_price}")
    print(f"止损价格 (原始): ${sl_price}")

    # 6. 格式化价格
    tp_formatted = client.format_price(symbol, tp_price)
    sl_formatted = client.format_price(symbol, sl_price)
    print(f"止盈价格 (格式化): ${tp_formatted}")
    print(f"止损价格 (格式化): ${sl_formatted}")

    # 7. 计算限价 (模拟 place_tpsl_order 的逻辑)
    tp_slippage = 0.01
    sl_slippage = 0.05

    # 止盈单限价 (做多平仓是卖出，限价高于触发价)
    tp_limit_price = tp_formatted * (1 + tp_slippage)
    tp_limit_formatted = client.format_price(symbol, tp_limit_price)

    # 止损单限价 (做多平仓是卖出，限价低于触发价)
    sl_limit_price = sl_formatted * (1 - sl_slippage)
    sl_limit_formatted = client.format_price(symbol, sl_limit_price)

    print(f"\n止盈单限价: ${tp_limit_formatted}")
    print(f"止损单限价: ${sl_limit_formatted}")

    # 8. 打印最终下单参数
    print("\n" + "-" * 40)
    print("最终下单参数:")
    print("-" * 40)

    print("\n市价单参数:")
    print(f"  symbol: {symbol}")
    print("  is_buy: True")
    print(f"  size: {formatted_size}")

    print("\n止盈单参数:")
    print(f"  symbol: {symbol}")
    print("  is_buy: False (平仓卖出)")
    print(f"  size: {formatted_size}")
    print(f"  trigger_price: {tp_formatted}")
    print(f"  limit_price: {tp_limit_formatted}")
    print("  reduce_only: True")

    print("\n止损单参数:")
    print(f"  symbol: {symbol}")
    print("  is_buy: False (平仓卖出)")
    print(f"  size: {formatted_size}")
    print(f"  trigger_price: {sl_formatted}")
    print(f"  limit_price: {sl_limit_formatted}")
    print("  reduce_only: True")

    # 9. 检查潜在问题
    print("\n" + "-" * 40)
    print("潜在问题检查:")
    print("-" * 40)

    if formatted_size == 0:
        print("❌ 问题: size 为 0，订单会被拒绝!")
    elif formatted_size and formatted_size < 0.0001:
        print(f"⚠️ 警告: size 很小 ({formatted_size})，可能低于最小订单限制")
    else:
        print(f"✅ size 看起来正常: {formatted_size}")

    # 检查价格精度
    if tp_formatted == sl_formatted:
        print("❌ 问题: 止盈和止损价格相同!")
    else:
        print("✅ 止盈止损价格不同")


@requires_private_key
@pytest.mark.skip(reason="需要交互式输入，不适合自动化测试")
def test_real_btc_order():
    """
    真实下单测试 - 使用最小金额
    ⚠️ 这会真实下单！
    """
    print("\n" + "=" * 60)
    print("测试 5: BTC 真实下单测试 (小金额)")
    print("=" * 60)

    confirm = input("\n⚠️ 这会真实下单 $10 的 BTC！确认继续? (yes/no): ")
    if confirm.lower() != 'yes':
        print("已取消")
        return

    from src.trading.order_manager import OrderManager

    client = HyperliquidClient(
        private_key=PRIVATE_KEY,
        account_address=ACCOUNT_ADDRESS,
        testnet=TESTNET
    )

    order_manager = OrderManager(client)

    # 使用小金额测试
    result = order_manager.execute_long(
        symbol="BTC",
        usdt_amount=10,
        leverage=3,
        with_tpsl=True
    )

    print("\n下单结果:")
    print(f"  success: {result.get('success') if result else 'None'}")
    print(f"  market_order: {result.get('market_order') if result else 'None'}")
    print(f"  take_profit_order: {result.get('take_profit_order') if result else 'None'}")
    print(f"  stop_loss_order: {result.get('stop_loss_order') if result else 'None'}")
    print(f"  errors: {result.get('errors') if result else 'None'}")

    if result:
        print(f"\n完整结果:\n{result}")


if __name__ == "__main__":
    # 检查环境变量
    if not PRIVATE_KEY:
        print("❌ 请设置 HYPERLIQUID_PRIVATE_KEY 环境变量")
        sys.exit(1)

    try:
        # 运行非下单测试
        test_asset_info_comparison()
        test_price_formatting()
        test_size_calculation()
        test_btc_tpsl_dry_run()

        # 询问是否进行真实下单测试
        print("\n" + "=" * 60)
        run_real = input("是否进行真实下单测试? (yes/no): ")
        if run_real.lower() == 'yes':
            test_real_btc_order()

        print("\n" + "=" * 60)
        print("✅ 测试完成")
        print("=" * 60)

    except KeyboardInterrupt:
        print("\n\n⚠️ 测试被用户中断")
    except Exception as e:
        print(f"\n\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
