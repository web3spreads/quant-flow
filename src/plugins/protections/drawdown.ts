/**
 * 最大回撤保护插件
 * 当账户净值从峰值回撤超过阈值时，触发全部平仓并暂停交易。
 */

import { IProtection, ProtectionAction, protectionReturn, type ProtectionContext, type ProtectionInit, type ProtectionReturn } from "./base.js";

export class MaxDrawdownProtection extends IProtection {
  private peakEquity = 0.0;
  private isPaused = false;
  private pauseReason = "";
  private lastProtectionTime: number | null = null;
  // 疑似坏采样待确认标记（内存态即可：重启后重新确认一轮无碍）
  private suspectPending = false;

  constructor(options: ProtectionInit) {
    super("max_drawdown", options);
  }

  check(context: ProtectionContext): ProtectionReturn {
    const maxDrawdownPct = Number(this.config.max_drawdown_pct ?? 0.10);
    const pauseHours = Number(this.config.pause_hours ?? 4.0);

    // 净值非法守卫：行情/接口抖动导致 equity<=0 时，若继续计算会算出巨大回撤
    // （(peak-0)/peak≈100%）误触发 CLOSE_ALL 平掉全部=实亏，或用坏值污染峰值。
    // 遇到非法净值直接跳过本次检查，不更新峰值、不触发。
    if (context.equity <= 0) {
      this.logger.warn(`最大回撤保护跳过：净值非法 (${context.equity.toFixed(4)})`);
      return protectionReturn({ triggered: false });
    }

    // 坏采样确认守卫：净值较峰值单次骤降超过 suspect_drop_ratio（默认 50%）
    // 大概率是账户接口降级形态（历史事故：统一账户 marginSummary 只报被占用
    // 抵押，净值被低估近 80%），而非真实亏损——真实回撤会跨周期持续。
    // 首次出现只告警等待下一周期复核；连续两次仍骤降才放行进入正常回撤判定。
    // CLOSE_ALL 不可逆，宁可迟一个周期也不能被单次坏采样触发（该场景平仓即实亏）。
    const suspectRatio = Number(this.config.suspect_drop_ratio ?? 0.5) || 0;
    if (suspectRatio > 0 && suspectRatio < 1 && this.peakEquity > 0) {
      const collapsed = context.equity < this.peakEquity * (1 - suspectRatio);
      if (collapsed && !this.suspectPending) {
        this.suspectPending = true;
        this.logger.error(
          `【严重】最大回撤保护：净值 $${context.equity.toFixed(2)} 较峰值 $${this.peakEquity.toFixed(2)} ` +
          `骤降逾 ${(suspectRatio * 100).toFixed(0)}%，疑似账户接口坏采样，等待下一周期确认后再判定回撤`,
        );
        return protectionReturn({ triggered: false });
      }
      // 连续两周期骤降=确认为真实回撤，清掉标记进入正常判定；
      // 净值恢复同样清掉标记（单次坏采样已被吸收）
      this.suspectPending = false;
    }

    // 更新峰值
    if (context.equity > this.peakEquity) this.peakEquity = context.equity;

    // 检查暂停期是否已过
    if (this.isPaused && this.lastProtectionTime !== null) {
      const elapsedHours = (context.timestamp - this.lastProtectionTime) / 3_600_000;
      if (elapsedHours >= pauseHours) {
        this.isPaused = false;
        this.pauseReason = "";
        // 冷静期结束后，把高水位峰值重置为当前净值，从新基准重新计量回撤。
        // 否则峰值永远停在触发前的旧高点：恢复交易后，同一笔已经发生的回撤会
        // 立刻被再次判定超限而重新暂停——「暂停 N 小时后自动恢复」形同死代码，
        // 账户被永久锁死（尤其 CLOSE_ALL 平成空仓后净值盯市冻结，永远回不到
        // 旧峰值）。重置后从新基准继续保护：再跌一个阈值仍会照常触发。
        this.peakEquity = context.equity;
        this.lastProtectionTime = null;
        this.logger.info(`最大回撤保护暂停期已过，恢复交易（高水位重置为当前净值 $${context.equity.toFixed(2)}）`);
      }
    }

    // 如果仍在暂停中
    if (this.isPaused) {
      return protectionReturn({
        triggered: true,
        action: ProtectionAction.PAUSE_NEW_TRADES,
        reason: this.pauseReason,
        shouldPause: true,
        details: { peak_equity: this.peakEquity },
      });
    }

    if (this.peakEquity <= 0) {
      this.saveState();
      return protectionReturn({ triggered: false });
    }

    const drawdownPct = (this.peakEquity - context.equity) / this.peakEquity;

    // 绝对额下限（可选，默认 0=关闭）：小账户上纯百分比触发线是噪声级别——
    // 线上 $8.61 账户的 10% 触发线只有 $0.86，一根普通 K 线即可击穿；且冷静期后
    // 高水位重置使熔断变成「下跌节拍器」（$12.64→$11.37→$10.21→$8.94→$7.71，
    // 每级恰好 ~10%）。要求回撤绝对额同时达标可避免噪声级触发。
    const minDrawdownUsd = Number(this.config.min_drawdown_usd ?? 0) || 0;
    const drawdownUsd = this.peakEquity - context.equity;
    if (minDrawdownUsd > 0 && drawdownUsd < minDrawdownUsd) {
      this.saveState();
      return protectionReturn({ triggered: false });
    }

    if (drawdownPct >= maxDrawdownPct) {
      const reason =
        `最大回撤保护触发: 回撤 ${(drawdownPct * 100).toFixed(1)}% >= 阈值 ${(maxDrawdownPct * 100).toFixed(1)}% ` +
        `(峰值 $${this.peakEquity.toFixed(2)} → 当前 $${context.equity.toFixed(2)})`;
      this.isPaused = true;
      this.pauseReason = reason;
      this.lastProtectionTime = context.timestamp;
      this.saveState();
      return protectionReturn({
        triggered: true,
        action: ProtectionAction.CLOSE_ALL_POSITIONS,
        reason,
        shouldPause: true,
        details: { drawdown_pct: drawdownPct, peak_equity: this.peakEquity, current_equity: context.equity },
      });
    }

    this.saveState();
    return protectionReturn({ triggered: false });
  }

  protected override resetState(): void {
    this.peakEquity = 0.0;
    this.isPaused = false;
    this.pauseReason = "";
    this.lastProtectionTime = null;
  }

  protected override getStateDict(): Record<string, unknown> {
    return {
      peak_equity: this.peakEquity,
      is_paused: this.isPaused,
      pause_reason: this.pauseReason,
      last_protection_time: this.lastProtectionTime === null ? null : new Date(this.lastProtectionTime).toISOString(),
    };
  }

  protected override restoreStateDict(state: Record<string, unknown>): void {
    this.peakEquity = Number(state.peak_equity ?? 0);
    this.isPaused = !!state.is_paused;
    this.pauseReason = String(state.pause_reason ?? "");
    const lpt = state.last_protection_time;
    this.lastProtectionTime = lpt ? Date.parse(String(lpt)) : null;
  }
}
