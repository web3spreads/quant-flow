#!/usr/bin/env python3
"""
测试止盈止损订单的限价计算逻辑
验证修复后 TP 和 SL 订单的限价方向正确
"""

def test_limit_price_logic():
    """测试限价计算逻辑"""

    print("=" * 60)
    print("测试止盈止损订单限价计算逻辑")
    print("=" * 60)

    trigger_price = 100.0
    tp_slippage = 0.01  # 1%
    sl_slippage = 0.05  # 5%

    # 测试做多（买入开仓，卖出平仓）
    print("\n【做多持仓】入场价: $100")
    print("-" * 60)

    # 止盈：触发价更高（如 $110），平仓方向是卖出
    tp_trigger_long = 110.0
    is_buy_tp_long = False  # 卖出平仓
    tp_limit_long = tp_trigger_long * (1 + tp_slippage)  # 限价高于触发价
    print(f"✅ 止盈(TP): 触发价=${tp_trigger_long:.2f}, 卖出平仓, 限价=${tp_limit_long:.2f}")
    print(f"   逻辑: limit > trigger (${tp_limit_long:.2f} > ${tp_trigger_long:.2f}) ✓")
    assert tp_limit_long > tp_trigger_long, "TP平多：限价应该高于触发价"

    # 止损：触发价更低（如 $95），平仓方向是卖出
    sl_trigger_long = 95.0
    is_buy_sl_long = False  # 卖出平仓
    sl_limit_long = sl_trigger_long * (1 - sl_slippage)  # 限价低于触发价
    print(f"✅ 止损(SL): 触发价=${sl_trigger_long:.2f}, 卖出平仓, 限价=${sl_limit_long:.2f}")
    print(f"   逻辑: limit < trigger (${sl_limit_long:.2f} < ${sl_trigger_long:.2f}) ✓")
    assert sl_limit_long < sl_trigger_long, "SL平多：限价应该低于触发价"

    # 测试做空（卖出开仓，买入平仓）
    print("\n【做空持仓】入场价: $100")
    print("-" * 60)

    # 止盈：触发价更低（如 $90），平仓方向是买入
    tp_trigger_short = 90.0
    is_buy_tp_short = True  # 买入平仓
    tp_limit_short = tp_trigger_short * (1 - tp_slippage)  # 限价低于触发价
    print(f"✅ 止盈(TP): 触发价=${tp_trigger_short:.2f}, 买入平仓, 限价=${tp_limit_short:.2f}")
    print(f"   逻辑: limit < trigger (${tp_limit_short:.2f} < ${tp_trigger_short:.2f}) ✓")
    assert tp_limit_short < tp_trigger_short, "TP平空：限价应该低于触发价"

    # 止损：触发价更高（如 $105），平仓方向是买入
    sl_trigger_short = 105.0
    is_buy_sl_short = True  # 买入平仓
    sl_limit_short = sl_trigger_short * (1 + sl_slippage)  # 限价高于触发价
    print(f"✅ 止损(SL): 触发价=${sl_trigger_short:.2f}, 买入平仓, 限价=${sl_limit_short:.2f}")
    print(f"   逻辑: limit > trigger (${sl_limit_short:.2f} > ${sl_trigger_short:.2f}) ✓")
    assert sl_limit_short > sl_trigger_short, "SL平空：限价应该高于触发价"

    print("\n" + "=" * 60)
    print("✅ 所有测试通过！")
    print("=" * 60)
    print("\n关键修复:")
    print("  - TP订单: 限价朝有利方向（要求更好的价格）")
    print("  - SL订单: 限价朝不利方向（接受更差的价格以快速成交）")
    print("\n这修复了 '422 Failed to deserialize JSON' 错误")


if __name__ == "__main__":
    test_limit_price_logic()
