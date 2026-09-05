/**
 * 保护插件管理器
 * 基于注册表模式编排多个保护插件，提供统一的检查和事件分发接口。
 */

import { IProtection, ProtectionAction, type ProtectionContext, type ProtectionLogger, type ProtectionReturn } from "./base.js";
import { MaxDrawdownProtection } from "./drawdown.js";
import { DailyLossProtection } from "./dailyLoss.js";
import { ConsecutiveLossProtection } from "./consecutiveLoss.js";

/** 注册表：插件名称 → 构造器（IProtection 实现注册于此即插即用）。 */
export const PROTECTION_REGISTRY: Record<
  string,
  new (options: { config: Record<string, unknown>; dataDir?: string; logger?: ProtectionLogger }) => IProtection
> = {
  max_drawdown: MaxDrawdownProtection,
  daily_loss: DailyLossProtection,
  consecutive_loss: ConsecutiveLossProtection,
};

/** 保护插件管理器 */
export class ProtectionManager {
  private readonly pluginsList: IProtection[] = [];
  private readonly onTriggered?: (reason: string) => void;
  private readonly logger: ProtectionLogger;
  // 触发日志去重：暂停期内每个周期都会重复触发同一原因（线上单次熔断刷出 726 条
  // 重复 WARNING），仅在原因变化时用 WARNING，重复时静默。
  private lastTriggerReason: Record<string, string> = {};

  constructor(options: {
    protectionsConfig: Record<string, unknown>[];
    dataDir?: string;
    onProtectionTriggered?: (reason: string) => void;
    logger?: ProtectionLogger;
  }) {
    this.onTriggered = options.onProtectionTriggered;
    this.logger = options.logger ?? { info: console.log, warn: console.warn, error: console.error };

    for (const cfg of options.protectionsConfig) {
      const name = String(cfg.name ?? "");
      if (cfg.enabled === false) {
        this.logger.info(`保护插件 ${name} 已禁用，跳过`);
        continue;
      }
      const Cls = PROTECTION_REGISTRY[name];
      if (!Cls) {
        this.logger.warn(`未知的保护插件: ${name}，跳过`);
        continue;
      }
      const plugin = new Cls({ config: cfg, dataDir: options.dataDir, logger: this.logger });
      this.pluginsList.push(plugin);
      this.logger.info(`已加载保护插件: ${name}`);
    }
  }

  /** 返回已加载的插件列表 */
  get plugins(): IProtection[] {
    return [...this.pluginsList];
  }

  /** 顺序执行所有保护插件的检查，返回所有触发的保护结果列表。 */
  checkAll(context: ProtectionContext): ProtectionReturn[] {
    const results: ProtectionReturn[] = [];
    for (const plugin of this.pluginsList) {
      if (!plugin.enabled) continue;
      try {
        const result = plugin.check(context);
        if (result.triggered) {
          result.pluginName = plugin.name;
          results.push(result);
          if (this.lastTriggerReason[plugin.name] !== result.reason) {
            this.lastTriggerReason[plugin.name] = result.reason;
            this.logger.warn(`保护插件 ${plugin.name} 触发: ${result.reason} (动作: ${result.action})`);
            // 触发回调与 WARNING 同步去重：暂停期内同一原因每周期重复回调
            // 只会刷屏（历史单次熔断 726 条重复告警）
            this.onTriggered?.(result.reason);
          }
        } else {
          // 恢复正常后清除去重记录：下次再触发（哪怕同一原因）重新用 WARNING
          delete this.lastTriggerReason[plugin.name];
        }
      } catch (e) {
        this.logger.error(`保护插件 ${plugin.name} 检查异常: ${e}`);
      }
    }
    return results;
  }

  /** 从多个保护结果中取最严重的动作（CLOSE_ALL > PAUSE > NONE）。 */
  static getMostSevereAction(results: ProtectionReturn[]): ProtectionAction {
    if (!results.length) return ProtectionAction.NONE;
    const severity: Record<string, number> = {
      [ProtectionAction.NONE]: 0,
      [ProtectionAction.PAUSE_NEW_TRADES]: 1,
      [ProtectionAction.CLOSE_ALL_POSITIONS]: 2,
    };
    let worst: ProtectionAction = ProtectionAction.NONE;
    for (const r of results) {
      if ((severity[r.action] ?? 0) > (severity[worst] ?? 0)) worst = r.action;
    }
    return worst;
  }

  /** 查询指定交易对是否被锁定。 */
  isSymbolLocked(symbol: string, timestamp?: number): [boolean, string] {
    for (const plugin of this.pluginsList) {
      if (!plugin.enabled) continue;
      const [locked, reason] = plugin.isSymbolLocked(symbol, timestamp);
      if (locked) return [true, reason];
    }
    return [false, ""];
  }

  /** 从支持超时检测的插件中获取所有超时持仓符号。 */
  getTimeoutSymbols(timestamp?: number): string[] {
    const result: string[] = [];
    for (const plugin of this.pluginsList) {
      if (!plugin.enabled) continue;
      result.push(...plugin.getTimeoutSymbols(timestamp));
    }
    return result;
  }

  /** 分发开仓事件到所有插件 */
  onTradeOpen(event: {
    symbol: string;
    entryPrice: number;
    size: number;
    isLong: boolean;
    leverage?: number;
    timestamp?: number;
  }): void {
    for (const plugin of this.pluginsList) {
      if (!plugin.enabled) continue;
      try {
        plugin.onTradeOpen(event);
      } catch (e) {
        this.logger.error(`插件 ${plugin.name} on_trade_open 异常: ${e}`);
      }
    }
  }

  /** 分发平仓事件到所有插件（forced=true 表示风控强制平仓，见 IProtection 文档） */
  onTradeClose(event: { symbol: string; pnl: number; timestamp?: number; forced?: boolean }): void {
    for (const plugin of this.pluginsList) {
      if (!plugin.enabled) continue;
      try {
        plugin.onTradeClose(event);
      } catch (e) {
        this.logger.error(`插件 ${plugin.name} on_trade_close 异常: ${e}`);
      }
    }
  }

  /**
   * 分发「持仓被风控强制平仓」事件到所有插件。
   *
   * 用于回撤强平 / 超时强平等风控主动平仓场景：仅让维护持仓状态的插件
   * （如按持仓状态计数的插件）清理其内部记录，不向基于盈亏的插件
   * （如 consecutive_loss）上报虚假 pnl。
   */
  onPositionDropped(symbol: string): void {
    for (const plugin of this.pluginsList) {
      if (!plugin.enabled) continue;
      try {
        plugin.onPositionDropped(symbol);
      } catch (e) {
        this.logger.error(`插件 ${plugin.name} on_position_dropped 异常: ${e}`);
      }
    }
  }

  /** 看板展示用：全部插件的状态快照。 */
  inspect(): Record<string, unknown>[] {
    return this.pluginsList.map((p) => p.inspect());
  }
}
