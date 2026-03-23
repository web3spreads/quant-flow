"""
Decimal 精度工具函数

核心计算路径使用 Decimal，仅在 API 调用边界转为 float。
"""

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal


def to_decimal(value, default: str = "0") -> Decimal:
    """安全转换为 Decimal，避免 float 直接构造导致精度污染。"""
    if isinstance(value, Decimal):
        return value
    if value is None:
        return Decimal(default)
    try:
        # 用 str 中转避免 float 精度污染
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def quantize_price(price: Decimal, tick_size: Decimal) -> Decimal:
    """将价格对齐到 tick_size 精度（四舍五入）。"""
    if tick_size <= 0:
        return price
    return (price / tick_size).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick_size


def quantize_size(size: Decimal, step_size: Decimal) -> Decimal:
    """将数量对齐到 step_size 精度（向下取整，避免超额）。"""
    if step_size <= 0:
        return size
    return (size / step_size).quantize(Decimal("1"), rounding=ROUND_DOWN) * step_size
