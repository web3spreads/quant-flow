/** 技术指标测试：指标计算、强趋势检测与迟滞确认器。 */
import { describe, expect, it } from "vitest";
import { TechnicalIndicators, TrendConfirmTracker, detectStrongTrend } from "../src/data/indicators.js";
import { makeOhlcv } from "./support.js";

describe("TechnicalIndicators", () => {
  it("指标列与最新快照字段齐全，空数据不炸只给空对象", () => {
    const frame = TechnicalIndicators.calculateAllIndicators(makeOhlcv());
    for (const col of ["ma_7", "ma_25", "rsi", "macd", "macd_hist", "bb_upper", "bb_lower"]) {
      expect(frame.columns, `缺少指标列 ${col}`).toHaveProperty(col);
    }
    const latest = TechnicalIndicators.getLatestIndicators(frame);
    for (const key of ["current_price", "rsi", "macd_hist", "bb_upper", "bb_lower", "volume_change"]) {
      expect(latest, `缺少字段 ${key}`).toHaveProperty(key);
    }
    expect(latest.rsi).toBeGreaterThan(50); // 构造的上行序列
    // 空数据必须走空对象，而不是 NaN/undefined 混进提示词与阈值比较
    expect(TechnicalIndicators.getLatestIndicators({ rows: [], columns: {} })).toEqual({});
  });
});

describe("detectStrongTrend", () => {
  const UP = { "15分钟": "强势上涨", "1小时": "强势上涨", "4小时": "强势上涨", 日线: "震荡整理" };
  const DOWN = { "15分钟": "强势下跌", "1小时": "强势下跌", "4小时": "强势下跌" };

  it("票数达阈值才出方向，否则中性", () => {
    const cases: Array<[name: string, trends: Record<string, string> | null, threshold: number, expected: number]> = [
      ["涨票达阈值", UP, 3, 1],
      ["涨票不足", UP, 4, 0],
      ["跌票达阈值", DOWN, 3, -1],
      ["null 输入", null, 1, 0],
      ["空对象", {}, 1, 0],
    ];
    for (const [name, trends, threshold, expected] of cases) {
      expect(detectStrongTrend(trends, threshold), name).toBe(expected);
    }
  });

  it("白名单排除噪声周期后重新计票", () => {
    const trends = { "1分钟": "强势上涨", "15分钟": "强势上涨", "1小时": "强势上涨" };
    // 白名单排除 1m 后只剩 2 票，不达阈值
    expect(detectStrongTrend(trends, 3, ["15m", "1h"])).toBe(0);
  });
});

describe("TrendConfirmTracker", () => {
  it("迟滞确认逐级放行，方向翻转立即归零重来", () => {
    const tracker = new TrendConfirmTracker(2, 3);
    expect(tracker.update(1)).toEqual([0, false]); // 第 1 周期：未确认
    expect(tracker.update(1)).toEqual([1, false]); // 第 2 周期：暂停生效
    expect(tracker.update(1)).toEqual([1, true]); // 第 3 周期：允许平逆势库存

    const flip = new TrendConfirmTracker(2, 2);
    flip.update(1);
    expect(flip.update(-1)).toEqual([0, false]); // 翻转后计数从头再来
    expect(flip.update(-1)).toEqual([-1, true]);
  });

  it("flatten 门槛不低于 confirm 门槛（安全阀：不能先平仓后确认）", () => {
    const tracker = new TrendConfirmTracker(3, 1);
    // flatten_min_cycles 会被抬到 confirm_cycles：连续 3 轮才同时放行两者
    expect(tracker.update(1)).toEqual([0, false]);
    expect(tracker.update(1)).toEqual([0, false]);
    expect(tracker.update(1)).toEqual([1, true]);
  });
});
