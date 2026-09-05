/** 保护插件包：注册表和所有内置保护插件。 */
export {
  IProtection,
  ProtectionAction,
  protectionReturn,
  type ProtectionContext,
  type ProtectionInit,
  type ProtectionLogger,
  type ProtectionReturn,
} from "./base.js";
export { ProtectionManager, PROTECTION_REGISTRY } from "./manager.js";
export { MaxDrawdownProtection } from "./drawdown.js";
export { DailyLossProtection } from "./dailyLoss.js";
export { ConsecutiveLossProtection } from "./consecutiveLoss.js";
