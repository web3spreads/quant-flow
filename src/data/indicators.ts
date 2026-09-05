/**
 * 技术指标计算模块。
 *
 * 数值口径（全部指标一律按这几条，改动会让历史回测结论失去可比性；
 * `tests/indicatorsGolden.test.ts` 逐值钉死）：
 * - 滚动均值/标准差：窗口不满时为 NaN；标准差用**样本标准差（ddof=1）**；
 * - EMA：y[0]=x[0]，y[i]=α·x[i]+(1-α)·y[i-1]，α=2/(span+1)（无 adjust 修正）；
 * - RSI：涨跌幅用**普通滚动均值**平滑，不是 Wilder 平滑；
 * - 变化率：(v[i]-v[i-1])/v[i-1]*100，首元素 NaN。
 */

export interface Candle {
  timestamp: number; // 毫秒
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

/** 指标扩展后的 K 线表。 */
type IndicatorFrame = {
  rows: Candle[];
  /** 每列一个与 rows 等长的数组，NaN 表示窗口未满 */
  columns: Record<string, number[]>;
};

const NAN = Number.NaN;

function rollingMean(values: number[], window: number): number[] {
  const out = new Array<number>(values.length).fill(NAN);
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    sum += values[i];
    if (i >= window) sum -= values[i - window];
    if (i >= window - 1) out[i] = sum / window;
  }
  return out;
}

function rollingStd(values: number[], window: number): number[] {
  // 样本标准差（ddof=1）
  const out = new Array<number>(values.length).fill(NAN);
  for (let i = window - 1; i < values.length; i++) {
    let mean = 0;
    for (let j = i - window + 1; j <= i; j++) mean += values[j];
    mean /= window;
    let sq = 0;
    for (let j = i - window + 1; j <= i; j++) sq += (values[j] - mean) ** 2;
    out[i] = Math.sqrt(sq / (window - 1));
  }
  return out;
}

function ewm(values: number[], span: number): number[] {
  // EMA：无 adjust 修正，首值即种子
  const out = new Array<number>(values.length).fill(NAN);
  if (values.length === 0) return out;
  const alpha = 2 / (span + 1);
  out[0] = values[0];
  for (let i = 1; i < values.length; i++) {
    out[i] = alpha * values[i] + (1 - alpha) * out[i - 1];
  }
  return out;
}

function diff(values: number[]): number[] {
  const out = new Array<number>(values.length).fill(NAN);
  for (let i = 1; i < values.length; i++) out[i] = values[i] - values[i - 1];
  return out;
}

function pctChange(values: number[]): number[] {
  const out = new Array<number>(values.length).fill(NAN);
  for (let i = 1; i < values.length; i++) {
    out[i] = values[i - 1] === 0 ? NAN : ((values[i] - values[i - 1]) / values[i - 1]) * 100;
  }
  return out;
}

interface IndicatorOptions {
  maPeriods?: number[];
  rsiPeriod?: number;
  macdParams?: { fast: number; slow: number; signal: number };
  bollingerParams?: { period: number; stdDev: number };
  emaPeriods?: number[];
  atrPeriods?: number[];
}

export class TechnicalIndicators {
  /** 计算所有技术指标（ma_7 / rsi / macd_hist / bb_upper / atr_14 / volume_ma_20 ...）。 */
  static calculateAllIndicators(rows: Candle[], options: IndicatorOptions = {}): IndicatorFrame {
    const maPeriods = options.maPeriods ?? [7, 25, 99];
    const rsiPeriod = options.rsiPeriod ?? 14;
    const macd = options.macdParams ?? { fast: 12, slow: 26, signal: 9 };
    const bb = options.bollingerParams ?? { period: 20, stdDev: 2.0 };
    const emaPeriods = options.emaPeriods ?? [20, 50];
    const atrPeriods = options.atrPeriods ?? [3, 14];

    const close = rows.map((r) => r.close);
    const high = rows.map((r) => r.high);
    const low = rows.map((r) => r.low);
    const volume = rows.map((r) => r.volume);
    const columns: Record<string, number[]> = {};

    // MA（简单移动平均）
    for (const p of maPeriods) columns[`ma_${p}`] = rollingMean(close, p);
    // EMA
    for (const p of emaPeriods) columns[`ema_${p}`] = ewm(close, p);

    // RSI = 100 - (100 / (1 + RS))，RS = 平均涨幅 / 平均跌幅（rolling mean，见文件头说明）
    {
      const delta = diff(close);
      const gain = delta.map((v) => (Number.isNaN(v) ? NAN : v > 0 ? v : 0));
      const loss = delta.map((v) => (Number.isNaN(v) ? NAN : v < 0 ? -v : 0));
      // 窗口内含 NaN 则结果为 NaN；delta[0]=NaN，因此前 rsiPeriod 个值为 NaN。
      const avgGain = rollingMeanWithNan(gain, rsiPeriod);
      const avgLoss = rollingMeanWithNan(loss, rsiPeriod);
      const rsi = avgGain.map((g, i) => {
        const l = avgLoss[i];
        if (Number.isNaN(g) || Number.isNaN(l)) return NAN;
        const rs = g / l; // l=0 时 rs=Infinity → rsi=100
        return 100 - 100 / (1 + rs);
      });
      columns.rsi = rsi;
    }

    // MACD
    {
      const emaFast = ewm(close, macd.fast);
      const emaSlow = ewm(close, macd.slow);
      const macdLine = emaFast.map((v, i) => v - emaSlow[i]);
      const signal = ewm(macdLine, macd.signal);
      columns.macd = macdLine;
      columns.macd_signal = signal;
      columns.macd_hist = macdLine.map((v, i) => v - signal[i]);
    }

    // 布林带
    {
      const middle = rollingMean(close, bb.period);
      const std = rollingStd(close, bb.period);
      columns.bb_middle = middle;
      columns.bb_upper = middle.map((m, i) => (Number.isNaN(m) ? NAN : m + bb.stdDev * std[i]));
      columns.bb_lower = middle.map((m, i) => (Number.isNaN(m) ? NAN : m - bb.stdDev * std[i]));
    }

    // ATR（EMA 平滑）
    {
      const tr = rows.map((_row, i) => {
        const highLow = high[i] - low[i];
        if (i === 0) return highLow; // 首行无前收，真实波幅退化为 high-low
        const highClose = Math.abs(high[i] - close[i - 1]);
        const lowClose = Math.abs(low[i] - close[i - 1]);
        return Math.max(highLow, highClose, lowClose);
      });
      for (const p of atrPeriods) columns[`atr_${p}`] = ewm(tr, p);
    }

    // 成交量指标
    columns.volume_ma_20 = rollingMean(volume, 20);
    columns.volume_change = pctChange(volume);

    return { rows, columns };
  }

  /** 获取最新的指标数据（最后一行），NaN 按安全默认值兜底。 */
  static getLatestIndicators(frame: IndicatorFrame): Record<string, number> {
    if (frame.rows.length === 0) return {};
    const i = frame.rows.length - 1;
    const last = frame.rows[i];
    const col = (name: string): number => frame.columns[name]?.[i] ?? NAN;
    const out: Record<string, number> = {
      timestamp: last.timestamp,
      current_price: last.close,
      open: last.open,
      high: last.high,
      low: last.low,
      volume: last.volume,
    };

    // MA：NaN 时用当前价格替代
    for (const name of Object.keys(frame.columns)) {
      if (!name.startsWith("ma_")) continue;
      const v = col(name);
      out[name] = Number.isNaN(v) ? last.close : v;
    }
    // RSI：NaN 时用中性值 50
    if ("rsi" in frame.columns) {
      const v = col("rsi");
      out.rsi = Number.isNaN(v) ? 50.0 : v;
    }
    // MACD：NaN 时用 0
    if ("macd" in frame.columns) {
      for (const name of ["macd", "macd_signal", "macd_hist"]) {
        const v = col(name);
        out[name] = Number.isNaN(v) ? 0.0 : v;
      }
    }
    // 布林带：NaN 时所有轨道用当前价、位置取中性 0.5
    if ("bb_upper" in frame.columns) {
      const middle = col("bb_middle");
      if (Number.isNaN(middle)) {
        out.bb_upper = last.close;
        out.bb_middle = last.close;
        out.bb_lower = last.close;
        out.bb_position = 0.5;
      } else {
        const upper = col("bb_upper");
        const lower = col("bb_lower");
        out.bb_upper = upper;
        out.bb_middle = middle;
        out.bb_lower = lower;
        const range = upper - lower;
        out.bb_position = range > 0 && !Number.isNaN(range) ? (last.close - lower) / range : 0.5;
      }
    }
    // 成交量：均线 NaN 用当前量；变化率 NaN/Inf 用 0
    if ("volume_ma_20" in frame.columns) {
      const vma = col("volume_ma_20");
      out.volume_ma_20 = Number.isNaN(vma) ? last.volume : vma;
      const vc = col("volume_change");
      out.volume_change = Number.isNaN(vc) || !Number.isFinite(vc) ? 0.0 : vc;
    }
    return out;
  }

  /**
   * 分析趋势方向。
   * 返回：强势上涨 / 上涨转弱 / 强势下跌 / 下跌转强 / 震荡 / 数据不足
   */
  static analyzeTrend(frame: IndicatorFrame, maShort = 7, maLong = 25): string {
    const n = frame.rows.length;
    if (n === 0 || n < Math.max(maShort, maLong)) return "数据不足";
    const i = n - 1;
    const maS = frame.columns[`ma_${maShort}`]?.[i];
    const maL = frame.columns[`ma_${maLong}`]?.[i];
    const price = frame.rows[i].close;
    if (maS === undefined || maL === undefined || Number.isNaN(maS) || Number.isNaN(maL)) {
      return "数据不足";
    }
    if (price > maS && maS > maL) return "强势上涨";
    if (price > maS && maS < maL) return "上涨转弱";
    if (price < maS && maS < maL) return "强势下跌";
    if (price < maS && maS > maL) return "下跌转强";
    return "震荡";
  }

  /** 获取多时间周期趋势（键为中文周期名）。 */
  static async getMultiTimeframeTrend(
    fetcher: { fetchOhlcv(symbol: string, timeframe: string, limit: number): Promise<Candle[] | null> },
    symbol: string,
    cachedOhlcv?: Record<string, Candle[]>,
  ): Promise<Record<string, string>> {
    const timeframes: Record<string, string> = {
      "1d": "日线", "4h": "4小时", "1h": "1小时", "15m": "15分钟", "1m": "1分钟",
    };
    const trends: Record<string, string> = {};
    const cache = cachedOhlcv ?? {};
    for (const [tf, tfName] of Object.entries(timeframes)) {
      try {
        // 优先复用已获取的数据，避免重复请求同一时间周期
        let rows: Candle[] | null | undefined = cache[tf];
        if (!rows) rows = await fetcher.fetchOhlcv(symbol, tf, 100);
        if (!rows || rows.length === 0) {
          trends[tfName] = "无数据";
          continue;
        }
        const frame = TechnicalIndicators.calculateAllIndicators(rows, {
          maPeriods: [7, 25], emaPeriods: [], atrPeriods: [],
        });
        trends[tfName] = TechnicalIndicators.analyzeTrend(frame);
      } catch {
        trends[tfName] = "获取失败";
      }
    }
    return trends;
  }
}

/** 滚动均值（窗口内含 NaN 时结果为 NaN，供 RSI 用）。 */
function rollingMeanWithNan(values: number[], window: number): number[] {
  const out = new Array<number>(values.length).fill(NAN);
  for (let i = window - 1; i < values.length; i++) {
    let sum = 0;
    let bad = false;
    for (let j = i - window + 1; j <= i; j++) {
      if (Number.isNaN(values[j])) { bad = true; break; }
      sum += values[j];
    }
    if (!bad) out[i] = sum / window;
  }
  return out;
}

// ── 强趋势检测（网格趋势过滤用，纯函数便于单测）─────────────────────────

/**
 * 效率比（Kaufman Efficiency Ratio）：净位移 / 路程。
 *
 * ER = |P_t − P_{t−n}| / Σ|P_i − P_{i−1}|，取值 0–1。
 * 接近 0 = 来回震荡（路走了很多、位置没变，网格的理想市况）；
 * 接近 1 = 单边直线（每一步都朝同一个方向，网格必然累积逆势库存）。
 *
 * 选它而不是趋势票数：ER 直接度量「均值回归 vs 趋势」这一个网格真正在意的属性，
 * 且是连续值、无参数、跨品种跨周期同尺度。回测按月拆开显示网格盈亏与行情形态
 * 几乎完全相关（震荡月稳定赚、趋势月稳定亏），形态判别因此是收益的主要杠杆。
 *
 * 数据不足（少于 2 根或路程为 0）返回 null——调用方必须区分「算不出」与「低 ER」，
 * 把算不出当震荡会在数据异常时放行布单。
 */
export function efficiencyRatio(rows: Candle[], lookback: number): number | null {
  const n = Math.max(2, Math.trunc(lookback));
  if (rows.length < 2) return null;
  const slice = rows.slice(-Math.min(n, rows.length));
  if (slice.length < 2) return null;
  let path = 0;
  for (let i = 1; i < slice.length; i++) path += Math.abs(slice[i].close - slice[i - 1].close);
  if (!(path > 0)) return null;
  return Math.abs(slice[slice.length - 1].close - slice[0].close) / path;
}

/** 英文周期名 → getMultiTimeframeTrend 输出的中文键 */
const TREND_TIMEFRAME_ALIASES: Record<string, string> = {
  "1d": "日线", "4h": "4小时", "1h": "1小时", "15m": "15分钟", "1m": "1分钟",
};

/**
 * 从多周期趋势判断是否存在「一致强势」趋势。
 *
 * 仅把 analyzeTrend 的两个最强状态（强势上涨/强势下跌）计票，避免在震荡/转折市
 * 误判（保守取向：宁可不拦，也不在震荡里错停网格）。票数达到 minVotes 且占优
 * 方向明确时返回 ±1。
 *
 * allowedTimeframes：参与计票的周期白名单（支持英文 "1m" 或中文 "1分钟"）。
 * 空/缺省 = 全部周期参与（历史行为）。用于排除 1m 等噪声周期。
 */
export function detectStrongTrend(
  trends: Record<string, string> | null | undefined,
  minVotes: number,
  allowedTimeframes?: string[] | null,
): number {
  if (!trends) return 0;
  let entries = Object.entries(trends);
  if (allowedTimeframes && allowedTimeframes.length > 0) {
    const allowed = new Set(allowedTimeframes.map((tf) => TREND_TIMEFRAME_ALIASES[tf] ?? tf));
    entries = entries.filter(([k]) => allowed.has(k));
  }
  const up = entries.filter(([, v]) => v === "强势上涨").length;
  const down = entries.filter(([, v]) => v === "强势下跌").length;
  if (up >= minVotes && up > down) return 1;
  if (down >= minVotes && down > up) return -1;
  return 0;
}

/**
 * 趋势连续确认器（迟滞去抖）。
 *
 * 单周期的强趋势判定噪声很大——线上 12.5 天里趋势过滤强平 145 次，多数由
 * 瞬时误判触发。本类要求同向信号连续出现 N 个周期才放行动作：
 * confirmCycles 控制「暂停加仓」生效门槛，flattenMinCycles 控制
 * 「市价平逆势库存」生效门槛（更高，让暂停先行、平仓靠后）。
 * 方向翻转或消失时计数即归零，无跨方向记忆。
 */
export class TrendConfirmTracker {
  private readonly confirmCycles: number;
  private readonly flattenMinCycles: number;
  private streakDir = 0;
  private streakCount = 0;

  constructor(confirmCycles = 1, flattenMinCycles = 1) {
    this.confirmCycles = Math.max(1, Math.trunc(confirmCycles));
    this.flattenMinCycles = Math.max(this.confirmCycles, Math.trunc(flattenMinCycles));
  }

  /** 输入本周期原始趋势方向，返回 [生效方向, 是否允许平逆势库存]。 */
  update(rawDir: number): [number, boolean] {
    if (rawDir === 0) {
      this.streakDir = 0;
      this.streakCount = 0;
      return [0, false];
    }
    if (rawDir === this.streakDir) this.streakCount += 1;
    else {
      this.streakDir = rawDir;
      this.streakCount = 1;
    }
    const effective = this.streakCount >= this.confirmCycles ? rawDir : 0;
    const flattenAllowed = this.streakCount >= this.flattenMinCycles;
    return [effective, flattenAllowed];
  }

  /** 当前连续方向与计数（调试/日志用）。 */
  get streak(): [number, number] {
    return [this.streakDir, this.streakCount];
  }
}
