/**
 * Hyperliquid 市场数据获取模块
 * 使用 Info API 获取 K 线（复用交易客户端的 InfoClient，不重复建连接）。
 */

import type { ExchangeClientLike } from "../trading/client.js";
import type { TradingLogger } from "../logger.js";
import type { Candle } from "./indicators.js";
import { clock } from "../utils/clock.js";

const UNIT_SECONDS: Record<string, number> = { m: 60, h: 3600, d: 86400 };
const TF_PATTERN = /^(\d+)([mhd])$/;

/** 将 timeframe 字符串（"15m"/"1h"/"4h"/"1d"）转换为秒数；无法解析时抛错。 */
export function timeframeToSeconds(tf: string): number {
  const match = TF_PATTERN.exec(tf.trim().toLowerCase());
  if (!match) {
    throw new Error(`无法解析的 timeframe 格式: ${JSON.stringify(tf)}，期望格式如 '15m', '1h', '4h', '1d'`);
  }
  return parseInt(match[1], 10) * UNIT_SECONDS[match[2]];
}

/** Hyperliquid 市场数据获取器 */
export class MarketDataFetcher {
  constructor(
    private readonly client: ExchangeClientLike,
    private readonly logger?: TradingLogger,
  ) {}

  /**
   * 获取 OHLCV K 线数据（升序，只含已收盘 K 线）。
   *
   * 丢弃未收盘的当前 K 线：接口总会带上刚开盘的残根（只有几秒数据），
   * 用它算 RSI/MACD 等指标会引入噪声（K 线节拍触发的本意就是用刚收盘的
   * 完整 K 线决策）。K 线 t 为周期起点（UTC 毫秒）：起点+周期 > 当前时刻
   * 即未收盘。留 2s 容差吸收本机与交易所的时钟偏差。
   */
  async fetchOhlcv(symbol: string, timeframe = "15m", limit = 100): Promise<Candle[] | null> {
    try {
      const endTime = clock.now();
      const periodMs = timeframeToSeconds(timeframe) * 1000;
      const startTime = endTime - periodMs * limit;
      const candles = await this.client.getCandles(symbol, timeframe, startTime, endTime);
      if (!candles || candles.length === 0) {
        this.logger?.printWarning(`⚠️ 没有获取到 ${symbol} 的K线数据`);
        return null;
      }
      // 字段: {t: 起始毫秒, o/h/l/c/v}
      let rows: Candle[] = candles
        .filter((c) => c && c.t !== undefined)
        .map((c) => ({
          timestamp: Number(c.t),
          open: Number(c.o),
          high: Number(c.h),
          low: Number(c.l),
          close: Number(c.c),
          volume: Number(c.v),
        }))
        .filter((r) => [r.open, r.high, r.low, r.close, r.volume].every(Number.isFinite))
        .sort((a, b) => a.timestamp - b.timestamp);

      const cutoff = clock.now() + 2000;
      rows = rows.filter((r) => r.timestamp + periodMs <= cutoff);
      if (rows.length === 0) {
        this.logger?.printWarning(`⚠️ ${symbol} 无已收盘的K线数据`);
        return null;
      }
      this.logger?.printInfo(`✅ 获取 ${symbol} K线数据: ${rows.length} 条 (${timeframe})`);
      return rows;
    } catch (e) {
      this.logger?.printError(`❌ 获取K线数据失败: ${e}`);
      return null;
    }
  }

  /** 获取 Ticker 信息（当前价格等；HL 只提供中间价，bid/ask 同价）。 */
}
