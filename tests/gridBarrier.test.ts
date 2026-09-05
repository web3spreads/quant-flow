/** Triple Barrier 网格级风控测试。 */
import { describe, expect, it } from "vitest";
import { Decimal } from "../src/utils/precision.js";
import { GridBarrierMonitor, TripleBarrierConfig } from "../src/trading/gridBarrier.js";

function makeMonitor(overrides: Partial<TripleBarrierConfig> = {}): GridBarrierMonitor {
  const config = new TripleBarrierConfig();
  config.stopLossPct = new Decimal("0.05");
  config.takeProfitPct = new Decimal("0.10");
  config.timeLimitSeconds = 3600;
  config.trailingStopActivationPct = new Decimal("0.03");
  config.trailingStopDeltaPct = new Decimal("0.01");
  Object.assign(config, overrides);
  return new GridBarrierMonitor(config, 1000.0);
}

const check = (monitor: GridBarrierMonitor, pnlPct: string, now = 1100.0, price = "100") =>
  monitor.check(new Decimal(price), new Decimal(pnlPct), now);

describe("三重屏障", () => {
  it("各条屏障各自触发，安全区内不误触", () => {
    const cases: Array<[name: string, monitor: GridBarrierMonitor, pnlPct: string, now: number, price: string, expected: string | null]> = [
      ["安全状态", makeMonitor(), "0.01", 1100, "100", null],
      ["止损", makeMonitor(), "-0.06", 1100, "100", "STOP_LOSS"],
      ["止盈", makeMonitor(), "0.12", 1100, "100", "TAKE_PROFIT"],
      ["时限", makeMonitor(), "0.0", 1000 + 3601, "100", "TIME_LIMIT"],
      ["限价保护", makeMonitor({ priceLowerLimit: new Decimal("90") }), "0.0", 1100, "89", "PRICE_LIMIT"],
    ];
    for (const [name, monitor, pnlPct, now, price, expected] of cases) {
      const result = check(monitor, pnlPct, now, price);
      if (expected === null) expect(result, name).toBeNull();
      else expect(result, name).toContain(expected);
    }
  });

  it("追踪止损：达激活阈值后按高水位回撤触发，未激活时不触发", () => {
    const armed = makeMonitor();
    expect(check(armed, "0.04")).toBeNull(); // 激活追踪（高水位 4%）
    expect(check(armed, "0.05")).toBeNull(); // 高水位上移至 5%
    expect(check(armed, "0.035")).toContain("TRAILING_STOP"); // 回撤 1.5% ≥ 1%

    const idle = makeMonitor();
    expect(check(idle, "0.02")).toBeNull(); // 未达 3% 激活阈值
    expect(check(idle, "0.005")).toBeNull(); // 无高水位则无追踪止损
  });

  it("reset 清空高水位并重置计时", () => {
    const monitor = makeMonitor();
    check(monitor, "0.04");
    monitor.reset(5000.0);
    expect((monitor as never as { trailingStopHighWater: unknown }).trailingStopHighWater).toBeNull();
    expect(check(monitor, "0.0", 5100.0)).toBeNull();
  });
});

describe("配置解析", () => {
  it("缺省用默认值，显式值覆盖，显式 null 关闭该道屏障", () => {
    expect(TripleBarrierConfig.fromConfig({}).stopLossPct!.eq("0.05")).toBe(true);
    const config = TripleBarrierConfig.fromConfig({
      stop_loss_pct: 0.08,
      time_limit_seconds: 7200,
      take_profit_pct: null,
    });
    expect(config.stopLossPct!.eq("0.08")).toBe(true);
    expect(config.timeLimitSeconds).toBe(7200);
    expect(config.takeProfitPct).toBeNull(); // 显式 null = 关闭，不得回落到默认 0.10
  });
});
