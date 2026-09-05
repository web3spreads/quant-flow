/**
 * 保护插件基础架构
 * 定义 IProtection 抽象基类、ProtectionReturn、ProtectionContext 等核心数据结构。
 */

import fs from "node:fs";
import path from "node:path";

export enum ProtectionAction {
  NONE = "none",
  PAUSE_NEW_TRADES = "pause_new_trades",
  CLOSE_ALL_POSITIONS = "close_all_positions",
}

/** 单个保护插件的检查结果 */
export interface ProtectionReturn {
  triggered: boolean;
  action: ProtectionAction;
  reason: string;
  shouldPause: boolean;
  /** null = 全局 */
  affectedSymbols: string[] | null;
  details: Record<string, unknown>;
  /** 由 ProtectionManager 在分发时填入，便于调用方识别来源 */
  pluginName: string;
}

export function protectionReturn(partial: Partial<ProtectionReturn> & { triggered: boolean }): ProtectionReturn {
  return {
    action: ProtectionAction.NONE,
    reason: "",
    shouldPause: false,
    affectedSymbols: null,
    details: {},
    pluginName: "",
    ...partial,
  };
}

/** 传递给各插件的上下文 */
export interface ProtectionContext {
  balance: number;
  equity: number;
  unrealizedPnl: number;
  marginUsed: number;
  currentPositions: Record<string, unknown>[];
  /** 毫秒时间戳（回测可注入模拟时间） */
  timestamp: number;
}

export interface ProtectionLogger {
  info(m: string): void;
  warn(m: string): void;
  error(m: string): void;
}

export interface ProtectionInit {
  config: Record<string, unknown>;
  dataDir?: string;
  logger?: ProtectionLogger;
}

/** 保护插件抽象基类（状态原子写入：tmp + rename）。 */
export abstract class IProtection {
  /** 插件名称（用于注册表和状态文件目录）——TS 构造期无法访问抽象属性，改由子类经 super 传入 */
  readonly name: string;
  readonly config: Record<string, unknown>;
  enabled: boolean;
  protected readonly dataDir: string;
  protected readonly stateFile: string;
  protected readonly logger: ProtectionLogger;

  constructor(name: string, options: ProtectionInit) {
    this.name = name;
    this.config = options.config;
    this.enabled = options.config.enabled !== false;
    this.logger = options.logger ?? { info: console.log, warn: console.warn, error: console.error };
    this.dataDir = path.join(options.dataDir ?? path.join("data", "protection"), this.name);
    fs.mkdirSync(this.dataDir, { recursive: true });
    this.stateFile = path.join(this.dataDir, "state.json");
    this.loadState();
  }

  /** 执行保护检查 */
  abstract check(context: ProtectionContext): ProtectionReturn;

  /** 开仓事件回调（子类按需覆盖） */
  onTradeOpen(_event: {
    symbol: string;
    entryPrice: number;
    size: number;
    isLong: boolean;
    leverage?: number;
    timestamp?: number;
  }): void {}

  /**
   * 平仓事件回调（子类按需覆盖）。
   *
   * forced=true 表示这是风控/保护机制的强制平仓（紧急平仓、趋势过滤减仓等），
   * 而非策略主动止盈平仓。与 onPositionDropped 的区别：本回调携带真实 pnl
   * （强平亏损必须计入亏损类计数器，否则连亏熔断对网格最大的亏损来源不可见）；
   * 插件可按 forced 区分语义——例如连亏熔断对 forced 且盈利的平仓不重置计数
   * （强平的浮盈了结不代表策略健康）。
   */
  onTradeClose(_event: { symbol: string; pnl: number; timestamp?: number; forced?: boolean }): void {}

  /**
   * 持仓被外部/风控强制平掉的事件回调（子类按需覆盖）。
   *
   * 与 onTradeClose 的区别：这是风控主动行为（如回撤强平、超时强平），
   * 不携带 pnl 语义，仅用于让维护持仓状态的插件清理其内部记录，
   * 不应影响基于盈亏的计数器（如连续亏损）。
   */
  onPositionDropped(_symbol: string): void {}

  /** 保存插件状态到 JSON 文件（原子写入）。 */
  saveState(): void {
    const state = this.getStateDict();
    if (state === null) return;
    try {
      const tmp = this.stateFile + ".tmp";
      fs.writeFileSync(tmp, JSON.stringify(state), "utf-8");
      fs.renameSync(tmp, this.stateFile);
    } catch (e) {
      this.logger.warn(`保存 ${this.name} 状态失败: ${e}`);
    }
  }

  /** 从 JSON 文件加载插件状态。 */
  loadState(): void {
    if (!fs.existsSync(this.stateFile)) return;
    try {
      const state = JSON.parse(fs.readFileSync(this.stateFile, "utf-8"));
      this.restoreStateDict(state);
    } catch (e) {
      this.logger.warn(`加载 ${this.name} 状态失败: ${e}`);
    }
  }

  protected getStateDict(): Record<string, unknown> | null {
    return null;
  }

  protected restoreStateDict(_state: Record<string, unknown>): void {}

  /** 重置插件状态（磁盘 + 内存）。 */
  reset(): void {
    if (fs.existsSync(this.stateFile)) fs.unlinkSync(this.stateFile);
    this.resetState();
  }

  protected resetState(): void {}

  /** 看板展示用：插件当前状态快照（config + 持久化状态）。 */
  inspect(): Record<string, unknown> {
    return { name: this.name, enabled: this.enabled, config: this.config, state: this.getStateDict() };
  }

  /** 查询指定交易对是否被锁定（支持 per-symbol 锁的插件覆盖）。 */
  isSymbolLocked(_symbol: string, _timestamp?: number): [boolean, string] {
    return [false, ""];
  }

  /** 返回所有超时持仓符号（支持超时检测的插件覆盖）。 */
  getTimeoutSymbols(_timestamp?: number): string[] {
    return [];
  }
}
