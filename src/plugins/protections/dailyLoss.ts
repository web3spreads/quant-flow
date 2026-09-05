/**
 * 单日亏损保护插件
 * 当日亏损超过阈值时暂停新开仓。每日自动重置基准净值。
 */

import { IProtection, ProtectionAction, protectionReturn, type ProtectionContext, type ProtectionInit, type ProtectionReturn } from "./base.js";

export class DailyLossProtection extends IProtection {
  private dailyStartEquity = 0.0;
  private dailyStartDate = "";
  private isPaused = false;
  private pauseReason = "";
  private lastProtectionTime: number | null = null;

  constructor(options: ProtectionInit) {
    super("daily_loss", options);
  }

  check(context: ProtectionContext): ProtectionReturn {
    const maxDailyLossPct = Number(this.config.max_daily_loss_pct ?? 0.05);
    const pauseHours = Number(this.config.pause_hours ?? 4.0);

    // 净值非法守卫：equity<=0 会污染日初基准并算出错误日亏损率，跳过本次检查
    if (context.equity <= 0) {
      this.logger.warn(`单日亏损保护跳过：净值非法 (${context.equity.toFixed(4)})`);
      return protectionReturn({ triggered: false });
    }

    // 「日」边界统一取 UTC：系统其余节拍（K 线对齐、交易所结算）都是 UTC，
    // 用宿主机本地时区会让日亏损基准在错误的时刻重置
    const today = new Date(context.timestamp).toISOString().slice(0, 10);

    // 判断当前暂停是否仍在 pause_hours 冷却期内：跨天也必须遵守该冷却，
    // 不能因日期翻转（如 23:00 触发、00:00 跨天）提前解除暂停。
    let pauseStillActive = false;
    if (this.isPaused && this.lastProtectionTime !== null) {
      const elapsedH = (context.timestamp - this.lastProtectionTime) / 3_600_000;
      pauseStillActive = elapsedH < pauseHours;
    }

    // 新的一天，重置日亏损基准；但暂停冷却未到期时保留暂停状态
    if (today !== this.dailyStartDate) {
      this.dailyStartEquity = context.equity;
      this.dailyStartDate = today;
      if (!pauseStillActive) {
        this.isPaused = false;
        this.pauseReason = "";
      }
    }

    // 首次检查时设置基准
    if (this.dailyStartEquity <= 0) {
      this.dailyStartEquity = context.equity;
      this.saveState();
      return protectionReturn({ triggered: false });
    }

    // 检查暂停期是否已过
    if (this.isPaused && this.lastProtectionTime !== null) {
      const elapsed = (context.timestamp - this.lastProtectionTime) / 3_600_000;
      if (elapsed >= pauseHours) {
        this.isPaused = false;
        this.pauseReason = "";
        this.logger.info("单日亏损保护暂停期已过，恢复交易");
      }
    }

    if (this.isPaused) {
      return protectionReturn({
        triggered: true,
        action: ProtectionAction.PAUSE_NEW_TRADES,
        reason: this.pauseReason,
        shouldPause: true,
      });
    }

    const dailyLossPct = (this.dailyStartEquity - context.equity) / this.dailyStartEquity;
    if (dailyLossPct >= maxDailyLossPct) {
      const reason =
        `单日亏损保护触发: 日亏损 ${(dailyLossPct * 100).toFixed(1)}% >= 阈值 ${(maxDailyLossPct * 100).toFixed(1)}% ` +
        `(日初 $${this.dailyStartEquity.toFixed(2)} → 当前 $${context.equity.toFixed(2)})`;
      this.isPaused = true;
      this.pauseReason = reason;
      this.lastProtectionTime = context.timestamp;
      this.saveState();
      return protectionReturn({
        triggered: true,
        action: ProtectionAction.PAUSE_NEW_TRADES,
        reason,
        shouldPause: true,
        details: { daily_loss_pct: dailyLossPct, daily_start_equity: this.dailyStartEquity, current_equity: context.equity },
      });
    }

    this.saveState();
    return protectionReturn({ triggered: false });
  }

  protected override resetState(): void {
    this.dailyStartEquity = 0.0;
    this.dailyStartDate = "";
    this.isPaused = false;
    this.pauseReason = "";
    this.lastProtectionTime = null;
  }

  protected override getStateDict(): Record<string, unknown> {
    return {
      daily_start_equity: this.dailyStartEquity,
      daily_start_date: this.dailyStartDate,
      is_paused: this.isPaused,
      pause_reason: this.pauseReason,
      last_protection_time: this.lastProtectionTime === null ? null : new Date(this.lastProtectionTime).toISOString(),
    };
  }

  protected override restoreStateDict(state: Record<string, unknown>): void {
    this.dailyStartEquity = Number(state.daily_start_equity ?? 0);
    this.dailyStartDate = String(state.daily_start_date ?? "");
    this.isPaused = !!state.is_paused;
    this.pauseReason = String(state.pause_reason ?? "");
    const lpt = state.last_protection_time;
    this.lastProtectionTime = lpt ? Date.parse(String(lpt)) : null;
  }
}
