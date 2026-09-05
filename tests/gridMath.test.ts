/** 网格数学引擎测试：区间计算、自适应仓位与资金不足拒绝。 */
import { describe, expect, it } from "vitest";
import { Decimal } from "../src/utils/precision.js";
import { GridLevel, GridLevelState, calculateGridConfig, extractOrderId } from "../src/utils/gridMath.js";

describe("calculateGridConfig", () => {
  it("区间按模式落位：NEUTRAL 对称、LONG 压在现价下方", () => {
    const neutral = calculateGridConfig({ currentPrice: 100, availableBalance: 1000, mode: "NEUTRAL", widthPct: 0.10 });
    expect(neutral.action).toBe("UPDATE_GRID");
    expect(neutral.mode).toBe("NEUTRAL");
    expect([neutral.lower_price, neutral.upper_price]).toEqual([95.0, 105.0]);

    const long = calculateGridConfig({ currentPrice: 100, availableBalance: 1000, mode: "LONG", widthPct: 0.10 });
    expect([long.lower_price, long.upper_price]).toEqual([90.0, 101.0]);
  });

  it("自适应仓位：先降格数保住单格下限，降到底仍不够就拒绝开仓", () => {
    // 总额度 = 50 × 5 × 0.4 = 100，单格最小 $11 → 只够 9 格；请求 20 格应降格数
    const shrunk = calculateGridConfig({
      currentPrice: 100, availableBalance: 50, gridNum: 20, leverage: 5, adaptiveSizing: true,
    });
    expect(shrunk.action).toBe("UPDATE_GRID");
    expect(shrunk.grid_num!).toBeLessThan(20);
    expect(shrunk.amount_per_grid!).toBeGreaterThanOrEqual(11.0);

    // $7.71 小账户：总额度 7.71 × 5 × 0.4 ≈ $15.4，连 3 格最小网格都撑不起 → 必须拒绝而非硬开
    const rejected = calculateGridConfig({
      currentPrice: 100, availableBalance: 7.71, gridNum: 6, leverage: 5, adaptiveSizing: true, minGridNum: 3,
    });
    expect(rejected.action).toBe("INSUFFICIENT_CAPITAL");
    expect(rejected.required_balance!).toBeGreaterThan(7.71);
    expect(String(rejected.reason)).toContain("资金不足");
  });

  it("非法输入被钳制而非崩溃", () => {
    const cfg = calculateGridConfig({ currentPrice: 100, availableBalance: 1000, gridNum: 0, leverage: 0 });
    expect(cfg.grid_num!).toBeGreaterThanOrEqual(1);
  });
});

describe("extractOrderId", () => {
  it("从回执各形态取出 oid，畸形输入给 null 而不是假 id", () => {
    const cases: Array<[name: string, receipt: unknown, expected: number | null]> = [
      ["resting", { response: { data: { statuses: [{ resting: { oid: 123 } }] } } }, 123],
      ["filled", { response: { data: { statuses: [{ filled: { oid: 456 } }] } } }, 456],
      ["空对象", {}, null],
      ["response 为 null", { response: null }, null],
    ];
    for (const [name, receipt, expected] of cases) {
      expect(extractOrderId(receipt), name).toBe(expected);
    }
  });
});

describe("GridLevel", () => {
  it("序列化往返不丢字段，reset 清挂单但保留累计统计", () => {
    const level = new GridLevel({ id: "L0", price: new Decimal("100.5"), amount: new Decimal("20"), side: "LONG" });
    level.state = GridLevelState.OPEN_FILLED;
    level.openFillPrice = new Decimal("100.4");
    const restored = GridLevel.fromDict(level.toDict());
    expect(restored.id).toBe("L0");
    expect(restored.price.eq("100.5")).toBe(true);
    expect(restored.state).toBe(GridLevelState.OPEN_FILLED);
    expect(restored.openFillPrice!.eq("100.4")).toBe(true);

    restored.roundTripCount = 3;
    restored.cumulativePnl = new Decimal("1.5");
    restored.openOrderId = 42;
    restored.reset();
    expect(restored.state).toBe(GridLevelState.IDLE);
    expect(restored.openOrderId).toBeNull();
    expect(restored.roundTripCount).toBe(3); // 统计保留
    expect(restored.cumulativePnl.eq("1.5")).toBe(true);
  });
});
