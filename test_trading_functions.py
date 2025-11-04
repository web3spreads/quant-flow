#!/usr/bin/env python3
"""
永续合约交易功能测试脚本

提供以下功能测试：
1. 查询账户余额
2. 做多（开多仓）
3. 平多（平多仓）
4. 做空（开空仓）
5. 平空（平空仓）

所有功能都支持杠杆设置，默认1x
"""

import os
import sys
import json
import time
from typing import Optional
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.trading.client import HyperliquidClient


class TradingTester:
    """交易功能测试类"""

    def __init__(self, testnet: bool = True):
        """
        初始化测试器

        Args:
            testnet: 是否使用测试网
        """
        # 从环境变量读取配置
        private_key = os.getenv("HYPERLIQUID_PRIVATE_KEY", "")
        account_address = os.getenv("HYPERLIQUID_ACCOUNT_ADDRESS", "")

        if not private_key:
            raise ValueError("❌ 请在 .env 文件中设置 HYPERLIQUID_PRIVATE_KEY")

        # 初始化客户端
        self.client = HyperliquidClient(
            private_key=private_key,
            account_address=account_address if account_address else None,
            testnet=testnet
        )

        print("=" * 80)
        print("✅ 客户端初始化成功")
        print("=" * 80)

    def test_balance(self) -> bool:
        """
        测试1: 查询账户余额

        Returns:
            是否成功
        """
        print("\n" + "=" * 80)
        print("📊 测试 1: 查询账户余额")
        print("=" * 80)

        try:
            balance = self.client.get_balance()

            if balance:
                print(f"\n账户信息:")
                print(f"  💰 账户总价值: ${balance['accountValue']:.2f}")
                print(f"  🔒 已用保证金: ${balance['totalMarginUsed']:.2f}")
                print(f"  💵 可用余额: ${balance['totalRawUsd']:.2f}")
                print(f"  📤 可提现: ${float(balance['withdrawable']):.2f}")
                print("\n✅ 余额查询成功")
                return True
            else:
                print("\n❌ 余额查询失败")
                return False

        except Exception as e:
            print(f"\n❌ 余额查询异常: {e}")
            import traceback
            traceback.print_exc()
            return False

    def test_positions(self) -> bool:
        """
        查询当前持仓

        Returns:
            是否成功
        """
        print("\n" + "=" * 80)
        print("📈 查询当前持仓")
        print("=" * 80)

        try:
            positions = self.client.get_positions()

            if len(positions) == 0:
                print("\n📭 当前无持仓")
            else:
                print(f"\n当前持仓数: {len(positions)}")
                for pos in positions:
                    coin = pos['coin']
                    size = float(pos['szi'])
                    entry_px = float(pos['entryPx'])
                    unrealized_pnl = float(pos['unrealizedPnl'])
                    position_value = float(pos['positionValue'])

                    side = "多仓 🟢" if size > 0 else "空仓 🔴"

                    print(f"\n  {coin} {side}")
                    print(f"    持仓数量: {abs(size):.4f}")
                    print(f"    入场价格: ${entry_px:.4f}")
                    print(f"    仓位价值: ${position_value:.2f}")
                    print(f"    未实现盈亏: ${unrealized_pnl:.2f} ({unrealized_pnl/position_value*100:.2f}%)")

            print("\n✅ 持仓查询成功")
            return True

        except Exception as e:
            print(f"\n❌ 持仓查询异常: {e}")
            import traceback
            traceback.print_exc()
            return False

    def test_open_long(
        self,
        symbol: str = "ETH",
        size: float = 0.01,
        leverage: int = 1
    ) -> bool:
        """
        测试2: 做多（开多仓）

        Args:
            symbol: 交易对符号（如 'ETH'）
            size: 下单数量（合约数量）
            leverage: 杠杆倍数（默认1x）

        Returns:
            是否成功
        """
        print("\n" + "=" * 80)
        print(f"📈 测试 2: 做多 {symbol} (杠杆 {leverage}x)")
        print("=" * 80)

        try:
            # 1. 设置杠杆
            print(f"\n1️⃣ 设置杠杆为 {leverage}x...")
            leverage_result = self.client.update_leverage(symbol, leverage, is_cross=True)
            print(f"   杠杆设置结果: {json.dumps(leverage_result, indent=2)}")

            # 检查杠杆设置是否成功
            if leverage_result.get('status') == 'error':
                print(f"\n❌ 杠杆设置失败，无法继续下单")
                print(f"   错误: {leverage_result.get('message')}")
                return False

            # 2. 获取当前价格
            current_price = self.client.get_current_price(symbol)
            if not current_price:
                print(f"❌ 无法获取 {symbol} 价格")
                return False
            print(f"\n2️⃣ 当前价格: ${current_price:.2f}")

            # 3. 计算预估成本
            estimated_cost = current_price * size / leverage
            print(f"\n3️⃣ 预估成本: ${estimated_cost:.2f} (杠杆 {leverage}x)")
            print(f"   持仓价值: ${current_price * size:.2f}")

            # 4. 下市价买单（做多）
            print(f"\n4️⃣ 下市价买单 {size} {symbol}...")
            order_result = self.client.place_market_order(
                symbol=symbol,
                is_buy=True,  # 买入 = 做多
                size=size,
                reduce_only=False
            )

            print(f"\n📋 订单结果:")
            print(json.dumps(order_result, indent=2))

            # 检查订单是否成功
            success, error_msg = self.client.check_order_success(order_result)
            if success:
                print(f"\n✅ 做多成功！")

                # 等待一下再查询持仓
                time.sleep(1)
                self.test_positions()
                return True
            else:
                print(f"\n❌ 做多失败: {error_msg}")
                return False

        except Exception as e:
            print(f"\n❌ 做多异常: {e}")
            import traceback
            traceback.print_exc()
            return False

    def test_close_long(self, symbol: str = "ETH", size: Optional[float] = None) -> bool:
        """
        测试3: 平多（平多仓）

        Args:
            symbol: 交易对符号
            size: 平仓数量（None=全平）

        Returns:
            是否成功
        """
        print("\n" + "=" * 80)
        print(f"📉 测试 3: 平多 {symbol}")
        print("=" * 80)

        try:
            # 1. 检查持仓
            positions = self.client.get_positions()
            position = next((p for p in positions if p['coin'] == symbol), None)

            if not position:
                print(f"❌ 没有 {symbol} 的持仓")
                return False

            position_size = float(position['szi'])
            if position_size <= 0:
                print(f"❌ {symbol} 不是多仓（当前仓位: {position_size}）")
                return False

            # 2. 确定平仓数量
            close_size = size if size else position_size

            print(f"\n当前多仓: {position_size:.4f} {symbol}")
            print(f"平仓数量: {close_size:.4f} {symbol}")

            # 3. 下市价卖单（平多）
            print(f"\n下市价卖单...")
            order_result = self.client.place_market_order(
                symbol=symbol,
                is_buy=False,  # 卖出 = 平多
                size=close_size,
                reduce_only=True  # 只减仓
            )

            print(f"\n📋 订单结果:")
            print(json.dumps(order_result, indent=2))

            # 检查订单是否成功
            success, error_msg = self.client.check_order_success(order_result)
            if success:
                print(f"\n✅ 平多成功！")

                # 等待一下再查询持仓
                time.sleep(1)
                self.test_positions()
                return True
            else:
                print(f"\n❌ 平多失败: {error_msg}")
                return False

        except Exception as e:
            print(f"\n❌ 平多异常: {e}")
            import traceback
            traceback.print_exc()
            return False

    def test_open_short(
        self,
        symbol: str = "ETH",
        size: float = 0.01,
        leverage: int = 1
    ) -> bool:
        """
        测试4: 做空（开空仓）

        Args:
            symbol: 交易对符号
            size: 下单数量（合约数量）
            leverage: 杠杆倍数（默认1x）

        Returns:
            是否成功
        """
        print("\n" + "=" * 80)
        print(f"📉 测试 4: 做空 {symbol} (杠杆 {leverage}x)")
        print("=" * 80)

        try:
            # 1. 设置杠杆
            print(f"\n1️⃣ 设置杠杆为 {leverage}x...")
            leverage_result = self.client.update_leverage(symbol, leverage, is_cross=True)
            print(f"   杠杆设置结果: {json.dumps(leverage_result, indent=2)}")

            # 检查杠杆设置是否成功
            if leverage_result.get('status') == 'error':
                print(f"\n❌ 杠杆设置失败，无法继续下单")
                print(f"   错误: {leverage_result.get('message')}")
                return False

            # 2. 获取当前价格
            current_price = self.client.get_current_price(symbol)
            if not current_price:
                print(f"❌ 无法获取 {symbol} 价格")
                return False
            print(f"\n2️⃣ 当前价格: ${current_price:.2f}")

            # 3. 计算预估成本
            estimated_cost = current_price * size / leverage
            print(f"\n3️⃣ 预估成本: ${estimated_cost:.2f} (杠杆 {leverage}x)")
            print(f"   持仓价值: ${current_price * size:.2f}")

            # 4. 下市价卖单（做空）
            print(f"\n4️⃣ 下市价卖单 {size} {symbol}...")
            order_result = self.client.place_market_order(
                symbol=symbol,
                is_buy=False,  # 卖出 = 做空
                size=size,
                reduce_only=False
            )

            print(f"\n📋 订单结果:")
            print(json.dumps(order_result, indent=2))

            # 检查订单是否成功
            success, error_msg = self.client.check_order_success(order_result)
            if success:
                print(f"\n✅ 做空成功！")

                # 等待一下再查询持仓
                time.sleep(1)
                self.test_positions()
                return True
            else:
                print(f"\n❌ 做空失败: {error_msg}")
                return False

        except Exception as e:
            print(f"\n❌ 做空异常: {e}")
            import traceback
            traceback.print_exc()
            return False

    def test_close_short(self, symbol: str = "ETH", size: Optional[float] = None) -> bool:
        """
        测试5: 平空（平空仓）

        Args:
            symbol: 交易对符号
            size: 平仓数量（None=全平）

        Returns:
            是否成功
        """
        print("\n" + "=" * 80)
        print(f"📈 测试 5: 平空 {symbol}")
        print("=" * 80)

        try:
            # 1. 检查持仓
            positions = self.client.get_positions()
            position = next((p for p in positions if p['coin'] == symbol), None)

            if not position:
                print(f"❌ 没有 {symbol} 的持仓")
                return False

            position_size = float(position['szi'])
            if position_size >= 0:
                print(f"❌ {symbol} 不是空仓（当前仓位: {position_size}）")
                return False

            # 2. 确定平仓数量
            close_size = size if size else abs(position_size)

            print(f"\n当前空仓: {position_size:.4f} {symbol}")
            print(f"平仓数量: {close_size:.4f} {symbol}")

            # 3. 下市价买单（平空）
            print(f"\n下市价买单...")
            order_result = self.client.place_market_order(
                symbol=symbol,
                is_buy=True,  # 买入 = 平空
                size=close_size,
                reduce_only=True  # 只减仓
            )

            print(f"\n📋 订单结果:")
            print(json.dumps(order_result, indent=2))

            # 检查订单是否成功
            success, error_msg = self.client.check_order_success(order_result)
            if success:
                print(f"\n✅ 平空成功！")

                # 等待一下再查询持仓
                time.sleep(1)
                self.test_positions()
                return True
            else:
                print(f"\n❌ 平空失败: {error_msg}")
                return False

        except Exception as e:
            print(f"\n❌ 平空异常: {e}")
            import traceback
            traceback.print_exc()
            return False


def show_menu():
    """显示菜单"""
    print("\n" + "=" * 80)
    print("🤖 永续合约交易功能测试菜单")
    print("=" * 80)
    print("1. 查询账户余额")
    print("2. 查询当前持仓")
    print("3. 做多（开多仓）")
    print("4. 平多（平多仓）")
    print("5. 做空（开空仓）")
    print("6. 平空（平空仓）")
    print("7. 完整测试流程（做多->平多->做空->平空）")
    print("0. 退出")
    print("=" * 80)


def main():
    """主函数"""
    print("\n" + "=" * 80)
    print("🚀 永续合约交易功能测试脚本")
    print("=" * 80)

    # 检查是否使用测试网
    testnet = os.getenv("HYPERLIQUID_TESTNET", "true").lower() == "true"
    if testnet:
        print("⚠️  当前使用: 测试网")
        print("💡 测试网水龙头: https://app.hyperliquid-testnet.xyz/faucet")
    else:
        print("⚠️  当前使用: 主网（真实资金！）")
        confirm = input("\n确认使用主网进行测试？(yes/no): ")
        if confirm.lower() != "yes":
            print("❌ 已取消")
            return

    try:
        # 初始化测试器
        tester = TradingTester(testnet=testnet)

        # 默认参数
        default_symbol = "ETH"
        default_size = 0.01
        default_leverage = 1

        while True:
            show_menu()
            choice = input("\n请选择功能 (0-7): ").strip()

            if choice == "0":
                print("\n👋 再见！")
                break

            elif choice == "1":
                tester.test_balance()

            elif choice == "2":
                tester.test_positions()

            elif choice == "3":
                symbol = input(f"交易对 (默认 {default_symbol}): ").strip() or default_symbol
                size = float(input(f"数量 (默认 {default_size}): ").strip() or default_size)
                leverage = int(input(f"杠杆 (默认 {default_leverage}x): ").strip() or default_leverage)
                tester.test_open_long(symbol, size, leverage)

            elif choice == "4":
                symbol = input(f"交易对 (默认 {default_symbol}): ").strip() or default_symbol
                size_input = input("平仓数量 (回车=全平): ").strip()
                size = float(size_input) if size_input else None
                tester.test_close_long(symbol, size)

            elif choice == "5":
                symbol = input(f"交易对 (默认 {default_symbol}): ").strip() or default_symbol
                size = float(input(f"数量 (默认 {default_size}): ").strip() or default_size)
                leverage = int(input(f"杠杆 (默认 {default_leverage}x): ").strip() or default_leverage)
                tester.test_open_short(symbol, size, leverage)

            elif choice == "6":
                symbol = input(f"交易对 (默认 {default_symbol}): ").strip() or default_symbol
                size_input = input("平仓数量 (回车=全平): ").strip()
                size = float(size_input) if size_input else None
                tester.test_close_short(symbol, size)

            elif choice == "7":
                print("\n" + "=" * 80)
                print("🔄 完整测试流程")
                print("=" * 80)

                symbol = input(f"交易对 (默认 {default_symbol}): ").strip() or default_symbol
                size = float(input(f"数量 (默认 {default_size}): ").strip() or default_size)
                leverage = int(input(f"杠杆 (默认 {default_leverage}x): ").strip() or default_leverage)

                print("\n开始完整测试...")

                # 1. 查询余额
                tester.test_balance()
                input("\n按回车继续...")

                # 2. 做多
                if tester.test_open_long(symbol, size, leverage):
                    input("\n按回车继续...")

                    # 3. 平多
                    tester.test_close_long(symbol)
                    input("\n按回车继续...")

                # 4. 做空
                if tester.test_open_short(symbol, size, leverage):
                    input("\n按回车继续...")

                    # 5. 平空
                    tester.test_close_short(symbol)

                print("\n" + "=" * 80)
                print("✅ 完整测试流程结束")
                print("=" * 80)

            else:
                print("❌ 无效选择，请重试")

            input("\n按回车继续...")

    except Exception as e:
        print(f"\n❌ 程序异常: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
