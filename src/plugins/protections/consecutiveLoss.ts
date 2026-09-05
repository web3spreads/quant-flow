/**
 * 连续亏损保护插件
 * 连续亏损次数达到阈值时暂停交易。支持全局模式和交易对级锁定模式。
 */

import { IProtection, ProtectionAction, protectionReturn, type ProtectionContext, type ProtectionInit, type ProtectionReturn } from "./base.js";
import { clock } from "../../utils/clock.js";

export class ConsecutiveLossProtection extends IProtection {
  private globalLosses = 0;
  private symbolLosses: Record<string, number> = {};
  private lockedSymbols: Record<string, string> = {}; // symbol -> 锁定截止时间 ISO
  private isPaused = false;
  private pauseReason = "";
  private lastProtectionTime: number | null = null;

  constructor(options: ProtectionInit) {
    super("consecutive_loss", options);
  }

  check(context: ProtectionContext): ProtectionReturn {
    const maxLosses = Number(this.config.max_consecutive_losses ?? 5);
    const perSymbol = !!this.config.per_symbol;
    const pauseHours = Number(this.config.pause_hours ?? 4.0);

    // 检查暂停期是否已过
    if (this.isPaused && this.lastProtectionTime !== null) {
      const elapsed = (context.timestamp - this.lastProtectionTime) / 3_600_000;
      if (elapsed >= pauseHours) {
        this.isPaused = false;
        this.pauseReason = "";
        // 暂停到期必须清零计数：否则空仓状态下没有任何平仓事件能重置计数，
        // 下一次 check 立即因「计数仍达阈值」重新触发，每个冷却期循环一次，
        // 账户被永久锁死（脚本实测可复现）。
        // 与 per_symbol 锁到期清零 symbolLosses 的语义保持一致。
        this.globalLosses = 0;
        this.lastProtectionTime = null;
        this.saveState();
        this.logger.info("连续亏损保护暂停期已过，恢复交易（连亏计数已重置）");
      }
    }

    // 清理已过期的 symbol 锁定
    this.cleanupExpiredLocks(context.timestamp);

    if (this.isPaused) {
      return protectionReturn({
        triggered: true,
        action: ProtectionAction.PAUSE_NEW_TRADES,
        reason: this.pauseReason,
        shouldPause: true,
      });
    }

    // 全局模式检查
    if (!perSymbol && this.globalLosses >= maxLosses) {
      const reason = `连续亏损保护触发: 全局连续亏损 ${this.globalLosses} 次 >= 阈值 ${maxLosses}`;
      this.isPaused = true;
      this.pauseReason = reason;
      this.lastProtectionTime = context.timestamp;
      this.saveState();
      return protectionReturn({
        triggered: true,
        action: ProtectionAction.PAUSE_NEW_TRADES,
        reason,
        shouldPause: true,
        details: { consecutive_losses: this.globalLosses },
      });
    }

    this.saveState();
    return protectionReturn({ triggered: false });
  }

  /**
   * 平仓事件：更新连续亏损计数。
   *
   * forced_close_no_reset 开启时，风控强制平仓（forced=true）的净盈利不重置
   * 计数、也不递增——强平时恰好浮盈了结不代表策略健康，只有主动止盈的盈利才算
   * 「打破连亏」。线上实证：12.5 天 145 次趋势过滤强平（正负盈亏交替）把计数
   * 反复清零，连亏熔断全程 0 次触发，该保护形同虚设。默认关闭（保持历史行为）。
   */
  override onTradeClose(event: { symbol: string; pnl: number; timestamp?: number; forced?: boolean }): void {
    const maxLosses = Number(this.config.max_consecutive_losses ?? 5);
    const perSymbol = !!this.config.per_symbol;
    const pauseHours = Number(this.config.pause_hours ?? 4.0);
    const forcedNoReset = !!this.config.forced_close_no_reset;
    const now = event.timestamp ?? clock.now();

    if (event.forced && forcedNoReset && event.pnl > 0) {
      // 强平净盈利：不重置也不递增，计数保持原状
      return;
    }
    if (event.pnl > 0) {
      // 盈利：重置计数
      this.globalLosses = 0;
      if (event.symbol in this.symbolLosses) this.symbolLosses[event.symbol] = 0;
    } else {
      // 非盈利（亏损或保本）：递增计数。
      // 注意：pnl==0 也按「非盈利」递增是既定设计——策略层的 size>0 守卫
      // 已保证只有真实成交才会进入此分支。
      this.globalLosses += 1;
      this.symbolLosses[event.symbol] = (this.symbolLosses[event.symbol] ?? 0) + 1;

      // per_symbol 模式：检查该交易对是否达阈值
      if (perSymbol && (this.symbolLosses[event.symbol] ?? 0) >= maxLosses) {
        const lockUntil = new Date(now + pauseHours * 3_600_000);
        this.lockedSymbols[event.symbol] = lockUntil.toISOString();
        this.logger.warn(
          `连续亏损保护: ${event.symbol} 连续亏损 ${this.symbolLosses[event.symbol]} 次，` +
          `锁定至 ${lockUntil.toISOString().slice(11, 16)}`,
        );
      }
    }
    this.saveState();
  }

  /** 查询指定交易对是否被锁定。 */
  override isSymbolLocked(symbol: string, timestamp?: number): [boolean, string] {
    const now = timestamp ?? clock.now();
    if (symbol in this.lockedSymbols) {
      const lockUntil = Date.parse(this.lockedSymbols[symbol]);
      if (now < lockUntil) {
        const losses = this.symbolLosses[symbol] ?? 0;
        return [true, `${symbol} 连续亏损 ${losses} 次，锁定至 ${new Date(lockUntil).toISOString().slice(11, 16)}`];
      }
      // 锁定已过期
      delete this.lockedSymbols[symbol];
      this.symbolLosses[symbol] = 0;
    }
    return [false, ""];
  }

  private cleanupExpiredLocks(now: number): void {
    for (const [sym, untilStr] of Object.entries(this.lockedSymbols)) {
      if (now >= Date.parse(untilStr)) {
        delete this.lockedSymbols[sym];
        this.symbolLosses[sym] = 0;
      }
    }
  }

  protected override resetState(): void {
    this.globalLosses = 0;
    this.symbolLosses = {};
    this.lockedSymbols = {};
    this.isPaused = false;
    this.pauseReason = "";
    this.lastProtectionTime = null;
  }

  protected override getStateDict(): Record<string, unknown> {
    return {
      global_losses: this.globalLosses,
      symbol_losses: this.symbolLosses,
      locked_symbols: this.lockedSymbols,
      is_paused: this.isPaused,
      pause_reason: this.pauseReason,
      last_protection_time: this.lastProtectionTime === null ? null : new Date(this.lastProtectionTime).toISOString(),
    };
  }

  protected override restoreStateDict(state: Record<string, unknown>): void {
    this.globalLosses = Number(state.global_losses ?? 0) || 0;
    this.symbolLosses = (state.symbol_losses as Record<string, number>) ?? {};
    this.lockedSymbols = (state.locked_symbols as Record<string, string>) ?? {};
    this.isPaused = !!state.is_paused;
    this.pauseReason = String(state.pause_reason ?? "");
    const lpt = state.last_protection_time;
    this.lastProtectionTime = lpt ? Date.parse(String(lpt)) : null;
  }
}
