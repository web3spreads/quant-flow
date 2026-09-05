/** HyperliquidClient 单元测试（离线）：价格/数量精度、部分平仓钳制、紧急平仓兜底。
 *
 * 构造方式：绕过构造函数（会建真实签名钱包），用 Object.create + 手工装配
 * 最小属性集，逐方法打桩。
 */
import { describe, expect, it } from "vitest";
import { HyperliquidClient, type Dict } from "../src/trading/client.js";

const FILLED_ORDER: Dict = {
  status: "ok",
  response: { type: "order", data: { statuses: [{ filled: { avgPx: "100.0", totalSz: "0.5", oid: 1 } }] } },
};
const REJECTED_ORDER: Dict = {
  status: "ok",
  response: { type: "order", data: { statuses: [{ error: "Order rejected" }] } },
};

function makeClient(szDecimals = 3): HyperliquidClient {
  const client = Object.create(HyperliquidClient.prototype) as HyperliquidClient;
  (client as Dict).assetInfoCache = new Map();
  (client as Dict).getAssetInfo = async () => ({ szDecimals });
  (client as Dict).logger = undefined;
  return client;
}

describe("下单精度", () => {
  // 价格：5 位有效数字，且小数位不超过 6 - szDecimals。
  // 低价币那条防的是历史缺陷——曾把所有价格拍到 0.1 tick，DOGE 的网格层级会撞成同一价。
  it.each([
    [4, "BTC", 94283.7, 94284.0, "高价取 5 位有效数字"],
    [4, "ETH", 1872.34, 1872.3, "中价保留一位小数"],
    [0, "DOGE", 0.123456, 0.12346, "低价币不被拍扁到 0.1 tick"],
    [2, "XYZ", 0.123456, 0.1235, "小数位受 szDecimals 限制"],
  ])("formatPrice szDecimals=%s %s: %s → %s（%s）", async (dec, sym, input, want) => {
    expect(await makeClient(dec as number).formatPrice(sym as string, input as number)).toBe(want);
  });

  it("roundSize 向下取整而非四舍五入（进位=放大敞口）", async () => {
    const client = makeClient(3);
    expect(await client.roundSize("ETH", 0.0035)).toBe(0.003);
    expect(await client.roundSize("ETH", 0.9999)).toBe(0.999);
    expect(await client.roundSize("ETH", 0.5)).toBe(0.5);
  });
});

describe("closePosition", () => {
  function clientWithPosition(positions: Dict[] | null): { client: HyperliquidClient; orders: Dict[] } {
    const client = makeClient(3);
    (client as Dict).getPositions = async () => positions;
    (client as Dict).getCurrentPrice = async () => 100.0;
    (client as Dict).assetIndex = async () => 4;
    const orders: Dict[] = [];
    (client as Dict).exchange = {
      order: async (params: Dict) => {
        orders.push(params);
        return FILLED_ORDER;
      },
    };
    return { client, orders };
  }

  it("平仓量钳制到实际持仓（超量平仓会反向开新仓）", async () => {
    const { client, orders } = clientWithPosition([{ coin: "ETH", szi: "0.3" }]);
    const result = await client.closePosition("ETH", 0.5);
    expect(result.status).toBe("ok");
    const order = orders[0].orders[0];
    expect(order.r).toBe(true); // reduce-only 语义，绝不反向开仓
    expect(order.t).toEqual({ limit: { tif: "Ioc" } });
    expect(Number(order.s)).toBe(0.3);
  });

  // 查不到（null）与确认没有（[]）都不能下单，但语义不同：前者是故障，后者是事实。
  it.each([
    [null, "持仓查询失败"],
    [[], "确认无持仓"],
  ])("%#: %s 时不下单并返回 error", async (positions) => {
    const { client, orders } = clientWithPosition(positions as Dict[] | null);
    const result = await client.closePosition("ETH", 0.5);
    expect(result.status).toBe("error");
    expect(orders).toEqual([]);
  });
});

describe("emergencyCloseWithRetry", () => {
  it("按量平仓连续失败后退化为市价全平兜底", async () => {
    const client = makeClient();
    const attempts: Array<number | null> = [];
    (client as Dict).closePosition = async (_s: string, size: number | null = null) => {
      attempts.push(size);
      return size === null ? FILLED_ORDER : REJECTED_ORDER;
    };
    const [ok] = await client.emergencyCloseWithRetry("ETH", 0.5, { reason: "测试", maxRetries: 2 });
    expect(ok).toBe(true);
    expect(attempts).toEqual([0.5, 0.5, null]);
  });

  // 历史缺陷 `if result:`：错误字典也是真值，平仓失败被记成成功，风控随后清掉持仓记录。
  it.each([
    [REJECTED_ORDER, "外层 ok 但内层 statuses 有 error"],
    [{ status: "error", message: "网络异常" }, "错误字典"],
  ])("%#: %s 一律判失败", async (ret) => {
    const client = makeClient();
    (client as Dict).closePosition = async () => ret;
    const [ok] = await client.emergencyCloseWithRetry("ETH", null, { reason: "测试", maxRetries: 1 });
    expect(ok).toBe(false);
  });
});
