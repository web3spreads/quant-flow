"""
网格计算数学引擎 (理科男强修版 - 彻底修复 Hyperliquid 保证金占用逻辑)
"""

from typing import Any


def calculate_grid_config(
    current_price: float,
    available_balance: float,
    mode: str = "NEUTRAL",
    width_pct: float = 0.05,
    grid_num: int = 6,
    leverage: int = 10
) -> dict[str, Any]:
    """
    针对 Hyperliquid 保证金占用逻辑进行强力修正。

    【终极修正逻辑】：
    Hyperliquid 测试网对于限价单的保证金检查极其严苛。
    如果我们有 $77 可用余额，10x 杠杆，理论总额度 $770。
    但如果一次性挂 6-8 个格子，系统会因为并行订单的潜在占用导致 Insufficient margin。

    修正方案：将单格金额大幅度压缩，确保 (单格金额 * 格子数) 远低于 (可用余额 * 杠杆)。
    """

    # 1. 计算区间 (保持原样)
    if mode == "LONG":
        lower_price = current_price * (1 - width_pct)
        upper_price = current_price * 1.01
    elif mode == "SHORT":
        lower_price = current_price * 0.99
        upper_price = current_price * (1 + width_pct)
    else: # NEUTRAL
        lower_price = current_price * (1 - width_pct/2)
        upper_price = current_price * (1 + width_pct/2)

    # 2. 【核心修正】极其保守的金额分配
    # 既然之前的 0.6 安全系数还是报错，说明测试网可能在撤单未完全释放时就预扣了新单。
    # 我们将安全系数调至 0.4，并强制限制单格金额。
    conservative_safety = 0.4

    # 总额度计算
    total_notional_cap = available_balance * leverage * conservative_safety

    # 单格金额
    amount_per_grid = total_notional_cap / grid_num

    # 强制兜底：如果算出来太大，强制压低到 25 USD 左右，这足够触发网格利润了
    if amount_per_grid > 30.0:
        amount_per_grid = 25.5

    # Hyperliquid 最小限制
    if amount_per_grid < 15.5:
        amount_per_grid = 15.5

    # 3. 计算止盈止损
    tp_ratio = round((width_pct / grid_num) * 0.8, 4)
    sl_ratio = 0.05

    return {
        "action": "UPDATE_GRID",
        "lower_price": round(lower_price, 1),
        "upper_price": round(upper_price, 1),
        "grid_num": int(grid_num),
        "amount_per_grid": round(amount_per_grid, 2),
        "tp_ratio": tp_ratio,
        "sl_ratio": sl_ratio,
        "mode": mode
    }
