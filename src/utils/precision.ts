/**
 * Decimal 精度工具函数
 *
 * 核心计算路径使用 Decimal（decimal.js），仅在 API 调用边界转为 number。
 */

import { Decimal } from "decimal.js";

// 28 位有效数字：长链路乘除不因精度截断产生口径差
Decimal.set({ precision: 28 });

export { Decimal };

/** 安全转换为 Decimal，避免 number 直接构造导致精度污染（一律经 String 中转）。 */
export function toDecimal(value: unknown, defaultValue = "0"): Decimal {
  if (value instanceof Decimal) return value;
  if (value === null || value === undefined) return new Decimal(defaultValue);
  try {
    const d = new Decimal(String(value));
    if (!d.isFinite()) return new Decimal(defaultValue);
    return d;
  } catch {
    return new Decimal(defaultValue);
  }
}
