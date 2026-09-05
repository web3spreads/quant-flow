/**
 * 模拟交易所与回测运行器测试（全离线、确定性）。
 *
 * 只留「错了会赔钱或让回测结论失真」的不变量：撮合保守性、reduce_only 语义、
 * 资金费与强平、时钟不泄露未来，以及回测端到端的两条结论。
 */
import { describe, expect, it } from "vitest";
import { SimulatedClient } from "../src/sim/simulatedClient.js";
import { TRIGGER_EXIT_PRESET, runBacktest } from "../src/sim/backtest.js";
import { syntheticBars, type Bar } from "../src/sim/dataset.js";
import { HyperliquidClient } from "../src/trading/client.js";
import { clock } from "../src/utils/clock.js";

const FEES = { makerRate: 0.00015, takerRate: 0.00045 };

function flatBars(count: number, price = 100_000, intervalMs = 300e3): Bar[] {
  const start = Date.parse("2026-01-01T00:00:00Z");
  return Array.from({ length: count }, (_, i) => ({ t: start + i * intervalMs, o: price, h: price, l: price, c: price, v: 1 }));
}

function makeSim(bars: Bar[], extra: Partial<ConstructorParameters<typeof SimulatedClient>[0]> = {}): SimulatedClient {
  return new SimulatedClient({ symbol: "BTC", bars, initialEquity: 10_000, feeRates: FEES, startIndex: 0, ...extra });
}

describe("SimulatedClient 撮合语义", () => {
  it("撮合保守性：post-only 穿价被拒 / Gtc 穿价按市价 taker / 挂单只在下一根严格穿过时按挂单价 maker 成交", async () => {
    const bars = flatBars(5);
    bars[2] = { ...bars[2], l: 99_400 }; // 触及挂单价：严格穿过口径下不成交
    bars[3] = { ...bars[3], l: 99_300 }; // 严格穿过：成交
    const sim = makeSim(bars);

    const alo = await sim.placeLimitOrder("BTC", true, 0.01, 100_500, false, "Alo");
    const [ok, err] = HyperliquidClient.checkOrderSuccess(alo);
    expect(ok).toBe(false);
    expect(HyperliquidClient.isPostOnlyRejection(err)).toBe(true);
    expect(sim.stats.postOnlyRejections).toBe(1);

    const gtc = await sim.placeLimitOrder("BTC", true, 0.01, 100_500, false, "Gtc");
    const view = HyperliquidClient.orderStatuses(gtc, 1)[0];
    expect(view.filled).toBe(true);
    expect(view.avgPx).toBe(100_000); // 穿价成交在市价，不在挂单价
    expect(sim.stats.takerFills).toBe(1);
    expect(sim.stats.takerFees).toBeCloseTo(0.01 * 100_000 * FEES.takerRate, 8);
    expect(sim.positionOf().szi).toBeCloseTo(0.01, 8);

    const resting = await sim.placeLimitOrder("BTC", true, 0.01, 99_400, false, "Alo");
    expect(HyperliquidClient.orderStatuses(resting, 1)[0].resting).toBe(true);
    sim.advance(); // 平盘
    sim.advance(); // low == 99_400：触及不成交
    expect(sim.openOrderCount()).toBe(1);
    sim.advance(); // low 99_300 < 99_400：成交
    expect(sim.openOrderCount()).toBe(0);
    expect(sim.positionOf().szi).toBeCloseTo(0.02, 8);
    expect(sim.stats.makerFills).toBe(1);
    const fills = await sim.userFills();
    expect(Number(fills[0].px)).toBe(99_400); // 成交价=挂单价
    expect(fills[0].crossed).toBe(false);
    expect(fills[0].dir).toBe("Open Long");
  });

  it("reduce_only：无反向持仓即拒；平仓成交记 closedPnl 且钳制到持仓", async () => {
    const bars = flatBars(4);
    bars[1] = { ...bars[1], h: 101_000 };
    const sim = makeSim(bars);

    const rejected = await sim.placeLimitOrder("BTC", false, 0.01, 100_500, true, "Alo");
    expect(HyperliquidClient.checkOrderSuccess(rejected)[1]).toContain("Reduce only");

    // 穿价 Gtc 建多 0.01 @ 100_000
    await sim.placeLimitOrder("BTC", true, 0.01, 100_500, false, "Gtc");
    const close = await sim.placeLimitOrder("BTC", false, 0.05, 100_500, true, "Alo"); // 超量 reduce_only
    expect(HyperliquidClient.orderStatuses(close, 1)[0].resting).toBe(true);
    sim.advance();
    expect(sim.positionOf().szi).toBe(0);
    const fills = await sim.userFills();
    expect(fills[0].dir).toBe("Close Long");
    expect(Number(fills[0].sz)).toBeCloseTo(0.01, 8); // 钳制到持仓，不会反手开空
    expect(Number(fills[0].closedPnl)).toBeCloseTo((100_500 - 100_000) * 0.01, 8);
    expect(sim.stats.realizedPnl).toBeCloseTo(Number(fills[0].closedPnl), 8);
  });

  it("资金费按小时结算（正费率多头付）；权益跌破维持保证金即强平停机", async () => {
    const bars = flatBars(16);
    bars[14] = { ...bars[14], o: 90_000, h: 90_000, l: 90_000, c: 90_000 };
    bars[15] = { ...bars[15], o: 90_000, h: 90_000, l: 90_000, c: 90_000 };
    const start = bars[0].t;
    const sim = makeSim(bars, {
      initialEquity: 300,
      defaultLeverage: 40,
      funding: [{ time: start + 3600e3, fundingRate: 0.0001 }],
    });
    await sim.updateLeverage("BTC", 40, false);
    await sim.placeLimitOrder("BTC", true, 0.1, 100_500, false, "Gtc"); // 名义 $10k，保证金 $251

    const cashBefore = sim.cash;
    for (let i = 0; i < 13; i++) expect(sim.advance()).toBe(true); // 跨过整点，行情仍是平盘
    expect(cashBefore - sim.cash).toBeCloseTo(0.1 * 100_000 * 0.0001, 6);
    expect(sim.stats.fundingPaid).toBeCloseTo(1, 6);
    expect(sim.liquidated).toBe(false);

    expect(sim.advance()).toBe(true); // 砸到 90_000：维持保证金不足
    expect(sim.liquidated).toBe(true);
    expect(sim.stats.liquidations).toBe(1);
    expect(sim.positionOf().szi).toBe(0);
    expect(sim.openOrderCount()).toBe(0);
    expect(sim.advance()).toBe(false); // 强平后停机
  });

  it("时钟接管：now = 最后已收盘 K 线收盘时刻 + 1s；getCandles 不泄露未来", async () => {
    const bars = flatBars(20, 100_000, 300e3);
    const sim = makeSim(bars, { startIndex: 5 });
    const restore = sim.install();
    try {
      expect(clock.now()).toBe(bars[5].t + 300e3 + 1000);
      const candles = (await sim.getCandles("BTC", "5m", 0, clock.now())) ?? [];
      expect(candles.length).toBe(6);
      expect(Math.max(...candles.map((c) => Number(c.t)))).toBe(bars[5].t);
      const hourly = (await sim.getCandles("BTC", "1h", 0, clock.now())) ?? [];
      expect(hourly.length).toBe(0); // 首个小时桶尚未收盘
    } finally {
      restore();
    }
    expect(clock.now()).toBeGreaterThan(Date.parse("2026-01-01T00:00:00Z") + 86400e3);
  });
});

describe("回测端到端（合成行情）", () => {
  // 纯正弦震荡=网格的理想市况。根数取「结论仍成立的最小规模」——一次回测按根计价，很贵。
  const bars = syntheticBars({ kind: "sine", count: 300, intervalMs: 300e3, amplitudePct: 0.02, periodBars: 48, seed: 7 });
  const BASE = { symbol: "BTC", bars, initialEquity: 1000, feeRates: FEES, warmupBars: 100, gridNum: 8 };
  // 两条用例共用同一次「当前配置」回测：结果确定性，跑一次就够
  let currentRun: ReturnType<typeof runBacktest> | undefined;
  const runCurrent = (): ReturnType<typeof runBacktest> => (currentRun ??= runBacktest(BASE));

  it("震荡行情：当前默认配置正收益、零 taker、无强平", async () => {
    const r = await runCurrent();
    expect(r.liquidations).toBe(0);
    expect(r.fills.taker).toBe(0); // post-only 下不该有任何 taker 成交
    expect(r.fills.maker).toBeGreaterThan(10);
    expect(r.rebuilds).toBeGreaterThan(0);
    expect(r.finalEquity).toBeGreaterThan(1000);
  }, 60_000);

  it("触发单退出对照组（层级触发单 / Gtc / 4h 时限）：taker 成交出现，收益更差", async () => {
    const current = await runCurrent();
    const triggers = await runBacktest({ ...BASE, config: TRIGGER_EXIT_PRESET });
    expect(triggers.fills.taker).toBeGreaterThan(0);
    expect(triggers.forcedCloses + triggers.fills.taker).toBeGreaterThan(0);
    expect(triggers.finalEquity).toBeLessThan(current.finalEquity);
  }, 60_000);
});
