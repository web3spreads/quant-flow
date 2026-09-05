/** 指标数值对拍：与金标准逐值比对（相对误差 1e-9）。
 *
 * 金标准按 indicators.ts 声明的数值口径（标准差 ddof=1、EMA 无 adjust 修正、
 * RSI 用普通滚动均值）算出，输入为 makeOhlcv() 的 100 根线性上行 K 线。
 * 任何一项口径漂移（如把标准差写成总体标准差、EMA 初始化不同）都会在此失败——
 * 口径变了，历史回测结论就不再可比。
 */
import { describe, expect, it } from "vitest";
import { TechnicalIndicators } from "../src/data/indicators.js";
import { makeOhlcv } from "./support.js";

const GOLDEN: Record<string, number> = {
  "ma_7": 148.0,
  "ma_25": 143.5,
  "ma_99": 125.0,
  "ema_20": 144.75023636867752,
  "ema_50": 137.48339985280973,
  "rsi": 100.0,
  "macd": 3.4969316658281855,
  "macd_signal": 3.495488139448719,
  "macd_hist": 0.00144352637946632,
  "bb_upper": 150.6660797830996,
  "bb_middle": 144.75,
  "bb_lower": 138.8339202169004,
  "atr_3": 1.0,
  "atr_14": 1.0,
  "volume_ma_20": 1089.5,
  "volume_change": 0.09107468123861207
};

describe("指标数值对拍", () => {
  const frame = TechnicalIndicators.calculateAllIndicators(makeOhlcv());
  const last = frame.rows.length - 1;
  for (const [name, expected] of Object.entries(GOLDEN)) {
    it(name, () => {
      const actual = frame.columns[name]?.[last];
      expect(actual, name + " 列缺失").toBeDefined();
      const tol = Math.max(Math.abs(expected) * 1e-9, 1e-9);
      expect(Math.abs((actual as number) - expected), name + ": " + actual + " vs " + expected).toBeLessThanOrEqual(tol);
    });
  }
});
